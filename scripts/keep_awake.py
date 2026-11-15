"""Streamlit Community Cloud 앱이 잠들지 않게 실제 브라우저로 한 번 열어 둔다.

Community Cloud는 12시간 동안 방문이 없으면 앱을 재운다. 단순 HTTP 요청은
방문으로 치지 않거나 잠든 앱을 깨우지 못하므로, 헤드리스 Chromium으로 앱을 열고
잠든 화면("Yes, get this app back up!")이면 버튼을 눌러 깨운 뒤 로그인 화면까지 뜨는지 확인한다.

    APP_URL=https://<앱>.streamlit.app python scripts/keep_awake.py

GitHub Actions의 keep-awake.yml이 몇 시간마다 실행한다. 로그가 공개되므로 주소는 출력하지 않는다.
"""
from __future__ import annotations

import os
import re
import sys
import time

from playwright.sync_api import Page, sync_playwright

# 잠든 화면의 깨우기 버튼
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
# 앱이 실제로 뜬 표시: 로그인 화면 또는 로그인된 화면에 있는 글자
APP_READY_TEXTS = tuple(t for t in os.environ.get("KEEP_AWAKE_READY_TEXTS", "비밀번호,투자 대시보드,오늘의 시장").split(",") if t)
LOAD_TIMEOUT_S = int(os.environ.get("KEEP_AWAKE_TIMEOUT", "300"))


def _app_ready(page: Page) -> bool:
    """앱(본 페이지나 Cloud가 감싼 iframe)에 Streamlit 화면이 그려졌는지."""
    for frame in page.frames:
        try:
            if frame.locator('[data-testid="stApp"]').count() == 0:
                continue
            body = frame.locator("body").inner_text(timeout=2000)
        except Exception:
            continue
        if any(t in body for t in APP_READY_TEXTS):
            return True
    return False


def _click_wake(page: Page) -> bool:
    for frame in page.frames:
        try:
            btn = frame.get_by_role("button", name=WAKE_BUTTON)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                return True
        except Exception:
            continue
    return False


def visit(url: str, timeout_s: int = LOAD_TIMEOUT_S) -> str:
    """앱을 열어 뜰 때까지 기다린다. 결과: 'awake'(이미 깨어 있음) 또는 'woken'(깨움)."""
    woke = False
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": os.environ["CHROMIUM_PATH"]}
                                       if os.environ.get("CHROMIUM_PATH") else {}))
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _app_ready(page):
                time.sleep(5)  # 웹소켓 세션이 잠깐이라도 유지되도록
                browser.close()
                return "woken" if woke else "awake"
            if _click_wake(page):
                woke = True
                print("잠든 화면이어서 깨우기 버튼을 눌렀어요. 앱이 다시 켜지기를 기다려요.", flush=True)
            time.sleep(3)
        page_text = ""
        try:
            page_text = page.locator("body").inner_text(timeout=2000)[:300]
        except Exception:
            pass
        browser.close()
    raise TimeoutError(f"{timeout_s}초 안에 앱 화면이 뜨지 않았어요. 마지막 화면 글자: {page_text!r}")


def main() -> int:
    url = (os.environ.get("APP_URL") or "").strip()
    if not url:
        print("APP_URL 비밀값이 없어요. 저장소 Settings → Secrets and variables → Actions에 APP_URL을 추가하세요.")
        return 1
    if not url.startswith("http"):
        url = "https://" + url
    url = url.split("?", 1)[0]  # 로그인 토큰(?t=)이 섞여 있어도 떼고 연다
    for attempt in (1, 2):
        try:
            result = visit(url)
        except Exception as e:  # 한 번 더 시도
            print(f"{attempt}번째 시도 실패: {type(e).__name__}: {e}", flush=True)
            continue
        print({"awake": "앱이 이미 깨어 있었어요.", "woken": "잠든 앱을 깨웠어요."}[result])
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
