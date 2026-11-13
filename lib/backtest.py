"""백테스트: 자산군 데이터 이어 붙이기, 포트폴리오 시뮬레이션, 성과 지표.

자산군마다 '대표 ETF → 그 이전을 대신하는 펀드·지수' 순서의 티커 목록을 두고,
앞 티커가 없는 기간은 다음 티커의 일별 수익률로 채운다(어느 구간에 무엇을 썼는지 기록).
계산 함수는 네트워크 없이 pandas만으로 동작한다(테스트 용이). 시세는 yfinance로 받고,
야후에 없는 오래된 금 시세는 저장소에 넣어 둔 세계은행 월평균 가격(data/gold_usd_monthly.csv)을 쓴다.
'비트코인 40/20 돌파' 자산은 받은 시세로 만드는 가짜 티커(BTC_TREND_T)다: 비트코인 트레이딩 화면과 같은 신호로
비트코인과 미국 초단기 국채 사이를 오가는 한 '주머니'의 가격 지수.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from lib.db import get_client

MAX_YEARS = 30         # 처음 열었을 때 기본 시작일 (30년 전)
EARLIEST = date(1970, 1, 1)   # 고를 수 있는 가장 이른 시작일. 데이터가 없는 자산은 있는 날부터 비교한다
CUSTOM = "T:"          # 직접 입력한 티커는 'T:AAPL'처럼 저장한다
FX = "KRW=X"           # 원/달러 (야후는 2003년 말부터)
RF = "^IRX"            # 미국 3개월 국채 금리(연 %): 샤프 지수의 무위험 수익률
GOLD_WB = "WB:GOLD"    # 세계은행 금 월평균 가격(1968~2000). 야후가 아니라 저장소 파일에서 읽는다
BTC_TREND_T = "SIG:BTC40/20"   # 비트코인 40/20 돌파 주머니 (받은 시세로 계산하는 가짜 티커)
TREND_BUY_N, TREND_SELL_N, TREND_FEE = 40, 20, 0.05
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REBALANCE = {"none": "안 함", "quarterly": "분기마다", "semiannual": "6개월마다", "yearly": "1년마다"}


@dataclass(frozen=True)
class AssetClass:
    label: str
    currency: str
    chain: tuple[str, ...]          # 앞에서부터 우선. 앞 티커가 없는 기간을 뒤 티커로 채운다
    note: str = ""


CATALOG: dict[str, AssetClass] = {
    "US_STOCK": AssetClass("미국 주식 (S&P 500)", "USD", ("SPY", "VFINX", "^GSPC")),
    "US_TOTAL": AssetClass("미국 전체 주식", "USD", ("VTI", "VTSMX")),
    "NASDAQ100": AssetClass("나스닥 100", "USD", ("QQQ", "^NDX")),
    "US_DIV": AssetClass("미국 배당주 (SCHD)", "USD", ("SCHD",)),
    "DEV_STOCK": AssetClass("선진국 주식 (미국 제외)", "USD", ("EFA", "VGTSX")),
    "EM_STOCK": AssetClass("신흥국 주식", "USD", ("EEM", "VEIEX")),
    "KOSPI": AssetClass("한국 주식 (코스피 200)", "KRW", ("069500.KS", "^KS11")),
    "US_LTB": AssetClass("미국 장기 국채", "USD", ("TLT", "VUSTX")),
    "US_ITB": AssetClass("미국 중기 국채", "USD", ("IEF", "VFITX")),
    "US_STB": AssetClass("미국 초단기 국채·현금", "USD", ("BIL", RF)),
    "US_AGG": AssetClass("미국 종합 채권", "USD", ("AGG", "VBMFX")),
    "US_TIPS": AssetClass("미국 물가연동채", "USD", ("TIP", "VIPSX")),
    "GOLD": AssetClass("금", "USD", ("GLD", "GC=F", GOLD_WB),
                       "2000년 8월 이전은 세계은행 금 월평균 가격(1968년~)으로 채워서, 그 기간은 한 달에 한 번만 값이 바뀌어요"),
    "COMMODITY": AssetClass("원자재", "USD", ("DBC", "^SPGSCI")),
    "REIT": AssetClass("미국 리츠", "USD", ("VNQ", "VGSIX")),
    "BTC": AssetClass("비트코인", "USD", ("BTC-USD",), "야후 데이터는 2014년 9월부터"),
    "BTC_TREND": AssetClass(
        "비트코인 40/20 돌파 (쉬는 동안 초단기 국채)", "USD", (BTC_TREND_T,),
        "비트코인 트레이딩 화면과 같은 규칙이에요. 종가가 직전 40일 최고 종가를 넘으면 비트코인을 사고, "
        "직전 20일 최저 종가 아래로 내려가면 팔아서 미국 초단기 국채(BIL)에 둬요. 신호 난 날 종가(UTC 0시)에 "
        "바꾸고 수수료는 한 번에 0.05%예요. 적립·리밸런싱 때는 이 몫을 한 자산으로 다뤄서, "
        "쉬는 동안 들어온 적립금은 국채에 쌓였다가 매수 신호 때 함께 비트코인으로 바뀌어요"),
}
TREND_BASE = {"BTC_TREND": "BTC"}   # 돌파 전략 자산 ← 원래 자산 (돌파 버전 함께 비교할 때 바꿔 넣는다)

TICKER_NOTES = {
    "VFINX": "뱅가드 S&P 500 펀드 (배당 재투자)",
    "^GSPC": "S&P 500 지수 (배당 제외)",
    "VTSMX": "뱅가드 미국 전체 주식 펀드",
    "^NDX": "나스닥 100 지수 (배당 제외)",
    "VGTSX": "뱅가드 미국 외 전체 주식 펀드 (신흥국 포함)",
    "VEIEX": "뱅가드 신흥국 주식 펀드",
    "^KS11": "코스피 지수 (배당 제외)",
    "VUSTX": "뱅가드 장기 국채 펀드",
    "VFITX": "뱅가드 중기 국채 펀드",
    RF: "미국 3개월 국채 금리로 계산한 이자",
    "VBMFX": "뱅가드 종합 채권 펀드",
    "VIPSX": "뱅가드 물가연동채 펀드",
    "GC=F": "금 선물 (근월물)",
    GOLD_WB: "세계은행 금 월평균 가격 (런던 시세, 월별)",
    BTC_TREND_T: "BTC-USD 일봉 40/20 신호로 비트코인 ↔ 미국 초단기 국채(BIL) 전환",
    "^SPGSCI": "S&P GSCI 원자재 지수",
    "VGSIX": "뱅가드 리츠 펀드",
}

# 내 포트폴리오 종목 → 자산군 (내 목표 비중으로 백테스트할 때 사용)
SYMBOL_TO_CLASS = {
    "SPY": "US_STOCK", "SPYM": "US_STOCK", "SPLG": "US_STOCK", "VOO": "US_STOCK", "IVV": "US_STOCK",
    "360750.KS": "US_STOCK", "379800.KS": "US_STOCK",
    "VTI": "US_TOTAL", "QQQ": "NASDAQ100", "QQQM": "NASDAQ100", "133690.KS": "NASDAQ100", "379810.KS": "NASDAQ100",
    "SCHD": "US_DIV", "402970.KS": "US_DIV", "458730.KS": "US_DIV",
    "069500.KS": "KOSPI", "102110.KS": "KOSPI",
    "TLT": "US_LTB", "IEF": "US_ITB", "BIL": "US_STB", "SGOV": "US_STB", "SHV": "US_STB",
    "AGG": "US_AGG", "BND": "US_AGG", "TIP": "US_TIPS",
    "GLD": "GOLD", "GLDM": "GOLD", "IAU": "GOLD", "411060.KS": "GOLD",
    "DBC": "COMMODITY", "VNQ": "REIT", "BTC-USD": "BTC", "BTC-KRW": "BTC",
}

PRESETS: dict[str, dict[str, float]] = {
    "주식 60 / 채권 40": {"US_STOCK": 60, "US_ITB": 40},
    "주식 60 / 장기채 30 / 금 10": {"US_STOCK": 60, "US_LTB": 30, "GOLD": 10},
    "올웨더 (레이 달리오)": {"US_STOCK": 30, "US_LTB": 40, "US_ITB": 15, "GOLD": 7.5, "COMMODITY": 7.5},
    "영구 포트폴리오 (해리 브라운)": {"US_STOCK": 25, "US_LTB": 25, "US_STB": 25, "GOLD": 25},
    "S&P 500 100%": {"US_STOCK": 100},
}


# ── 자산 이름·통화 ─────────────────────────────────────────

def label_of(key: str) -> str:
    if key in CATALOG:
        return CATALOG[key].label
    return key[len(CUSTOM):] if key.startswith(CUSTOM) else key


def chain_of(key: str) -> tuple[str, ...]:
    return CATALOG[key].chain if key in CATALOG else (key[len(CUSTOM):] if key.startswith(CUSTOM) else key,)


def currency_of(key: str) -> str:
    if key in CATALOG:
        return CATALOG[key].currency
    t = chain_of(key)[0]
    return "KRW" if t.endswith((".KS", ".KQ", "-KRW")) or t.startswith("^KS") or t.startswith("^KQ") else "USD"


def custom_key(ticker: str) -> str:
    t = ticker.strip().upper()
    for k, a in CATALOG.items():
        if a.chain[0] == t:
            return k
    return CUSTOM + t


# ── 시세 받기 (Streamlit Cloud에서는 yfinance만 됨) ────────────

@st.cache_data(ttl=12 * 3600, show_spinner=False)
def fetch_history(tickers: tuple[str, ...]) -> dict[str, pd.Series]:
    """티커별 전체 기간 일별 종가(배당·분할 반영). {티커: Series}. 실패한 티커는 빠진다."""
    tickers = tuple(t for t in tickers if t not in LOCAL_SOURCES)
    if not tickers:
        return {}
    raw = yf.download(list(tickers), period="max", interval="1d", auto_adjust=True,
                      progress=False, group_by="ticker", threads=True, timeout=20)
    out: dict[str, pd.Series] = {}
    if raw is None or raw.empty:
        return out
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            s = df["Close"].dropna()
        except KeyError:
            continue
        s = s[s > 0] if t != RF else s[s >= 0]
        if not s.empty:
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            out[t] = s.astype(float)
    return out


def gold_monthly_history() -> pd.Series:
    """세계은행(Pink Sheet) 금 월평균 가격(USD/온스, 1968-01~2000-12)을 영업일 시계열로 바꾼다.

    한 달 동안은 그달 평균값을 그대로 두고 다음 달 첫 영업일에 바뀐다(계단 모양).
    부드럽게 이어 그리면 하루 변동이 실제보다 훨씬 작게 잡히므로 계단으로 둔다.
    출처: World Bank Commodity Markets, datahub.io/core/gold-prices (PDDL, 퍼블릭 도메인).
    """
    try:
        df = pd.read_csv(DATA_DIR / "gold_usd_monthly.csv")
    except OSError:
        return pd.Series(dtype=float)
    m = pd.Series(df["usd"].astype(float).to_numpy(), index=pd.to_datetime(df["month"], format="%Y-%m"))
    days = pd.bdate_range(m.index[0], m.index[-1] + pd.offsets.MonthEnd(0))
    return m.reindex(m.index.union(days)).ffill().reindex(days)


LOCAL_SOURCES = {GOLD_WB: gold_monthly_history}   # 네트워크 없이 파일에서 읽는 '티커'


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_fred_krw() -> pd.Series:
    """연준(FRED) 원/달러 환율(DEXKOUS, 1981년~). 야후 KRW=X(2003년 말~) 이전 기간을 채운다."""
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS", timeout=10)
        r.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
        s.index = pd.to_datetime(df.iloc[:, 0])
        return s.dropna()
    except Exception:
        return pd.Series(dtype=float)


# ── 이어 붙이기 ───────────────────────────────────────────

def rate_to_index(yield_pct: pd.Series) -> pd.Series:
    """연 금리(%) 시계열을 '그 금리로 이자를 받는 현금' 가격 지수로 바꾼다."""
    y = yield_pct.sort_index()
    days = y.index.to_series().diff().dt.days.fillna(0).to_numpy()
    growth = (1 + y.shift(1).fillna(y.iloc[0]).to_numpy() / 100) ** (days / 365.0)
    return pd.Series(np.cumprod(growth), index=y.index)


def splice(chain: tuple[str, ...], hist: dict[str, pd.Series]) -> tuple[pd.Series, list[dict]]:
    """앞 티커를 기준으로, 앞 티커가 없는 과거 구간을 뒤 티커의 수익률로 채운 가격 지수와 구간 기록."""
    series: pd.Series | None = None
    segments: list[dict] = []
    for t in chain:
        s = hist.get(t)
        if s is None and t in LOCAL_SOURCES:
            s = LOCAL_SOURCES[t]()
        if s is None or len(s) < 2:
            continue
        s = rate_to_index(s) if t == RF else s.sort_index()
        if series is None:
            series = s
            segments.append({"ticker": t, "start": s.index[0], "end": s.index[-1]})
            continue
        first = series.index[0]
        older = s[s.index <= first]
        if len(older) < 2:
            continue
        # 이어 붙이는 날(first, 없으면 그 직전 거래일)에서 값을 맞춘다
        scaled = older / older.iloc[-1] * series.iloc[0]
        series = pd.concat([scaled[scaled.index < first], series])
        segments.insert(0, {"ticker": t, "start": scaled.index[0], "end": first})
    return (series if series is not None else pd.Series(dtype=float)), segments


def fx_series(hist: dict[str, pd.Series]) -> pd.Series:
    """원/달러 환율: 야후 KRW=X, 그 이전은 FRED로 채운다."""
    y = hist.get(FX, pd.Series(dtype=float)).sort_index()
    f = fetch_fred_krw()
    if f.empty:
        return y
    if y.empty:
        return f
    return pd.concat([f[f.index < y.index[0]], y])


# ── 비트코인 40/20 돌파 주머니 ─────────────────────────────

def trend_sleeve(btc: pd.Series, cash: pd.Series, buy_n: int = TREND_BUY_N, sell_n: int = TREND_SELL_N,
                 fee_pct: float = TREND_FEE) -> tuple[pd.Series, pd.Series]:
    """(주머니 가격 지수(USD, 1에서 시작), 보유 상태(1=비트코인, 0=국채)). 날짜는 비트코인 일봉 기준.

    신호는 lib.btc_trading과 같다(오늘 종가 vs 오늘을 뺀 직전 N일). 신호 난 날 종가에 바꾸므로
    그날 수익률은 바꾸기 전 자산을 따르고, 바꿀 때마다 수수료를 뗀다.
    """
    from lib import btc_trading   # 비트코인 화면과 같은 신호 함수를 쓴다

    btc = btc.sort_index()
    pos = btc_trading.compute_signals(pd.DataFrame({"Close": btc}), buy_n, sell_n)["Position"]
    c = cash.sort_index()
    c = c.reindex(c.index.union(btc.index)).ffill().reindex(btc.index).bfill()
    held = pos.shift(1).fillna(0.0)
    trade = pos.diff().abs().fillna(pos.abs())
    growth = 1 + held * btc.pct_change().fillna(0.0) + (1 - held) * c.pct_change().fillna(0.0)
    return (growth * (1 - fee_pct / 100 * trade)).cumprod(), pos


def add_derived(hist: dict[str, pd.Series], keys) -> tuple[dict[str, pd.Series], pd.Series | None]:
    """돌파 주머니가 필요하면 받은 시세로 만들어 hist에 넣는다(원본은 건드리지 않음). (hist, 보유 상태)"""
    if not any(k in TREND_BASE for k in keys) or BTC_TREND_T in hist:
        return hist, None
    btc = hist.get("BTC-USD")
    cash, _ = splice(CATALOG["US_STB"].chain, hist)
    if btc is None or len(btc) < 2 or cash.empty:
        return hist, None
    sleeve, pos = trend_sleeve(btc, cash)
    return {**hist, BTC_TREND_T: sleeve}, pos


def trend_variant(p: "Portfolio") -> "Portfolio | None":
    """비트코인 몫을 돌파 전략으로 바꾼 조합. 비트코인이 없으면 None."""
    base_to_trend = {v: k for k, v in TREND_BASE.items()}
    if not any(base_to_trend.get(k) and v > 0 for k, v in p.weights.items()):
        return None
    w: dict[str, float] = {}
    for k, v in p.weights.items():
        nk = base_to_trend.get(k, k)
        w[nk] = w.get(nk, 0) + v
    return Portfolio(p.name + TREND_SUFFIX, w)


TREND_SUFFIX = " · 비트코인 돌파"


# ── 시뮬레이션 ────────────────────────────────────────────

def _period_key(ts: pd.Timestamp, kind: str):
    if kind == "quarterly":
        return (ts.year, (ts.month - 1) // 3)
    if kind == "semiannual":
        return (ts.year, (ts.month - 1) // 6)
    if kind == "yearly":
        return ts.year
    return (ts.year, ts.month)


def simulate(prices: pd.DataFrame, weights: dict[str, float], initial: float, monthly: float,
             rebalance: str) -> pd.DataFrame:
    """가격표(열=자산, 기준 통화)와 비중으로 매일 평가금액을 계산한다.

    - 첫날 초기 금액을 비중대로 산다.
    - 매달 첫 거래일에 월 적립금을 비중대로 산다.
    - 리밸런싱 주기의 첫 거래일에 전체를 목표 비중으로 맞춘다.
    돌려주는 표: value(평가금액), invested(누적 투자금), nav(적립금 영향을 뺀 수익 지수, 1에서 시작).
    """
    cols = [c for c in weights if c in prices.columns]
    w = np.array([weights[c] for c in cols], dtype=float)
    w = w / w.sum()
    p = prices[cols].to_numpy(dtype=float)
    idx = prices.index
    units = initial * w / p[0]
    invested = initial
    nav = 1.0
    prev_val = initial
    out_val, out_inv, out_nav = [initial], [initial], [1.0]
    for i in range(1, len(idx)):
        val = float(units @ p[i])
        nav *= val / prev_val if prev_val > 0 else 1.0
        new_month = _period_key(idx[i], "monthly") != _period_key(idx[i - 1], "monthly")
        if new_month and monthly > 0:
            units = units + monthly * w / p[i]
            invested += monthly
            val += monthly
        if rebalance != "none" and _period_key(idx[i], rebalance) != _period_key(idx[i - 1], rebalance):
            units = val * w / p[i]
        prev_val = val
        out_val.append(val)
        out_inv.append(invested)
        out_nav.append(nav)
    return pd.DataFrame({"value": out_val, "invested": out_inv, "nav": out_nav}, index=idx)


def xirr(flows: list[tuple[pd.Timestamp, float]]) -> float | None:
    """현금흐름(투자는 음수, 마지막 평가금액은 양수)의 연 내부수익률. 이분법으로 푼다."""
    if len(flows) < 2:
        return None
    t0 = flows[0][0]
    yrs = np.array([(d - t0).days / 365.25 for d, _ in flows])
    amt = np.array([a for _, a in flows])

    def npv(r):
        return float(np.sum(amt / (1 + r) ** yrs))

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def metrics(sim: pd.DataFrame, rf_daily: pd.Series | None = None) -> dict:
    nav = sim["nav"]
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    rets = nav.pct_change().dropna()
    per_year = len(rets) / years if years > 0 else 252
    cagr = nav.iloc[-1] ** (1 / years) - 1
    vol = float(rets.std() * np.sqrt(per_year)) if len(rets) > 1 else 0.0
    excess = rets
    if rf_daily is not None and not rf_daily.empty:
        excess = rets - rf_daily.reindex(rets.index).ffill().fillna(0)
    sharpe = float(excess.mean() * per_year / vol) if vol > 0 else None
    dd = nav / nav.cummax() - 1
    yearly = yearly_returns(nav)
    flows = [(sim.index[0], -sim["invested"].iloc[0])]
    contrib = sim["invested"].diff().fillna(0)
    flows += [(d, -a) for d, a in contrib[contrib > 0].items()]
    flows.append((sim.index[-1], float(sim["value"].iloc[-1])))
    peak_spell = longest_spell(nav < nav.cummax())
    loss_spell = longest_spell(sim["value"] < sim["invested"] - 1e-9)
    value_spell = longest_spell(sim["value"] < sim["value"].cummax())
    return {
        "start": nav.index[0], "end": nav.index[-1], "years": years,
        "invested": float(sim["invested"].iloc[-1]), "final": float(sim["value"].iloc[-1]),
        "cagr": float(cagr), "irr": xirr(flows), "vol": vol, "mdd": float(dd.min()),
        "mdd_date": dd.idxmin(), "sharpe": sharpe,
        "peak_recovery": peak_spell,       # 수익률 지수(nav)가 고점 아래 있던 가장 긴 기간
        "value_peak_recovery": value_spell,  # 적립금 포함 잔고가 고점 아래 있던 가장 긴 기간
        "loss_recovery": loss_spell,       # 평가금액이 투자 원금보다 적었던 가장 긴 기간
        "best_year": (int(yearly.idxmax()), float(yearly.max())) if not yearly.empty else None,
        "worst_year": (int(yearly.idxmin()), float(yearly.min())) if not yearly.empty else None,
    }


def months_between(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return (b - a).days / 30.4375


def longest_spell(below: pd.Series) -> dict | None:
    """True가 이어진 가장 긴 구간. {start, end, months, recovered}. 끝까지 회복 못 했으면 recovered=False.

    start = 처음 아래로 내려간 날, end = 다시 회복한 날(회복 못 했으면 마지막 날).
    """
    best = None
    start = None
    dates = below.index
    for i, (d, u) in enumerate(below.items()):
        if u and start is None:
            start = d
        if start is not None and (not u or i == len(dates) - 1):
            recovered = not u
            spell = {"start": start, "end": d, "months": months_between(start, d), "recovered": recovered}
            if best is None or spell["months"] > best["months"]:
                best = spell
            start = None
    return best


def yearly_returns(nav: pd.Series) -> pd.Series:
    """연도별 수익률. 첫해·마지막 해는 그해 안의 구간만."""
    ends = nav.groupby(nav.index.year).last()
    starts = pd.concat([pd.Series([nav.iloc[0]], index=[nav.index[0].year]), ends.shift(1).dropna()])
    starts = starts[~starts.index.duplicated(keep="last")]
    return (ends / starts.reindex(ends.index) - 1).dropna()


def drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1


# ── 한 번에 돌리기 ─────────────────────────────────────────

@dataclass
class Portfolio:
    name: str
    weights: dict[str, float]


@dataclass
class Result:
    name: str
    sim: pd.DataFrame
    stats: dict
    weights: dict[str, float]
    other: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # 다른 통화로 환산한 평가금액


@dataclass
class Run:
    results: list[Result]
    base: str
    start: pd.Timestamp
    end: pd.Timestamp
    sources: pd.DataFrame        # 자산군별 사용한 데이터 구간
    notes: list[str]
    fx: pd.Series
    settings: dict = field(default_factory=dict)   # 화면에서 고른 옵션 (저장·불러오기용)


def needed_tickers(keys: set[str]) -> tuple[str, ...]:
    ts = {FX, RF}
    for k in keys:
        if k in TREND_BASE:   # 돌파 주머니는 비트코인과 초단기 국채 시세로 만든다
            ts.update(chain_of(TREND_BASE[k]) + CATALOG["US_STB"].chain)
        else:
            ts.update(chain_of(k))
    return tuple(sorted(ts - set(LOCAL_SOURCES) - {BTC_TREND_T}))


def run_backtest(portfolios: list[Portfolio], start: date, end: date, initial: float, monthly: float,
                 rebalance: str, base: str, hist: dict[str, pd.Series]) -> Run:
    keys = {k for p in portfolios for k, v in p.weights.items() if v > 0}
    notes: list[str] = []
    hist, trend_pos = add_derived(hist, keys)
    fx = fx_series(hist)
    series: dict[str, pd.Series] = {}
    rows = []
    for k in sorted(keys, key=label_of):
        s, segs = splice(chain_of(k), hist)
        if s.empty:
            notes.append(f"'{label_of(k)}' 시세를 받지 못해 뺐어요.")
            continue
        ccy = currency_of(k)
        if ccy != base:
            f = fx.reindex(s.index.union(fx.index)).ffill().reindex(s.index)
            s = (s * f if base == "KRW" else s / f).dropna()
            if s.empty:
                notes.append(f"'{label_of(k)}' 기간의 환율이 없어 뺐어요.")
                continue
        series[k] = s
        for seg in segs:
            rows.append({"자산": label_of(k), "티커": seg["ticker"],
                         "설명": TICKER_NOTES.get(seg["ticker"], "대표 ETF" if seg is segs[-1] else ""),
                         "시작": seg["start"].date(), "끝": seg["end"].date()})
        if k in CATALOG and CATALOG[k].note:
            notes.append(f"{CATALOG[k].label}: {CATALOG[k].note}")
    sources = pd.DataFrame(rows, columns=["자산", "티커", "설명", "시작", "끝"])
    if any(currency_of(k) != base for k in series) and fetch_fred_krw().empty:
        notes.append("미국 연준(FRED) 환율을 받지 못해 원/달러 환율은 야후(2003년 12월~) 것만 썼어요. "
                     "더 이전부터 보려면 계산 통화를 달러로 바꿔 보세요.")

    want_start, want_end = pd.Timestamp(start), pd.Timestamp(end)
    results: list[Result] = []
    common_start = want_start
    for p in portfolios:
        ks = [k for k, v in p.weights.items() if v > 0 and k in series]
        if ks:
            common_start = max([common_start] + [series[k].index[0] for k in ks])
    if common_start > want_start:
        late = [label_of(k) for k in series if series[k].index[0] >= common_start - pd.Timedelta(days=7)]
        notes.insert(0, f"{', '.join(late)} 데이터가 {common_start.date()}부터 있어서, 모든 조합을 이날부터 비교해요.")

    rf_idx = series_rf(hist)
    for p in portfolios:
        w = {k: v for k, v in p.weights.items() if v > 0 and k in series}
        if not w:
            continue
        frame = pd.concat({k: series[k] for k in w}, axis=1).sort_index().ffill()
        frame = frame[(frame.index >= common_start) & (frame.index <= want_end)].dropna()
        if len(frame) < 2:
            notes.append(f"'{p.name}'은(는) 기간 안에 데이터가 부족해요.")
            continue
        sim = simulate(frame, w, initial, monthly, rebalance)
        stats = metrics(sim, rf_idx)
        f = fx.reindex(sim.index.union(fx.index)).ffill().reindex(sim.index)
        other = sim["value"] / f if base == "KRW" else sim["value"] * f
        results.append(Result(p.name, sim, stats, w, other))
    real_start = min((r.sim.index[0] for r in results), default=common_start)
    real_end = max((r.sim.index[-1] for r in results), default=want_end)
    if trend_pos is not None and any("BTC_TREND" in r.weights for r in results):
        notes.append(trend_summary(trend_pos, real_start, real_end))
    return Run(results, base, real_start, real_end, sources, notes, fx)


def trend_summary(pos: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> str:
    """기간 안에서 돌파 전략이 비트코인을 들고 있던 비율과 매수 횟수, 마지막 상태."""
    p = pos[(pos.index >= start) & (pos.index <= end)]
    if p.empty:
        return ""
    buys = int((p.diff().fillna(p) > 0).sum())
    last = "비트코인 보유 중" if p.iloc[-1] > 0 else "국채에서 쉬는 중"
    return (f"비트코인 40/20 돌파: 기간 중 {p.mean() * 100:.0f}% 동안 비트코인을 들고 있었고, 매수 {buys}번, "
            f"{p.index[-1].date()} 기준 {last}이에요.")


def series_rf(hist: dict[str, pd.Series]) -> pd.Series:
    """일별 무위험 수익률(미국 3개월 국채)."""
    y = hist.get(RF)
    if y is None or y.empty:
        return pd.Series(dtype=float)
    return rate_to_index(y).pct_change().fillna(0)


# ── 내 포트폴리오에서 가져오기 ────────────────────────────

def weights_from_targets(targets: pd.DataFrame, assets: pd.DataFrame) -> tuple[dict[str, float], list[str]]:
    """포트폴리오 화면의 자산군 목표 비중을 백테스트 자산으로 옮긴다. (비중, 안내문)"""
    out: dict[str, float] = {}
    notes: list[str] = []
    for _, r in targets.iterrows():
        wgt = float(r.get("target_weight") or 0)
        if wgt <= 0:
            continue
        cls = r["asset_class"]
        syms = [s for s in assets.loc[assets["asset_class"] == cls, "symbol"].tolist() if not str(s).startswith("MANUAL:")]
        key = next((SYMBOL_TO_CLASS[s] for s in syms if s in SYMBOL_TO_CLASS), None)
        if key is None and syms:
            key = CUSTOM + syms[0]
        if key is None:
            notes.append(f"'{cls}'({wgt:g}%)은 티커가 없어 뺐어요.")
            continue
        out[key] = out.get(key, 0) + wgt
        notes.append(f"{cls} {wgt:g}% → {label_of(key)}")
    return out, notes


# ── 저장한 조합 (Supabase backtest_presets) ───────────────

def list_presets() -> list[dict]:
    return get_client().table("backtest_presets").select("*").order("id").execute().data


def save_preset(name: str, weights: dict[str, float], settings: dict) -> None:
    db = get_client()
    payload = {"name": name, "allocations": {k: float(v) for k, v in weights.items() if v > 0},
               "start_date": str(settings["start"]), "end_date": str(settings["end"]),
               "rebalance": settings["rebalance"], "initial_amount": float(settings["initial"]),
               "monthly_contribution": float(settings["monthly"]), "benchmark": settings.get("benchmark")}
    existing = [p for p in list_presets() if p["name"] == name]
    if existing:
        db.table("backtest_presets").update(payload).eq("id", existing[0]["id"]).execute()
    else:
        db.table("backtest_presets").insert(payload).execute()


def delete_preset(preset_id: int) -> None:
    get_client().table("backtest_presets").delete().eq("id", preset_id).execute()


# ── 저장한 백테스트 (설정 + 결과, Supabase backtest_runs) ─────

SAVE_FREQ = "W-FRI"   # 곡선은 주 단위로 줄여 저장한다 (30년 ≈ 1,600점). 지표는 매일 기준으로 계산한 값을 그대로 저장


def _enc(v):
    """JSON으로 저장할 수 있게 날짜·numpy 값을 바꾼다."""
    if isinstance(v, pd.Timestamp):
        return {"__ts": v.isoformat()}
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _dec(v):
    if isinstance(v, dict):
        if set(v) == {"__ts"}:
            return pd.Timestamp(v["__ts"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def _thin(df: pd.DataFrame) -> pd.DataFrame:
    """주 단위로 줄이되 첫날·마지막 날은 남긴다."""
    weekly = df.resample(SAVE_FREQ).last().dropna(how="all")
    weekly.index = [df.index[df.index <= d][-1] for d in weekly.index]
    return pd.concat([df.iloc[:1], weekly, df.iloc[-1:]]).pipe(lambda x: x[~x.index.duplicated(keep="last")])


def run_to_json(run: Run) -> dict:
    results = []
    for r in run.results:
        frame = _thin(r.sim.assign(other=r.other))
        results.append({
            "name": r.name, "weights": r.weights, "stats": _enc(r.stats),
            "dates": [d.strftime("%Y-%m-%d") for d in frame.index],
            **{c: [_enc(x) for x in frame[c]] for c in ("value", "invested", "nav", "other")},
        })
    src = run.sources.copy()
    for c in ("시작", "끝"):
        src[c] = src[c].astype(str)
    return {"base": run.base, "start": run.start.isoformat(), "end": run.end.isoformat(),
            "notes": run.notes, "sources": src.to_dict("records"), "results": results}


def run_from_json(d: dict, settings: dict | None = None) -> Run:
    results = []
    for r in d["results"]:
        idx = pd.to_datetime(r["dates"])
        sim = pd.DataFrame({c: pd.Series(r[c], dtype=float).to_numpy() for c in ("value", "invested", "nav")}, index=idx)
        other = pd.Series(pd.Series(r["other"], dtype=float).to_numpy(), index=idx)
        results.append(Result(r["name"], sim, _dec(r["stats"]), r["weights"], other))
    sources = pd.DataFrame(d.get("sources") or [], columns=["자산", "티커", "설명", "시작", "끝"])
    return Run(results, d["base"], pd.Timestamp(d["start"]), pd.Timestamp(d["end"]), sources,
               list(d.get("notes") or []), pd.Series(dtype=float), settings or {})


def list_runs() -> list[dict]:
    """저장한 백테스트 목록 (결과 본문 없이)."""
    return get_client().table("backtest_runs").select("id,name,settings,created_at").order("id").execute().data


def load_run(run_id: int) -> tuple[Run, dict]:
    row = get_client().table("backtest_runs").select("*").eq("id", run_id).execute().data[0]
    return run_from_json(row["results"], row["settings"]), row


def save_run(name: str, run: Run) -> None:
    get_client().table("backtest_runs").insert(
        {"name": name, "settings": _enc(run.settings), "results": run_to_json(run)}).execute()


def delete_run(run_id: int) -> None:
    get_client().table("backtest_runs").delete().eq("id", run_id).execute()
