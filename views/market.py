import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import fear_greed, indicators, market_hours, pledge, ui
from lib.charting import ma_color
from lib.market import clear_cache, load_ohlc, summarize, validate_yf_symbol

UP, DOWN, FLAT = ui.UP, ui.DOWN, ui.FLAT  # 한국식: 상승 빨강, 하락 파랑

st.title("오늘의 시장")


def fmt_value(v: float, is_rate: bool) -> str:
    if is_rate:
        return f"{v:.2f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if abs(v) >= 100000 else f"{v:,.2f}"
    return f"{v:,.2f}"


MINI_DAYS = 90  # 카드에 보여줄 기간
MINI_MAS = (50, 200)
CHART_H = 120  # 카드 차트 높이 (휴대폰에서 2열×2행이 한 화면에 들어가도록)

# 휴대폰(640px 이하): 카드를 2열로 두고 여백·글자를 줄인다. Streamlit은 원래 1열로 쌓는다.
MOBILE_CSS = """
<style>
@media (max-width: 640px) {
  .st-key-mkt-grid [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 0.5rem !important; }
  .st-key-mkt-grid [data-testid="stColumn"] {
    flex: 1 1 calc(50% - 0.25rem) !important; min-width: calc(50% - 0.25rem) !important;
    max-width: calc(50% - 0.25rem) !important; width: calc(50% - 0.25rem) !important;
  }
  .st-key-mkt-grid [data-testid="stColumn"]:not(:has([data-testid="stElementContainer"])) { display: none !important; }
  .st-key-mkt-grid [class*="st-key-card-"] { padding: 0.6rem 0.65rem 0.45rem !important; }
  .st-key-mkt-grid [class*="st-key-card-"] [data-testid="stVerticalBlock"] { gap: 0.2rem !important; }
  .st-key-mkt-grid .ios-label { font-size: 0.72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .st-key-mkt-grid .ios-value { font-size: 1.15rem; }
  .st-key-mkt-grid .ios-delta { font-size: 0.75rem; }
  .st-key-mkt-grid .ios-legend { font-size: 0.66rem; }
  .st-key-mkt-grid [data-testid="stCaptionContainer"] p { font-size: 0.64rem; }
  .st-key-mkt-grid [data-testid="stHeading"] h3 { font-size: 1.05rem; padding-top: 0.3rem; }
}
</style>
"""


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
        fig.add_trace(go.Scatter(x=x, y=view["Close"], mode="lines", line=dict(color=ui.BLUE, width=1.6),
                                 name="", hovertemplate="%{x} %{y:,.3f}<extra></extra>"))
    for n, ma in mas.items():
        m = ma[ma.index >= cutoff]
        if m.notna().any():
            fig.add_trace(go.Scatter(x=x, y=m.values, mode="lines", name=f"{n}일선",
                                     line=dict(color=ma_color(n), width=1.2),
                                     hovertemplate=f"{n}일선 %{{y:,.2f}}<extra></extra>"))
    fig.update_layout(height=CHART_H, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
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
        parts.append(f"<span style='white-space:nowrap'><span style='color:{ma_color(n)}'>━</span> {n}일 {pos}</span>")
    return " &nbsp;".join(parts)


FG_BAND_COLORS = [ui.RED, ui.ORANGE, ui.GRAY, ui.GREEN, ui.GREEN]


def fear_greed_chart(score: pd.Series) -> go.Figure:
    """공포·탐욕 지수 최근 90일. 0~100 구간을 옅은 색 띠로 깔아 둔다."""
    view = score[score.index >= score.index[-1] - pd.Timedelta(days=MINI_DAYS)]
    x = view.index.strftime("%y/%m/%d")
    fig = go.Figure()
    for (lo, hi, _), color in zip(fear_greed.BANDS, FG_BAND_COLORS):
        fig.add_hrect(y0=lo, y1=min(hi, 100), fillcolor=color, opacity=0.07 if color != ui.GRAY else 0.05,
                      line_width=0, layer="below")
    fig.add_trace(go.Scatter(x=x, y=view.values, mode="lines", line=dict(color=ui.ink(), width=1.6), name="",
                             hovertemplate="%{x} %{y:.0f}<extra></extra>"))
    fig.update_layout(height=CHART_H, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      xaxis=dict(visible=False, type="category"), yaxis=dict(visible=False, range=[0, 100]),
                      hovermode="x unified", dragmode=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def fear_greed_card(name: str, symbol: str, df: pd.DataFrame) -> None:
    score = df["Close"]
    last = float(score.iloc[-1])
    prev = float(score.iloc[-2]) if len(score) > 1 else last
    change = last - prev
    color = UP if change > 0 else DOWN if change < 0 else FLAT
    arrow = "▲" if change > 0 else "▼" if change < 0 else "–"
    st.markdown(ui.stat(name, f"{last:.0f} <span style='font-size:0.95rem;font-weight:600'>"
                              f"{fear_greed.rating_ko(last)}</span>", f"{arrow} {abs(change):.0f}", color),
                unsafe_allow_html=True)
    st.plotly_chart(fear_greed_chart(score), width="stretch",
                    config={"displayModeBar": False, "scrollZoom": False}, key=f"mini-{symbol}")
    ago = []
    for label, days in (("1주 전", 7), ("1달 전", 30)):
        past = score[score.index <= score.index[-1] - pd.Timedelta(days=days)]
        if not past.empty:
            ago.append(f"<span style='white-space:nowrap'>{label} {past.iloc[-1]:.0f}</span>")
    if ago:
        st.markdown(f"<div class='ios-legend'>{' &nbsp;'.join(ago)}</div>", unsafe_allow_html=True)
    st.caption(f"{score.index[-1]:%Y-%m-%d} 기준 · CNN")


def card(name: str, symbol: str, df: pd.DataFrame | None, is_rate: bool, error: str | None) -> None:
    with ui.card(f"mkt-{symbol}"):
        if df is None or df.empty:
            st.markdown(f"**{name}**")
            st.caption(error or f"`{symbol}` 시세를 못 받았어요")
            return
        if symbol == fear_greed.SYMBOL:
            fear_greed_card(name, symbol, df)
            return
        s = summarize(df["Close"], is_rate)
        color = UP if s["change"] > 0 else DOWN if s["change"] < 0 else FLAT
        arrow = "▲" if s["change"] > 0 else "▼" if s["change"] < 0 else "–"
        delta = f"{arrow} {abs(s['bp']):.1f}bp" if is_rate else f"{arrow} {abs(s['pct']):.2f}%"
        st.markdown(ui.stat(name, fmt_value(s['last'], is_rate), delta, color), unsafe_allow_html=True)
        has_ohlc = not (df["High"] == df["Low"]).all()
        st.plotly_chart(mini_chart(df, candles=has_ohlc), width="stretch",
                        config={"displayModeBar": False, "scrollZoom": False}, key=f"mini-{symbol}")
        legend = ma_legend(df)
        st.markdown(f"<div class='ios-legend'>{legend}</div>" if legend else "",
                    unsafe_allow_html=True)
        st.caption(asof_caption(df, s["asof"], symbol))


def asof_caption(df: pd.DataFrame, day: pd.Timestamp, symbol: str) -> str:
    """카드 아래 '언제 시세인지'. 장중 실시간 시세를 붙였으면 한국 시각으로 몇 시 몇 분 시세인지 보여 준다."""
    at = df.attrs.get("asof")
    if at is not None:
        kst = pd.Timestamp(at).tz_convert("Asia/Seoul")
        return f"{kst:%m/%d %H:%M} 기준 · {market_hours.status(symbol)} · {df.attrs.get('source', '야후')}"
    return f"{day:%Y-%m-%d} 기준 · {market_hours.status(symbol)}"


SORT_CSS = """
.sortable-component { display: flex; flex-direction: column; gap: 6px; font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', sans-serif; }
.sortable-container { width: 100%; box-sizing: border-box; min-height: 48px; background: #F2F2F7; border-radius: 14px; padding: 6px; margin-bottom: 8px; }
.sortable-container-header { font-weight: 600; font-size: 14px; padding: 4px 8px; color: #1D1D1F; }
.sortable-item, .sortable-item:hover {
  background: #FFFFFF; color: #1D1D1F; border: none; border-radius: 10px; padding: 10px 12px;
  margin: 3px 0; font-size: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.06); touch-action: manipulation;
}
"""


def order_editor(visible: list[dict]) -> None:
    """카테고리 순서와 카테고리 안 지표 순서를 끌어서 바꾼다 (휴대폰은 길게 누른 뒤 끌기)."""
    from streamlit_sortables import sort_items

    groups: dict[str, list[str]] = {}
    for row in visible:
        groups.setdefault(row["group_name"] or "기타", []).append(indicators.order_label(row))
    with ui.card("mkt-order"):
        st.markdown("**① 카테고리 순서**")
        st.caption("위아래로 끌어서 옮겨요. 휴대폰은 항목을 길게 누른 뒤 끌어요.")
        group_order = sort_items(list(groups), direction="vertical", custom_style=SORT_CSS, key="sort-groups")
        group_order = [g for g in group_order if g in groups] + [g for g in groups if g not in group_order]
        st.markdown("**② 카테고리 안 지표 순서**")
        st.caption("다른 카테고리로 끌어다 놓으면 그 카테고리로 옮겨져요.")
        containers = [{"header": g, "items": groups[g]} for g in group_order]
        result = sort_items(containers, multi_containers=True, direction="vertical", custom_style=SORT_CSS,
                            key="sort-items-" + "|".join(group_order))
        with st.container(horizontal=True, gap="small"):
            st.button("순서 저장", type="primary", key="save-order", on_click=_save_order, args=(result or containers,))
            st.button("취소", key="cancel-order", on_click=_close_order)


def _close_order() -> None:
    st.session_state["mkt-reorder"] = False


def _save_order(containers: list[dict]) -> None:
    indicators.save_order(indicators.reorder_plan(settings_df.to_dict("records"), containers))
    _close_order()
    st.toast("순서를 저장했어요.")



# ── 맨 위: 오늘의 명언 → 나의 다짐 ─────────────────────────

def quotes_section() -> None:
    """오늘의 명언 2개. 아침 예약 작업이 못 만들었으면 이 화면에서 한 번 만든다."""
    from lib import insight

    today = insight.today_kst()
    try:
        quotes = insight.daily_quotes(today)
        if not quotes and st.session_state.get("mkt-quotes-made") != today:
            st.session_state["mkt-quotes-made"] = today
            with st.spinner("오늘의 명언을 고르는 중…"):
                p, m, _ = insight.load_contexts()
                quotes, _ = insight.make_quotes(today, p, m)
    except Exception:
        quotes = []
    st.header(f"오늘의 명언 · {today.month}월 {today.day}일")
    if not quotes:
        st.caption("오늘의 명언을 아직 고르지 못했어요. ‘오늘의 인사이트’ 화면에서 다시 뽑을 수 있어요.")
        return
    for i, q in enumerate(quotes):
        with ui.card(f"mq-{i}"):
            st.markdown(f"<div class='mq-text'>“{html.escape(q['quote_ko'])}”</div>", unsafe_allow_html=True)
            src = f" · {q['source']}" if q.get("source") else ""
            st.caption(f"— {q['guru']}{src}")
            meaning, note = q.get("meaning") or q.get("philosophy"), q.get("my_portfolio_note")
            if meaning or note:
                with st.expander("해설 보기"):
                    if meaning:
                        st.markdown("**투자 철학**")
                        st.write(meaning)
                    if note:
                        st.markdown("**지금 내 포트폴리오에 주는 의미**")
                        st.write(note)


PLEDGE_CSS = f"""
<style>
.mq-text {{ font-size: 1.15rem; font-weight: 600; line-height: 1.5; letter-spacing: -0.01em; }}
.pledge-text {{ font-size: 1.05rem; line-height: 1.7; white-space: pre-wrap; word-break: keep-all; }}
.pledge-add {{ color: {ui.GREEN}; }}
.pledge-del {{ color: {ui.GRAY}; text-decoration: line-through; }}
.pledge-diff {{ margin-bottom: 0.9rem; }}
</style>
"""


def _pledge_edit(on: bool) -> None:
    st.session_state["pledge-edit"] = on
    if on:
        st.session_state["pledge-text"] = pledge.current(st.session_state.get("pledge-versions") or [])


def _pledge_save() -> None:
    changed = pledge.save(st.session_state.get("pledge-text", ""))
    st.session_state["pledge-edit"] = False
    st.toast("저장했어요. 바뀐 내용을 기록에 남겼어요." if changed else "바뀐 내용이 없어 그대로 두었어요.")


def pledge_history(versions: list[dict]) -> None:
    """저장한 날짜와 그때 바뀐 줄 (최신 → 오래된 순)."""
    with st.expander(f"지난 기록 {len(versions)}개"):
        for i in range(len(versions) - 1, -1, -1):
            v = versions[i]
            prev = versions[i - 1]["text"] if i else ""
            st.markdown(f"**{pledge.saved_label(v['saved_at'])}**" + (" · 처음 작성" if not i else ""))
            changes = pledge.diff_lines(prev, v["text"])
            lines = [f"<div class='pledge-{'add' if tag == '+' else 'del'}'>{'+' if tag == '+' else '−'} "
                     f"{html.escape(body)}</div>" for tag, body in changes]
            body = "".join(lines) or "<div class='pledge-del'>줄바꿈·공백만 바뀜</div>"
            st.markdown(f"<div class='pledge-diff'>{body}</div>", unsafe_allow_html=True)
        if len(versions) > 1:
            st.divider()
            pick = st.selectbox("그때 전체 글 보기", list(range(len(versions) - 1, -1, -1)), key="pledge-pick",
                                format_func=lambda i: pledge.saved_label(versions[i]["saved_at"]))
            st.markdown(f"<div class='pledge-text'>{html.escape(versions[pick]['text'])}</div>",
                        unsafe_allow_html=True)


def pledge_section() -> None:
    st.header("나의 다짐")
    st.html(PLEDGE_CSS)
    try:
        versions = pledge.load()
    except Exception:
        st.caption("다짐을 불러오지 못했어요.")
        return
    st.session_state["pledge-versions"] = versions
    editing = st.session_state.get("pledge-edit") or not versions
    with ui.card("pledge"):
        if editing:
            if "pledge-text" not in st.session_state:
                st.session_state["pledge-text"] = pledge.current(versions)
            st.text_area("나의 다짐", key="pledge-text", height=200, label_visibility="collapsed",
                         placeholder="매일 읽으며 마음을 다잡을 나만의 투자 원칙을 적어 보세요.")
            with st.container(horizontal=True, gap="small"):
                st.button("저장", type="primary", key="pledge-save", on_click=_pledge_save)
                if versions:
                    st.button("취소", key="pledge-cancel", on_click=_pledge_edit, args=(False,))
        else:
            st.markdown(f"<div class='pledge-text'>{html.escape(pledge.current(versions))}</div>",
                        unsafe_allow_html=True)
            st.caption(f"마지막 저장 {pledge.saved_label(versions[-1]['saved_at'])}")
            st.button("수정", key="pledge-open", on_click=_pledge_edit, args=(True,))
    if versions:
        pledge_history(versions)


try:
    settings_df = indicators.load_all()
except Exception:
    st.error("DB에 연결하지 못했어요. Secrets의 SUPABASE_URL과 SUPABASE_KEY를 확인해 주세요.")
    st.stop()

quotes_section()
pledge_section()

st.header("시장 지표")
tab_view, tab_edit = st.tabs(["지표", "지표 편집"])

with tab_view:
    visible = settings_df[settings_df["is_visible"].fillna(True).astype(bool)].to_dict("records")
    if not visible:
        st.info("보이는 지표가 없어요. ‘지표 편집’에서 추가해 주세요.")
    else:
        with st.spinner("시세를 불러오는 중…"):
            data, errors = load_ohlc(visible)
        with st.container(horizontal=True, gap="medium", vertical_alignment="center"):
            if st.button("새로고침", help="열린 장은 1분마다 지금 가격을, 닫힌 장은 마감 뒤 한 번만 새로 받아요. 누르면 모두 다시 받아요."):
                clear_cache()
                st.cache_data.clear()
                st.rerun()
            reorder = st.toggle("순서 바꾸기", key="mkt-reorder",
                                help="카테고리와 지표를 끌어서 위아래로 옮겨요. 휴대폰은 길게 누른 뒤 끌어요.")
        if reorder:
            order_editor(visible)
        groups: dict[str, list[dict]] = {}
        for row in visible:
            groups.setdefault(row["group_name"] or "기타", []).append(row)
        st.html(MOBILE_CSS)
        per_row = 4
        with st.container(key="mkt-grid"):
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
            "symbol": st.column_config.TextColumn("티커", required=True, help="yfinance 티커 (예: ^GSPC, 005930.KS), ecos:KR10Y, cnn:fear_greed"),
            "source": st.column_config.SelectboxColumn("소스", options=["yfinance", "ecos", "cnn"], default="yfinance"),
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
