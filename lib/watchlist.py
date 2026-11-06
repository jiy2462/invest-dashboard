"""관심종목(watchlist, assets)과 차트 그림(chart_drawings) 저장."""
from __future__ import annotations

import pandas as pd

from lib.db import get_client


def ensure_asset(symbol: str, name: str, market: str, currency: str) -> None:
    get_client().table("assets").upsert(
        {"symbol": symbol, "name": name, "market": market, "currency": currency},
        on_conflict="symbol",
    ).execute()


def list_watchlist() -> pd.DataFrame:
    rows = (get_client().table("watchlist").select("id,symbol,group_name,memo,created_at,assets(name,currency)")
            .order("created_at").execute().data)
    flat = [{**{k: v for k, v in r.items() if k != "assets"},
             "name": (r.get("assets") or {}).get("name", r["symbol"]),
             "currency": (r.get("assets") or {}).get("currency", "")} for r in rows]
    return pd.DataFrame(flat, columns=["id", "symbol", "name", "currency", "group_name", "memo", "created_at"])


def add_to_watchlist(symbol: str, group: str | None, memo: str | None) -> None:
    get_client().table("watchlist").insert(
        {"symbol": symbol, "group_name": group or None, "memo": memo or None}
    ).execute()


def update_watchlist(item_id: int, group: str | None, memo: str | None) -> None:
    get_client().table("watchlist").update({"group_name": group or None, "memo": memo or None}).eq("id", item_id).execute()


def remove_from_watchlist(item_id: int) -> None:
    get_client().table("watchlist").delete().eq("id", item_id).execute()


def list_drawings(symbol: str) -> list[dict]:
    return (get_client().table("chart_drawings").select("*").eq("symbol", symbol)
            .order("created_at").execute().data)


def add_drawing(symbol: str, interval: str, kind: str, points: list[dict], text: str | None) -> None:
    get_client().table("chart_drawings").insert(
        {"symbol": symbol, "interval": interval, "kind": kind, "points": points, "text": text or None}
    ).execute()


def delete_drawing(drawing_id: int) -> None:
    get_client().table("chart_drawings").delete().eq("id", drawing_id).execute()
