"""야후 일봉에 빠진 날 채우기 (2026-11-26: 코스피·닛케이·항셍·비트코인 11/25 줄이 없었음)."""
from datetime import date, datetime, timezone

import pandas as pd

from lib import gap_fill, live_quote as lq, market, market_hours as mh

UTC = timezone.utc
THU_1040_KST = datetime(2026, 11, 26, 1, 40, tzinfo=UTC)   # 목 10:40 서울, 수 20:40 뉴욕
THU_1700_KST = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)    # 한국 장 마감 뒤


def frame(days, closes=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    closes = closes or [100.0 + i for i in range(len(days))]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=idx)


YAHOO_KS11 = frame(["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-26"],
                   [4800.0, 4869.96, 4708.54, 5006.03, 4789.12])


def test_missing_days_finds_skipped_weekday_but_not_today_while_open():
    assert gap_fill.missing_days("^KS11", YAHOO_KS11, THU_1040_KST) == [date(2026, 11, 25)]
    # 오늘 줄이 없어도 장중이면 실시간 시세가 만든다 → 빠진 날 아님
    no_today = YAHOO_KS11.iloc[:-1]
    assert gap_fill.missing_days("^KS11", no_today, THU_1040_KST) == [date(2026, 11, 25)]
    # 장이 끝났는데 오늘 줄이 없으면 오늘도 채운다
    assert gap_fill.missing_days("^KS11", no_today, THU_1700_KST) == [date(2026, 11, 25), date(2026, 11, 26)]


def test_missing_days_skips_fx_futures_and_counts_weekends_for_crypto():
    assert gap_fill.missing_days("KRW=X", YAHOO_KS11, THU_1040_KST) == []
    assert gap_fill.missing_days("GC=F", YAHOO_KS11, THU_1040_KST) == []
    btc = frame(["2026-11-20", "2026-11-21", "2026-11-23", "2026-11-24", "2026-11-26"])
    assert gap_fill.missing_days("BTC-USD", btc, THU_1040_KST) == [date(2026, 11, 22), date(2026, 11, 25)]


def test_parse_naver_daily_kr_and_world_shapes():
    kr = gap_fill.parse_naver_daily([
        {"localTradedAt": "2026-11-25", "closePrice": "4,697.51", "openPrice": "4,809.00",
         "highPrice": "4,823.36", "lowPrice": "4,672.55"},
        {"localTradedAt": "2026-11-24", "closePrice": "5,006.03", "openPrice": "-", "highPrice": "-", "lowPrice": "-"},
    ])
    assert kr.loc["2026-11-25"].tolist() == [4809.0, 4823.36, 4672.55, 4697.51]
    assert kr.loc["2026-11-24"].tolist() == [5006.03] * 4
    world = gap_fill.parse_naver_daily([{"localTradedAt": "2026-11-25T16:08:28+08:00", "closePrice": "21,966.01",
                                         "openPrice": "22,252.81", "highPrice": "22,507.83", "lowPrice": "21,915.88"}])
    assert list(world.index) == [pd.Timestamp("2026-11-25")]


def test_fill_inserts_naver_row_for_korean_index(monkeypatch):
    naver = frame(["2026-11-24", "2026-11-25", "2026-11-26"], [5006.03, 4697.51, 4751.70])
    monkeypatch.setattr(gap_fill, "naver_daily", lambda sym: naver)
    monkeypatch.setattr(gap_fill, "hourly_daily", lambda syms: (_ for _ in ()).throw(AssertionError("안 불러야 함")))
    out = gap_fill.fill({"^KS11": YAHOO_KS11}, now=THU_1040_KST)
    df = out["^KS11"]
    assert list(df.index[-3:]) == [pd.Timestamp(d) for d in ("2026-11-24", "2026-11-25", "2026-11-26")]
    assert df.loc["2026-11-25", "Close"] == 4697.51
    assert df.loc["2026-11-26", "Close"] == 4789.12  # 야후에 있던 줄은 그대로
    s = market.summarize(df["Close"], False)
    assert round(s["pct"], 2) == round((4789.12 / 4697.51 - 1) * 100, 2) and s["prev_asof"] == pd.Timestamp("2026-11-25")


def test_fill_uses_hourly_bars_for_others_and_holidays_stay_empty(monkeypatch):
    btc = frame(["2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-26"])
    spy = frame(["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"])  # 11/25 빠짐
    calls = []

    def hourly(syms):
        calls.append(syms)
        return {"BTC-USD": frame(["2026-11-25"], [60383.5]), "SPY": frame(["2026-11-24"], [1.0])}

    monkeypatch.setattr(gap_fill, "hourly_daily", hourly)
    out = gap_fill.fill({"BTC-USD": btc, "SPY": spy}, now=THU_1040_KST)
    assert calls == [("BTC-USD", "SPY")]
    assert out["BTC-USD"].loc["2026-11-25", "Close"] == 60383.5
    assert len(out["SPY"]) == len(spy)  # 1시간봉에도 그날이 없으면 그대로 (휴장일)


def test_fill_keeps_data_when_sources_fail(monkeypatch):
    monkeypatch.setattr(gap_fill, "naver_daily", lambda sym: (_ for _ in ()).throw(ConnectionError("403")))
    monkeypatch.setattr(gap_fill, "hourly_daily", lambda syms: (_ for _ in ()).throw(ConnectionError("403")))
    out = gap_fill.fill({"^KS11": YAHOO_KS11}, now=THU_1040_KST)
    assert out["^KS11"].equals(YAHOO_KS11)


def test_daily_from_hourly_groups_by_exchange_date():
    idx = pd.DatetimeIndex(["2026-11-25 00:00", "2026-11-25 05:00", "2026-11-26 00:00"], tz="UTC")  # 서울 09·14시, 다음날 09시
    bars = pd.DataFrame({"Open": [1.0, 2.0, 5.0], "High": [3.0, 4.0, 6.0], "Low": [0.5, 1.5, 4.0],
                         "Close": [2.0, 3.5, 5.5]}, index=idx)
    d = gap_fill.daily_from_hourly("^KS11", bars)
    assert d.loc["2026-11-25"].tolist() == [1.0, 4.0, 0.5, 3.5]
    assert d.loc["2026-11-26", "Close"] == 5.5


def test_naver_world_preopen_is_ignored_and_open_is_used():
    pre = {"datas": [{"closePrice": "21,966.01", "openPrice": "-", "marketStatus": "PREOPEN",
                      "localTradedAt": "2026-11-26T09:29:56+08:00"}]}
    assert lq.parse_naver(pre, "^HSI", THU_1040_KST) is None
    live = {"datas": [{"closePrice": "58,125.61", "openPrice": "57,429.54", "highPrice": "58,251.93",
                       "lowPrice": "57,429.54", "marketStatus": "OPEN", "localTradedAt": "2026-11-26T10:29:55+09:00"}]}
    q = lq.parse_naver(live, "^N225", THU_1040_KST)
    assert q.price == 58125.61 and q.day == date(2026, 11, 26) and q.source == "네이버"
    assert lq.naver_target("^N225") == ("world", ".N225") and lq.naver_target("^HSI") == ("world", ".HSI")


def test_newer_of_naver_and_yahoo_wins_for_world_index(monkeypatch):
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {s: frame(["2026-11-24", "2026-11-25"]) for s in syms})
    older = lq.Quote(price=1.0, at=datetime(2026, 11, 26, 1, 20, tzinfo=UTC), day=date(2026, 11, 26))
    newer = lq.Quote(price=2.0, at=datetime(2026, 11, 26, 1, 29, tzinfo=UTC), day=date(2026, 11, 26),
                     open=1.5, high=2.1, low=1.4, source="네이버")
    monkeypatch.setattr(lq, "fetch_yahoo", lambda syms: {s: older for s in syms})
    monkeypatch.setattr(lq, "fetch_naver", lambda sym, now=None: newer)
    out, _ = market.load_ohlc([{"symbol": "^N225", "source": "yfinance"}], now=THU_1040_KST)
    assert out["^N225"]["Close"].iloc[-1] == 2.0 and out["^N225"].attrs["source"] == "네이버"


def test_caption_helpers():
    assert mh.day_label(pd.Timestamp("2026-11-25")) == "11/25(수)"
    assert mh.close_in_kst("^VIX", pd.Timestamp("2026-11-25")) == "11/26 06:00"  # 미국 16:00 = 한국 다음날 06:00
    assert mh.close_in_kst("^KS11", pd.Timestamp("2026-11-25")) is None
    assert mh.close_in_kst("KRW=X", pd.Timestamp("2026-11-25")) is None
