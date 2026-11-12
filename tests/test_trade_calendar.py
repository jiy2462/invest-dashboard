"""거래 캘린더 테스트 (네트워크 불필요)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from lib import portfolio as pf
from lib import trade_calendar as tcal
from tests import fakes
from tests.test_pages import run
from tests.test_portfolio import seed

NAMES = {"QQQM": "Invesco NASDAQ 100 ETF", "360750.KS": "TIGER 미국S&P500", "BTC-KRW": "비트코인",
         "MANUAL:KRX 금현물": "KRX 금현물", "SGOV": "iShares 0-3 Month Treasury Bond ETF"}


def frame(rows):
    cols = ["id", "trade_date", "symbol", "side", "quantity", "price", "currency", "fee", "fx_rate"]
    return pf.normalize_tx(pd.DataFrame([dict(zip(cols, r)) for r in rows]))


def test_shift_month():
    assert tcal.shift_month((2026, 1), -1) == (2025, 12)
    assert tcal.shift_month((2026, 12), 1) == (2027, 1)
    assert tcal.shift_month((2026, 11), -14) == (2025, 9)


def test_month_items_groups_and_orders():
    t = frame([
        (1, "2026-11-03", "360750.KS", "dividend", 0, 1200, "KRW", 0, None),
        (2, "2026-11-03", "QQQM", "buy", 2, 250, "USD", 0, 1450),
        (3, "2026-11-03", "QQQM", "buy", 1, 251, "USD", 0, 1450),     # 같은 날 같은 종목은 합침
        (4, "2026-11-03", "360750.KS", "sell", 5, 20000, "KRW", 0, None),
        (5, "2026-11-05", "SGOV", "dividend", 0, 3.5, "USD", 0.53, 1450),
        (6, "2026-11-07", "BTC-KRW", "buy", 0.00123, 1.4e8, "KRW", 0, None),
        (7, "2026-11-10", "MANUAL:KRX 금현물", "buy", 3, 150000, "KRW", 0, None),
        (8, "2026-10-31", "QQQM", "buy", 9, 250, "USD", 0, 1450),     # 다른 달은 빠짐
    ])
    items = tcal.month_items(t, 2026, 11, lambda s: NAMES.get(s, s))
    assert sorted(items) == [3, 5, 7, 10]
    day3 = items[3]
    assert [i["side"] for i in day3] == ["buy", "sell", "dividend"]
    assert day3[0]["label"] == "QQQM" and day3[0]["text"] == "3주"
    assert day3[1]["label"] == "미국S&P500" and day3[1]["name"] == "TIGER 미국S&P500" and day3[1]["text"] == "5주"
    assert day3[2]["text"] == "₩1,200"
    assert items[5][0]["text"] == "$2.97"                     # 배당은 세금 뺀 받은 금액
    assert items[7][0]["label"] == "비트코인" and items[7][0]["text"] == "0.00123개"
    assert items[10][0]["label"] == "금현물" and items[10][0]["text"] == "3g"
    lst = tcal.month_list(items, 2026, 11)
    assert list(lst.iloc[0]) == ["11/3 (화)", "매수", "Invesco NASDAQ 100 ETF", "3주"]
    s = tcal.month_summary(items, t, 2026, 11)
    assert s == "매수 4건 · 매도 1건 · 배당 ₩1,200 + $2.97"


def test_calendar_html_layout():
    items = {3: [dict(side="buy", label="<QQQM>", name="QQQM", text="3주")]}
    h = tcal.calendar_html(2026, 11, items, date(2026, 11, 12))
    assert h.count("class='tcal-wd'") == 7
    # 2026년 11월 1일은 일요일 → 앞 빈칸 없음, 30일까지, 뒤 빈칸 5개
    assert h.count("tcal-day tcal-out") == 5
    assert "tcal-today" in h and "tcal-chip tcal-buy" in h
    assert "&lt;QQQM&gt;" in h and "<QQQM>" not in h        # 이름은 이스케이프
    assert tcal.month_items(pd.DataFrame(), 2026, 11, str) == {}


def htmls(at):
    return " ".join(e.proto.body for e in at.get("html"))


def test_portfolio_calendar_navigation():
    seed()
    now = datetime.now(ZoneInfo("Asia/Seoul")).date()
    fakes.TABLES["transactions"].append(
        {"id": 9, "trade_date": now.isoformat(), "account_id": 2, "symbol": "SPYM", "side": "buy", "quantity": 4,
         "price": 80, "currency": "USD", "fee": 0, "fx_rate": 1415, "memo": None})
    at = run("portfolio")
    assert not at.exception
    assert any(s.value == "거래 캘린더" for s in at.subheader)
    assert f"{now.year}년 {now.month}월" in " ".join(m.value for m in at.markdown)
    assert "tcal-chip tcal-buy" in htmls(at) and "4주" in htmls(at)
    assert at.button(key="txcal-next").disabled and at.button(key="txcal-now").disabled
    # 이전 달로 계속 넘겨 6월(배당)까지 간다
    target = (2026, 6)
    for _ in range(24):
        if f"{target[0]}년 {target[1]}월" in " ".join(m.value for m in at.markdown):
            break
        at.button(key="txcal-prev").click().run()
        assert not at.exception
    assert "tcal-chip tcal-dividend" in htmls(at) and "$15.45" in htmls(at)
    assert not at.button(key="txcal-next").disabled
    at.button(key="txcal-now").click().run()
    assert f"{now.year}년 {now.month}월" in " ".join(m.value for m in at.markdown)
    # 가장 오래된 거래(3월) 앞으로는 못 넘긴다
    for _ in range(24):
        if at.button(key="txcal-prev").disabled:
            break
        at.button(key="txcal-prev").click().run()
    assert "2026년 3월" in " ".join(m.value for m in at.markdown)
