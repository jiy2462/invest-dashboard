"""비밀번호 로그인. 한 번 로그인하면 30일 동안 새로고침해도 풀리지 않는다.

로그인하면 서명된 토큰을 만들어 브라우저 쿠키와 주소창(?t=...)에 같이 둔다.
새로고침하면 Streamlit 세션은 새로 시작되지만, 쿠키나 주소의 토큰이 맞으면 바로 들어간다.
토큰은 APP_PASSWORD로 서명하므로 비밀번호를 바꾸면 기존 로그인은 모두 풀린다.
"""
from __future__ import annotations

import hashlib
import hmac
import time

import streamlit as st

COOKIE = "invest_auth"
PARAM = "t"
TTL_DAYS = 30


def _secret() -> str:
    try:
        return str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        return ""


def make_token(secret: str, now: float | None = None, days: int = TTL_DAYS) -> str:
    exp = int((now or time.time()) + days * 86400)
    sig = hmac.new(secret.encode(), f"invest-dashboard:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def check_token(token: str | None, secret: str, now: float | None = None) -> bool:
    if not token or not secret or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now or time.time()):
        return False
    good = hmac.new(secret.encode(), f"invest-dashboard:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, good)


def _cookie_token() -> str | None:
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:
        return None


def _set_cookie(value: str, max_age: int) -> None:
    """브라우저에 쿠키를 심는다 (Streamlit은 쿠키 쓰기 API가 없어 작은 스크립트로)."""
    st.html(
        "<script>"
        f"document.cookie = '{COOKIE}={value}; max-age={max_age}; path=/; SameSite=Lax'"
        " + (location.protocol === 'https:' ? '; Secure' : '');"
        "</script>",
        unsafe_allow_javascript=True,
    )


def _remember(token: str) -> None:
    """주소창에 토큰을 유지한다 (페이지를 옮기면 지워질 수 있어 매번 다시 넣는다)."""
    if st.query_params.get(PARAM) != token:
        st.query_params[PARAM] = token


def require_password() -> None:
    """비밀번호가 맞을 때만 앱을 보여준다."""
    secret = _secret()
    if st.session_state.get("authed"):
        if st.session_state.get("auth_token"):
            _remember(st.session_state["auth_token"])
        if st.session_state.pop("auth_set_cookie", False):
            _set_cookie(st.session_state["auth_token"], TTL_DAYS * 86400)
        return

    # 새로고침: 쿠키나 주소의 토큰으로 자동 로그인 (방금 로그아웃한 세션은 제외)
    logged_out = st.session_state.get("logged_out", False)
    for token in () if logged_out else (_cookie_token(), st.query_params.get(PARAM)):
        if check_token(token, secret):
            st.session_state["authed"] = True
            st.session_state["auth_token"] = token
            _remember(token)
            return

    if logged_out:
        _set_cookie("", 0)
    st.title("투자 대시보드")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("들어가기")
    if ok:
        if secret and hmac.compare_digest(pw, secret):
            st.session_state.pop("logged_out", None)
            st.session_state["authed"] = True
            st.session_state["auth_token"] = make_token(secret)
            st.session_state["auth_set_cookie"] = True
            st.rerun()
        st.error("비밀번호가 맞지 않아요.")
    st.stop()


def logout() -> None:
    """쿠키와 주소의 토큰을 지우고 로그인 화면으로."""
    st.session_state.clear()
    st.session_state["logged_out"] = True  # 로그인 화면에서 쿠키를 지운다
    st.query_params.clear()
    st.rerun()
