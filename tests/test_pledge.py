"""나의 다짐: 저장·바뀐 경우에만 기록·화면 (네트워크 불필요)."""
from datetime import datetime, timezone

from lib import pledge
from tests import fakes
from tests.test_pages import run


def stored() -> list[dict]:
    rows = [r for r in fakes.TABLES["app_settings"] if r["key"] == pledge.KEY]
    return rows[0]["value"]["versions"] if rows else []


def test_save_only_when_changed():
    t1 = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    assert pledge.save("잃지 않는다\n", now=t1)
    assert not pledge.save("잃지 않는다   \n\n")          # 공백·빈 줄만 다르면 저장하지 않음
    assert pledge.save("잃지 않는다\n분산한다")
    v = stored()
    assert [x["text"] for x in v] == ["잃지 않는다", "잃지 않는다\n분산한다"]
    assert v[0]["saved_at"].startswith("2026-11-09T09:00:00+09:00")
    assert pledge.saved_label(v[0]["saved_at"]) == "2026-11-09 (월) 09:00"


def test_diff_lines():
    assert pledge.diff_lines("a\nb", "a\nc") == [("-", "b"), ("+", "c")]
    assert pledge.diff_lines("", "a") == [("+", "a")]


def test_pledge_on_market_page():
    at = run("market")
    assert not at.exception
    # 처음에는 바로 쓰는 칸이 열려 있다
    box = [t for t in at.text_area if t.key == "pledge-text"][0]
    box.set_value("원칙 1. 잃지 않는다").run()
    [b for b in at.button if b.key == "pledge-save"][0].click().run()
    assert not at.exception
    assert [x["text"] for x in stored()] == ["원칙 1. 잃지 않는다"]
    md = " ".join(m.value for m in at.markdown)
    assert "원칙 1. 잃지 않는다" in md
    assert not [t for t in at.text_area if t.key == "pledge-text"]

    # 바꾸지 않고 저장하면 기록이 늘지 않는다
    [b for b in at.button if b.key == "pledge-open"][0].click().run()
    [b for b in at.button if b.key == "pledge-save"][0].click().run()
    assert len(stored()) == 1

    # 고쳐서 저장하면 기록이 생기고, 지난 기록에 바뀐 줄이 보인다
    [b for b in at.button if b.key == "pledge-open"][0].click().run()
    assert [t for t in at.text_area if t.key == "pledge-text"][0].value == "원칙 1. 잃지 않는다"
    [t for t in at.text_area if t.key == "pledge-text"][0].set_value("원칙 1. 잃지 않는다\n원칙 2. 분산한다").run()
    [b for b in at.button if b.key == "pledge-save"][0].click().run()
    assert not at.exception
    assert len(stored()) == 2
    assert any(e.label == "지난 기록 2개" for e in at.expander)
    md = " ".join(m.value for m in at.markdown)
    assert "+ 원칙 2. 분산한다" in md and "처음 작성" in md

    # 취소하면 저장하지 않는다
    [b for b in at.button if b.key == "pledge-open"][0].click().run()
    [t for t in at.text_area if t.key == "pledge-text"][0].set_value("다른 글").run()
    [b for b in at.button if b.key == "pledge-cancel"][0].click().run()
    assert len(stored()) == 2 and not [t for t in at.text_area if t.key == "pledge-text"]
