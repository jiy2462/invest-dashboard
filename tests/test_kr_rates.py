"""한국 금리 파서와 출처 순서 테스트. 응답 샘플은 2026-11-18 해외 서버(GitHub Actions)에서 받은 실제 응답 모양이다."""
import pandas as pd
import pytest

from lib import kr_rates, market

# api.stock.naver.com/marketindex/bond/KR10YT=RR/prices (목록)
BOND_PAGE = [
    {"localTradedAt": "2026-11-17T15:54:40+09:00", "closePrice": "3.9850", "fluctuations": "0.0030",
     "openPrice": "3.9790", "highPrice": "4.0040", "lowPrice": "3.9640"},
    {"localTradedAt": "2026-11-16T15:49:30+09:00", "closePrice": "3.9820", "fluctuations": "-0.0050",
     "openPrice": "3.9900", "highPrice": "4.0100", "lowPrice": "3.9770"},
]
# m.stock.naver.com/front-api/marketIndex/prices?category=domesticInterest&reutersCode=KFIA114000 ({"result": 목록})
CD_PAGE = {"isSuccess": True, "detailCode": "", "message": "", "result": [
    {"localTradedAt": "2026-11-17T00:00:00+09:00", "closePrice": "3.380", "openPrice": "0", "highPrice": "0", "lowPrice": "0"},
    {"localTradedAt": "2026-11-16T00:00:00+09:00", "closePrice": "3.380", "openPrice": "0", "highPrice": "0", "lowPrice": "0"},
]}
# api.stock.naver.com/marketindex/bond/KR10YT=RR (지금 시세, 장중)
BOND_NOW = {"categoryType": "bond", "reutersCode": "KR10YT=RR", "name": "한국 국채 10년",
            "localTradedAt": "2026-11-18T12:16:27+09:00", "closePrice": "3.9560", "fluctuations": "-0.0290"}
CNBC_BARS = {"barData": {"priceBars": [
    {"open": "4.2610", "high": "4.2620", "low": "4.2610", "close": "4.2620", "tradeTime": "20260801000000"},
    {"open": "4.2620", "high": "4.2780", "low": "4.2470", "close": "4.2660", "tradeTime": "20260803000000"},
]}}


def test_parse_naver_bond_page():
    df = kr_rates.parse_naver_prices(BOND_PAGE)
    assert list(df.index) == [pd.Timestamp("2026-11-16"), pd.Timestamp("2026-11-17")]
    assert df.loc["2026-11-17", "Close"] == 3.985 and df.loc["2026-11-17", "High"] == 4.004


def test_parse_naver_cd_page_zero_ohlc_becomes_close():
    df = kr_rates.parse_naver_prices(CD_PAGE)
    assert df["Close"].iloc[-1] == 3.38 and df["Open"].isna().all()
    filled = market.clean_ohlc(df)
    assert (filled["Open"] == filled["Close"]).all()


def test_parse_cnbc_bars():
    df = kr_rates.parse_cnbc_bars(CNBC_BARS)
    assert df.loc["2026-08-03", "Close"] == 4.266 and len(df) == 2


def test_naver_pages_and_now_merged(monkeypatch):
    seen = []

    def get(url):
        seen.append(url)
        if "/prices" in url:
            if "page=1&" in url:
                return BOND_PAGE
            if "page=2&" in url:
                return [{"localTradedAt": "2026-08-19T15:56:43+09:00", "closePrice": "4.4130"}]
            raise ConnectionError("오래된 페이지 실패")
        return BOND_NOW

    monkeypatch.setattr(kr_rates, "_get_json", get)
    df = kr_rates.naver_history("bond", "KR10YT=RR")
    assert df.index[0] == pd.Timestamp("2026-08-19")
    assert df.index[-1] == pd.Timestamp("2026-11-18") and df["Close"].iloc[-1] == 3.956  # 장중 값이 오늘 봉으로
    assert all("pageSize=60" in u for u in seen if "/prices" in u)  # 네이버 최대 60
    assert all(u.startswith("https://api.stock.naver.com") for u in seen)


def test_naver_falls_back_to_mobile_server(monkeypatch):
    def get(url):
        if url.startswith("https://api.stock"):
            raise ConnectionError("막힘")
        if "/prices" in url:
            return CD_PAGE if "page=1&" in url else {"isSuccess": True, "result": []}
        return {"isSuccess": True, "result": CD_PAGE["result"][0]}

    monkeypatch.setattr(kr_rates, "_get_json", get)
    df = kr_rates.naver_history("domesticInterest", "KFIA114000")
    assert df["Close"].iloc[-1] == 3.38 and len(df) == 2


def test_cd91_uses_naver_kofia_code(monkeypatch):
    calls = []
    s = kr_rates.parse_naver_prices(CD_PAGE)
    monkeypatch.setattr(kr_rates, "fetch_naver", lambda cat, code: calls.append((cat, code)) or s)
    assert kr_rates.fetch("ecos:CD91")["Close"].iloc[-1] == 3.38
    assert calls == [("domesticInterest", "KFIA114000")]


def test_naver_first_then_ecos(monkeypatch):
    s = pd.Series({pd.Timestamp("2026-11-04"): 2.6, pd.Timestamp("2026-11-05"): 2.59})
    monkeypatch.setattr(market, "ecos_key", lambda: "KEY")
    monkeypatch.setattr(market.kr_rates, "fetch", lambda sym: s)
    monkeypatch.setattr(market, "fetch_ecos", lambda sym, key: (_ for _ in ()).throw(AssertionError("ECOS 호출 안 돼야 함")))
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {})
    out, errors = market.load_series([{"symbol": "ecos:CD91", "source": "ecos"}])
    assert out["ecos:CD91"].iloc[-1] == 2.59 and not errors


def test_falls_back_to_ecos_and_reports_both(monkeypatch):
    def fail(sym):
        raise kr_rates.KrRateError("네이버 ConnectTimeout")

    def ecos_fail(sym, key):
        raise market.EcosError("ECOS 접속 실패: ConnectTimeout")

    monkeypatch.setattr(market, "ecos_key", lambda: "KEY")
    monkeypatch.setattr(market.kr_rates, "fetch", fail)
    monkeypatch.setattr(market, "fetch_ecos", ecos_fail)
    monkeypatch.setattr(market, "fetch_yf_ohlc", lambda syms: {})
    out, errors = market.load_series([{"symbol": "ecos:KR10Y", "source": "ecos"}])
    assert not out
    assert "네이버" in errors["ecos:KR10Y"] and "ECOS" in errors["ecos:KR10Y"]


def test_naver_fails_then_cnbc_and_all_reasons(monkeypatch):
    def naver(cat, code):
        raise kr_rates.KrRateError("네이버 KR10YT=RR: api ConnectTimeout, m ConnectTimeout")

    def cnbc_fail(code):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(kr_rates, "fetch_naver", naver)
    monkeypatch.setattr(kr_rates, "fetch_cnbc", cnbc_fail)
    with pytest.raises(kr_rates.KrRateError) as e:
        kr_rates.fetch("ecos:KR10Y")
    assert "네이버" in str(e.value) and "CNBC RuntimeError (403 Forbidden)" in str(e.value)

    monkeypatch.setattr(kr_rates, "fetch_cnbc", lambda code: kr_rates.parse_cnbc_bars(CNBC_BARS))
    assert kr_rates.fetch("ecos:KR10Y")["Close"].iloc[-1] == 4.266


def test_naver_success_short_circuits(monkeypatch):
    s = kr_rates.parse_naver_prices(BOND_PAGE)
    monkeypatch.setattr(kr_rates, "fetch_naver", lambda cat, code: s)
    monkeypatch.setattr(kr_rates, "fetch_cnbc", lambda code: (_ for _ in ()).throw(AssertionError("호출 안 돼야 함")))
    assert kr_rates.fetch("ecos:KR10Y")["Close"].iloc[-1] == 3.985


def test_failed_kr_rate_waits_longer_before_retry():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
    entry = {"df": None, "err": "x", "at": now - timedelta(minutes=10)}
    assert market.is_fresh("ecos:KR10Y", entry, now)      # 15분 동안은 다시 안 기다림
    assert not market.is_fresh("^GSPC", entry, now)       # 야후 지표는 5분
