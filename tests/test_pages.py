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
