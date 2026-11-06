"""거래시간 판별과 '닫힌 장은 다시 받지 않기'."""
from datetime import datetime, timedelta, timezone

from lib import market, market_hours as mh
from tests import fakes

UTC = timezone.utc
# 2026-11-06(금) 기준. 한국 15:30 = 06:30 UTC, 미국 16:00 EST = 21:00 UTC
KR_OPEN = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)     # 서울 11:00, 뉴욕 목 21:00
US_OPEN = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)    # 뉴욕 10:00, 서울 24:00
SATURDAY = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def test_market_of():
    assert mh.market_of("^KS11") == "kr" and mh.market_of("005930.KS") == "kr"
    assert mh.market_of("^GSPC") == "us" and mh.market_of("^TNX") == "us"
    assert mh.market_of("KRW=X") == "fx" and mh.market_of("CL=F") == "futures"
    assert mh.market_of("BTC-USD") == "crypto" and mh.market_of("^N225") == "jp"


def test_open_and_closed():
    assert mh.is_open("kr", KR_OPEN) and not mh.is_open("us", KR_OPEN)
    assert mh.is_open("us", US_OPEN) and not mh.is_open("kr", US_OPEN)
    assert not mh.is_open("kr", SATURDAY) and not mh.is_open("fx", SATURDAY)
    assert mh.is_open("crypto", SATURDAY) and mh.is_open("fx", US_OPEN)


def test_last_close_and_grace():
    # 금요일 미국 장중 → 미국 마지막 마감은 목요일 16:00 뉴욕
    assert mh.last_close("us", US_OPEN) == datetime(2026, 11, 5, 21, 0, tzinfo=UTC)
    # 한국 마감 10분 뒤는 아직 종가 확정 전이라 '살아 있음'
    assert mh.is_live("^KS11", datetime(2026, 11, 6, 6, 40, tzinfo=UTC))
    assert not mh.is_live("^KS11", datetime(2026, 11, 6, 7, 0, tzinfo=UTC))
    assert mh.last_close("fx", SATURDAY) == datetime(2026, 11, 6, 22, 0, tzinfo=UTC)


def _count_fetches(monkeypatch):
    calls = []

    def fake(syms):
        calls.append(syms)
        return {s: fakes.fake_ohlcv(s, "1d") for s in syms if s in fakes.BASE}

    monkeypatch.setattr(market, "fetch_yf_ohlc", fake)
    return calls


SETTINGS = [{"symbol": s, "source": "yfinance"} for s in ("^GSPC", "^KS11", "KRW=X")]


def test_only_open_markets_are_refetched(monkeypatch):
    calls = _count_fetches(monkeypatch)
    out, errors = market.load_ohlc(SETTINGS, now=KR_OPEN)
    assert set(out) == {"^GSPC", "^KS11", "KRW=X"} and not errors
    assert calls == [("KRW=X", "^GSPC", "^KS11")]

    # 4분 뒤: 전부 저장된 값
    market.load_ohlc(SETTINGS, now=KR_OPEN + timedelta(minutes=4))
    assert len(calls) == 1
    # 6분 뒤: 열린 한국·환율만 다시, 닫힌 미국은 그대로
    market.load_ohlc(SETTINGS, now=KR_OPEN + timedelta(minutes=6))
    assert calls[-1] == ("KRW=X", "^KS11")


def test_weekend_fetches_once(monkeypatch):
    calls = _count_fetches(monkeypatch)
    market.load_ohlc(SETTINGS, now=SATURDAY)
    market.load_ohlc(SETTINGS, now=SATURDAY + timedelta(hours=10))
    assert len(calls) == 1


def test_failure_keeps_old_value_and_retries_later(monkeypatch):
    calls = _count_fetches(monkeypatch)
    market.load_ohlc(SETTINGS, now=KR_OPEN)
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: calls.append(syms) or {})
    out, errors = market.load_ohlc(SETTINGS, now=KR_OPEN + timedelta(minutes=6))
    assert "^KS11" in out and not errors  # 예전 값 유지
    market.load_ohlc(SETTINGS, now=KR_OPEN + timedelta(minutes=8))
    assert len(calls) == 2  # 5분 안에는 다시 시도하지 않음
