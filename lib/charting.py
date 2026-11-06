"""캔들차트: 가격 + 이동평균선, 거래량, RSI. 저장된 추세선·메모·구간 등락률을 함께 그린다."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

from lib import ui

UP, DOWN = ui.UP, ui.DOWN
# 5 빨강 · 20 노랑 · 50 초록 · 120 검정(다크 모드에선 흰색) · 200 분홍
MA_COLORS = {5: ui.RED, 20: ui.YELLOW, 50: ui.GREEN, 120: "#1D1D1F", 200: ui.PINK}


def ma_color(n: int) -> str:
    return ui.ink() if n == 120 else MA_COLORS[n]
INTERVALS = {"30분봉": "30m", "일봉": "1d", "주봉": "1wk", "월봉": "1mo"}
PERIODS = {"1개월": 30, "3개월": 91, "6개월": 182, "1년": 365, "3년": 1095, "5년": 1826, "10년": 3652, "최대": None}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_ohlcv(symbol: str, interval: str) -> pd.DataFrame:
    """전체 기간을 받아 두고 화면에서 기간만 자른다(긴 이동평균선 계산용)."""
    period = "60d" if interval == "30m" else "max"
    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    idx = pd.to_datetime(df.index)
    # 거래소 현지 시각 그대로 두고 시간대 정보만 뗀다 (30분봉 장중 시간 표시용)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx
    return df


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def window(df: pd.DataFrame, days: int | None) -> tuple:
    if days is None or df.empty:
        return (df.index[0], df.index[-1]) if not df.empty else (None, None)
    start = df.index[-1] - pd.Timedelta(days=days)
    return max(start, df.index[0]), df.index[-1]


def close_at(df: pd.DataFrame, t) -> float | None:
    """t 이전(같은 날 포함) 마지막 종가."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    sub = df.loc[:ts]
    return float(sub["Close"].iloc[-1]) if not sub.empty else None


def build_figure(df: pd.DataFrame, interval: str, days: int | None, mas: list[int],
                 drawings: list[dict], title: str) -> go.Figure:
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.02,
                        row_heights=[0.62, 0.18, 0.20])
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"], name="가격",
        increasing=dict(line=dict(color=UP), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN),
    ), row=1, col=1)
    for n in mas:
        fig.add_trace(go.Scatter(x=df.index, y=df["Close"].rolling(n).mean(), mode="lines",
                                 name=f"MA{n}", line=dict(color=ma_color(n), width=1.3)), row=1, col=1)
    vol_colors = [UP if c >= o else DOWN for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, name="거래량",
                         opacity=0.5, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=rsi(df["Close"]), mode="lines", name="RSI(14)",
                             line=dict(color=ui.PURPLE, width=1.3), showlegend=False), row=3, col=1)
    for level in (30, 70):
        fig.add_hline(y=level, line=dict(color=ui.GRAY, width=0.8, dash="dot"), row=3, col=1)

    x0, x1 = window(df, days)
    if x0 is not None:
        vis = df.loc[x0:x1]
        lo, hi = float(vis["Low"].min()), float(vis["High"].max())
        pad = (hi - lo) * 0.05 or hi * 0.01
        fig.update_xaxes(range=[x0, x1 + (x1 - x0) * 0.02])
        fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)
        fig.update_yaxes(range=[0, float(vis["Volume"].max()) * 1.1 or 1], row=2, col=1)

    for d in drawings:
        _draw(fig, df, d)

    breaks = [dict(bounds=["sat", "mon"])]
    if interval == "30m":
        breaks.append(dict(bounds=[16, 9], pattern="hour"))
    if interval in ("30m", "1d"):
        fig.update_xaxes(rangebreaks=breaks)
    fig.update_yaxes(range=[0, 100], row=3, col=1, title_text="RSI")
    fig.update_layout(
        title=dict(text=title, x=0, y=0.99, yanchor="top", font=dict(size=19)), height=780, margin=dict(l=10, r=10, t=80, b=10),
        xaxis_rangeslider_visible=False, hovermode="x unified", dragmode="pan",
        legend=dict(orientation="h", y=1.0, x=0, yanchor="bottom"),
        newshape=dict(line=dict(color=ui.ORANGE, width=2)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _draw(fig: go.Figure, df: pd.DataFrame, d: dict) -> None:
    pts = d["points"]
    if d["kind"] == "trendline" and len(pts) == 2:
        fig.add_shape(type="line", x0=pts[0]["t"], y0=pts[0]["p"], x1=pts[1]["t"], y1=pts[1]["p"],
                      line=dict(color=ui.ORANGE, width=2), row=1, col=1)
        if d.get("text"):
            fig.add_annotation(x=pts[1]["t"], y=pts[1]["p"], text=d["text"], showarrow=False,
                               yshift=12, font=dict(color=ui.ORANGE), row=1, col=1)
    elif d["kind"] == "text" and pts:
        fig.add_annotation(x=pts[0]["t"], y=pts[0]["p"], text=d.get("text") or "", showarrow=True,
                           arrowhead=2, ax=0, ay=-40, bgcolor=ui.tok("card"),
                           bordercolor=ui.GRAY3, borderpad=4, font=dict(color=ui.ink()), row=1, col=1)
    elif d["kind"] == "measure" and len(pts) == 2:
        p0, p1 = close_at(df, pts[0]["t"]), close_at(df, pts[1]["t"])
        if p0 is None or p1 is None:
            return
        pct = (p1 / p0 - 1) * 100
        color = UP if pct >= 0 else DOWN
        fig.add_vrect(x0=pts[0]["t"], x1=pts[1]["t"], fillcolor=color, opacity=0.08, line_width=0,
                      row=1, col=1)
        fig.add_shape(type="line", x0=pts[0]["t"], y0=p0, x1=pts[1]["t"], y1=p1,
                      line=dict(color=color, width=1.5, dash="dash"), row=1, col=1)
        fig.add_annotation(x=pts[1]["t"], y=p1, text=f"{pct:+.2f}%", showarrow=False, yshift=14, xanchor="right",
                           font=dict(color=color, size=13), row=1, col=1)


def measure_pct(df: pd.DataFrame, t0, t1) -> float | None:
    p0, p1 = close_at(df, t0), close_at(df, t1)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return (p1 / p0 - 1) * 100
