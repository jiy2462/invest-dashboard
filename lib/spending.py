"""지출관리: 신한카드·현대카드 앱 푸시 알림에서 결제·취소 내역을 읽어 card_expenses 표로 정리한다.

알림 원문은 Supabase Edge Function `card-inbox`가 아이폰 단축어(iOS 27 알림 자동화)에서 받아 쌓는다.
일시·거래처·금액은 여기의 읽기 규칙(parse)이 채운다. 규칙을 고치면 PARSER_VERSION을 올린다:
사람이 고치지 않았고 확인 완료도 안 한 줄은 화면을 열 때 새 규칙으로 다시 읽는다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from lib.db import get_client

KST = timezone(timedelta(hours=9))
PARSER_VERSION = 1
CARDS = {"shinhan": "신한카드", "hyundai": "현대카드", "other": "기타"}
KINDS = {"pay": "결제", "cancel": "취소"}
COLUMNS = ["id", "received_at", "card", "title", "body", "kind", "spent_at", "merchant", "amount", "currency",
           "installment", "parse_version", "edited", "memo", "checked", "checked_at"]
PARSED = ("kind", "spent_at", "merchant", "amount", "currency", "installment")

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
# 결제금액이 아닌 숫자 앞에 붙는 말 (누적 이용액, 잔액, 한도 …)
_NOT_AMOUNT = re.compile(r"(누적|잔액|잔여|한도|가능|포인트|적립|캐시백|혜택|청구|결제\s*예정|최대|최소)\s*[:：]?\s*(금액)?\s*$")
_FOREIGN = {"USD": "USD", "US$": "USD", "$": "USD", "달러": "USD", "EUR": "EUR", "€": "EUR", "유로": "EUR",
            "JPY": "JPY", "¥": "JPY", "￥": "JPY", "엔": "JPY", "GBP": "GBP", "£": "GBP", "CNY": "CNY", "위안": "CNY"}
_CCY = r"(US\$|USD|EUR|JPY|GBP|CNY|\$|€|¥|￥|£)"
_NOT_TXN = re.compile(r"결제\s*예정|청구\s*(금액|서)|명세서|납부|자동\s*이체|이용\s*대금|출금\s*예정|이벤트|광고|혜택\s*안내")
# 거래처를 찾을 때 지우는 말
_NOISE = [
    r"\[?(신한|현대)\s*카드\]?(\s*[A-Za-z0-9]{1,8})?(\s*\(\s*[\d*]{3,4}\s*\))?",
    r"\(\s*[\d*]{3,4}\s*\)", r"SOL\s*페이|신한\s*SOL|현대카드\s*앱",
    r"(해외|체크|신용)?\s*(승인\s*취소|승인|취소|결제\s*완료|결제|사용|이용|환불)(\s*(되었습니다|됐어요|완료|알림|안내|내역))?",
    r"[가-힣A-Za-z][*＊]+[가-힣A-Za-z]*\s*님?", r"[가-힣]{2,4}\s*님",
    r"(누적|잔액|한도|포인트|적립)[^\n]*",
    rf"{_CCY}\s*{_NUM}", rf"{_NUM}\s*(원|USD|달러|엔|유로|위안)",
    r"일시불|\d{1,2}\s*개월|할부|무이자",
    r"\d{1,2}\s*[/월.]\s*\d{1,2}\s*일?", r"\d{1,2}\s*:\s*\d{2}(\s*:\s*\d{2})?", r"\d{1,2}\s*시\s*\d{1,2}\s*분",
]


# ── 읽기 규칙 ───────────────────────────────────────────────

def _to_float(whole: str, frac: str | None) -> float:
    return float(whole.replace(",", "") + (f".{frac}" if frac else ""))


def _amount(text: str) -> tuple[float | None, str]:
    """결제(취소) 금액과 통화. 원화를 먼저 찾고, 없으면 외화."""
    for m in re.finditer(rf"{_NUM}\s*원", text):
        if _NOT_AMOUNT.search(text[max(0, m.start() - 12):m.start()]):
            continue
        return _to_float(m.group(1), m.group(2)), "KRW"
    for pat, sym_first in ((rf"{_CCY}\s*{_NUM}", True), (rf"{_NUM}\s*(USD|EUR|JPY|GBP|CNY|달러|유로|엔|위안)", False)):
        for m in re.finditer(pat, text):
            if _NOT_AMOUNT.search(text[max(0, m.start() - 12):m.start()]):
                continue
            if sym_first:
                return _to_float(m.group(2), m.group(3)), _FOREIGN[m.group(1)]
            return _to_float(m.group(1), m.group(2)), _FOREIGN[m.group(3)]
    return None, "KRW"


def _when(text: str, received: datetime) -> tuple[datetime, re.Match | None]:
    """결제 일시(한국 시간)와, 일시가 적힌 부분(거래처 찾기에 씀)."""
    recv = received.astimezone(KST)
    pats = [
        r"(?P<y>20\d{2})\s*[./-]\s*(?P<mo>\d{1,2})\s*[./-]\s*(?P<d>\d{1,2})\.?\s*(?:\([^)]*\))?\s*(?P<h>\d{1,2})\s*:\s*(?P<mi>\d{2})",
        r"(?P<y>20\d{2})\s*년\s*(?P<mo>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:\([^)]*\))?\s*(?P<h>\d{1,2})\s*[:시]\s*(?P<mi>\d{2})분?",
        r"(?<![\d.])(?P<mo>\d{1,2})\s*[/.]\s*(?P<d>\d{1,2})\s*(?:\([^)]*\))?\s*(?P<h>\d{1,2})\s*:\s*(?P<mi>\d{2})",
        r"(?P<mo>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일\s*(?:\([^)]*\))?\s*(?P<h>\d{1,2})\s*[:시]\s*(?P<mi>\d{2})분?",
        r"(?<![\d,.])(?P<h>[0-2]?\d)\s*:\s*(?P<mi>[0-5]\d)(?![\d])",
    ]
    for p in pats:
        m = re.search(p, text)
        if not m:
            continue
        g = m.groupdict()
        try:
            hh, mm = int(g["h"]), int(g["mi"])
            if g.get("mo"):
                y = int(g["y"]) if g.get("y") else recv.year
                dt = datetime(y, int(g["mo"]), int(g["d"]), hh, mm, tzinfo=KST)
                if not g.get("y") and dt > recv + timedelta(days=1):    # 1월에 받은 12월 결제
                    dt = dt.replace(year=y - 1)
            else:
                dt = datetime(recv.year, recv.month, recv.day, hh, mm, tzinfo=KST)
                if dt > recv + timedelta(minutes=5):                     # 자정 넘어 받은 어제 결제
                    dt -= timedelta(days=1)
            return dt, m
        except ValueError:
            continue
    return recv.replace(second=0, microsecond=0), None


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" \t|·,:：/-_[]<>\"'")
    s = re.sub(r"^\)+|\(+$", "", s).strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    return s[:60]


def _strip_noise(s: str) -> str:
    for p in _NOISE:
        s = re.sub(p, " ", s)
    return _clean(s)


def _merchant(text: str, when: re.Match | None) -> str | None:
    m = re.search(r"(?:가맹점(?:명)?|사용처|거래처|이용\s*가맹점)\s*[:：]?\s*([^\n]+)", text)
    if m and _strip_noise(m.group(1)):
        return _clean(re.split(r"\s{2,}|누적|잔액", m.group(1))[0])
    m = re.search(rf"([^\n]+?)\s*에서\s*(?:{_CCY}\s*)?(?:{_NUM}\s*(?:원|USD|달러)?\s*)?(?:결제|승인|사용|이용|취소)", text)
    if m and _strip_noise(m.group(1)):
        return _strip_noise(m.group(1))
    if when is not None:
        rest = text[when.end():]
        for line in rest.split("\n"):
            cand = _clean(re.split(r"누적|잔액|한도", line)[0])
            if cand and _strip_noise(cand):
                return cand
    parts = [_strip_noise(p) for p in re.split(r"\n|\s{2,}|\|", text)]
    parts = [p for p in parts if len(p) >= 2 and re.search(r"[가-힣A-Za-z]", p)]
    return parts[-1] if parts else None


def parse(title: str | None, body: str | None, received: datetime) -> dict:
    """알림 한 건에서 결제 정보를 읽는다. 결제·취소 알림이 아니면 kind가 None."""
    text = "\n".join(x for x in (title or "", body or "") if x).replace("\r", "")
    amount, ccy = _amount(text)
    cancel = bool(re.search(r"취소|환불", text))
    kind = None
    if amount is not None:
        if cancel:
            kind = "cancel"
        elif not (_NOT_TXN.search(text) and not re.search(r"승인", text)):   # 청구·결제예정 안내는 빼기
            kind = "pay"
    when, wm = _when(text, received)
    inst = None
    m = re.search(r"일시불|(\d{1,2})\s*개월", text)
    if m:
        inst = "일시불" if m.group(0) == "일시불" else f"{int(m.group(1))}개월"
    return {"kind": kind, "spent_at": when, "merchant": _merchant(text, wm) if kind else None,
            "amount": amount if kind else None, "currency": ccy, "installment": inst}


# ── 저장 ────────────────────────────────────────────────────

def _table():
    return get_client().table("card_expenses")


def to_kst(value) -> datetime | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(KST).to_pydatetime()


def _row_update(row_id: int, payload: dict) -> None:
    out = {k: (v.isoformat() if isinstance(v, (datetime, date)) else v) for k, v in payload.items()}
    _table().update(out).eq("id", row_id).execute()


def load() -> pd.DataFrame:
    """전체 내역(최근 2,000건). 새 알림이나 규칙이 바뀐 줄은 읽어서 저장까지 한다."""
    rows = _table().select("*").order("received_at", desc=True).limit(2000).execute().data
    for r in rows:
        if (r.get("parse_version") or 0) < PARSER_VERSION and not r.get("edited") and not r.get("checked"):
            got = parse(r.get("title"), r.get("body"), to_kst(r.get("received_at")) or datetime.now(KST))
            got["parse_version"] = PARSER_VERSION
            _row_update(int(r["id"]), got)
            r.update({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in got.items()})
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["when"] = [to_kst(s) or to_kst(r) for s, r in zip(df["spent_at"], df["received_at"])]
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["checked"] = df["checked"].fillna(False).astype(bool)
    return df.sort_values(["when", "id"], ascending=False, kind="stable").reset_index(drop=True)


def set_checked(row_id: int, checked: bool) -> None:
    _row_update(row_id, {"checked": bool(checked),
                         "checked_at": datetime.now(timezone.utc) if checked else None})


def update(row_id: int, changes: dict) -> None:
    """사람이 고친 값 저장. 결제 정보를 고치면 다시 읽지 않도록 표시한다."""
    payload = dict(changes)
    if "checked" in payload:
        payload["checked_at"] = datetime.now(timezone.utc) if payload["checked"] else None
    if any(k in PARSED for k in payload):
        payload["edited"] = True
    _row_update(row_id, payload)


def delete(row_id: int) -> None:
    _table().delete().eq("id", row_id).execute()


def add_manual(spent_at: datetime, card: str, kind: str, merchant: str, amount: float, memo: str = "") -> None:
    """알림이 안 온 결제를 손으로 넣는다."""
    _table().insert({"card": card, "title": "직접 입력", "body": f"{merchant} {amount:,.0f}원",
                     "kind": kind, "spent_at": spent_at.isoformat(), "merchant": merchant, "amount": amount,
                     "currency": "KRW", "parse_version": PARSER_VERSION, "edited": True,
                     "memo": memo or None}).execute()


def apply_edits(edited_rows: dict, ids: list[int]) -> int:
    """st.data_editor의 edited_rows({행 번호: {열: 값}})를 DB에 반영한다. 반영한 줄 수."""
    col = {"확인": "checked", "일시": "spent_at", "구분": "kind", "거래처": "merchant", "금액": "amount", "메모": "memo"}
    inv_kind = {v: k for k, v in KINDS.items()}
    n = 0
    for pos, change in edited_rows.items():
        pos = int(pos)
        if pos >= len(ids):
            continue
        payload = {}
        for label, v in change.items():
            key = col.get(label)
            if not key:
                continue
            if key == "kind":
                v = inv_kind.get(v)
                if v is None:
                    continue
            elif key == "spent_at":
                ts = pd.Timestamp(v) if v not in (None, "") else None
                if ts is None or pd.isna(ts):
                    continue
                v = (ts.tz_localize(KST) if ts.tzinfo is None else ts).to_pydatetime()
            elif key == "amount":
                v = None if v is None or pd.isna(v) else abs(float(v))
            elif key in ("merchant", "memo"):
                v = (v or "").strip() or None
            elif key == "checked":
                v = bool(v)
            payload[key] = v
        if payload:
            update(int(ids[pos]), payload)
            n += 1
    return n


def month_summary(df: pd.DataFrame) -> dict:
    """원화 결제 합계·취소 합계·순지출·확인 안 한 건수."""
    if df.empty:
        return {"pay": 0.0, "cancel": 0.0, "net": 0.0, "unchecked": 0, "count": 0}
    krw = df[df["currency"].fillna("KRW") == "KRW"]
    pay = float(krw.loc[krw["kind"] == "pay", "amount"].sum())
    cancel = float(krw.loc[krw["kind"] == "cancel", "amount"].sum())
    return {"pay": pay, "cancel": cancel, "net": pay - cancel, "unchecked": int((~df["checked"]).sum()),
            "count": len(df)}
