"""시세 데이터 불러오기: yfinance(지수·환율·원자재·코인 등), 한국 금리는 네이버 금융 → 한국은행 ECOS 순서."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from lib import kr_rates

# ECOS 시장금리(일별) 통계표 817Y002의 항목 코드
ECOS_SERIES = {
    "ecos:KR10Y": ("817Y002", "010210000"),  # 국고채(10년)
    "ecos:KR3Y": ("817Y002", "010200000"),   # 국고채(3년)
    "ecos:CD91": ("817Y002", "010502000"),   # CD(91일)
}

LOOKBACK_DAYS = 120  # 90일 미니 차트 + 휴장일 여유


@st.cache_data(ttl=900, show_spinner=False)
def fetch_yf_closes(symbols: tuple[str, ...]) -> pd.DataFrame:
    """여러 티커의 일별 종가를 한 번에 받는다. 열 = 티커, 행 = 날짜."""
    if not symbols:
        return pd.DataFrame()
    raw = yf.download(
        list(symbols),
        period=f"{LOOKBACK_DAYS}d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    closes = raw["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=symbols[0])
    closes.index = pd.to_datetime(closes.index).tz_localize(None)
    return closes


class EcosError(Exception):
    """ECOS가 데이터 대신 오류를 돌려줄 때 (예: 인증키 오류, 데이터 없음)."""


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ecos(symbol: str, api_key: str) -> pd.Series:
    """ECOS 일별 금리 시계열(%). 실패하면 EcosError."""
    stat, item = ECOS_SERIES[symbol]
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/500/"
        f"{stat}/D/{start:%Y%m%d}/{end:%Y%m%d}/{item}"
    )
    try:
        body = requests.get(url, timeout=15).json()
    except Exception as e:  # 접속 실패, JSON 아님
        raise EcosError(f"ECOS 접속 실패: {type(e).__name__}") from e
    if "RESULT" in body:  # 오류 응답: {"RESULT": {"CODE": "INFO-100", "MESSAGE": "..."}}
        r = body["RESULT"]
        raise EcosError(f"ECOS {r.get('CODE', '')}: {r.get('MESSAGE', '')}")
    rows = body.get("StatisticSearch", {}).get("row", [])
    values = {}
    for r in rows:
        try:
            values[pd.to_datetime(r["TIME"], format="%Y%m%d")] = float(r["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue  # 휴일 등 빈 값
    if not values:
        raise EcosError("ECOS 응답에 값이 없어요")
    return pd.Series(values).sort_index()


def ecos_key() -> str:
    """Secrets의 ECOS_API_KEY. 실수로 [섹션] 아래에 넣은 경우도 찾아본다."""
    def find(d):
        for k, v in d.items():
            if k.strip().upper() == "ECOS_API_KEY" and isinstance(v, str):
                return v
            if isinstance(v, dict):
                hit = find(v)
                if hit:
                    return hit
        return ""
    try:
        return find(st.secrets.to_dict()).strip()
    except Exception:
        return ""


def load_series(settings: list[dict]) -> tuple[dict[str, pd.Series], dict[str, str]]:
    """지표 설정 목록에 대해 ({symbol: 시계열}, {symbol: 못 받은 이유})를 돌려준다."""
    out: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}
    yf_symbols = tuple(sorted({s["symbol"] for s in settings if s["source"] == "yfinance"}))
    try:
        closes = fetch_yf_closes(yf_symbols)
    except Exception:
        closes = pd.DataFrame()
    for sym in yf_symbols:
        if sym in closes.columns:
            s = closes[sym].dropna()
            if not s.empty:
                out[sym] = s
                continue
        errors[sym] = f"`{sym}` 시세를 못 받았어요"

    key = ecos_key()
    for s in settings:
        if s["source"] != "ecos":
            continue
        sym = s["symbol"]
        if sym not in ECOS_SERIES and sym not in kr_rates.SUPPORTED:
            errors[sym] = f"`{sym}`은 아직 지원하지 않는 한국 금리 지표예요"
            continue
        reasons = []
        # 1) 네이버 금융 (해외 서버에서도 열리는 편)
        try:
            out[sym] = kr_rates.fetch(sym)
            continue
        except kr_rates.KrRateError as e:
            reasons.append(str(e))
        # 2) 한국은행 ECOS (키가 있을 때)
        if key and sym in ECOS_SERIES:
            try:
                out[sym] = fetch_ecos(sym, key)
                continue
            except EcosError as e:
                reasons.append(str(e))
        elif not key:
            reasons.append("ECOS_API_KEY 없음")
        errors[sym] = "못 받았어요: " + " / ".join(reasons)
    return out, errors


def summarize(series: pd.Series, is_rate: bool) -> dict:
    """현재값, 전일 대비 변화, 최근 90일 구간을 계산한다.

    금리는 %가 아니라 %p(bp) 차이로 본다.
    """
    last = float(series.iloc[-1])
    prev = float(series.iloc[-2]) if len(series) > 1 else last
    change = last - prev
    pct = (change / prev * 100) if prev else 0.0
    cutoff = series.index[-1] - pd.Timedelta(days=90)
    return {
        "last": last,
        "change": change,
        "pct": pct,
        "bp": change * 100 if is_rate else None,
        "asof": series.index[-1],
        "spark": series[series.index >= cutoff],
    }


def validate_yf_symbol(symbol: str) -> bool:
    """새 티커를 추가하기 전에 실제로 시세가 나오는지 확인한다."""
    try:
        hist = yf.Ticker(symbol).history(period="5d")
        return not hist.empty
    except Exception:
        return False
