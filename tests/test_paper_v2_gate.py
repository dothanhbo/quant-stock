from strategy.paper_v2_gate import (
    PaperV2QualityGate,
    classify_state,
)


def make_signal(
    symbol="AAA",
    regime="BULL",
    breadth=80.0,
    breadth_change=5.0,
    score=80.0,
    relative_strength=80.0,
    adx=30.0,
    volume_ratio=1.5,
):
    return {
        "symbol": symbol,
        "regime": regime,
        "breadth_ema50_pct": breadth,
        "breadth_ema50_change_10d": breadth_change,
        "score": score,
        "relative_strength_20d": relative_strength,
        "adx": adx,
        "volume_ratio": volume_ratio,
    }


def test_classify_bear():
    signal = make_signal(regime="BEAR")

    assert classify_state(signal) == "BEAR"


def test_classify_divergent_bull():
    signal = make_signal(
        regime="BULL",
        breadth=45.0,
        breadth_change=-3.0,
    )

    assert classify_state(signal) == "DIVERGENT_BULL"


def test_classify_healthy_bull():
    signal = make_signal(
        regime="BULL",
        breadth=75.0,
        breadth_change=3.0,
    )

    assert classify_state(signal) == "HEALTHY_BULL"


def test_classify_fragile_bull():
    signal = make_signal(
        regime="BULL",
        breadth=55.0,
        breadth_change=-1.0,
    )

    assert classify_state(signal) == "FRAGILE_BULL"


def test_classify_recovery():
    signal = make_signal(
        regime="SIDEWAY",
        breadth=65.0,
        breadth_change=2.0,
    )

    assert classify_state(signal) == "RECOVERY"


def test_classify_neutral():
    signal = make_signal(
        regime="SIDEWAY",
        breadth=50.0,
        breadth_change=0.0,
    )

    assert classify_state(signal) == "NEUTRAL"


def test_bear_is_rejected():
    gate = PaperV2QualityGate(0.70)

    signal = make_signal(regime="BEAR")

    decision = gate.decide(signal, quality=1.0)

    assert decision.accepted is False
    assert decision.state == "BEAR"
    assert decision.reason == "BEAR"


def test_divergent_bull_is_rejected():
    gate = PaperV2QualityGate(0.70)

    signal = make_signal(
        regime="BULL",
        breadth=45.0,
        breadth_change=-3.0,
    )

    decision = gate.decide(signal, quality=1.0)

    assert decision.accepted is False
    assert decision.state == "DIVERGENT_BULL"
    assert decision.reason == "DIVERGENT_BULL"


def test_quality_below_threshold_is_rejected():
    gate = PaperV2QualityGate(0.70)

    signal = make_signal()

    decision = gate.decide(signal, quality=0.69)

    assert decision.accepted is False
    assert decision.reason == "quality<0.70"


def test_quality_at_threshold_is_accepted():
    gate = PaperV2QualityGate(0.70)

    signal = make_signal()

    decision = gate.decide(signal, quality=0.70)

    assert decision.accepted is True
    assert decision.reason == "Q0.70_PASS"


def test_apply_ranks_against_full_universe():
    gate = PaperV2QualityGate(0.70)

    universe = [
        make_signal("AAA", score=100, relative_strength=100, adx=50, volume_ratio=2.0),
        make_signal("BBB", score=50, relative_strength=50, adx=20, volume_ratio=1.0),
        make_signal("CCC", score=25, relative_strength=25, adx=10, volume_ratio=0.8),
    ]

    signals = [universe[0]]

    accepted, rejected = gate.apply(
        signals,
        quality_universe=universe,
    )

    assert len(accepted) == 1
    assert len(rejected) == 0

    assert accepted[0]["symbol"] == "AAA"
    assert accepted[0]["paper_v2_quality"] == 1.0
    assert accepted[0]["paper_v2_state"] == "HEALTHY_BULL"
    assert accepted[0]["paper_v2_gate"] == "Q0.70_PASS"


def test_apply_missing_symbol_is_rejected():
    gate = PaperV2QualityGate(0.70)

    signal = make_signal(symbol="")

    accepted, rejected = gate.apply([signal])

    assert len(accepted) == 0
    assert len(rejected) == 1