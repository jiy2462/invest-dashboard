"""오늘의 인사이트: 하루 2개 구루 명언 + 구독 경제 유튜브 추천 3개.

- 명언은 quotes 표(처음엔 lib/quotes_data.py로 채움)에서 날짜를 씨앗으로 무작위로 고르고,
  최근 60일 안에 나온 명언은 피한다.
- 해설: ANTHROPIC_API_KEY가 있으면 Claude가 내 포트폴리오·오늘 시장 숫자를 보고 쓰고,
  없거나 실패하면 규칙 기반 기본 해설을 쓴다.
- 영상: 채널 RSS(youtube.com/feeds/videos.xml?channel_id=...)로 최근 영상을 모으고,
  오늘 시장 상황·내 보유 자산과 관련된 단어가 제목에 있는지로 점수를 매겨 후보를 고른다.
  Claude가 있으면 후보 중 3개를 고르고 이유를 쓴다.
- 앱을 열었을 때 오늘 것이 없으면 그 자리에서 만들고, GitHub Actions가 매일 아침 미리 만든다.
"""
from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from lib import market
from lib.db import get_client, secret
from lib.quotes_data import QUOTES

KST = ZoneInfo("Asia/Seoul")
QUOTES_PER_DAY = 2
VIDEOS_PER_DAY = 3
QUOTE_COOLDOWN_DAYS = 60     # 최근 이 기간에 나온 명언은 다시 고르지 않는다
VIDEO_MAX_AGE_DAYS = 30      # 이보다 오래된 영상은 추천하지 않는다
VIDEO_REPEAT_DAYS = 14       # 최근 추천한 영상은 이 기간 동안 다시 추천하지 않는다
DEFAULT_MODEL = "claude-opus-5-5"
UA = {"User-Agent": "Mozilla/5.0 (invest-dashboard)", "Accept-Language": "ko,en;q=0.8"}


def today_kst(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(KST).date()


def _db():
    return get_client()


def _all(table: str) -> list[dict]:
    return _db().table(table).select("*").execute().data or []


# ── 명언 저장 ───────────────────────────────────────────────

QUOTE_FIELDS = ["guru", "quote_ko", "quote_original", "source", "theme", "philosophy"]


def list_quotes(active_only: bool = False) -> list[dict]:
    rows = sorted(_all("quotes"), key=lambda r: r.get("id") or 0)
    if active_only:
        rows = [r for r in rows if r.get("is_active", True) is not False]
    return rows


def seed_quotes() -> int:
    """quotes 표가 비어 있으면 초안 목록을 넣는다. 넣은 개수를 돌려준다."""
    if _all("quotes"):
        return 0
    db = _db()
    for q in QUOTES:
        db.table("quotes").insert({**{k: q.get(k) for k in QUOTE_FIELDS}, "is_active": True}).execute()
    return len(QUOTES)


def add_quote(row: dict) -> None:
    payload = {k: (row.get(k) or None) for k in QUOTE_FIELDS}
    payload["is_active"] = True
    _db().table("quotes").insert(payload).execute()


def update_quote(quote_id: int, changes: dict) -> None:
    payload = {k: v for k, v in changes.items() if k in QUOTE_FIELDS + ["is_active"]}
    if payload:
        _db().table("quotes").update(payload).eq("id", int(quote_id)).execute()


def _date_str(d) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


def daily_quotes(d: date) -> list[dict]:
    """그날의 명언 (명언 내용을 붙여서, 순서대로)."""
    rows = _db().table("daily_quotes").select("*").eq("date", _date_str(d)).execute().data or []
    by_id = {q["id"]: q for q in _all("quotes")}
    out = []
    for r in sorted(rows, key=lambda r: r.get("rank") or 0):
        q = by_id.get(r["quote_id"])
        if q:
            out.append({**q, "quote_id": q["id"], "meaning": r.get("meaning"),
                        "my_portfolio_note": r.get("my_portfolio_note"), "made_by": r.get("made_by"),
                        "rank": r.get("rank")})
    return out


def quote_dates() -> list[date]:
    return sorted({date.fromisoformat(_date_str(r["date"])) for r in _all("daily_quotes")}, reverse=True)


def save_daily_quotes(d: date, items: list[dict]) -> None:
    db = _db()
    db.table("daily_quotes").delete().eq("date", _date_str(d)).execute()
    for i, it in enumerate(items, 1):
        db.table("daily_quotes").insert({
            "date": _date_str(d), "quote_id": it["quote_id"], "rank": i,
            "meaning": it.get("meaning"), "my_portfolio_note": it.get("my_portfolio_note"),
            "made_by": it.get("made_by"),
        }).execute()


def pick_quotes(quotes: list[dict], recent_ids: set[int], d: date, n: int = QUOTES_PER_DAY,
                salt: int = 0, exclude: set[int] | None = None) -> list[dict]:
    """날짜를 씨앗으로 무작위 n개. 최근에 나온 명언은 피하고, 구루와 주제가 겹치지 않게 한다."""
    exclude = exclude or set()
    pool = [q for q in quotes if q["id"] not in recent_ids and q["id"] not in exclude]
    if len(pool) < n:  # 다 써 버렸으면 최근 것도 허용
        pool = [q for q in quotes if q["id"] not in exclude] or list(quotes)
    rng = random.Random(d.toordinal() * 1009 + salt)
    rng.shuffle(pool)
    picked: list[dict] = []
    for q in pool:
        if all(q["guru"] != p["guru"] and q.get("theme") != p.get("theme") for p in picked):
            picked.append(q)
        if len(picked) == n:
            return picked
    for q in pool:  # 조건을 다 못 채우면 남은 것 중에서
        if q not in picked:
            picked.append(q)
        if len(picked) == n:
            break
    return picked


# ── 채널·영상 저장 ──────────────────────────────────────────

def list_channels(active_only: bool = False) -> list[dict]:
    rows = sorted(_all("youtube_channels"), key=lambda r: str(r.get("created_at") or ""))
    return [r for r in rows if r.get("is_active", True)] if active_only else rows


def add_channel(channel_id: str, name: str) -> None:
    _db().table("youtube_channels").upsert(
        {"channel_id": channel_id, "channel_name": name or channel_id, "is_active": True},
        on_conflict="channel_id").execute()


def set_channel_active(channel_id: str, active: bool) -> None:
    _db().table("youtube_channels").update({"is_active": bool(active)}).eq("channel_id", channel_id).execute()


def delete_channel(channel_id: str) -> None:
    _db().table("youtube_channels").delete().eq("channel_id", channel_id).execute()


def upsert_videos(videos: list[dict]) -> None:
    db = _db()
    for v in videos:
        db.table("videos").upsert({k: v.get(k) for k in
                                   ("video_id", "channel_id", "title", "description", "published_at", "thumbnail_url")},
                                  on_conflict="video_id").execute()


def list_videos() -> list[dict]:
    return _all("videos")


def daily_videos(d: date) -> list[dict]:
    rows = _db().table("daily_videos").select("*").eq("date", _date_str(d)).execute().data or []
    vids = {v["video_id"]: v for v in _all("videos")}
    chans = {c["channel_id"]: c.get("channel_name") for c in _all("youtube_channels")}
    out = []
    for r in sorted(rows, key=lambda r: r.get("rank") or 0):
        v = vids.get(r["video_id"])
        if v:
            out.append({**v, "channel_name": chans.get(v["channel_id"], ""), "rank": r.get("rank"),
                        "reason": r.get("reason"), "watched": bool(r.get("watched")),
                        "liked": bool(r.get("liked")), "made_by": r.get("made_by")})
    return out


def video_dates() -> list[date]:
    return sorted({date.fromisoformat(_date_str(r["date"])) for r in _all("daily_videos")}, reverse=True)


def save_daily_videos(d: date, picks: list[dict]) -> None:
    db = _db()
    db.table("daily_videos").delete().eq("date", _date_str(d)).execute()
    for i, p in enumerate(picks, 1):
        db.table("daily_videos").insert({"date": _date_str(d), "video_id": p["video_id"], "rank": i,
                                         "reason": p.get("reason"), "made_by": p.get("made_by"),
                                         "watched": False, "liked": False}).execute()


def mark_video(d: date, video_id: str, **flags) -> None:
    payload = {k: bool(v) for k, v in flags.items() if k in ("watched", "liked")}
    (_db().table("daily_videos").update(payload).eq("date", _date_str(d)).eq("video_id", video_id).execute())


# ── 유튜브 (네트워크) ───────────────────────────────────────

CHANNEL_ID_RE = re.compile(r"(UC[\w-]{22})")
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}


class ChannelError(Exception):
    pass


def fetch_feed(channel_id: str) -> str:
    r = requests.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id},
                     headers=UA, timeout=10)
    r.raise_for_status()
    return r.text


def fetch_page(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=10, cookies={"CONSENT": "YES+1"})
    r.raise_for_status()
    return r.text


def parse_feed(xml_text: str) -> tuple[str, list[dict]]:
    """채널 RSS → (채널 이름, 영상 목록)."""
    root = ET.fromstring(xml_text)
    name = root.findtext("a:title", default="", namespaces=NS) or ""
    videos = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        group = e.find("media:group", NS)
        desc = group.findtext("media:description", default="", namespaces=NS) if group is not None else ""
        thumb = group.find("media:thumbnail", NS) if group is not None else None
        videos.append({
            "video_id": vid,
            "channel_id": e.findtext("yt:channelId", namespaces=NS),
            "title": e.findtext("a:title", default="", namespaces=NS),
            "description": (desc or "")[:1000],
            "published_at": e.findtext("a:published", namespaces=NS),
            "thumbnail_url": thumb.get("url") if thumb is not None else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    return name, videos


def resolve_channel(text: str) -> tuple[str, str]:
    """채널 주소·@핸들·채널 ID → (채널 ID, 채널 이름). RSS로 이름을 확인한다."""
    t = (text or "").strip()
    if not t:
        raise ChannelError("채널 주소를 넣어 주세요.")
    m = re.search(r"/channel/(UC[\w-]{22})", t) or re.fullmatch(r"(UC[\w-]{22})", t)
    if m:
        cid = m.group(1)
    else:
        if t.startswith("@"):
            url = f"https://www.youtube.com/{t}"
        elif t.startswith("http"):
            url = t
        elif "youtube.com" in t:
            url = "https://" + t
        else:
            url = f"https://www.youtube.com/@{t}"
        try:
            html = fetch_page(url)
        except Exception as e:
            raise ChannelError(f"채널 페이지를 열지 못했어요: {e}") from e
        found = (re.search(r'"externalId":"(UC[\w-]{22})"', html)
                 or re.search(r'<meta itemprop="(?:channelId|identifier)" content="(UC[\w-]{22})"', html)
                 or re.search(r'youtube\.com/channel/(UC[\w-]{22})', html))
        if not found:
            raise ChannelError("이 주소에서 채널 ID를 찾지 못했어요. 채널 페이지 주소(@이름)를 넣어 주세요.")
        cid = found.group(1)
    try:
        name, _ = parse_feed(fetch_feed(cid))
    except Exception as e:
        raise ChannelError(f"채널 RSS를 읽지 못했어요({cid}): {e}") from e
    return cid, name or cid


def parse_takeout(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Google 테이크아웃 구독정보 CSV(채널 ID, 채널 URL, 채널 제목) → [(채널 ID, 이름)]."""
    out, seen = [], set()
    cols = list(df.columns)
    name_col = next((c for c in cols if "title" in c.lower() or "제목" in c or "이름" in c), cols[-1] if cols else None)
    for _, r in df.iterrows():
        cid = None
        for c in cols:
            m = CHANNEL_ID_RE.search(str(r[c]))
            if m:
                cid = m.group(1)
                break
        if cid and cid not in seen:
            seen.add(cid)
            out.append((cid, str(r[name_col]).strip() if name_col is not None else cid))
    return out


def refresh_feeds(channels: list[dict]) -> tuple[int, list[str]]:
    """활성 채널의 RSS를 읽어 videos 표에 넣는다. (영상 수, 오류 메시지 목록)"""
    errors, collected = [], []

    def one(c):
        try:
            _, vids = parse_feed(fetch_feed(c["channel_id"]))
            return vids, None
        except Exception as e:
            return [], f"{c.get('channel_name') or c['channel_id']}: {e}"

    with ThreadPoolExecutor(max_workers=6) as ex:
        for vids, err in ex.map(one, channels):
            collected.extend(vids)
            if err:
                errors.append(err)
    if collected:
        upsert_videos(collected)
    return len(collected), errors


# ── 오늘 시장·내 포트폴리오 요약 ────────────────────────────

MARKET_SYMBOLS = {"^GSPC": "S&P 500", "^NDX": "나스닥 100", "^KS11": "코스피", "KRW=X": "원/달러",
                  "^TNX": "미국 10년물 금리", "^VIX": "VIX", "GC=F": "금", "CL=F": "WTI 유가", "BTC-USD": "비트코인"}


def market_context(closes: dict[str, pd.Series]) -> dict:
    """종가 시계열 → 지표별 숫자와 오늘 시장 상황 태그·관련 단어."""
    items = {}
    for sym, name in MARKET_SYMBOLS.items():
        s = closes.get(sym)
        if s is None:
            continue
        s = pd.Series(s).dropna()
        if len(s) < 2:
            continue
        last = float(s.iloc[-1])
        year = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
        month_ago = s[s.index <= s.index[-1] - pd.Timedelta(days=30)]
        base_m = float(month_ago.iloc[-1]) if not month_ago.empty else float(s.iloc[0])
        is_rate = sym == "^TNX"
        items[sym] = {
            "name": name, "last": round(last, 4),
            "d1": round((last - float(s.iloc[-2])) if is_rate else (last / float(s.iloc[-2]) - 1) * 100, 2),
            "m1": round((last - base_m) if is_rate else (last / base_m - 1) * 100, 2),
            "from_high": round((last / float(year.max()) - 1) * 100, 2),
            "unit": "%p" if is_rate else "%",
        }
    tags: list[str] = []
    kw: dict[str, float] = {}

    def tag(label, words, w=3.0):
        tags.append(label)
        for x in words:
            kw[x] = max(kw.get(x, 0), w)

    sp = items.get("^GSPC")
    if sp:
        if sp["from_high"] <= -20:
            tag(f"약세장 (S&P 500 고점 대비 {sp['from_high']:.0f}%)", ["폭락", "약세장", "침체", "바닥", "공포", "하락", "위기"])
        elif sp["from_high"] <= -10:
            tag(f"조정장 (S&P 500 고점 대비 {sp['from_high']:.0f}%)", ["조정", "하락", "폭락", "저점", "매수 기회", "급락"])
        elif sp["from_high"] > -3:
            tag("미국 증시 고점 근처", ["신고가", "고점", "버블", "과열", "랠리", "상승"], 2.0)
    vix = items.get("^VIX")
    if vix:
        if vix["last"] >= 25:
            tag(f"변동성 큼 (VIX {vix['last']:.0f})", ["공포", "변동성", "위기", "리스크", "급락", "VIX"])
        elif vix["last"] < 15:
            tag(f"시장 평온 (VIX {vix['last']:.0f})", ["낙관", "과열"], 1.0)
    tnx = items.get("^TNX")
    if tnx and abs(tnx["m1"]) >= 0.25:
        tag(f"미국 금리 {'상승' if tnx['m1'] > 0 else '하락'} (한 달 {tnx['m1']:+.2f}%p)",
            ["금리", "국채", "채권", "연준", "FOMC", "파월", "기준금리"])
    krw = items.get("KRW=X")
    if krw and abs(krw["m1"]) >= 2:
        tag(f"원화 {'약세' if krw['m1'] > 0 else '강세'} (원/달러 한 달 {krw['m1']:+.1f}%)", ["환율", "달러", "원화", "원달러"])
    gold = items.get("GC=F")
    if gold and abs(gold["m1"]) >= 5:
        tag(f"금값 {'급등' if gold['m1'] > 0 else '급락'} (한 달 {gold['m1']:+.1f}%)", ["금값", "골드", "금 투자", "금 가격"])
    oil = items.get("CL=F")
    if oil and abs(oil["m1"]) >= 10:
        tag(f"유가 급변 (한 달 {oil['m1']:+.1f}%)", ["유가", "원유", "석유", "OPEC"])
    btc = items.get("BTC-USD")
    if btc and abs(btc["m1"]) >= 15:
        tag(f"비트코인 급변 (한 달 {btc['m1']:+.1f}%)", ["비트코인", "코인", "가상자산"])
    ks = items.get("^KS11")
    if ks and abs(ks["m1"]) >= 5:
        tag(f"코스피 {'급등' if ks['m1'] > 0 else '급락'} (한 달 {ks['m1']:+.1f}%)", ["코스피", "국내 증시", "한국 증시", "국장"])
    for x in ("시장 전망", "증시", "경제", "투자 전략", "자산배분", "포트폴리오"):
        kw.setdefault(x, 0.5)
    return {"items": items, "tags": tags, "keywords": kw}


def load_market_context() -> dict:
    ohlc = market.fetch_yf_ohlc(tuple(MARKET_SYMBOLS))
    return market_context({s: df["Close"] for s, df in ohlc.items() if df is not None and not df.empty})


# 자산군·종목 이름에 이 글자가 있으면 영상 제목에서 오른쪽 단어를 찾는다
PORTFOLIO_WORDS = [
    (("금", "골드", "gold"), ["금값", "골드", "금 투자", "금 가격", "금 ETF"]),
    (("채권", "국채", "treasury", "bond", "tlt"), ["채권", "국채", "금리", "TLT"]),
    (("s&p", "sp500", "미국", "voo", "spy"), ["S&P", "미국 주식", "미국 증시", "미장"]),
    (("나스닥", "nasdaq", "qqq", "기술"), ["나스닥", "빅테크", "AI", "엔비디아"]),
    (("배당", "schd", "dividend"), ["배당", "배당주", "SCHD"]),
    (("코인", "비트", "btc", "crypto"), ["비트코인", "코인"]),
    (("달러", "usd", "외화"), ["달러", "환율"]),
    (("현금", "cd", "rp", "적금", "예금", "sgov", "bil"), ["현금", "예금", "파킹"]),
    (("코스피", "국내", "한국", "kodex", "tiger"), ["코스피", "국내 주식", "국장"]),
    (("리츠", "reit", "부동산"), ["리츠", "부동산"]),
]


def portfolio_context(summary: pd.DataFrame, valued: pd.DataFrame, names: dict[str, str],
                      asset_class: dict[str, str], goal_total: float | None = None) -> dict:
    """자산군 요약·종목 평가 → 해설과 영상 고르기에 쓸 작은 요약 (원화 기준)."""
    if summary is None or summary.empty or float(summary["value_krw"].sum()) <= 0:
        return {}
    total = float(summary["value_krw"].sum())
    cost = float(summary["cost_krw"].sum())
    classes = []
    for _, r in summary.iterrows():
        if r["value_krw"] <= 0 and r["target_weight"] <= 0:
            continue
        classes.append({"name": r["asset_class"], "weight": round(float(r["weight"]), 1),
                        "target": round(float(r["target_weight"]), 1), "diff": round(float(r["diff_pp"]), 1),
                        "pnl_pct": None if pd.isna(r["pnl_pct"]) else round(float(r["pnl_pct"]), 1),
                        "monthly": float(r.get("monthly_contribution") or 0)})
    holdings = []
    if valued is not None and not valued.empty:
        held = valued[valued["quantity"] > 0].sort_values("value_krw", ascending=False)
        for _, r in held.head(8).iterrows():
            holdings.append({"name": names.get(r["symbol"]) or r["symbol"].replace("MANUAL:", ""),
                             "asset_class": asset_class.get(r["symbol"], ""),
                             "weight": round(float(r["value_krw"]) / total * 100, 1),
                             "pnl_pct": None if pd.isna(r["pnl_krw_pct"]) else round(float(r["pnl_krw_pct"]), 1)})
    text = " ".join([c["name"] for c in classes] + [h["name"] for h in holdings] + list(names)).lower()
    kw = {}
    for keys, words in PORTFOLIO_WORDS:
        if any(k in text for k in keys):
            for w in words:
                kw[w] = 2.0
    return {"total_krw": round(total), "pnl_pct": round((total / cost - 1) * 100, 1) if cost else None,
            "goal_progress": round(total / goal_total * 100, 1) if goal_total else None,
            "monthly_total": sum(c["monthly"] for c in classes),
            "classes": classes, "holdings": holdings, "keywords": kw}


def load_portfolio_context() -> dict:
    """Supabase의 거래·현금·목표 비중과 현재 시세로 포트폴리오 요약을 만든다."""
    from lib import portfolio as pf

    tx = pf.list_transactions()
    assets = pf.list_assets()
    targets = pf.list_targets()
    cash = pf.list_cash()
    goal = float(pf.get_setting("goal_total", 0) or 0)
    if tx.empty and cash.empty:
        return {}
    info = assets.set_index("symbol").to_dict("index") if not assets.empty else {}
    asset_class = {s: (i.get("asset_class") if isinstance(i.get("asset_class"), str) and i.get("asset_class")
                       else pf.UNCLASSIFIED) for s, i in info.items()}
    names = {s: i.get("name") for s, i in info.items() if isinstance(i.get("name"), str)}
    ccys = (set(tx["currency"].dropna()) | set(cash["currency"].dropna()) | {"USD"}) - {"KRW"}

    def fx_ticker(c):
        return "KRW=X" if c == "USD" else f"{c}KRW=X"

    positions, _ = pf.compute_positions(tx)
    held = list(positions.loc[positions["quantity"] > 1e-9, "symbol"])
    quoted = sorted(s for s in held if not s.startswith("MANUAL:"))
    try:
        closes = market.fetch_yf_closes(tuple(sorted(set(quoted) | {fx_ticker(c) for c in ccys})))
    except Exception:
        closes = pd.DataFrame()
    last = {c: float(closes[c].dropna().iloc[-1]) for c in closes.columns if not closes[c].dropna().empty}
    fx = {c: last[fx_ticker(c)] for c in ccys if fx_ticker(c) in last}
    prices = {}
    for s in held:
        manual = (info.get(s) or {}).get("manual_price")
        if s in last and not s.startswith("MANUAL:"):
            prices[s] = last[s]
        elif s.startswith("MANUAL:") and (auto := market.auto_price(s)):
            prices[s] = auto
        elif manual is not None and not pd.isna(manual):
            prices[s] = float(manual)
    positions, _ = pf.compute_positions(tx, fx)
    valued = pf.value_positions(positions, prices, fx)
    summary = pf.class_summary(valued, asset_class, cash, targets, fx, goal or None)
    return portfolio_context(summary, valued, names, asset_class, goal or None)


# ── 기본 해설 (AI 없이) ─────────────────────────────────────

def _josa(word: str, pair: tuple[str, str]) -> str:
    """받침 유무로 조사 고르기: _josa('금', ('이','가')) → '금이'."""
    ch = word[-1] if word else ""
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    return word + (pair[0] if has else pair[1])


def basic_meaning(q: dict) -> str:
    return q.get("philosophy") or f"{q['guru']}의 '{q.get('theme') or '투자'}'에 관한 생각이다."


def basic_note(q: dict, p: dict, m: dict) -> str:
    """주제별로 내 포트폴리오 숫자를 넣은 짧은 설명."""
    if not p or not p.get("classes"):
        return "포트폴리오 데이터를 읽지 못해서, 오늘은 일반적인 의미만 보여 드려요."
    theme = q.get("theme") or ""
    classes = [c for c in p["classes"] if c["weight"] > 0 or c["target"] > 0]
    top = max(classes, key=lambda c: c["weight"])
    over = max(classes, key=lambda c: c["diff"])
    under = min(classes, key=lambda c: c["diff"])
    cash_w = sum(c["weight"] for c in classes if any(k in c["name"] for k in ("현금", "CD", "RP", "예금", "적금", "유보")))
    pnl = p.get("pnl_pct")
    pnl_txt = f"전체 평가손익은 {pnl:+.1f}%" if pnl is not None else "전체 평가손익은 아직 계산되지 않았고"
    items = (m or {}).get("items", {})
    sp, vix = items.get("^GSPC"), items.get("^VIX")
    monthly = p.get("monthly_total") or 0
    monthly_txt = f"매달 ₩{monthly:,.0f} 적립 계획" if monthly else "꾸준한 적립 계획"

    if theme == "분산":
        s = f"지금 포트폴리오는 {len([c for c in classes if c['weight'] > 0])}개 자산군에 나뉘어 있고, 가장 큰 비중은 {top['name']} {top['weight']:.0f}%예요."
        if over["diff"] >= 5:
            s += f" {_josa(over['name'], ('이', '가'))} 목표보다 {over['diff']:.1f}%p 많아 한쪽으로 쏠리지 않았는지 볼 때예요."
        else:
            s += " 목표 비중에서 크게 벗어난 자산군은 없어 분산은 계획대로 유지되고 있어요."
        return s
    if theme == "위험관리":
        s = f"현금성 자산은 전체의 {cash_w:.0f}%예요."
        if sp:
            s += f" S&P 500은 1년 고점 대비 {sp['from_high']:+.1f}% 위치예요."
        if over["diff"] >= 5:
            s += f" 목표보다 {over['diff']:.1f}%p 많은 {over['name']}에서 하락이 오면 전체 손실이 커질 수 있으니, 견딜 수 있는 비중인지 확인해 보세요."
        else:
            s += " 자산군 비중이 목표와 가까워 큰 하락이 와도 계획대로 버틸 수 있는 구조예요."
        return s
    if theme == "역발상":
        if sp and sp["from_high"] <= -10:
            return (f"S&P 500이 1년 고점보다 {abs(sp['from_high']):.0f}% 낮은 지금이 이 말의 '두려울 때'에 가까워요. "
                    f"목표보다 {abs(under['diff']):.1f}%p 덜 담긴 {under['name']}부터 채우는 데 이번 달 적립금을 쓰는 것을 생각해 볼 수 있어요.")
        if sp and sp["from_high"] > -3:
            return (f"S&P 500이 1년 고점 근처({sp['from_high']:+.1f}%)라 낙관이 큰 시기예요. "
                    f"목표보다 {over['diff']:+.1f}%p인 {over['name']}의 비중을 목표에 맞추는 리밸런싱이 이 말을 실천하는 가장 쉬운 방법이에요.")
        return (f"시장은 극단적인 공포도 열광도 아닌 상태예요. {pnl_txt}이고, "
                f"가장 덜 담긴 자산군은 {under['name']}({under['diff']:+.1f}%p)예요. 감정이 아니라 목표 비중을 기준으로 다음 매수를 정해 보세요.")
    if theme == "시장심리":
        s = f"{pnl_txt}예요."
        if vix:
            mood = "불안이 큰" if vix["last"] >= 25 else "평온한" if vix["last"] < 15 else "보통 수준의"
            s += f" VIX는 {vix['last']:.0f}로 {mood} 시장이에요."
        s += " 이런 숫자에 따라 계획을 바꾸고 싶어질 때가 이 말을 떠올릴 때예요."
        return s
    if theme in ("장기투자", "인내", "복리"):
        s = f"{pnl_txt}예요."
        if p.get("goal_progress"):
            s += f" 목표 총액의 {p['goal_progress']:.0f}%까지 왔어요."
        s += f" {monthly_txt}을 그대로 이어 가는 것이 이 말을 지키는 가장 확실한 방법이에요."
        return s
    if theme == "가치":
        h = (p.get("holdings") or [None])[0]
        if h:
            pl = f", 수익률 {h['pnl_pct']:+.1f}%" if h.get("pnl_pct") is not None else ""
            return (f"가장 큰 보유 종목은 {h['name']}(전체의 {h['weight']:.0f}%{pl})예요. "
                    f"지금 가격이 아니라 이 자산이 앞으로 벌어들일 가치를 보고 계속 들고 갈 이유가 있는지 한 번 정리해 보세요.")
        return f"가장 큰 자산군은 {top['name']}({top['weight']:.0f}%)예요. 그 자산의 가치를 믿는 이유를 한 문장으로 적어 보세요."
    if theme == "원칙":
        far = max(classes, key=lambda c: abs(c["diff"]))
        if abs(far["diff"]) >= 3:
            return (f"목표 비중에서 가장 멀어진 자산군은 {far['name']}({far['diff']:+.1f}%p)예요. "
                    "미리 정한 원칙(목표 비중, 적립 계획)대로 맞추면 그날의 기분에 흔들리지 않아요.")
        return f"모든 자산군이 목표 비중의 ±3%p 안에 있어요. 지금은 정한 원칙대로 {monthly_txt}만 이어 가면 돼요."
    if theme == "겸손":
        return (f"가장 큰 자산군 {top['name']}도 전체의 {top['weight']:.0f}%예요. "
                "어느 하나의 예측이 틀려도 전체가 무너지지 않도록 나눠 담은 것이 지금 포트폴리오의 장점이에요.")
    if theme == "비용":
        n = len(p.get("holdings") or [])
        return (f"보유 종목 상위 {n}개를 보면 대부분 장기 보유 자산이에요. 매매 횟수와 ETF 보수를 줄이는 것만으로 "
                "매년 확실한 수익률이 지켜져요. 다음 리밸런싱은 팔기보다 적립금으로 맞추는 쪽을 먼저 생각해 보세요.")
    return f"{pnl_txt}예요. 가장 큰 자산군은 {top['name']}({top['weight']:.0f}%)예요."


# ── 영상 점수 ───────────────────────────────────────────────

def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = pd.Timestamp(s)
        return (dt.tz_localize("UTC") if dt.tzinfo is None else dt).to_pydatetime()
    except Exception:
        return None


def score_videos(videos: list[dict], m: dict, p: dict, history: list[dict], channels: dict[str, str],
                 d: date, now: datetime | None = None) -> list[dict]:
    """영상마다 점수와 추천 이유를 붙여 높은 순으로 돌려준다. 이미 본 영상, 최근 추천한 영상, 오래된 영상은 뺀다."""
    now = now or datetime.now(timezone.utc)
    watched = {h["video_id"] for h in history if h.get("watched")}
    recent = {h["video_id"] for h in history
              if _date_str(h["date"]) != _date_str(d)
              and date.fromisoformat(_date_str(h["date"])) >= d - timedelta(days=VIDEO_REPEAT_DAYS)}
    vid_channel = {v["video_id"]: v["channel_id"] for v in videos}
    liked = {}
    for h in history:
        if h.get("liked"):
            c = vid_channel.get(h["video_id"])
            if c:
                liked[c] = liked.get(c, 0) + 1
    mk = (m or {}).get("keywords", {})
    pk = (p or {}).get("keywords", {})
    out = []
    for v in videos:
        if v["video_id"] in watched or v["video_id"] in recent or v.get("channel_id") not in channels:
            continue
        pub = _parse_dt(v.get("published_at"))
        age = (now - pub).total_seconds() / 86400 if pub else 99
        if age > VIDEO_MAX_AGE_DAYS:
            continue
        title = v.get("title") or ""
        text = (title + " " + (v.get("description") or "")[:300]).lower()
        hit_m = [k for k in mk if k.lower() in text]
        hit_p = [k for k in pk if k.lower() in text and k not in hit_m]
        score = sum(mk[k] * (1.5 if k.lower() in title.lower() else 1) for k in hit_m)
        score += sum(pk[k] * (1.5 if k.lower() in title.lower() else 1) for k in hit_p)
        score += 2 if age <= 2 else 1 if age <= 7 else 0
        score += min(liked.get(v["channel_id"], 0), 2)
        if "#shorts" in text or "#short" in text:
            score -= 3
        out.append({**v, "channel_name": channels.get(v["channel_id"], ""), "score": round(score, 2),
                    "hits_market": [k for k in hit_m if mk[k] >= 1], "hits_portfolio": hit_p,
                    "age_days": round(age, 1)})
    out.sort(key=lambda x: (-x["score"], x["age_days"]))
    return out


def basic_reason(v: dict, m: dict) -> str:
    parts = []
    if v.get("hits_market"):
        tags = [t for t in (m or {}).get("tags", [])]
        words = "·".join(f"'{k}'" for k in v["hits_market"][:3])
        parts.append(f"요즘 시장({tags[0] if tags else '오늘 시장'})과 관련된 {words} 이야기를 다뤄요.")
    if v.get("hits_portfolio"):
        words = "·".join(f"'{k}'" for k in v["hits_portfolio"][:2])
        parts.append(f"내 보유 자산과 관련된 {words} 내용이 있어요.")
    if not parts:
        parts.append(f"{v.get('channel_name') or '구독 채널'}의 최신 영상이에요.")
    if v.get("age_days", 99) <= 2:
        parts.append("최근 이틀 안에 올라왔어요.")
    return " ".join(parts)


def pick_videos_basic(scored: list[dict], m: dict, n: int = VIDEOS_PER_DAY) -> list[dict]:
    """점수 순으로 n개, 처음에는 채널이 겹치지 않게."""
    picked, used = [], set()
    for v in scored:
        if v["channel_id"] not in used:
            picked.append(v)
            used.add(v["channel_id"])
        if len(picked) == n:
            break
    for v in scored:
        if len(picked) == n:
            break
        if v not in picked:
            picked.append(v)
    return [{"video_id": v["video_id"], "reason": basic_reason(v, m), "made_by": "basic"} for v in picked]


# ── Claude ─────────────────────────────────────────────────

class ClaudeError(Exception):
    pass


def claude_enabled() -> bool:
    return bool(secret("ANTHROPIC_API_KEY"))


def ask_claude_json(system: str, prompt: str, schema: dict) -> dict:
    """Claude에 JSON 응답을 받는다. 키가 없거나 실패하면 ClaudeError."""
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        raise ClaudeError("ANTHROPIC_API_KEY가 없어요.")
    try:
        import anthropic
    except ImportError as e:
        raise ClaudeError("anthropic 패키지가 설치되지 않았어요.") from e
    client = anthropic.Anthropic(api_key=key, timeout=120.0)
    try:
        resp = client.beta.messages.create(
            model=secret("ANTHROPIC_MODEL", DEFAULT_MODEL),
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Claude API 오류 {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError(f"Claude API 연결 실패: {e}") from e
    if resp.stop_reason == "refusal":
        raise ClaudeError("Claude가 응답을 거절했어요.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ClaudeError("Claude 응답을 JSON으로 읽지 못했어요.")


SYSTEM_COACH = (
    "당신은 한국의 개인 투자자 인영 님의 투자 코치입니다. 인영 님은 자산군별 목표 비중을 정해 두고 "
    "매달 적립하며 장기 투자합니다. 존댓말(해요체)로, 쉬운 한국어로 씁니다. 특정 종목의 매수·매도를 지시하지 말고, "
    "주어진 숫자(비중, 목표와의 차이, 수익률, 시장 지표)를 근거로 점검할 점을 제안합니다. 숫자를 지어내지 마세요."
)


def _ctx_text(p: dict, m: dict) -> str:
    port = {k: p.get(k) for k in ("total_krw", "pnl_pct", "goal_progress", "monthly_total", "classes", "holdings")} if p else {}
    mk = {"지표": list((m or {}).get("items", {}).values()), "상황": (m or {}).get("tags", [])}
    return ("[내 포트폴리오 (원화 기준, weight·target은 %, diff는 목표 대비 %p)]\n"
            + json.dumps(port, ensure_ascii=False) + "\n\n[오늘 시장 (d1: 전일 대비, m1: 한 달 변화, from_high: 1년 고점 대비)]\n"
            + json.dumps(mk, ensure_ascii=False))


QUOTE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "meaning", "note"],
        "properties": {"id": {"type": "integer"}, "meaning": {"type": "string"}, "note": {"type": "string"}}}}},
}


def claude_quote_notes(quotes: list[dict], p: dict, m: dict) -> dict[int, tuple[str, str]]:
    qs = [{"id": q["id"], "guru": q["guru"], "quote": q["quote_ko"], "original": q.get("quote_original"),
           "theme": q.get("theme")} for q in quotes]
    prompt = (_ctx_text(p, m) + "\n\n[오늘의 명언]\n" + json.dumps(qs, ensure_ascii=False) +
              "\n\n각 명언마다 두 가지를 써 주세요.\n"
              "- meaning: 이 말에 담긴 투자 철학 해설 (2~3문장)\n"
              "- note: 이 철학이 지금 인영 님 포트폴리오와 오늘 시장에 주는 의미 (2~4문장, 위 숫자를 1~2개 인용)")
    data = ask_claude_json(SYSTEM_COACH, prompt, QUOTE_SCHEMA)
    out = {}
    for it in data.get("items", []):
        if it.get("id") is not None and it.get("meaning") and it.get("note"):
            out[int(it["id"])] = (it["meaning"].strip(), it["note"].strip())
    if not all(q["id"] in out for q in quotes):
        raise ClaudeError("Claude 응답에 빠진 명언이 있어요.")
    return out


VIDEO_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["video_id", "reason"],
        "properties": {"video_id": {"type": "string"}, "reason": {"type": "string"}}}}},
}


def claude_pick_videos(cands: list[dict], p: dict, m: dict, n: int = VIDEOS_PER_DAY) -> list[dict]:
    rows = [{"video_id": v["video_id"], "channel": v.get("channel_name"), "title": v.get("title"),
             "published": str(v.get("published_at"))[:10], "description": (v.get("description") or "")[:200]}
            for v in cands]
    prompt = (_ctx_text(p, m) + "\n\n[구독 채널의 최근 영상 후보]\n" + json.dumps(rows, ensure_ascii=False) +
              f"\n\n지금 시장 상황과 인영 님 포트폴리오에 가장 도움이 될 영상 {n}개를 골라 주세요. "
              "같은 채널은 가능하면 피하고, 쇼츠나 광고성 영상은 피하세요. "
              "reason에는 왜 지금 볼 만한지 한 문장(60자 안팎)으로 씁니다.")
    data = ask_claude_json(SYSTEM_COACH, prompt, VIDEO_SCHEMA)
    ids = {v["video_id"] for v in cands}
    picks, seen = [], set()
    for it in data.get("picks", []):
        vid = it.get("video_id")
        if vid in ids and vid not in seen and it.get("reason"):
            seen.add(vid)
            picks.append({"video_id": vid, "reason": it["reason"].strip(), "made_by": "claude"})
    if not picks:
        raise ClaudeError("Claude가 고른 영상이 후보에 없어요.")
    return picks[:n]


# ── 오늘 것 만들기 ──────────────────────────────────────────

def load_contexts() -> tuple[dict, dict, list[str]]:
    """(포트폴리오 요약, 시장 요약, 오류 목록). 실패해도 빈 요약으로 계속한다."""
    errors = []
    try:
        p = load_portfolio_context()
    except Exception as e:
        p = {}
        errors.append(f"포트폴리오 요약 실패: {e}")
    try:
        m = load_market_context()
    except Exception as e:
        m = {}
        errors.append(f"시장 지표 실패: {e}")
    return p, m, errors


def make_quotes(d: date, p: dict, m: dict, reroll: bool = False, same_quotes: bool = False
                ) -> tuple[list[dict], list[str]]:
    """그날의 명언 2개와 해설을 만들어 저장한다. (저장한 목록, 오류)"""
    errors = []
    seed_quotes()
    current = daily_quotes(d)
    quotes = list_quotes(active_only=True)
    if same_quotes and current:
        by_id = {q["id"]: q for q in list_quotes()}
        picked = [by_id[c["quote_id"]] for c in current if c["quote_id"] in by_id]
    else:
        recent = {r["quote_id"] for r in _all("daily_quotes")
                  if _date_str(r["date"]) != _date_str(d)
                  and date.fromisoformat(_date_str(r["date"])) >= d - timedelta(days=QUOTE_COOLDOWN_DAYS)}
        exclude = {c["quote_id"] for c in current} if reroll else set()
        salt = random.randint(1, 10 ** 6) if reroll else 0
        picked = pick_quotes(quotes, recent, d, salt=salt, exclude=exclude)
    notes, made_by = {}, "basic"
    if claude_enabled() and picked:
        try:
            notes = claude_quote_notes(picked, p, m)
            made_by = "claude"
        except Exception as e:
            errors.append(f"AI 해설 실패, 기본 해설로 대신했어요: {e}")
    items = []
    for q in picked:
        meaning, note = notes.get(q["id"]) or (basic_meaning(q), basic_note(q, p, m))
        items.append({"quote_id": q["id"], "meaning": meaning, "my_portfolio_note": note, "made_by": made_by})
    save_daily_quotes(d, items)
    return daily_quotes(d), errors


def make_videos(d: date, p: dict, m: dict, refresh: bool = True) -> tuple[list[dict], list[str]]:
    """구독 채널 RSS를 새로 읽고 그날의 추천 3개를 골라 저장한다."""
    errors = []
    channels = list_channels(active_only=True)
    if not channels:
        return [], ["추천에 쓸 채널이 없어요. '채널 관리'에서 채널을 넣어 주세요."]
    if refresh:
        _, errs = refresh_feeds(channels)
        errors += [f"RSS 실패 — {e}" for e in errs]
    names = {c["channel_id"]: c.get("channel_name") or c["channel_id"] for c in channels}
    scored = score_videos(list_videos(), m, p, _all("daily_videos"), names, d)
    if not scored:
        errors.append("최근 30일 안에 올라온 새 영상이 없어요.")
        return daily_videos(d), errors
    picks = []
    if claude_enabled():
        try:
            picks = claude_pick_videos(scored[:20], p, m)
        except Exception as e:
            errors.append(f"AI 추천 실패, 기본 추천으로 대신했어요: {e}")
    if len(picks) < VIDEOS_PER_DAY:
        have = {x["video_id"] for x in picks}
        rest = pick_videos_basic([v for v in scored if v["video_id"] not in have], m, VIDEOS_PER_DAY - len(picks))
        picks += rest
    save_daily_videos(d, picks)
    return daily_videos(d), errors


def ensure_today(d: date | None = None, ctx: tuple[dict, dict] | None = None) -> dict:
    """오늘 것이 없으면 만든다 (예약 작업과 화면 공통). 결과 요약을 돌려준다."""
    d = d or today_kst()
    out = {"date": d, "quotes": daily_quotes(d), "videos": daily_videos(d), "errors": [], "made": []}
    need_q, need_v = not out["quotes"], not out["videos"] and bool(list_channels(active_only=True))
    if not (need_q or need_v):
        return out
    if ctx is None:
        p, m, errs = load_contexts()
        out["errors"] += errs
    else:
        p, m = ctx
    if need_q:
        out["quotes"], errs = make_quotes(d, p, m)
        out["errors"] += errs
        out["made"].append("quotes")
    if need_v:
        out["videos"], errs = make_videos(d, p, m)
        out["errors"] += errs
        out["made"].append("videos")
    return out
