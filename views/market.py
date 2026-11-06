import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import indicators
from lib.charting import MA_COLORS
from lib.market import load_ohlc, summarize, validate_yf_symbol

UP, DOWN, FLAT = "#d62728", "#1f5fbf", "#888888"  # 한국식: 상승 빨강, 하락 파랑

st.title("오늘의 시장")


def fmt_value(v: float, is_rate: bool) -> str:
    if is_rate:
        return f"{v:.2f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if abs(v) >= 100000 else f"{v:,.2f}"
    return f"{v:,.2f}"


MINI_DAYS = 90  # 카드에 보여줄 기간
MINI_MAS = (50, 200)


def mini_chart(df: pd.DataFrame, candles: bool) -> go.Figure:
    """최근 90일 일봉 + 50일선·200일선. 이동평균은 전체 과거로 계산하고 화면에서만 자른다.

    x축은 날짜를 글자로 둬서 주말·휴장일 빈칸 없이 봉이 붙어 보이게 한다.
    """
    mas = {n: df["Close"].rolling(n).mean() for n in MINI_MAS}
    cutoff = df.index[-1] - pd.Timedelta(days=MINI_DAYS)
    view = df[df.index >= cutoff]
    x = view.index.strftime("%y/%m/%d")
    fig = go.Figure()
    if candles:
        fig.add_trace(go.Candlestick(
            x=x, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"], name="",
            increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
            decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
            hoverinfo="x+y",
        ))
    else:  # 종가만 있는 지표 (한국 금리)
        fig.add_trace(go.Scatter(x=x, y=view["Close"], mode="lines", line=dict(color="#555", width=1.4),
                                 name="", hovertemplate="%{x} %{y:,.3f}<extra></extra>"))
    for n, ma in mas.items():
        m = ma[ma.index >= cutoff]
        if m.notna().any():
            fig.add_trace(go.Scatter(x=x, y=m.values, mode="lines", name=f"{n}일선",
                                     line=dict(color=MA_COLORS[n], width=1.2),
                                     hovertemplate=f"{n}일선 %{{y:,.2f}}<extra></extra>"))
    fig.update_layout(height=150, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      xaxis=dict(visible=False, type="category", rangeslider=dict(visible=False)),
                      yaxis=dict(visible=False), hovermode="x unified", dragmode=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def ma_legend(df: pd.DataFrame) -> str:
    """카드 아래 범례: 지금 가격이 50일선·200일선 위인지 아래인지."""
    last = float(df["Close"].iloc[-1])
    parts = []
    for n in MINI_MAS:
        if len(df) < n:
            continue
        ma = float(df["Close"].rolling(n).mean().iloc[-1])
        pos = "위" if last >= ma else "아래"
        parts.append(f"<span style='white-space:nowrap'><span style='color:{MA_COLORS[n]}'>━</span> {n}일 {pos}</span>")
    return " &nbsp;".join(parts)


def card(name: str, symbol: str, df: pd.DataFrame | None, is_rate: bool, error: str | None) -> None:
    with st.container(border=True):
        if df is None or df.empty:
            st.markdown(f"**{name}**")
            st.caption(error or f"`{symbol}` 시세를 못 받았어요")
            return
        s = summarize(df["Close"], is_rate)
        color = UP if s["change"] > 0 else DOWN if s["change"] < 0 else FLAT
        arrow = "▲" if s["change"] > 0 else "▼" if s["change"] < 0 else "–"
        delta = f"{arrow} {abs(s['bp']):.1f}bp" if is_rate else f"{arrow} {abs(s['pct']):.2f}%"
        st.markdown(
            f"<div style='font-size:0.85rem;opacity:.75'>{name}</div>"
            f"<div style='font-size:1.4rem;font-weight:600'>{fmt_value(s['last'], is_rate)}</div>"
            f"<div style='color:{color};font-size:0.9rem'>{delta}</div>",
            unsafe_allow_html=True,
        )
        has_ohlc = not (df["High"] == df["Low"]).all()
        st.plotly_chart(mini_chart(df, candles=has_ohlc), width="stretch",
                        config={"displayModeBar": False, "scrollZoom": False}, key=f"mini-{symbol}")
        legend = ma_legend(df)
        st.markdown(f"<div style='font-size:0.75rem;opacity:.8'>{legend}</div>" if legend else "",
                    unsafe_allow_html=True)
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
            data, errors = load_ohlc(visible)
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
