"""시세 데이터 불러오기: yfinance(지수·환율·원자재·코인 등), 한국 금리는 네이버 금융 → 한국은행 ECOS 순서."""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from lib import fear_greed, kr_rates, market_hours

# ECOS 시장금리(일별) 통계표 817Y002의 항목 코드
ECOS_SERIES = {
    "ecos:KR10Y": ("817Y002", "010210000"),  # 국고채(10년)
    "ecos:KR3Y": ("817Y002", "010200000"),   # 국고채(3년)
    "ecos:CD91": ("817Y002", "010502000"),   # CD(91일)
}

LOOKBACK_DAYS = 120  # 관심종목·포트폴리오용 최근 종가
HISTORY_DAYS = 450  # 오늘의 시장: 90일 캔들 + 200일선 계산에 필요한 과거 (휴장일 여유 포함)
OHLC = ["Open", "High", "Low", "Close"]


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


def fetch_yf_ohlc(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """여러 티커의 일봉(시가·고가·저가·종가)을 한 번의 요청으로 받는다. {티커: DataFrame}."""
    if not symbols:
        return {}
    raw = yf.download(
        list(symbols),
        period=f"{HISTORY_DAYS}d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
        timeout=10,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for sym in symbols:
        try:
            df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = clean_ohlc(df)
        if not df.empty:
            out[sym] = df
    return out


@st.cache_data(ttl=900, show_spinner=False)
def fetch_yf_ohlc_cached(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """관심종목 표용: 모든 종목의 일봉을 한 번에 받아 15분 동안 재사용한다."""
    return fetch_yf_ohlc(symbols)


def clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """종가 없는 날은 빼고, 시가·고가·저가가 비면 종가로 채운다. 종가만 있는 시계열도 받는다."""
    if isinstance(df, pd.Series):
        df = df.to_frame("Close")
    df = df[[c for c in OHLC if c in df.columns]].dropna(subset=["Close"]).copy()
    for c in OHLC:
        if c not in df.columns:
            df[c] = df["Close"]
        df[c] = df[c].fillna(df["Close"])
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx
    return df[OHLC].sort_index()


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


def _fetch_extra(sym: str, key: str) -> tuple[pd.Series | None, str | None]:
    """야후 밖의 지표 하나: CNN 공포·탐욕 지수 또는 한국 금리."""
    if sym.startswith("cnn:"):
        if sym != fear_greed.SYMBOL:
            return None, f"`{sym}`은 아직 지원하지 않는 CNN 지표예요"
        try:
            return fear_greed.fetch(), None
        except fear_greed.FearGreedError as e:
            return None, str(e)
        except Exception as e:
            return None, f"CNN {type(e).__name__}"
    return _kr_rate(sym, key)


def _kr_rate(sym: str, key: str) -> tuple[pd.Series | None, str | None]:
    """한국 금리 하나: Investing.com → 네이버 금융 → (키가 있으면) 한국은행 ECOS 순서."""
    if sym not in ECOS_SERIES and sym not in kr_rates.SUPPORTED:
        return None, f"`{sym}`은 아직 지원하지 않는 한국 금리 지표예요"
    reasons = []
    try:  # 1) Investing.com → 네이버 금융 (해외 서버에서도 열리는 곳)
        return kr_rates.fetch(sym), None
    except kr_rates.KrRateError as e:
        reasons.append(str(e))
    except Exception as e:
        reasons.append(f"네이버 {type(e).__name__}")
    if key and sym in ECOS_SERIES:  # 2) 한국은행 ECOS
        try:
            return fetch_ecos(sym, key), None
        except EcosError as e:
            reasons.append(str(e))
    elif not key:
        reasons.append("ECOS_API_KEY 없음")
    return None, "못 받았어요: " + " / ".join(reasons)


def load_ohlc_uncached(yf_symbols: tuple[str, ...], kr_symbols: tuple[str, ...],
                       key: str) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """야후(한 번에 묶어서)와 한국 금리(지표마다)를 동시에 받는다."""
    out: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=1 + max(1, len(kr_symbols))) as pool:
        yf_job = pool.submit(fetch_yf_ohlc, yf_symbols)
        kr_jobs = {sym: pool.submit(_fetch_extra, sym, key) for sym in kr_symbols}
        try:
            out.update(yf_job.result())
        except Exception:
            pass
        for sym, job in kr_jobs.items():
            series, err = job.result()
            if series is not None and not series.empty:
                out[sym] = clean_ohlc(series)
            else:
                errors[sym] = err or f"`{sym}` 시세를 못 받았어요"
    for sym in yf_symbols:
        if sym not in out:
            errors[sym] = f"`{sym}` 시세를 못 받았어요"
    return out, errors


EXTRA_SOURCES = ("ecos", "cnn")  # 야후 밖에서 지표마다 따로 받는 출처

LIVE_TTL = timedelta(minutes=5)   # 열린 장: 5분마다 새로
RETRY_TTL = timedelta(minutes=5)  # 못 받은 지표: 5분 뒤 다시 시도


@st.cache_resource
def _store() -> dict:
    """지표별 시세 저장소 (서버 메모리, 모든 접속이 같이 씀). {symbol: {"df", "err", "at"}}"""
    return {"data": {}, "lock": threading.Lock()}


def clear_cache() -> None:
    st_ = _store()
    with st_["lock"]:
        st_["data"].clear()


def is_fresh(symbol: str, entry: dict | None, now: datetime) -> bool:
    """다시 받을 필요가 없는가.

    - 못 받은 지표: 5분 지나면 다시 시도
    - 열린 장(마감 직후 20분 포함): 5분 지나면 다시
    - 닫힌 장: 마감 뒤에 한 번 받았으면 다음 장이 열릴 때까지 그대로
    """
    if not entry:
        return False
    if entry.get("retry_at") and now < entry["retry_at"]:
        return True
    age = now - entry["at"]
    if entry["df"] is None:
        return age < RETRY_TTL
    if market_hours.is_live(symbol, now):
        return age < LIVE_TTL
    closed = market_hours.last_close(market_hours.market_of(symbol), now)
    return closed is None or entry["at"] >= closed + market_hours.GRACE


def load_ohlc(settings: list[dict], cached: bool = True,
              now: datetime | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """지표 설정 목록에 대해 ({symbol: 일봉 DataFrame}, {symbol: 못 받은 이유})를 돌려준다.

    장이 닫힌 지표는 저장해 둔 값을 쓰고, 다시 받아야 하는 지표만 한 번에 묶어 받는다.
    한국 금리처럼 종가만 있는 지표는 시가·고가·저가를 종가로 채운다.
    """
    yf_symbols = tuple(sorted({s["symbol"] for s in settings if s["source"] == "yfinance"}))
    kr_symbols = tuple(sorted({s["symbol"] for s in settings if s["source"] in EXTRA_SOURCES}))
    if not cached:
        return load_ohlc_uncached(yf_symbols, kr_symbols, ecos_key() if kr_symbols else "")

    now = now or datetime.now(timezone.utc)
    store = _store()
    with store["lock"]:
        data = store["data"]
        stale_yf = tuple(s for s in yf_symbols if not is_fresh(s, data.get(s), now))
        stale_kr = tuple(s for s in kr_symbols if not is_fresh(s, data.get(s), now))
        if stale_yf or stale_kr:
            got, errs = load_ohlc_uncached(stale_yf, stale_kr, ecos_key() if stale_kr else "")
            for sym in stale_yf + stale_kr:
                old = data.get(sym)
                if sym in got:
                    data[sym] = {"df": got[sym], "err": None, "at": now}
                elif old and old["df"] is not None:  # 이번에 실패하면 예전 값을 계속 보여 준다
                    data[sym] = {**old, "retry_at": now + RETRY_TTL}
                else:
                    data[sym] = {"df": None, "err": errs.get(sym), "at": now}
        out = {s: data[s]["df"] for s in yf_symbols + kr_symbols if data[s]["df"] is not None}
        errors = {s: data[s]["err"] or f"`{s}` 시세를 못 받았어요"
                  for s in yf_symbols + kr_symbols if data[s]["df"] is None}
    return out, errors


def load_series(settings: list[dict]) -> tuple[dict[str, pd.Series], dict[str, str]]:
    """종가 시계열만 필요할 때 (캐시 없이 바로)."""
    data, errors = load_ohlc(settings, cached=False)
    return {k: v["Close"] for k, v in data.items()}, errors


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


# 야후에 없는 국내 시세: 네이버 증권 시장지표 (구글 시트와 같은 출처)
NAVER_METALS = {"MANUAL:KRX 금현물": "M04020000"}  # KRX 금 1g (원)
_NAVER_URLS = (
    "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=metals&reutersCode={code}",
    "https://api.stock.naver.com/marketindex/metals/{code}",
    "https://m.stock.naver.com/marketindex/metals/{code}",
)
_CLOSE_RE = re.compile(r'"closePrice"\s*:\s*"?([0-9][0-9,]*\.?[0-9]*)')


def parse_naver_close(text: str) -> float | None:
    """네이버 응답(JSON 또는 페이지 HTML)에서 현재가(closePrice)를 꺼낸다."""
    m = _CLOSE_RE.search(text or "")
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_naver_metal(code: str) -> float | None:
    for url in _NAVER_URLS:
        try:
            r = requests.get(url.format(code=code), timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        except Exception:
            continue
        if r.ok:
            v = parse_naver_close(r.text)
            if v:
                return v
    return None


def auto_price(symbol: str) -> float | None:
    """티커 없는 자산 중 자동 시세가 있는 것 (예: KRX 금현물). 없거나 실패하면 None."""
    code = NAVER_METALS.get(symbol)
    if not code:
        return None
    try:
        return fetch_naver_metal(code)
    except Exception:
        return None
