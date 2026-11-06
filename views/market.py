import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import indicators
from lib.market import load_series, summarize, validate_yf_symbol

UP, DOWN, FLAT = "#d62728", "#1f5fbf", "#888888"  # 한국식: 상승 빨강, 하락 파랑

st.title("오늘의 시장")


def fmt_value(v: float, is_rate: bool) -> str:
    if is_rate:
        return f"{v:.2f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if abs(v) >= 100000 else f"{v:,.2f}"
    return f"{v:,.2f}"


def sparkline(s: pd.Series, color: str) -> go.Figure:
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, mode="lines", line=dict(color=color, width=1.6),
                               hovertemplate="%{x|%m/%d} %{y:,.2f}<extra></extra>"))
    fig.update_layout(height=70, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      xaxis=dict(visible=False), yaxis=dict(visible=False),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def card(name: str, symbol: str, series: pd.Series | None, is_rate: bool, error: str | None) -> None:
    with st.container(border=True):
        if series is None:
            st.markdown(f"**{name}**")
            st.caption(error or f"`{symbol}` 시세를 못 받았어요")
            return
        s = summarize(series, is_rate)
        color = UP if s["change"] > 0 else DOWN if s["change"] < 0 else FLAT
        arrow = "▲" if s["change"] > 0 else "▼" if s["change"] < 0 else "–"
        delta = f"{arrow} {abs(s['bp']):.1f}bp" if is_rate else f"{arrow} {abs(s['pct']):.2f}%"
        st.markdown(
            f"<div style='font-size:0.85rem;opacity:.75'>{name}</div>"
            f"<div style='font-size:1.4rem;font-weight:600'>{fmt_value(s['last'], is_rate)}</div>"
            f"<div style='color:{color};font-size:0.9rem'>{delta}</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(sparkline(s["spark"], color), width="stretch",
                        config={"displayModeBar": False}, key=f"spark-{symbol}")
        st.caption(f"{s['asof']:%Y-%m-%d} 기준")


try:
    settings_df = indicators.load_all()
except Exception:
    st.error("DB에 연결하지 못했어요. Secrets의 SUPABASE_URL과 SUPABASE_KEY를 확인해 주세요.")
    st.stop()

tab_view, tab_edit = st.tabs(["지표", "지표 편집"])

with tab_view:
    visible = settings_df[settings_df["is_visible"].fillna(True).astype(bool)].to_dict("records")
    if not visible:
        st.info("보이는 지표가 없어요. ‘지표 편집’에서 추가해 주세요.")
    else:
        with st.spinner("시세를 불러오는 중…"):
            data, errors = load_series(visible)
        if st.button("새로고침", help="시세는 15분 동안 캐시돼요"):
            st.cache_data.clear()
            st.rerun()
        groups: dict[str, list[dict]] = {}
        for row in visible:
            groups.setdefault(row["group_name"] or "기타", []).append(row)
        per_row = 4
        for group, rows in groups.items():
            st.subheader(group)
            for i in range(0, len(rows), per_row):
                cols = st.columns(per_row)
                for col, row in zip(cols, rows[i:i + per_row]):
                    with col:
                        card(row["display_name"], row["symbol"], data.get(row["symbol"]),
                             is_rate=(group == "금리"), error=errors.get(row["symbol"]))

with tab_edit:
    st.caption("표에서 바로 고치고 ‘저장’을 누르세요. 행을 지우면 지표가 삭제돼요. 금리 그룹은 등락을 bp로 보여줘요.")
    edited = st.data_editor(
        settings_df,
        key="indicator_editor",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_order=["display_name", "symbol", "source", "group_name", "sort_order", "is_visible"],
        column_config={
            "id": None,
            "display_name": st.column_config.TextColumn("이름", required=True),
            "symbol": st.column_config.TextColumn("티커", required=True, help="yfinance 티커 (예: ^GSPC, 005930.KS) 또는 ecos:KR10Y"),
            "source": st.column_config.SelectboxColumn("소스", options=["yfinance", "ecos"], default="yfinance"),
            "group_name": st.column_config.TextColumn("그룹"),
            "sort_order": st.column_config.NumberColumn("순서", step=1, default=99),
            "is_visible": st.column_config.CheckboxColumn("표시", default=True),
        },
    )
    if st.button("저장", type="primary"):
        new_rows = edited[edited["id"].isna() & (edited["source"].fillna("yfinance") == "yfinance")]
        bad = [s for s in new_rows["symbol"].dropna() if not validate_yf_symbol(str(s).strip())]
        if bad:
            st.error("시세를 찾지 못한 티커가 있어요: " + ", ".join(bad))
        else:
            indicators.save_changes(settings_df, edited)
            st.success("저장했어요.")
            st.rerun()

    st.divider()
    st.markdown("**자주 쓰는 지표 빠르게 추가**")
    existing = set(settings_df["symbol"])
    options = [p for p in indicators.PRESETS if p[1] not in existing]
    if options:
        pick = st.selectbox("지표", options, format_func=lambda p: f"{p[0]} · {p[3]} ({p[1]})")
        if st.button("추가"):
            next_order = int(settings_df["sort_order"].max() or 0) + 1 if len(settings_df) else 1
            indicators.add_preset(pick[0], pick[1], pick[2], pick[3], next_order)
            st.rerun()
    else:
        st.caption("목록의 지표를 모두 추가했어요.")
