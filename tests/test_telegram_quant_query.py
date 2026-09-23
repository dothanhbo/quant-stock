from __future__ import annotations

from services.telegram_bot.query import analyze_symbol


def _make_scan_result(*, breadth: float = 75.0):
    evaluations = [
        {
            "symbol": "FPT",
            "status": "PASSED",
            "regime": "BULL",
            "score": 90.0,
            "relative_strength_20d": 10.0,
            "adx": 30.0,
            "breadth_ema50_pct": breadth,
            "breadth_ema50_change_10d": 3.0,
        },
        {
            "symbol": "HPG",
            "status": "PASSED",
            "regime": "BULL",
            "score": 50.0,
            "relative_strength_20d": 0.0,
            "adx": 15.0,
            "breadth_ema50_pct": breadth,
            "breadth_ema50_change_10d": 3.0,
        },
    ]

    scan_stats = {
        "evaluations": evaluations,
        "total_symbols": 2,
        "fresh_count": 2,
        "market_config": {
            "regime": "BULL",
        },
        "market_state": {
            "breadth_ema50_pct": breadth,
            "breadth_ema50_change_10d": 3.0,
        },
    }

    return evaluations, scan_stats


def test_analyze_symbol_q70_full_exposure(monkeypatch):
    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: _make_scan_result(breadth=75.0),
    )

    result = analyze_symbol("FPT")

    assert result.evaluation["symbol"] == "FPT"

    assert result.q70_passed is True
    assert result.quality >= 0.70

    assert result.breadth_exposure_multiplier == 1.0
    assert result.breadth_exposure_pct == 100.0
    assert result.breadth_gate == "FULL"

    assert result.gate_state == "HEALTHY_BULL"
    assert result.gate_reason == "Q0.70_PASS"


def test_analyze_symbol_low_breadth_blocks_new_exposure(monkeypatch):
    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: _make_scan_result(breadth=35.0),
    )

    result = analyze_symbol("FPT")

    assert result.q70_passed is True
    assert result.quality >= 0.70

    assert result.breadth_exposure_multiplier == 0.0
    assert result.breadth_exposure_pct == 0.0
    assert result.breadth_gate == "BLOCK"


def test_analyze_symbol_neutral_breadth_half_exposure(monkeypatch):
    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: _make_scan_result(breadth=50.0),
    )

    result = analyze_symbol("FPT")

    assert result.q70_passed is True
    assert result.quality >= 0.70

    assert result.breadth_exposure_multiplier == 0.5
    assert result.breadth_exposure_pct == 50.0
    assert result.breadth_gate == "HALF"


def test_analyze_symbol_rejects_low_q70(monkeypatch):
    evaluations = [
        {
            "symbol": "FPT",
            "status": "PASSED",
            "regime": "BULL",
            "score": 40.0,
            "relative_strength_20d": -5.0,
            "adx": 10.0,
            "breadth_ema50_pct": 75.0,
            "breadth_ema50_change_10d": 3.0,
        },
        {
            "symbol": "HPG",
            "status": "PASSED",
            "regime": "BULL",
            "score": 50.0,
            "relative_strength_20d": 0.0,
            "adx": 15.0,
            "breadth_ema50_pct": 75.0,
            "breadth_ema50_change_10d": 3.0,
        },
        {
            "symbol": "MBB",
            "status": "PASSED",
            "score": 90.0,
            "relative_strength_20d": 10.0,
            "adx": 30.0,
            "breadth_ema50_pct": 75.0,
            "breadth_ema50_change_10d": 3.0,
        },
    ]

    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: (
            evaluations,
            {
                "evaluations": evaluations,
                "total_symbols": 3,
                "fresh_count": 3,
                "market_config": {"regime": "BULL"},
                "market_state": {
                    "breadth_ema50_pct": 75.0,
                    "breadth_ema50_change_10d": 3.0,
                },
            },
        ),
    )

    result = analyze_symbol("FPT")

    assert result.q70_passed is False
    assert result.quality < 0.70
    assert result.gate_reason.startswith("quality<")


def test_analyze_symbol_bear_rejects_even_with_high_q70(monkeypatch):
    evaluations = [
        {
            "symbol": "FPT",
            "status": "PASSED",
            "score": 90.0,
            "relative_strength_20d": 10.0,
            "adx": 30.0,
            "breadth_ema50_pct": 75.0,
            "breadth_ema50_change_10d": 3.0,
            "regime": "BEAR",
        },
        {
            "symbol": "HPG",
            "status": "PASSED",
            "regime": "BULL",
            "score": 50.0,
            "relative_strength_20d": 0.0,
            "adx": 15.0,
            "breadth_ema50_pct": 75.0,
            "breadth_ema50_change_10d": 3.0,
            "regime": "BEAR",
        },
    ]

    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: (
            evaluations,
            {
                "evaluations": evaluations,
                "total_symbols": 2,
                "fresh_count": 2,
                "market_config": {"regime": "BEAR"},
                "market_state": {
                    "breadth_ema50_pct": 75.0,
                    "breadth_ema50_change_10d": 3.0,
                },
            },
        ),
    )

    result = analyze_symbol("FPT")

    assert result.quality >= 0.70
    assert result.q70_passed is False
    assert result.gate_state == "BEAR"
    assert result.gate_reason == "BEAR"


def test_analyze_symbol_rejected_q70_has_no_breadth_application(monkeypatch):
    evaluations = [
        {
            "symbol": "FPT",
            "status": "PASSED",
            "regime": "BULL",
            "score": 100.0,
            "relative_strength_20d": 100.0,
            "adx": 100.0,
            "breadth_ema50_pct": 28.0,
            "breadth_ema50_change_10d": -5.0,
        },
        {
            "symbol": "HPG",
            "status": "PASSED",
            "regime": "BULL",
            "score": 0.0,
            "relative_strength_20d": 0.0,
            "adx": 0.0,
            "breadth_ema50_pct": 28.0,
            "breadth_ema50_change_10d": -5.0,
        },
    ]
    monkeypatch.setattr(
        "services.telegram_bot.query.scan_all_symbols",
        lambda: (
            evaluations,
            {
                "evaluations": evaluations,
                "total_symbols": 2,
                "fresh_count": 2,
                "market_config": {"regime": "BULL"},
                "market_state": {
                    "breadth_ema50_pct": 28.0,
                    "breadth_ema50_change_10d": -5.0,
                },
            },
        ),
    )

    result = analyze_symbol("HPG")

    assert result.q70_passed is False
    assert result.breadth_exposure_multiplier is None
    assert result.breadth_exposure_pct is None
    assert result.breadth_gate == "NOT_APPLICABLE"
