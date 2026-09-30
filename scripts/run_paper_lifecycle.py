from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv
from config.paper_store import resolve_active_paper_store
from core.paths import resolve_market_database_path
from core.market_data_integrity import require_market_data_integrity
from quantctl.runtime_configuration import resolve_runtime_configuration
from quantlab.evidence import (
    capture_prospective_portfolio_evidence,
    PaperEventCursor,
)

from execution.exit_engine import (
    ExitEngine,
    ExitEngineConfig,
)
from execution.lifecycle_manager import (
    PaperLifecycleManager,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from execution.persistence import PaperTradingStore
from execution.risk_guard import (
    RiskGuard,
    RiskLimits,
)
from execution.signal_executor import (
    PaperExecutionBatchResult,
    PaperSignalExecutor,
)
from config.trading_policy import TradingPolicy


def env_float(
    name: str,
    default: float,
) -> float:
    return float(
        os.getenv(
            name,
            str(default),
        )
    )


def env_int(
    name: str,
    default: int,
) -> int:
    return int(
        os.getenv(
            name,
            str(default),
        )
    )


def main() -> PaperExecutionBatchResult | None:
    load_dotenv()

    market_database_path = resolve_market_database_path()
    require_market_data_integrity(database_path=market_database_path)
    active_store = resolve_active_paper_store()
    with sqlite3.connect(market_database_path) as connection:
        latest_value = connection.execute(
            "SELECT MAX(date(time)) FROM prices WHERE UPPER(TRIM(symbol)) = 'VNINDEX'"
        ).fetchone()[0]
    if latest_value is None:
        raise RuntimeError("canonical market database has no VNINDEX session")
    # Keep the original pre-lifecycle cursor in the paper account.  If the
    # separate evidence ledger is unavailable after this lifecycle commits, a
    # retry still captures this run's events instead of treating them as
    # pre-activation history.  This metadata never participates in order,
    # fill, position, cash, or risk decisions.
    baseline = PaperTradingStore(
        active_store.database_path
    ).get_or_create_prospective_evidence_baseline(str(latest_value))
    pre_lifecycle_event_cursor = PaperEventCursor(*baseline)
    pending_result = None
    if latest_value:
        pending_result = PaperSignalExecutor.from_env().execute_pending_signals(
            valuation_date=str(latest_value),
            market_database_path=market_database_path,
        )
        if pending_result.executions:
            print(
                f"Pending next-open: {pending_result.filled_count} filled, "
                f"{pending_result.skipped_count} skipped, "
                f"{pending_result.rejected_count} rejected."
            )

    policy = TradingPolicy.from_env()

    broker = PaperBroker(
        initial_cash=env_float(
            "PAPER_INITIAL_CASH",
            100_000_000,
        ),
        commission_rate=env_float(
            "PAPER_COMMISSION_RATE",
            0.0015,
        ),
        slippage_bps=env_float(
            "PAPER_SLIPPAGE_BPS",
            5.0,
        ),
        sell_tax_rate=policy.sell_tax_rate,
        database_path=active_store.database_path,
        restore_state=True,
    )

    order_manager = OrderManager(
        broker=broker,
        risk_guard=RiskGuard(
            RiskLimits(
                maximum_position_pct=env_float(
                    "PAPER_MAX_POSITION_PCT",
                    20.0,
                ),
                maximum_gross_exposure_pct=env_float(
                    "PAPER_MAX_EXPOSURE_PCT",
                    80.0,
                ),
                maximum_open_positions=env_int(
                    "PAPER_MAX_OPEN_POSITIONS",
                    5,
                ),
                maximum_daily_loss_pct=env_float(
                    "PAPER_MAX_DAILY_LOSS_PCT",
                    3.0,
                ),
                minimum_cash_buffer_pct=env_float(
                    "PAPER_MIN_CASH_BUFFER_PCT",
                    5.0,
                ),
            )
        ),
    )

    v2_disable_trailing = (
        os.getenv("PAPER_V2_DISABLE_TRAILING", "").lower()
        == "true"
    )

    manager = PaperLifecycleManager(
        broker=broker,
        order_manager=order_manager,
        exit_engine=ExitEngine(
            ExitEngineConfig(
                enable_trailing_stop=not v2_disable_trailing,
            )
        ),
        market_database_path=market_database_path,
        default_trailing_atr_multiplier=(
            None
            if v2_disable_trailing
            else policy.trailing_atr_multiplier
        ),
    )

    # Pending fills already use the latest completed VNINDEX session.  Give
    # the lifecycle that exact session too, so exits, portfolio marks, and the
    # post-commit evidence observation share one causal market-date anchor.
    result = manager.run(valuation_date=str(latest_value))

    runtime_configuration = resolve_runtime_configuration()
    if (
        runtime_configuration.strategy_identity
        != active_store.strategy_identity
        or runtime_configuration.paper_store_id
        != active_store.store_id
    ):
        raise RuntimeError(
            "Effective runtime configuration does not match the active "
            "paper store used by lifecycle."
        )
    evidence = capture_prospective_portfolio_evidence(
        observation_date=result.valuation_date,
        paper_database_path=active_store.database_path,
        source_store_id=active_store.store_id,
        strategy_identity=active_store.strategy_identity,
        runtime_configuration_fingerprint=runtime_configuration.fingerprint,
        market_database_path=market_database_path,
        baseline_event_cursor=pre_lifecycle_event_cursor,
        lifecycle_warnings=(
            *(f"MISSING_PRICE:{symbol}" for symbol in result.missing_prices),
            *(f"MISSING_LIFECYCLE_STATE:{symbol}" for symbol in result.missing_states),
            *(f"REJECTED_EXIT:{symbol}" for symbol in result.rejected_exits),
        ),
    )
    print(
        "Prospective evidence: "
        f"{'created' if evidence.created else 'existing'} "
        f"for {evidence.record.observation_date} "
        f"({evidence.record.record_identity[:12]})."
    )

    print("\n" + "=" * 64)
    print("PAPER POSITION LIFECYCLE")
    print("=" * 64)
    print(
        f"Ngày xử lý: {result.valuation_date}"
    )
    print(
        f"Giữ vị thế: {len(result.held)}"
    )
    print(
        f"Đã thoát: {len(result.exited)}"
    )

    for item in result.held:
        print(
            "\n"
            f"🟡 {item.symbol} | HOLD | "
            f"PnL {item.unrealized_pnl:+,.0f} đ "
            f"({item.unrealized_pnl_pct:+.2f}%) | "
            f"Stop {item.effective_stop_price:,.0f}"
        )

    for item in result.exited:
        print(
            "\n"
            f"🔴 {item.symbol} | EXIT "
            f"{item.reason} | "
            f"{item.quantity:,} cổ | "
            f"Fill {item.fill_price:,.0f} đ | "
            f"PnL {item.realized_pnl:+,.0f} đ "
            f"({item.return_pct:+.2f}%)"
        )

    if result.missing_states:
        print(
            "\n⚠️ Chưa có lifecycle state: "
            + ", ".join(
                result.missing_states
            )
        )

    if result.missing_prices:
        print(
            "\n⚠️ Thiếu OHLC: "
            + ", ".join(
                result.missing_prices
            )
        )

    if result.rejected_exits:
        print(
            "\n❌ SELL bị từ chối: "
            + ", ".join(
                result.rejected_exits
            )
        )

    print("\n" + "-" * 64)
    print(
        f"Cash: {result.cash:,.0f} đ"
    )
    print(
        f"Equity: {result.equity:,.0f} đ"
    )
    print(
        "Realized PnL: "
        f"{result.realized_pnl:+,.0f} đ"
    )
    print(
        "Unrealized PnL: "
        f"{result.unrealized_pnl:+,.0f} đ"
    )
    print(
        "Open positions: "
        f"{result.open_positions}"
    )


    print("\nℹ️ Lifecycle chỉ ghi log terminal; "
          "báo cáo danh mục được gửi sau scanner.")

    return pending_result



if __name__ == "__main__":
    main()
