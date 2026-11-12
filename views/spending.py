from datetime import datetime

import pandas as pd
import streamlit as st

from lib import spending as sp
from lib import ui

st.title("지출관리")

# 휴대폰(640px 이하): 합계 네 칸을 2×2로 둔다(Streamlit은 원래 1열로 쌓는다).
st.html("""
<style>
@media (max-width: 640px) {
  .st-key-card-sp-summary [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 0.6rem !important; }
  .st-key-card-sp-summary [data-testid="stColumn"] {
    flex: 1 1 calc(50% - 0.3rem) !important; min-width: calc(50% - 0.3rem) !important;
    max-width: calc(50% - 0.3rem) !important; width: calc(50% - 0.3rem) !important;
  }
  .st-key-card-sp-summary .ios-value { font-size: 1.2rem; }
}
</style>
""")

try:
    df = sp.load()
except Exception:
    st.error("지출 표를 읽지 못했어요. Supabase에 `supabase/007_card_expenses.sql`이 적용됐는지 확인해 주세요.")
    st.stop()

txn = df[df["kind"].notna()] if not df.empty else df
other = df[df["kind"].isna()] if not df.empty else df
now = datetime.now(sp.KST)

# ── 보기 조건 ─────────────────────────────────────────────
this_month = now.strftime("%Y-%m")
months = sorted({this_month} | ({w.strftime("%Y-%m") for w in txn["when"] if w} if not txn.empty else set()),
                reverse=True)
c1, c2, c3 = st.columns([1, 1, 1.4])
month = c1.selectbox("월", ["전체"] + months, index=months.index(this_month) + 1, key="sp-month",
                     format_func=lambda m: m if m == "전체" else f"{m[:4]}년 {int(m[5:])}월")
card = c2.selectbox("카드", ["전체", "shinhan", "hyundai", "other"], key="sp-card",
                    format_func=lambda c: c if c == "전체" else sp.CARDS[c])
status = c3.segmented_control("확인", ["전체", "확인 전", "확인 완료"], default="전체", key="sp-status") or "전체"

view = txn
if not view.empty:
    if month != "전체":
        view = view[view["when"].map(lambda w: w.strftime("%Y-%m") if w else "") == month]
    if card != "전체":
        view = view[view["card"] == card]

# ── 합계 ──────────────────────────────────────────────────
s = sp.month_summary(view)
with ui.card("sp-summary"):
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(ui.stat("결제", f"{s['pay']:,.0f}원", f"{int((view['kind'] == 'pay').sum()) if not view.empty else 0}건"),
                unsafe_allow_html=True)
    k2.markdown(ui.stat("취소", f"{s['cancel']:,.0f}원" if s["cancel"] else "0원", f"{int((view['kind'] == 'cancel').sum()) if not view.empty else 0}건",
                    color=ui.BLUE), unsafe_allow_html=True)
    k3.markdown(ui.stat("순지출", f"{s['net']:,.0f}원"), unsafe_allow_html=True)
    k4.markdown(ui.stat("확인 전", f"{s['unchecked']}건", "모두 확인했어요" if s["count"] and not s["unchecked"] else "",
                    color=ui.GREEN), unsafe_allow_html=True)
    if not view.empty and (view["currency"].fillna("KRW") != "KRW").any():
        fx = view[view["currency"] != "KRW"]
        st.caption("해외 결제는 합계에서 빠져 있어요: " + ", ".join(
            f"{c} {fx.loc[(fx['currency'] == c) & (fx['kind'] == 'pay'), 'amount'].sum():,.2f}"
            for c in sorted(fx["currency"].unique())))

if not view.empty and status != "전체":
    view = view[view["checked"] == (status == "확인 완료")]

# ── 내역 표 (확인 칸·값 바로 고치기) ─────────────────────────
ver = st.session_state.setdefault("sp-ver", 0)


def _save(ids: list[int], kinds: list, amounts: list, v: int) -> None:
    state = st.session_state.get(f"sp-editor-{v}") or {}
    n = sp.apply_edits(state.get("edited_rows") or {}, ids, kinds, amounts)
    st.session_state["sp-ver"] = v + 1           # 표를 새로 그려 줄 번호가 어긋나지 않게
    if n:
        st.toast(f"{n}건 저장했어요.")


if view.empty:
    st.info("아직 결제 알림이 없어요. 아이폰 단축어 설정을 마치면 카드 결제 알림이 여기에 쌓여요."
            if txn.empty else "조건에 맞는 내역이 없어요.")
else:
    show_ccy = (view["currency"].fillna("KRW") != "KRW").any()
    table = pd.DataFrame({
        "확인": view["checked"].values,
        "거래처": view["merchant"].fillna("").values,
        "금액": view["amount"].values,
        "일시": [w.replace(tzinfo=None) if w else None for w in view["when"]],
        "구분": view["kind"].map(sp.KINDS).values,
        "카드": view["card"].map(lambda c: sp.CARDS.get(c, c)).values,
        "통화": view["currency"].fillna("KRW").values,
        "할부": view["installment"].fillna("").values,
        "메모": view["memo"].fillna("").values,
    })
    if not show_ccy:
        table = table.drop(columns="통화")
    ids = [int(i) for i in view["id"]]
    kinds, amounts = list(view["kind"]), [None if pd.isna(a) else float(a) for a in view["amount"]]
    st.data_editor(
        table, hide_index=True, width="stretch", key=f"sp-editor-{ver}", on_change=_save, args=(ids, kinds, amounts, ver),
        disabled=["카드", "통화", "할부"],
        column_config={
            "확인": st.column_config.CheckboxColumn("확인", width="small", help="확인 완료한 내역에 체크"),
            "일시": st.column_config.DatetimeColumn("일시", format="MM/DD HH:mm", width="small"),
            "카드": st.column_config.TextColumn("카드", width="small"),
            "구분": st.column_config.SelectboxColumn("구분", options=list(sp.KINDS.values()), width="small",
                                                   required=True),
            "거래처": st.column_config.TextColumn("거래처", width="medium"),
            "금액": st.column_config.NumberColumn("금액", format="localized", width="small",
                                                 help="취소는 음수(-)로 보여요. 취소 줄에는 숫자만 넣어도 -로 저장돼요."),
            "할부": st.column_config.TextColumn("할부", width="small"),
            "메모": st.column_config.TextColumn("메모", width="medium"),
        },
    )
    st.caption("확인 칸을 누르면 바로 저장돼요. 거래처·금액·일시를 고치면 그 값이 그대로 남아요. "
               "취소 금액은 음수(-)로 보여요.")

# ── 아래: 직접 추가 · 원문 · 결제 알림이 아닌 것 ─────────────
with st.expander("직접 추가 (알림이 안 온 결제)"):
    with st.form("sp-add", clear_on_submit=True):
        a1, a2 = st.columns(2)
        a_date = a1.date_input("날짜", now.date(), key="sp-add-date")
        a_time = a2.time_input("시간", now.time().replace(second=0, microsecond=0), key="sp-add-time")
        a3, a4 = st.columns(2)
        a_card = a3.selectbox("카드", list(sp.CARDS), format_func=sp.CARDS.get, key="sp-add-card")
        a_kind = a4.radio("구분", list(sp.KINDS), format_func=sp.KINDS.get, horizontal=True, key="sp-add-kind")
        a_merchant = st.text_input("거래처", key="sp-add-merchant")
        a_amount = st.number_input("금액 (원)", min_value=0, step=1000, key="sp-add-amount")
        a_memo = st.text_input("메모 (선택)", key="sp-add-memo")
        if st.form_submit_button("추가", type="primary"):
            if a_merchant.strip() and a_amount > 0:
                sp.add_manual(datetime.combine(a_date, a_time, tzinfo=sp.KST), a_card, a_kind,
                              a_merchant.strip(), float(a_amount), a_memo.strip())
                st.rerun()
            else:
                st.warning("거래처와 금액을 넣어 주세요.")

if not df.empty:
    with st.expander("알림 원문 보기 · 삭제"):
        by_id = df.set_index("id")

        def _label(i) -> str:
            r = by_id.loc[i]
            what = r["merchant"] if isinstance(r["merchant"], str) and r["merchant"] else str(r["body"])
            when = f"{r['when']:%m/%d %H:%M} · " if r["when"] else ""
            return f"{when}{sp.CARDS.get(r['card'], r['card'])} · {what.replace(chr(10), ' ')[:30]}"

        pick = st.selectbox("알림", list(df["id"]), key="sp-raw", format_func=_label)
        r = by_id.loc[pick]
        st.text((f"{r['title']}\n" if isinstance(r["title"], str) and r["title"] else "") + str(r["body"]))
        st.caption(f"받은 때 {sp.to_kst(r['received_at']):%Y-%m-%d %H:%M}" if sp.to_kst(r["received_at"]) else "")
        if st.button("이 알림 삭제", key="sp-del"):
            sp.delete(int(pick))
            st.rerun()
    if not other.empty:
        with st.expander(f"결제 알림이 아닌 것 {len(other)}건"):
            st.caption("금액을 못 찾았거나 청구·광고 안내로 보이는 알림이에요. 결제가 맞다면 위 ‘직접 추가’로 넣어 주세요.")
            st.dataframe(pd.DataFrame({
                "받은 때": [f"{sp.to_kst(v):%m/%d %H:%M}" if sp.to_kst(v) else "" for v in other["received_at"]],
                "카드": other["card"].map(lambda c: sp.CARDS.get(c, c)),
                "내용": (other["title"].fillna("") + " " + other["body"].astype(str)).str.replace("\n", " ")
                .str.strip().str.slice(0, 90),
            }), hide_index=True, width="stretch")
            if st.button("모두 삭제", key="sp-del-other"):
                for i in other["id"]:
                    sp.delete(int(i))
                st.rerun()
