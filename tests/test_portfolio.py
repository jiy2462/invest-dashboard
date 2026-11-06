"""포트폴리오 계산과 화면 테스트 (네트워크 불필요)."""
from datetime import date

import pandas as pd
import pytest

from lib import portfolio as pf
from tests import fakes
from tests.test_pages import run


def tx(rows):
    return pd.DataFrame([dict(zip(["id", "trade_date", "symbol", "side", "quantity", "price", "currency", "fee",
                                   "fx_rate"], r)) for r in rows])


# ── 계산 ────────────────────────────────────────────────────

def test_moving_average_cost_and_realized_krw():
    t = tx([
        (1, "2026-01-02", "SPYM", "buy", 10, 80, "USD", 0, 1400),
        (2, "2026-02-02", "SPYM", "buy", 10, 90, "USD", 0, 1500),
        (3, "2026-03-02", "SPYM", "sell", 5, 100, "USD", 1, 1450),
        (4, "2026-03-20", "SPYM", "dividend", 0, 7.5, "USD", 0, 1480),
    ])
    pos, ev = pf.compute_positions(t)
    p = pos.set_index("symbol").loc["SPYM"]
    assert p["quantity"] == 15
    assert p["avg_cost"] == pytest.approx(85)              # 매도해도 평단 유지
    assert p["avg_cost_krw"] == pytest.approx(123500)      # (800*1400 + 900*1500) / 20
    assert p["realized"] == pytest.approx(5 * 100 - 1 - 5 * 85)
    assert p["realized_krw"] == pytest.approx((500 - 1) * 1450 - 5 * 123500)
    assert p["dividends_krw"] == pytest.approx(7.5 * 1480)
    assert list(ev["kind"]) == ["매도", "배당"]


def test_full_sell_resets_and_krw_has_no_fx():
    t = tx([
        (1, "2026-01-02", "005930.KS", "buy", 10, 50000, "KRW", 100, None),
        (2, "2026-01-05", "005930.KS", "sell", 10, 60000, "KRW", 0, None),
    ])
    pos, _ = pf.compute_positions(t)
    p = pos.iloc[0]
    assert p["quantity"] == 0 and p["cost_krw"] == 0
    assert p["realized_krw"] == pytest.approx(600000 - 500100)


def test_missing_fx_uses_current_and_valuation():
    t = tx([(1, "2026-01-02", "SPYM", "buy", 2, 80, "USD", 0, None)])
    pos, _ = pf.compute_positions(t, {"USD": 1400})
    v = pf.value_positions(pos, {"SPYM": 90}, {"USD": 1450}).iloc[0]
    assert v["cost_krw"] == pytest.approx(160 * 1400)
    assert v["value_krw"] == pytest.approx(180 * 1450)
    assert v["pnl_pct"] == pytest.approx(12.5)


def test_value_without_price_falls_back_to_cost():
    t = tx([(1, "2026-01-02", "MANUAL:금", "buy", 10, 150000, "KRW", 0, None)])
    pos, _ = pf.compute_positions(t)
    v = pf.value_positions(pos, {}, {}).iloc[0]
    assert v["price_missing"] and v["value_krw"] == 1500000


def summary_fixture(values, weights, goal=None):
    valued = pd.DataFrame({"symbol": list(values), "quantity": 1.0, "value_krw": list(values.values()),
                           "cost_krw": list(values.values())})
    targets = pd.DataFrame({"asset_class": list(weights), "target_weight": list(weights.values()),
                            "monthly_contribution": 0, "target_return": 10.0})
    return pf.class_summary(valued, {s: s for s in values}, pd.DataFrame(), targets, {}, goal)


def test_class_summary_weights_and_goal():
    s = summary_fixture({"주식": 600, "금": 200, "현금": 200}, {"주식": 50, "금": 30, "현금": 20}, goal=2000)
    s = s.set_index("asset_class")
    assert s.loc["주식", "weight"] == pytest.approx(60)
    assert s.loc["주식", "diff_pp"] == pytest.approx(10)
    assert s.loc["금", "target_amount"] == pytest.approx(600)
    assert s.loc["금", "progress"] == pytest.approx(200 / 600 * 100)
    assert s.loc["금", "target_profit"] == pytest.approx(60)


def test_cash_and_unclassified_rows():
    valued = pd.DataFrame({"symbol": ["X"], "quantity": [1.0], "value_krw": [100.0], "cost_krw": [80.0]})
    cash = pd.DataFrame({"asset_class": ["현금"], "currency": ["USD"], "amount": [10]})
    targets = pd.DataFrame({"asset_class": ["현금"], "target_weight": [100], "monthly_contribution": [0],
                            "target_return": [None]})
    s = pf.class_summary(valued, {}, cash, targets, {"USD": 1000}).set_index("asset_class")
    assert s.loc["현금", "value_krw"] == 10000
    assert s.loc[pf.UNCLASSIFIED, "pnl_krw"] == 20


def test_rebalance_new_money_only_buys_underweight():
    s = summary_fixture({"주식": 600, "금": 200, "현금": 200}, {"주식": 50, "금": 30, "현금": 20})
    plan = pf.rebalance(s, 200).set_index("asset_class")
    assert (plan["trade_krw"] >= 0).all()
    assert plan["trade_krw"].sum() == pytest.approx(200)
    # 총 1200 기준 목표: 주식 600, 금 360, 현금 240 → 부족분 금 160, 현금 40
    assert plan.loc["금", "trade_krw"] == pytest.approx(160)
    assert plan.loc["현금", "trade_krw"] == pytest.approx(40)
    assert plan.loc["주식", "trade_krw"] == pytest.approx(0)


def test_rebalance_scales_when_money_short_and_full_with_sell():
    s = summary_fixture({"주식": 600, "금": 200, "현금": 200}, {"주식": 50, "금": 30, "현금": 20})
    plan = pf.rebalance(s, 100).set_index("asset_class")
    assert plan["trade_krw"].sum() == pytest.approx(100)
    assert plan.loc["금", "trade_krw"] > plan.loc["현금", "trade_krw"] > 0
    full = pf.rebalance(s, 0, allow_sell=True).set_index("asset_class")
    assert full.loc["주식", "trade_krw"] == pytest.approx(-100)
    assert full["after_weight"].round(6).tolist() == [50, 30, 20]


def test_rebalance_surplus_goes_by_weight():
    s = summary_fixture({"주식": 500, "금": 500}, {"주식": 50, "금": 50})
    plan = pf.rebalance(s, 100).set_index("asset_class")
    assert plan.loc["주식", "trade_krw"] == pytest.approx(50)


def test_month_buys_by_class():
    t = tx([
        (1, "2026-11-02", "SPYM", "buy", 1, 100, "USD", 0, 1400),
        (2, "2026-10-30", "SPYM", "buy", 1, 100, "USD", 0, 1400),
        (3, "2026-11-03", "005930.KS", "buy", 1, 50000, "KRW", 0, None),
    ])
    out = pf.month_buys_by_class(t, {"SPYM": "S&P 500"}, date(2026, 11, 6))
    assert out == {"S&P 500": 140000, pf.UNCLASSIFIED: 50000}


def test_parse_csv():
    accounts = pd.DataFrame({"id": [1], "name": ["ISA"]})
    raw = pd.DataFrame([
        ["2026-03-04", "ISA", "매수", "360750.KS", "TIGER 미국S&P500", "S&P 500", "5", "24000", "", "", "", ""],
        ["2026-03-05", "", "배당", "SPYM", "", "", "", "7.49", "USD", "", "1434.71", ""],
        ["2026-03-05", "없는계좌", "매수", "SPYM", "", "", "1", "80", "USD", "", "", ""],
        ["2026-03-05", "", "보유", "SPYM", "", "", "1", "80", "USD", "", "", ""],
    ], columns=pf.CSV_HEADERS)
    rows, assets, errs = pf.parse_csv(raw, accounts)
    assert len(rows) == 2 and len(errs) == 2
    assert rows[0]["currency"] == "KRW" and rows[0]["account_id"] == 1 and rows[0]["fx_rate"] is None
    assert rows[1]["side"] == "dividend" and rows[1]["quantity"] == 0 and rows[1]["fx_rate"] == 1434.71
    assert assets[0]["asset_class"] == "S&P 500"


# ── 화면 ────────────────────────────────────────────────────

def seed():
    T = fakes.TABLES
    T["accounts"] += [{"id": 1, "name": "ISA", "broker": "NH", "currency": "KRW"},
                      {"id": 2, "name": "메리츠", "broker": "메리츠", "currency": "USD"}]
    T["target_allocation"] += [
        {"asset_class": "S&P 500", "target_weight": 60, "monthly_contribution": 1000000, "target_return": 12,
         "status_note": "ISA 위주", "strategy": "적립식", "sort_order": 1},
        {"asset_class": "금", "target_weight": 20, "monthly_contribution": 500000, "target_return": 15,
         "status_note": None, "strategy": None, "sort_order": 2},
        {"asset_class": "투자유보현금", "target_weight": 20, "monthly_contribution": 0, "target_return": 2.5,
         "status_note": None, "strategy": None, "sort_order": 3},
    ]
    T["assets"] += [
        {"symbol": "SPYM", "name": "SPYM", "market": "US", "currency": "USD", "asset_class": "S&P 500",
         "manual_price": None},
        {"symbol": "MANUAL:KRX 금현물", "name": "KRX 금현물", "market": None, "currency": "KRW",
         "asset_class": "금", "manual_price": 150000},
    ]
    T["transactions"] += [
        {"id": 1, "trade_date": "2026-03-04", "account_id": 2, "symbol": "SPYM", "side": "buy", "quantity": 10,
         "price": 80, "currency": "USD", "fee": 0, "fx_rate": 1415, "memo": "정기매수"},
        {"id": 2, "trade_date": "2026-04-04", "account_id": 1, "symbol": "MANUAL:KRX 금현물", "side": "buy",
         "quantity": 10, "price": 140000, "currency": "KRW", "fee": 0, "fx_rate": None, "memo": None},
        {"id": 3, "trade_date": "2026-06-17", "account_id": 2, "symbol": "SPYM", "side": "dividend", "quantity": 0,
         "price": 15.45, "currency": "USD", "fee": 0, "fx_rate": 1510.96, "memo": None},
    ]
    T["cash_balances"] += [{"id": 1, "name": "RP", "asset_class": "투자유보현금", "account_id": None,
                            "currency": "KRW", "amount": 1000000, "memo": None, "updated_at": None}]


def test_portfolio_page_empty():
    at = run("portfolio")
    assert not at.exception
    assert any("아직 보유 종목이 없어요" in i.value for i in at.info)


def test_portfolio_page_with_data():
    seed()
    at = run("portfolio")
    assert not at.exception
    assert [t.label for t in at.tabs] == ["보유 현황", "매수 입력", "자산 배분·리밸런싱", "매도·배당·거래 내역",
                                           "실현손익·배당", "달러 환전", "설정"]
    assert at.metric[0].label == "총 평가금액"
    assert any(s.value == "리밸런싱" for s in at.subheader)
    md = " ".join(m.value for m in at.markdown)
    assert "매수:" in md or "그대로" in md
    at.toggle[0].set_value(True).run()      # 원화로 보기
    assert not at.exception
    [r for r in at.radio if r.label == "방식"][0].set_value("목표 비중에 맞추기 (매도 포함)").run()
    assert not at.exception
    at.multiselect[0].set_value([1]).run()  # ISA 계좌만
    assert not at.exception


def test_add_transactions_and_snapshot():
    seed()
    at = run("portfolio")
    at.radio(key="tx-side").set_value("buy").run()
    at.selectbox(key="tx-symbol").set_value("SPYM").run()
    at.number_input(key="tx-qty").set_value(3).run()
    at.number_input(key="tx-price").set_value(86.5).run()
    at.number_input(key="tx-fx").set_value(1440).run()
    at.button(key="save-tx").click().run()
    assert not at.exception
    t = fakes.TABLES["transactions"][-1]
    assert (t["symbol"], t["side"], t["quantity"], t["price"], t["fx_rate"]) == ("SPYM", "buy", 3, 86.5, 1440)

    # 보유보다 많이 팔기는 막힌다
    at.radio(key="tx-side").set_value("sell").run()
    at.number_input(key="tx-qty").set_value(100).run()
    at.number_input(key="tx-price").set_value(90).run()
    assert at.button(key="save-tx").disabled

    # 새 종목 검색으로 한국 종목 매수
    at.radio(key="tx-side").set_value("buy").run()
    at.selectbox(key="tx-symbol").set_value("__search__").run()
    at.text_input(key="tx-search").set_value("삼성").run()
    at.number_input(key="tx-qty").set_value(2).run()
    at.number_input(key="tx-price").set_value(98000).run()
    at.button(key="save-tx").click().run()
    assert not at.exception
    assert fakes.TABLES["transactions"][-1]["symbol"] == "005930.KS"
    assert any(a["symbol"] == "005930.KS" and a["asset_class"] == "S&P 500" for a in fakes.TABLES["assets"])

    at.button(key="save-snapshot").click().run()
    assert not at.exception
    assert {s["asset_class"] for s in fakes.TABLES["portfolio_snapshots"]} >= {"S&P 500", "금", "투자유보현금"}


def test_parse_naver_close():
    from lib.market import parse_naver_close
    assert parse_naver_close('{"isSuccess":true,"result":{"closePrice":"176,690.00","fluctuations":"-120"}}') == 176690
    assert parse_naver_close('<script>{"closePrice":"177,050"}</script>') == 177050
    assert parse_naver_close('{"closePrice":"-"}') is None
    assert parse_naver_close("") is None


def holdings(at):
    return next(d.value for d in at.dataframe if "시세" in d.value.columns)


def test_buy_tab_saves_transaction():
    seed()
    at = run("portfolio")
    at.selectbox(key="buy-symbol").set_value("SPYM").run()
    assert at.selectbox(key="buy-account-SPYM").value == 2      # 마지막으로 쓴 계좌가 기본
    at.date_input(key="buy-date").set_value(date(2026, 11, 5))
    at.number_input(key="buy-qty").set_value(4)
    at.number_input(key="buy-price").set_value(88.25)
    at.number_input(key="buy-fx").set_value(1450)
    at.text_input(key="buy-memo").set_value("정기 매수")
    next(b for b in at.button if b.label == "매수 저장").click().run()
    assert not at.exception
    t = fakes.TABLES["transactions"][-1]
    assert (t["symbol"], t["side"], t["quantity"], t["price"], t["fx_rate"], t["account_id"], t["memo"]) == \
        ("SPYM", "buy", 4, 88.25, 1450, 2, "정기 매수")
    assert str(t["trade_date"]) == "2026-11-05"
    assert any("SPYM" in s.value or "4주" in s.value for s in at.success)


def test_save_cash_maps_accounts_and_default_class():
    seed()
    orig = pf.list_cash()
    edited = pd.DataFrame([
        {"id": 1, "name": "RP", "asset_class": "투자유보현금", "account": "ISA", "currency": "KRW",
         "amount": 2000000, "memo": None},
        {"id": None, "name": "예수금", "asset_class": None, "account": None, "currency": "KRW",
         "amount": 50000, "memo": "메모"},
    ])
    pf.save_cash(edited, orig, {"ISA": 1})
    rows = {r["name"]: r for r in fakes.TABLES["cash_balances"]}
    assert rows["RP"]["amount"] == 2000000 and rows["RP"]["account_id"] == 1
    assert rows["예수금"]["asset_class"] == "투자유보현금" and rows["예수금"]["account_id"] is None
    pf.save_cash(edited.iloc[[1]].assign(id=[max(r["id"] for r in fakes.TABLES["cash_balances"])]),
                 pd.DataFrame(fakes.TABLES["cash_balances"]))
    assert [r["name"] for r in fakes.TABLES["cash_balances"]] == ["예수금"]


def test_pie_chart_shown():
    seed()
    at = run("portfolio")
    assert not at.exception
    at.radio(key="pie-by").set_value("종목별").run()
    assert not at.exception


def test_krx_gold_uses_auto_price_over_manual():
    seed()
    fakes.AUTO_PRICES["MANUAL:KRX 금현물"] = 180000
    at = run("portfolio")
    assert not at.exception
    df = holdings(at)
    row = df[df["종목"] == "KRX 금현물"].iloc[0]
    assert row["현재가"] == 180000 and row["시세"] == "시세(네이버)"

    fakes.AUTO_PRICES.clear()               # 네이버 실패 → 직접 입력값
    at = run("portfolio")
    row = holdings(at).set_index("종목").loc["KRX 금현물"]
    assert row["현재가"] == 150000 and row["시세"] == "직접 입력"


def fx_rows(rows):
    return pd.DataFrame([dict(id=i + 1, trade_date=date.fromisoformat(d), side=s, currency="USD", amount=a, rate=r,
                              fee=f, account_id=None, memo=None) for i, (d, s, a, r, f) in enumerate(rows)])


def test_fx_summary_matches_sheet_average_and_tracks_reconversion():
    log = fx_rows([("2025-01-02", "buy", 1000, 1400, 0), ("2025-02-01", "buy", 1000, 1500, 0),
                   ("2025-03-01", "sell", 500, 1480, 1000), ("2025-04-01", "buy", 500, 1300, 0)])
    s = pf.fx_summary(log, current_rate=1450)
    assert s["avg_rate"] == pytest.approx((1400000 + 1500000 + 650000) / 2500)   # 시트 방식: 전체 매수 평균
    assert s["held"] == pytest.approx(2000)
    assert s["realized"] == pytest.approx(500 * 1480 - 1000 - 500 * 1450)        # 이동평균 1450
    assert s["held_avg_rate"] == pytest.approx((1500 * 1450 + 500 * 1300) / 2000)
    assert s["unrealized"] == pytest.approx(2000 * 1450 - (1500 * 1450 + 500 * 1300))


def test_fx_tab_add_and_show():
    seed()
    at = run("portfolio")
    assert not at.exception
    at.number_input(key="fx-amt").set_value(700).run()
    at.number_input(key="fx-rate").set_value(1430).run()
    at.text_input(key="fx-memo").set_value("목표환율 도달").run()
    at.button(key="save-fx").click().run()
    assert not at.exception
    r = fakes.TABLES["fx_exchanges"][-1]
    assert (r["side"], r["currency"], r["amount"], r["rate"], r["memo"]) == ("buy", "USD", 700, 1430, "목표환율 도달")
    assert any(m.label == "평균 환전 환율" and m.value == "1,430.00" for m in at.metric)
