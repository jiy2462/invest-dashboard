"""알림 대기함: 알림 글 읽기 규칙과 화면 (네트워크 불필요)."""
from datetime import date, datetime

import pandas as pd

from lib import trade_inbox as ib
from tests import fakes
from tests.test_pages import run
from tests.test_portfolio import seed

ASSETS = pd.DataFrame([
    {"symbol": "360750.KS", "name": "TIGER 미국S&P500", "currency": "KRW"},
    {"symbol": "QQQM", "name": "Invesco NASDAQ 100 ETF", "currency": "USD"},
    {"symbol": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF", "currency": "USD"},
    {"symbol": "BTC-KRW", "name": "비트코인", "currency": "KRW"},
    {"symbol": "MANUAL:KRX 금현물", "name": "KRX 금현물", "currency": "KRW"},
])
GOT = datetime(2026, 11, 7, 10, 30, tzinfo=ib.KST)


def test_parse_korean_buy():
    g = ib.parse("한국투자증권", "[체결안내] TIGER 미국S&P500 매수 체결\n체결수량: 3주\n체결단가: 21,350원", "kis", ASSETS, GOT)
    assert (g["side"], g["symbol"], g["quantity"], g["price"], g["currency"]) == ("buy", "360750.KS", 3, 21350, "KRW")
    assert g["trade_date"] == date(2026, 11, 7)


def test_parse_us_sell_by_ticker_and_date():
    g = ib.parse("메리츠증권", "2026.11.06 QQQM 매도 체결 수량 2 가격 $245.10 수수료 0.12", "meritz", ASSETS, GOT)
    assert (g["side"], g["symbol"], g["quantity"], g["price"], g["fee"], g["currency"]) == \
        ("sell", "QQQM", 2, 245.10, 0.12, "USD")
    assert g["trade_date"] == date(2026, 11, 6)


def test_parse_dividend_and_upbit_coin():
    g = ib.parse("메리츠증권", "SGOV 배당금 입금 안내 11/5 입금액 USD 12.34", "meritz", ASSETS, GOT)
    assert (g["side"], g["symbol"], g["quantity"], g["price"]) == ("dividend", "SGOV", 0, 12.34)
    assert g["trade_date"] == date(2026, 11, 5)
    u = ib.parse("업비트", "BTC 매수 주문이 체결되었어요. 0.0012 BTC 평균가 152,000,000원", "upbit", ASSETS, GOT)
    assert (u["side"], u["symbol"], u["quantity"], u["price"]) == ("buy", "BTC-KRW", 0.0012, 152000000)


def test_parse_unknown_leaves_blanks():
    g = ib.parse("카카오톡", "오늘 저녁 뭐 먹어?", "other", ASSETS, GOT)
    assert g["side"] is None and g["symbol"] is None and g["price"] is None


def test_account_for_source():
    acc = pd.DataFrame([{"id": 1, "name": "ISA", "broker": "한국투자증권"}, {"id": 4, "name": "업비트", "broker": "업비트"},
                        {"id": 3, "name": "NH 금현물", "broker": "NH투자증권"}])
    assert ib.account_for("upbit", acc) == 4
    assert ib.account_for("kis", acc) == 1
    assert ib.account_for("nh", acc) == 3
    assert ib.account_for("other", acc) is None


def test_inbox_tab_saves_and_ignores():
    seed()
    fakes.TABLES["trade_inbox"] += [
        {"id": 1, "received_at": "2026-11-07T01:30:00+00:00", "source": "meritz", "title": "메리츠증권",
         "body": "SPYM 매수 체결 수량 4주 단가 $88.25", "status": "pending", "transaction_id": None, "handled_at": None},
        {"id": 2, "received_at": "2026-11-07T01:31:00+00:00", "source": "other", "title": "광고",
         "body": "이벤트 안내", "status": "pending", "transaction_id": None, "handled_at": None},
    ]
    at = run("portfolio")
    assert not at.exception
    assert "알림 대기함 2" in [t.label for t in at.tabs]
    assert at.selectbox(key="ib1-symbol").value == "SPYM"
    assert at.selectbox(key="ib1-account").value == 2
    assert at.number_input(key="ib1-qty").value == 4
    assert at.number_input(key="ib1-price").value == 88.25
    assert at.button(key="ib2-save").disabled           # 종목·값을 못 읽은 알림은 바로 못 넣는다
    at.button(key="ib1-save").click().run()
    assert not at.exception
    t = fakes.TABLES["transactions"][-1]
    assert (t["symbol"], t["side"], t["quantity"], t["price"], t["account_id"], t["memo"]) == \
        ("SPYM", "buy", 4, 88.25, 2, "알림 자동 입력")
    first = next(r for r in fakes.TABLES["trade_inbox"] if r["id"] == 1)
    assert first["status"] == "done" and first["transaction_id"] == t["id"]

    at.button(key="ib2-ignore").click().run()
    assert not at.exception
    assert next(r for r in fakes.TABLES["trade_inbox"] if r["id"] == 2)["status"] == "ignored"
    assert "알림 대기함" in [t.label for t in at.tabs]
    at.button(key="ib-restore-btn").click().run()
    assert next(r for r in fakes.TABLES["trade_inbox"] if r["id"] == 2)["status"] == "pending"
    assert at.selectbox(key="ib2-symbol").value is None  # 못 읽은 종목은 비워 둔다
