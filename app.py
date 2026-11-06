import streamlit as st

from lib.auth import require_password

st.set_page_config(page_title="투자 대시보드", page_icon="📈", layout="wide")

require_password()

pages = [
    st.Page("views/market.py", title="오늘의 시장", icon="🏠", default=True),
    st.Page("views/watchlist.py", title="관심종목·차트", icon="🕯️"),
    st.Page("views/portfolio.py", title="포트폴리오", icon="💼"),
    st.Page("views/backtest.py", title="백테스트", icon="🧪"),
    st.Page("views/insight.py", title="오늘의 인사이트", icon="💡"),
]

nav = st.navigation(pages)

with st.sidebar:
    if st.button("로그아웃"):
        st.session_state.clear()
        st.rerun()

nav.run()
