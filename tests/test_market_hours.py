"""거래시간 판별과 '닫힌 장은 다시 받지 않기'."""
from datetime import datetime, timedelta, timezone

import pandas as pd

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


# --- 장중 실시간 시세로 오늘 봉 만들기 ---
from lib import live_quote as lq  # noqa: E402

MON_0910 = datetime(2026, 11, 9, 0, 10, tzinfo=UTC)  # 서울 월 09:10, 뉴욕 일 19:10


def _friday_df(sym):
    """금요일(11/6)까지 일봉. 월요일 장 시작 직후 야후 일봉처럼 오늘 줄이 없다."""
    idx = pd.bdate_range(end="2026-11-06", periods=30)
    close = [100.0 + i for i in range(len(idx))]
    return pd.DataFrame({"Open": close, "High": [c + 1 for c in close], "Low": [c - 1 for c in close],
                         "Close": close}, index=idx)


def test_day_of_uses_market_dates():
    assert lq.day_of("^KS11", MON_0910).isoformat() == "2026-11-09"
    assert lq.day_of("GC=F", MON_0910).isoformat() == "2026-11-08"  # 뉴욕 날짜 (이른 쪽)
    assert lq.day_of("BTC-USD", MON_0910).isoformat() == "2026-11-09"


def test_apply_appends_today_when_daily_has_no_row():
    df = _friday_df("^KS11")
    q = lq.Quote(price=2700.0, at=MON_0910, day=lq.day_of("^KS11", MON_0910), open=2650.0, high=2710.0,
                 low=2640.0, source="네이버")
    out = lq.apply(df, q)
    assert len(out) == len(df) + 1 and out.index[-1] == pd.Timestamp("2026-11-09")
    assert out.iloc[-1].tolist() == [2650.0, 2710.0, 2640.0, 2700.0]
    assert out.attrs["asof"] == MON_0910 and len(df) == len(_friday_df("^KS11"))  # 원본은 그대로


def test_apply_updates_existing_row_and_never_splits_a_session():
    df = _friday_df("GC=F")
    df.loc[pd.Timestamp("2026-11-09")] = [100.0, 101.0, 99.0, 100.5]  # 야후가 월요일 날짜로 붙인 경우
    q = lq.Quote(price=105.0, at=MON_0910, day=lq.day_of("GC=F", MON_0910))  # 뉴욕 날짜는 일요일
    out = lq.apply(df, q)
    assert len(out) == len(df) and out.iloc[-1]["Close"] == 105.0 and out.iloc[-1]["High"] == 105.0


def test_quote_from_bars_uses_last_bar_and_same_day_range():
    idx = pd.date_range("2026-11-09 00:00", periods=10, freq="1min", tz="UTC")
    bars = pd.DataFrame({"Open": range(10), "High": [x + 1 for x in range(10)],
                         "Low": [x - 1 for x in range(10)], "Close": [x + 0.5 for x in range(10)]}, index=idx)
    q = lq.quote_from_bars("^KS11", bars)
    assert q.price == 9.5 and q.open == 0 and q.high == 10 and q.low == -1
    assert q.day.isoformat() == "2026-11-09"


def test_parse_naver_realtime_shape():
    body = {"datas": [{"itemCode": "KOSPI", "closePrice": "2,701.50", "openPrice": "2,650.00",
                       "highPrice": "2,710.10", "lowPrice": "2,640.00",
                       "localTradedAt": "2026-11-09T09:10:30+09:00", "marketStatus": "OPEN"}]}
    q = lq.parse_naver(body, "^KS11", MON_0910)
    assert q.price == 2701.5 and q.open == 2650.0 and q.source == "네이버"
    assert q.day.isoformat() == "2026-11-09" and q.at == datetime(2026, 11, 9, 0, 10, 30, tzinfo=UTC)


def test_live_quote_patches_open_markets_only(monkeypatch):
    settings = [{"symbol": s, "source": "yfinance"} for s in ("^GSPC", "^KS11", "GC=F")]
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {s: _friday_df(s) for s in syms})
    calls = []

    def yahoo(syms):
        calls.append(syms)
        return {s: lq.Quote(price=123.0, at=MON_0910 - timedelta(minutes=1), day=lq.day_of(s, MON_0910))
                for s in syms}

    monkeypatch.setattr(lq, "fetch_yahoo", yahoo)
    out, _ = market.load_ohlc(settings, now=MON_0910)
    assert calls == [("GC=F", "^KS11")]  # 미국 주식은 장 마감이라 안 받음
    assert out["^KS11"].index[-1] == pd.Timestamp("2026-11-09") and out["^KS11"]["Close"].iloc[-1] == 123.0
    assert "asof" not in out["^GSPC"].attrs
    # 1분 안에는 다시 받지 않고, 1분 지나면 다시
    market.load_ohlc(settings, now=MON_0910 + timedelta(seconds=30))
    assert len(calls) == 1
    market.load_ohlc(settings, now=MON_0910 + timedelta(seconds=61))
    assert len(calls) == 2


def test_naver_preferred_for_korea_and_backs_off_when_blocked(monkeypatch):
    settings = [{"symbol": "^KS11", "source": "yfinance"}]
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {s: _friday_df(s) for s in syms})
    old = lq.Quote(price=1.0, at=datetime(2026, 11, 6, 6, 29, tzinfo=UTC), day=lq.day_of("^KS11", MON_0910))
    monkeypatch.setattr(lq, "fetch_yahoo", lambda syms: {s: old for s in syms})  # 야후는 지난 장 시세 (지연)
    naver = []

    def fake_naver(sym, now=None):
        naver.append(sym)
        return lq.Quote(price=2701.5, at=MON_0910, day=lq.day_of(sym, MON_0910), open=2650.0,
                        high=2710.0, low=2640.0, source="네이버")

    monkeypatch.setattr(lq, "fetch_naver", fake_naver)
    out, _ = market.load_ohlc(settings, now=MON_0910)
    assert out["^KS11"]["Close"].iloc[-1] == 2701.5 and out["^KS11"].attrs["source"] == "네이버"

    # 네이버가 막히면 야후만 쓰고, 30분 동안은 네이버를 다시 기다리지 않는다
    def blocked(sym, now=None):
        naver.append(sym)
        raise ConnectionError("403")

    monkeypatch.setattr(lq, "fetch_naver", blocked)
    out, _ = market.load_ohlc(settings, now=MON_0910 + timedelta(minutes=2))
    assert out["^KS11"]["Close"].iloc[-1] == 2701.5  # 직전 실시간 값 유지 (아직 12시간 안)
    market.load_ohlc(settings, now=MON_0910 + timedelta(minutes=4))
    assert naver == ["^KS11", "^KS11"]


def test_refresh_clears_live_quotes(monkeypatch):
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {s: _friday_df(s) for s in syms})
    calls = []
    monkeypatch.setattr(lq, "fetch_yahoo", lambda syms: calls.append(syms) or {})
    settings = [{"symbol": "KRW=X", "source": "yfinance"}]
    market.load_ohlc(settings, now=MON_0910)
    market.clear_cache()
    market.load_ohlc(settings, now=MON_0910 + timedelta(seconds=5))
    assert len(calls) == 2
