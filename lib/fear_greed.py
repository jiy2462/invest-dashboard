"""CNN 공포·탐욕 지수(Fear & Greed Index, 0~100).

CNN 화면이 쓰는 데이터 주소(production.dataviz.cnn.io)를 fear-and-greed 패키지로 읽는다.
패키지는 브라우저처럼 요청한다 (인영님이 이 방식을 골랐음, 2026-11-07). 공식 API가 아니어서
형식이 바뀌거나 CNN이 막을 수 있다. 그때는 카드에 이유를 보여 준다.
"""
from __future__ import annotations

import pandas as pd
import requests

SYMBOL = "cnn:fear_greed"

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


def _package_fetch() -> dict:
    """fear-and-greed 패키지로 CNN 응답(JSON 전체)을 받는다.

    패키지는 불러올 때 requests 전체에 1분 캐시(requests_cache)를 켜므로, 다른 시세 요청에
    영향이 없도록 바로 끈다. 캐시는 lib.market이 따로 한다.
    """
    import requests_cache
    from fear_and_greed import cnn

    requests_cache.uninstall_cache()
    return cnn.Fetcher()()


def fetch() -> pd.Series:
    return load(_package_fetch)


def load(get) -> pd.Series:
    """get()이 돌려준 CNN 응답을 점수 시계열로. 실패는 이유가 담긴 FearGreedError로."""
    try:
        body = get()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        raise FearGreedError(f"CNN이 요청을 받아 주지 않았어요 ({code})") from e
    except ImportError as e:
        raise FearGreedError("fear-and-greed 패키지가 설치되지 않았어요") from e
    except Exception as e:
        raise FearGreedError(f"CNN 접속 실패: {type(e).__name__}") from e
    return parse(body)
