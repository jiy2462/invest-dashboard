"""비밀번호 로그인과 로그인 유지.

1) 일반 로그인: 서명된 30일 토큰을 쿠키와 주소창(?t=...)에 둔다. 앱을 열 때마다 새 토큰으로
   갈아 끼워서 30일 안에 한 번이라도 열면 계속 유지된다.
2) 기기 주소(홈화면 앱용): iOS 홈화면 앱은 Safari와 쿠키를 따로 쓰고, 홈화면에 추가할 때의
   주소를 그대로 열기 때문에 쿠키만으로는 자주 풀린다. 그래서 1년짜리 '기기 토큰'이 든 주소를
   만들어 홈화면에 추가하게 한다. 기기 토큰은 DB(app_settings)에 등록된 기기만 통과하므로
   휴대폰을 잃어버리면 그 기기만 해제할 수 있다.

모든 토큰은 APP_PASSWORD로 서명하므로 비밀번호를 바꾸면 기존 로그인과 기기 주소가 모두 풀린다.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as _rand
import time
from datetime import datetime, timezone

import streamlit as st

COOKIE = "invest_auth"
PARAM = "t"
TTL_DAYS = 30
DEVICE_TTL_DAYS = 365
DEVICES_KEY = "auth_devices"  # app_settings: {기기 id: {"name", "created", "exp"}}


def get_client():
    from lib.db import get_client as real

    return real()


def _secret() -> str:
    try:
        return str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        return ""


def _sig(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


# ---------- 일반 30일 토큰 ----------

def make_token(secret: str, now: float | None = None, days: int = TTL_DAYS) -> str:
    exp = int((now or time.time()) + days * 86400)
    return f"{exp}.{_sig(secret, f'invest-dashboard:{exp}')}"


def check_token(token: str | None, secret: str, now: float | None = None) -> bool:
    if not token or not secret or "." not in token or token.startswith("d."):
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sig(secret, f"invest-dashboard:{exp}"))


# ---------- 기기 토큰 (홈화면 앱용) ----------

def make_device_token(secret: str, device_id: str, exp: int) -> str:
    return f"d.{device_id}.{exp}.{_sig(secret, f'invest-dashboard-device:{device_id}:{exp}')}"


def parse_device_token(token: str | None, secret: str, now: float | None = None) -> str | None:
    """서명과 만료가 맞으면 기기 id를 돌려준다 (등록 여부는 따로 확인)."""
    if not token or not secret or not token.startswith("d."):
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    _, device_id, exp_s, sig = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < (now or time.time()):
        return None
    if not hmac.compare_digest(sig, _sig(secret, f"invest-dashboard-device:{device_id}:{exp}")):
        return None
    return device_id


def list_devices() -> dict:
    rows = get_client().table("app_settings").select("*").eq("key", DEVICES_KEY).execute().data or []
    value = rows[0]["value"] if rows else None
    return value if isinstance(value, dict) else {}


def _save_devices(devices: dict) -> None:
    get_client().table("app_settings").upsert({"key": DEVICES_KEY, "value": devices}, on_conflict="key").execute()


def add_device(secret: str, name: str, now: float | None = None) -> str:
    now = now or time.time()
    device_id = _rand.token_hex(8)
    exp = int(now + DEVICE_TTL_DAYS * 86400)
    devices = list_devices()
    devices[device_id] = {
        "name": name.strip() or "내 기기",
        "created": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"),
        "exp": exp,
    }
    _save_devices(devices)
    return make_device_token(secret, device_id, exp)


def remove_device(device_id: str) -> None:
    devices = list_devices()
    devices.pop(device_id, None)
    _save_devices(devices)


def check_device_token(token: str | None, secret: str, now: float | None = None) -> bool:
    device_id = parse_device_token(token, secret, now)
    if not device_id:
        return False
    try:
        return device_id in list_devices()
    except Exception:
        return False  # DB를 못 읽으면 기기 주소는 막고 비밀번호로만 들어오게 한다


# ---------- 브라우저 쪽 ----------

def _cookie_token() -> str | None:
    try:
        return st.context.cookies.get(COOKIE)
    except Exception:
        return None


def _set_cookie(value: str, max_age: int) -> None:
    """브라우저에 토큰을 저장한다: 쿠키 + localStorage (값이 비면 둘 다 지운다).

    iOS 홈화면 앱·Streamlit Cloud에서는 쿠키가 서버까지 안 오는 경우가 있어
    localStorage에도 두고, 로그인 화면에서 _restore_script가 주소에 붙여 다시 연다.
    """
    st.html(
        "<script>"
        f"document.cookie = '{COOKIE}={value}; max-age={max_age}; path=/; SameSite=Lax'"
        " + (location.protocol === 'https:' ? '; Secure' : '');"
        f"try {{ {'localStorage.setItem' if value else 'localStorage.removeItem'}('{COOKIE}'{f", '{value}'" if value else ''}); }} catch (e) {{}}"
        "</script>",
        unsafe_allow_javascript=True,
    )


def _restore_script() -> None:
    """로그인 화면: 브라우저에 저장된 토큰이 있으면 주소(?t=)에 붙여 다시 연다 (반복 방지 30초)."""
    st.html(
        "<script>(function () {"
        "try {"
        f"  var t = localStorage.getItem('{COOKIE}');"
        f"  if (!t) {{ var m = document.cookie.match(/(?:^|; ){COOKIE}=([^;]+)/); t = m && m[1]; }}"
        "  if (!t) return;"
        "  var u = new URL(location.href);"
        f"  if (u.searchParams.get('{PARAM}') === t) return;"
        "  var last = Number(sessionStorage.getItem('invest_auth_try') || 0);"
        "  if (Date.now() - last < 30000) return;"
        "  sessionStorage.setItem('invest_auth_try', String(Date.now()));"
        f"  u.searchParams.set('{PARAM}', t);"
        "  location.replace(u.toString());"
        "} catch (e) {}"
        "})();</script>",
        unsafe_allow_javascript=True,
    )


def _remember(token: str) -> None:
    """주소창에 토큰을 유지한다 (페이지를 옮기면 지워질 수 있어 매번 다시 넣는다)."""
    if st.query_params.get(PARAM) != token:
        st.query_params[PARAM] = token


def _sign_in(token: str) -> None:
    st.session_state["authed"] = True
    st.session_state["auth_token"] = token
    st.session_state["auth_set_cookie"] = True


def require_password() -> None:
    """비밀번호가 맞을 때만 앱을 보여준다."""
    secret = _secret()
    if not st.session_state.get("authed"):
        # 새로 연 화면: 주소나 쿠키의 토큰으로 자동 로그인 (방금 로그아웃한 세션은 제외)
        logged_out = st.session_state.get("logged_out", False)
        for token in () if logged_out else (st.query_params.get(PARAM), _cookie_token()):
            if check_device_token(token, secret):
                _sign_in(token)  # 기기 주소는 그대로 유지
                break
            if check_token(token, secret):
                _sign_in(make_token(secret))  # 열 때마다 30일 연장
                break

    if st.session_state.get("authed"):
        if st.session_state.get("auth_token"):
            _remember(st.session_state["auth_token"])
        if st.session_state.pop("auth_set_cookie", False):
            _set_cookie(st.session_state["auth_token"], DEVICE_TTL_DAYS * 86400)
        return

    if st.session_state.get("logged_out"):
        _set_cookie("", 0)
    else:
        _restore_script()
    st.title("투자 대시보드")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("들어가기")
    if ok:
        if secret and hmac.compare_digest(pw, secret):
            st.session_state.pop("logged_out", None)
            _sign_in(make_token(secret))
            st.rerun()
        st.error("비밀번호가 맞지 않아요.")
    st.stop()


def logout() -> None:
    """쿠키와 주소의 토큰을 지우고 로그인 화면으로."""
    st.session_state.clear()
    st.session_state["logged_out"] = True  # 로그인 화면에서 쿠키를 지운다
    st.query_params.clear()
    st.rerun()


def app_url() -> str:
    """사용자가 보는 앱 주소 (Streamlit Cloud의 안쪽 주소 /~/+/ 는 뺀다)."""
    try:
        url = st.context.url or ""
    except Exception:
        url = ""
    url = url.replace("/~/+", "")
    return url.rstrip("/") + "/" if url else ""


def device_panel() -> None:
    """사이드바: 홈화면 앱용 기기 주소 만들기·해제."""
    secret = _secret()
    with st.expander("홈화면 앱 로그인 유지", icon=":material/phone_iphone:"):
        st.caption("이 기기 전용 주소를 만들어 홈화면에 추가하면 1년 동안 비밀번호 없이 열려요. "
                   "주소 자체가 열쇠라서 다른 사람에게 보내거나 캡처해서 공유하지 마세요.")
        with st.form("device_add", clear_on_submit=True, border=False):
            name = st.text_input("기기 이름", placeholder="예: 아이폰")
            make = st.form_submit_button("이 기기용 주소 만들기")
        if make:
            try:
                token = add_device(secret, name)
            except Exception as e:
                st.error(f"기기를 저장하지 못했어요: {e}")
            else:
                _sign_in(token)  # 지금 화면 주소에도 바로 기기 토큰을 넣는다
                st.session_state["device_link"] = f"{app_url()}?{PARAM}={token}"
                st.rerun()
        link = st.session_state.get("device_link")
        if link:
            st.success("주소를 만들었어요. Safari에서 이 주소를 연 다음 공유 → 홈 화면에 추가를 누르세요.")
            st.code(link, language=None, wrap_lines=True)
            st.link_button("새 탭에서 열기", link, icon=":material/open_in_new:")

        try:
            devices = list_devices()
        except Exception:
            devices = {}
        current = parse_device_token(st.session_state.get("auth_token"), secret)
        for device_id, info in sorted(devices.items(), key=lambda kv: kv[1].get("created", "")):
            exp = datetime.fromtimestamp(int(info.get("exp", 0)), timezone.utc).strftime("%Y-%m-%d")
            mark = " (지금 이 화면)" if device_id == current else ""
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.markdown(f"**{info.get('name', '기기')}**{mark}  \n"
                        f"<span style='font-size:0.8em;opacity:.7'>만든 날 {info.get('created', '')} · {exp}까지</span>",
                        unsafe_allow_html=True)
            if c2.button("해제", key=f"rm_dev_{device_id}"):
                remove_device(device_id)
                if device_id == current:
                    logout()
                st.session_state.pop("device_link", None)
                st.rerun()
