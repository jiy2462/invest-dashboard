"""한국 금리를 해외 서버에서도 받을 수 있는 곳에서 가져온다 (한국은행 ECOS가 해외에서 막혀서).

순서: Investing.com(FinanceDataReader 경유) → 네이버 금융. 모두 비공식이라 형식이 바뀔 수 있고,
실패하면 각 출처의 이유를 모아 KrRateError를 던진다.
"""
from __future__ import annotations

import re

import pandas as pd
import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) invest-dashboard"}

# 국내 금리 일별 시세 페이지(HTML)의 marketindexCd
NAVER_DAILY_CODES = {
    "ecos:CD91": "IRR_CD91",       # CD(91일)
    "ecos:KR3Y": "IRR_GOVT03Y",    # 국고채(3년)
}
# 해외·국내 국채(모바일 API, JSON)의 reutersCode
NAVER_BOND_CODES = {
    "ecos:KR10Y": "KR10YT=RR",     # 국고채(10년)
    "ecos:KR3Y": "KR3YT=RR",
}
# Investing.com 심볼 (FinanceDataReader의 'INVESTING:<심볼>')
INVESTING_CODES = {
    "ecos:KR10Y": "KR10YT=RR",     # 국고채(10년)
    "ecos:KR3Y": "KR3YT=RR",       # 국고채(3년)
    "ecos:CD91": "KRCD91M=ED",     # CD(91일)
}
SUPPORTED = set(NAVER_DAILY_CODES) | set(NAVER_BOND_CODES) | set(INVESTING_CODES)

_ROW = re.compile(r'class="date">\s*([0-9]{4}\.[0-9]{2}\.[0-9]{2})\s*</td>\s*<td class="num">\s*([0-9.,-]+)', re.S)


class KrRateError(Exception):
    pass


def parse_daily_html(html: str) -> dict[pd.Timestamp, float]:
    """finance.naver.com 일별 시세 표에서 (날짜, 값)을 뽑는다."""
    out = {}
    for d, v in _ROW.findall(html):
        try:
            out[pd.Timestamp(d.replace(".", "-"))] = float(v.replace(",", ""))
        except ValueError:
            continue
    return out


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_bond_json(body) -> dict[pd.Timestamp, float]:
    """m.stock.naver.com 국채 시세 JSON에서 (날짜, 종가)를 뽑는다. 필드 이름이 조금 달라도 찾도록 느슨하게 본다."""
    date_keys = ("localTradedAt", "localTradeDate", "tradeDate", "date", "bizdate")
    value_keys = ("closePrice", "close", "tradePrice", "value", "price")
    out = {}
    for d in _walk(body):
        dk = next((k for k in date_keys if k in d), None)
        vk = next((k for k in value_keys if k in d), None)
        if not dk or not vk:
            continue
        try:
            out[pd.Timestamp(str(d[dk])[:10].replace(".", "-"))] = float(str(d[vk]).replace(",", ""))
        except (ValueError, TypeError):
            continue
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_naver_daily(code: str, pages: int = 8) -> pd.Series:
    values: dict = {}
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd={code}&page={page}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        rows = parse_daily_html(r.content.decode("euc-kr", errors="ignore"))
        if not rows:
            break
        values.update(rows)
    if not values:
        raise KrRateError(f"네이버 {code}: 표에서 값을 찾지 못했어요")
    return pd.Series(values).sort_index()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_naver_bond(reuters_code: str) -> pd.Series:
    url = ("https://m.stock.naver.com/front-api/marketIndex/prices"
           f"?category=bond&reutersCode={reuters_code}&page=1&pageSize=90")
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    values = parse_bond_json(r.json())
    if not values:
        raise KrRateError(f"네이버 {reuters_code}: 응답에서 값을 찾지 못했어요")
    return pd.Series(values).sort_index()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_investing(code: str) -> pd.Series:
    """FinanceDataReader의 Investing.com 리더. 기본 경로('KR10YT=RR'만 쓰면 Yahoo로 가서 실패)를 피하려고 접두어를 붙인다."""
    import FinanceDataReader as fdr

    start = (pd.Timestamp.today() - pd.Timedelta(days=150)).strftime("%Y-%m-%d")
    df = fdr.DataReader(f"INVESTING:{code}", start)
    if df is None or df.empty or "Close" not in df:
        raise KrRateError(f"Investing {code}: 값이 없어요")
    s = df["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) else pd.to_datetime(s.index)
    return s.sort_index()


def _reason(source: str, e: Exception) -> str:
    if isinstance(e, KrRateError):
        return str(e)
    detail = str(e).split("\n")[0][:60]
    return f"{source} {type(e).__name__}" + (f" ({detail})" if detail else "")


def fetch(symbol: str) -> pd.Series:
    """네이버에서 가능한 방법을 차례로 시도한다. 모두 실패하면 이유를 모아 KrRateError."""
    reasons = []
    attempts = []
    if symbol in INVESTING_CODES:
        attempts.append(("Investing", lambda: fetch_investing(INVESTING_CODES[symbol])))
    if symbol in NAVER_DAILY_CODES:
        attempts.append(("네이버", lambda: fetch_naver_daily(NAVER_DAILY_CODES[symbol])))
    if symbol in NAVER_BOND_CODES:
        attempts.append(("네이버", lambda: fetch_naver_bond(NAVER_BOND_CODES[symbol])))
    for source, attempt in attempts:
        try:
            return attempt()
        except Exception as e:  # 접속 실패, 형식 변경
            reasons.append(_reason(source, e))
    raise KrRateError(" / ".join(reasons) or "지원하지 않는 지표예요")
