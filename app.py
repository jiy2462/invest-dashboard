import streamlit as st

from lib import ui
from lib.auth import logout, require_password

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

nav.run()
