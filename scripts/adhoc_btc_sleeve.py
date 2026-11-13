"""일회성 분석: '50/50 + gold 15 + bit 5' 조합에서 비트코인 5% 몫을 돌파 전략(40/20)으로 운용하면?

- 원래: 앱 백테스트와 똑같이 (lib.backtest.run_backtest)
- 전략: 비트코인 5% 몫을 한 주머니로 보고, 주머니 안에서 lib.btc_trading 신호(BTC-USD, UTC 일봉,
  매수 40일·매도 20일, 수수료 0.05%)대로 비트코인 ↔ 미국 초단기 국채(BIL)를 오간다.
  적립·리밸런싱은 주머니 단위로 5%를 맞춘다(주머니 안이 국채면 국채로 적립).
- 참고: 5%를 계속 초단기 국채에만 둔 경우
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import backtest as bt  # noqa: E402
from lib import btc_trading as bx  # noqa: E402

START, END = date(2014, 9, 17), date(2026, 11, 6)
MONTHLY, INITIAL, REB, BASE = 4_000_000.0, 0.0, "semiannual", "KRW"
BUY_N, SELL_N, FEE = 40, 20, 0.05
SLEEVE = "BTC_DC"


def sleeve_series(btc: pd.Series, bil: pd.Series) -> tuple[pd.Series, pd.DataFrame, dict]:
    """비트코인 일봉 날짜 기준 주머니 가격(USD, 시작=1). 신호 난 날 종가에 바꾼다."""
    btc = btc.sort_index()
    sig = bx.compute_signals(pd.DataFrame({"Close": btc}), BUY_N, SELL_N)
    pos = sig["Position"]
    bil_d = bil.reindex(bil.index.union(btc.index)).ffill().reindex(btc.index).bfill()
    r_btc = btc.pct_change().fillna(0.0)
    r_bil = bil_d.pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0)
    trade = pos.diff().abs().fillna(pos.abs())
    fee = FEE / 100
    s = ((1 + held * r_btc + (1 - held) * r_bil) * (1 - fee * trade)).cumprod()
    res = bx.backtest(sig, fee_pct=FEE)
    info = {"exposure": float(held.mean()), "trades": int(len(res.trades)),
            "win_rate": res.stats["전략"].get("win_rate"),
            "btc_only_strategy": res.stats["전략"], "btc_hold": res.stats["그냥 보유"],
            "sleeve_total": float(s.iloc[-1] / s.iloc[0] - 1),
            "last_position": "보유" if pos.iloc[-1] > 0 else "현금(국채)"}
    trades = res.trades.copy()
    return s, trades, info


def fmt(stats: dict) -> dict:
    def pct(x):
        return None if x is None else round(x * 100, 2)

    def spell(d):
        if not d:
            return None
        return f"{d['months']:.0f}개월 ({d['start']:%Y-%m}~{d['end']:%Y-%m}){'' if d['recovered'] else ' 미회복'}"
    return {
        "최종(KRW)": round(stats["final"]), "투자금": round(stats["invested"]),
        "CAGR%": pct(stats["cagr"]), "IRR%": pct(stats["irr"]), "변동성%": pct(stats["vol"]),
        "MDD%": pct(stats["mdd"]), "MDD날짜": str(stats["mdd_date"].date()),
        "샤프": None if stats["sharpe"] is None else round(stats["sharpe"], 2),
        "최고의해": [stats["best_year"][0], pct(stats["best_year"][1])] if stats["best_year"] else None,
        "최악의해": [stats["worst_year"][0], pct(stats["worst_year"][1])] if stats["worst_year"] else None,
        "원금손실기간": spell(stats["loss_recovery"]),
        "고점회복(수익률)": spell(stats["peak_recovery"]), "고점회복(잔고)": spell(stats["value_peak_recovery"]),
    }


def main(hist: dict[str, pd.Series] | None = None) -> dict:
    keys = {"BTC", "GOLD", "US_STOCK", "NASDAQ100", "US_STB"}
    if hist is None:
        hist = dict(bt.fetch_history(bt.needed_tickers(keys)))
    bil, _ = bt.splice(bt.chain_of("US_STB"), hist)
    sleeve, trades, info = sleeve_series(hist["BTC-USD"], bil)
    hist[SLEEVE] = sleeve
    bt.CATALOG[SLEEVE] = bt.AssetClass("비트코인 돌파 주머니", "USD", (SLEEVE,))

    base = {"US_STOCK": 40.0, "NASDAQ100": 40.0, "GOLD": 15.0}
    ports = [bt.Portfolio("원래 (비트코인 보유)", {**base, "BTC": 5.0}),
             bt.Portfolio("돌파 전략 (40/20 + 쉬는 동안 국채)", {**base, SLEEVE: 5.0}),
             bt.Portfolio("참고: 5%를 국채에만", {**base, "US_STB": 5.0})]
    run = bt.run_backtest(ports, START, END, INITIAL, MONTHLY, REB, BASE, hist)
    out = {"period": [str(run.start.date()), str(run.end.date())], "notes": run.notes,
           "sources": run.sources.astype(str).to_dict("records"),
           "results": {r.name: fmt(r.stats) for r in run.results},
           "yearly": {r.name: {int(y): round(v * 100, 2) for y, v in bt.yearly_returns(r.sim["nav"]).items()}
                      for r in run.results},
           "sleeve": {k: (v if not isinstance(v, dict) else {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                                              for kk, vv in v.items()})
                      for k, v in info.items()}}
    trades = trades[(pd.to_datetime(trades["매수일"]) <= pd.Timestamp(END))]
    out["trades"] = trades.assign(수익률=trades["수익률"].round(1), 매수가=trades["매수가"].round(0),
                                  매도가=trades["매도가"].round(0)).astype(str).to_dict("records")
    # 원래 vs 전략: 비트코인 몫 금액(마지막 날)
    print("=== RESULT JSON ===")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print("=== END ===")
    return out


if __name__ == "__main__":
    main()
