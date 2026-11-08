"""Streamlit Cloud가 옛 lib 모듈을 메모리에 둔 채 새 app.py를 돌릴 때, 바뀐 파일을 새로 읽는지."""
import os
import sys

from streamlit.testing.v1 import AppTest

import lib
import lib.auth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_stale_lib_module_is_reloaded():
    before = {n: m for n, m in sys.modules.items() if n.startswith("lib.")}
    old = sys.modules["lib.auth"]
    saved = old.device_panel
    del old.device_panel                       # 옛 버전처럼 새 함수가 없는 모듈
    lib._mtimes = {"lib.auth": 0.0}            # 그 뒤로 파일이 바뀐 것처럼
    try:
        at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
        at.secrets["APP_PASSWORD"] = "pw"
        at.run()
        assert not at.exception
        assert at.text_input[0].label == "비밀번호"
        assert sys.modules["lib.auth"] is not old and hasattr(sys.modules["lib.auth"], "device_panel")
    finally:
        old.device_panel = saved
        for n, m in before.items():            # 다른 테스트가 쓰는 원래 모듈(가짜 DB 포함)로 되돌림
            sys.modules[n] = m
            setattr(lib, n.split(".", 1)[1], m)


def test_unchanged_lib_is_kept():
    import lib.ui as ui_mod

    lib._mtimes = None
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60)
    at.secrets["APP_PASSWORD"] = "pw"
    at.run()
    at.run()
    assert not at.exception
    assert sys.modules["lib.ui"] is ui_mod
