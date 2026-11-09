"""나의 다짐: 매일 읽으며 마음을 다잡는 글. 고쳐서 저장하면 바뀐 경우에만 기록을 남긴다.

DB 표를 새로 만들지 않고 app_settings 한 줄(key=my_pledge)에 담는다.
  value = {"versions": [{"saved_at": ISO 시각(KST), "text": 그때의 글}, ...]}  (오래된 것 → 최신)
지금 글은 마지막 버전이다.
"""
from __future__ import annotations

import difflib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lib.db import get_client

KEY = "my_pledge"
KST = ZoneInfo("Asia/Seoul")


def _norm(text: str) -> str:
    """비교용: 줄 끝 공백과 앞뒤 빈 줄은 무시한다."""
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")).strip()


def load() -> list[dict]:
    rows = get_client().table("app_settings").select("*").eq("key", KEY).execute().data or []
    value = rows[0]["value"] if rows else None
    return list((value or {}).get("versions") or [])


def current(versions: list[dict]) -> str:
    return versions[-1]["text"] if versions else ""


def save(text: str, versions: list[dict] | None = None, now: datetime | None = None) -> bool:
    """글을 저장한다. 이전 글과 달라졌을 때만 새 버전을 남기고 True를 돌려준다."""
    versions = load() if versions is None else list(versions)
    new = _norm(text)
    if new == _norm(current(versions)):
        return False
    at = (now or datetime.now(timezone.utc)).astimezone(KST)
    versions.append({"saved_at": at.isoformat(timespec="seconds"), "text": new})
    get_client().table("app_settings").upsert({"key": KEY, "value": {"versions": versions}},
                                              on_conflict="key").execute()
    return True


def diff_lines(old: str, new: str) -> list[tuple[str, str]]:
    """줄 단위로 비교해 [("+", 더한 줄), ("-", 지운 줄), ...]. 안 바뀐 줄은 뺀다."""
    out = []
    for line in difflib.ndiff(_norm(old).split("\n") if old else [], _norm(new).split("\n")):
        tag, body = line[:1], line[2:]
        if tag in "+-" and body.strip():
            out.append((tag, body))
    return out


def saved_label(saved_at: str) -> str:
    try:
        t = datetime.fromisoformat(saved_at).astimezone(KST)
    except (TypeError, ValueError):
        return str(saved_at)
    return f"{t:%Y-%m-%d} ({'월화수목금토일'[t.weekday()]}) {t:%H:%M}"
