"""Classification helpers for Passed, Watchlist and Rejected states."""

from __future__ import annotations


def classify(score: int, conditions: dict[str, bool], market_config: dict) -> tuple[str, str, list[str]]:
    failed = [name for name, passed in conditions.items() if not passed]
    min_score = int(market_config["min_score"])
    score_passed = score >= min_score

    if not failed and score_passed:
        return "PASSED", "passed", []

    watchlist_limit = min_score - int(market_config["watchlist_margin"])
    if score >= watchlist_limit and len(failed) <= 2:
        missing = failed + ([] if score_passed else ["score"])
        return "WATCHLIST", "watchlist", missing

    return "REJECTED", (failed[0] if failed else "score"), failed
