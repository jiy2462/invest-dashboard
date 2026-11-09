"""화면 스모크 테스트: python -m pytest tests (네트워크 불필요)."""
import os

from streamlit.testing.v1 import AppTest

from tests import fakes

HERE = os.path.dirname(__file__)


def run(page: str) -> AppTest:
    os.environ["PAGE"] = page
    at = AppTest.from_file(os.path.join(HERE, "run_page.py"), default_timeout=60)
    at.secrets["APP_PASSWORD"] = "x"
    return at.run()


def test_market_cards():
    at = run("market")
    assert not at.exception
    assert [s.value for s in at.subheader] == ["지수", "환율", "금리"]
    md = " ".join(m.value for m in at.markdown)
    assert "S&P 500" in md and "bp" in md
    assert any("ECOS_API_KEY" in c.value for c in at.caption)


def test_search_chart_watchlist_and_drawings():
    at = run("watchlist")
    assert not at.exception
    at.text_input[0].set_value("삼성").run()
    assert at.selectbox[0].options[0].startswith("삼성전자 · 005930.KS")
    at.button[0].click().run()  # 차트 보기
    assert not at.exception
    assert at.session_state.chart_symbol["symbol"] == "005930.KS"

    # 관심종목 추가
    [b for b in at.button if b.label == "관심종목에 추가"][0].click().run()
    assert not at.exception
    assert fakes.TABLES["watchlist"][0]["symbol"] == "005930.KS"
    assert fakes.TABLES["assets"][0]["name"] == "삼성전자"

    # 구간 등락률 저장
    at.radio(key=None) if False else None
    radio = [r for r in at.radio if r.label == "종류"][0]
    radio.set_value("구간 등락률").run()
    [b for b in at.button if b.key == "save-measure"][0].click().run()
    assert not at.exception
    assert fakes.TABLES["chart_drawings"][0]["kind"] == "measure"

    # 30분봉
    [s for s in at.selectbox if s.label == "봉"][0].set_value("30분봉").run()
    assert not at.exception


def test_english_search():
    at = run("watchlist")
    at.text_input[0].set_value("apple").run()
    assert any("AAPL" in o for o in at.selectbox[0].options)


def test_market_mini_candles_with_moving_averages():
    at = run("market")
    assert not at.exception
    charts = [c for c in at.get("plotly_chart")]
    assert charts, "지표 카드 차트가 없어요"
    import json
    fig = json.loads(charts[0].proto.spec)
    kinds = [t["type"] for t in fig["data"]]
    names = [t.get("name") for t in fig["data"]]
    assert kinds[0] == "candlestick"
    assert "50일선" in names and "200일선" in names
    md = " ".join(m.value for m in at.markdown)
    assert "200일 " in md


def test_click_watchlist_row_opens_chart():
    at = run("watchlist")
    at.text_input[0].set_value("삼성").run()
    at.button[0].click().run()
    [b for b in at.button if b.label == "관심종목에 추가"][0].click().run()
    # 다른 종목 차트를 연 뒤
    at.text_input[0].set_value("apple").run()
    [b for b in at.button if b.label == "차트 보기"][0].click().run()
    assert at.session_state.chart_symbol["symbol"] == "AAPL"
    # 표가 차트보다 위에 있어요
    labels = [s.value for s in at.subheader]
    assert "관심종목" in labels
    # 표에서 종목명 셀을 누르면 그 종목 차트가 바로 열려요
    at.session_state["watch-table-전체"] = {"selection": {"rows": [], "columns": [], "cells": [(0, "종목")]}}
    at.run()
    assert not at.exception
    assert at.session_state.chart_symbol["symbol"] == "005930.KS"
    # 같은 선택이 남아 있어도 검색으로 연 차트를 덮어쓰지 않아요
    at.text_input[0].set_value("apple").run()
    [b for b in at.button if b.label == "차트 보기"][0].click().run()
    assert at.session_state.chart_symbol["symbol"] == "AAPL"


def test_market_card_shows_live_quote_time(monkeypatch):
    """장중이면 카드 아래에 한국 시각으로 몇 시 몇 분 시세인지 보여 준다."""
    from datetime import datetime, timezone

    from lib import live_quote, market_hours

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    monkeypatch.setattr(market_hours, "is_live", lambda *a, **k: True)
    monkeypatch.setattr(live_quote, "fetch_yahoo", lambda syms: {
        s: live_quote.Quote(price=1500.0, at=now, day=live_quote.day_of(s, now)) for s in syms if s == "KRW=X"})
    at = run("market")
    assert not at.exception
    kst = now.astimezone(live_quote.KST)
    assert any(c.value.startswith(f"{kst:%m/%d %H:%M} 기준") and "야후" in c.value for c in at.caption)
    assert any("1,500.00" in m.value for m in at.markdown)
