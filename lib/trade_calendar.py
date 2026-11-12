"""거래 내역 월간 캘린더: 날짜별 매수(빨강)·매도(파랑)·배당(노랑) 칸.

화면(views/portfolio.py)은 month_items()로 그 달의 칸 내용을 만들고 calendar_html()로 그린다.
"""
from __future__ import annotations

import calendar as _cal
import html
import re
from datetime import date
from typing import Callable

import pandas as pd

from lib import ui

SIDE_ORDER = {"buy": 0, "sell": 1, "dividend": 2}
WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"]   # 한국 달력처럼 일요일부터
SYMBOL_PREFIX = {"KRW": "₩", "USD": "$", "JPY": "¥", "EUR": "€", "CNY": "¥", "HKD": "HK$"}
_US_TICKER = re.compile(r"^[A-Z]{1,5}$")


def shift_month(ym: tuple[int, int], n: int) -> tuple[int, int]:
    """(연, 월)에서 n달 앞(+)이나 뒤(-)."""
    k = ym[0] * 12 + (ym[1] - 1) + n
    return k // 12, k % 12 + 1


_BRANDS = re.compile(r"^(TIGER|KODEX|ACE|RISE|KBSTAR|SOL|HANARO|ARIRANG|PLUS|KOSEF|KIWOOM|TIMEFOLIO|KRX)\s+",
                     re.IGNORECASE)


def short_label(symbol: str, name: str) -> str:
    """칸이 좁으니 미국 티커는 티커(QQQM), 나머지는 운용사 이름을 뗀 이름(미국S&P500, 금현물, 비트코인)."""
    if _US_TICKER.match(symbol or ""):
        return symbol
    full = (name or symbol or "").replace("MANUAL:", "")
    return _BRANDS.sub("", full).strip() or full


def unit_of(symbol: str, name: str) -> str:
    s = f"{symbol} {name}"
    if "금현물" in s:
        return "g"
    if re.search(r"-(KRW|USD)$", symbol or ""):      # 비트코인 등 코인
        return "개"
    return "주"


def fmt_qty(q: float) -> str:
    return f"{q:,.0f}" if abs(q - round(q)) < 1e-9 else f"{q:,.8f}".rstrip("0").rstrip(".")


def fmt_money(v: float, ccy: str) -> str:
    sign = SYMBOL_PREFIX.get(ccy, "")
    body = f"{v:,.0f}" if ccy in ("KRW", "JPY") else f"{v:,.2f}"
    return f"{sign}{body}" if sign else f"{body} {ccy}"


def month_items(tx: pd.DataFrame, year: int, month: int, name_of: Callable[[str], str]) -> dict[int, list[dict]]:
    """그 달 거래를 날짜별로 묶는다. 같은 날 같은 종목·구분은 합친다.

    반환: {일: [{side, label, name, text}]} — 매수 → 매도 → 배당 순.
    매수·매도는 수량(주), 배당은 받은 금액(배당 총액 - 세금, 종목 통화).
    """
    if tx is None or tx.empty:
        return {}
    d = pd.to_datetime(tx["trade_date"])
    m = tx[(d.dt.year == year) & (d.dt.month == month)].copy()
    if m.empty:
        return {}
    m["day"] = pd.to_datetime(m["trade_date"]).dt.day
    m["amount"] = m["price"] - m["fee"]
    m["currency"] = m["currency"].fillna("KRW")
    g = m.groupby(["day", "side", "symbol", "currency"], as_index=False).agg(
        quantity=("quantity", "sum"), amount=("amount", "sum"), first_id=("id", "min"))
    g["order"] = g["side"].map(SIDE_ORDER).fillna(9)
    g = g.sort_values(["day", "order", "first_id"], kind="stable")
    out: dict[int, list[dict]] = {}
    for r in g.itertuples(index=False):
        name = name_of(r.symbol)
        if r.side == "dividend":
            text = fmt_money(float(r.amount), r.currency)
        else:
            text = f"{fmt_qty(float(r.quantity))}{unit_of(r.symbol, name)}"
        out.setdefault(int(r.day), []).append(
            dict(side=r.side, label=short_label(r.symbol, name), name=name, text=text))
    return out


def month_summary(items: dict[int, list[dict]], tx: pd.DataFrame, year: int, month: int) -> str:
    """'매수 12건 · 매도 2건 · 배당 ₩12,000 + $3.10' 같은 한 줄."""
    if tx is None or tx.empty:
        return ""
    d = pd.to_datetime(tx["trade_date"])
    m = tx[(d.dt.year == year) & (d.dt.month == month)]
    parts = []
    for side, word in (("buy", "매수"), ("sell", "매도")):
        n = int((m["side"] == side).sum())
        if n:
            parts.append(f"{word} {n}건")
    dv = m[m["side"] == "dividend"]
    if not dv.empty:
        by = (dv["price"] - dv["fee"]).groupby(dv["currency"].fillna("KRW")).sum()
        order = sorted(by.index, key=lambda c: (c != "KRW", c))
        parts.append("배당 " + " + ".join(fmt_money(float(by[c]), c) for c in order))
    return " · ".join(parts)


def _css() -> str:
    t = {k: ui.tok(k) for k in ("card", "fill", "hairline", "ink", "muted")}
    return f"""
<style>
.tcal {{ width: 100%; }}
.tcal-grid {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }}
.tcal-wd {{ text-align: center; font-size: 0.74rem; font-weight: 600; color: {t['muted']}; padding: 2px 0 4px; }}
.tcal-day {{ min-height: 5.4rem; border-radius: 10px; background: {t['fill']}; padding: 4px 4px 5px;
             display: flex; flex-direction: column; gap: 3px; min-width: 0; }}
.tcal-day.tcal-out {{ background: transparent; }}
.tcal-day.tcal-today {{ box-shadow: inset 0 0 0 2px {ui.BLUE}; }}
.tcal-num {{ font-size: 0.74rem; font-weight: 600; color: {t['ink']}; padding-left: 2px;
             font-variant-numeric: tabular-nums; }}
.tcal-day.tcal-weekend .tcal-num {{ color: {t['muted']}; }}
.tcal-chip {{ display: flex; gap: 4px; align-items: baseline; border-radius: 6px; padding: 2px 5px;
              font-size: 0.72rem; line-height: 1.25; font-weight: 600; min-width: 0; }}
.tcal-chip .tcal-n {{ flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.tcal-chip .tcal-q {{ flex: 0 0 auto; white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 500; }}
.tcal-buy {{ background: {ui.RED}; color: #FFFFFF; }}
.tcal-sell {{ background: {ui.BLUE}; color: #FFFFFF; }}
.tcal-dividend {{ background: #FFCC00; color: #1D1D1F; }}
.tcal-legend {{ display: flex; gap: 12px; flex-wrap: wrap; font-size: 0.78rem; color: {t['muted']}; margin-top: 8px; }}
.tcal-legend span::before {{ content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 3px;
                             margin-right: 5px; vertical-align: -1px; background: var(--c); }}
@media (max-width: 640px) {{
  .tcal-grid {{ gap: 2px; }}
  .tcal-day {{ min-height: 3.6rem; padding: 3px 2px 4px; border-radius: 7px; gap: 2px; }}
  .tcal-num {{ font-size: 0.68rem; }}
  .tcal-chip {{ flex-direction: column; gap: 0; padding: 1px 3px; border-radius: 4px; font-size: 0.6rem; }}
  .tcal-chip .tcal-n {{ width: 100%; white-space: normal; word-break: break-all; display: -webkit-box;
                        -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
  .tcal-chip .tcal-q {{ width: 100%; white-space: normal; word-break: break-all; }}
}}
</style>
"""


def calendar_html(year: int, month: int, items: dict[int, list[dict]], today: date | None = None) -> str:
    """일요일부터 시작하는 7칸 달력 HTML. 다른 달 칸은 빈칸."""
    cal = _cal.Calendar(firstweekday=6)
    cells = [f"<div class='tcal-wd'>{w}</div>" for w in WEEKDAYS]
    for i, d in enumerate(cal.itermonthdates(year, month)):
        if d.month != month:
            cells.append("<div class='tcal-day tcal-out'></div>")
            continue
        cls = ["tcal-day"]
        if i % 7 in (0, 6):
            cls.append("tcal-weekend")
        if today and d == today:
            cls.append("tcal-today")
        chips = []
        for it in items.get(d.day, []):
            title = html.escape(f"{d.month}/{d.day} {it['name']} {it['text']}", quote=True)
            chips.append(f"<div class='tcal-chip tcal-{it['side']}' title=\"{title}\">"
                         f"<span class='tcal-n'>{html.escape(it['label'])}</span>"
                         f"<span class='tcal-q'>{html.escape(it['text'])}</span></div>")
        cells.append(f"<div class='{' '.join(cls)}'><div class='tcal-num'>{d.day}</div>{''.join(chips)}</div>")
    legend = ("<div class='tcal-legend'>"
              f"<span style='--c:{ui.RED}'>매수 (주식수)</span>"
              f"<span style='--c:{ui.BLUE}'>매도 (주식수)</span>"
              "<span style='--c:#FFCC00'>배당 (받은 금액)</span></div>")
    return f"{_css()}<div class='tcal'><div class='tcal-grid'>{''.join(cells)}</div>{legend}</div>"


def month_list(items: dict[int, list[dict]], year: int, month: int) -> pd.DataFrame:
    """캘린더 칸을 표로: 날짜·구분·종목(전체 이름)·주식수/금액. 휴대폰에서 잘린 이름을 확인할 때 쓴다."""
    names = {"buy": "매수", "sell": "매도", "dividend": "배당"}
    rows = [{"날짜": f"{month}/{d} ({WEEKDAYS[(date(year, month, d).weekday() + 1) % 7]})", "구분": names.get(it["side"], it["side"]),
             "종목": it["name"], "주식수/금액": it["text"]}
            for d in sorted(items) for it in items[d]]
    return pd.DataFrame(rows, columns=["날짜", "구분", "종목", "주식수/금액"])
