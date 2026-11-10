"""적립 계획(매주 적립액), 투자유보현금 분리, 정기 리밸런싱 날짜 계산.

적립 계획은 app_settings 의 'dca_plan' 하나에 저장한다(표를 새로 만들지 않는다):
  {months, weekday(0=월), monthly_deposit, lump_sum, start("YYYY-MM-DD"), reserve([자산군 이름])}
계산 함수는 DB·네트워크 없이 pandas만으로 동작한다.

생각 방식
- 매주 쓸 돈(합계) = (월 입금액 × 개월 수 + 목돈) ÷ 계획 기간의 전체 매수 횟수. 기간 내내 같다.
- 남은 적립금 = 매주 합계 × 남은 매수 횟수. 지금 평가액에 이 돈을 더한 총액이 목표 비중이 되도록
  부족한 자산군에 나눈다(매도 없음). 시세가 바뀌면 자산군별 금액만 다시 나뉜다.
- 투자유보현금(reserve)은 월 입금액·목돈과 별개인 돈이라 비중의 분모와 적립 대상에서 뺀다.
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
            "reserve": [pf.CASH_CLASS]}


def clean_plan(raw: dict | None, targets: pd.DataFrame | None = None) -> dict:
    plan = default_plan(targets)
    if isinstance(raw, dict):
        plan.update({k: raw[k] for k in plan if k in raw and raw[k] is not None})
    plan["months"] = max(1, int(pf._num(plan["months"]) or 1))
    plan["weekday"] = int(pf._num(plan["weekday"])) % 7
    plan["monthly_deposit"] = max(0.0, pf._num(plan["monthly_deposit"]))
    plan["lump_sum"] = max(0.0, pf._num(plan["lump_sum"]))
    plan["reserve"] = [str(c) for c in (plan["reserve"] if isinstance(plan["reserve"], list) else [])]
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


# ── 투자 자산 / 투자유보현금 나누기 ─────────────────────────

def split_reserve(summary: pd.DataFrame, reserve) -> tuple[pd.DataFrame, pd.DataFrame]:
    """자산군 요약을 (투자 자산, 유보 자산)으로 나눈다.

    투자 자산의 현재 비중·목표 비중은 투자 자산끼리(유보현금 제외) 100% 기준으로 다시 계산한다.
    유보 자산의 weight 는 전체 자산 대비 비중 그대로 둔다.
    """
    is_res = summary["asset_class"].isin(list(reserve or []))
    inv, res = summary[~is_res].copy(), summary[is_res].copy()
    total = float(inv["value_krw"].sum())
    inv["weight"] = inv["value_krw"] / total * 100 if total else 0.0
    w = pd.to_numeric(inv["target_weight"], errors="coerce").fillna(0).clip(lower=0)
    inv["target_weight"] = w / w.sum() * 100 if w.sum() > 0 else 0.0
    inv["diff_pp"] = inv["weight"] - inv["target_weight"]
    return inv.reset_index(drop=True), res.reset_index(drop=True)


# ── 매주 적립액 ─────────────────────────────────────────────

def weekly_plan(invest: pd.DataFrame, sched: dict) -> pd.DataFrame:
    """자산군별 매주 적립액.

    열: asset_class, value_krw, weight, target_weight, total_buy(남은 기간 합계), weekly(매주),
        end_value, end_weight(목표일 예상, 시세가 그대로일 때)
    """
    n = len(sched["remaining"])
    r = pf.rebalance(invest, float(sched["remaining_money"]) if n else 0.0, allow_sell=False)
    return pd.DataFrame({
        "asset_class": r["asset_class"], "value_krw": r["value_krw"], "weight": r["weight"],
        "target_weight": r["target_weight_norm"], "total_buy": r["trade_krw"],
        "weekly": r["trade_krw"] / n if n else 0.0, "end_value": r["after_krw"], "end_weight": r["after_weight"],
    })


def project(invest: pd.DataFrame, weekly: pd.DataFrame, sched: dict, until: date) -> pd.DataFrame:
    """until 전날까지 계획대로 적립했다고 보고(시세 그대로) 자산군 금액을 더한 요약."""
    k = sum(1 for d in sched["remaining"] if d < until)
    add = weekly.set_index("asset_class")["weekly"] * k
    out = invest.copy()
    out["value_krw"] = out["value_krw"] + out["asset_class"].map(add).fillna(0.0)
    total = float(out["value_krw"].sum())
    out["weight"] = out["value_krw"] / total * 100 if total else 0.0
    return out


# ── 유보현금으로 매수(하락 시) ──────────────────────────────

def deploy(invest: pd.DataFrame, amount: float, classes=None) -> pd.DataFrame:
    """유보현금 amount 를 투자 자산에 넣을 때의 자산군별 매수 금액.

    classes 가 비면 목표보다 부족한 자산군에 부족한 만큼씩 나눈다. classes 를 고르면 그 자산군에만
    넣는다(부족분 비율대로, 부족분이 없으면 목표 비중 비율대로, 그것도 없으면 똑같이).
    돌려주는 열은 pf.rebalance 와 같다.
    """
    if not classes:
        return pf.rebalance(invest, amount, allow_sell=False)
    r = pf.rebalance(invest, amount, allow_sell=True)   # gap 계산용
    picked = r["asset_class"].isin(list(classes))
    key = r["trade_krw"].clip(lower=0).where(picked, 0.0)
    if key.sum() <= 0:
        key = r["target_weight_norm"].where(picked, 0.0)
    if key.sum() <= 0:
        key = picked.astype(float)
    r["trade_krw"] = key / key.sum() * amount if key.sum() > 0 else 0.0
    r["after_krw"] = r["value_krw"] + r["trade_krw"]
    after_total = float(r["after_krw"].sum())
    r["after_weight"] = r["after_krw"] / after_total * 100 if after_total else 0.0
    return r


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
