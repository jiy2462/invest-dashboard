"""시세 데이터 불러오기: yfinance(지수·환율·원자재·코인 등)와 한국은행 ECOS(한국 금리)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

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


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ecos(symbol: str, api_key: str) -> pd.Series:
    """ECOS 일별 금리 시계열(%). 실패하면 빈 Series."""
    stat, item = ECOS_SERIES[symbol]
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/500/"
        f"{stat}/D/{start:%Y%m%d}/{end:%Y%m%d}/{item}"
    )
    rows = requests.get(url, timeout=10).json().get("StatisticSearch", {}).get("row", [])
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(r["DATA_VALUE"]) for r in rows],
        index=pd.to_datetime([r["TIME"] for r in rows], format="%Y%m%d"),
    )
    return s.sort_index()


def load_series(settings: list[dict]) -> dict[str, pd.Series]:
    """지표 설정 목록에 대해 {symbol: 종가 시계열}을 돌려준다. 못 받은 지표는 빠진다."""
    out: dict[str, pd.Series] = {}
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

    ecos_key = st.secrets.get("ECOS_API_KEY", "")
    for s in settings:
        if s["source"] == "ecos" and ecos_key and s["symbol"] in ECOS_SERIES:
            try:
                series = fetch_ecos(s["symbol"], ecos_key)
            except Exception:
                continue
            if not series.empty:
                out[s["symbol"]] = series
    return out


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
