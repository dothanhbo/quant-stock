from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backtesting.trade import Trade


@dataclass(slots=True, frozen=True)
class AllocationDiagnosticsResult:
    summary: dict[str, Any]
    trade_allocations: pd.DataFrame
    exposure_timeline: pd.DataFrame

    def save(
        self,
        *,
        output_dir: str | Path,
        prefix: str,
    ) -> None:
        output_path = Path(output_dir)

        output_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        pd.DataFrame(
            [self.summary]
        ).to_csv(
            output_path
            / f"{prefix}_diagnostics_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.trade_allocations.to_csv(
            output_path
            / f"{prefix}_trade_allocations.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.exposure_timeline.to_csv(
            output_path
            / f"{prefix}_exposure_timeline.csv",
            index=False,
            encoding="utf-8-sig",
        )


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(result):
        return default

    return result


def _safe_mean(
    series: pd.Series,
) -> float:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).dropna()

    if numeric.empty:
        return 0.0

    return float(
        numeric.mean()
    )


def _safe_median(
    series: pd.Series,
) -> float:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).dropna()

    if numeric.empty:
        return 0.0

    return float(
        numeric.median()
    )


def _safe_max(
    series: pd.Series,
) -> float:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).dropna()

    if numeric.empty:
        return 0.0

    return float(
        numeric.max()
    )


def _safe_min(
    series: pd.Series,
) -> float:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).dropna()

    if numeric.empty:
        return 0.0

    return float(
        numeric.min()
    )


def build_trade_allocation_table(
    trades: Iterable[Trade],
    *,
    initial_capital: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for trade_number, trade in enumerate(
        trades,
        start=1,
    ):
        entry_price = _safe_float(
            trade.entry_price
        )

        quantity = int(
            getattr(
                trade,
                "quantity",
                0,
            )
            or 0
        )

        notional_value = (
            entry_price
            * quantity
        )

        buy_commission = _safe_float(
            getattr(
                trade,
                "buy_commission",
                0.0,
            )
        )

        sell_commission = _safe_float(
            getattr(
                trade,
                "sell_commission",
                0.0,
            )
        )

        sell_tax = _safe_float(
            getattr(
                trade,
                "sell_tax",
                0.0,
            )
        )

        transaction_cost = (
            buy_commission
            + sell_commission
            + sell_tax
        )

        risk_amount = _safe_float(
            getattr(
                trade,
                "risk_amount",
                0.0,
            )
        )

        risk_pct = _safe_float(
            getattr(
                trade,
                "risk_pct",
                0.0,
            )
        )

        stop_price = getattr(
            trade,
            "stop_price",
            None,
        )

        stop_distance_pct = 0.0

        if stop_price is not None:
            numeric_stop = _safe_float(
                stop_price
            )

            if (
                entry_price > 0
                and 0 < numeric_stop < entry_price
            ):
                stop_distance_pct = (
                    (
                        entry_price
                        - numeric_stop
                    )
                    / entry_price
                    * 100
                )

        rows.append(
            {
                "trade_number": trade_number,
                "symbol": trade.symbol,
                "entry_date": trade.entry_date,
                "exit_date": trade.exit_date,
                "entry_price": entry_price,
                "quantity": quantity,
                "notional_value": (
                    notional_value
                ),
                "notional_pct_initial_capital": (
                    notional_value
                    / initial_capital
                    * 100
                    if initial_capital > 0
                    else 0.0
                ),
                "stop_price": stop_price,
                "stop_distance_pct": (
                    stop_distance_pct
                ),
                "risk_amount": risk_amount,
                "risk_pct": risk_pct,
                "net_return_pct": _safe_float(
                    getattr(
                        trade,
                        "net_return_pct",
                        0.0,
                    )
                ),
                "net_pnl": _safe_float(
                    getattr(
                        trade,
                        "net_pnl",
                        getattr(
                            trade,
                            "pnl",
                            0.0,
                        ),
                    )
                ),
                "transaction_cost": (
                    transaction_cost
                ),
                "market_regime": getattr(
                    trade,
                    "market_regime",
                    None,
                ),
                "entry_model": getattr(
                    trade,
                    "entry_model",
                    None,
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def build_exposure_timeline(
    equity_curve: pd.DataFrame,
) -> pd.DataFrame:
    if equity_curve.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "cash",
                "market_value",
                "equity",
                "cash_pct",
                "exposure_pct",
                "capital_utilization_pct",
                "open_positions",
                "portfolio_heat_pct",
            ]
        )

    required_columns = {
        "date",
        "cash",
        "market_value",
        "equity",
        "open_positions",
    }

    missing = (
        required_columns
        - set(equity_curve.columns)
    )

    if missing:
        raise ValueError(
            "equity_curve thiếu cột: "
            + ", ".join(
                sorted(missing)
            )
        )

    timeline = equity_curve.copy()

    numeric_columns = [
        "cash",
        "market_value",
        "equity",
        "open_positions",
    ]

    if (
        "portfolio_heat_pct"
        in timeline.columns
    ):
        numeric_columns.append(
            "portfolio_heat_pct"
        )
    else:
        timeline[
            "portfolio_heat_pct"
        ] = 0.0

    for column in numeric_columns:
        timeline[column] = pd.to_numeric(
            timeline[column],
            errors="coerce",
        ).fillna(0.0)

    timeline["cash_pct"] = (
        timeline["cash"]
        / timeline["equity"]
        .replace(0, pd.NA)
        * 100
    ).fillna(0.0)

    timeline["exposure_pct"] = (
        timeline["market_value"]
        / timeline["equity"]
        .replace(0, pd.NA)
        * 100
    ).fillna(0.0)

    timeline[
        "capital_utilization_pct"
    ] = timeline[
        "exposure_pct"
    ]

    columns = [
        "date",
        "cash",
        "market_value",
        "equity",
        "cash_pct",
        "exposure_pct",
        "capital_utilization_pct",
        "open_positions",
        "portfolio_heat_pct",
    ]

    if "drawdown_pct" in timeline.columns:
        columns.append(
            "drawdown_pct"
        )

    return timeline[
        columns
    ].reset_index(
        drop=True
    )


def analyze_allocation_diagnostics(
    *,
    allocator_name: str,
    trades: Iterable[Trade],
    metrics: dict[str, Any],
    equity_curve: pd.DataFrame,
    initial_capital: float,
) -> AllocationDiagnosticsResult:
    trades_list = list(
        trades
    )

    trade_allocations = (
        build_trade_allocation_table(
            trades_list,
            initial_capital=(
                initial_capital
            ),
        )
    )

    exposure_timeline = (
        build_exposure_timeline(
            equity_curve
        )
    )

    if trade_allocations.empty:
        average_notional = 0.0
        median_notional = 0.0
        maximum_notional = 0.0
        average_quantity = 0.0
        average_risk_pct = 0.0
        median_risk_pct = 0.0
        average_stop_distance = 0.0
        total_transaction_cost = 0.0
    else:
        average_notional = _safe_mean(
            trade_allocations[
                "notional_value"
            ]
        )

        median_notional = _safe_median(
            trade_allocations[
                "notional_value"
            ]
        )

        maximum_notional = _safe_max(
            trade_allocations[
                "notional_value"
            ]
        )

        average_quantity = _safe_mean(
            trade_allocations[
                "quantity"
            ]
        )

        average_risk_pct = _safe_mean(
            trade_allocations[
                "risk_pct"
            ]
        )

        median_risk_pct = _safe_median(
            trade_allocations[
                "risk_pct"
            ]
        )

        average_stop_distance = (
            _safe_mean(
                trade_allocations[
                    "stop_distance_pct"
                ]
            )
        )

        total_transaction_cost = float(
            trade_allocations[
                "transaction_cost"
            ].sum()
        )

    if exposure_timeline.empty:
        average_cash_pct = 0.0
        minimum_cash_pct = 0.0
        average_exposure_pct = 0.0
        maximum_exposure_pct = 0.0
        average_open_positions = 0.0
        maximum_open_positions = 0
        average_heat_pct = 0.0
        maximum_heat_pct = 0.0
    else:
        average_cash_pct = _safe_mean(
            exposure_timeline[
                "cash_pct"
            ]
        )

        minimum_cash_pct = _safe_min(
            exposure_timeline[
                "cash_pct"
            ]
        )

        average_exposure_pct = (
            _safe_mean(
                exposure_timeline[
                    "exposure_pct"
                ]
            )
        )

        maximum_exposure_pct = (
            _safe_max(
                exposure_timeline[
                    "exposure_pct"
                ]
            )
        )

        average_open_positions = (
            _safe_mean(
                exposure_timeline[
                    "open_positions"
                ]
            )
        )

        maximum_open_positions = int(
            _safe_max(
                exposure_timeline[
                    "open_positions"
                ]
            )
        )

        average_heat_pct = _safe_mean(
            exposure_timeline[
                "portfolio_heat_pct"
            ]
        )

        maximum_heat_pct = _safe_max(
            exposure_timeline[
                "portfolio_heat_pct"
            ]
        )

    summary: dict[str, Any] = {
        "allocator": allocator_name,
        "initial_capital": float(
            initial_capital
        ),
        "total_trades": len(
            trades_list
        ),
        "final_equity": _safe_float(
            metrics.get(
                "final_equity",
                initial_capital,
            )
        ),
        "total_return_pct": _safe_float(
            metrics.get(
                "total_return_pct"
            )
        ),
        "sharpe_ratio": _safe_float(
            metrics.get(
                "sharpe_ratio"
            )
        ),
        "max_drawdown_pct": _safe_float(
            metrics.get(
                "max_drawdown_pct"
            )
        ),
        "average_trade_notional": (
            average_notional
        ),
        "median_trade_notional": (
            median_notional
        ),
        "maximum_trade_notional": (
            maximum_notional
        ),
        "average_trade_notional_pct_initial": (
            average_notional
            / initial_capital
            * 100
            if initial_capital > 0
            else 0.0
        ),
        "average_quantity": (
            average_quantity
        ),
        "average_risk_pct": (
            average_risk_pct
        ),
        "median_risk_pct": (
            median_risk_pct
        ),
        "average_stop_distance_pct": (
            average_stop_distance
        ),
        "average_cash_pct": (
            average_cash_pct
        ),
        "minimum_cash_pct": (
            minimum_cash_pct
        ),
        "average_exposure_pct": (
            average_exposure_pct
        ),
        "maximum_exposure_pct": (
            maximum_exposure_pct
        ),
        "average_capital_utilization_pct": (
            average_exposure_pct
        ),
        "maximum_capital_utilization_pct": (
            maximum_exposure_pct
        ),
        "average_open_positions": (
            average_open_positions
        ),
        "maximum_open_positions": (
            maximum_open_positions
        ),
        "average_portfolio_heat_pct": (
            average_heat_pct
        ),
        "maximum_portfolio_heat_pct": (
            maximum_heat_pct
        ),
        "total_transaction_cost": (
            total_transaction_cost
        ),
    }

    return AllocationDiagnosticsResult(
        summary=summary,
        trade_allocations=(
            trade_allocations
        ),
        exposure_timeline=(
            exposure_timeline
        ),
    )


def print_allocation_diagnostics(
    result: AllocationDiagnosticsResult,
) -> None:
    summary = result.summary

    print()
    print("=" * 90)
    print(
        f"ALLOCATOR DIAGNOSTICS: "
        f"{summary['allocator']}"
    )
    print("=" * 90)

    print(
        f"Trades                   : "
        f"{summary['total_trades']}"
    )
    print(
        f"Return                   : "
        f"{summary['total_return_pct']:+.2f}%"
    )
    print(
        f"Sharpe                   : "
        f"{summary['sharpe_ratio']:.2f}"
    )
    print(
        f"Max Drawdown             : "
        f"{summary['max_drawdown_pct']:.2f}%"
    )

    print()
    print("CAPITAL UTILIZATION")
    print("-" * 90)

    print(
        f"Average Exposure         : "
        f"{summary['average_exposure_pct']:.2f}%"
    )
    print(
        f"Maximum Exposure         : "
        f"{summary['maximum_exposure_pct']:.2f}%"
    )
    print(
        f"Average Cash             : "
        f"{summary['average_cash_pct']:.2f}%"
    )
    print(
        f"Minimum Cash             : "
        f"{summary['minimum_cash_pct']:.2f}%"
    )
    print(
        f"Average Open Positions   : "
        f"{summary['average_open_positions']:.2f}"
    )
    print(
        f"Maximum Open Positions   : "
        f"{summary['maximum_open_positions']}"
    )

    print()
    print("POSITION SIZING")
    print("-" * 90)

    print(
        f"Average Trade Notional   : "
        f"{summary['average_trade_notional']:,.0f}"
    )
    print(
        f"Median Trade Notional    : "
        f"{summary['median_trade_notional']:,.0f}"
    )
    print(
        f"Average Notional / Start : "
        f"{summary['average_trade_notional_pct_initial']:.2f}%"
    )
    print(
        f"Average Quantity         : "
        f"{summary['average_quantity']:,.0f}"
    )
    print(
        f"Average Trade Risk       : "
        f"{summary['average_risk_pct']:.2f}%"
    )
    print(
        f"Median Trade Risk        : "
        f"{summary['median_risk_pct']:.2f}%"
    )

    print()
    print("PORTFOLIO RISK")
    print("-" * 90)

    print(
        f"Average Portfolio Heat   : "
        f"{summary['average_portfolio_heat_pct']:.2f}%"
    )
    print(
        f"Maximum Portfolio Heat   : "
        f"{summary['maximum_portfolio_heat_pct']:.2f}%"
    )
    print(
        f"Transaction Cost         : "
        f"{summary['total_transaction_cost']:,.0f}"
    )

    print("=" * 90)