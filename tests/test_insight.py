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


def test_score_videos_splits_new_and_old_and_skips_watched_shorts():
    add_feeds()
    vids = ins.parse_feed(fakes.fake_feed(CH1))[1] + ins.parse_feed(fakes.fake_feed(CH2))[1]
    m = {"keywords": {"금리": 3.0, "국채": 3.0, "연준": 3.0, "조정": 3.0}, "tags": ["미국 금리 상승"]}
    p = {"keywords": {"S&P": 2.0}}
    chans = {CH1: "A", CH2: "B"}
    new = ins.score_videos(vids, m, p, [], chans, D, max_age=ins.NEW_VIDEO_DAYS, repeat_days=0)
    ids = [v["video_id"] for v in new]
    assert ids[0] == "v_rate" and set(ids) == {"v_rate", "v_sp", "v_food"}   # 2주 지난 것·쇼츠 제외
    old = ins.score_videos(vids, m, p, [], chans, D, min_age=ins.NEW_VIDEO_DAYS, recency=False)
    assert [v["video_id"] for v in old] == ["v_old"]
    assert "금리" in ins.basic_reason(new[0], m)
    hist = [{"date": D.isoformat(), "video_id": "v_rate", "watched": True, "liked": False}]
    assert "v_rate" not in [v["video_id"] for v in ins.score_videos(vids, m, p, hist, chans, D)]


def catalog_videos(n=40):
    """채널 3개, 2주~몇 년 전 영상. 5개마다 하나는 금리 이야기."""
    now = datetime.now(timezone.utc)
    chans = [CH1, CH2, "UC" + "c" * 22]
    out = []
    for i in range(n):
        out.append({"video_id": f"o{i}", "channel_id": chans[i % 3],
                    "title": f"금리 이야기 {i}" if i % 5 == 0 else f"투자 공부 {i}",
                    "description": "", "published_at": (now - timedelta(days=20 + i * 30)).isoformat()})
    return out, {c: c[-1] for c in chans}


def test_pick_videos_basic_mixes_market_and_random():
    vids, chans = catalog_videos()
    m = {"keywords": {"금리": 3.0}, "tags": ["금리 상승"]}
    scored = ins.score_videos(vids, m, {}, [], chans, D, min_age=ins.NEW_VIDEO_DAYS, recency=False)
    picks = ins.pick_videos_basic(scored, m, D)
    assert len(picks) == 4 and len({x["video_id"] for x in picks}) == 4
    titles = {v["video_id"]: v["title"] for v in vids}
    assert sum("금리" in titles[x["video_id"]] for x in picks) >= 2           # 시장 맞춤 2개 이상
    assert any("무작위" in x["reason"] for x in picks)                        # 나머지는 무작위
    assert any("년" in x["reason"] and "월 영상" in x["reason"] for x in picks)  # 언제 영상인지
    by_ch = {v["video_id"]: v["channel_id"] for v in vids}
    assert len({by_ch[x["video_id"]] for x in picks}) == 3                    # 채널 3개 모두
    assert picks == ins.pick_videos_basic(scored, m, D)                        # 같은 날은 같은 결과
    others = [ins.pick_videos_basic(scored, m, D, salt=s) for s in range(1, 6)]
    assert any(o != picks for o in others)                                     # 다시 고르면 바뀜


def test_parse_duration():
    assert ins.parse_duration("PT1H2M3S") == 3723
    assert ins.parse_duration("PT59S") == 59
    assert ins.parse_duration("P0D") == 0
    assert ins.parse_duration("P1DT1M") == 86460
    assert ins.parse_duration("") == 0


def add_catalog():
    """채널 A: 120개(최신순). 7개마다 쇼츠, 하나는 라이브 예정(길이 0). 채널 B: 3개."""
    fakes.CATALOG[CH1] = [{"id": f"a{i}", "title": ("금리 전망 " if i % 4 == 0 else "시장 이야기 ") + str(i),
                           "days_ago": i * 3, "sec": 40 if i % 7 == 3 else 0 if i == 5 else 900}
                          for i in range(120)]
    fakes.CATALOG[CH2] = [{"id": f"b{i}", "title": f"S&P 500 분석 {i}", "days_ago": 1 + i * 40, "sec": 1200}
                          for i in range(3)]


def test_refresh_videos_reads_whole_channel_once_then_only_new(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    add_catalog()
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")
    n, errors = ins.refresh_videos(ins.list_channels())
    assert not errors
    ids = {v["video_id"] for v in fakes.TABLES["videos"]}
    shorts = {f"a{i}" for i in range(120) if i % 7 == 3} | {"a5"}
    assert ids == {f"a{i}" for i in range(120)} - shorts | {"b0", "b1", "b2"}
    state = ins.catalog_state()
    assert state[CH1]["count"] == 120 - len(shorts) and state[CH1]["synced"]
    pages = [c for c in fakes.YT_CALLS if c[0] == "playlistItems" and c[1]["playlistId"] == "UU" + CH1[2:]]
    assert len(pages) == 3                                    # 50개씩 3쪽을 끝까지

    # 다음 날: 새 영상 하나만 추가, 첫 쪽만 읽는다
    fakes.YT_CALLS.clear()
    fakes.CATALOG[CH1].insert(0, {"id": "a_new", "title": "새 영상", "days_ago": 0, "sec": 700})
    ins.refresh_videos(ins.list_channels())
    assert "a_new" in {v["video_id"] for v in fakes.TABLES["videos"]}
    assert len([c for c in fakes.YT_CALLS if c[0] == "playlistItems"]) == 2   # 채널마다 1쪽


def test_api_failure_falls_back_to_rss(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    add_feeds()
    fakes.CATALOG[CH1] = [{"id": "a0", "title": "금리", "days_ago": 30, "sec": 900}]
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")          # CATALOG에 없음 → API 실패 → RSS
    n, errors = ins.refresh_videos(ins.list_channels())
    ids = {v["video_id"] for v in fakes.TABLES["videos"]}
    assert "a0" in ids and "v_sp" in ids and "v_rate" not in ids
    assert len(errors) == 1 and "B" in errors[0]
    assert CH2 not in ins.catalog_state()


def test_make_videos_with_api_saves_four_picks_and_new_videos(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    add_catalog()
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")
    m = {"keywords": {"금리": 3.0}, "tags": ["금리 상승"]}
    vids, errors = ins.make_videos(D, {}, m)
    assert not errors
    picks = [v for v in vids if v["kind"] == "pick"]
    new = [v for v in vids if v["kind"] == "new"]
    now = datetime.now(timezone.utc)
    age = lambda v: (now - pd.Timestamp(v["published_at"]).to_pydatetime()).days  # noqa: E731
    assert len(picks) == 4 and all(age(v) > ins.NEW_VIDEO_DAYS for v in picks)
    assert sum("금리" in v["title"] for v in picks) >= 2
    assert new and all(age(v) <= ins.NEW_VIDEO_DAYS for v in new)
    assert {v["video_id"] for v in new} == {"a0", "a1", "a2", "a4", "b0"}   # a3 쇼츠, a5 라이브 예정
    assert all(v["reason"] for v in new)                                    # 6개 이하라 모두 소개 문장
    assert not {v["video_id"] for v in picks} & {v["video_id"] for v in new}

    # 봤어요는 다시 고르기 뒤에도 남고, 다시 고르면 추천이 바뀐다
    ins.mark_video(D, new[0]["video_id"], watched=True)
    before = {v["video_id"] for v in picks}
    vids2, _ = ins.make_videos(D, {}, m, refresh=False, reroll=True)
    after = {v["video_id"] for v in vids2 if v["kind"] == "pick"}
    assert len(after) == 4 and before != after
    assert next(v for v in vids2 if v["video_id"] == new[0]["video_id"])["watched"] is True


def test_make_videos_many_new_videos_cards_and_list(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    fakes.CATALOG[CH1] = [{"id": f"n{i}", "title": f"영상 {i}", "days_ago": i * 0.3, "sec": 900} for i in range(60)]
    ins.add_channel(CH1, "A")
    vids, _ = ins.make_videos(D, {}, {})
    new = [v for v in vids if v["kind"] == "new"]
    assert len(new) == ins.NEW_MAX
    assert len([v for v in new if v["reason"]]) == ins.NEW_CARDS
    assert len([v for v in vids if v["kind"] == "pick"]) == 4


def test_make_videos_rss_only():
    add_feeds()
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")
    vids, errors = ins.make_videos(D, {}, {"keywords": {"금리": 3.0}, "tags": []})
    assert not errors
    assert [v["video_id"] for v in vids if v["kind"] == "pick"] == ["v_old"]
    assert {v["video_id"] for v in vids if v["kind"] == "new"} == {"v_rate", "v_sp", "v_food"}
    assert {v["channel_name"] for v in vids} == {"A", "B"}
    ins.mark_video(D, vids[0]["video_id"], watched=True)
    assert ins.daily_videos(D)[0]["watched"] is True


def test_make_videos_with_claude(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(ins, "claude_enabled", lambda: True)
    add_catalog()
    ins.add_channel(CH1, "A")
    ins.add_channel(CH2, "B")
    seen = {}

    def fake_ask(system, prompt, schema):
        import json
        import re
        a = json.loads(re.search(r"\[A\..*?\]\n(\[.*?\])\n", prompt, re.S).group(1))
        b = json.loads(re.search(r"\[B\..*?\]\n(\[.*?\])\n", prompt, re.S).group(1))
        seen["a"], seen["b"] = a, b
        return {"picks": [{"video_id": x["video_id"], "reason": "AI 이유"} for x in a[:4]],
                "new": [{"video_id": x["video_id"], "reason": "AI 소개"} for x in b]}

    monkeypatch.setattr(ins, "ask_claude_json", fake_ask)
    vids, errors = ins.make_videos(D, {}, {"keywords": {"금리": 3.0}, "tags": []})
    assert not errors
    assert len(seen["a"]) > 4 and seen["b"]
    picks = [v for v in vids if v["kind"] == "pick"]
    assert len(picks) == 4 and all(v["made_by"] == "claude" and v["reason"] == "AI 이유" for v in picks)
    assert all(v["reason"] == "AI 소개" for v in vids if v["kind"] == "new")


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
    rows = fakes.TABLES["daily_videos"]          # 채널 A: 지난 영상 1개(추천) + 새 영상 2개
    assert {r["video_id"] for r in rows if r["made_by"] != "new"} == {"v_old"}
    assert {r["video_id"] for r in rows if r["made_by"] == "new"} == {"v_rate", "v_food"}
    md = " ".join(m.value for m in at.markdown)
    assert "채널 전체 영상에서 고른 4개" in md and "새로 올라온 영상" in md
    assert any("YOUTUBE_API_KEY" in c.value for c in at.caption)
    [b for b in at.button if b.key == "w-tp0"][0].click().run()
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


def test_market_page_shows_today_quotes_first():
    ins.make_quotes(ins.today_kst(), {}, {})
    at = run("market")
    assert not at.exception
    assert [h.value.split(" ·")[0] for h in at.header] == ["오늘의 명언", "나의 다짐", "시장 지표"]
    picked = ins.daily_quotes(ins.today_kst())
    md = " ".join(m.value for m in at.markdown)
    assert all(q["quote_ko"] in md for q in picked)
    assert any(c.value.startswith("— ") for c in at.caption)


def test_market_page_makes_quotes_when_missing():
    at = run("market")
    assert not at.exception
    assert len(ins.daily_quotes(ins.today_kst())) == ins.QUOTES_PER_DAY


def test_insight_page_with_api_key_shows_both_sections(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    fakes.CATALOG[CH1] = [{"id": f"n{i}", "title": f"금리 영상 {i}", "days_ago": i * 0.5, "sec": 900} for i in range(80)]
    ins.add_channel(CH1, "채널 A")
    at = run("insight")
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "채널 전체 영상에서 고른 4개" in md and "영상 80개 모음" in md
    assert len([b for b in at.button if (b.key or "").startswith("w-tp")]) == 4
    assert len([b for b in at.button if (b.key or "").startswith("w-tn")]) == ins.NEW_CARDS
    assert any("더 보기" in e.label for e in at.expander)
    assert not any("YOUTUBE_API_KEY" in c.value for c in at.caption)


def test_video_url_brave_by_device():
    ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"
    android = "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36"
    mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"
    plain = "https://www.youtube.com/watch?v=abc"
    assert ins.video_url("abc", "brave", ios) == "brave://open-url?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc"
    a = ins.video_url("abc", "brave", android)
    assert a.startswith("intent://www.youtube.com/watch?v=abc#Intent;scheme=https;package=com.brave.browser;")
    assert "S.browser_fallback_url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc" in a and a.endswith(";end")
    assert ins.video_url("abc", "brave", mac) == plain
    assert ins.video_url("abc", "default", ios) == plain
    assert ins.open_in() == "brave"                 # 기본값: Brave
    ins.set_open_in("default")
    assert ins.open_in() == "default"


def test_insight_page_brave_toggle():
    add_feeds()
    ins.add_channel(CH1, "A")
    at = run("insight")
    assert not at.exception
    t = at.toggle(key="open-brave")
    assert t.value is True
    t.set_value(False).run()
    assert not at.exception
    assert ins.open_in() == "default"
