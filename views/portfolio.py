from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import plan as pl
from lib import portfolio as pf
from lib import ui
from lib import symbols
from lib import trade_inbox as ib
from lib import valuation

UP, DOWN = ui.UP, ui.DOWN              # 한국식: 이익 빨강, 손실 파랑
BAR, INK, MUTED = ui.BLUE, ui.ink(), ui.GRAY3
CURRENCIES = ["KRW", "USD", "JPY", "EUR", "CNY", "HKD"]
MANUAL = valuation.MANUAL               # 티커 없는 자산(예: KRX 금현물)의 티커 앞머리
NEW_SEARCH, NEW_MANUAL = "__search__", "__manual__"

st.title("포트폴리오")

try:
    accounts = pf.list_accounts()
    assets = pf.list_assets()
    tx_all = pf.list_transactions()
    targets = pf.list_targets()
    cash_all = pf.list_cash()
    goal_total = float(pf.get_setting("goal_total", 0) or 0)
    plan = pl.load_plan(targets)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    auto_rules = pf.get_auto_deposits()
    deposited = pf.apply_auto_deposits(today) if auto_rules else []
    if deposited:
        cash_all = pf.list_cash()
        auto_rules = pf.get_auto_deposits()
except Exception:
    st.error("DB에 연결하지 못했어요. Secrets를 확인하고, Supabase에 `supabase/002_portfolio.sql`이 적용됐는지 확인해 주세요.")
    st.stop()

for d in deposited:
    st.toast(f"{d['name']}에 자동 적립 ₩{d['added']:,.0f}를 더했어요 ({', '.join(d['months'])}).")
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


def kdate(d, year: bool = True) -> str:
    return (f"{d.year}년 " if year else "") + f"{d.month}월 {d.day}일 ({pl.WEEKDAYS[d.weekday()]})"


# 그래프는 보기 전용: 길게 누르기·드래그·두 번 탭으로 확대되지 않게 한다.
STATIC = {"displayModeBar": False, "scrollZoom": False, "doubleClick": False, "showAxisDragHandles": False}


def no_zoom(fig: go.Figure) -> go.Figure:
    fig.update_layout(dragmode=False)
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    return fig


# 범주 색 (고정 순서, 순환하지 않음). 8번째부터는 ‘기타’로 묶는다.
PIE_COLORS = [ui.BLUE, ui.ORANGE, ui.GREEN, ui.PURPLE, ui.PINK, ui.TEAL, ui.YELLOW]
OTHER_COLOR = ui.GRAY3


def donut(parts: pd.DataFrame, total: float) -> go.Figure:
    """구성 비율 원형 그래프. parts: label, value(원). 큰 순서대로 7개 + 기타."""
    parts = parts[parts["value"] > 0].sort_values("value", ascending=False)
    if len(parts) > len(PIE_COLORS):
        head = parts.iloc[:len(PIE_COLORS) - 1]
        parts = pd.concat([head, pd.DataFrame([{"label": "기타", "value": parts["value"].iloc[len(head):].sum()}])])
    colors = PIE_COLORS[:len(parts)]
    if len(parts) == len(PIE_COLORS) and parts["label"].iloc[-1] == "기타":
        colors = PIE_COLORS[:len(parts) - 1] + [OTHER_COLOR]
    fig = go.Figure(go.Pie(
        labels=parts["label"], values=parts["value"], hole=0.58, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=ui.tok("bg"), width=2)),
        textinfo="label+percent", textposition="outside", texttemplate="%{label}<br>%{percent:.1%}",
        textfont=dict(size=12), outsidetextfont=dict(size=12), automargin=True,
        hovertemplate="%{label}<br>₩%{value:,.0f} · %{percent:.1%}<extra></extra>",
    ))
    fig.update_layout(height=380, margin=dict(l=60, r=60, t=40, b=40), showlegend=False,
                      annotations=[dict(text=f"총 평가<br><b>₩{total / 1e4:,.0f}만</b>", x=0.5, y=0.5,
                                        showarrow=False, font=dict(size=15))],
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


# ── 시세·환율 ───────────────────────────────────────────────
ccys = (set(tx_all["currency"].dropna()) | set(cash_all["currency"].dropna()) | {"USD"}) - {"KRW"}
with st.spinner("시세 불러오는 중…"):
    fx, prices, price_src = valuation.quote_prices(tx_all, cash_all, asset_info)

# ── 상단: 계좌 필터, 원화 보기 ─────────────────────────────
f1, f2 = st.columns([3, 1])
sel_acc = f1.multiselect("계좌", list(accounts["id"]), format_func=lambda i: acc_name.get(i, str(i)),
                         placeholder="전체 계좌")
in_krw = f2.toggle("원화로 보기", value=False, help="끄면 종목 통화 그대로, 켜면 원화(거래 당시 환율 기준 원가)")

tx = tx_all[tx_all["account_id"].isin(sel_acc)] if sel_acc else tx_all
cash = cash_all if not sel_acc else cash_all[cash_all["account_id"].isin(sel_acc)]
positions, events = pf.compute_positions(tx, fx)
valued = pf.value_positions(positions, prices, fx)
month_buys = pf.month_buys_by_class(tx, asset_class, today, fx)
summary = pf.class_summary(valued, asset_class, cash, targets, fx, goal_total or None, month_buys)

# 투자유보현금도 함께 비중을 계산한다(목표 비중 합이 100이 아니면 100 기준으로 맞춤).
# 저장한 적립 계획으로 매주 적립액을 계산하고, 목표보다 많은 현금 자산군은 기간 동안 나눠 투입한다.
invest = summary.copy()
_w = pd.to_numeric(invest["target_weight"], errors="coerce").fillna(0).clip(lower=0)
invest["target_weight"] = _w / _w.sum() * 100 if _w.sum() > 0 else 0.0
invest["diff_pp"] = invest["weight"] - invest["target_weight"]
cash_set = set(plan["cash_classes"])
bought_today = bool(((tx["side"] == "buy") & (tx["trade_date"] == today)).any())
sched = pl.schedule(plan, today, bought_today)
weekly = pl.weekly_plan(summary, sched, plan["cash_classes"])

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

def pick_symbol(prefix: str, allow_none: bool = False):
    """종목 고르기: 이미 있는 종목, 새 종목 검색, 티커 없는 자산 직접 등록. (티커, 통화, 새 종목 정보)

    allow_none이면 맨 앞에 '종목을 골라 주세요'를 둬서, 아무것도 안 고른 상태로 시작할 수 있다."""
    known = list(assets["symbol"])
    pick = st.selectbox("종목", ([None] if allow_none else []) + known + [NEW_SEARCH, NEW_MANUAL], key=f"{prefix}-symbol",
                        format_func=lambda s: "종목을 골라 주세요" if s is None else {
                            NEW_SEARCH: "➕ 새 종목 검색", NEW_MANUAL: "➕ 티커 없는 자산 직접 등록"}.get(
                            s, f"{name_of(s)} · {s.replace(MANUAL, '')}"))
    new_asset = None
    if pick == NEW_SEARCH:
        q = st.text_input("종목 검색", placeholder="TIGER 미국S&P500, SPYM, 005930 …", key=f"{prefix}-search")
        hits = symbols.search(q) if q else []
        if q and not hits:
            st.caption("결과가 없어요. 티커를 그대로 넣어 보세요 (예: 360750.KS, SPYM).")
        if hits:
            h = st.selectbox("검색 결과", hits, key=f"{prefix}-hits-{q}",
                             format_func=lambda h: f"{h['name']} · {h['symbol']} ({h['market']})")
            ac = st.selectbox("자산군", class_options, key=f"{prefix}-new-class")
            new_asset = {"symbol": h["symbol"], "name": h["name"], "market": symbols.market_for(h["symbol"]),
                         "currency": symbols.currency_for(h["symbol"]), "asset_class": ac}
    elif pick == NEW_MANUAL:
        c1, c2, c3 = st.columns([2, 1, 1])
        mname = c1.text_input("이름", placeholder="KRX 금현물", key=f"{prefix}-manual-name")
        mccy = c2.selectbox("통화", CURRENCIES, key=f"{prefix}-manual-ccy")
        ac = c3.selectbox("자산군", class_options, key=f"{prefix}-manual-class")
        st.caption("시세가 자동으로 안 잡히는 자산이에요. 현재가는 ‘설정 → 종목’에서 직접 넣어 주세요.")
        if mname.strip():
            new_asset = {"symbol": MANUAL + mname.strip(), "name": mname.strip(), "market": None,
                         "currency": mccy, "asset_class": ac}
    sym = new_asset["symbol"] if new_asset else (pick if pick in known else None)
    ccy = new_asset["currency"] if new_asset else ((asset_info.get(sym) or {}).get("currency") or "KRW")
    return sym, ccy, new_asset


acc_ids = [None] + list(accounts["id"])


def acc_label(i) -> str:
    return "선택 안 함" if i is None else acc_name.get(i, str(i))


try:
    inbox = ib.list_items("pending")
    inbox_ok = True
except Exception:
    inbox, inbox_ok = pd.DataFrame(columns=ib.COLUMNS), False

tab_hold, tab_buy, tab_inbox, tab_alloc, tab_tx, tab_real, tab_fx, tab_set = st.tabs(
    ["보유 현황", "매수 입력", f"알림 대기함 {len(inbox)}" if len(inbox) else "알림 대기함", "자산 배분·리밸런싱",
     "매도·배당·거래 내역", "실현손익·배당", "달러 환전", "설정"])

NUM = st.column_config.NumberColumn(format="localized")
PCT = st.column_config.NumberColumn(format="%+.2f%%")
WEIGHT = st.column_config.NumberColumn(format="%.1f%%")

# ── 보유 현황 ───────────────────────────────────────────────
with tab_hold:
    held = valued[valued["quantity"] > 0].copy() if not valued.empty else valued
    if total_value > 0:
        g1, g2 = st.columns([3, 2])
        by = g2.radio("구성 비율", ["자산군별", "종목별"], horizontal=True, key="pie-by")
        if by == "자산군별":
            parts = summary.loc[summary["value_krw"] > 0, ["asset_class", "value_krw"]].rename(
                columns={"asset_class": "label", "value_krw": "value"})
        else:
            parts = pd.concat([
                pd.DataFrame({"label": held["symbol"].map(name_of), "value": held["value_krw"]}),
                pd.DataFrame({"label": cash["name"], "value": pf.cash_krw(cash, fx)}),
            ]).groupby("label", as_index=False)["value"].sum().sort_values("value", ascending=False)
        g1.plotly_chart(donut(parts, total_value), width="stretch", config=STATIC, key="pie")
        with g2:
            legend = parts.assign(비중=parts["value"] / parts["value"].sum() * 100).sort_values("value", ascending=False)
            st.dataframe(pd.DataFrame({"": legend["label"], "금액": legend["value"].round(0), "비중": legend["비중"]}),
                         hide_index=True, width="stretch", column_config={"금액": NUM, "비중": WEIGHT})
    if held.empty and cash.empty:
        st.info("아직 보유 종목이 없어요. ‘매수 입력’ 탭에서 매수 기록을 넣거나, 아래 현금성 자산 표에 잔액을 넣어 보세요.")
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
    st.markdown("**현금성 자산** · 투자유보현금(RP·CD·예수금), 적금처럼 잔액만 관리하는 자산")
    st.caption("표에서 바로 고치세요. 맨 아래 빈 줄에 쓰면 추가, 줄 왼쪽을 골라 휴지통을 누르면 삭제돼요. "
               "다 고친 뒤 ‘현금성 자산 저장’을 누르면 포트폴리오(자산군 합계·비중)에 반영돼요.")
    cash_view = cash_all.assign(account=cash_all["account_id"].map(acc_name))[
        ["id", "name", "asset_class", "account", "currency", "amount", "memo"]]
    c_edit = st.data_editor(
        cash_view, num_rows="dynamic", hide_index=True, width="stretch", key="cash-editor",
        column_config={
            "id": None, "name": st.column_config.TextColumn("이름", required=True),
            "asset_class": st.column_config.SelectboxColumn("자산군", options=class_options,
                                                            default=pf.CASH_CLASS if pf.CASH_CLASS in class_options
                                                            else None),
            "account": st.column_config.SelectboxColumn("계좌", options=list(accounts["name"])),
            "currency": st.column_config.SelectboxColumn("통화", options=CURRENCIES, default="KRW"),
            "amount": st.column_config.NumberColumn("잔액", format="localized", min_value=0),
            "memo": "메모",
        })
    if st.button("현금성 자산 저장", key="save-cash", type="primary"):
        pf.save_cash(c_edit, cash_all, dict(zip(accounts["name"], accounts["id"])))
        st.toast("현금성 자산을 저장했어요.")
        st.rerun()
    if sel_acc:
        st.caption("계좌 필터와 상관없이 전체 현금성 자산을 편집해요.")
    cash_names = dict(zip(cash_all["id"], cash_all["name"]))
    rule_by_id = {int(r["cash_id"]): r for r in auto_rules if int(r["cash_id"]) in cash_names}
    for cid, r in rule_by_id.items():
        st.caption(f"🔁 {cash_names[cid]}: 매월 {int(r['day'])}일 ₩{float(r['amount']):,.0f} 자동 적립 "
                   f"(마지막 반영 {r['last']})")
    if not cash_all.empty:
        with st.expander("매월 자동 적립 (적금 자동이체 등)"):
            st.caption("정한 날이 지나면 앱을 열 때(또는 월말 자동 기록 때) 그 달 금액을 잔액에 한 번 더해요. "
                       "이번 달 날짜가 이미 지났으면 이번 달은 이미 들어간 것으로 보고 다음 달부터 더해요.")
            a1, a2, a3 = st.columns([2, 2, 1])
            ad_id = a1.selectbox("현금성 자산", list(cash_names), key="auto-cash", format_func=lambda i: cash_names[i])
            cur = rule_by_id.get(int(ad_id), {})
            ad_amt = a2.number_input("매월 금액 (원)", min_value=0.0, step=10000.0, format="%.0f",
                                     value=float(cur.get("amount", 100000.0)), key=f"auto-amt-{ad_id}",
                                     help="0으로 저장하면 자동 적립을 꺼요")
            ad_day = a3.number_input("매월 며칠", min_value=1, max_value=28, step=1,
                                     value=int(cur.get("day", 1)), key=f"auto-day-{ad_id}")
            if st.button("자동 적립 저장", key="auto-save", type="primary"):
                pf.set_auto_deposit(int(ad_id), ad_amt, int(ad_day), today)
                st.toast("자동 적립을 껐어요." if ad_amt <= 0 else
                         f"{cash_names[ad_id]}: 매월 {int(ad_day)}일 ₩{ad_amt:,.0f} 자동 적립을 저장했어요.")
                st.rerun()
    st.markdown("**자산군별 전략 메모**")
    st.caption("펼쳐서 내용을 고치고 저장하거나, 메모를 삭제할 수 있어요. 삭제해도 자산군과 목표 비중은 그대로예요. "
               "‘설정 → 자산군·목표 비중’ 표의 ‘전략’ 칸과 같은 내용이라 어느 쪽에서 고쳐도 함께 바뀌어요.")
    notes = targets[targets["strategy"].notna()]
    for _, t in notes.iterrows():
        cls = t["asset_class"]
        with st.expander(cls):
            # 설정 탭 표에서 전략을 바꾸면 여기 입력칸도 새 내용으로 바꾼다(입력칸이 예전 글을 들고 있지 않게).
            seen = st.session_state.setdefault("memo-seen", {})
            if seen.get(cls) != t["strategy"]:
                st.session_state[f"memo-{cls}"] = seen[cls] = t["strategy"]
            txt = st.text_area("전략", key=f"memo-{cls}", height=160, label_visibility="collapsed")
            m1, m2, _ = st.columns([1, 1, 5])
            if m1.button("저장", key=f"memo-save-{cls}", type="primary", disabled=txt == t["strategy"]):
                pf.save_strategy(cls, txt)
                st.session_state.pop("targets-editor", None)
                st.toast(f"{cls} 전략을 저장했어요.")
                st.rerun()
            if m2.button("삭제", key=f"memo-del-{cls}"):
                pf.save_strategy(cls, None)
                st.session_state.pop("targets-editor", None)
                st.toast(f"{cls} 전략 메모를 지웠어요.")
                st.rerun()
    free = [c for c in targets["asset_class"] if c not in set(notes["asset_class"])]
    if free:
        with st.expander("＋ 전략 메모 추가"):
            n_cls = st.selectbox("자산군", free, key="memo-new-class")
            n_txt = st.text_area("전략", key="memo-new-text", height=120)
            if st.button("추가", key="memo-add", disabled=not n_txt.strip()):
                pf.save_strategy(n_cls, n_txt)
                st.session_state.pop("targets-editor", None)
                st.rerun()
    elif targets.empty:
        st.caption("설정 탭에서 자산군을 먼저 만들면 메모를 쓸 수 있어요.")


# ── 매수 입력 ─────────────────────────────────────────────
with tab_buy:
    st.subheader("매수 입력")
    st.caption("저장하면 바로 보유 현황·자산 배분·이번 달 적립에 반영돼요.")
    b_sym, b_ccy, b_new = pick_symbol("buy")
    last_acc = None
    if b_sym and not tx_all.empty:
        prev = tx_all[(tx_all["symbol"] == b_sym) & tx_all["account_id"].notna()]
        last_acc = int(prev["account_id"].iloc[-1]) if not prev.empty else None
    with st.form("buy-form", clear_on_submit=True, border=True):
        c1, c2 = st.columns(2)
        b_date = c1.date_input("매수 날짜", today, key="buy-date")
        b_acc = c2.selectbox("계좌", acc_ids, format_func=acc_label, key=f"buy-account-{b_sym}",
                             index=acc_ids.index(last_acc) if last_acc in acc_ids else 0)
        c1, c2, c3 = st.columns(3)
        b_qty = c1.number_input("주식 수", min_value=0.0, step=1.0, format="%.8g", key="buy-qty")
        b_price = c2.number_input(f"주식 가격 ({b_ccy})", min_value=0.0, step=1.0, format="%.4f", key="buy-price")
        b_fx = None
        if b_ccy != "KRW":
            b_fx = c3.number_input(f"환율 (원/{b_ccy})", min_value=0.0, value=float(fx.get(b_ccy, 0.0)), step=1.0,
                                   format="%.2f", key="buy-fx", help="원화 원가 계산용. 현재 환율이 미리 들어가 있어요.")
        b_memo = st.text_input("메모", placeholder="정기 매수, 5% 하락 분할 …", key="buy-memo")
        go_buy = st.form_submit_button("매수 저장", type="primary", disabled=not b_sym)
    if go_buy:
        if b_qty <= 0 or b_price <= 0:
            st.error("주식 수와 주식 가격을 넣어 주세요.")
        else:
            if b_new:
                pf.upsert_asset(**b_new)
            pf.add_transaction({"trade_date": b_date, "account_id": b_acc, "symbol": b_sym, "side": "buy",
                                "quantity": b_qty, "price": b_price, "currency": b_ccy, "fee": 0.0,
                                "fx_rate": b_fx or None, "memo": b_memo or None})
            st.session_state["buy-saved"] = (f"{b_new['name'] if b_new else name_of(b_sym)} {b_qty:g}주 × "
                                             f"{b_price:,.4g} {b_ccy} 매수를 저장했어요.")
            st.rerun()
    if st.session_state.get("buy-saved"):
        st.success(st.session_state.pop("buy-saved"))
    recent = tx_all[tx_all["side"] == "buy"].tail(5).iloc[::-1] if not tx_all.empty else tx_all
    if not recent.empty:
        st.markdown("**최근 매수**")
        st.dataframe(pd.DataFrame({
            "날짜": recent["trade_date"], "종목": recent["symbol"].map(name_of), "주식 수": recent["quantity"],
            "가격": recent["price"], "통화": recent["currency"], "계좌": recent["account_id"].map(acc_name).fillna(""),
            "메모": recent["memo"].fillna(""),
        }), hide_index=True, width="stretch", column_config={
            "주식 수": st.column_config.NumberColumn(format="%.8g"), "가격": NUM})
        st.caption("잘못 넣었으면 ‘매도·배당·거래 내역’ 탭의 ‘거래 삭제’에서 지울 수 있어요.")


# ── 자산 배분·리밸런싱 ─────────────────────────────────────
def gap_label(d: float) -> tuple[str, str]:
    """목표 대비 차이 문구와 색. 0.5%p 안쪽은 '목표대로'."""
    if abs(d) < 0.5:
        return "목표대로", ui.GREEN
    return (f"{d:+.1f}%p 많음", ui.ORANGE) if d > 0 else (f"{d:+.1f}%p 부족", DOWN)


def weight_chart(df: pd.DataFrame) -> go.Figure:
    """자산군마다 '지금' 막대와 '목표' 막대를 나란히, 이름 아래에 얼마나 많거나 부족한지."""
    df = df.iloc[::-1].reset_index(drop=True)
    labels = []
    for c, d in zip(df["asset_class"], df["weight"] - df["target_weight"]):
        txt, color = gap_label(d)
        labels.append(f"<b>{c}</b><br><span style='color:{color}'>{txt}</span>")
    fig = go.Figure()
    fig.add_bar(y=labels, x=df["target_weight"], orientation="h", name="목표 비중",
                marker=dict(color=MUTED, cornerradius=4), text=[f"{v:.1f}%" for v in df["target_weight"]],
                textposition="outside", cliponaxis=False,
                customdata=df["asset_class"], hovertemplate="%{customdata}<br>목표 %{x:.1f}%<extra></extra>")
    fig.add_bar(y=labels, x=df["weight"], orientation="h", name="지금 비중",
                marker=dict(color=BAR, cornerradius=4), text=[f"{v:.1f}%" for v in df["weight"]],
                textposition="outside", cliponaxis=False,
                customdata=df["asset_class"], hovertemplate="%{customdata}<br>지금 %{x:.1f}%<extra></extra>")
    top = float(max(df["weight"].max(), df["target_weight"].max(), 1))
    fig.update_layout(height=80 + 64 * len(df), margin=dict(l=0, r=10, t=10, b=0), barmode="group",
                      bargap=0.28, bargroupgap=0.08,
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, traceorder="reversed"),
                      xaxis=dict(ticksuffix="%", range=[0, top * 1.25], gridcolor="rgba(128,128,128,.15)",
                                 zeroline=False),
                      yaxis=dict(tickfont=dict(size=12)),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return no_zoom(fig)


def snapshot_chart(snaps: pd.DataFrame, by_class: bool) -> go.Figure:
    """월별 기록 꺾은선: 합계(평가금액·투자원금) 또는 자산군별 평가금액."""
    fig = go.Figure()
    if by_class:
        piv = snaps.pivot_table(index="month", columns="asset_class", values="value_krw", aggfunc="sum").sort_index()
        order = list(dict.fromkeys(list(targets["asset_class"]) + list(piv.columns)))
        for i, c in enumerate(x for x in order if x in piv.columns):
            fig.add_scatter(x=piv.index, y=piv[c], mode="lines+markers", name=c,
                            line=dict(color=ui.PALETTE[i % len(ui.PALETTE)], width=2), marker=dict(size=6),
                            hovertemplate="₩%{y:,.0f}")
    else:
        tot = snaps.groupby("month")[["value_krw", "cost_krw"]].sum().reset_index()
        fig.add_scatter(x=tot["month"], y=tot["value_krw"], mode="lines+markers", name="평가금액",
                        line=dict(color=BAR, width=2.5), marker=dict(size=8), hovertemplate="₩%{y:,.0f}")
        fig.add_scatter(x=tot["month"], y=tot["cost_krw"], mode="lines+markers", name="투자원금",
                        line=dict(color=MUTED, width=2, dash="dot"), marker=dict(size=6), hovertemplate="₩%{y:,.0f}")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0),
                      yaxis=dict(tickformat=",.0f", gridcolor="rgba(128,128,128,.15)"),
                      xaxis=dict(tickformat="%Y-%m", dtick="M1", hoverformat="%Y년 %m월"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return no_zoom(fig)


def trade_table(r: pd.DataFrame, key: str, now_label: str = "현재") -> None:
    """자산군별 매수/매도 표 (pf.rebalance 결과)."""
    out = pd.DataFrame({
        "자산군": r["asset_class"], "매수/매도": r["trade_krw"].round(0),     # 휴대폰에서도 바로 보이게 앞쪽에
        f"{now_label} 비중": r["weight"], "목표 비중": r["target_weight_norm"],
        f"{now_label} 금액": r["value_krw"].round(0), "이후 금액": r["after_krw"].round(0),
        "이후 비중": r["after_weight"],
    })
    st.dataframe(out, hide_index=True, width="stretch", key=key, column_config={
        f"{now_label} 금액": NUM, "이후 금액": NUM,
        "매수/매도": st.column_config.NumberColumn(format="localized", help="+ 매수, − 매도 (원)"),
        f"{now_label} 비중": WEIGHT, "이후 비중": WEIGHT, "목표 비중": WEIGHT})


def trade_lines(r: pd.DataFrame) -> list[str]:
    buys, sells = r[r["trade_krw"] > 0.5], r[r["trade_krw"] < -0.5]
    msg = []
    if not sells.empty:
        msg.append("**매도** " + ", ".join(f"{a} ₩{-v:,.0f}" for a, v in zip(sells["asset_class"], sells["trade_krw"])))
    if not buys.empty:
        msg.append("**매수** " + ", ".join(f"{a} ₩{v:,.0f}" for a, v in zip(buys["asset_class"], buys["trade_krw"])))
    return msg


with tab_alloc:
    if summary.empty:
        st.info("‘설정’ 탭에서 자산군과 목표 비중을 정하면 여기서 비교와 리밸런싱을 볼 수 있어요.")
    else:
        has_target = float(invest["target_weight"].sum()) > 0
        raw_wsum = float(pd.to_numeric(targets["target_weight"], errors="coerce").fillna(0).sum())
        st.subheader("자산 배분")
        # 보유도 목표도 없는 자산군(예: 다 판 자산군)은 그래프·표에서 뺀다.
        invest = invest[(invest["value_krw"] > 0) | (invest["target_weight"] > 0)].reset_index(drop=True)
        if invest.empty:
            st.caption("보유 자산이 없어요.")
        else:
            st.plotly_chart(weight_chart(invest), width="stretch", config=STATIC, key="weights")
            st.caption("자산군마다 위 파란 막대가 지금 비중, 아래 회색 막대가 목표 비중이에요. 이름 아래 글자는 "
                       "목표보다 얼마나 많은지(주황)·부족한지(파랑)예요. 투자유보현금도 함께 계산해요.")
            if has_target and abs(raw_wsum - 100) > 0.01:
                st.caption(f"목표 비중 합이 {raw_wsum:g}%라서 100% 기준으로 다시 계산했어요.")
            table = pd.DataFrame({
                "자산군": invest["asset_class"], "평가금액": invest["value_krw"], "투자원금": invest["cost_krw"],
                "손익": invest["pnl_krw"], "수익률": invest["pnl_pct"], "현재 비중": invest["weight"],
                "목표 비중": invest["target_weight"], "차이": invest["diff_pp"],
                "목표금액": invest["target_amount"], "달성률": invest["progress"],
                "목표 수익률": invest["target_return"], "목표 수익금": invest["target_profit"],
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

        # ── 정기 리밸런싱: 매년 2월·8월 마지막 주 월요일 ──
        st.subheader("리밸런싱")
        nxt, after = pl.rebalance_dates(today, 2)
        dday = (nxt - today).days
        with ui.card("rebalance-next"):
            st.markdown(ui.stat("다음 리밸런싱", kdate(nxt),
                            ("오늘이에요" if dday == 0 else f"D-{dday}") + f" · 그다음 {kdate(after)}",
                            ui.ORANGE if dday <= 7 else ui.tok("muted")), unsafe_allow_html=True)
            st.caption("매년 2월과 8월의 마지막 주 월요일에, 투자유보현금을 포함한 전체 자산을 목표 비중으로 되돌려요. "
                       "많은 자산군을 팔아(현금은 꺼내) 부족한 자산군을 사요.")
        if invest.empty or not has_target:
            st.info("목표 비중을 먼저 정해 주세요.")
        else:
            r1, r2 = st.columns([2, 1])
            basis = r1.segmented_control(
                "계산 기준", ["지금 평가액", "리밸런싱 날 예상"], default="지금 평가액", key="rebal-basis",
                help="‘리밸런싱 날 예상’은 그날까지 적립 계획대로 매주 샀다고 보고(시세는 지금 그대로) 계산해요.")
            extra = r2.number_input("함께 넣을 돈 (원)", min_value=0.0, value=0.0, step=100000.0, format="%.0f",
                                    key="rebal-extra", help="리밸런싱하는 날 추가로 넣을 목돈이나 유보현금. 없으면 0")
            projected = basis == "리밸런싱 날 예상"
            base = pl.project(invest, weekly, sched, nxt) if projected else invest
            rb = pf.rebalance(base, extra, allow_sell=True)
            trade_table(rb, "rebal-table", "예상" if projected else "현재")
            lines = trade_lines(rb)
            if lines:
                for line in lines:
                    st.markdown(line)
                sold = float(-rb.loc[rb["trade_krw"] < 0, "trade_krw"].sum())
                bought = float(rb.loc[rb["trade_krw"] > 0, "trade_krw"].sum())
                st.caption(f"매도 합계 ₩{sold:,.0f} · 매수 합계 ₩{bought:,.0f}"
                           + (f" (함께 넣을 돈 ₩{extra:,.0f} 포함)" if extra else "")
                           + (" · 그날 시세로 다시 계산되니, 리밸런싱 날 이 화면을 열어 그 금액대로 하면 돼요."
                              if dday else ""))
            else:
                st.markdown("지금 비중 그대로 두면 돼요.")
            if projected:
                k = sum(1 for d in sched["remaining"] if d < nxt)
                st.caption(f"예상 금액 = 지금 평가액 + 매주 적립액 × {k}회 (리밸런싱 날 전까지 남은 매수 횟수).")
            with st.expander("종목별로 나눠 보기 (대략 수량)"):
                by_sym = pl.symbol_trades(rb, valued, asset_class)
                if by_sym.empty:
                    st.caption("매수·매도할 것이 없어요.")
                else:
                    st.dataframe(pd.DataFrame({
                        "자산군": by_sym["asset_class"],
                        "종목": by_sym["symbol"].map(lambda s: name_of(s) if isinstance(s, str) else "(보유 종목 없음)"),
                        "매수/매도": by_sym["trade_krw"].round(0),
                        "1주 가격(원)": by_sym["unit_krw"].round(0),
                        "대략 수량": by_sym["quantity"],
                    }), hide_index=True, width="stretch", key="rebal-symbols", column_config={
                        "매수/매도": st.column_config.NumberColumn(format="localized", help="+ 매수, − 매도 (원)"),
                        "1주 가격(원)": NUM, "대략 수량": st.column_config.NumberColumn(format="%+.2f")})
                    st.caption("자산군 금액을 지금 보유한 종목의 평가금액 비율대로 나눈 참고용이에요. "
                               "한 종목으로만 사거나 팔아도 자산군 금액만 맞으면 돼요.")

        st.subheader(f"{today.month}월 적립 현황")
        left_this_month = sum(1 for d in sched["remaining"] if (d.year, d.month) == (today.year, today.month))
        acc_tbl = summary[["asset_class", "month_bought"]].merge(weekly[["asset_class", "weekly"]], on="asset_class",
                                                                 how="left").fillna({"weekly": 0.0})
        acc_tbl["month_plan"] = acc_tbl["month_bought"] + acc_tbl["weekly"] * left_this_month
        acc_tbl = acc_tbl[acc_tbl["month_plan"] > 0.5]
        if acc_tbl.empty:
            st.caption("‘설정 → 적립 계획’에서 월 입금액을 넣으면 매주 적립액과 이번 달 매수 금액을 비교해 보여 줘요.")
        else:
            st.dataframe(pd.DataFrame({
                "자산군": acc_tbl["asset_class"], "매주 적립액": acc_tbl["weekly"].round(0),
                "이번 달 매수": acc_tbl["month_bought"].round(0), "이번 달 계획": acc_tbl["month_plan"].round(0),
                "진행률": (acc_tbl["month_bought"] / acc_tbl["month_plan"].replace(0, np.nan) * 100).fillna(0),
            }), hide_index=True, width="stretch", key="month-progress", column_config={
                "매주 적립액": NUM, "이번 달 매수": NUM, "이번 달 계획": NUM,
                "진행률": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%")})
            st.caption(f"이번 달 계획 = 이번 달에 이미 산 금액 + 매주 적립액 × 이번 달 남은 매수일 {left_this_month}회. "
                       "매주 적립액은 ‘설정 → 적립 계획’에서 정한 값으로 계산해요.")

        st.subheader("월별 기록")
        snaps = pf.list_snapshots()
        st.caption("매달 말일 밤 11시 59분(한국 시간)에 전체 계좌 기준 자산 상태가 자동으로 저장돼요. "
                   "지금 상태를 바로 남기고 싶으면 아래 버튼을 누르세요 (같은 달은 덮어써요).")
        if st.button(f"{today:%Y년 %m월} 기록 지금 저장", key="save-snapshot", disabled=bool(sel_acc),
                     help="전체 계좌 기준으로만 저장해요. 같은 달에 다시 누르면 덮어써요."):
            pf.save_snapshot(today, summary)
            st.toast(f"{today:%Y년 %m월} 기록을 저장했어요.")
            st.rerun()
        if snaps.empty:
            st.caption("아직 기록이 없어요. 첫 월말이 지나면 여기에 달마다 꺾은선 그래프가 그려져요.")
        else:
            snaps["month"] = pd.to_datetime(snaps["month"])
            by_class = st.segmented_control("보기", ["합계", "자산군별"], default="합계", key="snap-view",
                                            label_visibility="collapsed") == "자산군별"
            st.plotly_chart(snapshot_chart(snaps, by_class), width="stretch", config=STATIC, key="snapshots")
            with st.expander("월별 자산군 금액 표"):
                st.dataframe(snaps.pivot_table(index="asset_class", columns=snaps["month"].dt.strftime("%Y-%m"),
                                               values="value_krw", aggfunc="sum"),
                             width="stretch", column_config={c: NUM for c in snaps["month"].dt.strftime("%Y-%m")})

# ── 거래 입력·내역 ─────────────────────────────────────────
with tab_tx:
    st.subheader("매도·배당 입력")
    st.caption("매수는 ‘매수 입력’ 탭이 더 간단해요. 여기서는 매도·배당과 수수료까지 자세히 넣을 수 있어요.")
    sym, ccy, new_asset = pick_symbol("tx")

    side = st.radio("구분", list(pf.SIDES), format_func=pf.SIDES.get, horizontal=True, key="tx-side", index=1)
    c1, c2 = st.columns(2)
    tdate = c1.date_input("날짜", today, key="tx-date")
    account = c2.selectbox("계좌", acc_ids, format_func=acc_label, key="tx-account")
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

# ── 알림 대기함 ───────────────────────────────────────────
INBOX_SHOW = 10

with tab_inbox:
    st.caption("아이폰 단축어(알림 자동화)가 보낸 체결·배당 알림이 여기에 쌓여요. 내용을 확인하고 "
               "[거래내역에 넣기]를 누르면 거래 내역에 들어가요. 빈칸은 알림에서 못 읽은 값이에요.")
    if not inbox_ok:
        st.warning("알림 대기함 표를 읽지 못했어요. Supabase에 `supabase/006_trade_inbox.sql`이 적용됐는지 확인해 주세요.")
    elif inbox.empty:
        st.info("대기 중인 알림이 없어요.")
    for _, item in inbox.head(INBOX_SHOW).iterrows():
        iid = int(item["id"])
        p = f"ib{iid}"
        got = ib.received_kst(item["received_at"])
        guess = ib.parse(item["title"], item["body"], item["source"], assets, got)
        # 처음 그릴 때만 추측값을 넣는다(사람이 고친 값은 그대로 둔다).
        if guess["symbol"]:
            st.session_state.setdefault(f"{p}-symbol", guess["symbol"])
        with ui.card(f"inbox-{iid}"):
            st.markdown(f"**{ib.SOURCES.get(item['source'], item['source'])}** · "
                        f"{got:%m/%d %H:%M}" if got else f"**{ib.SOURCES.get(item['source'], item['source'])}**")
            src_label = ib.SOURCES.get(item["source"], item["source"])
            head = item["title"] if isinstance(item["title"], str) and item["title"] not in ("", src_label) else ""
            st.text((f"{head}\n" if head else "") + str(item["body"]))
            sides = list(pf.SIDES)
            i_side = st.radio("구분", sides, format_func=pf.SIDES.get, horizontal=True, key=f"{p}-side",
                              index=sides.index(guess["side"]) if guess["side"] in sides else 0)
            i_sym, i_ccy, i_new = pick_symbol(p, allow_none=True)
            c1, c2 = st.columns(2)
            i_date = c1.date_input("날짜", guess["trade_date"], key=f"{p}-date")
            acc_guess = ib.account_for(item["source"], accounts)
            i_acc = c2.selectbox("계좌", acc_ids, format_func=acc_label, key=f"{p}-account",
                                 index=acc_ids.index(acc_guess) if acc_guess in acc_ids else 0)
            c1, c2, c3, c4 = st.columns(4)
            if i_side == "dividend":
                i_qty = 0.0
                i_price = c1.number_input(f"배당금 총액 ({i_ccy})", min_value=0.0, step=1.0, format="%.4f",
                                          value=float(guess["price"] or 0.0), key=f"{p}-price")
            else:
                i_qty = c1.number_input("수량", min_value=0.0, step=1.0, format="%.8g",
                                        value=float(guess["quantity"] or 0.0), key=f"{p}-qty")
                i_price = c2.number_input(f"단가 ({i_ccy})", min_value=0.0, step=1.0, format="%.4f",
                                          value=float(guess["price"] or 0.0), key=f"{p}-price")
            i_fee = c3.number_input(f"수수료·세금 ({i_ccy})", min_value=0.0, step=1.0, format="%.4f",
                                    value=float(guess["fee"] or 0.0), key=f"{p}-fee")
            i_fx = None
            if i_ccy != "KRW":
                i_fx = c4.number_input(f"환율 (원/{i_ccy})", min_value=0.0, value=float(fx.get(i_ccy, 0.0)), step=1.0,
                                       format="%.2f", key=f"{p}-fx")
            i_ok = bool(i_sym) and i_price > 0 and (i_side == "dividend" or i_qty > 0)
            b1, b2 = st.columns(2)
            if b1.button("거래내역에 넣기", type="primary", disabled=not i_ok, key=f"{p}-save", width="stretch"):
                if i_new:
                    pf.upsert_asset(**i_new)
                new_id = pf.add_transaction({"trade_date": i_date, "account_id": i_acc, "symbol": i_sym,
                                             "side": i_side, "quantity": i_qty, "price": i_price, "currency": i_ccy,
                                             "fee": i_fee, "fx_rate": i_fx or None, "memo": "알림 자동 입력"})
                ib.mark(iid, "done", new_id)
                st.toast(f"{name_of(i_sym) if not i_new else i_new['name']} {pf.SIDES[i_side]} 기록을 저장했어요.")
                st.rerun()
            if b2.button("무시", key=f"{p}-ignore", width="stretch"):
                ib.mark(iid, "ignored")
                st.rerun()
    if len(inbox) > INBOX_SHOW:
        st.caption(f"나머지 {len(inbox) - INBOX_SHOW}건은 위 알림을 처리하면 이어서 보여요.")
    if inbox_ok:
        with st.expander("처리한 알림"):
            try:
                done = ib.list_items(None)
                done = done[done["status"] != "pending"].head(30)
            except Exception:
                done = pd.DataFrame(columns=ib.COLUMNS)
            if done.empty:
                st.caption("아직 없어요.")
            else:
                st.dataframe(pd.DataFrame({
                    "받은 때": done["received_at"].map(lambda v: f"{ib.received_kst(v):%m/%d %H:%M}"
                                                       if ib.received_kst(v) else ""),
                    "회사": done["source"].map(lambda s: ib.SOURCES.get(s, s)),
                    "상태": [ib.status_label(st_, rw) for st_, rw in zip(done["status"], done["raw"])],
                    "내용": done["body"].astype(str).str.replace("\n", " ").str.slice(0, 80),
                }), hide_index=True, width="stretch")
                ignored = done[done["status"] == "ignored"]
                if not ignored.empty:
                    st.caption("‘자동 거름’은 증권사 알림인데 체결·배당 말을 못 찾아 대기함에 안 올린 거예요. "
                               "체결 알림이 여기 있으면 아래에서 되돌려 주세요.")
                    back = st.selectbox("무시한 알림 되돌리기", list(ignored["id"]), key="ib-restore",
                                        format_func=lambda i: str(ignored.set_index("id").loc[i, "body"])[:40])
                    if st.button("대기함으로 되돌리기", key="ib-restore-btn"):
                        ib.mark(int(back), "pending")
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
    # ── 적립 계획: 몇 개월 뒤 목표 비중이 되도록 매주 얼마씩 살지 ──
    st.subheader("적립 계획")
    st.caption("몇 개월 뒤에 아래 표의 목표 비중이 되도록, 매주 정한 요일에 자산군마다 얼마씩 사면 되는지 계산해요. "
               "넣을 값은 목표 기간, 월 입금액 목표, 추가 목돈, 그리고 아래 표의 자산군별 목표 비중이에요.")
    with ui.card("dca-plan"):
        p1, p2, p3, p4 = st.columns(4)
        in_months = p1.number_input("목표 기간 (개월 뒤)", min_value=1, max_value=120, step=1,
                                    value=int(plan["months"]), key="plan-months")
        in_weekday = p2.selectbox("매수 요일", list(range(7)), index=int(plan["weekday"]), key="plan-weekday",
                                  format_func=lambda i: f"매주 {pl.WEEKDAYS[i]}요일")
        in_monthly = p3.number_input("월 입금액 목표 (원)", min_value=0.0, step=100000.0, format="%.0f",
                                     value=float(plan["monthly_deposit"]), key="plan-monthly",
                                     help="매달 새로 넣을 수 있는 돈의 합계 (이미 가진 투자유보현금 제외)")
        in_lump = p4.number_input("추가 목돈 (원)", min_value=0.0, step=1000000.0, format="%.0f",
                                  value=float(plan["lump_sum"]), key="plan-lump",
                                  help="한 번에 생겨 더 넣을 수 있는 돈. 남은 매수 횟수에 고르게 나눠 넣어요. 없으면 0")
        in_cash = st.multiselect(
            "나눠 투입할 현금 자산군", class_names, key="plan-cash",
            default=[c for c in plan["cash_classes"] if c in class_names],
            help="다른 자산군과 함께 비중을 계산하되, 목표 비중보다 많이 있는 금액(목표 0%면 전부)을 "
                 "목표 기간 동안 매수일마다 똑같이 나눠 꺼내 부족한 자산군을 사요.")
        live = {**plan, "months": int(in_months), "weekday": int(in_weekday), "monthly_deposit": float(in_monthly),
                "lump_sum": float(in_lump), "cash_classes": list(in_cash)}
        plan_changed = any(live[k] != plan[k] for k in ("months", "weekday", "monthly_deposit", "lump_sum")) \
            or set(live["cash_classes"]) != {c for c in plan["cash_classes"] if c in class_names}
        if plan_changed or plan["start"] is None:
            live["start"] = None       # 저장하면 오늘부터 새로 시작하므로, 미리 보기도 오늘부터로 계산
        if st.button("적립 계획 저장", key="save-plan", type="primary",
                     disabled=not plan_changed and plan["start"] is not None):
            pl.save_plan(live, today)
            st.toast("적립 계획을 저장했어요. 오늘부터 새 계획으로 계산해요.")
            st.rerun()
        plan_box = st.container()      # 계산 결과 (아래 목표 비중 표의 값까지 읽은 뒤 채운다)

    st.subheader("자산군·목표 비중")
    st.caption("목표 총액은 수익금을 뺀, 내가 적립할 원금의 목표예요. 표에서 행을 추가·수정하고 저장하세요. "
               "행을 지우려면 아래 ‘자산군 행 삭제’를 쓰세요. 순서대로 화면에 보여요.")
    g = st.number_input("목표 총액 (원)", min_value=0.0, value=goal_total, step=1000000.0, format="%.0f",
                        help="0이면 현재 총액 기준으로 목표금액을 계산해요")
    if g != goal_total and st.button("목표 총액 저장", key="save-goal"):
        pf.set_setting("goal_total", g)
        st.rerun()
    edited = st.data_editor(
        targets[["asset_class", "target_weight", "target_return", "strategy"]],
        num_rows="dynamic", hide_index=True, width="stretch", key="targets-editor",
        column_config={
            "asset_class": st.column_config.TextColumn("자산군", required=True),
            "target_weight": st.column_config.NumberColumn("목표 비중(%)", min_value=0, max_value=100, format="%.1f"),
            "target_return": st.column_config.NumberColumn("목표 수익률(%)", format="%.1f"),
            "strategy": st.column_config.TextColumn("전략", width="large"),
        })
    e_w = pd.to_numeric(edited["target_weight"], errors="coerce").fillna(0)
    wsum = float(e_w.sum())
    st.caption(f"목표 비중 합계 {wsum:g}% (투자유보현금 포함)"
               + ("" if abs(wsum - 100) < 0.01 or wsum == 0 else " · 100%가 아니라서 100% 기준으로 다시 계산해요"))

    # 적립 계획 결과: 위 입력값과 방금 표에 쓴 목표 비중(저장 전이어도)으로 계산해 위 카드에 채운다.
    saved_w = dict(zip(targets["asset_class"], pd.to_numeric(targets["target_weight"], errors="coerce").fillna(0)))
    live_w = {str(c).strip(): float(w) for c, w in zip(edited["asset_class"], e_w) if str(c or "").strip()}
    weights_changed = live_w != {k: float(v) for k, v in saved_w.items()}
    s_live = summary.copy()
    s_live["target_weight"] = s_live["asset_class"].map(live_w).fillna(0.0)
    new_rows = [c for c in live_w if c not in set(s_live["asset_class"])]
    if new_rows:
        s_live = pd.concat([s_live, pd.DataFrame({"asset_class": new_rows, "value_krw": 0.0,
                                                  "target_weight": [live_w[c] for c in new_rows]})],
                           ignore_index=True)
    sched_live = pl.schedule(live, today, bought_today)
    weekly_live = pl.weekly_plan(s_live, sched_live, live["cash_classes"])
    with plan_box:
        n_left, n_all = len(sched_live["remaining"]), len(sched_live["dates"])
        if plan["start"] is None:
            st.caption("아직 저장한 적립 계획이 없어요. 값을 정하고 ‘적립 계획 저장’을 누르면 오늘부터 시작해요. "
                       "아래는 지금 값으로 계산한 미리 보기예요.")
        elif plan_changed or weights_changed:
            st.caption("⚠️ 저장하지 않은 변경이 있어요. 아래는 바뀐 값으로 계산한 미리 보기예요"
                       + (" (계획을 저장하면 오늘부터 새로 시작해요)." if plan_changed else " (‘자산군 저장’을 눌러야 반영돼요)."))
        if not n_left:
            st.info(f"목표일({kdate(sched_live['end'])})이 지났어요. 새 목표 기간을 정하고 ‘적립 계획 저장’을 눌러 주세요.")
        elif float(s_live["target_weight"].sum()) <= 0:
            st.info("아래 표에서 자산군별 목표 비중을 먼저 정해 주세요.")
        else:
            cash_out = float(weekly_live["cash_out"].sum())
            cash_label = "·".join(c for c in live["cash_classes"]
                                  if float(weekly_live.loc[weekly_live["asset_class"] == c, "cash_out"].sum()) > 0.5)
            st.markdown(ui.stat(
                f"매주 {pl.WEEKDAYS[live['weekday']]}요일에 살 금액 (합계)",
                f"₩{sched_live['per_buy'] + cash_out:,.0f}",
                (f"새로 넣는 돈 ₩{sched_live['per_buy']:,.0f} + {cash_label}에서 꺼내는 돈 ₩{cash_out:,.0f}<br>"
                 if cash_out > 0.5 else "")
                + f"다음 매수일 {kdate(sched_live['next'], year=False)} · 남은 매수 {n_left}회 / 전체 {n_all}회 · "
                f"목표일 {kdate(sched_live['end'])}", ui.tok("muted")), unsafe_allow_html=True)
            show = weekly_live[(weekly_live["value_krw"] > 0) | (weekly_live["target_weight"] > 0)]
            st.dataframe(pd.DataFrame({
                "자산군": show["asset_class"], "매주 적립액": show["weekly"].round(0),   # 휴대폰에서도 바로 보이게 앞쪽에
                "현재 비중": show["weight"], "목표 비중": show["target_weight"],
                "현재 평가액": show["value_krw"].round(0), "남은 기간 합계": show["total_buy"].round(0),
                "목표일 예상 비중": show["end_weight"],
            }), hide_index=True, width="stretch", key="plan-table", column_config={
                "현재 평가액": NUM, "남은 기간 합계": NUM, "현재 비중": WEIGHT, "목표 비중": WEIGHT,
                "목표일 예상 비중": WEIGHT,
                "매주 적립액": st.column_config.NumberColumn(
                    format="localized", help="매수일마다 이 자산군에 살 금액 (원). −는 그 현금 자산군에서 꺼내 쓸 금액")})
            over = show[(show["end_weight"] - show["target_weight"]).abs() > 0.05]
            if not over.empty:
                big = over[over["end_weight"] > over["target_weight"]]
                st.caption("ℹ️ " + ", ".join(f"{a}(예상 {e:.1f}% / 목표 {t:.1f}%)" for a, e, t in
                                             zip(big["asset_class"], big["end_weight"], big["target_weight"]))
                           + "은(는) 이미 목표보다 많아서, 팔지 않고 적립만으로는 목표일에도 목표 비중까지 내려오지 않아요. "
                             "나머지 차이는 리밸런싱 날에 맞춰요.")
            st.caption(
                f"매주 합계 = (월 입금액 ₩{live['monthly_deposit']:,.0f} × {live['months']}개월 + 목돈 "
                f"₩{live['lump_sum']:,.0f}) ÷ 전체 매수 {n_all}회. 지금 평가액에 남은 적립금 "
                f"₩{sched_live['remaining_money']:,.0f}을 더한 총액이 목표 비중이 되도록 부족한 자산군에 나눠요. "
                + (f"{cash_label}은 목표 비중을 넘는 ₩{cash_out * n_left:,.0f}을 남은 {n_left}회에 똑같이 나눠 "
                   f"꺼내 써요(매주 ₩{cash_out:,.0f}). " if cash_out > 0.5 else "")
                + "시세가 바뀌면 열 때마다 자산군별 금액을 다시 계산하고, 오늘 매수를 기록하면 다음 매수일 기준으로 넘어가요.")
    st.caption("자산군 이름을 바꾸면 그 자산군의 종목·현금성 자산도 새 이름으로 옮겨져요. "
               "‘전략’ 칸은 ‘보유 현황’ 맨 아래 ‘자산군별 전략 메모’와 같은 내용이에요.")
    if st.button("자산군 저장", key="save-targets", type="primary"):
        pf.save_targets(edited, targets)
        st.session_state.pop("targets-editor", None)
        st.toast("자산군·목표 비중을 저장했어요.")
        st.rerun()
    if not targets.empty:
        with st.expander("자산군 행 삭제"):
            st.caption("지울 자산군을 고르고 삭제를 누르세요. 그 행의 목표 비중·목표 수익률·전략이 함께 "
                       "지워져요. 거래 기록은 그대로예요.")
            del_cls = st.multiselect("삭제할 자산군", class_names, key="targets-del", placeholder="자산군 선택")
            if del_cls:
                gone = set(del_cls)
                used = [name_of(s) for s, c in asset_class.items() if c in gone]
                used += [str(n) for n, c in zip(cash_all["name"], cash_all["asset_class"]) if c in gone]
                if used:
                    st.warning(f"이 자산군에 속한 종목·현금 {len(used)}개({', '.join(used[:5])}"
                               f"{' 등' if len(used) > 5 else ''})는 지워지지 않고 ‘{pf.UNCLASSIFIED}’로 옮겨져요. "
                               "아래 ‘종목’ 표나 ‘보유 현황’의 현금성 자산 표에서 다른 자산군으로 바꿀 수 있어요.")
            if st.button(f"선택한 {len(del_cls)}개 행 삭제" if del_cls else "삭제", key="targets-del-btn",
                         disabled=not del_cls):
                pf.delete_targets(del_cls)
                for k in ("targets-editor", "targets-del"):
                    st.session_state.pop(k, None)
                st.toast(f"{', '.join(del_cls)} 행을 삭제했어요.")
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
