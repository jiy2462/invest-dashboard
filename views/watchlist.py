import pandas as pd
import streamlit as st

from lib import charting, symbols
from lib import watchlist as wl
from lib.market import fetch_yf_closes

st.title("관심종목·차트")

if "chart_symbol" not in st.session_state:
    st.session_state.chart_symbol = None

# ── 종목 검색 ───────────────────────────────────────────────
query = st.text_input("종목 검색", placeholder="삼성전자, apple, AAPL, 005930 …")
if query:
    with st.spinner("찾는 중…"):
        hits = symbols.search(query)
    if hits:
        pick = st.selectbox("검색 결과", hits, format_func=lambda h: f"{h['name']} · {h['symbol']} ({h['market']})",
                            key=f"hits-{query}")
        if st.button("차트 보기", type="primary"):
            st.session_state.chart_symbol = pick
            st.rerun()
    else:
        st.caption("결과가 없어요. 티커를 그대로 넣어 보세요 (예: 005930.KS, AAPL).")
        if st.button(f"‘{query.strip()}’ 티커로 열기"):
            sym = query.strip().upper()
            st.session_state.chart_symbol = {"symbol": sym, "name": symbols.name_for(sym), "market": symbols.market_for(sym)}
            st.rerun()

# ── 관심종목 표 ─────────────────────────────────────────────
try:
    watch = wl.list_watchlist()
except Exception:
    st.error("DB에 연결하지 못했어요. Secrets를 확인해 주세요.")
    st.stop()


def watch_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    closes = fetch_yf_closes(tuple(sorted(df["symbol"].unique())))
    year = {}
    try:
        for s in df["symbol"].unique():
            d = charting.fetch_ohlcv(s, "1d")
            year[s] = d[d.index >= d.index[-1] - pd.Timedelta(days=365)] if not d.empty else d
    except Exception:
        pass
    rows = []
    for _, r in df.iterrows():
        s = closes[r["symbol"]].dropna() if r["symbol"] in closes.columns else pd.Series(dtype=float)
        price = float(s.iloc[-1]) if len(s) else None
        if price is not None:
            price = round(price) if r["currency"] == "KRW" else round(price, 2)
        chg = (float(s.iloc[-1]) / float(s.iloc[-2]) - 1) * 100 if len(s) > 1 else None
        y = year.get(r["symbol"])
        pos = None
        if y is not None and not y.empty and price is not None:
            lo, hi = float(y["Low"].min()), float(y["High"].max())
            pos = (price - lo) / (hi - lo) * 100 if hi > lo else None
        rows.append({"id": r["id"], "그룹": r["group_name"] or "", "종목": r["name"], "티커": r["symbol"],
                     "현재가": price, "등락률": chg, "52주 위치": pos, "통화": r["currency"], "메모": r["memo"] or ""})
    return pd.DataFrame(rows)


current = st.session_state.chart_symbol

# ── 관심종목 표 (차트 바로 위) ──────────────────────────────
st.subheader("관심종목")
if watch.empty:
    st.info("아직 관심종목이 없어요. 종목을 검색해 차트에서 추가해 보세요.")
else:
    with st.spinner("관심종목 시세 불러오는 중…"):
        table = watch_table(watch)
    groups = ["전체"] + sorted({g for g in table["그룹"] if g})
    gsel = st.radio("그룹", groups, horizontal=True, label_visibility="collapsed")
    view = table if gsel == "전체" else table[table["그룹"] == gsel]
    st.caption("종목명이나 티커를 누르면 바로 아래에 차트가 열려요.")
    event = st.dataframe(
        view.drop(columns=["id"]), hide_index=True, width="stretch",
        on_select="rerun", selection_mode=["single-row", "single-cell"], key=f"watch-table-{gsel}",
        column_config={
            "현재가": st.column_config.NumberColumn(format="localized"),
            "등락률": st.column_config.NumberColumn(format="%+.2f%%"),
            "52주 위치": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%",
                                                       help="52주 최저가 0% ~ 최고가 100%"),
        },
    )
    sel = event.selection if event else None
    idx = None
    if sel and sel.get("cells"):
        idx = sel["cells"][0][0]
    elif sel and sel.get("rows"):
        idx = sel["rows"][0]
    r = view.iloc[idx] if idx is not None and idx < len(view) else None
    picked = r["티커"] if r is not None else None
    # 표에서 새로 고른 종목일 때만 차트를 바꿔요 (검색으로 연 차트를 덮어쓰지 않도록)
    if picked != st.session_state.get("watch_pick"):
        st.session_state.watch_pick = picked
        if picked and (current is None or current["symbol"] != picked):
            st.session_state.chart_symbol = {"symbol": picked, "name": r["종목"], "market": symbols.market_for(picked)}
            st.rerun()
    if r is not None:
        b1, b2 = st.columns([1, 4])
        if b1.button("관심종목에서 빼기"):
            wl.remove_from_watchlist(int(r["id"]))
            st.session_state.watch_pick = None
            st.rerun()
        with b2.popover(f"{r['종목']} 그룹·메모 수정"):
            ng = st.text_input("그룹", r["그룹"], key=f"eg-{r['id']}")
            nm = st.text_input("메모", r["메모"], key=f"em-{r['id']}")
            if st.button("저장", key=f"es-{r['id']}"):
                wl.update_watchlist(int(r["id"]), ng, nm)
                st.rerun()
    st.divider()

# ── 차트 ───────────────────────────────────────────────────
if current:
    sym = current["symbol"]
    c1, c2, c3 = st.columns([1, 1, 3])
    interval_label = c1.selectbox("봉", list(charting.INTERVALS), index=1)
    interval = charting.INTERVALS[interval_label]
    period_opts = ["1개월"] if interval == "30m" else list(charting.PERIODS)
    period_label = c2.selectbox("기간", period_opts, index=0 if interval == "30m" else 3,
                                help="30분봉은 최근 60일까지만 제공돼요")
    days = 60 if interval == "30m" else charting.PERIODS[period_label]
    mas = c3.multiselect("이동평균선", list(charting.MA_COLORS), default=list(charting.MA_COLORS),
                         format_func=lambda n: f"{n}일")

    with st.spinner("차트 불러오는 중…"):
        try:
            df = charting.fetch_ohlcv(sym, interval)
        except Exception:
            df = pd.DataFrame()
    if df.empty:
        st.warning(f"`{sym}` 시세를 받지 못했어요.")
    else:
        drawings = wl.list_drawings(sym)
        fig = charting.build_figure(df, interval, days, mas, drawings,
                                    title=f"{current['name']} ({sym})")
        st.plotly_chart(fig, width="stretch", key="main-chart", config={
            "scrollZoom": True, "displaylogo": False,
            "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
        })
        st.caption("툴바의 펜 아이콘으로 자유롭게 그릴 수 있지만, 이 그림은 저장되지 않아요. "
                   "남겨 둘 추세선·메모·구간 등락률은 아래 ‘차트에 추가’에서 저장하세요.")

        in_watch = sym in set(watch["symbol"])
        a1, a2 = st.columns(2)
        with a1.expander("관심종목 " + ("(추가됨)" if in_watch else "추가"), expanded=not in_watch):
            groups = sorted({g for g in watch["group_name"].dropna() if g})
            g = st.selectbox("그룹", groups + ["새 그룹…"], index=None, placeholder="그룹 선택(선택 사항)")
            if g == "새 그룹…":
                g = st.text_input("새 그룹 이름")
            memo = st.text_input("메모", key="add-memo")
            if st.button("관심종목에 추가", disabled=in_watch):
                wl.ensure_asset(sym, current["name"], symbols.market_for(sym), symbols.currency_for(sym))
                wl.add_to_watchlist(sym, g, memo)
                st.success("추가했어요.")
                st.rerun()

        with a2.expander("차트에 추가 (저장됨)"):
            kind = st.radio("종류", ["추세선", "텍스트 메모", "구간 등락률"], horizontal=True)
            first, last = df.index[0].date(), df.index[-1].date()
            default0 = max(first, (df.index[-1] - pd.Timedelta(days=min(days or 365, 90))).date())
            if kind == "구간 등락률":
                d0 = st.date_input("시작일", default0, min_value=first, max_value=last)
                d1 = st.date_input("끝일", last, min_value=first, max_value=last)
                pct = charting.measure_pct(df, d0, d1)
                if pct is not None:
                    st.metric("등락률 (종가 기준)", f"{pct:+.2f}%")
                if st.button("저장", key="save-measure") and d0 < d1:
                    wl.add_drawing(sym, interval, "measure",
                                   [{"t": d0.isoformat()}, {"t": d1.isoformat()}], None)
                    st.rerun()
            elif kind == "추세선":
                d0 = st.date_input("시작일", default0, min_value=first, max_value=last, key="tl-d0")
                p0 = st.number_input("시작 가격", value=charting.close_at(df, d0) or 0.0, key="tl-p0")
                d1 = st.date_input("끝일", last, min_value=first, max_value=last, key="tl-d1")
                p1 = st.number_input("끝 가격", value=charting.close_at(df, d1) or 0.0, key="tl-p1")
                label = st.text_input("이름(선택)", key="tl-label")
                if st.button("저장", key="save-trend"):
                    wl.add_drawing(sym, interval, "trendline",
                                   [{"t": d0.isoformat(), "p": p0}, {"t": d1.isoformat(), "p": p1}], label)
                    st.rerun()
            else:
                d0 = st.date_input("날짜", last, min_value=first, max_value=last, key="tx-d0")
                p0 = st.number_input("가격 위치", value=charting.close_at(df, d0) or 0.0, key="tx-p0")
                text = st.text_input("메모 내용", key="tx-text")
                if st.button("저장", key="save-text") and text:
                    wl.add_drawing(sym, interval, "text", [{"t": d0.isoformat(), "p": p0}], text)
                    st.rerun()

            if drawings:
                st.markdown("**저장된 그림**")
                names = {"trendline": "추세선", "text": "메모", "measure": "구간 등락률"}
                for d in drawings:
                    pts = " → ".join(p["t"][:10] for p in d["points"])
                    cc1, cc2 = st.columns([4, 1])
                    cc1.caption(f"{names.get(d['kind'], d['kind'])} · {pts} {d.get('text') or ''}")
                    if cc2.button("삭제", key=f"del-d-{d['id']}"):
                        wl.delete_drawing(d["id"])
                        st.rerun()
