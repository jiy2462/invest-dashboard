from datetime import date, datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import btc_trading as bt
from lib import ui

BUY_C, SELL_C = ui.UP, ui.DOWN  # 한국식: 매수 빨강, 매도 파랑
PERIODS = {"6개월": 182, "1년": 365, "2년": 730, "5년": 1826, "전체": None}

# 휴대폰에서 숫자 카드 4개를 2열×2행으로
MOBILE_CSS = """
<style>
@media (max-width: 640px) {
  .st-key-btc-stats [data-testid="stHorizontalBlock"], .st-key-btc-stats2 [data-testid="stHorizontalBlock"] {
    flex-wrap: wrap !important; gap: 0.6rem !important; }
  .st-key-btc-stats [data-testid="stColumn"], .st-key-btc-stats2 [data-testid="stColumn"] {
    flex: 1 1 calc(50% - 0.3rem) !important; min-width: calc(50% - 0.3rem) !important;
    max-width: calc(50% - 0.3rem) !important; width: calc(50% - 0.3rem) !important;
  }
  .st-key-btc-stats .ios-value { font-size: 1.2rem; }
  .st-key-btc-stats2 [data-testid="stMetric"] { min-height: 0; padding: 0.7rem 0.8rem; }
  .st-key-btc-stats2 [data-testid="stMetricValue"] { font-size: 1.35rem; }
}
</style>
"""

st.title("비트코인 트레이딩")
st.html(MOBILE_CSS)

# ── 설정 ───────────────────────────────────────────────────
try:
    cfg = bt.load_settings()
    db_ok = True
except Exception:
    cfg, db_ok = bt.clean(None), False
    st.warning("DB에 연결하지 못해 기본 설정(40일/20일)으로 보여 드려요. 설정 저장은 안 돼요.")

B, S = cfg["buy_n"], cfg["sell_n"]
krw = cfg["symbol"].endswith("KRW")


def money(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"₩{v:,.0f}" if krw else f"${v:,.0f}"


def pct(v: float | None, digits: int = 1) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:+.{digits}f}%"


st.caption(f"변형 돈키안 채널: 종가가 **직전 {B}일 최고 종가**를 넘으면 매수, **직전 {S}일 최저 종가** 아래로 "
           f"내려가면 매도해요. 진입은 길게, 청산은 짧게 잡아 상승 추세는 오래 타고 하락은 빨리 끊는 전략이에요. "
           f"하루 종가 기준: {bt.BASIS[cfg['basis']]}.")

now = datetime.now(timezone.utc)
with st.spinner("비트코인 시세를 받는 중…"):
    try:
        prices, info = bt.load_prices(cfg["symbol"], cfg["basis"], now)
    except Exception as e:
        prices, info = pd.DataFrame(), {"error": str(e)}
if prices.empty or len(prices) < max(B, S) + 2:
    st.error("비트코인 시세를 받지 못했어요. 잠시 뒤 새로고침해 주세요." +
             (f" ({info['error']})" if info.get("error") else ""))
    st.stop()

sig = bt.compute_signals(prices, B, S)
partial = bool(info.get("partial"))
s = bt.status(sig, partial, B, S)
done = sig.iloc[:-1] if partial else sig  # 확정된 봉만

# ── 오늘 상태 ───────────────────────────────────────────────
nxt = bt.next_close(cfg["basis"], now)
when = f"{nxt:%m/%d %H:%M}"
with ui.card("btc-status"):
    with st.container(key="btc-stats"):
        c1, c2, c3, c4 = st.columns(4)
        if s.holding:
            gain = (s.price / s.entry_price - 1) * 100 if s.entry_price else None
            c1.markdown(ui.stat("전략 상태", "보유 중",
                            f"{s.entry_date:%y/%m/%d} 매수 · {pct(gain)}" if s.entry_date is not None else "",
                            BUY_C if (gain or 0) >= 0 else SELL_C), unsafe_allow_html=True)
        else:
            c1.markdown(ui.stat("전략 상태", "관망 (현금)", "매수 신호 기다리는 중", ui.GRAY), unsafe_allow_html=True)
        live = "진행 중 가격" if partial else f"{s.date:%m/%d} 종가"
        prev = s.close if partial else (float(done["Close"].iloc[-2]) if len(done) > 1 else None)
        chg = (s.price / prev - 1) * 100 if prev else None
        c2.markdown(ui.stat(f"현재가 · {live}", money(s.price), f"{pct(chg, 2)} (전날 종가 대비)",
                            ui.UP if (chg or 0) >= 0 else ui.DOWN), unsafe_allow_html=True)
        c3.markdown(ui.stat(f"매수선 · {B}일 최고 종가", money(s.buy_line),
                        f"지금보다 {pct((s.buy_line / s.price - 1) * 100)}", BUY_C), unsafe_allow_html=True)
        c4.markdown(ui.stat(f"매도선 · {S}일 최저 종가", money(s.sell_line),
                        f"지금보다 {pct((s.sell_line / s.price - 1) * 100)}", SELL_C), unsafe_allow_html=True)

    if s.pending == "매수":
        st.warning(f"**지금 가격 그대로 {when}에 마감하면 매수 신호**가 나요. 아직 확정은 아니에요.", icon=":material/trending_up:")
    elif s.pending == "매도":
        st.warning(f"**지금 가격 그대로 {when}에 마감하면 매도 신호**가 나요. 아직 확정은 아니에요.", icon=":material/trending_down:")
    elif s.holding:
        st.markdown(f"보유 중이에요. **{when}** 마감 종가가 **{money(s.sell_line)}** 아래면 매도 신호예요 "
                    f"(지금보다 {abs(s.gap_pct):.1f}% 하락).")
    else:
        st.markdown(f"관망 중이에요. **{when}** 마감 종가가 **{money(s.buy_line)}** 위면 매수 신호예요 "
                    f"(지금보다 {abs(s.gap_pct):.1f}% 상승).")
    last_trade = done.index[done["Entry"] | done["Exit"]]
    note = ""
    if len(last_trade):
        t = last_trade[-1]
        kind = "매수" if done.loc[t, "Entry"] else "매도"
        note = f"마지막 확정 신호: {t:%Y-%m-%d} {kind} ({money(done.loc[t, 'Close'])}) · "
    asof = info.get("asof")
    asof_txt = f" · 시세 {asof.astimezone(bt.KST):%m/%d %H:%M} 기준" if asof else ""
    st.caption(f"{note}마지막 확정 봉 {s.date:%Y-%m-%d} · 다음 마감 {when} (한국 시각){asof_txt} · {info.get('source', '')}")
    gaps = bt.gap_days(prices)
    if gaps:
        st.caption(f"⚠️ 최근 1년 시세 중 {gaps}일이 비어 있어요. 기간 계산이 그만큼 길어질 수 있어요.")


# ── 차트 ───────────────────────────────────────────────────
def chart(view: pd.DataFrame, candles: bool, all_signals: bool) -> go.Figure:
    fig = go.Figure()
    x = view.index
    fig.add_trace(go.Scatter(x=x, y=view["Lower"], mode="lines", line=dict(color=SELL_C, width=1.2, dash="dash",
                             shape="hv"), name=f"매도선 ({S}일 최저)"))
    fig.add_trace(go.Scatter(x=x, y=view["Upper"], mode="lines", line=dict(color=BUY_C, width=1.2, dash="dash",
                             shape="hv"), name=f"매수선 ({B}일 최고)", fill="tonexty",
                             fillcolor="rgba(142,142,147,0.08)"))
    if candles:
        fig.add_trace(go.Candlestick(x=x, open=view["Open"], high=view["High"], low=view["Low"], close=view["Close"],
                                     name="가격", increasing=dict(line=dict(color=ui.UP, width=1), fillcolor=ui.UP),
                                     decreasing=dict(line=dict(color=ui.DOWN, width=1), fillcolor=ui.DOWN),
                                     opacity=0.75))
    else:
        fig.add_trace(go.Scatter(x=x, y=view["Close"], mode="lines", line=dict(color=ui.ink(), width=1.6),
                                 name="종가"))
    # 보유 구간 옅게 칠하기
    pos = view["Position"]
    starts = view.index[(pos > 0) & (pos.shift(1, fill_value=0) == 0)]
    for a in starts:
        rest = view.loc[a:]
        out = rest.index[rest["Position"] == 0]
        b = out[0] if len(out) else view.index[-1]
        fig.add_vrect(x0=a, x1=b, fillcolor=BUY_C, opacity=0.06, line_width=0, layer="below")
    if all_signals:
        for col, sym, color, name in (("Buy", "triangle-up", BUY_C, "매수 조건"), ("Sell", "triangle-down", SELL_C, "매도 조건")):
            pts = view[view[col] & ~view["Entry"] & ~view["Exit"]]
            fig.add_trace(go.Scatter(x=pts.index, y=pts["Close"], mode="markers", name=name,
                                     marker=dict(symbol=sym, size=7, color=color, opacity=0.45)))
    for col, sym, color, name in (("Entry", "triangle-up", BUY_C, "매수"), ("Exit", "triangle-down", SELL_C, "매도")):
        pts = view[view[col]]
        fig.add_trace(go.Scatter(x=pts.index, y=pts["Close"], mode="markers", name=name,
                                 marker=dict(symbol=sym, size=13, color=color, line=dict(color=ui.ink(), width=0.8))))
    fig.update_layout(height=470, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified", dragmode="pan",
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(tickformat=",.0f", gridcolor=ui.tok("hairline"))
    fig.update_xaxes(gridcolor=ui.tok("hairline"))
    return fig


with ui.card("btc-chart"):
    a, b, c = st.columns([3, 2, 2])
    period = a.segmented_control("기간", list(PERIODS), default="1년", key="btc_period") or "1년"
    candles = b.segmented_control("표시", ["선", "캔들"], default="선", key="btc_style") == "캔들"
    all_sig = c.toggle("같은 방향 신호도 표시", key="btc_all", help="이미 보유 중일 때 또 나온 매수 조건, 이미 판 뒤에 또 나온 "
                       "매도 조건까지 옅게 보여 줘요. 큰 표시가 실제 매매예요.")
    days = PERIODS[period]
    view = sig if days is None else sig[sig.index >= sig.index[-1] - pd.Timedelta(days=days)]
    st.plotly_chart(chart(view, candles, all_sig), width="stretch", config={"displayModeBar": False},
                    key="btc-fig")
    st.caption("큰 ▲는 매수, ▼는 매도 (같은 방향 신호가 이어지면 처음 한 번만). 붉게 칠한 구간이 보유 기간이에요."
               + (" 마지막 봉은 아직 진행 중이에요." if partial else ""))

# ── 백테스트 ───────────────────────────────────────────────
st.subheader("이 전략으로 했다면")
first = done.index[0].date()
default_start = max(date(2018, 1, 1), first)
with ui.card("btc-bt"):
    d1, d2 = st.columns(2)
    start = d1.date_input("시작일", value=default_start, min_value=first, max_value=done.index[-1].date(),
                          key="btc_bt_start", format="YYYY-MM-DD")
    d2.caption(f"신호가 난 날 종가에 사고판다고 보고, 살 때·팔 때 각각 수수료 {cfg['fee_pct']:.2f}%를 빼요. "
               "보유하지 않는 동안 현금 이자는 넣지 않았어요.")
    try:
        res = bt.backtest(done, start=start, fee_pct=cfg["fee_pct"])
    except ValueError as e:
        st.info(str(e))
        res = None
    if res is not None:
        sx, hx = res.stats["전략"], res.stats["그냥 보유"]
        with st.container(key="btc-stats2"):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("누적 수익률", pct(sx["total"] * 100, 0), f"그냥 보유 {pct(hx['total'] * 100, 0)}",
                      delta_color="off", delta_arrow="off")
            m2.metric("연평균 수익률", pct(sx["cagr"] * 100), f"그냥 보유 {pct(hx['cagr'] * 100)}", delta_color="off", delta_arrow="off")
            m3.metric("최대 낙폭", pct(sx["mdd"] * 100), f"그냥 보유 {pct(hx['mdd'] * 100)}", delta_color="off", delta_arrow="off")
            win = f"승률 {sx['win_rate'] * 100:.0f}%" if sx.get("win_rate") is not None else "완료된 매매 없음"
            m4.metric("매매 횟수", f"{sx['trades']}번", f"{win} · 보유 {sx['exposure'] * 100:.0f}%", delta_color="off", delta_arrow="off")
        eq = res.equity
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq["전략"], name="전략", line=dict(color=ui.BLUE, width=2)))
        fig.add_trace(go.Scatter(x=eq.index, y=eq["그냥 보유"], name="그냥 보유", line=dict(color=ui.GRAY, width=1.4)))
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                          legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        lo, hi = float(eq.min().min()), float(eq.max().max())
        ticks = [v for v in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if lo / 2 <= v <= hi * 2]
        fig.update_yaxes(type="log", tickvals=ticks, ticktext=[f"{v:g}배" for v in ticks],
                         gridcolor=ui.tok("hairline"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="btc-eq")
        if sx.get("avg_win") is not None or sx.get("avg_loss") is not None:
            st.caption(f"이긴 매매 평균 {pct(sx.get('avg_win'))} · 진 매매 평균 {pct(sx.get('avg_loss'))}. "
                       "추세 추종은 자주 작게 지고 가끔 크게 버는 게 정상이에요.")

        tr = res.trades.iloc[::-1].copy()
        if not tr.empty:
            st.markdown("**매매 기록** (최근 순)")
            tr["매수가"] = tr["매수가"].map(money)
            tr["매도가"] = tr["매도가"].map(money)
            tr["매수일"] = tr["매수일"].map(str)
            tr["매도일"] = tr["매도일"].map(lambda d: "–" if d is None or pd.isna(d) else str(d))
            tr = tr[["매수일", "매도일", "수익률", "매수가", "매도가", "보유일수", "상태"]]
            st.dataframe(tr, hide_index=True, width="stretch", height=min(38 + 35 * len(tr), 420), column_config={
                "수익률": st.column_config.NumberColumn(format="%+.1f%%"),
            })

with st.expander(f"다른 기간 조합과 비교 (지금 {B}/{S}일)"):
    st.caption("매수·매도 기간을 바꿨을 때 같은 시작일부터 연평균 수익률과 최대 낙폭이에요. 표에서 한 칸만 유독 "
               "좋다면 우연일 수 있어요. 주변 값도 고르게 괜찮은 조합이 믿을 만해요.")
    if st.button("계산하기", key="btc_grid"):
        with st.spinner("계산 중…"):
            g = bt.grid(done, start, cfg["fee_pct"])
        cagr = g.pivot(index="매수(일)", columns="매도(일)", values="연평균")
        mdd = g.pivot(index="매수(일)", columns="매도(일)", values="최대 낙폭")
        g1, g2 = st.columns(2)
        g1.markdown("**연평균 수익률 (%)**")
        g1.dataframe(cagr.style.format("{:+.1f}"),
                     width="stretch")
        g2.markdown("**최대 낙폭 (%)**")
        g2.dataframe(mdd.style.format("{:.0f}"), width="stretch")
        st.caption("행 = 매수 기간, 열 = 매도 기간 (일).")

with st.expander("전략 설정"):
    with st.form("btc_cfg", border=False):
        f1, f2 = st.columns(2)
        sym = f1.selectbox("시세", list(bt.SYMBOLS), index=list(bt.SYMBOLS).index(cfg["symbol"]),
                           format_func=lambda k: f"{bt.SYMBOLS[k]} · {k}")
        basis = f2.selectbox("하루 종가 기준", list(bt.BASIS), index=list(bt.BASIS).index(cfg["basis"]),
                             format_func=bt.BASIS.get,
                             help="비트코인은 24시간 거래라 '하루 종가'를 정해야 해요. 미국 장마감 기준은 야후 "
                                  "1시간봉으로 만들고 최근 약 2년만 있어서, 그 전은 UTC 일봉으로 이어요.")
        f3, f4, f5 = st.columns(3)
        buy_n = f3.number_input("매수 기간(일)", 2, 365, value=B, step=1)
        sell_n = f4.number_input("매도 기간(일)", 2, 365, value=S, step=1)
        fee = f5.number_input("수수료(%, 한쪽)", 0.0, 5.0, value=float(cfg["fee_pct"]), step=0.01, format="%.2f")
        if st.form_submit_button("저장", type="primary", disabled=not db_ok):
            bt.save_settings({"symbol": sym, "basis": basis, "buy_n": buy_n, "sell_n": sell_n, "fee_pct": fee})
            st.toast("저장했어요.")
            st.rerun()

st.caption("투자 자문이 아니에요. 규칙에 따른 참고용 신호이며, 실제 매매와 위험 관리는 본인 판단으로 해 주세요.")
