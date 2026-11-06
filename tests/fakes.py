"""네트워크 없이 화면을 시험하기 위한 가짜 Supabase 클라이언트와 가짜 시세.

작업 환경에서 Yahoo·ECOS·Supabase 접속이 막혀 있어도 Streamlit AppTest로 화면을 돌려 볼 수 있다.
"""
from __future__ import annotations

import copy
from datetime import datetime

import numpy as np
import pandas as pd

TABLES: dict[str, list[dict]] = {}


def reset_db() -> None:
    TABLES.clear()
    rows = [("^GSPC", "S&P 500", "지수"), ("^KS11", "코스피", "지수"), ("KRW=X", "원/달러", "환율"),
            ("^TNX", "미국 10년물", "금리"), ("ecos:KR10Y", "한국 10년물", "금리")]
    TABLES["indicator_settings"] = [
        dict(id=i + 1, symbol=s, source="ecos" if s.startswith("ecos:") else "yfinance",
             display_name=n, group_name=g, sort_order=i + 1, is_visible=True)
        for i, (s, n, g) in enumerate(rows)
    ]
    for t in ("assets", "watchlist", "chart_drawings", "accounts", "transactions"):
        TABLES[t] = []


class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: str):
        self.t, self.op, self.payload, self.filters, self.cols = table, "select", None, [], "*"
        self.on_conflict = None

    def select(self, cols="*"):
        self.op, self.cols = "select", cols
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def delete(self):
        self.op = "delete"
        return self

    def update(self, p):
        self.op, self.payload = "update", p
        return self

    def insert(self, p):
        self.op, self.payload = "insert", p
        return self

    def upsert(self, p, on_conflict=None):
        self.op, self.payload, self.on_conflict = "upsert", p, on_conflict
        return self

    def _match(self, r):
        return all(r.get(k) == v for k, v in self.filters)

    def execute(self):
        rows = TABLES.setdefault(self.t, [])
        if self.op == "select":
            out = [copy.deepcopy(r) for r in rows if self._match(r)]
            if "assets(" in self.cols:
                for r in out:
                    a = next((x for x in TABLES["assets"] if x["symbol"] == r["symbol"]), None)
                    r["assets"] = {"name": a["name"], "currency": a["currency"]} if a else None
            key = "sort_order" if self.t == "indicator_settings" else "id"
            return _Res(sorted(out, key=lambda r: r.get(key) or 0))
        if self.op in ("insert", "upsert"):
            if self.op == "upsert":
                k = self.on_conflict
                for r in rows:
                    if r.get(k) == self.payload[k]:
                        r.update(self.payload)
                        return _Res([r])
            nid = max([r.get("id", 0) or 0 for r in rows] + [0]) + 1
            row = {"id": nid, "created_at": datetime.now().isoformat(), **self.payload}
            rows.append(row)
            return _Res([row])
        if self.op == "delete":
            rows[:] = [r for r in rows if not self._match(r)]
        if self.op == "update":
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
        return _Res([])


class FakeClient:
    def table(self, name):
        return _Query(name)


BASE = {"^GSPC": 6800, "^KS11": 4000, "KRW=X": 1430, "^TNX": 4.1, "005930.KS": 98000, "AAPL": 270}


def fake_ohlcv(symbol: str, interval: str) -> pd.DataFrame:
    if symbol not in BASE:
        return pd.DataFrame()
    n = 60 * 13 if interval == "30m" else 800
    freq = {"30m": "30min", "1d": "B", "1wk": "W-FRI", "1mo": "ME"}[interval]
    idx = pd.date_range(end="2026-11-05 15:00", periods=n, freq=freq)
    if interval == "30m":
        idx = idx[(idx.hour >= 9) & (idx.hour < 16) & (idx.dayofweek < 5)]
    rng = np.random.default_rng(abs(hash(symbol)) % 1000)
    close = BASE[symbol] * np.exp(np.cumsum(rng.normal(0, 0.012, len(idx))))
    open_ = close * (1 + rng.normal(0, 0.004, len(idx)))
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.006,
                         "Low": np.minimum(open_, close) * 0.994, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, len(idx))}, index=idx)


def fake_closes(symbols) -> pd.DataFrame:
    return pd.DataFrame({s: fake_ohlcv(s, "1d")["Close"] for s in symbols if s in BASE})


KRX = pd.DataFrame([{"symbol": "005930.KS", "name": "삼성전자", "market": "KOSPI"},
                    {"symbol": "005935.KS", "name": "삼성전자우", "market": "KOSPI"},
                    {"symbol": "247540.KQ", "name": "에코프로비엠", "market": "KOSDAQ"}])


def install() -> None:
    """앱 모듈의 네트워크 함수를 가짜로 바꾼다."""
    import lib.charting as charting
    import lib.indicators as indicators
    import lib.market as market
    import lib.symbols as symbols
    import lib.watchlist as watchlist

    reset_db()
    indicators.get_client = watchlist.get_client = lambda: FakeClient()
    market.fetch_yf_closes = fake_closes
    market.validate_yf_symbol = lambda s: s in BASE
    charting.fetch_ohlcv = fake_ohlcv
    symbols.krx_listing = lambda: KRX
    symbols.yf_search = lambda q: [{"symbol": "AAPL", "name": "Apple Inc.", "market": "NASDAQ"}] if "app" in q.lower() else []
    symbols.currency_for = lambda s: "KRW" if s.endswith(".KS") else "USD"
