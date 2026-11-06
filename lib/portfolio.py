"""포트폴리오: 거래·계좌·자산군·현금 저장(Supabase)과 보유 현황·리밸런싱 계산.

계산 함수는 DB·네트워크 없이 pandas만으로 동작한다(테스트 용이).
평단은 이동평균법: 매수하면 평단이 바뀌고, 매도는 평단을 바꾸지 않고 실현손익만 만든다.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from lib.db import get_client

SIDES = {"buy": "매수", "sell": "매도", "dividend": "배당"}
UNCLASSIFIED = "미분류"
TX_COLUMNS = ["id", "trade_date", "account_id", "symbol", "side", "quantity", "price",
              "currency", "fee", "fx_rate", "memo"]


# ── 저장 ────────────────────────────────────────────────────

def _rows(table: str, order: str = "id") -> list[dict]:
    return get_client().table(table).select("*").order(order).execute().data


def list_accounts() -> pd.DataFrame:
    return pd.DataFrame(_rows("accounts"), columns=["id", "name", "broker", "currency"])


def add_account(name: str, broker: str | None, currency: str) -> None:
    get_client().table("accounts").insert({"name": name, "broker": broker or None, "currency": currency}).execute()


def update_account(account_id: int, name: str, broker: str | None, currency: str) -> None:
    get_client().table("accounts").update({"name": name, "broker": broker or None, "currency": currency}) \
        .eq("id", account_id).execute()


def delete_account(account_id: int) -> None:
    get_client().table("accounts").delete().eq("id", account_id).execute()


def list_assets() -> pd.DataFrame:
    return pd.DataFrame(_rows("assets", "symbol"),
                        columns=["symbol", "name", "market", "currency", "asset_class", "manual_price", "manual_price_at"])


def upsert_asset(symbol: str, name: str, market: str | None, currency: str, asset_class: str | None) -> None:
    get_client().table("assets").upsert(
        {"symbol": symbol, "name": name, "market": market, "currency": currency, "asset_class": asset_class or None},
        on_conflict="symbol",
    ).execute()


def update_asset(symbol: str, asset_class: str | None, manual_price: float | None) -> None:
    payload = {"asset_class": asset_class or None, "manual_price": manual_price}
    payload["manual_price_at"] = datetime.now().isoformat() if manual_price is not None else None
    get_client().table("assets").update(payload).eq("symbol", symbol).execute()


def list_transactions() -> pd.DataFrame:
    df = pd.DataFrame(_rows("transactions"), columns=TX_COLUMNS)
    return normalize_tx(df)


def add_transaction(tx: dict) -> None:
    payload = {k: tx.get(k) for k in TX_COLUMNS if k != "id"}
    payload["trade_date"] = str(payload["trade_date"])
    get_client().table("transactions").insert(payload).execute()


def delete_transaction(tx_id: int) -> None:
    get_client().table("transactions").delete().eq("id", tx_id).execute()


def list_targets() -> pd.DataFrame:
    df = pd.DataFrame(_rows("target_allocation", "sort_order"),
                      columns=["asset_class", "target_weight", "monthly_contribution", "target_return",
                               "status_note", "strategy", "sort_order"])
    # 예전 '현황'(status_note)은 '전략' 하나로 합쳐 보여 준다. 다음 저장 때 strategy 에 합쳐 저장된다.
    df["strategy"] = pd.Series([merge_notes(a, b) for a, b in zip(df["status_note"], df["strategy"])],
                               index=df.index, dtype=object)
    df["status_note"] = None
    return df.sort_values("sort_order", kind="stable").reset_index(drop=True)


def merge_notes(*parts) -> str | None:
    return "\n\n".join(t for t in (_txt(p) for p in parts) if t) or None


def save_strategy(asset_class: str, text: str | None) -> None:
    """자산군 하나의 전략 메모를 저장한다. 빈 값이면 메모를 지운다(자산군·목표 비중은 그대로)."""
    get_client().table("target_allocation").update(
        {"strategy": _txt(text), "status_note": None}).eq("asset_class", asset_class).execute()


def save_targets(edited: pd.DataFrame, original: pd.DataFrame) -> None:
    """설정 화면 표 편집 결과를 저장한다: 남은 행은 upsert, 지운 행은 delete."""
    db = get_client()
    keep = set()
    for i, r in edited.reset_index(drop=True).iterrows():
        name = str(r.get("asset_class") or "").strip()
        if not name:
            continue
        keep.add(name)
        db.table("target_allocation").upsert({
            "asset_class": name,
            "target_weight": _num(r.get("target_weight")),
            "monthly_contribution": _num(r.get("monthly_contribution")),
            "target_return": None if pd.isna(r.get("target_return")) else float(r.get("target_return")),
            "status_note": _txt(r.get("status_note")),
            "strategy": _txt(r.get("strategy")),
            "sort_order": i + 1,
        }, on_conflict="asset_class").execute()
    for name in set(original["asset_class"]) - keep:
        db.table("target_allocation").delete().eq("asset_class", name).execute()


def list_cash() -> pd.DataFrame:
    return pd.DataFrame(_rows("cash_balances"),
                        columns=["id", "name", "asset_class", "account_id", "currency", "amount", "memo", "updated_at"])


CASH_CLASS = "투자유보현금"


def save_cash(edited: pd.DataFrame, original: pd.DataFrame, account_ids: dict[str, int] | None = None,
              default_class: str = CASH_CLASS) -> None:
    """현금성 자산 표 편집 결과 저장: 남은 행은 수정·추가, 지운 행은 삭제.

    account 열(계좌 이름)이 있으면 account_ids로 id를 찾아 넣는다. 자산군이 비면 default_class.
    """
    db = get_client()
    keep = set()
    for _, r in edited.iterrows():
        name = _txt(r.get("name"))
        if not name:
            continue
        payload = {"name": name, "asset_class": _txt(r.get("asset_class")) or default_class,
                   "currency": _txt(r.get("currency")) or "KRW", "amount": _num(r.get("amount")),
                   "memo": _txt(r.get("memo")), "updated_at": datetime.now().isoformat()}
        if "account" in edited.columns:
            payload["account_id"] = (account_ids or {}).get(_txt(r.get("account")) or "")
        rid = r.get("id")
        if rid is not None and not pd.isna(rid):
            keep.add(int(rid))
            db.table("cash_balances").update(payload).eq("id", int(rid)).execute()
        else:
            db.table("cash_balances").insert(payload).execute()
    for rid in set(original["id"].dropna().astype(int)) - keep:
        db.table("cash_balances").delete().eq("id", int(rid)).execute()


def get_setting(key: str, default=None):
    rows = get_client().table("app_settings").select("*").eq("key", key).execute().data
    return rows[0]["value"] if rows else default


def set_setting(key: str, value) -> None:
    get_client().table("app_settings").upsert({"key": key, "value": value}, on_conflict="key").execute()


def list_snapshots() -> pd.DataFrame:
    return pd.DataFrame(_rows("portfolio_snapshots", "month"), columns=["month", "asset_class", "value_krw", "cost_krw"])


def save_snapshot(month: date, summary: pd.DataFrame) -> None:
    db = get_client()
    m = month.replace(day=1).isoformat()
    for _, r in summary.iterrows():
        db.table("portfolio_snapshots").upsert(
            {"month": m, "asset_class": r["asset_class"], "value_krw": float(r["value_krw"]),
             "cost_krw": float(r["cost_krw"])},
            on_conflict="month,asset_class",
        ).execute()


def _num(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if np.isnan(f) else f


def _txt(v) -> str | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


# ── 계산 ────────────────────────────────────────────────────

def normalize_tx(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in TX_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in ("quantity", "price", "fee"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["fx_rate"] = pd.to_numeric(df["fx_rate"], errors="coerce")
    return df[TX_COLUMNS].sort_values(["trade_date", "id"], kind="stable").reset_index(drop=True)


def _fx_of(row, current_fx: dict[str, float]) -> float:
    if row["currency"] == "KRW":
        return 1.0
    if pd.notna(row["fx_rate"]) and row["fx_rate"] > 0:
        return float(row["fx_rate"])
    return float(current_fx.get(row["currency"], np.nan))


def compute_positions(tx: pd.DataFrame, current_fx: dict[str, float] | None = None
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """거래내역 → (종목별 보유 현황, 실현 이벤트(매도·배당) 목록).

    보유 현황 열: symbol, currency, quantity, avg_cost, cost, avg_cost_krw, cost_krw,
                 realized, realized_krw, dividends, dividends_krw, buy_total_krw
    원화 금액은 거래 당시 환율(fx_rate)로, 없으면 현재 환율로 계산한다.
    """
    current_fx = current_fx or {}
    pos: dict[str, dict] = {}
    events = []
    for _, t in normalize_tx(tx).iterrows():
        sym = t["symbol"]
        p = pos.setdefault(sym, dict(symbol=sym, currency=t["currency"], quantity=0.0, cost=0.0, cost_krw=0.0,
                                     realized=0.0, realized_krw=0.0, dividends=0.0, dividends_krw=0.0,
                                     buy_total_krw=0.0))
        fx = _fx_of(t, current_fx)
        q, price, fee = float(t["quantity"]), float(t["price"]), float(t["fee"])
        if t["side"] == "buy":
            amount = q * price + fee
            p["quantity"] += q
            p["cost"] += amount
            p["cost_krw"] += amount * fx
            p["buy_total_krw"] += amount * fx
        elif t["side"] == "sell":
            held = p["quantity"]
            q = min(q, held) if held > 0 else 0.0
            if q <= 0:
                continue
            avg, avg_krw = p["cost"] / held, p["cost_krw"] / held
            proceeds = q * price - fee
            gain, gain_krw = proceeds - q * avg, proceeds * fx - q * avg_krw
            p["quantity"] = held - q
            p["cost"] -= q * avg
            p["cost_krw"] -= q * avg_krw
            if p["quantity"] < 1e-12:
                p["quantity"] = p["cost"] = p["cost_krw"] = 0.0
            p["realized"] += gain
            p["realized_krw"] += gain_krw
            events.append(dict(trade_date=t["trade_date"], symbol=sym, kind="매도", currency=t["currency"],
                               amount=gain, amount_krw=gain_krw, account_id=t["account_id"]))
        elif t["side"] == "dividend":
            amount = price - fee  # 배당은 price에 받은 총액
            p["dividends"] += amount
            p["dividends_krw"] += amount * fx
            events.append(dict(trade_date=t["trade_date"], symbol=sym, kind="배당", currency=t["currency"],
                               amount=amount, amount_krw=amount * fx, account_id=t["account_id"]))
    cols = ["symbol", "currency", "quantity", "avg_cost", "cost", "avg_cost_krw", "cost_krw",
            "realized", "realized_krw", "dividends", "dividends_krw", "buy_total_krw"]
    df = pd.DataFrame(list(pos.values()))
    if df.empty:
        positions = pd.DataFrame(columns=cols)
    else:
        qty = df["quantity"].replace(0, np.nan)
        df["avg_cost"] = df["cost"] / qty
        df["avg_cost_krw"] = df["cost_krw"] / qty
        positions = df[cols]
    ev = pd.DataFrame(events, columns=["trade_date", "symbol", "kind", "currency", "amount", "amount_krw", "account_id"])
    return positions, ev


def value_positions(positions: pd.DataFrame, prices: dict[str, float], fx: dict[str, float]) -> pd.DataFrame:
    """현재가·현재 환율로 평가. 시세가 없으면 원가로 평가하고 price_missing=True."""
    df = positions.copy()
    if df.empty:
        for c in ("price", "price_missing", "value", "value_krw", "pnl", "pnl_pct", "pnl_krw", "pnl_krw_pct", "fx_now"):
            df[c] = pd.Series(dtype=float)
        return df
    df["price"] = df["symbol"].map(lambda s: prices.get(s, np.nan)).astype(float)
    df["price_missing"] = df["price"].isna()
    df["fx_now"] = df["currency"].map(lambda c: 1.0 if c == "KRW" else fx.get(c, np.nan)).astype(float)
    df["value"] = np.where(df["price_missing"], df["cost"], df["quantity"] * df["price"])
    df["value_krw"] = df["value"] * df["fx_now"]
    # 환율을 모르면 원화 평가는 원가로 둔다
    df["value_krw"] = df["value_krw"].fillna(df["cost_krw"])
    df["pnl"] = df["value"] - df["cost"]
    df["pnl_pct"] = df["pnl"] / df["cost"].replace(0, np.nan) * 100
    df["pnl_krw"] = df["value_krw"] - df["cost_krw"]
    df["pnl_krw_pct"] = df["pnl_krw"] / df["cost_krw"].replace(0, np.nan) * 100
    return df


def cash_krw(cash: pd.DataFrame, fx: dict[str, float]) -> pd.Series:
    if cash.empty:
        return pd.Series(dtype=float)
    rate = cash["currency"].map(lambda c: 1.0 if c == "KRW" else fx.get(c, np.nan)).astype(float)
    return pd.to_numeric(cash["amount"], errors="coerce").fillna(0) * rate.fillna(0)


def class_summary(valued: pd.DataFrame, asset_class: dict[str, str], cash: pd.DataFrame,
                  targets: pd.DataFrame, fx: dict[str, float], goal_total: float | None = None,
                  month_buys: dict[str, float] | None = None) -> pd.DataFrame:
    """자산군별 요약 (시트 '포트폴리오' 탭과 같은 열).

    asset_class, value_krw, cost_krw, pnl_krw, pnl_pct, weight, target_weight, diff_pp,
    target_amount, progress, monthly_contribution, month_bought, target_return, target_profit
    """
    parts = []
    if not valued.empty:
        held = valued[valued["quantity"] > 0]
        parts.append(pd.DataFrame({
            "asset_class": held["symbol"].map(lambda s: asset_class.get(s) or UNCLASSIFIED),
            "value_krw": held["value_krw"], "cost_krw": held["cost_krw"]}))
    if not cash.empty:
        v = cash_krw(cash, fx)
        parts.append(pd.DataFrame({"asset_class": cash["asset_class"].fillna(UNCLASSIFIED),
                                   "value_krw": v, "cost_krw": v}))
    held = (pd.concat(parts).groupby("asset_class", sort=False)[["value_krw", "cost_krw"]].sum()
            if parts else pd.DataFrame(columns=["value_krw", "cost_krw"]))

    t = targets.set_index("asset_class") if not targets.empty else pd.DataFrame(
        columns=["target_weight", "monthly_contribution", "target_return"])
    order = list(t.index) + [c for c in held.index if c not in t.index]
    out = pd.DataFrame(index=pd.Index(order, name="asset_class"))
    out["value_krw"] = held["value_krw"].reindex(order).fillna(0.0)
    out["cost_krw"] = held["cost_krw"].reindex(order).fillna(0.0)
    out["pnl_krw"] = out["value_krw"] - out["cost_krw"]
    out["pnl_pct"] = out["pnl_krw"] / out["cost_krw"].replace(0, np.nan) * 100
    total = out["value_krw"].sum()
    out["weight"] = out["value_krw"] / total * 100 if total else 0.0
    out["target_weight"] = pd.to_numeric(t["target_weight"], errors="coerce").reindex(order).fillna(0.0)
    out["diff_pp"] = out["weight"] - out["target_weight"]
    base = goal_total if goal_total else total
    out["target_amount"] = base * out["target_weight"] / 100
    out["progress"] = out["value_krw"] / out["target_amount"].replace(0, np.nan) * 100
    out["monthly_contribution"] = pd.to_numeric(t["monthly_contribution"], errors="coerce").reindex(order).fillna(0.0)
    out["month_bought"] = pd.Series(month_buys or {}, dtype=float).reindex(order).fillna(0.0)
    out["target_return"] = pd.to_numeric(t["target_return"], errors="coerce").reindex(order)
    out["target_profit"] = out["target_amount"] * out["target_return"] / 100
    return out.reset_index()


def month_buys_by_class(tx: pd.DataFrame, asset_class: dict[str, str], month: date,
                        current_fx: dict[str, float] | None = None) -> dict[str, float]:
    """그 달에 매수한 원화 금액(수수료 포함)을 자산군별로 합친다."""
    df = normalize_tx(tx)
    df = df[(df["side"] == "buy") & df["trade_date"].map(lambda d: (d.year, d.month) == (month.year, month.month))]
    out: dict[str, float] = {}
    for _, t in df.iterrows():
        k = asset_class.get(t["symbol"]) or UNCLASSIFIED
        out[k] = out.get(k, 0.0) + (t["quantity"] * t["price"] + t["fee"]) * _fx_of(t, current_fx or {})
    return out


def rebalance(summary: pd.DataFrame, new_money: float = 0.0, allow_sell: bool = False) -> pd.DataFrame:
    """목표 비중으로 가기 위한 자산군별 매수(+)/매도(-) 금액.

    allow_sell=False: 새로 넣는 돈만 모자란 자산군에 나눠 넣는다(시트처럼 적립식).
      부족분 합이 투입금보다 크면 부족분 비율대로 나누고, 남으면 목표 비중대로 나눈다.
    allow_sell=True: 투입금 포함 총액을 목표 비중에 정확히 맞춘다(매도 포함).
    목표 비중 합이 100이 아니면 합 기준으로 다시 맞춘다. 목표가 없는 자산군은 0%로 본다.
    """
    df = summary[["asset_class", "value_krw", "target_weight"]].copy()
    w = df["target_weight"].clip(lower=0)
    w = w / w.sum() if w.sum() > 0 else w * 0
    total_after = df["value_krw"].sum() + new_money
    desired = total_after * w
    gap = desired - df["value_krw"]
    if allow_sell:
        trade = gap
    else:
        need = gap.clip(lower=0)
        if new_money <= 0:
            trade = need * 0
        elif need.sum() >= new_money:
            trade = need / need.sum() * new_money
        else:
            trade = need + (new_money - need.sum()) * w
    df["trade_krw"] = trade.fillna(0.0)
    df["after_krw"] = df["value_krw"] + df["trade_krw"]
    after_total = df["after_krw"].sum()
    df["weight"] = df["value_krw"] / df["value_krw"].sum() * 100 if df["value_krw"].sum() else 0.0
    df["after_weight"] = df["after_krw"] / after_total * 100 if after_total else 0.0
    df["target_weight_norm"] = w * 100
    return df


# ── 달러 환전 기록 ──────────────────────────────────────────

FX_SIDES = {"buy": "환전 (원→외화)", "sell": "재환전 (외화→원)"}


def list_fx() -> pd.DataFrame:
    df = pd.DataFrame(_rows("fx_exchanges"),
                      columns=["id", "trade_date", "side", "currency", "amount", "rate", "fee", "account_id", "memo"])
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in ("amount", "rate", "fee"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df.sort_values(["trade_date", "id"], kind="stable").reset_index(drop=True)


def add_fx(row: dict) -> None:
    payload = {k: row.get(k) for k in ("trade_date", "side", "currency", "amount", "rate", "fee", "account_id", "memo")}
    payload["trade_date"] = str(payload["trade_date"])
    get_client().table("fx_exchanges").insert(payload).execute()


def delete_fx(fx_id: int) -> None:
    get_client().table("fx_exchanges").delete().eq("id", fx_id).execute()


def fx_summary(fx: pd.DataFrame, current_rate: float | None = None) -> dict:
    """환전 기록 요약 (한 통화).

    avg_rate: 지금까지 환전한 모든 외화의 평균 환율 (시트의 '평균환율'과 같은 방식)
    held / held_avg_rate: 재환전하지 않고 남은 외화와 그 평단(이동평균)
    realized: 재환전으로 생긴 원화 손익 (재환전 금액 − 수수료 − 이동평균 원가)
    unrealized: 남은 외화를 현재 환율로 바꾸면 생길 손익
    """
    buys = fx[fx["side"] == "buy"]
    sells = fx[fx["side"] == "sell"]
    bought = float(buys["amount"].sum())
    bought_krw = float((buys["amount"] * buys["rate"] + buys["fee"]).sum())
    tx = pd.DataFrame({"id": fx["id"], "trade_date": fx["trade_date"], "symbol": "FX", "side": fx["side"],
                       "quantity": fx["amount"], "price": fx["rate"], "currency": "KRW", "fee": fx["fee"],
                       "fx_rate": 1.0, "account_id": fx["account_id"], "memo": None})
    pos, _ = compute_positions(tx)
    p = pos.iloc[0] if not pos.empty else None
    held = float(p["quantity"]) if p is not None else 0.0
    held_cost = float(p["cost"]) if p is not None else 0.0
    out = {
        "bought": bought, "bought_krw": bought_krw, "avg_rate": bought_krw / bought if bought else None,
        "sold": float(sells["amount"].sum()), "sold_krw": float((sells["amount"] * sells["rate"] - sells["fee"]).sum()),
        "held": held, "held_cost": held_cost, "held_avg_rate": held_cost / held if held else None,
        "realized": float(p["realized"]) if p is not None else 0.0,
        "current_rate": current_rate, "unrealized": None,
    }
    if current_rate and held:
        out["unrealized"] = held * current_rate - held_cost
    return out


# ── CSV 가져오기 ────────────────────────────────────────────

CSV_HEADERS = ["날짜", "계좌", "구분", "티커", "종목명", "자산군", "수량", "가격", "통화", "수수료", "환율", "메모"]


def parse_csv(df: pd.DataFrame, accounts: pd.DataFrame) -> tuple[list[dict], list[dict], list[str]]:
    """CSV 표 → (거래 목록, 새 종목 목록, 오류 메시지). 구분은 매수/매도/배당 또는 buy/sell/dividend."""
    side_map = {"매수": "buy", "매도": "sell", "배당": "dividend", "buy": "buy", "sell": "sell", "dividend": "dividend"}
    acc = {str(n): int(i) for i, n in zip(accounts["id"], accounts["name"])}
    txs, assets, errors = [], {}, []
    for i, r in df.iterrows():
        line = i + 2
        raw = str(r.get("구분", "")).strip()
        side = side_map.get(raw) or side_map.get(raw.lower())
        if not side:
            errors.append(f"{line}행: 구분은 매수/매도/배당 중 하나여야 해요")
            continue
        sym = _txt(r.get("티커"))
        if not sym:
            errors.append(f"{line}행: 티커가 비어 있어요")
            continue
        try:
            d = pd.to_datetime(str(r.get("날짜"))).date()
        except Exception:
            errors.append(f"{line}행: 날짜를 읽지 못했어요")
            continue
        acc_name = _txt(r.get("계좌"))
        if acc_name and acc_name not in acc:
            errors.append(f"{line}행: 계좌 '{acc_name}'가 없어요. 설정 탭에서 먼저 만들어 주세요")
            continue
        ccy = (_txt(r.get("통화")) or ("KRW" if sym.endswith((".KS", ".KQ")) else "USD")).upper()
        fx = _num(r.get("환율"))
        txs.append({"trade_date": d, "account_id": acc.get(acc_name) if acc_name else None, "symbol": sym,
                    "side": side, "quantity": 0.0 if side == "dividend" else _num(r.get("수량")),
                    "price": _num(r.get("가격")), "currency": ccy, "fee": _num(r.get("수수료")),
                    "fx_rate": fx if fx > 0 and ccy != "KRW" else None, "memo": _txt(r.get("메모"))})
        assets.setdefault(sym, {"symbol": sym, "name": _txt(r.get("종목명")) or sym, "currency": ccy,
                                "asset_class": _txt(r.get("자산군"))})
    return txs, list(assets.values()), errors
