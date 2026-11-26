"""일회성 점검 3: 네이버 해외지수 실시간·CD 상세 원문."""
import json, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Referer": "https://m.stock.naver.com/"}
for u in ["https://polling.finance.naver.com/api/realtime/worldstock/index/.N225",
          "https://polling.finance.naver.com/api/realtime/worldstock/index/.HSI",
          "https://api.stock.naver.com/index/.HSI/basic",
          "https://api.stock.naver.com/marketindex/domesticInterest/KFIA114000",
          "https://m.stock.naver.com/api/index/KOSDAQ/price?page=1&pageSize=3",
          "https://api.stock.naver.com/index/.HSI/price?page=1&pageSize=3",
          "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=domesticInterest&reutersCode=KFIA114000"]:
    r = requests.get(u, headers=H, timeout=8)
    print("\nURL", u, r.status_code)
    try:
        b = r.json()
        def strip(o):
            if isinstance(o, dict):
                return {k: strip(v) for k, v in o.items() if k not in ("stockExchangeType", "description", "itemLogoUrl", "itemLogoPngUrl")}
            if isinstance(o, list):
                return [strip(v) for v in o[:3]]
            return o
        print(json.dumps(strip(b), ensure_ascii=False)[:2500])
    except Exception:
        print(r.text[:500])
