import pandas as pd

from research.run_bull_entry_regime_audit import aggregate, labeled_bucket


def test_labeled_bucket_uses_fixed_interpretable_ranges():
    assert labeled_bucket(2.9, edges=[3, 6], labels=["low", "mid", "high"]) == "low"
    assert labeled_bucket(3.0, edges=[3, 6], labels=["low", "mid", "high"]) == "mid"
    assert labeled_bucket(6.0, edges=[3, 6], labels=["low", "mid", "high"]) == "high"


def test_aggregate_reports_weighted_pnl_and_unweighted_trade_metrics():
    frame = pd.DataFrame(
        {
            "bucket": ["A", "A", "B"],
            "return_pct": [2.0, -1.0, 3.0],
            "pnl": [200.0, -100.0, 300.0],
            "holding_days": [5, 10, 20],
        }
    )
    result = aggregate(frame, dimensions=["bucket"])
    a = result[result["value"] == "A"].iloc[0]
    assert a["trades"] == 2
    assert a["total_net_pnl"] == 100.0
    assert a["average_return_pct"] == 0.5
    assert a["profit_factor"] == 2.0
