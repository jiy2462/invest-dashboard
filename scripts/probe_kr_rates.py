"""임시: 해외 서버(GitHub Actions)에서 한국 금리 후보 출처가 열리는지 확인."""
import json, time, requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
now = int(time.time())
C = [
    ("naver-m-prices90", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT=RR&page=1&pageSize=90", {}),
    ("naver-m-prices10", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR&page=1&pageSize=10", {}),
    ("naver-m-prices-nopage", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR", {}),
    ("naver-m-detail", "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=bond&reutersCode=KR10YT%3DRR", {}),
    ("naver-api-bond", "https://api.stock.naver.com/marketindex/bond/KR10YT%3DRR", {}),
    ("naver-api-bond-prices", "https://api.stock.naver.com/marketindex/bond/KR10YT%3DRR/prices?page=1&pageSize=20", {}),
    ("naver-m-interest-cd", "https://m.stock.naver.com/front-api/marketIndex/prices?category=interest&reutersCode=IRR_CD91&page=1&pageSize=10", {}),
    ("naver-api-interest-cd", "https://api.stock.naver.com/marketindex/interest/IRR_CD91/prices?page=1&pageSize=10", {}),
    ("naver-daily-cd", "https://finance.naver.com/marketindex/interestDailyQuote.naver?marketindexCd=IRR_CD91&page=1", {}),
    ("naver-detail-cd", "https://finance.naver.com/marketindex/interestDetail.naver?marketindexCd=IRR_CD91", {}),
    ("naver-siseJson", "https://api.finance.naver.com/siseJson.naver?symbol=KR10YT=RR&requestType=1&startTime=20261001&endTime=20261118&timeframe=day", {}),
    ("naver-polling-kospi", "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI", {}),
    ("daum-days-cd", "https://finance.daum.net/api/global/commodities/BOND-KRCD%3DKQ/days?page=1&perPage=10", {"Referer": "https://finance.daum.net/"}),
    ("daum-m-days-cd", "https://m.finance.daum.net/api/global/commodities/BOND-KRCD%3DKQ/days?page=1&perPage=10", {"Referer": "https://m.finance.daum.net/"}),
    ("daum-charts-cd", "https://finance.daum.net/api/charts/BOND-KRCD%3DKQ/days?limit=30&adjusted=true", {"Referer": "https://finance.daum.net/"}),
    ("daum-quote-cd", "https://finance.daum.net/api/quotes/BOND-KRCD%3DKQ", {"Referer": "https://finance.daum.net/"}),
    ("daum-global-cd", "https://finance.daum.net/api/global/commodities/BOND-KRCD%3DKQ", {"Referer": "https://finance.daum.net/"}),
    ("daum-page-cd", "https://m.finance.daum.net/global/commodities/BOND-KRCD%3DKQ", {}),
    ("daum-days-10y", "https://finance.daum.net/api/global/commodities/BOND-KR10YT%3DRR/days?page=1&perPage=10", {"Referer": "https://finance.daum.net/"}),
    ("cnbc-quote", "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=KR10Y-KR%7CKR3Y-KR&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json", {}),
    ("cnbc-bars", f"https://ts-api.cnbc.com/harmony/app/bars/KR10Y-KR/1D/20260801000000/20261118000000/adjusted/EST5EDT.json", {}),
    ("investing-hist", "https://api.investing.com/api/financialdata/historical/29292?start-date=2026-10-01&end-date=2026-11-18&time-frame=Daily&add-missing-rows=false", {"domain-id": "www"}),
]
for name, url, extra in C:
    try:
        r = requests.get(url, headers={**UA, **extra}, timeout=10)
        body = r.text[:500].replace("\n", " ")
        print(f"### {name} {r.status_code} {r.headers.get('content-type')} {len(r.text)}\n{body}\n")
    except Exception as e:
        print(f"### {name} ERR {type(e).__name__}: {str(e)[:200]}\n")
