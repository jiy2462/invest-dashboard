"""야후 일봉에서 빠진 날 채우기.

야후는 가끔 지난 거래일 일봉을 통째로 빠뜨린다 (2026-11-26 확인: 코스피·코스닥·닛케이·항셍·비트코인의
11/25 줄이 없었음). 그러면 오늘 등락률이 이틀 전과 비교되고 차트에도 그날 봉이 없다.
최근 7일 안에서 빠진 거래일만 찾아
- 한국·일본·홍콩 지수: 네이버 증권 일별 시세 (거래소 종가 그대로)
- 그 밖(미국 주식·ETF, 코인 등): 야후 1시간봉을 모아 만든 일봉
으로 채운다. 환율·선물은 야후가 붙이는 날짜 기준이 섞여 있어 건드리지 않는다.
휴장일도 '빠진 날'로 잡히지만, 출처에도 그날 시세가 없으니 채워지는 것은 없다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from lib import live_quote, market_hours

WINDOW_DAYS = 7
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://m.stock.naver.com/"}
# 야후 티커 → 네이버 일별 시세 주소
NAVER_DAILY = {
    "^KS11": "https://m.stock.naver.com/api/index/KOSPI/price?page=1&pageSize=10",
    "^KQ11": "https://m.stock.naver.com/api/index/KOSDAQ/price?page=1&pageSize=10",
    "^KS200": "https://m.stock.naver.com/api/index/KPI200/price?page=1&pageSize=10",
    "^N225": "https://api.stock.naver.com/index/.N225/price?page=1&pageSize=10",
    "^HSI": "https://api.stock.naver.com/index/.HSI/price?page=1&pageSize=10",
}
OHLC = ["Open", "High", "Low", "Close"]


def _tz(market: str):
    if market in market_hours.MARKETS:
        return ZoneInfo(market_hours.MARKETS[market].tz)
    return timezone.utc


def missing_days(symbol: str, df: pd.DataFrame, now: datetime) -> list[date]:
    """최근 7일 중 거래했어야 하는데 일봉에 없는 날 (휴장일 포함될 수 있음).

    오늘은 장이 이미 끝났을 때만 넣는다. 장중이면 실시간 시세가 오늘 봉을 만든다.
    """
    market = market_hours.market_of(symbol)
    if market in market_hours.WEEKLY or df is None or df.empty:
        return []
    crypto = market in market_hours.ALWAYS
    today = now.astimezone(_tz(market)).date()
    have = {pd.Timestamp(i).date() for i in df.index}
    first = pd.Timestamp(df.index[0]).date()
    closed_today = False
    if not crypto:
        lc = market_hours.last_close(market, now)
        closed_today = lc is not None and lc.astimezone(_tz(market)).date() == today
    out = []
    for back in range(WINDOW_DAYS, -1, -1):
        d = today - timedelta(days=back)
        if d <= first or d in have:
            continue
        if d == today and not closed_today:
            continue
        if not crypto and d.weekday() >= 5:
            continue
        out.append(d)
    return out


def parse_naver_daily(body) -> pd.DataFrame:
    """네이버 일별 시세 목록 → 일봉. 날짜는 거래소 현지 날짜 (localTradedAt 앞 10글자)."""
    rows = {}
    for r in body if isinstance(body, list) else []:
        if not isinstance(r, dict):
            continue
        close = live_quote._num(r.get("closePrice"))
        when = str(r.get("localTradedAt") or "")[:10]
        if not close or len(when) != 10:
            continue
        vals = [live_quote._num(r.get(k)) for k in ("openPrice", "highPrice", "lowPrice")]
        o, h, lo = (v if v else close for v in vals)
        rows[pd.Timestamp(when)] = [o, max(h, close), min(lo, close), close]
    if not rows:
        return pd.DataFrame(columns=OHLC)
    return pd.DataFrame.from_dict(rows, orient="index", columns=OHLC).astype(float).sort_index()


@st.cache_data(ttl=300, show_spinner=False)
def naver_daily(symbol: str) -> pd.DataFrame:
    r = requests.get(NAVER_DAILY[symbol], headers=HEADERS, timeout=4)
    r.raise_for_status()
    return parse_naver_daily(r.json())


def daily_from_hourly(symbol: str, bars: pd.DataFrame) -> pd.DataFrame:
    """1시간봉을 거래일별로 묶어 일봉으로."""
    if bars is None or bars.empty or "Close" not in bars:
        return pd.DataFrame(columns=OHLC)
    bars = bars.dropna(subset=["Close"])
    if bars.empty:
        return pd.DataFrame(columns=OHLC)
    idx = pd.to_datetime(bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    days = [pd.Timestamp(live_quote.day_of(symbol, t.to_pydatetime())) for t in idx]
    bars = bars.set_axis(pd.DatetimeIndex(days))
    col = lambda c: bars[c] if c in bars else bars["Close"]  # noqa: E731
    g = pd.DataFrame({"Open": col("Open"), "High": col("High"), "Low": col("Low"), "Close": bars["Close"]})
    grp = g.groupby(level=0)
    return pd.DataFrame({"Open": grp["Open"].first(), "High": grp["High"].max(),
                         "Low": grp["Low"].min(), "Close": grp["Close"].last()}).sort_index()


@st.cache_data(ttl=300, show_spinner=False)
def hourly_daily(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    raw = yf.download(list(symbols), period=f"{WINDOW_DAYS + 1}d", interval="60m", auto_adjust=False,
                      progress=False, group_by="ticker", threads=True, ignore_tz=False, timeout=10)
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for sym in symbols:
        try:
            bars = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        out[sym] = daily_from_hourly(sym, bars)
    return out


def _insert(df: pd.DataFrame, extra: pd.DataFrame, days: list[date]) -> pd.DataFrame:
    want = [pd.Timestamp(d) for d in days if pd.Timestamp(d) in extra.index]
    if not want:
        return df
    add = extra.loc[want, OHLC].dropna(subset=["Close"])
    return pd.concat([df, add]).sort_index() if not add.empty else df


def fill(data: dict[str, pd.DataFrame], now: datetime | None = None) -> dict[str, pd.DataFrame]:
    """빠진 거래일을 채운 사본. 실패해도 원래 일봉을 그대로 돌려준다."""
    now = now or datetime.now(timezone.utc)
    gaps = {s: missing_days(s, df, now) for s, df in data.items()}
    gaps = {s: d for s, d in gaps.items() if d}
    if not gaps:
        return data
    out = dict(data)
    hourly_syms = []
    for sym, days in gaps.items():
        if sym in NAVER_DAILY:
            try:
                out[sym] = _insert(out[sym], naver_daily(sym), days)
                continue  # 네이버에 없는 날은 휴장일
            except Exception:
                pass
        hourly_syms.append(sym)
    if hourly_syms:
        try:
            hourly = hourly_daily(tuple(sorted(hourly_syms)))
        except Exception:
            hourly = {}
        for sym in hourly_syms:
            if sym in hourly:
                out[sym] = _insert(out[sym], hourly[sym], gaps[sym])
    return out
