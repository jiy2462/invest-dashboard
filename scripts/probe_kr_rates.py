"""임시: 해외 서버(GitHub Actions)에서 한국 금리 후보 출처가 열리는지 확인."""
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
lst = requests.get("https://api.stock.naver.com/marketindex/domesticInterest", headers=UA, timeout=10).json()
for it in lst:
    print("ITEM", it.get("reutersCode"), it.get("name"), it.get("closePrice"), it.get("localTradedAt"))
codes = [it["reutersCode"] for it in lst if "CD" in (it.get("name") or "") or "CD" in it.get("reutersCode", "")]
for code in codes:
    for name, url in [
        ("api-prices", f"https://api.stock.naver.com/marketindex/domesticInterest/{code}/prices?page=1&pageSize=60"),
        ("api-prices-p3", f"https://api.stock.naver.com/marketindex/domesticInterest/{code}/prices?page=3&pageSize=60"),
        ("m-prices", f"https://m.stock.naver.com/front-api/marketIndex/prices?category=domesticInterest&reutersCode={code}&page=1&pageSize=60"),
        ("api-detail", f"https://api.stock.naver.com/marketindex/domesticInterest/{code}"),
    ]:
        try:
            r = requests.get(url, headers=UA, timeout=10)
            print(f"### {code} {name} {r.status_code} {len(r.text)}\n{r.text[:900]}\n")
        except Exception as e:
            print(f"### {code} {name} ERR {type(e).__name__}")
