"""일회성 점검 2: 야후 일봉 빈 날을 채울 출처 찾기."""
import json, sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")
import pandas as pd, requests, yfinance as yf
pd.set_option("display.width", 200)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Referer": "https://m.stock.naver.com/"}
T = ("^KS11", "^KQ11", "^N225", "^HSI", "BTC-USD", "^GSPC")
print("=== yahoo 60m 7d")
raw = yf.download(list(T), period="7d", interval="60m", group_by="ticker", progress=False, auto_adjust=False, ignore_tz=False)
for s in T:
    try:
        b = raw[s].dropna(subset=["Close"])
        print(s, sorted({str(t.date()) for t in b.index}), len(b))
    except Exception as e:
        print(s, "ERR", e)
print("=== yahoo 1d 5d single for HSI/BTC")
for s in ("^HSI", "BTC-USD"):
    print(yf.Ticker(s).history(period="5d", interval="1d").tail(5)[["Close"]])
print("=== yahoo 1m 1d HSI single")
print(yf.Ticker("^HSI").history(period="1d", interval="1m").tail(3))
print("=== yahoo 5d 1m HSI")
print(yf.download("^HSI", period="5d", interval="1m", progress=False).tail(3))
def show(url, n=600):
    try:
        r = requests.get(url, headers=H, timeout=8)
        print("\nURL", url, r.status_code); print(r.text[:n])
    except Exception as e:
        print("\nURL", url, "ERR", e)
for u in [
    "https://api.stock.naver.com/index/KOSPI/price?page=1&pageSize=5",
    "https://m.stock.naver.com/api/index/KOSPI/price?page=1&pageSize=5",
    "https://api.stock.naver.com/index/.N225/price?page=1&pageSize=5",
    "https://api.stock.naver.com/index/.HSI/price?page=1&pageSize=5",
    "https://api.stock.naver.com/index/.N225/basic",
    "https://api.stock.naver.com/index/.HSI/basic",
    "https://polling.finance.naver.com/api/realtime/worldstock/index/.HSI",
    "https://polling.finance.naver.com/api/realtime/worldstock/index/.N225",
    "https://api.stock.naver.com/marketindex/domesticInterest/KFIA114000",
    "https://api.stock.naver.com/marketindex/domesticInterest/KFIA114000/prices?page=1&pageSize=5",
    "https://api.stock.naver.com/marketindex/domesticInterest",
    "https://api.upbit.com/v1/candles/days?market=USDT-BTC&count=4",
    "https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD/candles?granularity=ONE_DAY&limit=4",
    "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400",
]:
    show(u)
