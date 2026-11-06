import hmac

import streamlit as st


def require_password() -> None:
    """비밀번호가 맞을 때만 앱을 보여준다. 세션 동안 로그인 상태를 유지한다."""
    if st.session_state.get("authed"):
        return

    st.title("투자 대시보드")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("들어가기")
    if ok:
        expected = st.secrets.get("APP_PASSWORD", "")
        if expected and hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        st.error("비밀번호가 맞지 않아요.")
    st.stop()
