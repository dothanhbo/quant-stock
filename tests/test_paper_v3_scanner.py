from strategy.paper_v3_scanner import PaperV3Scanner


def signal(symbol, breadth):
    return {
        "symbol": symbol,
        "regime": "BULL",
        "breadth_ema50_pct": breadth,
        "breadth_ema50_change_10d": 0.0,
        "score": 100.0,
        "relative_strength_20d": 100.0,
        "adx": 50.0,
    }


def test_v3_assigns_breadth_exposure_without_changing_q70():
    universe = [signal("AAA", 80), signal("BBB", 70), signal("CCC", 60)]
    accepted, stats = PaperV3Scanner().process(
        [universe[0], universe[1], universe[2]],
        {"evaluations": universe},
    )

    assert [x["symbol"] for x in accepted] == ["AAA", "BBB", "CCC"]
    assert [x["breadth_exposure_multiplier"] for x in accepted] == [1.0, 1.0, 1.0]
    assert stats["paper_v3_version"] == "V3_BREADTH_40_60"


def test_v3_blocks_below_40_and_halves_40_to_60():
    universe = [
        signal("AAA", 80),
        signal("BBB", 55),
        signal("CCC", 35),
    ]
    accepted, stats = PaperV3Scanner().process(
        universe,
        {"evaluations": universe},
    )

    assert [x["symbol"] for x in accepted] == ["AAA", "BBB"]
    assert accepted[0]["breadth_exposure_multiplier"] == 1.0
    assert accepted[1]["breadth_exposure_multiplier"] == 0.5
    assert [x["symbol"] for x in stats["paper_v3_breadth_blocked"]] == ["CCC"]
