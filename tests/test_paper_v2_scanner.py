from strategy.paper_v2_scanner import PaperV2Scanner


def make_signal(
    symbol: str,
    score: float,
    relative_strength: float,
    adx: float,
    volume_ratio: float,
) -> dict:
    return {
        "symbol": symbol,
        "regime": "BULL",
        "breadth_ema50_pct": 80.0,
        "breadth_ema50_change_10d": 5.0,
        "score": score,
        "relative_strength_20d": relative_strength,
        "adx": adx,
        "volume_ratio": volume_ratio,
    }


def test_process_uses_full_quality_universe():
    scanner = PaperV2Scanner(threshold=0.70)

    universe = [
        make_signal("AAA", 100, 100, 50, 2.0),
        make_signal("BBB", 50, 50, 20, 1.0),
        make_signal("CCC", 25, 25, 10, 0.8),
    ]

    signals = [universe[0]]

    accepted, stats = scanner.process(
        signals,
        {"evaluations": universe},
    )

    assert len(accepted) == 1
    assert accepted[0]["symbol"] == "AAA"

    assert stats["paper_v2_version"] == "Q70_FROZEN"
    assert stats["paper_v2_quality_universe"] == 3
    assert stats["paper_v2_quality_scored"] == 3
    assert stats["paper_v2_rejected"] == []


def test_process_records_rejected_signals():
    scanner = PaperV2Scanner(threshold=0.70)

    universe = [
        make_signal("AAA", 100, 100, 50, 2.0),
        make_signal("BBB", 50, 50, 20, 1.0),
        make_signal("CCC", 25, 25, 10, 0.8),
    ]

    signals = [universe[1], universe[2]]

    accepted, stats = scanner.process(
        signals,
        {"evaluations": universe},
    )

    assert accepted == []

    assert len(stats["paper_v2_rejected"]) == 2
    assert {
        item["symbol"]
        for item in stats["paper_v2_rejected"]
    } == {"BBB", "CCC"}