"""indicator_settings 테이블 읽기·쓰기."""
from __future__ import annotations

import pandas as pd

from lib.db import get_client

COLUMNS = ["id", "display_name", "symbol", "source", "group_name", "sort_order", "is_visible"]

# 지표 편집 화면에서 빠르게 추가할 수 있는 목록
PRESETS = [
    ("S&P 500", "^GSPC", "yfinance", "지수"),
    ("나스닥 100", "^NDX", "yfinance", "지수"),
    ("나스닥 종합", "^IXIC", "yfinance", "지수"),
    ("다우존스", "^DJI", "yfinance", "지수"),
    ("러셀 2000", "^RUT", "yfinance", "지수"),
    ("코스피", "^KS11", "yfinance", "지수"),
    ("코스닥", "^KQ11", "yfinance", "지수"),
    ("닛케이 225", "^N225", "yfinance", "지수"),
    ("항셍", "^HSI", "yfinance", "지수"),
    ("원/달러", "KRW=X", "yfinance", "환율"),
    ("원/엔", "JPYKRW=X", "yfinance", "환율"),
    ("달러 인덱스", "DX-Y.NYB", "yfinance", "환율"),
    ("미국 10년물", "^TNX", "yfinance", "금리"),
    ("미국 2년물 (근사: 13주 국채)", "^IRX", "yfinance", "금리"),
    ("한국 10년물", "ecos:KR10Y", "ecos", "금리"),
    ("한국 3년물", "ecos:KR3Y", "ecos", "금리"),
    ("CD 91일", "ecos:CD91", "ecos", "금리"),
    ("VIX", "^VIX", "yfinance", "변동성"),
    ("CNN 공포·탐욕 지수", "cnn:fear_greed", "cnn", "심리"),
    ("WTI", "CL=F", "yfinance", "원자재"),
    ("금", "GC=F", "yfinance", "원자재"),
    ("은", "SI=F", "yfinance", "원자재"),
    ("구리", "HG=F", "yfinance", "원자재"),
    ("비트코인", "BTC-USD", "yfinance", "코인"),
    ("이더리움", "ETH-USD", "yfinance", "코인"),
]


def load_all() -> pd.DataFrame:
    rows = (
        get_client().table("indicator_settings").select("*").order("sort_order").execute().data
    )
    return pd.DataFrame(rows, columns=COLUMNS)


def save_changes(original: pd.DataFrame, edited: pd.DataFrame) -> None:
    """편집 전후를 비교해 삭제·수정·추가를 반영한다."""
    db = get_client().table("indicator_settings")
    orig_ids = set(original["id"].dropna().astype(int))
    kept_ids = set(edited["id"].dropna().astype(int))

    for gone in orig_ids - kept_ids:
        db.delete().eq("id", gone).execute()

    for _, row in edited.iterrows():
        payload = {
            "display_name": str(row["display_name"]).strip(),
            "symbol": str(row["symbol"]).strip(),
            "source": row["source"] or "yfinance",
            "group_name": (row["group_name"] or None),
            "sort_order": int(row["sort_order"]) if pd.notna(row["sort_order"]) else 0,
            "is_visible": bool(row["is_visible"]) if pd.notna(row["is_visible"]) else True,
        }
        if not payload["display_name"] or not payload["symbol"]:
            continue
        if pd.notna(row["id"]):
            db.update(payload).eq("id", int(row["id"])).execute()
        else:
            db.insert(payload).execute()


def add_preset(name: str, symbol: str, source: str, group: str, sort_order: int) -> None:
    get_client().table("indicator_settings").insert(
        {
            "display_name": name,
            "symbol": symbol,
            "source": source,
            "group_name": group,
            "sort_order": sort_order,
            "is_visible": True,
        }
    ).execute()
