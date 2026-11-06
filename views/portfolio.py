from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import portfolio as pf
from lib import symbols
from lib import market

UP, DOWN = "#d62728", "#1f5fbf"          # 한국식: 이익 빨강, 손실 파랑
BAR, INK, MUTED = "#2a78d6", "#52514e", "#b9b8b2"
CURRENCIES = ["KRW", "USD", "JPY", "EUR", "CNY", "HKD"]
MANUAL = "MANUAL:"                       # 티커 없는 자산(예: KRX 금현물)의 티커 앞머리
NEW_SEARCH, NEW_MANUAL = "__search__", "__manual__"

st.title("포트폴리오")

try:
    accounts = pf.list_accounts()
    assets = pf.list_assets()
    tx_all = pf.list_transactions()
    targets = pf.list_targets()
    cash_all = pf.list_cash()
    goal_total = float(pf.get_setting("goal_total", 0) or 0)
except Exception:
    st.error("DB에 연결하지 못했어요. Secrets를 확인하고, Supabase에 `supabase/002_portfolio.sql`이 적용됐는지 확인해 주세요.")
    st.stop()

acc_name = dict(zip(accounts["id"], accounts["name"]))
asset_info = assets.set_index("symbol").to_dict("index") if not assets.empty else {}
asset_class = {s: (i["asset_class"] if pd.notna(i.get("asset_class")) and i.get("asset_class") else pf.UNCLASSIFIED)
               for s, i in asset_info.items()}
class_names = list(targets["asset_class"])
class_options = class_names + ([pf.UNCLASSIFIED] if pf.UNCLASSIFIED not in class_names else [])


def name_of(sym: str) -> str:
    n = (asset_info.get(sym) or {}).get("name")
    return n if isinstance(n, str) and n else sym.replace(MANUAL, "")


def money(values: pd.Series, ccy: pd.Series | str) -> pd.Series:
    """원화는 원 단위, 외화는 센트 단위로 반올림."""
    digits = (ccy == "KRW") if isinstance(ccy, pd.Series) else pd.Series(ccy == "KRW", index=values.index)
    return pd.Series(np.where(digits, values.round(0), values.round(2)), index=values.index)


def won(v: float) -> str:
    return f"{'+' if v > 0 else '−' if v < 0 else ''}₩{abs(v):,.0f}"


def fx_ticker(ccy: str) -> str:
    return "KRW=X" if ccy == "USD" else f"{ccy}KRW=X"


# ── 시세·환율 ───────────────────────────────────────────────
ccys = (set(tx_all["currency"].dropna()) | set(cash_all["currency"].dropna()) | {"USD"}) - {"KRW"}
net_qty = tx_all.assign(q=tx_all["quantity"].where(tx_all["side"] == "buy", -tx_all["quantity"].where(
    tx_all["side"] == "sell", 0))).groupby("symbol")["q"].sum() if not tx_all.empty else pd.Series(dtype=float)
held_syms = set(net_qty[net_qty > 1e-9].index)  # 다 판 종목은 시세를 받지 않는다
quoted = sorted(s for s in held_syms if not s.startswith(MANUAL))
with st.spinner("시세 불러오는 중…"):
    try:
        closes = market.fetch_yf_closes(tuple(sorted(set(quoted) | {fx_ticker(c) for c in ccys})))
    except Exception:
        closes = pd.DataFrame()
last = {c: float(closes[c].dropna().iloc[-1]) for c in closes.columns if not closes[c].dropna().empty}
fx = {c: last[fx_ticker(c)] for c in ccys if fx_ticker(c) in last}
prices, price_src = {}, {}
for s in held_syms:
    manual = (asset_info.get(s) or {}).get("manual_price")
    auto = market.auto_price(s) if s.startswith(MANUAL) else None
    if s in last and not s.startswith(MANUAL):
        prices[s], price_src[s] = last[s], "시세"
    elif auto:
        prices[s], price_src[s] = auto, "시세(네이버)"
    elif manual is not None and not pd.isna(manual):
        prices[s], price_src[s] = float(manual), "직접 입력"

# ── 상단: 계좌 필터, 원화 보기 ─────────────────────────────
f1, f2 = st.columns([3, 1])
sel_acc = f1.multiselect("계좌", list(accounts["id"]), format_func=lambda i: acc_name.get(i, str(i)),
                         placeholder="전체 계좌")
in_krw = f2.toggle("원화로 보기", value=False, help="끄면 종목 통화 그대로, 켜면 원화(거래 당시 환율 기준 원가)")

tx = tx_all[tx_all["account_id"].isin(sel_acc)] if sel_acc else tx_all
cash = cash_all if not sel_acc else cash_all[cash_all["account_id"].isin(sel_acc)]
positions, events = pf.compute_positions(tx, fx)
valued = pf.value_positions(positions, prices, fx)
today = date.today()
month_buys = pf.month_buys_by_class(tx, asset_class, today, fx)
summary = pf.class_summary(valued, asset_class, cash, targets, fx, goal_total or None, month_buys)

total_value = float(summary["value_krw"].sum())
total_cost = float(summary["cost_krw"].sum())
pnl = total_value - total_cost
m1, m2, m3, m4 = st.columns(4)
m1.metric("총 평가금액", f"₩{total_value:,.0f}")
m2.metric("투자원금", f"₩{total_cost:,.0f}")
m3.metric("평가손익", f"₩{pnl:,.0f}", f"{pnl / total_cost * 100:+.2f}%" if total_cost else None,
          delta_color="red" if pnl > 0 else "blue" if pnl < 0 else "off")
if goal_total:
    m4.metric("목표 총액 달성", f"{total_value / goal_total * 100:.1f}%", f"목표 ₩{goal_total:,.0f}",
              delta_color="off")
else:
    m4.metric("이번 달 적립", f"₩{sum(month_buys.values()):,.0f}")
if ccys and not all(c in fx for c in ccys if c in set(tx["currency"]) | set(cash["currency"])):
    st.warning("환율을 받지 못한 통화가 있어 일부 원화 금액은 원가로 표시돼요.")

tab_hold, tab_alloc, tab_tx, tab_real, tab_fx, tab_set = st.tabs(
    ["보유 현황", "자산 배분·리밸런싱", "거래 입력·내역", "실현손익·배당", "달러 환전", "설정"])

NUM = st.column_config.NumberColumn(format="localized")
PCT = st.column_config.NumberColumn(format="%+.2f%%")
WEIGHT = st.column_config.NumberColumn(format="%.1f%%")

# ── 보유 현황 ───────────────────────────────────────────────
with tab_hold:
    held = valued[valued["quantity"] > 0].copy() if not valued.empty else valued
    if held.empty and cash.empty:
        st.info("아직 보유 종목이 없어요. ‘거래 입력·내역’ 탭에서 매수 기록을 넣거나, ‘설정’에서 현금성 자산을 넣어 보세요.")
    if not held.empty:
        order = {c: i for i, c in enumerate(class_options)}
        held["자산군"] = held["symbol"].map(lambda s: asset_class.get(s, pf.UNCLASSIFIED))
        held = held.assign(_o=held["자산군"].map(lambda c: order.get(c, 99))).sort_values(["_o", "value_krw"],
                                                                                       ascending=[True, False])
        rows = pd.DataFrame({
            "자산군": held["자산군"],
            "종목": held["symbol"].map(name_of),
            "티커": held["symbol"].str.replace(MANUAL, "", regex=False),
            "수량": held["quantity"],
        })
        if in_krw:
            rows["평단(원)"] = held["avg_cost_krw"].round(0)
            rows["현재가(원)"] = (held["price"] * held["fx_now"]).round(0)
            rows["평가금액(원)"] = held["value_krw"].round(0)
            rows["손익(원)"] = held["pnl_krw"].round(0)
            rows["수익률"] = held["pnl_krw_pct"]
        else:
            rows["통화"] = held["currency"]
            rows["평단"] = money(held["avg_cost"], held["currency"])
            rows["현재가"] = money(held["price"], held["currency"])
            rows["평가금액"] = money(held["value"], held["currency"])
            rows["손익"] = money(held["pnl"], held["currency"])
            rows["수익률"] = held["pnl_pct"]
        rows["비중"] = held["value_krw"] / total_value * 100 if total_value else 0.0
        rows["시세"] = held["symbol"].map(lambda s: price_src.get(s, "없음"))
        st.dataframe(rows, hide_index=True, width="stretch", column_config={
            "수량": st.column_config.NumberColumn(format="%.6g"),
            **{c: NUM for c in rows.columns if c.startswith(("평단", "현재가", "평가금액", "손익"))},
            "수익률": PCT, "비중": WEIGHT})
        st.caption("원화로 보기의 손익에는 환율 변동이 포함돼요 (원가는 거래 당시 환율, 평가는 현재 환율). "
                   "평단은 이동평균법이라 매도해도 평단이 바뀌지 않아요.")
        missing = held.loc[held["price_missing"], "symbol"].map(name_of).tolist()
        if missing:
            st.warning(f"시세를 못 받아 원가로 표시한 종목: {', '.join(missing)}. ‘설정 → 종목’에서 현재가를 직접 넣을 수 있어요.")
    if not cash.empty:
        st.markdown("**현금성 자산**")
        st.dataframe(pd.DataFrame({
            "자산군": cash["asset_class"], "이름": cash["name"], "통화": cash["currency"],
            "잔액": pd.to_numeric(cash["amount"]), "원화 환산": pf.cash_krw(cash, fx).round(0),
            "메모": cash["memo"].fillna(""),
        }), hide_index=True, width="stretch", column_config={"잔액": NUM, "원화 환산": NUM})
    notes = targets[(targets["status_note"].notna()) | (targets["strategy"].notna())]
    if not notes.empty:
        st.markdown("**자산군별 현황·전략 메모**")
        for _, t in notes.iterrows():
            with st.expander(t["asset_class"]):
                if pd.notna(t["status_note"]):
                    st.markdown(f"**현황** · {t['status_note']}")
                if pd.notna(t["strategy"]):
                    st.markdown(f"**전략** · {t['strategy']}")


# ── 자산 배분·리밸런싱 ─────────────────────────────────────
def weight_chart(df: pd.DataFrame) -> go.Figure:
    """현재 비중(막대)과 목표 비중(세로 눈금)."""
    df = df.iloc[::-1]
    fig = go.Figure()
    fig.add_bar(y=df["asset_class"], x=df["weight"], orientation="h", name="현재 비중",
                marker=dict(color=BAR, cornerradius=4), width=0.55,
                hovertemplate="%{y}<br>현재 %{x:.1f}%<extra></extra>")
    fig.add_scatter(y=df["asset_class"], x=df["target_weight"], mode="markers", name="목표 비중",
                    marker=dict(symbol="line-ns", size=22, line=dict(width=3, color=INK)),
                    hovertemplate="%{y}<br>목표 %{x:.1f}%<extra></extra>")
    fig.update_layout(height=60 + 44 * len(df), margin=dict(l=0, r=30, t=10, b=0),
                      legend=dict(orientation="h", y=1.08, x=0), bargap=0.3,
                      xaxis=dict(ticksuffix="%", gridcolor="rgba(128,128,128,.15)", zeroline=False),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


with tab_alloc:
    if summary.empty:
        st.info("‘설정’ 탭에서 자산군과 목표 비중을 정하면 여기서 비교와 리밸런싱을 볼 수 있어요.")
    else:
        st.plotly_chart(weight_chart(summary), width="stretch", config={"displayModeBar": False}, key="weights")
        table = pd.DataFrame({
            "자산군": summary["asset_class"], "평가금액": summary["value_krw"], "투자원금": summary["cost_krw"],
            "손익": summary["pnl_krw"], "수익률": summary["pnl_pct"], "현재 비중": summary["weight"],
            "목표 비중": summary["target_weight"], "차이": summary["diff_pp"],
            "목표금액": summary["target_amount"], "달성률": summary["progress"],
            "목표 수익률": summary["target_return"], "목표 수익금": summary["target_profit"],
        })
        krw_cols = ["평가금액", "투자원금", "손익", "목표금액", "목표 수익금"]
        table[krw_cols] = table[krw_cols].round(0)
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            **{c: NUM for c in ("평가금액", "투자원금", "손익", "목표금액", "목표 수익금")},
            "수익률": PCT, "현재 비중": WEIGHT, "목표 비중": WEIGHT,
            "차이": st.column_config.NumberColumn(format="%+.1f%%p"),
            "달성률": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
            "목표 수익률": st.column_config.NumberColumn(format="%.1f%%"),
        })
        st.caption(("목표금액 = 목표 총액 × 목표 비중" if goal_total else "목표금액 = 현재 총액 × 목표 비중 "
                    "(‘설정’에서 목표 총액을 넣으면 그 기준으로 바뀌어요)") + " · 금액은 모두 원화")

        st.subheader("리밸런싱")
        r1, r2 = st.columns([2, 1])
        mode = r1.radio("방식", ["새 돈만 넣기 (매도 없음)", "목표 비중에 맞추기 (매도 포함)"], horizontal=True)
        default_money = float(summary["monthly_contribution"].sum())
        new_money = r2.number_input("추가로 넣을 돈 (원)", min_value=0.0, value=default_money, step=100000.0,
                                format="%.0f", help="기본값은 자산군별 월 적립 목표의 합")
        if targets["target_weight"].fillna(0).sum() <= 0:
            st.info("목표 비중을 먼저 정해 주세요.")
        else:
            wsum = float(targets["target_weight"].fillna(0).sum())
            if abs(wsum - 100) > 0.01:
                st.caption(f"목표 비중 합이 {wsum:g}%라서 100% 기준으로 다시 계산했어요.")
            plan = pf.rebalance(summary, new_money, allow_sell=mode.startswith("목표"))
            holds = (valued[valued["quantity"] > 0].assign(c=lambda d: d["symbol"].map(lambda s: asset_class.get(s)))
                     .groupby("c")["symbol"].apply(lambda s: ", ".join(name_of(x) for x in s))
                     if not valued.empty else pd.Series(dtype=str))
            out = pd.DataFrame({
                "자산군": plan["asset_class"], "현재 금액": plan["value_krw"],
                "매수/매도": plan["trade_krw"], "이후 금액": plan["after_krw"],
                "현재 비중": plan["weight"], "이후 비중": plan["after_weight"], "목표 비중": plan["target_weight_norm"],
                "보유 종목": plan["asset_class"].map(holds).fillna(""),
            })
            out[["현재 금액", "매수/매도", "이후 금액"]] = out[["현재 금액", "매수/매도", "이후 금액"]].round(0)
            st.dataframe(out, hide_index=True, width="stretch", column_config={
                "현재 금액": NUM, "이후 금액": NUM,
                "매수/매도": st.column_config.NumberColumn(format="localized", help="+ 매수, − 매도 (원)"),
                "현재 비중": WEIGHT, "이후 비중": WEIGHT, "목표 비중": WEIGHT})
            buys = plan[plan["trade_krw"] > 0.5]
            sells = plan[plan["trade_krw"] < -0.5]
            msg = []
            if not buys.empty:
                msg.append("매수: " + ", ".join(f"{a} ₩{v:,.0f}" for a, v in zip(buys["asset_class"], buys["trade_krw"])))
            if not sells.empty:
                msg.append("매도: " + ", ".join(f"{a} ₩{-v:,.0f}" for a, v in zip(sells["asset_class"], sells["trade_krw"])))
            st.markdown(" · ".join(msg) if msg else "지금 비중 그대로 두면 돼요.")

        st.subheader(f"{today.month}월 적립 현황")
        acc_tbl = summary[(summary["monthly_contribution"] > 0) | (summary["month_bought"] > 0)]
        if acc_tbl.empty:
            st.caption("‘설정’에서 자산군별 월 적립 목표를 넣으면 이번 달 매수 금액과 비교해 보여 줘요.")
        else:
            st.dataframe(pd.DataFrame({
                "자산군": acc_tbl["asset_class"], "월 목표": acc_tbl["monthly_contribution"],
                "이번 달 매수": acc_tbl["month_bought"].round(0),
                "진행률": (acc_tbl["month_bought"] / acc_tbl["monthly_contribution"].replace(0, np.nan) * 100).fillna(0),
            }), hide_index=True, width="stretch", column_config={
                "월 목표": NUM, "이번 달 매수": NUM,
                "진행률": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%")})

        st.subheader("월별 기록")
        snaps = pf.list_snapshots()
        s1, s2 = st.columns([1, 3])
        if s1.button(f"{today:%Y년 %m월} 기록 저장", key="save-snapshot", disabled=bool(sel_acc),
                     help="전체 계좌 기준으로만 저장해요. 같은 달에 다시 누르면 덮어써요."):
            pf.save_snapshot(today, summary)
            st.rerun()
        if snaps.empty:
            s2.caption("매달 말에 한 번 저장해 두면 자산 추이를 볼 수 있어요 (시트 ‘전략’ 탭의 월별 열).")
        else:
            snaps["month"] = pd.to_datetime(snaps["month"])
            tot = snaps.groupby("month")[["value_krw", "cost_krw"]].sum().reset_index()
            fig = go.Figure()
            fig.add_scatter(x=tot["month"], y=tot["value_krw"], mode="lines+markers", name="평가금액",
                            line=dict(color=BAR, width=2), marker=dict(size=8),
                            hovertemplate="%{x|%Y-%m} ₩%{y:,.0f}<extra>평가금액</extra>")
            fig.add_scatter(x=tot["month"], y=tot["cost_krw"], mode="lines", name="투자원금",
                            line=dict(color=MUTED, width=2, dash="dot"),
                            hovertemplate="%{x|%Y-%m} ₩%{y:,.0f}<extra>투자원금</extra>")
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                              legend=dict(orientation="h", y=1.1, x=0),
                              yaxis=dict(tickformat=",.0f", gridcolor="rgba(128,128,128,.15)"),
                              xaxis=dict(tickformat="%Y-%m", dtick="M1"),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            s2.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="snapshots")
            with st.expander("월별 자산군 금액 표"):
                st.dataframe(snaps.pivot_table(index="asset_class", columns=snaps["month"].dt.strftime("%Y-%m"),
                                               values="value_krw", aggfunc="sum"),
                             width="stretch", column_config={c: NUM for c in snaps["month"].dt.strftime("%Y-%m")})

# ── 거래 입력·내역 ─────────────────────────────────────────
with tab_tx:
    st.subheader("거래 입력")
    known = list(assets["symbol"])
    pick = st.selectbox("종목", known + [NEW_SEARCH, NEW_MANUAL], key="tx-symbol",
                        format_func=lambda s: {NEW_SEARCH: "➕ 새 종목 검색", NEW_MANUAL: "➕ 티커 없는 자산 직접 등록"}.get(
                            s, f"{name_of(s)} · {s.replace(MANUAL, '')}"))
    new_asset = None
    if pick == NEW_SEARCH:
        q = st.text_input("종목 검색", placeholder="TIGER 미국S&P500, SPYM, 005930 …", key="tx-search")
        hits = symbols.search(q) if q else []
        if q and not hits:
            st.caption("결과가 없어요. 티커를 그대로 넣어 보세요 (예: 360750.KS, SPYM).")
        if hits:
            h = st.selectbox("검색 결과", hits, key=f"tx-hits-{q}",
                             format_func=lambda h: f"{h['name']} · {h['symbol']} ({h['market']})")
            ac = st.selectbox("자산군", class_options, key="tx-new-class")
            new_asset = {"symbol": h["symbol"], "name": h["name"], "market": symbols.market_for(h["symbol"]),
                         "currency": symbols.currency_for(h["symbol"]), "asset_class": ac}
    elif pick == NEW_MANUAL:
        c1, c2, c3 = st.columns([2, 1, 1])
        mname = c1.text_input("이름", placeholder="KRX 금현물", key="tx-manual-name")
        mccy = c2.selectbox("통화", CURRENCIES, key="tx-manual-ccy")
        ac = c3.selectbox("자산군", class_options, key="tx-manual-class")
        st.caption("시세가 자동으로 안 잡히는 자산이에요. 현재가는 ‘설정 → 종목’에서 직접 넣어 주세요.")
        if mname.strip():
            new_asset = {"symbol": MANUAL + mname.strip(), "name": mname.strip(), "market": None,
                         "currency": mccy, "asset_class": ac}
    sym = new_asset["symbol"] if new_asset else (pick if pick in known else None)
    ccy = new_asset["currency"] if new_asset else ((asset_info.get(sym) or {}).get("currency") or "KRW")

    side = st.radio("구분", list(pf.SIDES), format_func=pf.SIDES.get, horizontal=True, key="tx-side")
    c1, c2 = st.columns(2)
    tdate = c1.date_input("날짜", today, key="tx-date")
    acc_ids = [None] + list(accounts["id"])
    account = c2.selectbox("계좌", acc_ids, format_func=lambda i: "선택 안 함" if i is None else acc_name[i],
                           key="tx-account")
    c1, c2, c3, c4 = st.columns(4)
    if side == "dividend":
        qty = 0.0
        price = c1.number_input(f"배당금 총액 ({ccy})", min_value=0.0, step=1.0, format="%.4f", key="tx-price")
    else:
        qty = c1.number_input("수량", min_value=0.0, step=1.0, format="%.8g", key="tx-qty")
        price = c2.number_input(f"단가 ({ccy})", min_value=0.0, step=1.0, format="%.4f", key="tx-price")
    fee = c3.number_input(f"수수료·세금 ({ccy})", min_value=0.0, step=1.0, format="%.4f", key="tx-fee")
    fx_rate = None
    if ccy != "KRW":
        fx_rate = c4.number_input(f"환율 (원/{ccy})", min_value=0.0, value=float(fx.get(ccy, 0.0)), step=1.0,
                                  format="%.2f", key="tx-fx", help="거래 당시 환율(보유 달러 평균환율 등). 원화 원가 계산에 써요.")
    memo = st.text_input("메모", placeholder="정기 매수, 5% 하락 분할 …", key="tx-memo")
    amount = qty * price + fee if side == "buy" else (qty * price - fee if side == "sell" else price - fee)
    if sym and amount:
        st.caption(f"{pf.SIDES[side]} 금액 {amount:,.2f} {ccy}" +
                   (f" ≈ ₩{amount * fx_rate:,.0f}" if fx_rate else ""))
    held_qty = float(positions.set_index("symbol")["quantity"].get(sym, 0.0)) if sym and not positions.empty else 0.0
    if side == "sell" and sym and qty > held_qty + 1e-9:
        st.warning(f"보유 수량({held_qty:g})보다 많이 팔 수 없어요.")
    ok = bool(sym) and (price > 0) and (side == "dividend" or qty > 0) and not (side == "sell" and qty > held_qty + 1e-9)
    if st.button("거래 저장", type="primary", disabled=not ok, key="save-tx"):
        if new_asset:
            pf.upsert_asset(**new_asset)
        pf.add_transaction({"trade_date": tdate, "account_id": account, "symbol": sym, "side": side,
                            "quantity": qty, "price": price, "currency": ccy, "fee": fee,
                            "fx_rate": fx_rate or None, "memo": memo or None})
        for k in ("tx-qty", "tx-price", "tx-fee", "tx-memo"):
            st.session_state.pop(k, None)
        st.toast(f"{name_of(sym) if not new_asset else new_asset['name']} {pf.SIDES[side]} 기록을 저장했어요.")
        st.rerun()

    with st.expander("CSV로 한꺼번에 넣기 (시트에서 옮길 때)"):
        st.caption("열: " + ", ".join(pf.CSV_HEADERS) + ". 구분은 매수/매도/배당, 배당은 가격 칸에 받은 총액. "
                   "환율은 외화 거래만, 계좌는 ‘설정’에 있는 이름.")
        sample = pd.DataFrame([["2026-03-04", "", "매수", "SPYM", "SPDR Portfolio S&P 500", "", 4, 80.05, "USD", 0, 1415, "정기매수"]],
                              columns=pf.CSV_HEADERS)
        st.download_button("양식 내려받기", sample.to_csv(index=False).encode("utf-8-sig"), "거래_양식.csv", "text/csv")
        up = st.file_uploader("CSV 파일", type=["csv"], key="tx-csv")
        if up is not None:
            raw = pd.read_csv(up, dtype=str, encoding="utf-8-sig").fillna("")
            rows, new_assets, errs = pf.parse_csv(raw, accounts)
            for e in errs[:20]:
                st.error(e)
            st.caption(f"읽은 거래 {len(rows)}건, 새 종목 {len([a for a in new_assets if a['symbol'] not in asset_info])}개")
            if rows and st.button(f"{len(rows)}건 저장", key="import-csv"):
                for a in new_assets:
                    if a["symbol"] not in asset_info:
                        pf.upsert_asset(a["symbol"], a["name"], symbols.market_for(a["symbol"]), a["currency"],
                                        a["asset_class"])
                for r in rows:
                    pf.add_transaction(r)
                st.toast(f"{len(rows)}건을 저장했어요.")
                st.rerun()

    st.subheader("거래 내역")
    if tx.empty:
        st.caption("아직 거래가 없어요.")
    else:
        fs = st.multiselect("종목 필터", sorted(tx["symbol"].unique()), format_func=name_of, key="tx-filter")
        show = tx[tx["symbol"].isin(fs)] if fs else tx
        show = show.iloc[::-1]
        st.dataframe(pd.DataFrame({
            "id": show["id"], "날짜": show["trade_date"], "계좌": show["account_id"].map(acc_name).fillna(""),
            "구분": show["side"].map(pf.SIDES), "종목": show["symbol"].map(name_of), "수량": show["quantity"],
            "단가/금액": show["price"], "통화": show["currency"], "수수료": show["fee"], "환율": show["fx_rate"],
            "메모": show["memo"].fillna(""),
        }), hide_index=True, width="stretch", column_config={
            "id": None, "수량": st.column_config.NumberColumn(format="%.8g"), "단가/금액": NUM, "환율": NUM})
        with st.expander("거래 삭제"):
            del_id = st.selectbox("삭제할 거래", list(show["id"]), key="tx-del",
                                  format_func=lambda i: (lambda r: f"{r['trade_date']} {pf.SIDES[r['side']]} "
                                                         f"{name_of(r['symbol'])} {r['quantity']:g} × {r['price']:,g}")(
                                      tx.set_index("id").loc[i].to_dict() | {"id": i}))
            if st.button("삭제", key="tx-del-btn"):
                pf.delete_transaction(int(del_id))
                st.rerun()

# ── 실현손익·배당 ─────────────────────────────────────────
with tab_real:
    if events.empty:
        st.info("매도하거나 배당을 받으면 여기에 쌓여요 (시트 ‘순수익’ 탭).")
    else:
        realized = float(events.loc[events["kind"] == "매도", "amount_krw"].sum())
        divs = float(events.loc[events["kind"] == "배당", "amount_krw"].sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("총 수익금", f"₩{realized + divs:,.0f}")
        c2.metric("매매 실현손익", f"₩{realized:,.0f}")
        c3.metric("배당금", f"₩{divs:,.0f}")
        ev = events.copy()
        ev["자산군"] = ev["symbol"].map(lambda s: asset_class.get(s, pf.UNCLASSIFIED))
        ev["연도"] = pd.to_datetime(ev["trade_date"]).dt.year.astype(str)
        st.markdown("**자산군·연도별 (원화)**")
        st.dataframe(ev.pivot_table(index=["자산군", "kind"], columns="연도", values="amount_krw", aggfunc="sum",
                                    fill_value=0).rename_axis(index=["자산군", "구분"]),
                     width="stretch", column_config={y: NUM for y in ev["연도"].unique()})
        st.markdown("**내역**")
        ev = ev.iloc[::-1]
        st.dataframe(pd.DataFrame({
            "날짜": ev["trade_date"], "구분": ev["kind"], "종목": ev["symbol"].map(name_of),
            "금액": money(ev["amount"], ev["currency"]), "통화": ev["currency"], "원화": ev["amount_krw"].round(0),
            "계좌": ev["account_id"].map(acc_name).fillna(""),
        }), hide_index=True, width="stretch", column_config={"금액": NUM, "원화": NUM})
        st.caption("매도 손익 = 매도금액 − 수수료 − 매도수량 × 이동평균 평단. 원화 손익에는 환차손익이 포함돼요.")

# ── 달러 환전 ─────────────────────────────────────────────
with tab_fx:
    try:
        fx_log = pf.list_fx()
    except Exception:
        st.error("환전 기록 표가 없어요. Supabase에 `supabase/003_fx_exchanges.sql`을 적용해 주세요.")
        fx_log = None
    if fx_log is not None:
        fx_ccys = sorted(set(fx_log["currency"]) | {"USD"})
        ccy = st.radio("통화", fx_ccys, horizontal=True, key="fx-ccy") if len(fx_ccys) > 1 else "USD"
        log = fx_log[fx_log["currency"] == ccy]
        now_rate = fx.get(ccy)
        summ = pf.fx_summary(log, now_rate)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("평균 환전 환율", f"{summ['avg_rate']:,.2f}" if summ["avg_rate"] else "–",
                  help="지금까지 환전한 모든 금액의 평균 (시트의 '평균환율'과 같은 방식)")
        c2.metric("현재 환율", f"{now_rate:,.2f}" if now_rate else "–",
                  f"{now_rate - summ['avg_rate']:+,.2f}원" if now_rate and summ["avg_rate"] else None,
                  delta_color="off")
        c3.metric(f"총 환전 ({ccy})", f"{summ['bought']:,.2f}", help=f"원화 ₩{summ['bought_krw']:,.0f}")
        c4.metric(f"재환전 안 한 {ccy}", f"{summ['held']:,.2f}",
                  help=f"평단 {summ['held_avg_rate']:,.2f}원 (재환전해도 평단은 그대로인 이동평균)"
                  if summ["held_avg_rate"] else None)
        if summ["sold"] or summ["unrealized"] is not None:
            parts = []
            if summ["sold"]:
                parts.append(f"재환전 {summ['sold']:,.2f} {ccy}, 환차손익 {won(summ['realized'])}")
            if summ["unrealized"] is not None:
                parts.append(f"남은 {ccy}(평단 {summ['held_avg_rate']:,.2f})를 지금 환율로 바꾸면 {won(summ['unrealized'])}")
            st.caption(" · ".join(parts))
        st.caption("환전한 달러로 산 주식은 거래 기록에 따로 있어요. 이 표는 평균 환전 환율을 보기 위한 기록이라 총 자산에는 더하지 않아요.")

        with st.expander("환전 기록 추가", expanded=log.empty):
            c1, c2, c3 = st.columns(3)
            fside = c1.radio("구분", list(pf.FX_SIDES), format_func=pf.FX_SIDES.get, key="fx-side")
            fdate = c2.date_input("날짜", today, key="fx-date")
            fccy = c3.selectbox("통화", [c for c in CURRENCIES if c != "KRW"], key="fx-new-ccy")
            c1, c2, c3 = st.columns(3)
            famt = c1.number_input(f"금액 ({fccy})", min_value=0.0, step=100.0, format="%.2f", key="fx-amt")
            frate = c2.number_input(f"환율 (원/{fccy})", min_value=0.0, value=float(fx.get(fccy, 0.0)), step=1.0,
                                    format="%.2f", key="fx-rate")
            ffee = c3.number_input("수수료 (원)", min_value=0.0, step=100.0, format="%.0f", key="fx-fee")
            c1, c2 = st.columns([1, 2])
            facc = c1.selectbox("계좌", acc_ids, format_func=lambda i: "선택 안 함" if i is None else acc_name[i],
                                key="fx-account")
            fmemo = c2.text_input("메모", placeholder="목표환율 도달, 채널 하단 터치 …", key="fx-memo")
            if famt and frate:
                st.caption(f"원화 {famt * frate:,.0f}원")
            if st.button("환전 기록 저장", type="primary", disabled=not (famt > 0 and frate > 0), key="save-fx"):
                pf.add_fx({"trade_date": fdate, "side": fside, "currency": fccy, "amount": famt, "rate": frate,
                           "fee": ffee, "account_id": facc, "memo": fmemo or None})
                for k in ("fx-amt", "fx-fee", "fx-memo"):
                    st.session_state.pop(k, None)
                st.rerun()

        if not log.empty:
            show = log.iloc[::-1]
            st.dataframe(pd.DataFrame({
                "id": show["id"], "날짜": show["trade_date"], "구분": show["side"].map(pf.FX_SIDES),
                ccy: show["amount"], "환율": show["rate"], "원화": (show["amount"] * show["rate"]).round(0),
                "수수료": show["fee"], "계좌": show["account_id"].map(acc_name).fillna(""), "메모": show["memo"].fillna(""),
            }), hide_index=True, width="stretch", column_config={
                "id": None, ccy: st.column_config.NumberColumn(format="localized"), "환율": NUM, "원화": NUM})
            with st.expander("환전 기록 삭제"):
                rows_by_id = log.set_index("id")
                fdel = st.selectbox("삭제할 기록", list(show["id"]), key="fx-del", format_func=lambda i: (
                    f"{rows_by_id.loc[i, 'trade_date']} {pf.FX_SIDES[rows_by_id.loc[i, 'side']]} "
                    f"{rows_by_id.loc[i, 'amount']:,.2f} × {rows_by_id.loc[i, 'rate']:,.2f}"))
                if st.button("삭제", key="fx-del-btn"):
                    pf.delete_fx(int(fdel))
                    st.rerun()

# ── 설정 ───────────────────────────────────────────────────
with tab_set:
    st.subheader("자산군·목표 비중")
    st.caption("시트 ‘전략’·‘포트폴리오’ 탭의 행이에요. 행을 추가·삭제하고 저장하세요. 순서대로 화면에 보여요.")
    g = st.number_input("목표 총액 (원)", min_value=0.0, value=goal_total, step=1000000.0, format="%.0f",
                        help="0이면 현재 총액 기준으로 목표금액을 계산해요")
    if g != goal_total and st.button("목표 총액 저장", key="save-goal"):
        pf.set_setting("goal_total", g)
        st.rerun()
    edited = st.data_editor(
        targets[["asset_class", "target_weight", "monthly_contribution", "target_return", "status_note", "strategy"]],
        num_rows="dynamic", hide_index=True, width="stretch", key="targets-editor",
        column_config={
            "asset_class": st.column_config.TextColumn("자산군", required=True),
            "target_weight": st.column_config.NumberColumn("목표 비중(%)", min_value=0, max_value=100, format="%.1f"),
            "monthly_contribution": st.column_config.NumberColumn("월 적립 목표(원)", min_value=0, format="localized"),
            "target_return": st.column_config.NumberColumn("목표 수익률(%)", format="%.1f"),
            "status_note": st.column_config.TextColumn("현황", width="medium"),
            "strategy": st.column_config.TextColumn("트레이딩 전략", width="large"),
        })
    wsum = pd.to_numeric(edited["target_weight"], errors="coerce").fillna(0).sum()
    st.caption(f"목표 비중 합계 {wsum:g}%" + ("" if abs(wsum - 100) < 0.01 or wsum == 0 else " (100%가 아니에요)"))
    if st.button("자산군 저장", key="save-targets"):
        pf.save_targets(edited, targets)
        st.rerun()

    st.subheader("종목")
    if assets.empty:
        st.caption("거래를 입력하면 종목이 여기에 생겨요.")
    else:
        a_edit = st.data_editor(
            assets[["symbol", "name", "currency", "asset_class", "manual_price"]], hide_index=True, width="stretch",
            key="assets-editor", disabled=["symbol", "name", "currency"],
            column_config={
                "symbol": "티커", "name": "이름", "currency": "통화",
                "asset_class": st.column_config.SelectboxColumn("자산군", options=class_options),
                "manual_price": st.column_config.NumberColumn("직접 입력 현재가", format="localized",
                                                              help="시세가 안 잡히는 종목만. 자동 시세(야후, KRX 금은 네이버)를 "
                                                                   "못 받을 때만 이 값을 써요."),
            })
        if st.button("종목 저장", key="save-assets"):
            before = assets.set_index("symbol")
            for _, r in a_edit.iterrows():
                b = before.loc[r["symbol"]]
                mp = None if pd.isna(r["manual_price"]) else float(r["manual_price"])
                bp = None if pd.isna(b["manual_price"]) else float(b["manual_price"])
                if r["asset_class"] != b["asset_class"] or mp != bp:
                    pf.update_asset(r["symbol"], r["asset_class"], mp)
            st.rerun()

    st.subheader("현금성 자산")
    st.caption("투자유보현금, RP, CD, 적금처럼 티커 없이 잔액만 관리하는 자산이에요.")
    c_edit = st.data_editor(
        cash_all[["id", "name", "asset_class", "currency", "amount", "memo"]], num_rows="dynamic", hide_index=True,
        width="stretch", key="cash-editor",
        column_config={
            "id": None, "name": st.column_config.TextColumn("이름", required=True),
            "asset_class": st.column_config.SelectboxColumn("자산군", options=class_options),
            "currency": st.column_config.SelectboxColumn("통화", options=CURRENCIES, default="KRW"),
            "amount": st.column_config.NumberColumn("잔액", format="localized"),
            "memo": "메모",
        })
    if st.button("현금성 자산 저장", key="save-cash"):
        pf.save_cash(c_edit, cash_all)
        st.rerun()

    st.subheader("계좌")
    if not accounts.empty:
        st.dataframe(accounts.rename(columns={"name": "이름", "broker": "증권사", "currency": "기본 통화"}),
                     hide_index=True, column_config={"id": None})
    with st.form("add-account", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        an = c1.text_input("계좌 이름", placeholder="ISA, 메리츠 해외, 업비트 …")
        ab = c2.text_input("증권사")
        ac = c3.selectbox("기본 통화", CURRENCIES)
        if st.form_submit_button("계좌 추가") and an.strip():
            if an.strip() in set(accounts["name"]):
                st.error("같은 이름의 계좌가 있어요.")
            else:
                pf.add_account(an.strip(), ab.strip(), ac)
                st.rerun()
    if not accounts.empty:
        used = set(tx_all["account_id"].dropna().astype(int))
        c1, c2 = st.columns([3, 1])
        d = c1.selectbox("삭제할 계좌", list(accounts["id"]), format_func=acc_name.get, key="acc-del")
        if c2.button("계좌 삭제", key="acc-del-btn", disabled=int(d) in used,
                     help="거래가 있는 계좌는 지울 수 없어요"):
            pf.delete_account(int(d))
            st.rerun()
