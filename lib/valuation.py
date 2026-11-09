"""포트폴리오 현재 시세·환율과 자산군 요약. 화면(views/portfolio.py)과 월말 자동 기록 스크립트가 함께 쓴다."""
from __future__ import annotations

from datetime import date

import pandas as pd

from lib import market
from lib import portfolio as pf

MANUAL = "MANUAL:"   # 티커 없는 자산(예: KRX 금현물)의 티커 앞머리


def fx_ticker(ccy: str) -> str:
    return "KRW=X" if ccy == "USD" else f"{ccy}KRW=X"


def held_symbols(tx_all: pd.DataFrame) -> set[str]:
    if tx_all.empty:
        return set()
    q = tx_all["quantity"].where(tx_all["side"] == "buy", -tx_all["quantity"].where(tx_all["side"] == "sell", 0))
    net = tx_all.assign(q=q).groupby("symbol")["q"].sum()
    return set(net[net > 1e-9].index)   # 다 판 종목은 시세를 받지 않는다


def quote_prices(tx_all: pd.DataFrame, cash_all: pd.DataFrame, asset_info: dict) -> tuple[dict, dict, dict]:
    """(환율, 종목별 현재가, 종목별 시세 출처)."""
    ccys = (set(tx_all["currency"].dropna()) | set(cash_all["currency"].dropna()) | {"USD"}) - {"KRW"}
    held = held_symbols(tx_all)
    quoted = sorted(s for s in held if not s.startswith(MANUAL))
    try:
        closes = market.fetch_yf_closes(tuple(sorted(set(quoted) | {fx_ticker(c) for c in ccys})))
    except Exception:
        closes = pd.DataFrame()
    last = {c: float(closes[c].dropna().iloc[-1]) for c in closes.columns if not closes[c].dropna().empty}
    fx = {c: last[fx_ticker(c)] for c in ccys if fx_ticker(c) in last}
    prices, src = {}, {}
    for s in held:
        manual = (asset_info.get(s) or {}).get("manual_price")
        auto = market.auto_price(s) if s.startswith(MANUAL) else None
        if s in last and not s.startswith(MANUAL):
            prices[s], src[s] = last[s], "시세"
        elif auto:
            prices[s], src[s] = auto, "시세(네이버)"
        elif manual is not None and not pd.isna(manual):
            prices[s], src[s] = float(manual), "직접 입력"
    return fx, prices, src


def class_map(assets: pd.DataFrame) -> dict[str, str]:
    info = assets.set_index("symbol").to_dict("index") if not assets.empty else {}
    return {s: (i["asset_class"] if pd.notna(i.get("asset_class")) and i.get("asset_class") else pf.UNCLASSIFIED)
            for s, i in info.items()}


def current_summary(today: date) -> pd.DataFrame:
    """전체 계좌 기준 자산군 요약을 DB와 시세로 새로 계산한다 (월말 기록용)."""
    assets = pf.list_assets()
    tx_all = pf.list_transactions()
    cash_all = pf.list_cash()
    targets = pf.list_targets()
    goal_total = float(pf.get_setting("goal_total", 0) or 0)
    info = assets.set_index("symbol").to_dict("index") if not assets.empty else {}
    fx, prices, _ = quote_prices(tx_all, cash_all, info)
    positions, _ = pf.compute_positions(tx_all, fx)
    valued = pf.value_positions(positions, prices, fx)
    ac = class_map(assets)
    return pf.class_summary(valued, ac, cash_all, targets, fx, goal_total or None,
                            pf.month_buys_by_class(tx_all, ac, today, fx))
