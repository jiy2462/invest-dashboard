import pandas as pd
import streamlit as st

from lib import insight as ins
from lib import ui
from lib.quotes_data import THEMES

st.title("오늘의 인사이트")

try:
    ins.seed_quotes()
    channels = ins.list_channels()
except Exception:
    st.error("DB에 연결하지 못했어요. Supabase에 `supabase/005_insight.sql`이 적용됐는지 확인해 주세요.")
    st.stop()

today = ins.today_kst()

# 오늘 것이 없으면 만든다 (한 세션에서 한 번만)
if st.session_state.get("insight_checked") != today:
    with st.spinner("오늘의 명언과 추천 영상을 고르는 중…"):
        res = ins.ensure_today(today)
    st.session_state.insight_checked = today
    st.session_state.insight_errors = res["errors"]


def run_with_context(label: str, fn):
    """포트폴리오·시장 요약을 불러와 fn(p, m)을 실행하고 오류를 남긴다."""
    with st.spinner(label):
        p, m, errs = ins.load_contexts()
        _, more = fn(p, m)
    st.session_state.insight_errors = errs + more
    st.rerun()


def quote_card(q: dict, key: str) -> None:
    with ui.card(f"quote-{key}"):
        st.markdown(f"#### “{q['quote_ko']}”")
        if q.get("quote_original"):
            st.caption(q["quote_original"])
        src = f" · {q['source']}" if q.get("source") else ""
        st.markdown(f"**— {q['guru']}**{src}  ·  `{q.get('theme') or ''}`")
        st.markdown("**투자 철학**")
        st.write(q.get("meaning") or q.get("philosophy") or "")
        st.markdown("**지금 내 포트폴리오에 주는 의미**")
        st.write(q.get("my_portfolio_note") or "")
        if q.get("made_by") == "claude":
            st.caption("해설: Claude")


def video_card(v: dict, d, key: str) -> None:
    with ui.card(f"video-{key}"):
        c1, c2 = st.columns([1, 2])
        if v.get("thumbnail_url"):
            c1.image(v["thumbnail_url"], width="stretch")
        url = f"https://www.youtube.com/watch?v={v['video_id']}"
        c2.markdown(f"**[{v['title']}]({url})**")
        pub = str(v.get("published_at") or "")[:10]
        c2.caption(f"{v.get('channel_name') or ''} · {pub}")
        if v.get("reason"):
            c2.write(v["reason"])
        b1, b2, _ = c2.columns([1, 1, 2])
        if b1.button("✅ 봤어요" if v.get("watched") else "봤어요", key=f"w-{key}"):
            ins.mark_video(d, v["video_id"], watched=not v.get("watched"))
            st.rerun()
        if b2.button("❤️ 좋아요" if v.get("liked") else "좋아요", key=f"l-{key}"):
            ins.mark_video(d, v["video_id"], liked=not v.get("liked"))
            st.rerun()


tab_today, tab_hist, tab_ch, tab_q = st.tabs(["오늘", "지난 기록", "채널 관리", "명언 목록"])

with tab_today:
    errs = st.session_state.get("insight_errors") or []
    if errs:
        with st.expander(f"알림 {len(errs)}건", expanded=False):
            for e in errs:
                st.caption(e)
    if not ins.claude_enabled():
        st.caption("AI 해설이 꺼져 있어 기본 해설을 보여 드려요. Secrets에 `ANTHROPIC_API_KEY`를 넣으면 Claude가 매일 해설과 추천 이유를 써요.")

    st.subheader(f"오늘의 명언 · {today:%m월 %d일}")
    quotes = ins.daily_quotes(today)
    for i, q in enumerate(quotes):
        quote_card(q, f"t{i}")
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("다른 명언 뽑기", key="reroll"):
        run_with_context("새 명언을 고르는 중…", lambda p, m: ins.make_quotes(today, p, m, reroll=True))
    if c2.button("해설 다시 만들기", key="renote", disabled=not quotes):
        run_with_context("해설을 새로 쓰는 중…", lambda p, m: ins.make_quotes(today, p, m, same_quotes=True))

    st.subheader("오늘의 추천 영상")
    active = [c for c in channels if c.get("is_active", True)]
    if not active:
        st.info("추천에 쓸 유튜브 채널이 아직 없어요. ‘채널 관리’ 탭에서 구독 중인 경제 채널을 넣어 주세요.")
    else:
        vids = ins.daily_videos(today)
        if not vids:
            st.caption("오늘 추천할 새 영상을 찾지 못했어요.")
        for i, v in enumerate(vids):
            video_card(v, today, f"t{i}")
        if st.button("영상 다시 고르기", key="revideo"):
            run_with_context("채널의 새 영상을 읽는 중…", lambda p, m: ins.make_videos(today, p, m))

with tab_hist:
    dates = sorted(set(ins.quote_dates()) | set(ins.video_dates()), reverse=True)
    past = [d for d in dates if d != today]
    if not past:
        st.caption("아직 지난 기록이 없어요. 내일부터 여기서 지난 명언과 추천을 다시 볼 수 있어요.")
    else:
        d = st.selectbox("날짜", past, format_func=lambda x: f"{x:%Y-%m-%d (%a)}")
        for i, q in enumerate(ins.daily_quotes(d)):
            quote_card(q, f"h{i}")
        for i, v in enumerate(ins.daily_videos(d)):
            video_card(v, d, f"h{i}")

with tab_ch:
    st.caption("구독 중인 경제 채널을 넣어 두면 매일 그 채널들의 최근 영상 중에서 추천해요. "
               "채널 주소(youtube.com/@이름), @이름, 채널 ID(UC로 시작) 모두 돼요.")
    if st.session_state.get("channel_msg"):
        st.success(st.session_state.pop("channel_msg"))
    with st.form("add-channel", clear_on_submit=True):
        c1, c2 = st.columns([4, 1])
        text = c1.text_input("채널 주소", placeholder="https://www.youtube.com/@채널이름", label_visibility="collapsed")
        if c2.form_submit_button("추가", width="stretch") and text.strip():
            try:
                cid, name = ins.resolve_channel(text)
                ins.add_channel(cid, name)
                st.session_state.channel_msg = f"‘{name}’ 채널을 추가했어요. ‘오늘’ 탭에서 ‘영상 다시 고르기’를 누르면 바로 반영돼요."
                st.rerun()
            except ins.ChannelError as e:
                st.error(str(e))

    with st.expander("Google 테이크아웃 구독 목록(CSV)으로 한꺼번에 넣기"):
        st.caption("takeout.google.com → YouTube → ‘구독정보’만 선택해 내보내면 subscriptions.csv가 생겨요. "
                   "경제 채널만 골라 넣으세요.")
        up = st.file_uploader("subscriptions.csv", type=["csv"], key="takeout")
        if up is not None:
            try:
                found = ins.parse_takeout(pd.read_csv(up))
            except Exception as e:
                found = []
                st.error(f"CSV를 읽지 못했어요: {e}")
            have = {c["channel_id"] for c in channels}
            new = [(cid, n) for cid, n in found if cid not in have]
            if found and not new:
                st.caption("CSV의 채널이 모두 이미 들어 있어요.")
            if new:
                pick = st.multiselect(f"넣을 채널 ({len(new)}개 중)", new, format_func=lambda x: x[1])
                if st.button("선택한 채널 넣기", disabled=not pick):
                    for cid, n in pick:
                        ins.add_channel(cid, n)
                    st.rerun()

    if channels:
        st.markdown(f"**채널 {len(channels)}개**")
        for c in channels:
            c1, c2, c3 = st.columns([5, 1, 1])
            c1.markdown(f"[{c['channel_name']}](https://www.youtube.com/channel/{c['channel_id']})")
            on = c2.toggle("사용", value=bool(c.get("is_active", True)), key=f"on-{c['channel_id']}")
            if on != bool(c.get("is_active", True)):
                ins.set_channel_active(c["channel_id"], on)
                st.rerun()
            if c3.button("삭제", key=f"del-{c['channel_id']}"):
                ins.delete_channel(c["channel_id"])
                st.rerun()

with tab_q:
    rows = ins.list_quotes()
    st.caption(f"명언 {len(rows)}개 중 {sum(1 for r in rows if r.get('is_active', True))}개를 사용 중이에요. "
               "‘사용’을 끄면 더 이상 뽑히지 않아요. 문구도 바로 고칠 수 있어요.")
    cols = ["id", "is_active", "guru", "quote_ko", "theme", "quote_original", "source", "philosophy"]
    df = pd.DataFrame(rows, columns=cols)
    df["is_active"] = df["is_active"].fillna(True).astype(bool)
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key="quotes-editor", disabled=["id"],
        column_config={
            "id": None,
            "is_active": st.column_config.CheckboxColumn("사용", width="small"),
            "guru": st.column_config.TextColumn("구루"),
            "quote_ko": st.column_config.TextColumn("명언", width="large"),
            "theme": st.column_config.SelectboxColumn("주제", options=THEMES),
            "quote_original": st.column_config.TextColumn("원문"),
            "source": st.column_config.TextColumn("출처"),
            "philosophy": st.column_config.TextColumn("투자 철학 풀이", width="large"),
        },
    )
    if st.button("변경 저장", key="save-quotes"):
        orig = df.set_index("id")
        n = 0
        for _, r in edited.iterrows():
            o = orig.loc[r["id"]]
            changes = {c: (None if pd.isna(r[c]) else r[c]) for c in cols[1:]
                       if not (pd.isna(r[c]) and pd.isna(o[c])) and r[c] != o[c]}
            if changes:
                ins.update_quote(int(r["id"]), changes)
                n += 1
        st.success(f"{n}개 명언을 고쳤어요." if n else "바뀐 내용이 없어요.")

    with st.expander("명언 직접 추가"):
        with st.form("add-quote", clear_on_submit=True):
            g = st.text_input("구루")
            k = st.text_area("명언 (한글)")
            o = st.text_input("원문 (선택)")
            s = st.text_input("출처 (선택)")
            t = st.selectbox("주제", THEMES)
            ph = st.text_area("투자 철학 풀이 (선택)")
            if st.form_submit_button("추가") and g.strip() and k.strip():
                ins.add_quote({"guru": g.strip(), "quote_ko": k.strip(), "quote_original": o.strip(),
                               "source": s.strip(), "theme": t, "philosophy": ph.strip()})
                st.success("추가했어요.")
