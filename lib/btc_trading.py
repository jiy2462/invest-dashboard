"""비트코인 트레이딩: 변형 돈키안 채널 돌파 전략 (매수 40일 / 매도 20일).

규칙 (기본값)
- 매수: 종가가 '직전 40일' 최고 종가를 넘으면 (오늘은 빼고 계산 → 미래 참조 없음)
- 매도: 종가가 '직전 20일' 최저 종가 아래로 내려가면
진입은 길게, 청산은 짧게 잡아 상승 추세는 오래 타고 하락은 빨리 끊는 비대칭 변형이다.

포지션은 '마지막으로 나온 신호'를 따른다. 40일 최고가 ≥ 20일 최저가라서 한 날에 매수·매도가
함께 나올 수 없으므로, 매수 신호 뒤에는 매도 신호가 나올 때까지 계속 보유한 것으로 본다.
같은 방향 신호가 며칠 이어져도 실제 매매는 처음 한 번뿐이다.

하루 종가 기준 (비트코인은 24시간 거래라 '하루'를 정해야 한다)
- utc: 야후 일봉 = UTC 0시(한국 오전 9시) 마감
- ny : 미국 정규장 마감(뉴욕 16:00, 서머타임 자동 반영) 기준. 야후 1시간봉으로 만들며 최근 약 2년만
       있어서, 그 전은 UTC 일봉으로 잇는다.

설정은 app_settings 한 줄(key=btc_trading)에 저장한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from lib import charting, live_quote
from lib.db import get_client

KEY = "btc_trading"
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
US_CLOSE = time(16, 0)

SYMBOLS = {"BTC-USD": "비트코인 (달러)", "BTC-KRW": "비트코인 (원화)"}
BASIS = {"utc": "UTC 0시 마감 (한국 오전 9시)", "ny": "미국 장마감 (뉴욕 16시)"}
DEFAULTS = {"symbol": "BTC-USD", "buy_n": 40, "sell_n": 20, "basis": "utc", "fee_pct": 0.05}


# ── 설정 ───────────────────────────────────────────────────
def clean(cfg: dict | None) -> dict:
    """저장된 값이 비었거나 이상해도 기본값으로 채운다."""
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in out and v is not None:
            out[k] = v
    out["buy_n"] = int(min(max(int(out["buy_n"]), 2), 365))
    out["sell_n"] = int(min(max(int(out["sell_n"]), 2), 365))
    out["fee_pct"] = float(min(max(float(out["fee_pct"]), 0.0), 5.0))
    if out["symbol"] not in SYMBOLS:
        out["symbol"] = DEFAULTS["symbol"]
    if out["basis"] not in BASIS:
        out["basis"] = DEFAULTS["basis"]
    return out


def load_settings() -> dict:
    rows = get_client().table("app_settings").select("*").eq("key", KEY).execute().data or []
    return clean(rows[0]["value"] if rows else None)


def save_settings(cfg: dict) -> dict:
    cfg = clean(cfg)
    get_client().table("app_settings").upsert({"key": KEY, "value": cfg}, on_conflict="key").execute()
    return cfg


# ── 시세 ───────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_hourly(symbol: str) -> pd.DataFrame:
    """야후 1시간봉 (최근 약 2년, 야후 한도 730일). 인덱스는 봉 시작 시각(UTC, 시간대 포함)."""
    df = yf.Ticker(symbol).history(period="729d", interval="60m", auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close"]].dropna(subset=["Close"])
    idx = pd.to_datetime(df.index)
    df.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert("UTC")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def fetch_live(symbol: str) -> live_quote.Quote | None:
    try:
        return live_quote.fetch_yahoo((symbol,)).get(symbol)
    except Exception:
        return None


def ny_sessions(bars: pd.DataFrame) -> pd.DataFrame:
    """1시간봉 → 뉴욕 16시 마감 일봉. 'D일 봉' = 전날 16:00 초과 ~ D일 16:00 이하에 끝난 1시간봉들.

    시가=첫 봉 시가, 고가·저가=구간 최고·최저, 종가=16:00에 끝난 봉의 종가.
    complete 열: 16:00 마감까지 다 찬 봉인지 (마지막 봉은 아직 진행 중일 수 있다).
    """
    if bars.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "complete"])
    end = (bars.index + pd.Timedelta(hours=1)).tz_convert(NY).tz_localize(None)  # 봉이 끝나는 뉴욕 시각
    session = (end - pd.Timedelta(hours=16, microseconds=1)).normalize() + pd.Timedelta(days=1)
    g = bars.assign(session=session.values, end=end.values).groupby("session")
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "last_end": g["end"].max()})
    out["complete"] = out["last_end"] >= out.index + pd.Timedelta(hours=16)
    out.index.name = None
    return out.drop(columns="last_end")


def load_prices(symbol: str, basis: str, now: datetime | None = None) -> tuple[pd.DataFrame, dict]:
    """전략에 쓸 일봉(Open/High/Low/Close)과 정보 {"partial": 마지막 봉이 아직 진행 중인가, "source", "asof"}."""
    now = now or datetime.now(timezone.utc)
    daily = charting.fetch_ohlcv(symbol, "1d", "max")
    if daily is None or daily.empty:
        return pd.DataFrame(), {}
    daily = daily[["Open", "High", "Low", "Close"]].copy()
    daily.index = pd.to_datetime(daily.index).normalize()
    daily = daily[~daily.index.duplicated(keep="last")]
    info = {"source": "야후 일봉 (UTC 0시 마감)", "asof": None}

    if basis == "ny":
        try:
            sess = ny_sessions(fetch_hourly(symbol))
        except Exception:
            sess = pd.DataFrame()
        if not sess.empty:
            first = sess[sess["complete"]].index.min() if sess["complete"].any() else sess.index.min()
            sess = sess[sess.index >= first]
            df = pd.concat([daily[daily.index < first], sess[["Open", "High", "Low", "Close"]]])
            info.update(source=f"야후 1시간봉 → 뉴욕 16시 마감 ({first:%Y-%m-%d}부터, 그 전은 UTC 일봉)",
                        partial=not bool(sess["complete"].iloc[-1]), switch=first)
            return df, info
        info["source"] += " · 1시간봉을 받지 못해 UTC 일봉으로 계산했어요"
        info["fallback"] = True

    q = fetch_live(symbol)
    if live_quote.is_recent(q, now):
        daily = live_quote.apply(daily, q)
        info["asof"] = q.at
    info["partial"] = bool(daily.index[-1].date() >= now.date())
    return daily, info


def gap_days(df: pd.DataFrame, days: int = 400) -> int:
    """최근 기간 중 빠진 날 수 (비트코인은 매일 거래하므로 0이어야 정상)."""
    if df.empty:
        return 0
    recent = df.index[df.index >= df.index[-1] - pd.Timedelta(days=days)]
    want = pd.date_range(recent.min(), recent.max(), freq="D")
    return int(len(want.difference(recent)))


def next_close(basis: str, now: datetime | None = None) -> datetime:
    """다음 일봉 마감 시각 (한국 시각)."""
    now = now or datetime.now(timezone.utc)
    if basis == "ny":
        ny = now.astimezone(NY)
        cut = datetime.combine(ny.date(), US_CLOSE, tzinfo=NY)
        if ny >= cut:
            cut = datetime.combine(ny.date() + timedelta(days=1), US_CLOSE, tzinfo=NY)
        return cut.astimezone(KST)
    utc = now.astimezone(timezone.utc)
    return datetime.combine(utc.date() + timedelta(days=1), time(0), tzinfo=timezone.utc).astimezone(KST)


# ── 신호 ───────────────────────────────────────────────────
def positions(buy: pd.Series, sell: pd.Series, start=None) -> pd.Series:
    """마지막 신호를 따르는 보유 상태(1=보유, 0=현금). start 이전은 현금으로 두고 그날부터 따진다."""
    state = pd.Series(np.where(buy, 1.0, np.where(sell, 0.0, np.nan)), index=buy.index)
    if start is not None:
        state[state.index < pd.Timestamp(start)] = np.nan
        first = state.index[state.index >= pd.Timestamp(start)]
        if len(first) and pd.isna(state[first[0]]):
            state[first[0]] = 0.0
    return state.ffill().fillna(0.0)


def compute_signals(df: pd.DataFrame, buy_n: int = 40, sell_n: int = 20) -> pd.DataFrame:
    close = df["Close"].astype(float)
    upper = close.shift(1).rolling(buy_n).max()
    lower = close.shift(1).rolling(sell_n).min()
    buy = (close > upper).fillna(False)
    sell = (close < lower).fillna(False)
    pos = positions(buy, sell)
    change = pos.diff().fillna(pos)
    out = df.copy()
    out["Upper"], out["Lower"] = upper, lower
    out["Buy"], out["Sell"] = buy, sell
    out["Position"] = pos
    out["Entry"], out["Exit"] = change > 0, change < 0
    return out


@dataclass
class Status:
    date: pd.Timestamp
    close: float
    holding: bool               # 확정 봉 기준 전략 상태
    entry_date: pd.Timestamp | None
    entry_price: float | None
    buy_line: float             # 지금 진행 중인(또는 다음) 봉에서 넘으면 매수인 가격
    sell_line: float            # 지금 진행 중인(또는 다음) 봉에서 깨면 매도인 가격
    partial: bool               # 마지막 봉이 아직 진행 중인가
    live_price: float | None = None
    pending: str | None = None  # 진행 중 가격으로 지금 마감하면 생길 매매 ("매수"/"매도")
    extra: dict = field(default_factory=dict)

    @property
    def watch_line(self) -> float:
        return self.sell_line if self.holding else self.buy_line

    @property
    def price(self) -> float:
        return self.live_price if self.live_price is not None else self.close

    @property
    def gap_pct(self) -> float:
        """지금 가격 기준, 다음 매매 기준선까지 몇 % 남았나 (보유 중이면 매도선까지 하락폭)."""
        return (self.watch_line / self.price - 1) * 100


def status(sig: pd.DataFrame, partial: bool, buy_n: int, sell_n: int) -> Status:
    """확정된 봉까지의 상태와, 지금(또는 다음) 봉에서 지켜볼 기준선."""
    done = sig.iloc[:-1] if partial else sig
    last = done.iloc[-1]
    holding = bool(last["Position"] > 0)
    entry_date = entry_price = None
    if holding:
        entries = done.index[done["Entry"]]
        if len(entries):
            entry_date = entries[-1]
            entry_price = float(done.loc[entry_date, "Close"])
    buy_line = float(done["Close"].tail(buy_n).max())
    sell_line = float(done["Close"].tail(sell_n).min())
    st_ = Status(date=done.index[-1], close=float(last["Close"]), holding=holding, entry_date=entry_date,
                 entry_price=entry_price, buy_line=buy_line, sell_line=sell_line, partial=partial)
    if partial:
        now_price = float(sig["Close"].iloc[-1])
        st_.live_price = now_price
        if not holding and now_price > buy_line:
            st_.pending = "매수"
        elif holding and now_price < sell_line:
            st_.pending = "매도"
    return st_


# ── 백테스트 ───────────────────────────────────────────────
@dataclass
class Result:
    equity: pd.DataFrame          # 전략 / 보유 (시작=1)
    trades: pd.DataFrame
    stats: dict[str, dict]        # {"전략": {...}, "그냥 보유": {...}}


def _stats(eq: pd.Series, exposure: float | None = None) -> dict:
    eq = eq.dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    out = {"total": float(total), "cagr": float(cagr), "mdd": mdd}
    if exposure is not None:
        out["exposure"] = exposure
    return out


def backtest(sig: pd.DataFrame, start=None, fee_pct: float = 0.05) -> Result:
    """신호가 난 날 종가에 사고판다고 보고 계산한다. 수수료는 한 번 사고팔 때마다 fee_pct%씩."""
    s = sig if start is None else sig[sig.index >= pd.Timestamp(start)]
    if len(s) < 2:
        raise ValueError("기간이 너무 짧아요.")
    pos = positions(sig["Buy"], sig["Sell"], start=s.index[0]).loc[s.index]
    ret = s["Close"].pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0)
    trade = pos.diff().abs().fillna(pos.abs())
    fee = fee_pct / 100
    strat = ((1 + held * ret) * (1 - fee * trade)).cumprod()
    hold = s["Close"] / s["Close"].iloc[0]
    equity = pd.DataFrame({"전략": strat, "그냥 보유": hold})

    rows = []
    entry = None
    for t, p in pos.items():
        if p > 0 and entry is None:
            entry = t
        elif p == 0 and entry is not None:
            rows.append((entry, t))
            entry = None
    if entry is not None:
        rows.append((entry, None))
    trades = []
    for a, b in rows:
        pa = float(s.loc[a, "Close"])
        pb = float(s.loc[b, "Close"]) if b is not None else float(s["Close"].iloc[-1])
        r = (pb * (1 - fee)) / (pa * (1 + fee)) - 1
        trades.append({"매수일": a.date(), "매수가": pa, "매도일": b.date() if b is not None else None,
                       "매도가": pb, "수익률": r * 100,
                       "보유일수": int(((b if b is not None else s.index[-1]) - a).days),
                       "상태": "완료" if b is not None else "보유 중"})
    trades = pd.DataFrame(trades, columns=["매수일", "매수가", "매도일", "매도가", "수익률", "보유일수", "상태"])

    done = trades[trades["상태"] == "완료"]
    sstats = _stats(strat, exposure=float(held.mean()))
    sstats.update(trades=int(len(trades)),
                  win_rate=float((done["수익률"] > 0).mean()) if len(done) else None,
                  avg_win=float(done.loc[done["수익률"] > 0, "수익률"].mean()) if (done["수익률"] > 0).any() else None,
                  avg_loss=float(done.loc[done["수익률"] <= 0, "수익률"].mean()) if (done["수익률"] <= 0).any() else None)
    return Result(equity=equity, trades=trades, stats={"전략": sstats, "그냥 보유": _stats(hold, exposure=1.0)})


def grid(df: pd.DataFrame, start, fee_pct: float, buys=(20, 30, 40, 55, 70), sells=(10, 15, 20, 30)) -> pd.DataFrame:
    """매수·매도 기간 조합별 연평균 수익률과 최대 낙폭. 지금 고른 40/20이 우연히 좋은 값인지 보는 용도."""
    rows = []
    for b in buys:
        for s_ in sells:
            r = backtest(compute_signals(df, b, s_), start=start, fee_pct=fee_pct).stats["전략"]
            rows.append({"매수(일)": b, "매도(일)": s_, "연평균": r["cagr"] * 100, "최대 낙폭": r["mdd"] * 100,
                         "거래 수": r["trades"]})
    return pd.DataFrame(rows)
