"""임시: 새 kr_rates 코드를 해외 서버(GitHub Actions)에서 실제로 돌려 본다."""
import time
from lib import kr_rates, market

for sym in ["ecos:KR10Y", "ecos:KR3Y", "ecos:CD91"]:
    t = time.time()
    df = kr_rates.fetch(sym)
    print(sym, f"{time.time()-t:.2f}s", len(df), df.index[0].date(), "~", df.index[-1].date())
    print(df.tail(3))
t = time.time()
print("CNBC", kr_rates.fetch_cnbc("KR10Y-KR").tail(2), f"{time.time()-t:.2f}s")
t = time.time()
data, errors = market.load_ohlc([{"symbol": s, "source": "ecos"} for s in ["ecos:KR10Y", "ecos:KR3Y", "ecos:CD91"]], cached=False)
print("load_ohlc", f"{time.time()-t:.2f}s", {k: len(v) for k, v in data.items()}, errors)
