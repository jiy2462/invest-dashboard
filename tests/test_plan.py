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

def test_weekly_plan_reaches_target():
    s = summary_fixture({"주식": 500, "금": 100}, {"주식": 50, "금": 50})
    sched = pl.schedule(plan_of(months=1, monthly_deposit=400, start="2026-11-09"), date(2026, 11, 9))
    n = len(sched["remaining"])
    w = pl.weekly_plan(s, sched).set_index("asset_class")
    assert w["weekly"].sum() == pytest.approx(sched["per_buy"])
    assert w.loc["금", "weekly"] == pytest.approx(400 / n) and w.loc["주식", "weekly"] == pytest.approx(0)
    assert w.loc["금", "end_weight"] == pytest.approx(50)       # (500, 500) → 목표 비중 도달
    inv2 = pl.project(s, w.reset_index(), sched, sched["remaining"][1])   # 시세 그대로면 다음 주도 같은 금액
    sched2 = pl.schedule(plan_of(months=1, monthly_deposit=400, start="2026-11-09"), sched["remaining"][1])
    w2 = pl.weekly_plan(inv2, sched2).set_index("asset_class")
    assert w2.loc["금", "weekly"] == pytest.approx(w.loc["금", "weekly"])


def test_cash_with_zero_target_is_spread_over_period():
    # 유보현금 목표 0% → 300을 남은 매수일에 똑같이 꺼내 부족한 자산군에 넣는다
    s = summary_fixture({"주식": 500, "금": 100, "투자유보현금": 300}, {"주식": 50, "금": 50, "투자유보현금": 0})
    sched = pl.schedule(plan_of(months=1, monthly_deposit=100, start="2026-11-09"), date(2026, 11, 9))
    n = len(sched["remaining"])
    w = pl.weekly_plan(s, sched, ["투자유보현금"]).set_index("asset_class")
    assert w.loc["투자유보현금", "weekly"] == pytest.approx(-300 / n)
    assert w.loc["투자유보현금", "cash_out"] == pytest.approx(300 / n)
    assert w.loc["투자유보현금", "end_value"] == pytest.approx(0)
    assert w.loc["금", "total_buy"] == pytest.approx(400)              # 새 돈 100 + 꺼낸 현금 300
    assert w["total_buy"].sum() == pytest.approx(100)                   # 전체로는 새로 넣은 돈만큼 늘어남
    assert w.loc["금", "end_weight"] == pytest.approx(50)
    # 현금 자산군으로 고르지 않으면 꺼내지 않고 그냥 함께 계산한다(현금은 그대로)
    plain = pl.weekly_plan(s, sched, []).set_index("asset_class")
    assert plain.loc["투자유보현금", "weekly"] == 0 and plain["cash_out"].sum() == 0


def test_cash_with_target_only_excess_is_spread():
    # 목표 10%: 목표일 총액 1000(지금 900 + 새 돈 100)의 10% = 100만 남기고 200을 나눠 꺼낸다
    s = summary_fixture({"주식": 500, "금": 100, "현금": 300}, {"주식": 45, "금": 45, "현금": 10})
    sched = pl.schedule(plan_of(months=1, monthly_deposit=100, start="2026-11-09"), date(2026, 11, 9))
    w = pl.weekly_plan(s, sched, ["현금"]).set_index("asset_class")
    assert w.loc["현금", "total_buy"] == pytest.approx(-200)
    assert w.loc["현금", "end_weight"] == pytest.approx(10)
    assert w["end_value"].sum() == pytest.approx(1000)


def test_weekly_plan_overweight_class_gets_nothing_and_plan_over():
    s = summary_fixture({"주식": 900, "금": 50, "채권": 50}, {"주식": 50, "금": 25, "채권": 25})
    sched = pl.schedule(plan_of(months=1, monthly_deposit=200, start="2026-11-09"), date(2026, 11, 9))
    w = pl.weekly_plan(s, sched).set_index("asset_class")
    assert w.loc["주식", "weekly"] == 0 and w.loc["주식", "end_weight"] > 50      # 적립만으로는 못 맞춤
    assert w.loc["금", "total_buy"] == pytest.approx(100)
    ended = pl.schedule(plan_of(months=1, start="2026-01-01"), date(2026, 11, 9))
    assert pl.weekly_plan(s, ended)["weekly"].sum() == 0


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
    assert p["start"] is None and p["monthly_deposit"] == 1500000 and p["cash_classes"] == ["투자유보현금"]
    pl.save_plan({**p, "months": 9, "weekday": 2, "lump_sum": 5000000}, date(2026, 11, 10))
    q = pl.load_plan(pf.list_targets())
    assert (q["months"], q["weekday"], q["lump_sum"], q["start"]) == (9, 2, 5000000, date(2026, 11, 10))
    assert pl.clean_plan({"months": 0, "weekday": 9, "monthly_deposit": -5, "start": "x"})["months"] == 1


def test_save_targets_keeps_monthly_and_renames_cash_class_in_plan():
    seed()
    pl.save_plan(pl.load_plan(pf.list_targets()), date(2026, 11, 10))
    original = pf.list_targets()
    edited = original[["asset_class", "target_weight", "target_return", "strategy"]].copy()   # 월 적립 열 없음
    edited.loc[0, "target_weight"] = 80
    edited.loc[2, "asset_class"] = "유보현금"
    pf.save_targets(edited, original)
    rows = {r["asset_class"]: r for r in fakes.TABLES["target_allocation"]}
    assert rows["S&P 500"]["target_weight"] == 80 and rows["S&P 500"]["monthly_contribution"] == 1000000
    assert pl.load_plan()["cash_classes"] == ["유보현금"]


# ── 화면 ────────────────────────────────────────────────────

def frames(at, col):
    return [d.value for d in at.dataframe if col in d.value.columns]


def test_settings_plan_table_and_save():
    seed()                                   # 목표 60/20/20, 유보현금(RP) 100만원
    at = run("portfolio")
    assert not at.exception
    assert any(s.value == "적립 계획" for s in at.subheader)
    table = frames(at, "남은 기간 합계")[0]
    assert "투자유보현금" in list(table["자산군"])                          # 함께 계산
    md = " ".join(m.value for m in at.markdown)
    assert "요일에 살 금액" in md
    at.number_input(key="plan-months").set_value(12)
    at.number_input(key="plan-monthly").set_value(2000000)
    at.number_input(key="plan-lump").set_value(4000000)
    at.selectbox(key="plan-weekday").set_value(4).run()
    assert not at.exception
    assert any("저장하지 않은" in c.value or "저장한 적립 계획이 없어요" in c.value for c in at.caption)
    table = frames(at, "남은 기간 합계")[0]
    assert table["남은 기간 합계"].sum() == pytest.approx(2000000 * 12 + 4000000, abs=5)
    at.button(key="save-plan").click().run()
    assert not at.exception
    saved = next(r for r in fakes.TABLES["app_settings"] if r["key"] == pl.PLAN_KEY)["value"]
    assert (saved["months"], saved["weekday"], saved["monthly_deposit"], saved["lump_sum"]) == \
        (12, 4, 2000000, 4000000)
    assert saved["start"] and saved["cash_classes"] == ["투자유보현금"]
    assert at.button(key="save-plan").disabled


def test_settings_zero_cash_target_spreads_reserve():
    seed()
    for r in fakes.TABLES["target_allocation"]:
        r["target_weight"] = {"S&P 500": 70, "금": 30, "투자유보현금": 0}[r["asset_class"]]
    at = run("portfolio")
    assert not at.exception
    table = frames(at, "남은 기간 합계")[0].set_index("자산군")
    assert table.loc["투자유보현금", "남은 기간 합계"] == pytest.approx(-1000000, abs=2)   # RP 100만원 전부 투입
    assert table.loc["투자유보현금", "목표일 예상 비중"] == pytest.approx(0, abs=0.01)
    md = " ".join(m.value for m in at.markdown)
    assert "꺼내는 돈" in md
    at.multiselect(key="plan-cash").set_value([]).run()                    # 현금 자산군 지정 해제
    table = frames(at, "남은 기간 합계")[0].set_index("자산군")
    assert table.loc["투자유보현금", "남은 기간 합계"] == 0


def test_alloc_tab_rebalance_includes_reserve():
    seed()
    at = run("portfolio")
    assert not at.exception
    subs = [s.value for s in at.subheader]
    assert "리밸런싱" in subs
    md = " ".join(m.value for m in at.markdown)
    assert "다음 리밸런싱" in md
    rb = next(d.value for d in at.dataframe if "매수/매도" in d.value.columns and "현재 금액" in d.value.columns)
    assert list(rb["자산군"]) == ["S&P 500", "금", "투자유보현금"]
    assert rb["매수/매도"].sum() == pytest.approx(0, abs=2)                   # 판 만큼 산다
    assert rb["이후 비중"].round(1).tolist() == [60.0, 20.0, 20.0]
    at.number_input(key="rebal-extra").set_value(1000000).run()
    assert not at.exception
    rb = next(d.value for d in at.dataframe if "매수/매도" in d.value.columns and "현재 금액" in d.value.columns)
    assert rb["매수/매도"].sum() == pytest.approx(1000000, abs=2)
    at.segmented_control(key="rebal-basis").set_value("리밸런싱 날 예상").run()
    assert not at.exception
    assert any("예상 금액" in d.value.columns for d in at.dataframe)
