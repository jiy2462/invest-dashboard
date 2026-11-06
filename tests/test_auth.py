"""로그인 유지: 토큰 서명·만료, 새로고침(새 세션)에서 주소의 토큰으로 자동 로그인."""
import os

from streamlit.testing.v1 import AppTest

from lib import auth

HERE = os.path.dirname(__file__)
APP = """
import streamlit as st
from lib.auth import require_password, logout
require_password()
st.write("들어옴")
if st.button("로그아웃"):
    logout()
"""


def test_token_sign_and_expiry():
    t = auth.make_token("pw", now=1000)
    assert auth.check_token(t, "pw", now=2000)
    assert not auth.check_token(t, "other", now=2000)          # 비밀번호 바뀌면 무효
    assert not auth.check_token(t, "pw", now=1000 + 31 * 86400)  # 30일 지나면 만료
    assert not auth.check_token(t.split(".")[0] + ".bad", "pw", now=2000)
    assert not auth.check_token(None, "pw") and not auth.check_token("junk", "pw")


def _app():
    at = AppTest.from_string(APP, default_timeout=30)
    at.secrets["APP_PASSWORD"] = "pw"
    return at


def test_login_then_refresh_stays_logged_in():
    at = _app().run()
    assert at.text_input[0].label == "비밀번호"
    at.text_input[0].set_value("pw")
    at.button[0].click().run()
    assert not at.exception
    token = at.query_params[auth.PARAM]
    token = token[0] if isinstance(token, list) else token
    assert auth.check_token(token, "pw")

    # 새로고침 = 새 세션, 주소에는 토큰이 남아 있음
    fresh = _app()
    fresh.query_params[auth.PARAM] = token
    fresh.run()
    assert not fresh.exception
    assert any(m.value == "들어옴" for m in fresh.markdown)

    # 로그아웃하면 다시 비밀번호 화면
    fresh.button[0].click().run()
    assert any(t.label == "비밀번호" for t in fresh.text_input)


def test_wrong_or_forged_token_needs_password():
    at = _app()
    at.query_params[auth.PARAM] = auth.make_token("not-the-password")
    at.run()
    assert at.text_input[0].label == "비밀번호"
