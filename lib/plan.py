"""적립 계획(매주 적립액), 투자유보현금 나눠 넣기, 정기 리밸런싱 날짜 계산.

적립 계획은 app_settings 의 'dca_plan' 하나에 저장한다(표를 새로 만들지 않는다):
  {months, weekday(0=월), monthly_deposit, lump_sum, start("YYYY-MM-DD"), cash_classes([자산군 이름])}
계산 함수는 DB·네트워크 없이 pandas만으로 동작한다.

생각 방식
- 매주 쓸 돈(합계) = (월 입금액 × 개월 수 + 목돈) ÷ 계획 기간의 전체 매수 횟수. 기간 내내 같다.
- 남은 적립금 = 매주 합계 × 남은 매수 횟수. 지금 평가액에 이 돈을 더한 총액이 목표 비중이 되도록
  부족한 자산군에 나눈다(매도 없음). 시세가 바뀌면 자산군별 금액만 다시 나뉜다.
- 투자유보현금도 다른 자산군과 함께 비중을 계산한다. 다만 현금 자산군(cash_classes)이 목표일 총액 기준
  목표 금액보다 많으면(목표 비중 0%면 전부) 그 초과분을 목표 기간 동안 매수일마다 똑같이 꺼내
  부족한 자산군에 넣는다(월 입금액처럼 균등 분할 투입).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd

from lib import portfolio as pf

PLAN_KEY = "dca_plan"
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
REBALANCE_MONTHS = (2, 8)     # 매년 2월·8월
REBALANCE_WEEKDAY = 0         # 마지막 주 월요일


# ── 적립 계획 저장 ──────────────────────────────────────────

def default_plan(targets: pd.DataFrame | None = None) -> dict:
    """저장한 계획이 없을 때의 기본값. 월 입금액은 예전 자산군별 '월 적립 목표'의 합(유보현금 제외)."""
    monthly = 0.0
    if targets is not None and not targets.empty:
        inv = targets[targets["asset_class"] != pf.CASH_CLASS]
        monthly = float(pd.to_numeric(inv["monthly_contribution"], errors="coerce").fillna(0).sum())
    return {"months": 6, "weekday": 0, "monthly_deposit": monthly, "lump_sum": 0.0, "start": None,
            "cash_classes": [pf.CASH_CLASS]}


def clean_plan(raw: dict | None, targets: pd.DataFrame | None = None) -> dict:
    plan = default_plan(targets)
    if isinstance(raw, dict):
        plan.update({k: raw[k] for k in plan if k in raw and raw[k] is not None})
    plan["months"] = max(1, int(pf._num(plan["months"]) or 1))
    plan["weekday"] = int(pf._num(plan["weekday"])) % 7
    plan["monthly_deposit"] = max(0.0, pf._num(plan["monthly_deposit"]))
    plan["lump_sum"] = max(0.0, pf._num(plan["lump_sum"]))
    cc = plan["cash_classes"]
    plan["cash_classes"] = [str(c) for c in (cc if isinstance(cc, list) else [])]
    try:
        plan["start"] = date.fromisoformat(str(plan["start"])[:10]) if plan["start"] else None
    except ValueError:
        plan["start"] = None
    return plan


def load_plan(targets: pd.DataFrame | None = None) -> dict:
    return clean_plan(pf.get_setting(PLAN_KEY), targets)


def save_plan(plan: dict, today: date) -> None:
    """계획을 저장한다. 저장한 날(today)부터 months 개월 계획이 새로 시작된다."""
    p = clean_plan(plan)
    p["start"] = today.isoformat()
    pf.set_setting(PLAN_KEY, p)


# ── 날짜 ────────────────────────────────────────────────────

def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def buy_dates(start: date, end: date, weekday: int) -> list[date]:
    """start~end(양 끝 포함) 사이의 매수 요일 날짜."""
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def schedule(plan: dict, today: date, bought_today: bool = False) -> dict:
    """계획 기간과 남은 매수일·남은 적립금.

    bought_today: 오늘이 매수일이고 이미 매수를 기록했으면 True — 오늘 회차는 끝난 것으로 본다.
    """
    start = plan.get("start") or today
    end = add_months(start, int(plan["months"]))
    all_dates = buy_dates(start, end, int(plan["weekday"]))
    remaining = [d for d in all_dates if d > today or (d == today and not bought_today)]
    total_money = float(plan["monthly_deposit"]) * int(plan["months"]) + float(plan["lump_sum"])
    per_buy = total_money / len(all_dates) if all_dates else 0.0
    done = len(all_dates) - len(remaining)
    return {"start": start, "end": end, "dates": all_dates, "remaining": remaining,
            "next": remaining[0] if remaining else None, "total_money": total_money, "per_buy": per_buy,
            "remaining_money": per_buy * len(remaining),
            "lump_left": float(plan["lump_sum"]) * len(remaining) / len(all_dates) if all_dates else 0.0,
            "done": done}


def last_weekday_of_month(year: int, month: int, weekday: int = REBALANCE_WEEKDAY) -> date:
    d = date(year, month, calendar.monthrange(year, month)[1])
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def rebalance_dates(today: date, count: int = 2, months=REBALANCE_MONTHS,
                    weekday: int = REBALANCE_WEEKDAY) -> list[date]:
    """오늘(포함) 이후의 정기 리밸런싱 날짜: 매년 2월·8월 마지막 주 월요일."""
    out, y = [], today.year
    while len(out) < count:
        for m in sorted(months):
            d = last_weekday_of_month(y, m, weekday)
            if d >= today and len(out) < count:
                out.append(d)
        y += 1
    return out


# ── 매주 적립액 ─────────────────────────────────────────────

def cash_excess(summary: pd.DataFrame, cash_classes, final_total: float) -> pd.Series:
    """현금 자산군마다 목표일 총액 기준 목표 금액을 넘는 금액(나눠 투입할 돈). 목표 0%면 전부."""
    w = pd.to_numeric(summary["target_weight"], errors="coerce").fillna(0).clip(lower=0)
    w = w / w.sum() if w.sum() > 0 else w * 0
    excess = (summary["value_krw"] - final_total * w).clip(lower=0)
    return excess.where(summary["asset_class"].isin(list(cash_classes or [])), 0.0)


def weekly_plan(summary: pd.DataFrame, sched: dict, cash_classes=()) -> pd.DataFrame:
    """자산군별 매주 적립액. 현금 자산군의 초과분은 남은 매수일에 똑같이 나눠 꺼낸다(weekly 가 −).

    열: asset_class, value_krw, weight, target_weight, total_buy(남은 기간 합계, −는 꺼내 쓸 돈),
        weekly(매주), cash_out(그중 현금에서 꺼내는 몫), end_value, end_weight(목표일 예상, 시세 그대로)
    """
    n = len(sched["remaining"])
    new = float(sched["remaining_money"]) if n else 0.0
    total = float(summary["value_krw"].sum())
    excess = cash_excess(summary, cash_classes, total + new) if n else summary["value_krw"] * 0.0
    adj = summary.copy()
    adj["value_krw"] = adj["value_krw"] - excess          # 꺼낼 돈은 미리 빼고, 넣을 돈에 더한다
    r = pf.rebalance(adj, new + float(excess.sum()), allow_sell=False)
    trade = r["trade_krw"] - excess.values
    end = summary["value_krw"].values + trade
    end_total = float(end.sum())
    return pd.DataFrame({
        "asset_class": summary["asset_class"].values, "value_krw": summary["value_krw"].values,
        "weight": summary["value_krw"].values / total * 100 if total else 0.0,
        "target_weight": r["target_weight_norm"].values, "total_buy": trade,
        "weekly": trade / n if n else 0.0, "cash_out": excess.values / n if n else 0.0,
        "end_value": end, "end_weight": end / end_total * 100 if end_total else 0.0,
    })


def project(summary: pd.DataFrame, weekly: pd.DataFrame, sched: dict, until: date) -> pd.DataFrame:
    """until 전날까지 계획대로 적립했다고 보고(시세 그대로) 자산군 금액을 바꾼 요약."""
    k = sum(1 for d in sched["remaining"] if d < until)
    add = weekly.set_index("asset_class")["weekly"] * k
    out = summary.copy()
    out["value_krw"] = out["value_krw"] + out["asset_class"].map(add).fillna(0.0)
    total = float(out["value_krw"].sum())
    out["weight"] = out["value_krw"] / total * 100 if total else 0.0
    return out


# ── 종목별로 나누기 ─────────────────────────────────────────

def symbol_trades(trades: pd.DataFrame, valued: pd.DataFrame, asset_class: dict[str, str]) -> pd.DataFrame:
    """자산군별 매수(+)/매도(-) 금액을 그 자산군의 보유 종목에 평가금액 비율대로 나눈다(참고용).

    열: asset_class, symbol(없으면 None), currency, unit_krw(1주 원화 가격), trade_krw, quantity(대략 수량)
    """
    held = valued[valued["quantity"] > 0] if not valued.empty else valued
    cls_of = held["symbol"].map(lambda s: asset_class.get(s) or pf.UNCLASSIFIED) if not held.empty else None
    rows = []
    for cls, amt in zip(trades["asset_class"], trades["trade_krw"]):
        if abs(amt) < 0.5:
            continue
        h = held[cls_of == cls] if cls_of is not None else held
        tot = float(h["value_krw"].sum()) if not h.empty else 0.0
        if tot <= 0:
            rows.append({"asset_class": cls, "symbol": None, "currency": None, "unit_krw": np.nan,
                         "trade_krw": float(amt), "quantity": np.nan})
            continue
        for _, p in h.iterrows():
            unit = float(p["value_krw"]) / float(p["quantity"])
            t = float(amt) * float(p["value_krw"]) / tot
            rows.append({"asset_class": cls, "symbol": p["symbol"], "currency": p.get("currency"),
                         "unit_krw": unit, "trade_krw": t, "quantity": t / unit if unit else np.nan})
    return pd.DataFrame(rows, columns=["asset_class", "symbol", "currency", "unit_krw", "trade_krw", "quantity"])
