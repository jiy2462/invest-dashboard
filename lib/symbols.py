"""종목 검색: 한국 종목은 KRX 목록(한글명), 해외 종목은 yfinance 검색."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf

KR_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ", "KOSDAQ GLOBAL": ".KQ", "KONEX": ".KN"}


@st.cache_data(ttl=86400, show_spinner=False)
def krx_listing() -> pd.DataFrame:
    """KRX 상장 종목 목록 (symbol, name, market). 실패하면 빈 표."""
    try:
        import FinanceDataReader as fdr

        df = fdr.StockListing("KRX")[["Code", "Name", "Market"]]
    except Exception:
        return pd.DataFrame(columns=["symbol", "name", "market"])
    df = df[df["Market"].isin(KR_SUFFIX)]
    df = df.assign(symbol=df["Code"] + df["Market"].map(KR_SUFFIX))
    return df.rename(columns={"Name": "name", "Market": "market"})[["symbol", "name", "market"]]


@st.cache_data(ttl=3600, show_spinner=False)
def yf_search(query: str) -> list[dict]:
    try:
        quotes = yf.Search(query, max_results=8).quotes
    except Exception:
        return []
    out = []
    for q in quotes:
        if not q.get("symbol"):
            continue
        out.append({
            "symbol": q["symbol"],
            "name": q.get("shortname") or q.get("longname") or q["symbol"],
            "market": q.get("exchDisp") or q.get("exchange") or "",
        })
    return out


def search(query: str, limit: int = 15) -> list[dict]:
    """한글·영문 이름 또는 티커로 찾는다. 한국 종목을 먼저 보여준다."""
    q = query.strip()
    if not q:
        return []
    results: list[dict] = []
    kr = krx_listing()
    if not kr.empty:
        code_hit = kr["symbol"].str.upper().str.startswith(q.upper())
        name_hit = kr["name"].str.contains(q, case=False, regex=False)
        hits = kr[code_hit | name_hit].copy()
        # 이름이 정확히 같거나 앞부분이 같은 종목을 위로
        hits["rank"] = (~hits["name"].eq(q)).astype(int) + (~hits["name"].str.startswith(q)).astype(int)
        results += hits.sort_values(["rank", "name"]).head(limit)[["symbol", "name", "market"]].to_dict("records")
    seen = {r["symbol"] for r in results}
    if q.isascii():
        results += [r for r in yf_search(q) if r["symbol"] not in seen]
    return results[:limit]


def name_for(symbol: str) -> str:
    kr = krx_listing()
    if not kr.empty:
        hit = kr[kr["symbol"] == symbol]
        if not hit.empty:
            return str(hit.iloc[0]["name"])
    return symbol


def currency_for(symbol: str) -> str:
    if symbol.endswith((".KS", ".KQ", ".KN")):
        return "KRW"
    try:
        return yf.Ticker(symbol).fast_info["currency"] or "USD"
    except Exception:
        return "USD"


def market_for(symbol: str) -> str:
    if symbol.endswith(".KS"):
        return "KOSPI"
    if symbol.endswith((".KQ", ".KN")):
        return "KOSDAQ"
    return "US" if "." not in symbol else symbol.rsplit(".", 1)[1]
