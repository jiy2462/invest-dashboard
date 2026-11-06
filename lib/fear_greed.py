"""CNN 공포·탐욕 지수(Fear & Greed Index, 0~100).

CNN 화면이 쓰는 데이터 주소(production.dataviz.cnn.io)를 읽는다. 공식 API가 아니어서
형식이 바뀌거나 자동 요청을 거절할 수 있다. 그때는 카드에 이유를 보여 준다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

SYMBOL = "cnn:fear_greed"
URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
HEADERS = {"User-Agent": "invest-dashboard/1.0 (personal dashboard)", "Accept": "application/json"}
HISTORY_DAYS = 400

# CNN 구간: 0–24 극단적 공포, 25–44 공포, 45–55 중립, 56–75 탐욕, 76–100 극단적 탐욕
BANDS = [(0, 25, "극단적 공포"), (25, 45, "공포"), (45, 56, "중립"), (56, 76, "탐욕"), (76, 101, "극단적 탐욕")]


class FearGreedError(Exception):
    pass


def rating_ko(score: float) -> str:
    for lo, hi, name in BANDS:
        if lo <= score < hi:
            return name
    return "극단적 탐욕" if score >= 100 else "극단적 공포"


def _day(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def parse(body: dict) -> pd.Series:
    """응답 JSON → 날짜별 점수. 과거 기록에 현재 점수를 덧붙인다."""
    values: dict[pd.Timestamp, float] = {}
    hist = (body or {}).get("fear_and_greed_historical") or {}
    for p in hist.get("data") or []:
        try:
            values[pd.Timestamp(int(p["x"]), unit="ms").normalize()] = float(p["y"])
        except (KeyError, TypeError, ValueError):
            continue
    now = (body or {}).get("fear_and_greed") or {}
    try:
        values[_day(now["timestamp"])] = float(now["score"])
    except (KeyError, TypeError, ValueError):
        pass
    if not values:
        raise FearGreedError("CNN 응답에서 점수를 찾지 못했어요")
    return pd.Series(values).sort_index()


def fetch() -> pd.Series:
    start = date.today() - timedelta(days=HISTORY_DAYS)
    try:
        r = requests.get(URL.format(start=f"{start:%Y-%m-%d}"), headers=HEADERS, timeout=8)
    except Exception as e:
        raise FearGreedError(f"CNN 접속 실패: {type(e).__name__}") from e
    if not r.ok:
        raise FearGreedError(f"CNN이 요청을 받아 주지 않았어요 ({r.status_code})")
    try:
        body = r.json()
    except ValueError as e:
        raise FearGreedError("CNN 응답이 JSON이 아니에요") from e
    return parse(body)
