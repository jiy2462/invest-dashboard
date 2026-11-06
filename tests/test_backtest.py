"""백테스트 계산과 화면 테스트 (네트워크 불필요)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from lib import backtest as bt
from tests import fakes
from tests.test_pages import run


def series(start, values, freq="B"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


# ── 이어 붙이기 ─────────────────────────────────────────────

def test_splice_fills_older_period_with_proxy_returns():
    etf = series("2020-01-06", [100, 110, 121])
    fund = series("2019-12-30", [50, 55, 60, 65, 70, 77])   # 2020-01-06에 77, 그 전은 50→77
    s, segs = bt.splice(("ETF", "FUND"), {"ETF": etf, "FUND": fund})
    assert s.loc["2020-01-06"] == 100 and s.iloc[-1] == 121      # ETF 구간은 그대로
    assert s.iloc[0] == pytest.approx(100 * 50 / 77)             # 대체 구간은 수익률만 가져옴
    assert [g["ticker"] for g in segs] == ["FUND", "ETF"]
    assert segs[0]["end"] == pd.Timestamp("2020-01-06")


def test_splice_skips_missing_proxy():
    etf = series("2020-01-06", [100, 101])
    s, segs = bt.splice(("ETF", "NOPE"), {"ETF": etf})
    assert len(s) == 2 and len(segs) == 1


def test_rate_becomes_interest_index():
    y = pd.Series([5.0] * 366, index=pd.date_range("2020-01-01", periods=366, freq="D"))
    idx = bt.rate_to_index(y)
    assert idx.iloc[-1] == pytest.approx(1.05 ** (365 / 365), rel=1e-3)


# ── 시뮬레이션 ─────────────────────────────────────────────

def test_rebalance_vs_buy_and_hold():
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    up = pd.Series(np.linspace(100, 300, len(idx)), index=idx)
    flat = pd.Series(100.0, index=idx)
    prices = pd.DataFrame({"A": up, "B": flat})
    hold = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "none")
    yearly = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "yearly")
    assert hold["value"].iloc[-1] == pytest.approx(500 * 3 + 500)
    assert yearly["value"].iloc[-1] < hold["value"].iloc[-1]       # 오르는 자산을 덜어내므로
    assert hold["nav"].iloc[0] == 1.0


def test_monthly_contribution_does_not_count_as_return():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    prices = pd.DataFrame({"A": pd.Series(100.0, index=idx)})
    sim = bt.simulate(prices, {"A": 100}, 1000, 100, "none")
    assert sim["invested"].iloc[-1] == 1000 + 100 * 11           # 2~12월 첫 거래일
    assert sim["value"].iloc[-1] == pytest.approx(2100)
    assert sim["nav"].iloc[-1] == pytest.approx(1.0)             # 가격이 그대로면 수익률 0
    m = bt.metrics(sim)
    assert m["cagr"] == pytest.approx(0, abs=1e-9)
    assert m["irr"] == pytest.approx(0, abs=1e-6)


def test_metrics_cagr_mdd_yearly():
    nav = pd.Series([1.0, 2.0, 1.0, 4.0], index=pd.to_datetime(["2020-01-01", "2020-12-31", "2021-06-30", "2022-01-01"]))
    sim = pd.DataFrame({"value": nav * 100, "invested": 100.0, "nav": nav})
    m = bt.metrics(sim)
    assert m["mdd"] == pytest.approx(-0.5)
    assert m["cagr"] == pytest.approx(4 ** (1 / ((nav.index[-1] - nav.index[0]).days / 365.25)) - 1)
    yr = bt.yearly_returns(nav)
    assert yr.loc[2020] == pytest.approx(1.0) and yr.loc[2021] == pytest.approx(-0.5) and yr.loc[2022] == pytest.approx(3.0)


def test_run_backtest_30y_60_40_with_spliced_sources_and_fx():
    hist = fakes.fake_history(bt.needed_tickers({"US_STOCK", "US_ITB", "GOLD", "KOSPI"}))
    ports = [bt.Portfolio("60/40", {"US_STOCK": 60, "US_ITB": 40})]
    res = bt.run_backtest(ports, date(1996, 11, 6), date(2026, 11, 5), 10_000_000, 0, "yearly", "KRW", hist)
    r = res.results[0]
    assert r.sim.index[0] == pd.Timestamp("1996-11-06")         # 30년 그대로 (환율은 FRED로 채움)
    assert r.stats["mdd"] < 0 and np.isfinite(r.stats["cagr"])
    assert set(res.sources["티커"]) == {"VFINX", "SPY", "VFITX", "IEF"}
    assert r.other.notna().all()                               # 달러 환산
    # 금이 들어가면 2000-08-30부터로 밀린다
    ports.append(bt.Portfolio("금 포함", {"US_STOCK": 60, "US_LTB": 30, "GOLD": 10}))
    res = bt.run_backtest(ports, date(1996, 11, 6), date(2026, 11, 5), 10_000_000, 0, "yearly", "USD", hist)
    assert all(x.sim.index[0] >= pd.Timestamp("2000-08-30") for x in res.results)
    assert "2000-08-30" in res.notes[0]


def test_weights_from_my_targets():
    targets = pd.DataFrame([{"asset_class": "S&P 500", "target_weight": 37},
                            {"asset_class": "금", "target_weight": 15},
                            {"asset_class": "현금", "target_weight": 27},
                            {"asset_class": "적금", "target_weight": 0},
                            {"asset_class": "기타", "target_weight": 5}])
    assets = pd.DataFrame([{"symbol": "360750.KS", "asset_class": "S&P 500"},
                           {"symbol": "MANUAL:KRX 금현물", "asset_class": "금"},
                           {"symbol": "GLDM", "asset_class": "금"},
                           {"symbol": "357870.KS", "asset_class": "현금"},
                           {"symbol": "SGOV", "asset_class": "현금"}])
    w, notes = bt.weights_from_targets(targets, assets)
    assert w == {"US_STOCK": 37, "GOLD": 15, "US_STB": 27}
    assert any("기타" in n for n in notes)


# ── 화면 ───────────────────────────────────────────────────

def test_backtest_page_runs_and_saves_preset():
    at = run("backtest")
    assert not at.exception
    [b for b in at.button if b.label == "백테스트 실행"][0].click().run()
    assert not at.exception
    assert at.session_state.bt_run.results
    assert any("결과" in s.value for s in at.subheader)
    [b for b in at.button if b.key == "bt_save0"][0].click().run()
    assert not at.exception
    assert fakes.TABLES["backtest_presets"][0]["name"] == "주식 60 / 장기채 30 / 금 10"
    assert fakes.TABLES["backtest_presets"][0]["allocations"]["GOLD"] == 10


def test_backtest_page_uses_my_targets():
    fakes.TABLES["target_allocation"] = [{"asset_class": "S&P 500", "target_weight": 60, "sort_order": 1},
                                         {"asset_class": "금", "target_weight": 40, "sort_order": 2}]
    fakes.TABLES["assets"] = [{"symbol": "SPYM", "name": "SPYM", "asset_class": "S&P 500", "currency": "USD"},
                              {"symbol": "GLDM", "name": "GLDM", "asset_class": "금", "currency": "USD"}]
    at = run("backtest")
    assert not at.exception
    assert at.session_state.bt_pick1 == "내 목표 비중 (포트폴리오 화면)"
    [b for b in at.button if b.label == "백테스트 실행"][0].click().run()
    assert not at.exception
    names = [r.name for r in at.session_state.bt_run.results]
    assert names[1] == "내 목표 비중 (포트폴리오 화면)" and names[-1].startswith("비교 기준")


def test_months_to_recover_principal():
    idx = pd.to_datetime(["2020-01-01", "2020-03-01", "2020-07-01", "2021-01-01", "2021-06-01"])
    sim = pd.DataFrame({"value": [100, 80, 95, 101, 90], "invested": 100.0,
                        "nav": [1.0, 0.8, 0.95, 1.01, 0.9]}, index=idx)
    m = bt.metrics(sim)
    lr = m["loss_recovery"]
    assert lr["start"] == pd.Timestamp("2020-03-01") and lr["end"] == pd.Timestamp("2021-01-01")
    assert round(lr["months"]) == 10 and lr["recovered"]
    pr = m["peak_recovery"]                    # 2021-06 하락은 아직 회복 중이지만 더 짧다
    assert pr["recovered"] and round(pr["months"]) == 10
    still = bt.longest_spell(pd.Series([False, True, True], index=idx[:3]))
    assert not still["recovered"] and still["end"] == idx[2]


def test_semiannual_rebalance_happens_in_january_and_july():
    idx = pd.bdate_range("2020-01-01", "2021-12-31")
    prices = pd.DataFrame({"A": pd.Series(np.linspace(100, 300, len(idx)), index=idx), "B": 100.0})
    q = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "quarterly")
    h = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "semiannual")
    y = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "yearly")
    assert q["value"].iloc[-1] < h["value"].iloc[-1] < y["value"].iloc[-1]   # 오르는 자산을 덜어내는 횟수만큼
    assert bt._period_key(pd.Timestamp("2020-06-30"), "semiannual") != bt._period_key(pd.Timestamp("2020-07-01"), "semiannual")
    assert "semiannual" in bt.REBALANCE


def test_run_roundtrip_through_json():
    hist = fakes.fake_history(bt.needed_tickers({"US_STOCK", "US_ITB"}))
    res = bt.run_backtest([bt.Portfolio("60/40", {"US_STOCK": 60, "US_ITB": 40})], date(2000, 1, 3), date(2026, 11, 5),
                          1000, 10, "semiannual", "USD", hist)
    res.settings = {"start": "2000-01-03", "end": "2026-11-05", "base": "USD"}
    import json
    blob = json.loads(json.dumps(bt.run_to_json(res)))          # DB에 들어갈 수 있는 JSON이어야 함
    back = bt.run_from_json(blob, res.settings)
    a, b = res.results[0], back.results[0]
    assert b.stats["cagr"] == pytest.approx(a.stats["cagr"]) and b.stats["mdd_date"] == a.stats["mdd_date"]
    assert b.stats["loss_recovery"] == a.stats["loss_recovery"]
    assert b.sim.index[0] == a.sim.index[0] and b.sim.index[-1] == a.sim.index[-1]
    assert b.sim["value"].iloc[-1] == pytest.approx(a.sim["value"].iloc[-1])
    assert len(b.sim) < len(a.sim) / 4                            # 주 단위로 줄여 저장
    assert list(back.sources["티커"]) == list(res.sources["티커"]) and back.settings["base"] == "USD"


def test_save_view_and_delete_backtest_run():
    at = run("backtest")
    [b for b in at.button if b.label == "백테스트 실행"][0].click().run()
    assert not at.exception
    [b for b in at.button if b.label == "이 결과 저장"][0].click().run()
    assert not at.exception
    rows = fakes.TABLES["backtest_runs"]
    assert len(rows) == 1 and rows[0]["settings"]["rebalance"] == "yearly"
    assert [p["name"] for p in rows[0]["settings"]["portfolios"]][0] == "주식 60 / 장기채 30 / 금 10"

    # 다른 설정으로 바꾼 뒤 저장한 결과를 열면 옵션과 결과가 되돌아온다
    at.selectbox(key="bt_rebal").set_value("quarterly").run()
    at.session_state["bt_run"] = None
    at.run()
    [b for b in at.button if b.label == "보기"][0].click().run()
    assert not at.exception
    assert at.session_state.bt_rebal == "yearly"
    assert at.session_state.bt_run.results[0].name == "주식 60 / 장기채 30 / 금 10"
    assert any("저장한 결과" in c.value for c in at.caption)

    [b for b in at.button if b.label == "삭제" and str(b.key).startswith("bt_rdel")][0].click().run()
    assert not at.exception
    assert fakes.TABLES["backtest_runs"] == []
