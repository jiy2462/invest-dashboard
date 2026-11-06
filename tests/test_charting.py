from lib.charting import history_period


def test_history_period_fetches_only_what_the_range_needs():
    assert history_period("30m", 60) == "60d"
    assert history_period("1d", 365) == "2y"      # 1년 + 200일선
    assert history_period("1d", 1095) == "5y"     # 3년
    assert history_period("1d", 3652) == "max"    # 10년 + 200일선
    assert history_period("1d", None) == "max"    # 최대
    assert history_period("1wk", 365) == "max"    # 주봉은 전체도 가벼움
