"""매매 수수료율(증권사별)과 ETF 연보수(종목별).

- 매매 수수료: 거래를 입력할 때 계좌의 증권사 요율로 수수료 금액을 자동 계산해 넣는다
  (알림에 실제 수수료가 적혀 있으면 그 값을 쓴다). 배당에는 붙이지 않는다.
- 연보수: ETF 시세(기준가)에 이미 매일 녹아 있는 비용이라 포트폴리오 손익에서 또 빼지 않는다.
  보유 현황에 '1년에 대략 얼마 나가는지' 보여 주고, 백테스트의 비용 가정에 쓴다.

기본값은 사용자가 알려 준 요율(2026-11-13)이고, 포트폴리오 → 설정에서 고치면 app_settings `cost_rates`에 저장된다.
"""
from __future__ import annotations

import math

import pandas as pd

SETTINGS_KEY = "cost_rates"   # app_settings: {"brokers": {증권사: %}, "expense": {티커: %}}

# 증권사별 매매 수수료율 (%, 거래금액 기준)
DEFAULT_BROKER_FEES: dict[str, float] = {
    "한국투자증권": 0.0146,   # 국내 ETF (TIGER 미국S&P500 등)
    "메리츠증권": 0.007,      # 해외 ETF (QQQM, GLDM, SPYM, SGOV)
    "NH투자증권": 0.22,       # KRX 금현물
}

# 종목별 ETF 연보수 (%, 1년)
DEFAULT_EXPENSE: dict[str, float] = {
    "360750.KS": 0.0939,   # TIGER 미국S&P500
    "357870.KS": 0.0543,   # TIGER CD금리투자KIS(합성)
    "379810.KS": 0.1447,   # KODEX 미국나스닥100 (매수 예정)
    "QQQM": 0.15,
    "SPYM": 0.02,
    "GLDM": 0.10,
    "SGOV": 0.09,
}


def _clean(d, upper: bool = False) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (d or {}).items():
        if k is None or (isinstance(k, float) and math.isnan(k)):
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        k = str(k).strip()
        if k and math.isfinite(x) and x >= 0:
            out[k.upper() if upper else k] = x
    return out


def merge(saved: dict | None) -> dict:
    """저장된 값이 있으면 그것을, 없으면 기본값을 쓴다. {"brokers": {...}, "expense": {...}}"""
    saved = saved or {}
    brokers = _clean(saved["brokers"]) if "brokers" in saved else dict(DEFAULT_BROKER_FEES)
    expense = _clean(saved["expense"], upper=True) if "expense" in saved else dict(DEFAULT_EXPENSE)
    return {"brokers": brokers, "expense": expense}


def load() -> dict:
    from lib import portfolio as pf
    try:
        return merge(pf.get_setting(SETTINGS_KEY))
    except Exception:
        return merge(None)


def save(brokers: dict, expense: dict) -> None:
    from lib import portfolio as pf
    pf.set_setting(SETTINGS_KEY, {"brokers": _clean(brokers), "expense": _clean(expense, upper=True)})


# ── 매매 수수료 ─────────────────────────────────────────────

def _norm(s) -> str:
    return "".join(str(s or "").split()).upper()


def rate_for_account(account_id, accounts: pd.DataFrame, rates: dict) -> float | None:
    """계좌의 증권사 수수료율(%). 계좌를 안 골랐거나 요율이 없는 증권사면 None."""
    if account_id is None or accounts is None or accounts.empty:
        return None
    try:
        hit = accounts[accounts["id"] == account_id]
    except Exception:
        return None
    if hit.empty:
        return None
    broker = _norm(hit["broker"].iloc[0])
    if not broker:
        return None
    for name, r in rates.get("brokers", {}).items():
        if _norm(name) == broker:
            return float(r)
    return None


def trade_fee(quantity: float, price: float, rate_pct: float | None, currency: str) -> float:
    """거래금액 × 요율. 원화는 원 미만을 버리고(증권사 관행), 외화는 센트 단위로 반올림한다."""
    if not rate_pct or quantity <= 0 or price <= 0:
        return 0.0
    raw = quantity * price * rate_pct / 100
    if currency == "KRW":
        return float(math.floor(raw + 1e-9))
    return round(raw + 1e-12, 2)


def fee_hint(rate_pct: float | None, fee: float, currency: str) -> str:
    """입력칸 아래 한 줄 설명."""
    if rate_pct is None:
        return "이 계좌 증권사의 수수료율이 없어 0으로 넣어요. ‘설정 → 수수료·연보수’에서 정할 수 있어요."
    amt = f"₩{fee:,.0f}" if currency == "KRW" else f"{fee:,.2f} {currency}"
    return f"수수료를 비워 두면 {rate_pct:g}%로 계산한 {amt}을 넣어요."


# ── 연보수 ─────────────────────────────────────────────────

def expense_of(symbol: str, rates: dict) -> float | None:
    return rates.get("expense", {}).get(str(symbol).upper())


def yearly_cost(held: pd.DataFrame, rates: dict) -> tuple[pd.Series, float, float]:
    """보유 종목(symbol, value_krw)별 연보수율, 1년 예상 비용(원), 연보수가 있는 종목의 평가금액 합.

    연보수는 ETF 기준가에 매일 조금씩 녹아 있어 수익률에 이미 반영돼 있다. 여기 금액은 '참고용'이다.
    """
    if held is None or held.empty:
        return pd.Series(dtype=float), 0.0, 0.0
    er = held["symbol"].map(lambda s: expense_of(s, rates))
    has = er.notna()
    cost = float((held.loc[has, "value_krw"] * er[has] / 100).sum())
    return er, cost, float(held.loc[has, "value_krw"].sum())
