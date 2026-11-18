"""한국 금리를 해외 서버(Streamlit Cloud)에서도 열리는 곳에서 가져온다 (한국은행 ECOS는 해외에서 막혀서).

순서: 네이버 증권 API(api.stock.naver.com → m.stock.naver.com) → CNBC(10년물만).
2026-11-18 GitHub Actions(미국 서버)에서 직접 열어 보고 고른 출처다. 모두 비공식이라 형식이 바뀔 수 있고,
실패하면 각 출처의 이유를 모아 KrRateError를 던진다.

- 예전 경로는 더 이상 안 된다: Investing.com(FinanceDataReader)은 형식이 바뀌어 TypeError,
  네이버 PC 일별 시세 HTML은 410(없어짐), 모바일 API는 pageSize 90을 거절(최대 60).
- 페이지를 늦게 띄우지 않도록 요청마다 짧은 타임아웃을 두고, 여러 페이지는 동시에 받는다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = (3.05, 6)  # (접속, 응답) 초. 막힌 출처를 오래 기다리지 않는다.

# 네이버 증권 시장지표: 심볼 → (카테고리, 코드)
NAVER_CODES = {
    "ecos:KR10Y": ("bond", "KR10YT=RR"),              # 국고채 10년
    "ecos:KR3Y": ("bond", "KR3YT=RR"),                # 국고채 3년
    "ecos:CD91": ("domesticInterest", "KFIA114000"),  # CD금리(91일), 금융투자협회 고시
}
# CNBC 심볼 (10년물만 있음)
CNBC_CODES = {"ecos:KR10Y": "KR10Y-KR"}
SUPPORTED = set(NAVER_CODES) | set(CNBC_CODES)

PAGE_SIZE = 60  # 네이버가 받는 최대값
PAGES = 5       # 60 × 5 = 약 300 거래일 (90일 캔들 + 200일선)
HISTORY_DAYS = 450


class KrRateError(Exception):
    pass


def _num(v) -> float | None:
    """'3,985' → 3985.0. 빈 값·0(네이버가 시가·고가·저가가 없을 때 0을 줌)은 None."""
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x != 0 else None


def parse_naver_prices(body) -> pd.DataFrame:
    """네이버 시세 목록(JSON)을 일봉 DataFrame으로. api.stock은 목록, m.stock은 {"result": 목록}."""
    rows = body.get("result") if isinstance(body, dict) else body
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        close = _num(r.get("closePrice"))
        when = str(r.get("localTradedAt") or "")[:10]
        if close is None or not when:
            continue
        try:
            day = pd.Timestamp(when)
        except ValueError:
            continue
        out[day] = {"Open": _num(r.get("openPrice")), "High": _num(r.get("highPrice")),
                    "Low": _num(r.get("lowPrice")), "Close": close}
    if not out:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    return pd.DataFrame.from_dict(out, orient="index").astype(float).sort_index()


def parse_naver_now(body) -> pd.DataFrame:
    """지금 시세(상세 JSON) 한 줄. 장중이면 오늘 값이 들어 있다."""
    if isinstance(body, dict) and isinstance(body.get("result"), dict):
        body = body["result"]
    return parse_naver_prices([body] if isinstance(body, dict) else [])


def _get_json(url: str):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _naver_urls(category: str, code: str) -> list[tuple[str, callable, callable]]:
    """(이름, 페이지 주소, 지금 시세 주소) 후보. 같은 데이터를 주는 두 서버."""
    return [
        ("api", lambda p: f"https://api.stock.naver.com/marketindex/{category}/{code}/prices?page={p}&pageSize={PAGE_SIZE}",
         lambda: f"https://api.stock.naver.com/marketindex/{category}/{code}"),
        ("m", lambda p: ("https://m.stock.naver.com/front-api/marketIndex/prices"
                         f"?category={category}&reutersCode={code}&page={p}&pageSize={PAGE_SIZE}"),
         lambda: f"https://m.stock.naver.com/front-api/marketIndex/productDetail?category={category}&reutersCode={code}"),
    ]


def _naver_once(page_url, now_url) -> pd.DataFrame:
    """한 서버에서 여러 페이지와 지금 시세를 동시에 받는다. 첫 페이지는 꼭 성공해야 한다."""
    with ThreadPoolExecutor(max_workers=PAGES + 1) as pool:
        pages = [pool.submit(_get_json, page_url(p)) for p in range(1, PAGES + 1)]
        now_job = pool.submit(_get_json, now_url())
        frames = [parse_naver_prices(pages[0].result())]  # 실패하면 예외가 그대로 올라간다
        if frames[0].empty:
            raise KrRateError("응답에서 값을 찾지 못했어요")
        for job in pages[1:] + [now_job]:
            try:
                body = job.result()
            except Exception:
                continue  # 오래된 페이지·지금 시세는 없어도 된다
            frames.append(parse_naver_now(body) if job is now_job else parse_naver_prices(body))
    df = pd.concat([f for f in frames if not f.empty])
    return df[~df.index.duplicated(keep="first")].sort_index()


def naver_history(category: str, code: str) -> pd.DataFrame:
    """네이버 일봉 (캐시 없이). api.stock이 막히면 m.stock으로."""
    reasons = []
    for name, page_url, now_url in _naver_urls(category, code):
        try:
            return _naver_once(page_url, now_url)
        except Exception as e:
            reasons.append(f"{name} {_reason(e)}")
    raise KrRateError(f"네이버 {code}: " + ", ".join(reasons))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_naver(category: str, code: str) -> pd.DataFrame:
    return naver_history(category, code)


def parse_cnbc_bars(body) -> pd.DataFrame:
    bars = ((body or {}).get("barData") or {}).get("priceBars") or []
    out = {}
    for b in bars:
        close = _num(b.get("close"))
        t = str(b.get("tradeTime") or "")[:8]
        if close is None or len(t) != 8:
            continue
        out[pd.Timestamp(t)] = {"Open": _num(b.get("open")), "High": _num(b.get("high")),
                                "Low": _num(b.get("low")), "Close": close}
    if not out:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    return pd.DataFrame.from_dict(out, orient="index").astype(float).sort_index()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_cnbc(code: str) -> pd.DataFrame:
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=HISTORY_DAYS)
    url = (f"https://ts-api.cnbc.com/harmony/app/bars/{code}/1D/"
           f"{start:%Y%m%d}000000/{end:%Y%m%d}000000/adjusted/EST5EDT.json")
    df = parse_cnbc_bars(_get_json(url))
    if df.empty:
        raise KrRateError(f"CNBC {code}: 값이 없어요")
    return df


def _reason(e: Exception) -> str:
    if isinstance(e, KrRateError):
        return str(e)
    detail = str(e).split("\n")[0][:60]
    return type(e).__name__ + (f" ({detail})" if detail else "")


def fetch(symbol: str) -> pd.DataFrame:
    """가능한 출처를 차례로 시도해 일봉(시가·고가·저가·종가)을 돌려준다. 모두 실패하면 이유를 모아 KrRateError."""
    attempts = []
    if symbol in NAVER_CODES:
        attempts.append(("네이버", lambda: fetch_naver(*NAVER_CODES[symbol])))
    if symbol in CNBC_CODES:
        attempts.append(("CNBC", lambda: fetch_cnbc(CNBC_CODES[symbol])))
    reasons = []
    for source, attempt in attempts:
        try:
            return attempt()
        except KrRateError as e:
            reasons.append(str(e))
        except Exception as e:  # 접속 실패, 형식 변경
            reasons.append(f"{source} {_reason(e)}")
    raise KrRateError(" / ".join(reasons) or "지원하지 않는 지표예요")
