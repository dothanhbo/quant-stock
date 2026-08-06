from __future__ import annotations

import argparse
from datetime import date

from dotenv import load_dotenv

from backtesting.paper_parity import (
    BacktestPaperParityConfig,
    audit_entry_parity,
    audit_exit_parity,
)
from execution.exit_models import (
    ExitBar,
    PositionExitState,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit parity giữa backtest "
            "và paper execution."
        )
    )
    parser.add_argument(
        "--sell-tax-rate",
        type=float,
        default=0.0,
        help=(
            "Thuế bán dạng decimal. "
            "Giữ 0 để khớp paper hiện tại."
        ),
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    config = (
        BacktestPaperParityConfig
        .from_env(
            sell_tax_rate=(
                args.sell_tax_rate
            )
        )
    )

    entry = audit_entry_parity(
        config=config
    )

    exit_result = audit_exit_parity(
        state=PositionExitState(
            symbol="VNM",
            entry_date=date(
                2026,
                8,
                5,
            ),
            entry_price=60_000,
            quantity=entry.quantity,
            stop_price=58_000,
            take_profit_price=64_000,
            highest_price=62_000,
            trailing_stop_price=59_000,
        ),
        bar=ExitBar(
            symbol="VNM",
            valuation_date=date(
                2026,
                8,
                6,
            ),
            open_price=61_000,
            high_price=64_500,
            low_price=60_500,
            close_price=64_000,
        ),
    )

    print(
        "\n"
        + "=" * 68
    )
    print(
        "BACKTEST ↔ PAPER PARITY AUDIT"
    )
    print(
        "=" * 68
    )
    print(
        f"Initial cash      : "
        f"{config.initial_cash:,.0f}"
    )
    print(
        f"Position sizer    : "
        f"{config.position_sizer}"
    )
    print(
        f"Commission        : "
        f"{config.commission_pct:.3f}%"
    )
    print(
        f"Slippage          : "
        f"{config.slippage_bps:.1f} bps"
    )
    print(
        f"Sell tax          : "
        f"{config.sell_tax_pct:.3f}%"
    )
    print(
        f"Quantity          : "
        f"{entry.quantity:,}"
    )
    print(
        f"Entry fill paper  : "
        f"{entry.paper_fill_price:,.2f}"
    )
    print(
        f"Entry fill test   : "
        f"{entry.backtest_fill_price:,.2f}"
    )
    print(
        f"Entry parity      : "
        f"{'PASS' if entry.passed else 'FAIL'}"
    )
    print(
        f"Exit reason       : "
        f"{exit_result.paper_decision.reason}"
    )
    print(
        f"Exit parity       : "
        f"{'PASS' if exit_result.passed else 'FAIL'}"
    )

    overall = (
        entry.passed
        and exit_result.passed
    )

    print(
        "-" * 68
    )
    print(
        "KẾT QUẢ           : "
        f"{'PASS' if overall else 'FAIL'}"
    )

    if not overall:
        raise SystemExit(
            1
        )


if __name__ == "__main__":
    main()
