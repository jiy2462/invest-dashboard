from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import backtest as bt
from lib import portfolio as pf

UP, DOWN = "#d62728", "#1f5fbf"          # 한국식: 이익 빨강, 손실 파랑
LINES = ["#2a78d6", "#e8743b", "#2ca58d"]
BENCH = "#52514e"
DIRECT = "직접 입력 (티커)"
MY = "내 목표 비중 (포트폴리오 화면)"
SAVED = "저장: "
LABEL_TO_KEY = {a.label: k for k, a in bt.CATALOG.items()}
ASSET_OPTIONS = [a.label for a in bt.CATALOG.values()] + [DIRECT]
MAX_SLOTS = 3

st.title("백테스트")
st.caption("자산군과 비중을 골라 최대 30년 동안 어떻게 됐을지 계산해요. 배당은 재투자한 것으로 보고, "
           "세금·수수료는 빼지 않았어요. 대표 ETF가 생기기 전 기간은 비슷한 펀드나 지수로 이어 붙여요.")

# ── 저장한 조합·내 목표 비중 ─────────────────────────────────
try:
    saved = bt.list_presets()
except Exception:
    saved = []
    st.warning("DB에 연결하지 못해 저장한 조합을 불러오지 못했어요. 백테스트는 그대로 할 수 있어요.")
try:
    my_weights, my_notes = bt.weights_from_targets(pf.list_targets(), pf.list_assets())
except Exception:
    my_weights, my_notes = {}, []
saved_by_name = {p["name"]: p for p in saved}

choices = list(bt.PRESETS) + ([MY] if my_weights else []) + [SAVED + n for n in saved_by_name]


def weights_of(choice: str) -> dict[str, float]:
    if choice == MY:
        return my_weights
    if choice.startswith(SAVED):
        p = saved_by_name.get(choice[len(SAVED):])
        return {k: float(v) for k, v in (p or {}).get("allocations", {}).items()}
    return bt.PRESETS.get(choice, {})


def rows_of(weights: dict[str, float]) -> pd.DataFrame:
    rows = [{"자산": bt.CATALOG[k].label if k in bt.CATALOG else DIRECT,
             "티커": "" if k in bt.CATALOG else bt.label_of(k), "비중(%)": float(v)}
            for k, v in weights.items()]
    return pd.DataFrame(rows, columns=["자산", "티커", "비중(%)"])


def weights_from_rows(df: pd.DataFrame) -> tuple[dict[str, float], list[str]]:
    out: dict[str, float] = {}
    errs: list[str] = []
    for _, r in df.iterrows():
        label, tick = r.get("자산"), str(r.get("티커") or "").strip()
        w = r.get("비중(%)")
        if w is None or pd.isna(w) or float(w) <= 0:
            continue
        if label == DIRECT or (not isinstance(label, str) and tick):
            if not tick:
                errs.append("‘직접 입력’ 행에 티커를 적어 주세요.")
                continue
            key = bt.custom_key(tick)
        elif label in LABEL_TO_KEY:
            key = LABEL_TO_KEY[label]
        else:
            continue
        out[key] = out.get(key, 0) + float(w)
    return out, errs


def load_slot(i: int) -> None:
    choice = st.session_state[f"bt_pick{i}"]
    st.session_state[f"bt_rows{i}"] = rows_of(weights_of(choice))
    st.session_state[f"bt_name{i}"] = choice.removeprefix(SAVED)
    st.session_state[f"bt_ver{i}"] = st.session_state.get(f"bt_ver{i}", 0) + 1
    p = saved_by_name.get(choice[len(SAVED):]) if choice.startswith(SAVED) else None
    if p:  # 저장할 때의 설정도 불러온다
        if p.get("start_date"):
            st.session_state["bt_start"] = max(date.fromisoformat(p["start_date"]), min_start)
        if p.get("end_date"):
            st.session_state["bt_end"] = min(date.fromisoformat(p["end_date"]), today)
        if p.get("rebalance") in bt.REBALANCE:
            st.session_state["bt_rebal"] = p["rebalance"]


today = date.today()
min_start = date(today.year - bt.MAX_YEARS, today.month, min(today.day, 28))
defaults = ["주식 60 / 장기채 30 / 금 10", MY if my_weights else "올웨더 (레이 달리오)", "S&P 500 100%"]
st.session_state.setdefault("bt_start", min_start)
st.session_state.setdefault("bt_end", today)
st.session_state.setdefault("bt_rebal", "yearly")
st.session_state.setdefault("bt_base", "KRW")
st.session_state.setdefault("bt_bench", "US_STOCK")
st.session_state.setdefault("bt_n", 2)
for _ccy, _init in (("KRW", 10_000_000.0), ("USD", 10_000.0)):
    st.session_state.setdefault(f"bt_init_{_ccy}", _init)
    st.session_state.setdefault(f"bt_monthly_{_ccy}", 0.0)
for i in range(MAX_SLOTS):
    if f"bt_rows{i}" not in st.session_state:
        st.session_state[f"bt_pick{i}"] = defaults[i]
        load_slot(i)
    # 다른 화면에 다녀오면 위젯 값이 지워지므로 따로 기억해 둔 값으로 되살린다
    if f"bt_pick{i}" not in st.session_state:
        st.session_state[f"bt_pick{i}"] = st.session_state.get(f"_bt_pick{i}", defaults[i])
    if f"bt_name{i}" not in st.session_state:
        st.session_state[f"bt_name{i}"] = st.session_state.get(f"_bt_name{i}", "")
    if st.session_state[f"bt_pick{i}"] not in choices:
        st.session_state[f"bt_pick{i}"] = defaults[i] if defaults[i] in choices else choices[0]

# ── 저장한 백테스트 ──────────────────────────────────────────
def open_saved(run_id: int) -> None:
    """저장한 백테스트의 결과를 보여 주고, 그때 고른 옵션을 입력 칸에 되돌려 놓는다."""
    try:
        loaded, row = bt.load_run(run_id)
    except Exception as e:
        st.session_state["bt_load_error"] = str(e)
        return
    st.session_state["bt_run"] = loaded
    st.session_state["bt_saved_meta"] = {"name": row["name"], "created_at": row.get("created_at"), "loaded": True}
    cfg = loaded.settings
    if cfg.get("start"):
        st.session_state["bt_start"] = max(date.fromisoformat(cfg["start"]), min_start)
    if cfg.get("end"):
        st.session_state["bt_end"] = min(date.fromisoformat(cfg["end"]), today)
    if cfg.get("base") in ("KRW", "USD"):
        st.session_state["bt_base"] = cfg["base"]
        st.session_state[f"bt_init_{cfg['base']}"] = float(cfg.get("initial") or 0)
        st.session_state[f"bt_monthly_{cfg['base']}"] = float(cfg.get("monthly") or 0)
    if cfg.get("rebalance") in bt.REBALANCE:
        st.session_state["bt_rebal"] = cfg["rebalance"]
    if cfg.get("benchmark") in ["없음"] + list(bt.CATALOG):
        st.session_state["bt_bench"] = cfg["benchmark"]
    ports = (cfg.get("portfolios") or [])[:MAX_SLOTS]
    if ports:
        st.session_state["bt_n"] = len(ports)
    for i, p in enumerate(ports):
        st.session_state[f"bt_rows{i}"] = rows_of(p["weights"])
        st.session_state[f"bt_name{i}"] = p["name"]
        st.session_state[f"bt_ver{i}"] = st.session_state.get(f"bt_ver{i}", 0) + 1


def settings_text(cfg: dict) -> str:
    if not cfg:
        return ""
    ccy = cfg.get("base", "KRW")
    sym = "₩" if ccy == "KRW" else "$"
    bench_key = cfg.get("benchmark") or "없음"
    bench_label = bt.CATALOG[bench_key].label if bench_key in bt.CATALOG else "없음"
    return (f"{cfg.get('start')} ~ {cfg.get('end')} · 계산 통화 {ccy} · 초기 {sym}{float(cfg.get('initial') or 0):,.0f}"
            f" · 월 적립 {sym}{float(cfg.get('monthly') or 0):,.0f} · 리밸런싱 {bt.REBALANCE.get(cfg.get('rebalance'), '–')}"
            f" · 비교 기준 {bench_label}")


try:
    saved_runs = bt.list_runs()
except Exception:
    saved_runs = None
if st.session_state.get("bt_load_error"):
    st.error(f"저장한 백테스트를 불러오지 못했어요: {st.session_state.pop('bt_load_error')}")
if saved_runs:
    with st.expander(f"저장한 백테스트 ({len(saved_runs)}개)"):
        for r in reversed(saved_runs):
            a, b, c = st.columns([6, 1, 1])
            when = str(r.get("created_at") or "")[:10]
            names = " / ".join(p["name"] for p in (r["settings"] or {}).get("portfolios", []))
            a.markdown(f"**{r['name']}** · {when}  \n{names}")
            a.caption(settings_text(r["settings"] or {}))
            b.button("보기", key=f"bt_open{r['id']}", on_click=open_saved, args=(r["id"],))
            if c.button("삭제", key=f"bt_rdel{r['id']}"):
                bt.delete_run(r["id"])
                meta = st.session_state.get("bt_saved_meta")
                if meta and meta.get("name") == r["name"]:
                    st.session_state.pop("bt_saved_meta", None)
                st.toast(f"‘{r['name']}’ 삭제했어요.")
                st.rerun()

# ── 설정 ───────────────────────────────────────────────────
with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    start = c1.date_input("시작일", min_value=min_start, max_value=today, key="bt_start",
                          format="YYYY-MM-DD")
    end = c2.date_input("종료일", min_value=min_start, max_value=today, key="bt_end",
                        format="YYYY-MM-DD")
    base = c3.radio("계산 통화", ["KRW", "USD"], horizontal=True, key="bt_base",
                    help="원화로 고르면 미국 자산은 그날 환율로 원화로 바꿔 계산해요(환노출). "
                         "달러로 고르면 한국 자산을 달러로 바꿔요. 결과에는 다른 통화 금액도 함께 보여 줘요.")
    rebalance = c4.selectbox("리밸런싱", list(bt.REBALANCE), format_func=bt.REBALANCE.get, key="bt_rebal")
    d1, d2, d3, d4 = st.columns(4)
    unit = "원" if base == "KRW" else "달러"
    initial = d1.number_input(f"초기 금액({unit})", min_value=0.0,
                              step=1_000_000.0 if base == "KRW" else 1_000.0, format="%.0f", key=f"bt_init_{base}")
    monthly = d2.number_input(f"월 적립금({unit})", min_value=0.0,
                              step=100_000.0 if base == "KRW" else 100.0, format="%.0f", key=f"bt_monthly_{base}")
    bench_opts = ["없음"] + list(bt.CATALOG)
    bench = d3.selectbox("비교 기준", bench_opts, key="bt_bench",
                         format_func=lambda k: k if k == "없음" else bt.CATALOG[k].label)
    n_slots = d4.radio("비교할 조합 수", [1, 2, 3], horizontal=True, key="bt_n")

if start >= end:
    st.error("시작일이 종료일보다 앞이어야 해요.")
    st.stop()

# ── 조합 ───────────────────────────────────────────────────
portfolios: list[bt.Portfolio] = []
problems: list[str] = []
cols = st.columns(n_slots)
for i in range(n_slots):
    with cols[i], st.container(border=True):
        st.selectbox("불러오기", choices, key=f"bt_pick{i}", on_change=load_slot, args=(i,))
        name = st.text_input("조합 이름", key=f"bt_name{i}")
        st.session_state[f"_bt_pick{i}"] = st.session_state[f"bt_pick{i}"]
        st.session_state[f"_bt_name{i}"] = name
        edited = st.data_editor(
            st.session_state[f"bt_rows{i}"], key=f"bt_edit{i}_{st.session_state[f'bt_ver{i}']}",
            num_rows="dynamic", hide_index=True, width="stretch", column_order=["자산", "비중(%)", "티커"],
            column_config={
                "자산": st.column_config.SelectboxColumn("자산", options=ASSET_OPTIONS, required=True, width="medium"),
                "티커": st.column_config.TextColumn("티커", width="small", help="‘직접 입력’일 때 야후 티커 (예: AAPL, 005930.KS)"),
                "비중(%)": st.column_config.NumberColumn("비중(%)", width="small", min_value=0.0, max_value=100.0, step=1.0,
                                                       format="%.1f"),
            })
        weights, errs = weights_from_rows(edited)
        problems += [f"{name}: {e}" for e in errs]
        total = sum(weights.values())
        if total and abs(total - 100) > 0.01:
            st.caption(f"비중 합계 {total:g}% → 100%로 맞춰 계산해요.")
        elif total:
            st.caption("비중 합계 100%")
        if st.session_state[f"bt_pick{i}"] == MY and my_notes:
            with st.expander("어떻게 옮겼나요"):
                st.markdown("\n".join(f"- {n}" for n in my_notes))
        if st.button("이 조합 저장", key=f"bt_save{i}", disabled=not (weights and name.strip())):
            try:
                bt.save_preset(name.strip(), weights, {"start": start, "end": end, "rebalance": rebalance,
                                                       "initial": initial, "monthly": monthly,
                                                       "benchmark": None if bench == "없음" else bench})
                st.toast(f"‘{name.strip()}’ 저장했어요.")
                st.rerun()
            except Exception as e:
                st.error(f"저장하지 못했어요: {e}")
        if weights:
            portfolios.append(bt.Portfolio(name.strip() or f"조합 {i + 1}", weights))

if saved:
    with st.expander(f"저장한 조합 관리 ({len(saved)}개)"):
        for p in saved:
            a, b = st.columns([5, 1])
            a.markdown(f"**{p['name']}** · " + ", ".join(f"{bt.label_of(k)} {float(v):g}%"
                                                        for k, v in p["allocations"].items()))
            if b.button("삭제", key=f"bt_del{p['id']}"):
                bt.delete_preset(p["id"])
                st.rerun()

for p in problems:
    st.warning(p)

if st.button("백테스트 실행", type="primary", disabled=not portfolios):
    runs = list(portfolios)
    if bench != "없음":
        runs.append(bt.Portfolio(f"비교 기준: {bt.CATALOG[bench].label}", {bench: 100}))
    keys = {k for p in runs for k in p.weights}
    with st.spinner("과거 시세를 받는 중이에요 (처음 한 번은 조금 걸려요)…"):
        try:
            hist = bt.fetch_history(bt.needed_tickers(keys))
        except Exception as e:
            st.error(f"시세를 받지 못했어요: {e}")
            st.stop()
        result = bt.run_backtest(runs, start, end, initial, monthly, rebalance, base, hist)
        result.settings = {"start": str(start), "end": str(end), "base": base, "rebalance": rebalance,
                           "initial": float(initial), "monthly": float(monthly), "benchmark": bench,
                           "portfolios": [{"name": p.name, "weights": p.weights} for p in portfolios]}
        st.session_state["bt_run"] = result
        st.session_state.pop("bt_saved_meta", None)

run: bt.Run | None = st.session_state.get("bt_run")
if run is None:
    st.stop()
if not run.results:
    st.error("계산할 수 있는 조합이 없어요. 아래 안내를 확인해 주세요.")
    for n in run.notes:
        st.info(n)
    st.stop()


# ── 결과 ───────────────────────────────────────────────────
def money(v: float, ccy: str) -> str:
    if v is None or pd.isna(v):
        return "–"
    return f"₩{v:,.0f}" if ccy == "KRW" else f"${v:,.0f}"


def pct(v) -> str:
    return "–" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"


def spell_text(sp: dict | None, none: str) -> str:
    if not sp:
        return none
    span = f"{sp['start']:%Y-%m} ~ {sp['end']:%Y-%m}"
    if not sp["recovered"]:
        return f"{sp['months']:.0f}개월째 회복 중 ({sp['start']:%Y-%m}~)"
    return f"{sp['months']:.0f}개월 ({span})"


other_ccy = "USD" if run.base == "KRW" else "KRW"
st.divider()
st.subheader(f"결과 · {run.start.date()} ~ {run.end.date()} ({(run.end - run.start).days / 365.25:.1f}년)")
meta = st.session_state.get("bt_saved_meta")
if meta:
    st.caption(f"📁 저장한 결과 ‘{meta['name']}’ ({str(meta.get('created_at') or '')[:10]} 저장)."
               + (" 곡선은 주 단위로 저장돼서 그래프가 조금 덜 촘촘해요. 지표는 저장할 때 계산한 값 그대로예요."
                  if meta.get("loaded") else ""))
if run.settings:
    st.caption("설정: " + settings_text(run.settings))
for n in run.notes:
    st.info(n)

if not meta and run.settings:
    with st.form("bt_save_run", border=False):
        default_name = f"{date.today():%Y-%m-%d} · " + " vs ".join(p["name"] for p in run.settings["portfolios"])
        s1, s2 = st.columns([4, 1])
        run_name = s1.text_input("저장 이름", value=default_name, label_visibility="collapsed")
        if s2.form_submit_button("이 결과 저장", width="stretch"):
            if saved_runs is None:
                st.error("DB에 연결하지 못해 저장할 수 없어요.")
            else:
                try:
                    bt.save_run(run_name.strip() or default_name, run)
                    st.session_state["bt_saved_meta"] = {"name": run_name.strip() or default_name,
                                                         "created_at": str(date.today())}
                    st.toast("결과를 저장했어요. 위 ‘저장한 백테스트’에서 다시 볼 수 있어요.")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장하지 못했어요. Supabase에 `supabase/004_backtest_runs.sql`이 적용됐는지 확인해 주세요. ({e})")

has_monthly = any(r.sim["invested"].iloc[-1] > r.sim["invested"].iloc[0] for r in run.results)
rows = []
for r in run.results:
    s = r.stats
    row = {
        "조합": r.name,
        f"최종 금액 ({run.base})": money(s["final"], run.base),
        f"최종 금액 ({other_ccy} 환산)": money(r.other.iloc[-1], other_ccy),
        "총 투자금": money(s["invested"], run.base),
        "연평균 수익률 (CAGR)": pct(s["cagr"]),
    }
    if has_monthly:
        row["적립 포함 연수익률 (IRR)"] = pct(s["irr"])
    row.update({
        "변동성 (연)": f"{s['vol'] * 100:.1f}%",
        "최대 낙폭 (MDD)": f"{s['mdd'] * 100:.1f}% ({s['mdd_date'].date()})",
        "샤프 지수": "–" if s["sharpe"] is None else f"{s['sharpe']:.2f}",
        "최고의 해": f"{s['best_year'][0]} {pct(s['best_year'][1])}" if s["best_year"] else "–",
        "최악의 해": f"{s['worst_year'][0]} {pct(s['worst_year'][1])}" if s["worst_year"] else "–",
        "원금 손실 → 원금 회복 (최장)": spell_text(s["loss_recovery"], "원금 아래로 내려간 적 없음"),
        "고점 → 고점 회복 (최장)": spell_text(s["peak_recovery"], "–"),
    })
    rows.append(row)
table = pd.DataFrame(rows).set_index("조합").T
st.dataframe(table, width="stretch", height=36 * (len(table) + 1) + 4)
st.caption("CAGR·변동성·MDD·샤프는 적립금 영향을 뺀 수익률로 계산해요. "
           "샤프 지수의 무위험 수익률은 미국 3개월 국채 금리예요. "
           f"{other_ccy} 환산은 그날 환율로 바꾼 금액이에요. "
           "‘원금 손실 → 원금 회복’은 평가금액이 그때까지 넣은 돈보다 적어진 날부터 다시 넘어선 날까지, "
           "‘고점 → 고점 회복’은 직전 최고점 아래로 내려간 날부터 그 고점을 되찾은 날까지예요.")

colors = {r.name: (BENCH if r.name.startswith("비교 기준") else LINES[i % len(LINES)])
          for i, r in enumerate(run.results)}


def dash(name: str) -> str:
    return "dot" if name.startswith("비교 기준") else "solid"


t1, t2, t3, t4 = st.tabs(["자산 곡선", "낙폭", "연도별 수익률", "데이터 출처"])
with t1:
    a, b = st.columns([2, 1])
    view = a.radio("금액 통화", [run.base, other_ccy], horizontal=True, key="bt_view")
    log = b.checkbox("로그 눈금", value=True, key="bt_log")
    fig = go.Figure()
    for r in run.results:
        y = r.sim["value"] if view == run.base else r.other
        fig.add_trace(go.Scatter(x=y.index, y=y, name=r.name, line=dict(color=colors[r.name], width=2,
                                                                         dash=dash(r.name)),
                                 hovertemplate="%{y:,.0f}"))
    inv = run.results[0].sim["invested"]
    if view != run.base:
        inv = inv / run.results[0].sim["value"] * run.results[0].other
    fig.add_trace(go.Scatter(x=inv.index, y=inv, name="투자 원금", line=dict(color="#b9b8b2", width=1, dash="dash"),
                             hovertemplate="%{y:,.0f}"))
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                      yaxis=dict(type="log" if log else "linear", tickformat=",.0f",
                                 tickprefix="₩" if view == "KRW" else "$"),
                      legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="bt_curve")
    if view != run.base:
        st.caption(f"{run.base}로 계산한 결과를 날마다 환율로 바꿔 본 곡선이에요. 투자 원금선도 그날 환율로 바꿔서 출렁여요.")

with t2:
    fig = go.Figure()
    for r in run.results:
        dd = bt.drawdown(r.sim["nav"]) * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd, name=r.name, line=dict(color=colors[r.name], width=1.5,
                                                                          dash=dash(r.name)),
                                 hovertemplate="%{y:.1f}%"))
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), hovermode="x unified",
                      yaxis=dict(ticksuffix="%"), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="bt_dd")
    st.caption("고점 대비 얼마나 떨어져 있었는지 보여 줘요. 0%로 돌아오면 고점을 회복한 거예요.")

with t3:
    yearly = pd.DataFrame({r.name: bt.yearly_returns(r.sim["nav"]) * 100 for r in run.results})
    fig = go.Figure()
    for r in run.results:
        fig.add_trace(go.Bar(x=yearly.index, y=yearly[r.name], name=r.name, marker_color=colors[r.name],
                             hovertemplate="%{y:+.1f}%"))
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0), barmode="group", hovermode="x unified",
                      yaxis=dict(ticksuffix="%"), legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="bt_yearly")
    st.caption("첫해와 마지막 해는 기간 안에 든 부분만의 수익률이에요.")
    styled = yearly.sort_index(ascending=False).style.format("{:+.1f}%").map(
        lambda v: f"color: {UP}" if v > 0 else f"color: {DOWN}" if v < 0 else "")
    st.dataframe(styled, width="stretch")

with t4:
    st.markdown("각 자산이 어느 기간에 어떤 데이터를 썼는지예요. 위 행이 더 오래된 대체 데이터예요.")
    st.dataframe(run.sources, hide_index=True, width="stretch")
    st.caption("지수(^로 시작)는 배당이 빠진 가격이라 실제보다 수익률이 조금 낮게 나와요. "
               "펀드·ETF는 배당 재투자를 반영한 가격이에요. 원/달러 환율은 야후(2003년 말~)와 미국 연준 FRED(그 이전)를 써요.")
    w = pd.DataFrame([{"조합": r.name, **{bt.label_of(k): v / sum(r.weights.values()) * 100
                                         for k, v in r.weights.items()}} for r in run.results]).set_index("조합")
    st.markdown("**계산에 쓴 비중 (%)**")
    st.dataframe(w.fillna(0).round(1), width="stretch")
