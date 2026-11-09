"""장중 실시간 시세: 일봉 마지막 줄(오늘 봉)을 지금 가격으로 덮어쓰거나 새로 붙인다.

야후 일봉은 장이 열린 직후 한동안 오늘 줄이 없거나 늦게 바뀐다 (한국·일본 지수는 야후 시세 자체가
약 20분 늦음). 그래서 장중인 지표만 따로
- 한국 지수·종목: 네이버 실시간 시세 (해외 서버에서 막히면 야후로)
- 그 밖: 야후 1분봉 (여러 티커를 한 번에)
을 받아 오늘 봉을 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from lib import market_hours

KST = ZoneInfo("Asia/Seoul")
MAX_AGE = timedelta(hours=12)  # 이보다 오래된 시세는 지난 장 것이라 쓰지 않는다 (장 하나는 8시간 안쪽)

NAVER_INDEX = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ", "^KS200": "KPI200"}
NAVER_URLS = (
    "https://polling.finance.naver.com/api/realtime/domestic/{kind}/{code}",
    "https://m.stock.naver.com/api/{kind}/{code}/basic",
)
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://m.stock.naver.com/"}


@dataclass(frozen=True)
class Quote:
    price: float
    at: datetime          # 시세 시각 (UTC)
    day: date             # 이 시세가 속한 거래일 (일봉 날짜와 맞출 때)
    open: float | None = None
    high: float | None = None
    low: float | None = None
    source: str = "야후"


def day_of(symbol: str, ts: datetime) -> date:
    """시세 시각이 일봉 몇 일 자 줄에 들어가는지.

    거래소 지수·주식은 거래소 현지 날짜. 환율·선물은 야후가 붙이는 날짜가 뉴욕/런던 기준으로 섞여
    있어서, 둘 중 이른 뉴욕 날짜를 쓴다 (늦은 날짜를 쓰면 같은 장이 두 봉으로 갈라질 수 있음).
    코인은 UTC 날짜.
    """
    market = market_hours.market_of(symbol)
    if market in market_hours.MARKETS:
        tz = ZoneInfo(market_hours.MARKETS[market].tz)
    elif market in market_hours.WEEKLY:
        tz = ZoneInfo("America/New_York")
    else:
        tz = timezone.utc
    return ts.astimezone(tz).date()


def is_recent(q: Quote | None, now: datetime) -> bool:
    return q is not None and now - q.at < MAX_AGE


def apply(df: pd.DataFrame, q: Quote) -> pd.DataFrame:
    """일봉에 실시간 시세를 반영한 사본. 오늘 줄이 있으면 종가·고가·저가를 고치고, 없으면 새로 붙인다."""
    out = df.copy()
    day = pd.Timestamp(q.day)
    last = out.index[-1]
    if last >= day:
        row = out.loc[last]
        if q.source == "네이버" and q.open is not None and last == day:  # 네이버는 오늘 시가·고가·저가가 정확함
            out.loc[last, ["Open", "High", "Low"]] = [q.open, q.high or q.price, q.low or q.price]
        else:
            out.loc[last, "High"] = max(float(row["High"]), q.price)
            out.loc[last, "Low"] = min(float(row["Low"]), q.price)
        out.loc[last, "Close"] = q.price
    else:
        new = pd.DataFrame({"Open": [q.open or q.price], "High": [max(q.high or q.price, q.price)],
                            "Low": [min(q.low or q.price, q.price)], "Close": [q.price]}, index=[day])
        out = pd.concat([out, new[out.columns.intersection(new.columns)]])
    out.attrs["asof"] = q.at
    out.attrs["source"] = q.source
    return out


# --- 네이버 (한국 지수·종목) ---

def naver_target(symbol: str) -> tuple[str, str] | None:
    s = symbol.upper()
    if s in NAVER_INDEX:
        return "index", NAVER_INDEX[s]
    if s.endswith((".KS", ".KQ")) and s[:6].isalnum():
        return "stock", s[:6]
    return None


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_naver(body, symbol: str, now: datetime) -> Quote | None:
    """네이버 실시간 응답에서 현재가·시가·고가·저가·시각을 꺼낸다. 필드가 조금 달라도 찾도록 느슨하게."""
    for d in _walk(body):
        price = _num(d.get("closePrice"))
        if not price:
            continue
        at = now
        raw = d.get("localTradedAt") or d.get("tradedAt")
        if raw:
            try:
                ts = pd.Timestamp(raw)
                ts = ts.tz_localize(KST) if ts.tzinfo is None else ts
                at = ts.to_pydatetime().astimezone(timezone.utc)
            except (ValueError, TypeError):
                pass
        return Quote(price=price, at=at, day=day_of(symbol, at), open=_num(d.get("openPrice")),
                     high=_num(d.get("highPrice")), low=_num(d.get("lowPrice")), source="네이버")
    return None


def fetch_naver(symbol: str, now: datetime | None = None) -> Quote:
    """네이버 실시간 시세 하나. 실패하면 예외."""
    now = now or datetime.now(timezone.utc)
    target = naver_target(symbol)
    if not target:
        raise ValueError(f"{symbol}: 네이버 대상 아님")
    kind, code = target
    last_err: Exception | None = None
    for url in NAVER_URLS:
        try:
            r = requests.get(url.format(kind=kind, code=code), headers=NAVER_HEADERS, timeout=4)
            r.raise_for_status()
            q = parse_naver(r.json(), symbol, now)
            if q:
                return q
            last_err = ValueError("응답에 현재가 없음")
        except Exception as e:  # 접속 실패, 해외 차단, 형식 변경
            last_err = e
    raise last_err or ValueError("네이버 실패")


# --- 야후 1분봉 (나머지) ---

def quote_from_bars(symbol: str, bars: pd.DataFrame) -> Quote | None:
    """1분봉에서 마지막 가격과, 그 거래일 안의 시가·고가·저가."""
    if bars is None or bars.empty or "Close" not in bars:
        return None
    bars = bars.dropna(subset=["Close"])
    if bars.empty:
        return None
    idx = pd.to_datetime(bars.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    bars = bars.set_axis(idx.tz_convert("UTC"))
    at = bars.index[-1].to_pydatetime()
    day = day_of(symbol, at)
    same_day = bars[[day_of(symbol, t.to_pydatetime()) == day for t in bars.index]]
    col = lambda c: same_day[c] if c in same_day else same_day["Close"]  # noqa: E731
    return Quote(price=float(bars["Close"].iloc[-1]), at=at, day=day,
                 open=float(col("Open").dropna().iloc[0]) if col("Open").notna().any() else None,
                 high=float(col("High").max()), low=float(col("Low").min()), source="야후")


def fetch_yahoo(symbols: tuple[str, ...]) -> dict[str, Quote]:
    """여러 티커의 오늘 1분봉을 한 번에 받아 실시간 시세로."""
    if not symbols:
        return {}
    raw = yf.download(list(symbols), period="1d", interval="1m", auto_adjust=False, progress=False,
                      group_by="ticker", threads=True, ignore_tz=False, timeout=10)
    out: dict[str, Quote] = {}
    if raw is None or raw.empty:
        return out
    for sym in symbols:
        try:
            bars = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        q = quote_from_bars(sym, bars)
        if q:
            out[sym] = q
    return out
