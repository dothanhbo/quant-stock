from pathlib import Path

from execution.signal_executor import (
    PaperExecutionConfig,
    PaperSignalExecutor,
)


DATABASE_PATH = Path(
    "position_sizer_test.db"
)


def main() -> None:
    for path in (
        DATABASE_PATH,
        Path(f"{DATABASE_PATH}-wal"),
        Path(f"{DATABASE_PATH}-shm"),
    ):
        path.unlink(
            missing_ok=True
        )

    config = PaperExecutionConfig(
        enabled=True,
        database_path=DATABASE_PATH,
        initial_cash=100_000_000,
        position_sizer="atr_risk",
        risk_per_trade_pct=1.0,
        atr_stop_multiplier=2.0,
        maximum_position_pct=20.0,
        maximum_orders_per_scan=3,
        lot_size=100,
    )

    executor = PaperSignalExecutor(
        config
    )

    result = executor.execute_signals(
        [
            {
                "symbol": "HPG",
                "date": "2026-08-05",
                "entry": 25.0,
                "stop_loss": 23.4,
                "atr": 0.8,
                "score": 80,
                "adx": 28,
                "volume_ratio": 1.4,
                "relative_strength_20d": 5.0,
                "regime": "SIDEWAY",
            },
            {
                "symbol": "FPT",
                "date": "2026-08-05",
                "entry": 120.0,
                "stop_loss": 115.0,
                "atr": 2.5,
                "score": 78,
                "adx": 25,
                "volume_ratio": 1.2,
                "relative_strength_20d": 4.0,
                "regime": "SIDEWAY",
            },
        ]
    )

    print(result)

    for execution in result.executions:
        print(execution)

    assert (
        result.position_sizer
        == "atr_risk"
    )
    assert (
        result.executions[0].quantity
        == 600
    )
    assert (
        result.executions[1].quantity
        == 100
    )
    assert (
        result.executions[0]
        .estimated_risk_pct
        > 0
    )

    print()
    print(
        "✅ Shared PositionSizer test passed."
    )


if __name__ == "__main__":
    main()
