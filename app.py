import os
import sys
import time

import streamlit as st


def _process_start() -> float:
    """이 파이썬 프로세스가 시작된 시각 (리눅스 /proc 기준, 못 읽으면 지금)."""
    try:
        with open("/proc/self/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            boot = next(int(line.split()[1]) for line in f if line.startswith("btime"))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return time.time()


def _fresh_lib() -> None:
    """Streamlit Cloud는 새 코드를 받아도 lib 모듈을 메모리에 그대로 둘 때가 있다 (→ ImportError,
    AttributeError). lib 파일이 바뀌었으면 lib 모듈을 모두 메모리에서 내려 이번 실행에서 새로 읽게 한다."""
    import lib

    loaded = {n: m for n, m in sys.modules.items() if n.startswith("lib.") and getattr(m, "__file__", None)}
    mtimes = {}
    for n, m in loaded.items():
        try:
            mtimes[n] = os.path.getmtime(m.__file__)
        except OSError:
            pass
    seen = getattr(lib, "_mtimes", None)
    if seen is None:  # 이 프로세스에서 처음: 프로세스가 뜬 뒤에 바뀐 파일이 있는지 본다
        started = _process_start()
        stale = any(t > started for t in mtimes.values())
    else:
        stale = any(seen.get(n, t) != t for n, t in mtimes.items())
    if stale:
        for n in loaded:
            sys.modules.pop(n, None)
            if hasattr(lib, n.split(".", 1)[1]):
                delattr(lib, n.split(".", 1)[1])
        mtimes = {}
    lib._mtimes = {} if stale else {**(seen or {}), **mtimes}


_fresh_lib()

from lib import ui  # noqa: E402
from lib.auth import device_panel, logout, require_password  # noqa: E402

st.set_page_config(page_title="투자 대시보드", page_icon="📈", layout="wide")
ui.apply()

require_password()

pages = [
    st.Page("views/market.py", title="오늘의 시장", icon=":material/space_dashboard:", default=True),
    st.Page("views/watchlist.py", title="관심종목·차트", icon=":material/candlestick_chart:"),
    st.Page("views/portfolio.py", title="포트폴리오", icon=":material/donut_large:"),
    st.Page("views/backtest.py", title="백테스트", icon=":material/history:"),
    st.Page("views/insight.py", title="오늘의 인사이트", icon=":material/lightbulb:"),
    st.Page("views/spending.py", title="지출관리", icon=":material/receipt_long:"),
]

nav = st.navigation(pages)

with st.sidebar:
    if st.button("로그아웃", icon=":material/logout:"):
        logout()
    device_panel()

nav.run()
