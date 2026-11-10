"""적립 계획(매주 적립액)·투자유보현금 분리·정기 리밸런싱 테스트 (네트워크 불필요)."""
from datetime import date

import pandas as pd
import pytest

from lib import plan as pl
from lib import portfolio as pf
from tests import fakes
from tests.test_pages import run
from tests.test_portfolio import seed, summary_fixture


def plan_of(**kw):
    return pl.clean_plan({"months": 6, "weekday": 0, "monthly_deposit": 1000, "lump_sum": 0, **kw})


# ── 날짜 ────────────────────────────────────────────────────

def test_add_months_and_buy_dates():
    assert pl.add_months(date(2026, 11, 10), 6) == date(2027, 5, 10)
    assert pl.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)      # 없는 날짜는 그 달 말일
    d = pl.buy_dates(date(2026, 11, 10), date(2026, 12, 7), 0)           # 화요일 시작, 매주 월요일
    assert d == [date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30), date(2026, 12, 7)]
    assert pl.buy_dates(date(2026, 11, 9), date(2026, 11, 9), 0) == [date(2026, 11, 9)]   # 양 끝 포함


def test_rebalance_dates_last_monday_of_feb_and_aug():
    assert pl.last_weekday_of_month(2027, 2) == date(2027, 2, 22)
    assert pl.last_weekday_of_month(2027, 8) == date(2027, 8, 30)
    assert pl.last_weekday_of_month(2028, 2) == date(2028, 2, 28)
    assert pl.rebalance_dates(date(2026, 11, 10)) == [date(2027, 2, 22), date(2027, 8, 30)]
    assert pl.rebalance_dates(date(2027, 2, 22))[0] == date(2027, 2, 22)             # 당일 포함
    assert pl.rebalance_dates(date(2027, 2, 23), 3) == [date(2027, 8, 30), date(2028, 2, 28), date(2028, 8, 28)]
    assert all(d.weekday() == 0 for d in pl.rebalance_dates(date(2026, 1, 1), 10))


def test_schedule_constant_weekly_budget():
    p = plan_of(monthly_deposit=1000, lump_sum=1000, start="2026-11-10")
    s = pl.schedule(p, date(2026, 11, 10))
    assert (s["end"], len(s["dates"]), s["next"]) == (date(2027, 5, 10), 26, date(2026, 11, 16))
    assert s["per_buy"] == pytest.approx(7000 / 26) and s["remaining_money"] == pytest.approx(7000)
    later = pl.schedule(p, date(2026, 11, 23))                 # 매수일 당일: 오늘 회차 포함
    assert len(later["remaining"]) == 25 and later["next"] == date(2026, 11, 23)
    assert later["per_buy"] == pytest.approx(s["per_buy"])     # 매주 합계는 기간 내내 같다
    done = pl.schedule(p, date(2026, 11, 23), bought_today=True)
    assert len(done["remaining"]) == 24 and done["next"] == date(2026, 11, 30)
    assert done["lump_left"] == pytest.approx(1000 * 24 / 26)
    over = pl.schedule(p, date(2027, 6, 1))
    assert over["remaining"] == [] and over["next"] is None and over["remaining_money"] == 0
    assert pl.schedule(plan_of(), date(2026, 11, 10))["start"] == date(2026, 11, 10)   # 저장 전: 오늘부터


# ── 계산 ────────────────────────────────────────────────────

def test_split_reserve_recomputes_weights_among_invest_assets():
    s = summary_fixture({"주식": 600, "금": 200, "투자유보현금": 200}, {"주식": 60, "금": 20, "투자유보현금": 5})
    inv, res = pl.split_reserve(s, ["투자유보현금"])
    inv = inv.set_index("asset_class")
    assert list(res["asset_class"]) == ["투자유보현금"] and res["weight"].iloc[0] == pytest.approx(20)
    assert inv.loc["주식", "weight"] == pytest.approx(75) and inv.loc["주식", "target_weight"] == pytest.approx(75)
    assert inv["target_weight"].sum() == pytest.approx(100)
    both, none = pl.split_reserve(s, [])                       # 따로 두는 자산군이 없으면 전부 투자 자산
    assert len(both) == 3 and none.empty


def test_weekly_plan_reaches_target_and_excludes_reserve():
    s = summary_fixture({"주식": 500, "금": 100, "투자유보현금": 300}, {"주식": 50, "금": 50, "투자유보현금": 5})
    inv, _ = pl.split_reserve(s, ["투자유보현금"])
    sched = pl.schedule(plan_of(months=1, monthly_deposit=400, start="2026-11-09"), date(2026, 11, 9))
    n = len(sched["remaining"])
    w = pl.weekly_plan(inv, sched).set_index("asset_class")
    assert "투자유보현금" not in w.index
    assert w["weekly"].sum() == pytest.approx(sched["per_buy"])
    assert w.loc["금", "weekly"] == pytest.approx(400 / n) and w.loc["주식", "weekly"] == pytest.approx(0)
    assert w.loc["금", "end_weight"] == pytest.approx(50)       # (500, 500) → 목표 비중 도달
    # 시세가 그대로면 다음 주에도 같은 금액이 나온다
    inv2 = pl.project(inv, w.reset_index(), sched, sched["remaining"][1])
    sched2 = pl.schedule(plan_of(months=1, monthly_deposit=400, start="2026-11-09"), sched["remaining"][1])
    w2 = pl.weekly_plan(inv2, sched2).set_index("asset_class")
    assert w2.loc["금", "weekly"] == pytest.approx(w.loc["금", "weekly"])


def test_weekly_plan_overweight_class_gets_nothing_and_plan_over():
    s = summary_fixture({"주식": 900, "금": 50, "채권": 50}, {"주식": 50, "금": 25, "채권": 25})
    sched = pl.schedule(plan_of(months=1, monthly_deposit=200, start="2026-11-09"), date(2026, 11, 9))
    w = pl.weekly_plan(pl.split_reserve(s, [])[0], sched).set_index("asset_class")
    assert w.loc["주식", "weekly"] == 0 and w.loc["주식", "end_weight"] > 50      # 적립만으로는 못 맞춤
    assert w.loc["금", "total_buy"] == pytest.approx(100)
    ended = pl.schedule(plan_of(months=1, start="2026-01-01"), date(2026, 11, 9))
    assert pl.weekly_plan(pl.split_reserve(s, [])[0], ended)["weekly"].sum() == 0


def test_deploy_reserve_cash():
    s = summary_fixture({"주식": 600, "금": 200, "채권": 200}, {"주식": 50, "금": 30, "채권": 20})
    inv, _ = pl.split_reserve(s, [])
    auto = pl.deploy(inv, 200).set_index("asset_class")
    assert auto.loc["금", "trade_krw"] == pytest.approx(160) and auto.loc["채권", "trade_krw"] == pytest.approx(40)
    one = pl.deploy(inv, 200, ["주식"]).set_index("asset_class")           # 하락한 자산군을 직접 고름
    assert one.loc["주식", "trade_krw"] == pytest.approx(200) and one["trade_krw"].sum() == pytest.approx(200)
    assert one.loc["주식", "after_weight"] == pytest.approx(800 / 1200 * 100)
    two = pl.deploy(inv, 100, ["금", "채권"]).set_index("asset_class")
    assert two.loc["금", "trade_krw"] == pytest.approx(100 * 130 / 150) and two.loc["주식", "trade_krw"] == 0


def test_symbol_trades_split_by_holding_value():
    valued = pd.DataFrame({"symbol": ["A", "B", "C"], "currency": ["USD", "KRW", "KRW"], "quantity": [10.0, 30.0, 5.0],
                           "value_krw": [1000.0, 3000.0, 500.0]})
    trades = pd.DataFrame({"asset_class": ["주식", "금", "코인"], "trade_krw": [-400.0, 100.0, 50.0]})
    out = pl.symbol_trades(trades, valued, {"A": "주식", "B": "주식", "C": "금"})
    a = out.set_index("symbol", drop=False)
    assert a.loc["A", "trade_krw"] == pytest.approx(-100) and a.loc["A", "quantity"] == pytest.approx(-1)
    assert a.loc["B", "trade_krw"] == pytest.approx(-300) and a.loc["B", "quantity"] == pytest.approx(-3)
    assert a.loc["C", "quantity"] == pytest.approx(1)
    coin = out[out["asset_class"] == "코인"].iloc[0]
    assert pd.isna(coin["symbol"]) and coin["trade_krw"] == 50               # 보유 종목이 없는 자산군


# ── 저장 ────────────────────────────────────────────────────

def test_plan_save_load_and_defaults():
    seed()
    p = pl.load_plan(pf.list_targets())
    assert p["start"] is None and p["monthly_deposit"] == 1500000 and p["reserve"] == ["투자유보현금"]
    pl.save_plan({**p, "months": 9, "weekday": 2, "lump_sum": 5000000}, date(2026, 11, 10))
    q = pl.load_plan(pf.list_targets())
    assert (q["months"], q["weekday"], q["lump_sum"], q["start"]) == (9, 2, 5000000, date(2026, 11, 10))
    assert pl.clean_plan({"months": 0, "weekday": 9, "monthly_deposit": -5, "start": "x"})["months"] == 1


def test_save_targets_keeps_monthly_and_renames_reserve_in_plan():
    seed()
    pl.save_plan(pl.load_plan(pf.list_targets()), date(2026, 11, 10))
    original = pf.list_targets()
    edited = original[["asset_class", "target_weight", "target_return", "strategy"]].copy()   # 월 적립 열 없음
    edited.loc[0, "target_weight"] = 80
    edited.loc[2, "asset_class"] = "유보현금"
    pf.save_targets(edited, original)
    rows = {r["asset_class"]: r for r in fakes.TABLES["target_allocation"]}
    assert rows["S&P 500"]["target_weight"] == 80 and rows["S&P 500"]["monthly_contribution"] == 1000000
    assert pl.load_plan()["reserve"] == ["유보현금"]


# ── 화면 ────────────────────────────────────────────────────

def frames(at, col):
    return [d.value for d in at.dataframe if col in d.value.columns]


def test_settings_plan_table_and_save():
    seed()
    at = run("portfolio")
    assert not at.exception
    assert any(s.value == "적립 계획" for s in at.subheader)
    table = frames(at, "남은 기간 합계")[0]
    assert list(table["자산군"]) == ["S&P 500", "금"]                       # 투자유보현금은 빠짐
    md = " ".join(m.value for m in at.markdown)
    assert "요일에 살 금액" in md
    at.number_input(key="plan-months").set_value(12)
    at.number_input(key="plan-monthly").set_value(2000000)
    at.number_input(key="plan-lump").set_value(4000000)
    at.selectbox(key="plan-weekday").set_value(4).run()
    assert not at.exception
    assert any("저장하지 않은" in c.value or "저장한 적립 계획이 없어요" in c.value for c in at.caption)
    table = frames(at, "남은 기간 합계")[0]
    total = 2000000 * 12 + 4000000
    assert table["남은 기간 합계"].sum() == pytest.approx(total, abs=2)
    at.button(key="save-plan").click().run()
    assert not at.exception
    saved = fakes.TABLES["app_settings"][-1]
    assert saved["key"] == pl.PLAN_KEY
    assert (saved["value"]["months"], saved["value"]["weekday"], saved["value"]["monthly_deposit"],
            saved["value"]["lump_sum"]) == (12, 4, 2000000, 4000000)
    assert saved["value"]["start"]
    assert at.button(key="save-plan").disabled                              # 저장 뒤에는 바뀐 것이 없음

    at.multiselect(key="plan-reserve").set_value([]).run()                  # 유보현금도 함께 계산
    assert not at.exception
    assert "투자유보현금" in list(frames(at, "남은 기간 합계")[0]["자산군"])


def test_alloc_tab_reserve_card_and_rebalance():
    seed()
    at = run("portfolio")
    assert not at.exception
    subs = [s.value for s in at.subheader]
    assert "투자유보현금" in subs and "리밸런싱" in subs
    md = " ".join(m.value for m in at.markdown)
    assert "다음 리밸런싱" in md and "지금 쓸 수 있는 유보현금" in md and "₩1,000,000" in md
    rb = next(d.value for d in at.dataframe if "매수/매도" in d.value.columns and "현재 금액" in d.value.columns)
    assert list(rb["자산군"]) == ["S&P 500", "금"]
    assert rb["매수/매도"].sum() == pytest.approx(0, abs=2)                   # 판 만큼 산다
    assert rb["이후 비중"].round(1).tolist() == [75.0, 25.0]                 # 60:20 → 유보현금 빼고 75:25
    assert "**매도**" in md and "**매수**" in md

    at.number_input(key="rebal-extra").set_value(1000000).run()
    assert not at.exception
    rb = next(d.value for d in at.dataframe if "매수/매도" in d.value.columns and "현재 금액" in d.value.columns)
    assert rb["매수/매도"].sum() == pytest.approx(1000000, abs=2)
    at.segmented_control(key="rebal-basis").set_value("리밸런싱 날 예상").run()
    assert not at.exception
    assert any("예상 금액" in d.value.columns for d in at.dataframe)

    at.number_input(key="dip-amount").set_value(500000).run()               # 하락 시 유보현금 매수
    assert not at.exception
    dip = [d.value for d in at.dataframe if "매수/매도" in d.value.columns][0]
    assert dip["매수/매도"].sum() == pytest.approx(500000, abs=2)
    at.multiselect(key="dip-classes").set_value(["금"]).run()
    dip = [d.value for d in at.dataframe if "매수/매도" in d.value.columns][0]
    assert list(dip["자산군"]) == ["금"] and dip["매수/매도"].iloc[0] == 500000
