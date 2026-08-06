from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import median

import pandas as pd


TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class PaperPerformanceReport:
    initial_equity: float
    current_equity: float
    total_return_value: float
    total_return_pct: float

    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float

    net_realized_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    expectancy_amount: float
    expectancy_pct: float
    average_win_amount: float
    average_loss_amount: float
    average_win_pct: float
    average_loss_pct: float
    payoff_ratio: float
    largest_win_amount: float
    largest_loss_amount: float

    average_holding_days: float
    median_holding_days: float
    minimum_holding_days: int
    maximum_holding_days: int

    max_drawdown_pct: float
    cagr_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float

    first_snapshot_date: date | None
    latest_snapshot_date: date | None
    snapshot_days: int

    def to_dict(
        self,
    ) -> dict[str, object]:
        result = asdict(
            self
        )

        for key in (
            "first_snapshot_date",
            "latest_snapshot_date",
        ):
            value = result[key]

            if value is not None:
                result[key] = value.isoformat()

        return result

    def to_json(
        self,
        *,
        indent: int = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
        )


def _table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (
            table_name,
        ),
    ).fetchone()

    return row is not None


def load_initial_equity(
    database_path: str | Path,
    *,
    fallback: float = 100_000_000.0,
) -> float:
    path = Path(
        database_path
    )

    with sqlite3.connect(
        path
    ) as connection:
        if not _table_exists(
            connection,
            "paper_metadata",
        ):
            return fallback

        row = connection.execute(
            """
            SELECT value
            FROM paper_metadata
            WHERE key = 'initial_cash'
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return fallback

    try:
        value = json.loads(
            row[0]
        )
        numeric_value = float(
            value
        )
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return fallback

    return (
        numeric_value
        if numeric_value > 0
        else fallback
    )


def load_closed_trades_frame(
    database_path: str | Path,
) -> pd.DataFrame:
    path = Path(
        database_path
    )

    columns = [
        "symbol",
        "entry_date",
        "exit_date",
        "quantity",
        "entry_price",
        "exit_price",
        "gross_proceeds",
        "commission",
        "realized_pnl",
        "return_pct",
        "holding_days",
        "exit_reason",
        "order_id",
        "created_at",
    ]

    with sqlite3.connect(
        path
    ) as connection:
        if not _table_exists(
            connection,
            "paper_closed_trades",
        ):
            return pd.DataFrame(
                columns=columns
            )

        frame = pd.read_sql_query(
            """
            SELECT
                symbol,
                entry_date,
                exit_date,
                quantity,
                entry_price,
                exit_price,
                gross_proceeds,
                commission,
                realized_pnl,
                return_pct,
                holding_days,
                exit_reason,
                order_id,
                created_at
            FROM paper_closed_trades
            ORDER BY exit_date, id
            """,
            connection,
        )

    for column in (
        "entry_date",
        "exit_date",
        "created_at",
    ):
        frame[column] = pd.to_datetime(
            frame[column],
            errors="coerce",
        )

    numeric_columns = [
        "quantity",
        "entry_price",
        "exit_price",
        "gross_proceeds",
        "commission",
        "realized_pnl",
        "return_pct",
        "holding_days",
    ]

    for column in numeric_columns:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    return frame


def load_daily_equity_curve(
    database_path: str | Path,
    *,
    initial_equity: float,
) -> pd.DataFrame:
    path = Path(
        database_path
    )

    columns = [
        "date",
        "cash",
        "positions_value",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "gross_exposure_pct",
        "open_positions",
        "peak_equity",
        "drawdown_pct",
        "daily_return",
    ]

    with sqlite3.connect(
        path
    ) as connection:
        if not _table_exists(
            connection,
            "paper_portfolio_snapshots",
        ):
            return pd.DataFrame(
                columns=columns
            )

        frame = pd.read_sql_query(
            """
            SELECT
                id,
                cash,
                positions_value,
                equity,
                realized_pnl,
                unrealized_pnl,
                gross_exposure_pct,
                open_positions,
                created_at
            FROM paper_portfolio_snapshots
            ORDER BY created_at, id
            """,
            connection,
        )

    if frame.empty:
        return pd.DataFrame(
            columns=columns
        )

    frame["created_at"] = pd.to_datetime(
        frame["created_at"],
        errors="coerce",
        utc=True,
    )
    frame = frame.dropna(
        subset=[
            "created_at",
            "equity",
        ]
    )

    if frame.empty:
        return pd.DataFrame(
            columns=columns
        )

    frame["date"] = (
        frame["created_at"]
        .dt.tz_convert(
            "Asia/Ho_Chi_Minh"
        )
        .dt.date
    )

    numeric_columns = [
        "cash",
        "positions_value",
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "gross_exposure_pct",
        "open_positions",
    ]

    for column in numeric_columns:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    # Keep only the final persisted state for each calendar day.
    frame = (
        frame.sort_values(
            [
                "created_at",
                "id",
            ]
        )
        .groupby(
            "date",
            as_index=False,
        )
        .tail(
            1
        )
        .sort_values(
            "date"
        )
        .reset_index(
            drop=True
        )
    )

    if (
        not frame.empty
        and initial_equity > 0
    ):
        first_date = frame[
            "date"
        ].iloc[0]

        baseline = pd.DataFrame(
            [
                {
                    "id": 0,
                    "cash": initial_equity,
                    "positions_value": 0.0,
                    "equity": initial_equity,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "gross_exposure_pct": 0.0,
                    "open_positions": 0,
                    "created_at": pd.NaT,
                    "date": first_date,
                }
            ]
        )

        if not math.isclose(
            float(
                frame["equity"].iloc[0]
            ),
            initial_equity,
            rel_tol=0.0,
            abs_tol=0.01,
        ):
            frame = pd.concat(
                [
                    baseline,
                    frame,
                ],
                ignore_index=True,
            )

    frame["peak_equity"] = (
        frame["equity"]
        .cummax()
    )

    frame["drawdown_pct"] = (
        (
            frame["equity"]
            / frame["peak_equity"]
            - 1
        )
        * 100
    )

    frame["daily_return"] = (
        frame["equity"]
        .pct_change()
    )

    return frame[
        columns
    ]


def _safe_mean(
    series: pd.Series,
) -> float:
    if series.empty:
        return 0.0

    value = series.mean()

    if pd.isna(
        value
    ):
        return 0.0

    return float(
        value
    )


def _annualized_volatility_pct(
    returns: pd.Series,
) -> float:
    returns = returns.dropna()

    if len(returns) < 2:
        return 0.0

    volatility = returns.std(
        ddof=1
    )

    if pd.isna(
        volatility
    ):
        return 0.0

    return float(
        volatility
        * math.sqrt(
            TRADING_DAYS_PER_YEAR
        )
        * 100
    )


def _sharpe_ratio(
    returns: pd.Series,
    *,
    risk_free_rate_pct: float,
) -> float:
    returns = returns.dropna()

    if len(returns) < 2:
        return 0.0

    volatility = returns.std(
        ddof=1
    )

    if (
        pd.isna(
            volatility
        )
        or volatility == 0
    ):
        return 0.0

    daily_risk_free_rate = (
        risk_free_rate_pct
        / 100
        / TRADING_DAYS_PER_YEAR
    )

    excess_returns = (
        returns
        - daily_risk_free_rate
    )

    return float(
        excess_returns.mean()
        / volatility
        * math.sqrt(
            TRADING_DAYS_PER_YEAR
        )
    )


def _sortino_ratio(
    returns: pd.Series,
    *,
    risk_free_rate_pct: float,
) -> float:
    returns = returns.dropna()

    if len(returns) < 2:
        return 0.0

    daily_risk_free_rate = (
        risk_free_rate_pct
        / 100
        / TRADING_DAYS_PER_YEAR
    )

    excess_returns = (
        returns
        - daily_risk_free_rate
    )

    downside = excess_returns[
        excess_returns < 0
    ]

    if downside.empty:
        return 0.0

    downside_deviation = math.sqrt(
        float(
            downside.pow(
                2
            ).mean()
        )
    )

    if downside_deviation == 0:
        return 0.0

    return float(
        excess_returns.mean()
        / downside_deviation
        * math.sqrt(
            TRADING_DAYS_PER_YEAR
        )
    )


def _cagr_pct(
    *,
    initial_equity: float,
    current_equity: float,
    first_date: date | None,
    latest_date: date | None,
) -> float:
    if (
        initial_equity <= 0
        or current_equity <= 0
        or first_date is None
        or latest_date is None
    ):
        return 0.0

    days = (
        latest_date
        - first_date
    ).days

    if days <= 0:
        return 0.0

    years = days / 365.25

    return float(
        (
            (
                current_equity
                / initial_equity
            )
            ** (
                1 / years
            )
            - 1
        )
        * 100
    )


def calculate_paper_performance(
    database_path: str | Path,
    *,
    risk_free_rate_pct: float = 0.0,
    fallback_initial_equity: float = (
        100_000_000.0
    ),
) -> PaperPerformanceReport:
    path = Path(
        database_path
    )

    if not path.exists():
        raise FileNotFoundError(
            "Không tìm thấy paper database: "
            f"{path}"
        )

    initial_equity = load_initial_equity(
        path,
        fallback=(
            fallback_initial_equity
        ),
    )

    trades = load_closed_trades_frame(
        path
    )

    equity_curve = load_daily_equity_curve(
        path,
        initial_equity=initial_equity,
    )

    if equity_curve.empty:
        current_equity = initial_equity
        first_snapshot_date = None
        latest_snapshot_date = None
        snapshot_days = 0
        max_drawdown_pct = 0.0
        daily_returns = pd.Series(
            dtype=float
        )
    else:
        current_equity = float(
            equity_curve[
                "equity"
            ].iloc[-1]
        )
        first_snapshot_date = (
            equity_curve[
                "date"
            ].iloc[0]
        )
        latest_snapshot_date = (
            equity_curve[
                "date"
            ].iloc[-1]
        )
        snapshot_days = int(
            equity_curve[
                "date"
            ].nunique()
        )
        max_drawdown_pct = float(
            equity_curve[
                "drawdown_pct"
            ].min()
        )
        daily_returns = (
            equity_curve[
                "daily_return"
            ]
            .dropna()
        )

    total_return_value = (
        current_equity
        - initial_equity
    )

    total_return_pct = (
        total_return_value
        / initial_equity
        * 100
        if initial_equity > 0
        else 0.0
    )

    if trades.empty:
        pnl = pd.Series(
            dtype=float
        )
        returns = pd.Series(
            dtype=float
        )
        holding_days = pd.Series(
            dtype=float
        )
    else:
        pnl = (
            trades[
                "realized_pnl"
            ]
            .dropna()
            .astype(
                float
            )
        )
        returns = (
            trades[
                "return_pct"
            ]
            .dropna()
            .astype(
                float
            )
        )
        holding_days = (
            trades[
                "holding_days"
            ]
            .dropna()
            .astype(
                int
            )
        )

    winning_pnl = pnl[
        pnl > 0
    ]
    losing_pnl = pnl[
        pnl < 0
    ]
    breakeven_pnl = pnl[
        pnl == 0
    ]

    winning_returns = returns[
        returns > 0
    ]
    losing_returns = returns[
        returns < 0
    ]

    total_trades = int(
        len(
            pnl
        )
    )
    winning_trades = int(
        len(
            winning_pnl
        )
    )
    losing_trades = int(
        len(
            losing_pnl
        )
    )
    breakeven_trades = int(
        len(
            breakeven_pnl
        )
    )

    win_rate_pct = (
        winning_trades
        / total_trades
        * 100
        if total_trades > 0
        else 0.0
    )

    gross_profit = float(
        winning_pnl.sum()
    )
    gross_loss = float(
        abs(
            losing_pnl.sum()
        )
    )
    net_realized_pnl = float(
        pnl.sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    average_win_amount = _safe_mean(
        winning_pnl
    )
    average_loss_amount = _safe_mean(
        losing_pnl
    )
    average_win_pct = _safe_mean(
        winning_returns
    )
    average_loss_pct = _safe_mean(
        losing_returns
    )

    payoff_ratio = (
        average_win_amount
        / abs(
            average_loss_amount
        )
        if average_loss_amount < 0
        else 0.0
    )

    largest_win_amount = (
        float(
            winning_pnl.max()
        )
        if not winning_pnl.empty
        else 0.0
    )
    largest_loss_amount = (
        float(
            losing_pnl.min()
        )
        if not losing_pnl.empty
        else 0.0
    )

    average_holding_days = _safe_mean(
        holding_days
    )

    if holding_days.empty:
        median_holding_days = 0.0
        minimum_holding_days = 0
        maximum_holding_days = 0
    else:
        median_holding_days = float(
            median(
                holding_days.tolist()
            )
        )
        minimum_holding_days = int(
            holding_days.min()
        )
        maximum_holding_days = int(
            holding_days.max()
        )

    cagr_pct = _cagr_pct(
        initial_equity=initial_equity,
        current_equity=current_equity,
        first_date=first_snapshot_date,
        latest_date=latest_snapshot_date,
    )

    calmar_ratio = (
        cagr_pct
        / abs(
            max_drawdown_pct
        )
        if max_drawdown_pct < 0
        else 0.0
    )

    return PaperPerformanceReport(
        initial_equity=initial_equity,
        current_equity=current_equity,
        total_return_value=(
            total_return_value
        ),
        total_return_pct=(
            total_return_pct
        ),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=(
            breakeven_trades
        ),
        win_rate_pct=win_rate_pct,
        net_realized_pnl=(
            net_realized_pnl
        ),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        expectancy_amount=(
            _safe_mean(
                pnl
            )
        ),
        expectancy_pct=(
            _safe_mean(
                returns
            )
        ),
        average_win_amount=(
            average_win_amount
        ),
        average_loss_amount=(
            average_loss_amount
        ),
        average_win_pct=(
            average_win_pct
        ),
        average_loss_pct=(
            average_loss_pct
        ),
        payoff_ratio=payoff_ratio,
        largest_win_amount=(
            largest_win_amount
        ),
        largest_loss_amount=(
            largest_loss_amount
        ),
        average_holding_days=(
            average_holding_days
        ),
        median_holding_days=(
            median_holding_days
        ),
        minimum_holding_days=(
            minimum_holding_days
        ),
        maximum_holding_days=(
            maximum_holding_days
        ),
        max_drawdown_pct=(
            max_drawdown_pct
        ),
        cagr_pct=cagr_pct,
        annualized_volatility_pct=(
            _annualized_volatility_pct(
                daily_returns
            )
        ),
        sharpe_ratio=(
            _sharpe_ratio(
                daily_returns,
                risk_free_rate_pct=(
                    risk_free_rate_pct
                ),
            )
        ),
        sortino_ratio=(
            _sortino_ratio(
                daily_returns,
                risk_free_rate_pct=(
                    risk_free_rate_pct
                ),
            )
        ),
        calmar_ratio=calmar_ratio,
        first_snapshot_date=(
            first_snapshot_date
        ),
        latest_snapshot_date=(
            latest_snapshot_date
        ),
        snapshot_days=snapshot_days,
    )
