"""오늘의 시장: 끌어서 순서 바꾸기."""
from lib import indicators
from tests import fakes
from tests.test_pages import run

L = indicators.order_label


def rows():
    return [dict(r) for r in fakes.TABLES["indicator_settings"]]


def test_plan_reorders_groups_items_and_moves_between_groups():
    rs = rows()  # 지수: ^GSPC(1) ^KS11(2) / 환율: KRW=X(3) / 금리: ^TNX(4) ecos:KR10Y(5)
    by = {r["symbol"]: r for r in rs}
    containers = [
        {"header": "환율", "items": [L(by["KRW=X"]), L(by["^TNX"])]},     # 미국 10년물을 환율로 옮김
        {"header": "지수", "items": [L(by["^KS11"]), L(by["^GSPC"])]},
        {"header": "금리", "items": [L(by["ecos:KR10Y"])]},
    ]
    plan = {u["id"]: u for u in indicators.reorder_plan(rs, containers)}
    order = sorted(((u["sort_order"], u["id"]) for u in plan.values()))
    assert by["KRW=X"]["id"] in plan and plan[by["KRW=X"]["id"]]["sort_order"] == 1
    assert plan[by["^TNX"]["id"]] == {"id": by["^TNX"]["id"], "group_name": "환율", "sort_order": 2}
    assert plan[by["^GSPC"]["id"]]["sort_order"] == 4
    assert by["ecos:KR10Y"]["id"] not in plan  # 5번 그대로
    assert order


def test_hidden_rows_go_last_and_unchanged_not_written():
    rs = rows()
    rs.append(dict(id=9, symbol="^VIX", display_name="VIX", group_name="변동성", sort_order=3, is_visible=False))
    same = [{"header": "지수", "items": [L(rs[0]), L(rs[1])]}, {"header": "환율", "items": [L(rs[2])]},
            {"header": "금리", "items": [L(rs[3]), L(rs[4])]}]
    plan = indicators.reorder_plan(rs, same)
    assert plan == [{"id": 9, "group_name": "변동성", "sort_order": 6}]


def test_save_order_writes_db_and_page_shows_editor():
    rs = rows()
    indicators.save_order([{"id": rs[0]["id"], "group_name": "맨앞", "sort_order": 9},
                           {"id": rs[1]["id"], "group_name": "맨앞", "sort_order": 8}])
    t = {r["id"]: r for r in fakes.TABLES["indicator_settings"]}
    assert (t[rs[0]["id"]]["sort_order"], t[rs[1]["id"]]["sort_order"]) == (9, 8)

    at = run("market")
    at.toggle(key="mkt-reorder").set_value(True).run()
    assert not at.exception
    assert any("카테고리 순서" in m.value for m in at.markdown)
    [b for b in at.button if b.key == "cancel-order"][0].click().run()
    assert not at.exception and at.session_state["mkt-reorder"] is False
