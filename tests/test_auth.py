"""로그인 유지: 토큰 서명·만료, 새로고침(새 세션)에서 주소의 토큰으로 자동 로그인."""
import os
import time

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


def test_cookie_login_renews_token():
    old = auth.make_token("pw", now=time.time() - 20 * 86400)  # 10일 남은 토큰
    at = _app()
    at.query_params[auth.PARAM] = old
    at.run()
    assert any(m.value == "들어옴" for m in at.markdown)
    new = at.query_params[auth.PARAM]
    new = new[0] if isinstance(new, list) else new
    assert new != old and auth.check_token(new, "pw")
    assert int(new.split(".")[0]) > time.time() + 29 * 86400  # 다시 30일


def test_device_token_registered_and_revocable():
    tok = auth.add_device("pw", "아이폰")
    assert tok.startswith("d.") and auth.check_device_token(tok, "pw")
    assert not auth.check_token(tok, "pw")                        # 일반 토큰으로는 안 통함
    assert not auth.check_device_token(tok, "other")              # 비밀번호 바뀌면 무효
    device_id = tok.split(".")[1]
    forged = auth.make_device_token("pw", "deadbeefdeadbeef", int(time.time()) + 999)
    assert not auth.check_device_token(forged, "pw")              # 등록 안 된 기기
    expired = auth.make_device_token("pw", device_id, int(time.time()) - 1)
    assert not auth.check_device_token(expired, "pw")

    # 기기 주소로 새 세션 열면 바로 들어가고, 주소의 토큰은 그대로 유지
    at = _app()
    at.query_params[auth.PARAM] = tok
    at.run()
    assert any(m.value == "들어옴" for m in at.markdown)
    kept = at.query_params[auth.PARAM]
    assert (kept[0] if isinstance(kept, list) else kept) == tok

    auth.remove_device(device_id)
    again = _app()
    again.query_params[auth.PARAM] = tok
    again.run()
    assert again.text_input[0].label == "비밀번호"


PANEL_APP = """
import streamlit as st
from lib.auth import require_password, device_panel
require_password()
device_panel()
"""


def test_device_panel_creates_link():
    at = AppTest.from_string(PANEL_APP, default_timeout=30)
    at.secrets["APP_PASSWORD"] = "pw"
    at.query_params[auth.PARAM] = auth.make_token("pw")
    at.run()
    name = [t for t in at.text_input if t.label == "기기 이름"][0]
    name.set_value("아이폰")
    [b for b in at.button if b.label == "이 기기용 주소 만들기"][0].click().run()
    assert not at.exception
    devices = auth.list_devices()
    assert [d["name"] for d in devices.values()] == ["아이폰"]
    link = at.code[0].value
    tok = link.split(f"?{auth.PARAM}=")[1]
    assert auth.check_device_token(tok, "pw")
    cur = at.query_params[auth.PARAM]
    assert (cur[0] if isinstance(cur, list) else cur) == tok  # 지금 화면 주소도 기기 주소로

    [b for b in at.button if b.label == "해제"][0].click().run()
    assert auth.list_devices() == {}
    assert any(t.label == "비밀번호" for t in at.text_input)  # 지금 기기를 해제하면 로그아웃
