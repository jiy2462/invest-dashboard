"""비트코인 트레이딩 (변형 돈키안 40/20) 계산과 화면."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from lib import btc_trading as bt
from tests import fakes
from tests.test_pages import run


def frame(closes, start="2026-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c}, index=idx)


def test_channels_use_only_previous_days():
    df = frame([10, 11, 12, 13, 14, 15])
    sig = bt.compute_signals(df, buy_n=3, sell_n=2)
    # 4번째 날(13)의 매수선 = 직전 3일(10,11,12) 최고 = 12 → 오늘 값은 들어가지 않는다
    assert sig["Upper"].iloc[3] == 12
    assert sig["Lower"].iloc[3] == 11
    assert bool(sig["Buy"].iloc[3])


def test_repeated_signals_make_one_trade():
    # 오르는 동안 매수 조건이 매일 나오지만 매매는 처음 한 번, 떨어질 때 매도 한 번
    closes = [10, 10, 10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9]
    sig = bt.compute_signals(frame(closes), buy_n=3, sell_n=2)
    assert sig["Buy"].sum() > 1 and sig["Sell"].sum() > 1
    assert sig["Entry"].sum() == 1 and sig["Exit"].sum() == 1
    entry, exit_ = sig.index[sig["Entry"]][0], sig.index[sig["Exit"]][0]
    assert entry < exit_
    assert (sig.loc[entry:exit_ - pd.Timedelta(days=1), "Position"] == 1).all()
    assert sig.loc[exit_, "Position"] == 0


def test_backtest_buys_and_sells_at_signal_close_with_fees():
    closes = [10, 10, 10, 12, 15, 9, 9]
    sig = bt.compute_signals(frame(closes), buy_n=3, sell_n=2)
    assert list(sig.index[sig["Entry"]].day) == [4] and list(sig.index[sig["Exit"]].day) == [6]
    res = bt.backtest(sig, fee_pct=0.0)
    # 12에 사서 9에 팔기 → -25%
    assert res.equity["전략"].iloc[-1] == pytest.approx(9 / 12)
    t = res.trades.iloc[0]
    assert t["매수가"] == 12 and t["매도가"] == 9 and t["상태"] == "완료"
    assert t["수익률"] == pytest.approx(-25)
    fee = bt.backtest(sig, fee_pct=0.1)
    assert fee.equity["전략"].iloc[-1] == pytest.approx(9 / 12 * 0.999 ** 2)
    assert res.stats["그냥 보유"]["total"] == pytest.approx(9 / 10 - 1)
    assert res.stats["전략"]["mdd"] == pytest.approx(-0.4)   # 15 → 9


def test_backtest_start_ignores_earlier_position():
    closes = [10, 10, 10, 12, 15, 16, 17, 18]
    sig = bt.compute_signals(frame(closes), buy_n=3, sell_n=2)
    # 이미 보유 중인 구간에서 시작하면, 새 매수 신호가 나는 날 산다
    res = bt.backtest(sig, start="2026-01-07", fee_pct=0.0)
    assert res.trades.iloc[0]["매수일"] == pd.Timestamp("2026-01-07").date()
    assert res.trades.iloc[0]["상태"] == "보유 중"


def test_status_pending_uses_in_progress_price():
    closes = [10, 10, 10, 10, 10, 13]  # 마지막 봉은 진행 중
    sig = bt.compute_signals(frame(closes), buy_n=3, sell_n=2)
    s = bt.status(sig, partial=True, buy_n=3, sell_n=2)
    assert not s.holding and s.pending == "매수"
    assert s.buy_line == 10 and s.price == 13
    s2 = bt.status(sig, partial=False, buy_n=3, sell_n=2)
    assert s2.holding and s2.entry_price == 13 and s2.pending is None


def test_ny_sessions_close_at_16_et_and_mark_partial():
    # 2026-11-09(월) 겨울시간: 16:00 ET = 21:00 UTC. 봉 시작 20:00 UTC 봉이 16시에 끝난다.
    idx = pd.date_range("2026-11-08 21:00", "2026-11-10 17:00", freq="h", tz="UTC")
    bars = pd.DataFrame({"Open": range(len(idx)), "High": range(len(idx)), "Low": range(len(idx)),
                         "Close": [float(i) for i in range(len(idx))]}, index=idx)
    sess = bt.ny_sessions(bars)
    day = sess.loc[pd.Timestamp("2026-11-09")]
    assert day["Close"] == bars.loc[pd.Timestamp("2026-11-09 20:00", tz="UTC"), "Close"]
    assert bool(day["complete"])
    assert not bool(sess["complete"].iloc[-1])  # 11/10 은 16시 전에 끝남
    # 여름시간: 16:00 EDT = 20:00 UTC
    idx2 = pd.date_range("2026-07-01 00:00", "2026-07-01 23:00", freq="h", tz="UTC")
    bars2 = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": np.arange(len(idx2), dtype=float)}, index=idx2)
    s2 = bt.ny_sessions(bars2)
    assert s2.loc[pd.Timestamp("2026-07-01"), "Close"] == 19.0  # 19:00 UTC 시작 봉이 20:00 UTC(=16 EDT)에 끝남


def test_next_close_in_kst():
    winter = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    assert bt.next_close("utc", winter).strftime("%m/%d %H:%M") == "11/12 09:00"
    assert bt.next_close("ny", winter).strftime("%m/%d %H:%M") == "11/12 06:00"
    summer = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    assert bt.next_close("ny", summer).strftime("%m/%d %H:%M") == "07/02 05:00"


def test_settings_are_cleaned():
    cfg = bt.clean({"buy_n": 0, "sell_n": "25", "symbol": "DOGE", "basis": "x", "fee_pct": -1})
    assert cfg == {"symbol": "BTC-USD", "buy_n": 2, "sell_n": 25, "basis": "utc", "fee_pct": 0.0}


def test_gap_days():
    df = frame(range(10)).drop(pd.Timestamp("2026-01-05"))
    assert bt.gap_days(df) == 1


def test_page_renders_and_saves_settings():
    at = run("btc_trading")
    assert not at.exception, at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "매수선" in md and "매도선" in md
    assert any("이 전략으로 했다면" == s.value for s in at.subheader)
    assert len(at.get("plotly_chart")) == 2
    # 조합 비교
    [b for b in at.button if b.key == "btc_grid"][0].click().run()
    assert not at.exception

    # 설정 저장 → 미국 장마감 기준 · 55/20
    at.selectbox[1].set_value("ny")
    at.number_input[0].set_value(55)
    at.button(key="FormSubmitter:btc_cfg-저장").click().run()
    assert not at.exception, at.exception
    saved = fakes.TABLES["app_settings"][0]
    assert saved["key"] == "btc_trading" and saved["value"]["buy_n"] == 55 and saved["value"]["basis"] == "ny"
    assert any("뉴욕 16시" in c.value for c in at.caption)
