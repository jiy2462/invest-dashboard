"""알림 대기함: 아이폰 단축어가 보낸 체결·배당 알림을 읽어 거래 입력값을 미리 채운다.

알림은 Supabase Edge Function `trade-inbox`가 trade_inbox 표에 원문 그대로 쌓는다.
읽기 규칙(parse)은 회사별 알림 형식을 모르는 상태에서 흔한 표현을 넓게 잡는 1차 버전이라,
화면에서 사람이 확인·수정한 뒤 [거래내역에 넣기]를 눌러야 거래로 들어간다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from lib.db import get_client

KST = timezone(timedelta(hours=9))
SOURCES = {"upbit": "업비트", "kis": "한국투자증권", "meritz": "메리츠증권", "nh": "NH투자증권", "other": "기타"}
COLUMNS = ["id", "received_at", "source", "title", "body", "status", "transaction_id", "handled_at", "raw"]

_NUM = r"([\d,]+(?:\.\d+)?)"


# ── 저장 ────────────────────────────────────────────────────

def list_items(status: str | None = "pending") -> pd.DataFrame:
    q = get_client().table("trade_inbox").select("*")
    if status:
        q = q.eq("status", status)
    rows = q.order("received_at", desc=True).limit(200).execute().data
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.sort_values("received_at", ascending=False, kind="stable") if not df.empty else df


def status_label(status: str, raw) -> str:
    """처리한 알림 표의 상태: 서버가 체결·배당 말을 못 찾아 자동으로 걸러 둔 것은 따로 표시."""
    if status == "ignored" and isinstance(raw, dict) and raw.get("skip_reason"):
        return "자동 거름"
    return {"done": "입력함", "ignored": "무시함", "pending": "대기"}.get(status, status)


def mark(item_id: int, status: str, transaction_id: int | None = None) -> None:
    get_client().table("trade_inbox").update({
        "status": status, "transaction_id": transaction_id,
        "handled_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", item_id).execute()


# ── 읽기 규칙 ───────────────────────────────────────────────

def received_kst(value) -> datetime | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(KST).to_pydatetime()


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _first(patterns: list[str], text: str) -> float | None:
    for p in patterns:
        m = re.search(p, text)
        if m:
            v = _num(m.group(1))
            if v is not None:
                return v
    return None


def _side(text: str) -> str | None:
    if re.search(r"배당|분배금", text):
        return "dividend"
    if re.search(r"매도|\bask\b|\bsell\b", text, re.I):
        return "sell"
    if re.search(r"매수|\bbid\b|\bbuy\b", text, re.I):
        return "buy"
    return None


def _date(text: str, fallback: date) -> date:
    m = re.search(r"(20\d{2})\s*[./년-]\s*(\d{1,2})\s*[./월-]\s*(\d{1,2})", text)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(?<![\d.])(\d{1,2})\s*[/월]\s*(\d{1,2})\s*일?(?![\d.])", text)
        if m:
            d = date(fallback.year, int(m.group(1)), int(m.group(2)))
            return d if d <= fallback else date(fallback.year - 1, d.month, d.day)
    except ValueError:
        pass
    return fallback


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()


def match_symbol(text: str, assets: pd.DataFrame) -> str | None:
    """알림 글에서 이미 등록된 종목을 찾는다: 이름(긴 것부터) → 티커 → 6자리 종목코드 → 코인 기호."""
    if assets.empty:
        return None
    flat = _norm(text)
    named = sorted(((str(n), s) for s, n in zip(assets["symbol"], assets["name"]) if isinstance(n, str) and n),
                   key=lambda x: -len(x[0]))
    for name, sym in named:
        if len(_norm(name)) >= 3 and _norm(name) in flat:
            return sym
    words = set(re.findall(r"[A-Z][A-Z0-9.\-]{1,9}", text.upper()))
    codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", text))
    for sym in assets["symbol"]:
        s = str(sym)
        base = s.split(".")[0]
        if s.upper() in words or (re.fullmatch(r"\d{6}", base) and base in codes):
            return s
        if s.endswith("-KRW") and s[:-4] in words:        # 업비트: BTC → BTC-KRW
            return s
    return None


def parse(title: str | None, body: str, source: str, assets: pd.DataFrame, received: datetime | None) -> dict:
    """알림 한 건에서 거래 입력값을 추측한다. 못 찾은 값은 None."""
    text = f"{title or ''}\n{body or ''}"
    side = _side(text)
    sym = match_symbol(text, assets)
    info = assets.set_index("symbol").to_dict("index") if not assets.empty else {}
    ccy = (info.get(sym) or {}).get("currency") if sym else None
    if not ccy:
        ccy = "USD" if re.search(r"\$|USD|달러", text) else "KRW"
    qty = _first([rf"체결\s*수량\s*[:：]?\s*{_NUM}", rf"수량\s*[:：]?\s*{_NUM}", rf"{_NUM}\s*주(?![가-힣])",
                  rf"{_NUM}\s*(?:{re.escape(sym.split('-')[0]) if sym else 'BTC'})\b"], text)
    price = _first([rf"(?:체결\s*)?(?:단가|체결가|가격|평균가|평균\s*체결가)\s*[:：]?\s*(?:USD|\$)?\s*{_NUM}",
                    rf"@\s*(?:USD|\$)?\s*{_NUM}"], text)
    fee = _first([rf"(?:수수료|제세금|세금)\s*[:：]?\s*(?:USD|\$)?\s*{_NUM}"], text) or 0.0
    if side == "dividend":
        amount = _first([rf"(?:배당금|분배금|입금액|지급액|세후|금액)\s*[:：]?\s*(?:USD|\$)?\s*{_NUM}",
                         rf"(?:USD|\$)\s*{_NUM}", rf"{_NUM}\s*(?:원|달러|USD)"], text)
        qty, price = 0.0, amount
    if price is None and side in ("buy", "sell") and qty:
        total = _first([rf"(?:체결\s*)?(?:금액|총액|거래대금)\s*[:：]?\s*(?:USD|\$)?\s*{_NUM}"], text)
        price = total / qty if total else None
    today = (received or datetime.now(KST)).date()
    return {"side": side, "symbol": sym, "currency": ccy, "quantity": qty, "price": price, "fee": fee,
            "trade_date": _date(text, today), "source": source}


def account_for(source: str, accounts: pd.DataFrame):
    """알림 보낸 회사와 증권사 이름이 같은 첫 계좌."""
    broker = SOURCES.get(source)
    if not broker or accounts.empty or source == "other":
        return None
    hit = accounts[accounts["broker"].fillna("").map(_norm) == _norm(broker)]
    if hit.empty:
        hit = accounts[accounts["name"].fillna("").map(_norm).str.contains(_norm(broker)[:2], regex=False)]
    return None if hit.empty else hit["id"].iloc[0]
