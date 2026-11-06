"""한국 금리 파서와 출처 순서 테스트. 응답 형식 샘플은 네이버 화면 구조를 바탕으로 만든 것이다."""
import pandas as pd

from lib import kr_rates, market

DAILY_HTML = """
<table class="tbl_exchange today"><tbody>
<tr class="down">
  <td class="date">2026.11.05</td>
  <td class="num">2.590</td>
  <td class="num"><img src="down.gif"> 0.01</td>
</tr>
<tr class="up">
  <td class="date">2026.11.04</td>
  <td class="num">2.600</td>
  <td class="num"><img src="up.gif"> 0.02</td>
</tr>
</tbody></table>
"""

BOND_JSON = {"isSuccess": True, "result": [
    {"localTradedAt": "2026-11-05", "closePrice": "2.951", "fluctuations": "-0.012"},
    {"localTradedAt": "2026-11-04", "closePrice": "2.963", "fluctuations": "0.020"},
]}


def test_parse_daily_html():
    got = kr_rates.parse_daily_html(DAILY_HTML)
    assert got == {pd.Timestamp("2026-11-05"): 2.59, pd.Timestamp("2026-11-04"): 2.6}


def test_parse_bond_json():
    got = kr_rates.parse_bond_json(BOND_JSON)
    assert got[pd.Timestamp("2026-11-05")] == 2.951 and len(got) == 2


def test_naver_first_then_ecos(monkeypatch):
    s = pd.Series({pd.Timestamp("2026-11-04"): 2.6, pd.Timestamp("2026-11-05"): 2.59})
    monkeypatch.setattr(market, "ecos_key", lambda: "KEY")
    monkeypatch.setattr(market.kr_rates, "fetch", lambda sym: s)
    monkeypatch.setattr(market, "fetch_ecos", lambda sym, key: (_ for _ in ()).throw(AssertionError("ECOS 호출 안 돼야 함")))
    monkeypatch.setattr(market, "fetch_yf_closes", lambda syms: pd.DataFrame())
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
    monkeypatch.setattr(market, "fetch_yf_closes", lambda syms: pd.DataFrame())
    out, errors = market.load_series([{"symbol": "ecos:KR10Y", "source": "ecos"}])
    assert not out
    assert "네이버" in errors["ecos:KR10Y"] and "ECOS" in errors["ecos:KR10Y"]
