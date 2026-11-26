"""일회성 점검: 오늘의 시장 지표를 실제 출처에서 받아 마지막 줄들을 출력한다 (GitHub Actions에서 실행)."""
import sys, warnings
from datetime import datetime, timezone
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import pandas as pd
pd.set_option("display.width", 200)
from lib import market, live_quote, market_hours, fear_greed, kr_rates

now = datetime.now(timezone.utc)
print("NOW UTC", now, "KST", now.astimezone(live_quote.KST))
YF = ("^GSPC", "^NDX", "^KS11", "^KQ11", "^N225", "^HSI", "BTC-USD", "GC=F", "CL=F", "SI=F",
      "KRW=X", "DX-Y.NYB", "^IRX", "^TNX", "^VIX")
data = market.fetch_yf_ohlc(YF)
for s in YF:
    df = data.get(s)
    print(f"\n=== {s} live={market_hours.is_live(s, now)}")
    print(None if df is None else df.tail(4))
print("\n=== per-ticker history (single download) for asian")
import yfinance as yf
for s in ("^KS11", "^KQ11", "^N225", "^HSI"):
    print(s); print(yf.Ticker(s).history(period="7d", interval="1d", auto_adjust=False).tail(4)[["Open","High","Low","Close"]])
live = tuple(s for s in YF if market_hours.is_live(s, now))
print("\nLIVE", live)
qs = live_quote.fetch_yahoo(live)
for s, q in qs.items():
    print("YQ", s, q)
for s in ("^KS11", "^KQ11"):
    try:
        print("NQ", s, live_quote.fetch_naver(s, now))
    except Exception as e:
        print("NQ ERR", s, e)
for s, q in qs.items():
    df = data.get(s)
    if df is not None:
        out = live_quote.apply(df, q)
        sm = market.summarize(out["Close"], False)
        print("APPLIED", s, out.tail(3).to_dict("index"), "pct", round(sm["pct"], 3))
print("\n=== CNN")
try:
    raw = fear_greed._package_fetch()
    print("now:", raw.get("fear_and_greed"))
    print(fear_greed.parse(raw).tail(4))
except Exception as e:
    print("CNN ERR", repr(e))
print("\n=== KR rates")
for s in ("ecos:KR10Y", "ecos:CD91"):
    try:
        print(s); print(kr_rates.fetch(s).tail(4))
    except Exception as e:
        print("ERR", e)
