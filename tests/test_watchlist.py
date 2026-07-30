from strategy.watchlist import classify


def config():
    return {"min_score": 66, "watchlist_margin": 8}


def test_passed_requires_score_and_all_conditions():
    status, reason, missing = classify(70, {"trend": True, "adx": True}, config())
    assert (status, reason, missing) == ("PASSED", "passed", [])


def test_watchlist_can_have_one_missing_filter():
    status, reason, missing = classify(68, {"trend": True, "adx": False}, config())
    assert status == "WATCHLIST"
    assert reason == "watchlist"
    assert missing == ["adx"]


def test_rejected_when_too_many_conditions_fail():
    status, reason, missing = classify(
        68,
        {"trend": False, "adx": False, "volume": False},
        config(),
    )
    assert status == "REJECTED"
    assert reason == "trend"
    assert len(missing) == 3
