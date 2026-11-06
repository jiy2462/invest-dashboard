"""Apple(iOS) 느낌의 화면 스타일: 색상표, 카드, 전역 CSS.

색은 .streamlit/config.toml 의 라이트/다크 테마와 맞춘 Apple 시스템 색을 쓴다.
한국식으로 상승·이익은 빨강, 하락·손실은 파랑이다.
"""
from __future__ import annotations

import streamlit as st

# Apple 시스템 색 (라이트 기준. 다크에서도 충분히 잘 보이는 값)
RED, BLUE, GREEN = "#FF3B30", "#007AFF", "#34C759"
ORANGE, YELLOW, PURPLE = "#FF9500", "#F5B400", "#AF52DE"
PINK, TEAL, INDIGO = "#EE5AAE", "#5AC8FA", "#5856D6"
GRAY, GRAY3 = "#8E8E93", "#C7C7CC"

UP, DOWN, FLAT = RED, BLUE, GRAY
PALETTE = [BLUE, ORANGE, GREEN, PURPLE, PINK, TEAL, YELLOW, INDIGO]

# 화면 바탕별 색: (라이트, 다크)
_TOKENS = {
    "bg": ("#F2F2F7", "#000000"),
    "card": ("#FFFFFF", "#1C1C1E"),
    "fill": ("rgba(118,118,128,0.12)", "rgba(118,118,128,0.24)"),
    "fill_strong": ("#E3E3E8", "#2C2C2E"),
    "ink": ("#1D1D1F", "#F5F5F7"),
    "muted": ("#6E6E73", "#98989D"),
    "hairline": ("rgba(60,60,67,0.12)", "rgba(84,84,88,0.45)"),
    "shadow": ("0 1px 2px rgba(0,0,0,0.04), 0 6px 20px rgba(0,0,0,0.05)", "none"),
    "glass": ("rgba(242,242,247,0.8)", "rgba(0,0,0,0.7)"),
    "sidebar": ("rgba(251,251,253,0.85)", "rgba(17,17,19,0.85)"),
}


def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def tok(name: str) -> str:
    light, dark = _TOKENS[name]
    return dark if is_dark() else light


def ink() -> str:
    """본문 글자색. 차트에서 '검정' 역할을 하는 선(목표 비중 표시, 120일선 등)에 쓴다."""
    return tok("ink")


def card(key: str):
    """둥근 모서리의 흰 카드. key는 화면 안에서 겹치지 않게 준다."""
    return st.container(border=True, key=f"card-{key}")


def stat(label: str, value: str, delta: str = "", color: str | None = None) -> str:
    """카드 위쪽 큰 숫자 블록(HTML)."""
    d = f"<div class='ios-delta' style='color:{color}'>{delta}</div>" if delta else ""
    return (f"<div class='ios-label'>{label}</div>"
            f"<div class='ios-value'>{value}</div>{d}")


def _css() -> str:
    t = {k: tok(k) for k in _TOKENS}
    return f"""
<style>
/* ── 바탕·글꼴 ─────────────────────────────── */
html, body, .stApp {{ -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }}
.stApp {{ background: {t['bg']}; }}
.block-container, [data-testid="stMainBlockContainer"] {{
  max-width: 1280px; padding-top: 3.25rem; padding-bottom: 4rem;
}}
h1 {{ letter-spacing: -0.025em; }}
h2, h3, h4 {{ letter-spacing: -0.015em; }}
[data-testid="stHeading"] h1 {{ padding-bottom: 0.35rem; }}
[data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {{ padding-top: 0.6rem; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: {t['muted']}; }}
[data-testid="stHeaderActionElements"] {{ display: none; }}

/* ── 상단 바: 반투명 유리 ───────────────────── */
[data-testid="stHeader"] {{
  background: {t['glass']}; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
}}

/* ── 사이드바 ─────────────────────────────── */
[data-testid="stSidebar"] > div:first-child {{
  background: {t['sidebar']};
}}
[data-testid="stSidebar"] {{ border-right: 1px solid {t['hairline']}; }}
[data-testid="stSidebarNavLink"] {{ border-radius: 10px; padding: 0.35rem 0.6rem; margin: 1px 0; }}
[data-testid="stSidebarNavLink"] span {{ font-weight: 500; letter-spacing: -0.01em; }}
[data-testid="stSidebarNavLink"][aria-current="page"] {{ background: {t['fill']}; }}
[data-testid="stSidebarNavSeparator"] {{ border-color: {t['hairline']}; }}

/* ── 카드 (ui.card, 테두리 컨테이너, 폼, 펼침, 지표) ── */
[class*="st-key-card-"], [data-testid="stForm"], [data-testid="stExpander"] details,
[data-testid="stMetric"] {{
  background: {t['card']}; border: none !important; border-radius: 18px !important;
  box-shadow: {t['shadow']};
}}
[class*="st-key-card-"] {{ padding: 1.1rem 1.15rem 0.9rem; }}
[data-testid="stForm"] {{ padding: 1.25rem 1.25rem 1rem; }}
[data-testid="stExpander"] details {{ overflow: hidden; }}
[data-testid="stExpander"] summary {{ padding: 0.8rem 1rem; font-weight: 500; }}
[data-testid="stExpander"] summary:hover {{ background: {t['fill']}; }}
[data-testid="stMetric"] {{ padding: 0.95rem 1.1rem; min-height: 8.4rem; }}
[data-testid="stMetricLabel"] p {{ color: {t['muted']}; font-size: 0.82rem; font-weight: 500; }}
[data-testid="stMetricValue"] {{ letter-spacing: -0.02em; }}
/* 카드 안의 카드는 납작하게 */
[class*="st-key-card-"] [data-testid="stExpander"] details,
[class*="st-key-card-"] [data-testid="stMetric"] {{ box-shadow: none; background: {t['fill']}; }}

/* 카드 위 숫자 블록 (ui.stat) */
.ios-label {{ font-size: 0.82rem; font-weight: 500; color: {t['muted']}; letter-spacing: -0.005em; }}
.ios-value {{ font-size: 1.6rem; font-weight: 650; letter-spacing: -0.025em; line-height: 1.2;
              font-variant-numeric: tabular-nums; margin-top: 2px; }}
.ios-delta {{ font-size: 0.88rem; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ios-legend {{ font-size: 0.74rem; color: {t['muted']}; }}

/* ── 탭: iOS 세그먼트 컨트롤 ───────────────── */
[data-testid="stTabs"] [role="tablist"] {{
  gap: 2px; background: {t['fill']}; padding: 3px; border-radius: 11px; border: none; box-shadow: none;
  width: fit-content; max-width: 100%; overflow-x: auto; margin-bottom: 0.9rem;
}}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ display: none; }}
[data-testid="stTab"] {{
  border-radius: 8px; padding: 0.32rem 1rem; height: auto; min-height: 0; border: none;
  background: transparent; transition: background .15s ease;
}}
[data-testid="stTab"] p {{ font-size: 0.88rem; font-weight: 500; color: {t['ink']}; white-space: nowrap; }}
[data-testid="stTab"][aria-selected="true"] {{
  background: {'#636366' if is_dark() else t['card']};
  box-shadow: 0 1px 3px rgba(0,0,0,0.10), 0 1px 1px rgba(0,0,0,0.04);
}}
[data-testid="stTab"][aria-selected="true"] p {{ font-weight: 600; }}

/* ── 버튼 ──────────────────────────────────── */
[data-testid^="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"] {{
  background: {t['fill']}; border: none; font-weight: 500;
}}
[data-testid^="stBaseButton-secondary"]:hover {{ background: {t['fill_strong']}; color: inherit; }}
[data-testid^="stBaseButton-primary"] {{ border: none; font-weight: 600; box-shadow: none; }}
button[kind] {{ transition: transform .08s ease, background .15s ease; }}
button[kind]:active {{ transform: scale(0.97); }}

/* ── 입력칸·표·알림 ─────────────────────────── */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{ border-radius: 10px; }}
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {{ border-radius: 14px; overflow: hidden; }}
[data-testid="stAlert"] > div {{ border-radius: 14px; }}
hr {{ border-color: {t['hairline']}; }}
[data-testid="stPlotlyChart"] {{ border-radius: 12px; }}
</style>
"""


def apply() -> None:
    """모든 화면 공통 스타일. app.py에서 한 번 부른다."""
    st.html(_css())
