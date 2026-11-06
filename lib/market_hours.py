"""지표별 거래시간: 지금 장이 열려 있는지, 마지막으로 닫힌 때가 언제인지.

휴장일(공휴일)은 따로 챙기지 않는다. 공휴일에는 열린 장처럼 15분마다 다시 받을 뿐이라 해가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

GRACE = timedelta(minutes=20)  # 마감 직후 종가가 확정될 때까지는 열린 장처럼 본다


@dataclass(frozen=True)
class Market:
    label: str
    tz: str
    open: time
    close: time


MARKETS = {
    "kr": Market("한국", "Asia/Seoul", time(9, 0), time(15, 30)),
    "us": Market("미국", "America/New_York", time(9, 30), time(16, 0)),
    "jp": Market("일본", "Asia/Tokyo", time(9, 0), time(15, 30)),
    "hk": Market("홍콩", "Asia/Hong_Kong", time(9, 30), time(16, 0)),
    "cn": Market("중국", "Asia/Shanghai", time(9, 30), time(15, 0)),
    "uk": Market("영국", "Europe/London", time(8, 0), time(16, 30)),
    "eu": Market("유럽", "Europe/Berlin", time(9, 0), time(17, 30)),
}
# 환율·선물은 뉴욕 시각 일요일 17시 ~ 금요일 17시 (주중엔 쉬는 시간이 거의 없음), 코인은 항상 열림
WEEKLY = {"fx", "futures"}
ALWAYS = {"crypto"}

_INDEX = {
    "^KS11": "kr", "^KQ11": "kr", "^KS200": "kr",
    "^N225": "jp", "^HSI": "hk", "000001.SS": "cn", "^FTSE": "uk",
    "^GDAXI": "eu", "^FCHI": "eu", "^STOXX50E": "eu",
    "DX-Y.NYB": "fx",
}
_SUFFIX = {
    ".KS": "kr", ".KQ": "kr", ".T": "jp", ".HK": "hk", ".SS": "cn", ".SZ": "cn",
    ".L": "uk", ".DE": "eu", ".PA": "eu", ".AS": "eu",
}


def market_of(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("ECOS:"):
        return "kr"
    if s in _INDEX:
        return _INDEX[s]
    if s.endswith("=X"):
        return "fx"
    if s.endswith("=F"):
        return "futures"
    if any(s.endswith(q) for q in ("-USD", "-KRW", "-USDT", "-EUR")):
        return "crypto"
    for suf, m in _SUFFIX.items():
        if s.endswith(suf):
            return m
    return "us"  # ^GSPC, ^VIX, ^TNX, 미국 주식·ETF


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def last_close(market: str, now: datetime | None = None) -> datetime | None:
    """지금 이전에 마지막으로 장이 닫힌 시각. 지금 열려 있으면 그 전 마감. 코인은 None."""
    now = _now(now)
    if market in ALWAYS:
        return None
    if market in WEEKLY:
        ny = now.astimezone(ZoneInfo("America/New_York"))
        fri = (ny - timedelta(days=(ny.weekday() - 4) % 7)).replace(hour=17, minute=0, second=0, microsecond=0)
        if fri > ny:
            fri -= timedelta(days=7)
        return fri
    m = MARKETS[market]
    local = now.astimezone(ZoneInfo(m.tz))
    for back in range(8):
        d = (local - timedelta(days=back)).date()
        if d.weekday() >= 5:
            continue
        close = datetime.combine(d, m.close, ZoneInfo(m.tz))
        if close <= local:
            return close
    return None


def is_open(market: str, now: datetime | None = None) -> bool:
    now = _now(now)
    if market in ALWAYS:
        return True
    if market in WEEKLY:
        ny = now.astimezone(ZoneInfo("America/New_York"))
        wd, t = ny.weekday(), ny.time()
        if wd == 5:
            return False
        if wd == 6:
            return t >= time(17, 0)
        if wd == 4:
            return t < time(17, 0)
        return True
    m = MARKETS[market]
    local = now.astimezone(ZoneInfo(m.tz))
    return local.weekday() < 5 and m.open <= local.time() < m.close


def is_live(symbol: str, now: datetime | None = None) -> bool:
    """시세가 바뀔 수 있는 상태인가: 장중이거나, 마감 직후 종가 확정 전."""
    now = _now(now)
    market = market_of(symbol)
    if is_open(market, now):
        return True
    closed = last_close(market, now)
    return closed is not None and now - closed < GRACE


def status(symbol: str, now: datetime | None = None) -> str:
    return "장중" if is_open(market_of(symbol), now) else "장 마감"
