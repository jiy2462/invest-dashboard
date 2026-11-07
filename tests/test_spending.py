"""지출관리: 카드 알림 읽기 규칙과 화면 (네트워크 불필요)."""
from datetime import datetime

import pytest

from lib import spending as sp
from tests import fakes
from tests.test_pages import run

GOT = datetime(2026, 11, 7, 14, 25, tzinfo=sp.KST)


@pytest.mark.parametrize("title, body, kind, when, merchant, amount, ccy, inst", [
    (None, "신한카드(1234)승인 홍*동 12,500원(일시불)11/07 14:23 스타벅스 누적1,234,567원",
     "pay", (11, 7, 14, 23), "스타벅스", 12500, "KRW", "일시불"),
    (None, "신한카드(1234)승인취소 홍*동 12,500원(일시불)11/07 14:23 스타벅스",
     "cancel", (11, 7, 14, 23), "스타벅스", 12500, "KRW", "일시불"),
    ("현대카드", "현대카드 M 승인\n홍*동\n45,000원 일시불\n11/07 13:10\n쿠팡(쿠페이)\n누적 523,000원",
     "pay", (11, 7, 13, 10), "쿠팡(쿠페이)", 45000, "KRW", "일시불"),
    ("신한 SOL페이", "[신한카드] 김*영님 3,200원 결제 | GS25 역삼점 | 11.07 14:20",
     "pay", (11, 7, 14, 20), "GS25 역삼점", 3200, "KRW", None),
    ("현대카드", "스타벅스코리아에서 6,100원 결제되었습니다.", "pay", (11, 7, 14, 25), "스타벅스코리아", 6100, "KRW", None),
    ("신한카드", "신한카드(9876)해외승인 김*영 USD 12.50 11/07 03:12 AMAZON.COM 누적 800,000원",
     "pay", (11, 7, 3, 12), "AMAZON.COM", 12.5, "USD", None),
    ("신한카드", "신한카드 승인 김*영 120,000원 3개월 11/05 19:40 이마트 성수점",
     "pay", (11, 5, 19, 40), "이마트 성수점", 120000, "KRW", "3개월"),
    ("신한카드", "가맹점: 배달의민족\n결제금액: 23,000원\n일시: 2026.11.07 12:01",
     "pay", (11, 7, 12, 1), "배달의민족", 23000, "KRW", None),
])
def test_parse_card_alerts(title, body, kind, when, merchant, amount, ccy, inst):
    g = sp.parse(title, body, GOT)
    assert (g["kind"], g["merchant"], g["amount"], g["currency"], g["installment"]) == (kind, merchant, amount, ccy, inst)
    assert (g["spent_at"].month, g["spent_at"].day, g["spent_at"].hour, g["spent_at"].minute) == when
    assert g["spent_at"].utcoffset().total_seconds() == 9 * 3600


@pytest.mark.parametrize("body", ["11월 결제예정금액은 1,234,000원입니다.", "이번 주말 혜택 이벤트! 최대 10,000원 캐시백",
                                  "앱 업데이트 안내"])
def test_parse_not_a_payment(body):
    assert sp.parse("현대카드", body, GOT)["kind"] is None


def test_parse_december_alert_read_in_january():
    g = sp.parse(None, "신한카드 승인 3,000원 12/31 23:50 편의점", datetime(2027, 1, 1, 0, 5, tzinfo=sp.KST))
    assert g["spent_at"].year == 2026 and g["spent_at"].month == 12


def _alert(i, card, body, **kw):
    return {"id": i, "received_at": "2026-11-07T05:30:00+00:00", "card": card, "title": None, "body": body,
            "raw": None, "kind": None, "spent_at": None, "merchant": None, "amount": None, "currency": "KRW",
            "installment": None, "parse_version": 0, "edited": False, "memo": None, "checked": False,
            "checked_at": None, **kw}


def test_spending_page_parses_and_checks():
    now = datetime.now(sp.KST)
    stamp = f"{now:%m/%d} 09:00"
    fakes.TABLES["card_expenses"] += [
        _alert(1, "shinhan", f"신한카드(1234)승인 홍*동 12,500원(일시불){stamp} 스타벅스 누적1,234,567원"),
        _alert(2, "hyundai", f"현대카드 M 취소\n홍*동\n2,500원 일시불\n{stamp}\n스타벅스"),
        _alert(3, "hyundai", "11월 결제예정금액은 1,234,000원입니다."),
        _alert(4, "shinhan", "이미 고친 줄", kind="pay", merchant="고친 가게", amount=1000, edited=True,
               spent_at=now.isoformat(), parse_version=0),
    ]
    at = run("spending")
    assert not at.exception
    rows = {r["id"]: r for r in fakes.TABLES["card_expenses"]}
    assert (rows[1]["kind"], rows[1]["merchant"], rows[1]["amount"], rows[1]["parse_version"]) == \
        ("pay", "스타벅스", 12500, sp.PARSER_VERSION)
    assert (rows[2]["kind"], rows[2]["amount"]) == ("cancel", 2500)
    assert rows[3]["kind"] is None
    assert rows[4]["merchant"] == "고친 가게" and rows[4]["parse_version"] == 0     # 사람이 고친 줄은 그대로
    md = " ".join(m.value for m in at.markdown)
    assert "13,500원" in md and "2,500원" in md and "11,000원" in md       # 결제·취소·순지출
    assert any("결제 알림이 아닌 것 1건" in e.label for e in at.expander)

    # 표에서 확인 칸을 누른 것과 같은 변경을 저장
    ids = [1, 2]                                                          # 표에 보이는 순서의 id
    n = sp.apply_edits({0: {"확인": True}, 1: {"거래처": " 스벅 ", "금액": -2000}}, ids)
    assert n == 2
    rows = {r["id"]: r for r in fakes.TABLES["card_expenses"]}
    assert rows[ids[0]]["checked"] and rows[ids[0]]["checked_at"] and not rows[ids[0]]["edited"]
    assert (rows[ids[1]]["merchant"], rows[ids[1]]["amount"], rows[ids[1]]["edited"]) == ("스벅", 2000, True)


def test_spending_manual_add_and_empty():
    at = run("spending")
    assert not at.exception
    assert any("아직 결제 알림이 없어요" in i.value for i in at.info)
    at.text_input(key="sp-add-merchant").set_value("동네 빵집")
    at.number_input(key="sp-add-amount").set_value(8000)
    [b for b in at.button if b.label == "추가"][0].click().run()
    assert not at.exception
    r = fakes.TABLES["card_expenses"][-1]
    assert (r["merchant"], r["amount"], r["kind"], r["edited"]) == ("동네 빵집", 8000.0, "pay", True)
