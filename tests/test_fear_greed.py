"""CNN 공포·탐욕 지수: 응답 해석, 구간 이름, 오늘의 시장 카드."""
import json

import pandas as pd

from lib import fear_greed, indicators, market
from tests import fakes
from tests.test_pages import run

# CNN 응답 구조를 줄인 샘플 (x = 밀리초 시각, y = 점수)
BODY = {
    "fear_and_greed": {"score": 62.3, "rating": "greed", "timestamp": "2026-11-06T14:30:00+00:00"},
    "fear_and_greed_historical": {"data": [
        {"x": 1762300800000.0, "y": 40.0, "rating": "fear"},   # 2025-11-05
        {"x": 1762387200000.0, "y": 44.5, "rating": "fear"},   # 2025-11-06
    ]},
}


def test_parse_and_rating():
    s = fear_greed.parse(BODY)
    assert s.iloc[-1] == 62.3 and s.index[-1] == pd.Timestamp("2026-11-06")
    assert s.loc[pd.Timestamp("2025-11-05")] == 40.0
    assert [fear_greed.rating_ko(v) for v in (10, 30, 50, 60, 90)] == ["극단적 공포", "공포", "중립", "탐욕", "극단적 탐욕"]


def test_fetch_failure_becomes_card_message(monkeypatch):
    def refuse():
        raise fear_greed.FearGreedError("CNN이 요청을 받아 주지 않았어요 (418)")

    monkeypatch.setattr(fear_greed, "fetch", refuse)
    out, errors = market.load_series([{"symbol": fear_greed.SYMBOL, "source": "cnn"}])
    assert not out and "418" in errors[fear_greed.SYMBOL]


def test_preset_and_market_card():
    assert ("CNN 공포·탐욕 지수", "cnn:fear_greed", "cnn", "심리") in indicators.PRESETS
    fakes.TABLES["indicator_settings"].append(dict(id=99, symbol="cnn:fear_greed", source="cnn",
                                                   display_name="CNN 공포·탐욕 지수", group_name="심리",
                                                   sort_order=99, is_visible=True))
    at = run("market")
    assert not at.exception
    assert "심리" in [s.value for s in at.subheader]
    md = " ".join(m.value for m in at.markdown)
    assert "CNN 공포·탐욕 지수" in md and "1주 전" in md
    figs = [json.loads(c.proto.spec) for c in at.get("plotly_chart")]
    fg = [f for f in figs if f["layout"].get("yaxis", {}).get("range") == [0, 100]]
    assert fg and fg[0]["data"][0]["type"] == "scatter"



def test_load_parses_history_and_maps_errors():
    import requests

    s = fear_greed.load(lambda: BODY)
    assert len(s) == 3 and s.iloc[-1] == 62.3

    def blocked():
        r = requests.Response()
        r.status_code = 418
        raise requests.HTTPError(response=r)

    try:
        fear_greed.load(blocked)
        raise AssertionError("오류가 나야 함")
    except fear_greed.FearGreedError as e:
        assert "418" in str(e)


def test_package_fetch_uses_fear_and_greed_and_turns_off_global_cache(monkeypatch):
    import requests_cache
    from fear_and_greed import cnn

    monkeypatch.setattr(cnn.Fetcher, "__call__", lambda self: BODY)
    assert fear_greed._package_fetch() is BODY
    assert not requests_cache.is_installed()  # 다른 requests 요청엔 캐시가 끼지 않음
