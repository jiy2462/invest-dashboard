"""오늘의 인사이트: 명언 고르기·해설, 유튜브 RSS·추천, 화면 테스트 (네트워크 불필요)."""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from lib import insight as ins
from lib.quotes_data import QUOTES, THEMES
from tests import fakes
from tests.test_pages import run

D = date(2026, 11, 6)
CH1, CH2 = "UC" + "a" * 22, "UC" + "b" * 22


def seed_portfolio():
    T = fakes.TABLES
    T["accounts"].append({"id": 1, "name": "ISA", "broker": None, "currency": "KRW"})
    T["assets"] += [
        {"symbol": "SPYM", "name": "SPDR S&P 500", "market": "US", "currency": "USD", "asset_class": "미국주식"},
        {"symbol": "005930.KS", "name": "삼성전자", "market": "KRX", "currency": "KRW", "asset_class": "국내주식"},
    ]
    T["transactions"] += [
        {"id": 1, "trade_date": "2026-01-02", "account_id": 1, "symbol": "SPYM", "side": "buy", "quantity": 100,
         "price": 70, "currency": "USD", "fee": 0, "fx_rate": 1400, "memo": None},
        {"id": 2, "trade_date": "2026-01-02", "account_id": 1, "symbol": "005930.KS", "side": "buy", "quantity": 50,
         "price": 60000, "currency": "KRW", "fee": 0, "fx_rate": None, "memo": None},
    ]
    T["target_allocation"] += [
        {"asset_class": "미국주식", "target_weight": 50, "monthly_contribution": 500000, "sort_order": 1},
        {"asset_class": "국내주식", "target_weight": 30, "monthly_contribution": 300000, "sort_order": 2},
        {"asset_class": "금", "target_weight": 20, "monthly_contribution": 200000, "sort_order": 3},
    ]


def add_feeds():
    fakes.FEEDS[CH1] = ("경제 채널 A", [
        {"id": "v_rate", "title": "연준 금리 인하 언제? 국채 금리 급등의 의미", "days_ago": 1},
        {"id": "v_food", "title": "주말 맛집 브이로그", "days_ago": 0},
        {"id": "v_old", "title": "금리 전망 총정리", "days_ago": 60},
    ])
    fakes.FEEDS[CH2] = ("경제 채널 B", [
        {"id": "v_sp", "title": "S&P 500 조정, 지금 사도 될까", "days_ago": 2},
        {"id": "v_short", "title": "금리 한 줄 요약 #shorts", "days_ago": 0},
    ])


# ── 명언 ────────────────────────────────────────────────────

def test_quote_data_is_complete():
    assert len(QUOTES) >= 70
    keys = {(q["guru"], q["quote_ko"]) for q in QUOTES}
    assert len(keys) == len(QUOTES)  # 중복 없음
    for q in QUOTES:
        assert q["theme"] in THEMES, q
        assert q["quote_ko"] and q["philosophy"] and q["guru"]


def test_pick_quotes_is_daily_stable_distinct_and_skips_recent():
    quotes = [{**q, "id": i + 1} for i, q in enumerate(QUOTES)]
    a = ins.pick_quotes(quotes, set(), D)
    assert a == ins.pick_quotes(quotes, set(), D)        # 같은 날은 같은 결과
    assert len(a) == 2 and a[0]["guru"] != a[1]["guru"] and a[0]["theme"] != a[1]["theme"]
    recent = {q["id"] for q in quotes[:-3]}
    b = ins.pick_quotes(quotes, recent, D)
    assert all(q["id"] not in recent for q in b)
    c = ins.pick_quotes(quotes, set(), D, salt=7, exclude={q["id"] for q in a})
    assert not {q["id"] for q in a} & {q["id"] for q in c}


def test_seed_and_make_quotes_basic_with_portfolio_numbers():
    seed_portfolio()
    p = ins.load_portfolio_context()
    assert p["total_krw"] > 0 and {c["name"] for c in p["classes"]} >= {"미국주식", "국내주식", "금"}
    assert "S&P" in p["keywords"]
    items, errors = ins.make_quotes(D, p, {})
    assert len(fakes.TABLES["quotes"]) == len(QUOTES)
    assert len(items) == 2 and not errors
    assert all(it["made_by"] == "basic" and it["my_portfolio_note"] and it["meaning"] for it in items)
    # 다시 불러도 같은 날 명언은 그대로 (ensure_today는 새로 만들지 않음)
    again = ins.ensure_today(D, ctx=(p, {}))
    assert [q["quote_id"] for q in again["quotes"]] == [q["quote_id"] for q in items]
    assert again["made"] == []


@pytest.mark.parametrize("theme", THEMES)
def test_basic_note_every_theme(theme):
    p = {"pnl_pct": 12.3, "goal_progress": 61.0, "monthly_total": 1_000_000,
         "classes": [{"name": "미국주식", "weight": 58.0, "target": 50.0, "diff": 8.0, "pnl_pct": 20.0, "monthly": 5e5},
                     {"name": "금", "weight": 12.0, "target": 20.0, "diff": -8.0, "pnl_pct": 5.0, "monthly": 2e5},
                     {"name": "투자유보현금", "weight": 30.0, "target": 30.0, "diff": 0.0, "pnl_pct": 0.0, "monthly": 0}],
         "holdings": [{"name": "SPDR S&P 500", "asset_class": "미국주식", "weight": 58.0, "pnl_pct": 20.0}]}
    m = {"items": {"^GSPC": {"from_high": -12.0, "last": 6000, "m1": -5, "d1": -1},
                   "^VIX": {"last": 28.0, "from_high": -10, "m1": 5, "d1": 1}}}
    note = ins.basic_note({"theme": theme, "guru": "x"}, p, m)
    assert len(note) > 20 and "nan" not in note
    assert ins.basic_note({"theme": theme}, {}, {}).startswith("포트폴리오 데이터를")


def test_claude_notes_used_when_available(monkeypatch):
    monkeypatch.setattr(ins, "claude_enabled", lambda: True)

    def fake_ask(system, prompt, schema):
        assert "인영" in system and "미국주식" in prompt
        ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', prompt)]
        return {"items": [{"id": i, "meaning": f"철학 {i}", "note": f"내 포트폴리오 {i}"} for i in ids]}

    monkeypatch.setattr(ins, "ask_claude_json", fake_ask)
    seed_portfolio()
    items, errors = ins.make_quotes(D, ins.load_portfolio_context(), {})
    assert not errors and all(it["made_by"] == "claude" and it["meaning"].startswith("철학") for it in items)


def test_claude_failure_falls_back_to_basic(monkeypatch):
    monkeypatch.setattr(ins, "claude_enabled", lambda: True)

    def boom(*a):
        raise ins.ClaudeError("테스트 실패")

    monkeypatch.setattr(ins, "ask_claude_json", boom)
    items, errors = ins.make_quotes(D, {}, {})
    assert len(items) == 2 and all(it["made_by"] == "basic" for it in items)
    assert "테스트 실패" in errors[0]


# ── 시장 상황 ───────────────────────────────────────────────

def series(values, end="2026-11-05"):
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)))


def test_market_context_tags_correction_vix_and_rates():
    up = np.linspace(100, 120, 230)
    sp = series(np.concatenate([up, np.linspace(120, 102, 30)]))   # 고점 대비 -15%
    vix = series(np.full(260, 30.0))
    tnx = series(np.concatenate([np.full(230, 4.0), np.linspace(4.0, 4.5, 30)]))
    m = ins.market_context({"^GSPC": sp, "^VIX": vix, "^TNX": tnx})
    tags = " ".join(m["tags"])
    assert "조정장" in tags and "변동성 큼" in tags and "금리 상승" in tags
    assert m["items"]["^GSPC"]["from_high"] == pytest.approx(-15, abs=0.1)
    assert m["keywords"]["금리"] == 3.0 and m["keywords"]["조정"] == 3.0


# ── 유튜브 ──────────────────────────────────────────────────

def test_parse_feed_and_resolve_channel_by_handle():
    add_feeds()
    name, vids = ins.parse_feed(fakes.fake_feed(CH1))
    assert name == "경제 채널 A" and len(vids) == 3
    assert vids[0]["video_id"] == "v_rate" and vids[0]["channel_id"] == CH1 and vids[0]["thumbnail_url"]
    fakes.PAGES["https://www.youtube.com/@econ"] = f'<html>..."externalId":"{CH1}"...</html>'
    assert ins.resolve_channel("@econ") == (CH1, "경제 채널 A")
    assert ins.resolve_channel(f"https://www.youtube.com/channel/{CH2}") == (CH2, "경제 채널 B")
    with pytest.raises(ins.ChannelError):
        ins.resolve_channel("@없는채널")


def test_parse_takeout_csv():
    df = pd.DataFrame({"채널 ID": [CH1, CH2, CH1], "채널 URL": [f"http://www.youtube.com/channel/{CH1}"] * 3,
                       "채널 제목": ["A", "B", "A"]})
    assert ins.parse_takeout(df) == [(CH1, "A"), (CH2, "B")]


def test_score_videos_prefers_market_topics_and_skips_watched_old_shorts():
    add_feeds()
    vids = ins.parse_feed(fakes.fake_feed(CH1))[1] + ins.parse_feed(fakes.fake_feed(CH2))[1]
    m = {"keywords": {"금리": 3.0, "국채": 3.0, "연준": 3.0, "조정": 3.0}, "tags": ["미국 금리 상승"]}
    p = {"keywords": {"S&P": 2.0}}
    chans = {CH1: "A", CH2: "B"}
    scored = ins.score_videos(vids, m, p, [], chans, D, now=datetime.now(timezone.utc))
    ids = [v["video_id"] for v in scored]
    assert "v_old" not in ids                       # 30일 지난 영상 제외
    assert ids[0] == "v_rate"                       # 금리·국채·연준
    assert ids.index("v_short") > ids.index("v_food") or scored[ids.index("v_short")]["score"] < scored[0]["score"]
    picks = ins.pick_videos_basic(scored, m)
    assert len(picks) == 3 and len({p["video_id"] for p in picks}) == 3
    assert "금리" in picks[0]["reason"]
    hist = [{"date": D.isoformat(), "video_id": "v_rate", "watched": True, "liked": False}]
    assert "v_rate" not in [v["video_id"] for v in ins.score_videos(vids, m, p, hist, chans, D)]


def test_make_videos_reads_feeds_and_saves_three():
    add_feeds()
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")
    vids, errors = ins.make_videos(D, {}, {"keywords": {"금리": 3.0}, "tags": []})
    assert len(vids) == 3 and not errors
    assert {v["channel_name"] for v in vids} == {"A", "B"}
    ins.mark_video(D, vids[0]["video_id"], watched=True)
    assert ins.daily_videos(D)[0]["watched"] is True


# ── 화면 ────────────────────────────────────────────────────

def test_insight_page_quotes_channels_and_videos():
    seed_portfolio()
    add_feeds()
    fakes.PAGES["https://www.youtube.com/@econ"] = f'"externalId":"{CH1}"'
    at = run("insight")
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert md.count("지금 내 포트폴리오에 주는 의미") == 2
    assert any("채널이 아직 없어요" in i.value for i in at.info)

    # 채널 추가 → 영상 다시 고르기
    at.text_input[0].set_value("@econ")
    [b for b in at.button if b.label == "추가"][0].click().run()
    assert not at.exception
    assert fakes.TABLES["youtube_channels"], ([e.value for e in at.error], [b.label for b in at.button])
    assert fakes.TABLES["youtube_channels"][0]["channel_id"] == CH1
    [b for b in at.button if b.label == "영상 다시 고르기"][0].click().run()
    assert not at.exception
    assert len(fakes.TABLES["daily_videos"]) == 2  # 채널 A의 최근 영상 2개 (60일 지난 것 제외)
    [b for b in at.button if b.key == "w-t0"][0].click().run()
    assert not at.exception
    assert any(r["watched"] for r in fakes.TABLES["daily_videos"])

    # 다른 명언 뽑기 → 명언이 바뀐다
    before = {r["quote_id"] for r in fakes.TABLES["daily_quotes"]}
    [b for b in at.button if b.label == "다른 명언 뽑기"][0].click().run()
    assert not at.exception
    after = {r["quote_id"] for r in fakes.TABLES["daily_quotes"]}
    assert len(after) == 2 and not before & after

    # 명언 끄기 저장
    at.button(key="save-quotes").click().run()
    assert not at.exception


def test_market_page_shows_today_quotes_preview():
    ins.make_quotes(ins.today_kst(), {}, {})
    at = run("market")
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "오늘의 명언" in md and "—" in md
