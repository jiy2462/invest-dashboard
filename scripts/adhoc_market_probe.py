"""일회성 점검 4: 고친 코드로 실제 데이터 받아 보기."""
import sys, warnings
from datetime import datetime, timezone
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import pandas as pd
from lib import market, market_hours
syms = ["^GSPC", "^KS11", "^KQ11", "^N225", "^HSI", "BTC-USD", "^VIX", "KRW=X"]
settings = [{"symbol": s, "source": "yfinance"} for s in syms] + [
    {"symbol": "ecos:CD91", "source": "ecos"}, {"symbol": "cnn:fear_greed", "source": "cnn"}]
now = datetime.now(timezone.utc)
data, errors = market.load_ohlc(settings, now=now)
print("NOW", now, "errors", errors)
for s in syms + ["ecos:CD91", "cnn:fear_greed"]:
    df = data.get(s)
    if df is None:
        continue
    sm = market.summarize(df["Close"], s.startswith("ecos"))
    print(f"{s:10} last {df.index[-1].date()} close {sm['last']:.2f} pct {sm['pct']:+.2f}% vs {sm['prev_asof'].date()} "
          f"src={df.attrs.get('source')} asof={df.attrs.get('asof')} | days={[d.strftime('%m-%d') for d in df.index[-4:]]}")
