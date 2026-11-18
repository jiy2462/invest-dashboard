"""임시: 해외 서버(GitHub Actions)에서 한국 금리 후보 출처가 열리는지 확인."""
import json, time, requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
now = int(time.time())
C = [
    ("m-domint-cd", "https://m.stock.naver.com/front-api/marketIndex/prices?category=domesticInterest&reutersCode=IRR_CD91&page=1&pageSize=60", {}),
    ("m-domint-cd-detail", "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=domesticInterest&reutersCode=IRR_CD91", {}),
    ("api-domint-cd", "https://api.stock.naver.com/marketindex/domesticInterest/IRR_CD91", {}),
    ("api-domint-cd-prices", "https://api.stock.naver.com/marketindex/domesticInterest/IRR_CD91/prices?page=1&pageSize=60", {}),
    ("api-domint-list", "https://api.stock.naver.com/marketindex/domesticInterest", {}),
    ("m-domint-list", "https://m.stock.naver.com/front-api/marketIndex/domesticInterest", {}),
    ("m-majors-domint", "https://m.stock.naver.com/front-api/marketIndex/majors?category=domesticInterest", {}),
    ("m-list-domint", "https://m.stock.naver.com/front-api/marketIndex/list?category=domesticInterest", {}),
    ("m-bond-p2", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR&page=2&pageSize=60", {}),
    ("m-bond-p6", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR10YT%3DRR&page=6&pageSize=60", {}),
    ("m-bond-3y", "https://m.stock.naver.com/front-api/marketIndex/prices?category=bond&reutersCode=KR3YT%3DRR&page=1&pageSize=60", {}),
    ("m-page-cd", "https://m.stock.naver.com/marketindex/domesticInterest/IRR_CD91", {}),
    ("cnbc-bars-3y", "https://ts-api.cnbc.com/harmony/app/bars/KR3Y-KR/1D/20250601000000/20261118000000/adjusted/EST5EDT.json", {}),
]
for name, url, extra in C:
    try:
        r = requests.get(url, headers={**UA, **extra}, timeout=10)
        body = r.text[:1500].replace("\n", " ")
        print(f"### {name} {r.status_code} {r.headers.get('content-type')} {len(r.text)}\n{body}\n")
    except Exception as e:
        print(f"### {name} ERR {type(e).__name__}: {str(e)[:200]}\n")
