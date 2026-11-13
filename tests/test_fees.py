"""매매 수수료·ETF 연보수: 계산, 포트폴리오 입력 자동 수수료, 백테스트 비용 반영 (네트워크 불필요)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from lib import backtest as bt
from lib import fees
from tests import fakes
from tests.test_pages import run

RATES = fees.merge(None)


# ── 계산 ────────────────────────────────────────────────────

def test_trade_fee_rounding_and_defaults():
    assert RATES["brokers"] == {"한국투자증권": 0.0146, "메리츠증권": 0.007, "NH투자증권": 0.22}
    assert fees.trade_fee(10, 19_000, 0.0146, "KRW") == 27          # 27.74 → 원 미만 버림
    assert fees.trade_fee(1, 195_000, 0.22, "KRW") == 429            # 금 1g
    assert fees.trade_fee(40, 250, 0.007, "USD") == 0.70
    assert fees.trade_fee(1, 80, 0.007, "USD") == 0.01
    assert fees.trade_fee(5, 100, None, "KRW") == 0.0
    assert fees.expense_of("gldm", RATES) == 0.10 and fees.expense_of("QQQ", RATES) is None


def test_rate_for_account_matches_broker_name():
    acc = pd.DataFrame({"id": [1, 2, 3, 4], "name": ["ISA", "메리츠 미국", "NH 금현물", "업비트"],
                        "broker": ["한국투자증권", "메리츠 증권", "NH투자증권", "업비트"]})
    assert fees.rate_for_account(1, acc, RATES) == 0.0146
    assert fees.rate_for_account(2, acc, RATES) == 0.007          # 띄어쓰기 달라도 같은 증권사
    assert fees.rate_for_account(3, acc, RATES) == 0.22
    assert fees.rate_for_account(4, acc, RATES) is None
    assert fees.rate_for_account(None, acc, RATES) is None


def test_saved_rates_override_and_clean():
    r = fees.merge({"brokers": {"업비트": 0.05, None: 1, "x": float("nan")}, "expense": {"qqqm": "0.15"}})
    assert r["brokers"] == {"업비트": 0.05} and r["expense"] == {"QQQM": 0.15}


def test_yearly_cost_only_for_etfs_with_expense():
    held = pd.DataFrame({"symbol": ["QQQM", "MANUAL:KRX 금현물"], "value_krw": [10_000_000, 5_000_000]})
    er, cost, base = fees.yearly_cost(held, RATES)
    assert cost == pytest.approx(15_000) and base == 10_000_000
    assert er.isna().tolist() == [False, True]


# ── 포트폴리오 화면 ────────────────────────────────────────

def seed():
    T = fakes.TABLES
    T["accounts"] += [{"id": 1, "name": "ISA", "broker": "한국투자증권", "currency": "KRW"},
                      {"id": 2, "name": "메리츠 미국", "broker": "메리츠증권", "currency": "USD"},
                      {"id": 3, "name": "NH 금현물", "broker": "NH투자증권", "currency": "KRW"}]
    T["target_allocation"] += [{"asset_class": "S&P 500", "target_weight": 100, "monthly_contribution": 0,
                                "target_return": None, "status_note": None, "strategy": None, "sort_order": 1}]
    T["assets"] += [
        {"symbol": "SPYM", "name": "SPYM", "market": "US", "currency": "USD", "asset_class": "S&P 500",
         "manual_price": None},
        {"symbol": "360750.KS", "name": "TIGER 미국S&P500", "market": "KR", "currency": "KRW",
         "asset_class": "S&P 500", "manual_price": None},
        {"symbol": "MANUAL:KRX 금현물", "name": "KRX 금현물", "market": None, "currency": "KRW",
         "asset_class": "S&P 500", "manual_price": 150000},
    ]
    T["transactions"] += [
        {"id": 1, "trade_date": "2026-03-04", "account_id": 2, "symbol": "SPYM", "side": "buy", "quantity": 10,
         "price": 80, "currency": "USD", "fee": 0, "fx_rate": 1415, "memo": None},
        {"id": 2, "trade_date": "2026-03-04", "account_id": 1, "symbol": "360750.KS", "side": "buy", "quantity": 10,
         "price": 20000, "currency": "KRW", "fee": 0, "fx_rate": None, "memo": None},
        {"id": 3, "trade_date": "2026-03-04", "account_id": 3, "symbol": "MANUAL:KRX 금현물", "side": "buy",
         "quantity": 2, "price": 140000, "currency": "KRW", "fee": 0, "fx_rate": None, "memo": None},
    ]


def test_buy_tab_fills_fee_from_broker_rate_unless_typed():
    seed()
    at = run("portfolio")
    at.selectbox(key="buy-symbol").set_value("360750.KS").run()
    assert at.selectbox(key="buy-account-360750.KS").value == 1
    at.number_input(key="buy-qty").set_value(10)
    at.number_input(key="buy-price").set_value(19_000)
    next(b for b in at.button if b.label == "매수 저장").click().run()
    assert not at.exception
    assert fakes.TABLES["transactions"][-1]["fee"] == 27            # 190,000 × 0.0146%

    at.selectbox(key="buy-symbol").set_value("SPYM").run()
    at.number_input(key="buy-qty").set_value(40)
    at.number_input(key="buy-price").set_value(250)
    at.number_input(key="buy-fee").set_value(1.5)                  # 직접 넣은 값이 우선
    next(b for b in at.button if b.label == "매수 저장").click().run()
    assert fakes.TABLES["transactions"][-1]["fee"] == 1.5


def test_sell_tab_auto_fee_and_holdings_expense_column():
    seed()
    at = run("portfolio")
    at.selectbox(key="tx-symbol").set_value("MANUAL:KRX 금현물").run()
    at.radio(key="tx-side").set_value("sell").run()
    at.selectbox(key="tx-account").set_value(3).run()
    at.number_input(key="tx-qty").set_value(1).run()
    at.number_input(key="tx-price").set_value(195_000).run()
    assert any("0.22%" in c.value and "₩429" in c.value for c in at.caption)
    at.button(key="save-tx").click().run()
    assert not at.exception
    assert fakes.TABLES["transactions"][-1]["fee"] == 429

    held = next(d.value for d in at.dataframe if "시세" in d.value.columns)
    er = dict(zip(held["티커"], held["연보수"]))
    assert er["SPYM"] == 0.02 and er["360750.KS"] == 0.0939 and pd.isna(er["KRX 금현물"])
    assert any("ETF 연보수" in c.value and "따로 한 번 더 빼지 않았어요" in c.value for c in at.caption)


def test_inbox_uses_alert_fee_or_broker_rate():
    seed()
    fakes.TABLES["trade_inbox"] += [
        {"id": 1, "received_at": "2026-11-07T01:30:00+00:00", "source": "meritz", "title": "메리츠증권",
         "body": "SPYM 매수 체결 수량 40주 단가 $250", "status": "pending", "transaction_id": None, "handled_at": None},
        {"id": 2, "received_at": "2026-11-07T01:31:00+00:00", "source": "kis", "title": "한국투자증권",
         "body": "TIGER 미국S&P500 매수 체결 10주 단가 19,000원 수수료 30원", "status": "pending",
         "transaction_id": None, "handled_at": None},
    ]
    at = run("portfolio")
    assert at.number_input(key="ib1-fee").value is None              # 알림에 없으면 비워 두고 자동
    assert at.number_input(key="ib2-fee").value == 30
    at.button(key="ib1-save").click().run()
    assert fakes.TABLES["transactions"][-1]["fee"] == 0.70
    at.button(key="ib2-save").click().run()
    assert fakes.TABLES["transactions"][-1]["fee"] == 30


def test_settings_save_rates():
    seed()
    at = run("portfolio")
    at.button(key="save-fees").click().run()
    assert not at.exception
    saved = next(r for r in fakes.TABLES["app_settings"] if r["key"] == fees.SETTINGS_KEY)["value"]
    assert saved["brokers"]["NH투자증권"] == 0.22 and saved["expense"]["379810.KS"] == 0.1447


# ── 백테스트 ───────────────────────────────────────────────

def test_simulate_charges_trade_fees_on_buys_and_rebalance():
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    prices = pd.DataFrame({"A": pd.Series(100.0, index=idx)})
    sim = bt.simulate(prices, {"A": 100}, 1000, 100, "none", {"A": 1.0})
    assert sim["value"].iloc[-1] == pytest.approx(2100 * 0.99)
    assert sim["fees"].iloc[-1] == pytest.approx(21)
    assert sim["nav"].iloc[-1] < 1.0
    up = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    prices = pd.DataFrame({"A": up, "B": pd.Series(100.0, index=idx)})
    free = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "quarterly")
    paid = bt.simulate(prices, {"A": 50, "B": 50}, 1000, 0, "quarterly", {"A": 0.5, "B": 0.5})
    assert paid["value"].iloc[-1] < free["value"].iloc[-1]
    assert paid["fees"].iloc[-1] > 5            # 처음 5 + 리밸런싱 때 매매분
    assert paid["nav"].iloc[-1] / free["nav"].iloc[-1] == pytest.approx(
        paid["value"].iloc[-1] / free["value"].iloc[-1])


def test_expense_only_adjusts_by_difference_to_embedded_fee():
    idx = pd.date_range("2000-01-01", periods=731, freq="D")
    flat = pd.Series(100.0, index=idx)
    segs = [{"ticker": "^GSPC", "start": idx[0], "end": idx[365]},
            {"ticker": "SPY", "start": idx[365], "end": idx[-1]}]
    s, applied = bt.apply_expense(flat, segs, 0.0945)            # SPY 보수 그대로면 SPY 구간은 그대로
    assert applied == {"^GSPC": pytest.approx(-0.0945), "SPY": pytest.approx(0.0)}
    assert s.iloc[365] == pytest.approx(100 * (1 - 0.000945) ** (365 / 365.25))
    assert s.iloc[-1] == pytest.approx(s.iloc[365])
    gold = [{"ticker": "GLD", "start": idx[0], "end": idx[-1]}]
    g, applied = bt.apply_expense(flat, gold, 0.10)              # GLDM으로 사면 GLD 0.40%와의 차이만 되돌림
    assert applied["GLD"] == pytest.approx(0.30) and g.iloc[-1] > 100
    same, applied = bt.apply_expense(flat, segs, None)
    assert same.equals(flat) and applied == {}


def test_default_costs_follow_how_user_buys():
    c = bt.default_costs(["US_STOCK", "NASDAQ100", "GOLD", "US_STB", "KOSPI", "US_LTB", "BTC", "BTC_TREND",
                          "T:AAPL", "T:005930.KS"], RATES)
    assert (c["US_STOCK"]["trade"], c["US_STOCK"]["er"]) == (0.007, 0.02)
    assert (c["NASDAQ100"]["trade"], c["NASDAQ100"]["er"]) == (0.007, 0.15)
    assert (c["GOLD"]["trade"], c["GOLD"]["er"]) == (0.22, 0.0)
    assert (c["US_STB"]["trade"], c["US_STB"]["er"]) == (0.007, 0.09)
    assert (c["KOSPI"]["trade"], c["KOSPI"]["er"]) == (0.0146, 0.15)
    assert (c["US_LTB"]["trade"], c["US_LTB"]["er"]) == (0.007, 0.15)
    assert (c["BTC"]["trade"], c["BTC_TREND"]["trade"]) == (0.05, 0.0)
    assert c["T:AAPL"] == {"trade": 0.007, "er": None, "basis": "AAPL · 메리츠"}
    assert c["T:005930.KS"]["trade"] == 0.0146


def test_run_backtest_with_costs_is_lower_and_saves():
    hist = fakes.fake_history(bt.needed_tickers({"US_STOCK", "US_ITB"}))
    ports = [bt.Portfolio("60/40", {"US_STOCK": 60, "US_ITB": 40})]
    args = (ports, date(1996, 11, 6), date(2026, 11, 5), 10_000_000, 1_000_000, "yearly", "KRW", hist)
    free = bt.run_backtest(*args)
    costs = bt.default_costs({"US_STOCK", "US_ITB"}, RATES)
    paid = bt.run_backtest(*args, costs)
    # SPYM(0.02%)이 SPY(0.0945%)·VFINX(0.14%)보다 싸서 그 차이는 되돌려 받으므로, 결과가 꼭 낮아지지는 않는다
    assert paid.results[0].stats["final"] != free.results[0].stats["final"]
    dear = bt.run_backtest(*args, {"US_STOCK": {"trade": 0.1, "er": 0.5}, "US_ITB": {"trade": 0.1, "er": 0.5}})
    assert dear.results[0].stats["final"] < free.results[0].stats["final"]
    assert paid.results[0].stats["fees_paid"] > 0
    assert "연보수 조정" in paid.sources.columns and "연보수 조정" not in free.sources.columns
    back = bt.run_from_json(bt.run_to_json(paid))
    assert list(back.sources["연보수 조정"]) == list(paid.sources["연보수 조정"])


def test_backtest_page_runs_with_costs():
    at = run("backtest")
    assert not at.exception
    assert at.toggle(key="bt_cost").value is True
    assert any("비용 가정" in e.label for e in at.expander)
    next(b for b in at.button if b.label == "백테스트 실행").click().run()
    assert not at.exception
    table = next(d.value for d in at.dataframe if "낸 매매 수수료 (합계)" in d.value.index)
    assert table is not None
    assert any("연보수" in i.value for i in at.info)
