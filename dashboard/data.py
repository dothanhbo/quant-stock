from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class DashboardPaths:
    market_db: Path = Path("data/market.db")
    paper_db: Path = Path("data/paper_trading.db")


@dataclass(frozen=True, slots=True)
class OverviewMetrics:
    equity: float
    cash: float
    total_return_pct: float
    total_return_value: float
    open_positions: int
    win_rate_pct: float
    max_drawdown_pct: float
    realized_pnl: float
    unrealized_pnl: float
    exposure_pct: float
    initial_cash: float


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy database: {path}")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _read_sql(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    return pd.read_sql_query(query, connection, params=params)


def load_metadata(paths: DashboardPaths) -> dict[str, str]:
    with _connect(paths.paper_db) as connection:
        if not _table_exists(connection, "paper_metadata"):
            return {}
        rows = connection.execute("SELECT key, value FROM paper_metadata").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def load_snapshots(paths: DashboardPaths) -> pd.DataFrame:
    with _connect(paths.paper_db) as connection:
        if not _table_exists(connection, "paper_portfolio_snapshots"):
            return pd.DataFrame()
        frame = _read_sql(
            connection,
            """
            SELECT id, cash, positions_value, equity, realized_pnl,
                   unrealized_pnl, gross_exposure_pct, open_positions, created_at
            FROM paper_portfolio_snapshots
            ORDER BY id
            """,
        )
    if not frame.empty:
        frame["created_at"] = pd.to_datetime(frame["created_at"], errors="coerce", utc=True)
        frame["date"] = frame["created_at"].dt.tz_convert("Asia/Ho_Chi_Minh").dt.date
    return frame


def load_positions(paths: DashboardPaths) -> pd.DataFrame:
    with _connect(paths.paper_db) as connection:
        if not _table_exists(connection, "paper_positions"):
            return pd.DataFrame()
        lifecycle_exists = _table_exists(connection, "paper_position_lifecycle")
        if lifecycle_exists:
            query = """
                SELECT p.symbol, p.quantity, p.average_price, p.market_price,
                       p.realized_pnl, l.entry_date, l.stop_price,
                       l.take_profit_price, l.highest_price,
                       l.trailing_stop_price, l.maximum_holding_days
                FROM paper_positions p
                LEFT JOIN paper_position_lifecycle l ON l.symbol = p.symbol
                ORDER BY p.symbol
            """
        else:
            query = """
                SELECT symbol, quantity, average_price, market_price, realized_pnl,
                       NULL AS entry_date, NULL AS stop_price,
                       NULL AS take_profit_price, NULL AS highest_price,
                       NULL AS trailing_stop_price, NULL AS maximum_holding_days
                FROM paper_positions
                ORDER BY symbol
            """
        frame = _read_sql(connection, query)

    if frame.empty:
        return frame

    frame["market_value"] = frame["quantity"] * frame["market_price"]
    frame["cost_value"] = frame["quantity"] * frame["average_price"]
    frame["unrealized_pnl"] = frame["market_value"] - frame["cost_value"]
    frame["unrealized_pnl_pct"] = frame["unrealized_pnl"].div(frame["cost_value"]).mul(100).fillna(0.0)
    frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce").dt.date
    today = date.today()
    frame["days_held"] = frame["entry_date"].apply(
        lambda value: max((today - value).days, 0) if pd.notna(value) else None
    )
    return frame


def load_fills(paths: DashboardPaths, limit: int | None = None) -> pd.DataFrame:
    with _connect(paths.paper_db) as connection:
        if not _table_exists(connection, "paper_fills"):
            return pd.DataFrame()
        suffix = " LIMIT ?" if limit is not None else ""
        params: tuple[Any, ...] = (limit,) if limit is not None else ()
        frame = _read_sql(
            connection,
            """
            SELECT id, order_id, symbol, side, quantity, price, gross_value,
                   commission, slippage_cost, net_cash_flow, created_at
            FROM paper_fills
            ORDER BY id DESC
            """ + suffix,
            params,
        )
    if not frame.empty:
        frame["created_at"] = pd.to_datetime(frame["created_at"], errors="coerce", utc=True)
    return frame


def load_closed_trades(paths: DashboardPaths) -> pd.DataFrame:
    with _connect(paths.paper_db) as connection:
        if not _table_exists(connection, "paper_closed_trades"):
            return pd.DataFrame()
        frame = _read_sql(
            connection,
            """
            SELECT id, symbol, entry_date, exit_date, quantity, entry_price,
                   exit_price, gross_proceeds, commission, realized_pnl,
                   return_pct, holding_days, exit_reason, order_id, created_at
            FROM paper_closed_trades
            ORDER BY id DESC
            """,
        )
    if not frame.empty:
        frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce").dt.date
        frame["exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce").dt.date
    return frame


def load_market_health(paths: DashboardPaths) -> pd.DataFrame:
    with _connect(paths.market_db) as connection:
        if not _table_exists(connection, "prices"):
            return pd.DataFrame()
        frame = _read_sql(
            connection,
            """
            SELECT symbol, COUNT(*) AS row_count,
                   MIN(substr(time, 1, 10)) AS first_date,
                   MAX(substr(time, 1, 10)) AS latest_date
            FROM prices
            GROUP BY symbol
            ORDER BY symbol
            """,
        )
    return frame


def load_latest_signals(paths: DashboardPaths, limit: int = 20) -> pd.DataFrame:
    with _connect(paths.market_db) as connection:
        if not _table_exists(connection, "signals"):
            return pd.DataFrame()
        frame = _read_sql(
            connection,
            """
            SELECT signal_date, symbol, score, entry, stop_loss, take_profit,
                   rsi, adx, volume_ratio, relative_strength, status, result,
                   holding_days
            FROM signals
            ORDER BY signal_date DESC, score DESC
            LIMIT ?
            """,
            (limit,),
        )
    return frame


def load_vnindex(paths: DashboardPaths) -> pd.DataFrame:
    with _connect(paths.market_db) as connection:
        if not _table_exists(connection, "prices"):
            return pd.DataFrame()
        frame = _read_sql(
            connection,
            """
            SELECT substr(time, 1, 10) AS date, close
            FROM prices
            WHERE symbol IN ('VNINDEX', 'VN-INDEX')
            ORDER BY time
            """,
        )
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return frame


def compute_overview(paths: DashboardPaths) -> OverviewMetrics:
    metadata = load_metadata(paths)
    snapshots = load_snapshots(paths)
    positions = load_positions(paths)
    closed = load_closed_trades(paths)

    initial_cash = float(metadata.get("initial_cash", 100_000_000.0))
    if snapshots.empty:
        cash = float(metadata.get("cash", initial_cash))
        positions_value = float(positions["market_value"].sum()) if not positions.empty else 0.0
        equity = cash + positions_value
        realized = float(metadata.get("realized_pnl", 0.0))
        unrealized = float(positions["unrealized_pnl"].sum()) if not positions.empty else 0.0
        exposure = positions_value / equity * 100 if equity else 0.0
        open_positions = len(positions)
    else:
        latest = snapshots.iloc[-1]
        cash = float(latest["cash"])
        equity = float(latest["equity"])
        realized = float(latest["realized_pnl"])
        unrealized = float(latest["unrealized_pnl"])
        exposure = float(latest["gross_exposure_pct"])
        open_positions = int(latest["open_positions"])

    total_return_value = equity - initial_cash
    total_return_pct = total_return_value / initial_cash * 100 if initial_cash else 0.0
    if closed.empty:
        win_rate = 0.0
    else:
        win_rate = float((closed["realized_pnl"] > 0).mean() * 100)

    if snapshots.empty or len(snapshots) < 2:
        max_drawdown = 0.0
    else:
        running_max = snapshots["equity"].cummax()
        drawdown = snapshots["equity"].div(running_max).sub(1.0).mul(100)
        max_drawdown = float(drawdown.min())

    return OverviewMetrics(
        equity=equity,
        cash=cash,
        total_return_pct=total_return_pct,
        total_return_value=total_return_value,
        open_positions=open_positions,
        win_rate_pct=win_rate,
        max_drawdown_pct=max_drawdown,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        exposure_pct=exposure,
        initial_cash=initial_cash,
    )


def compute_performance(paths: DashboardPaths) -> dict[str, float]:
    closed = load_closed_trades(paths)
    if closed.empty:
        return {
            "total_trades": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
        }

    wins = closed.loc[closed["realized_pnl"] > 0]
    losses = closed.loc[closed["realized_pnl"] < 0]
    gross_profit = float(wins["realized_pnl"].sum())
    gross_loss = abs(float(losses["realized_pnl"].sum()))
    return {
        "total_trades": float(len(closed)),
        "win_rate": float(len(wins) / len(closed) * 100),
        "profit_factor": gross_profit / gross_loss if gross_loss else (gross_profit > 0) * 999.0,
        "expectancy": float(closed["return_pct"].mean()),
        "avg_win": float(wins["return_pct"].mean()) if not wins.empty else 0.0,
        "avg_loss": float(losses["return_pct"].mean()) if not losses.empty else 0.0,
    }


def load_historical_baseline(
    path: Path = Path("research_results/regime_policy/summary.csv"),
) -> dict[str, float | str]:
    """Load the frozen/adaptive research baseline used for forward comparison."""
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if frame.empty:
        return {}
    if "policy" in frame.columns and (frame["policy"] == "adaptive_default").any():
        row = frame.loc[frame["policy"] == "adaptive_default"].iloc[0]
    else:
        row = frame.iloc[0]
    return {
        "source": str(path),
        "policy": str(row.get("policy", "baseline")),
        "total_trades": float(row.get("total_trades", 0.0)),
        "win_rate": float(row.get("win_rate_pct", 0.0)),
        "profit_factor": float(row.get("profit_factor", 0.0)),
        "expectancy": float(row.get("expectancy_pct", 0.0)),
        "max_drawdown": float(row.get("max_drawdown_pct", 0.0)),
        "sharpe": float(row.get("sharpe_ratio", 0.0)),
        "total_return": float(row.get("total_return_pct", 0.0)),
    }


def compute_forward_validation(paths: DashboardPaths) -> dict[str, object]:
    """Compare live paper observations with the frozen research baseline and VNINDEX."""
    overview = compute_overview(paths)
    performance = compute_performance(paths)
    snapshots = load_snapshots(paths)
    vnindex = load_vnindex(paths)
    baseline = load_historical_baseline()

    benchmark_return = 0.0
    strategy_return = overview.total_return_pct
    comparison = pd.DataFrame()
    if not snapshots.empty:
        daily = (
            snapshots.sort_values("created_at")
            .dropna(subset=["date", "equity"])
            .drop_duplicates("date", keep="last")[["date", "equity"]]
        )
        if not daily.empty:
            base_equity = float(daily.iloc[0]["equity"])
            daily["Paper"] = daily["equity"].div(base_equity).sub(1.0).mul(100) if base_equity else 0.0
            comparison = daily[["date", "Paper"]].copy()
            if not vnindex.empty:
                bench = (
                    vnindex.dropna(subset=["date", "close"])
                    .drop_duplicates("date", keep="last")
                    .copy()
                )
                start_date = daily.iloc[0]["date"]
                end_date = daily.iloc[-1]["date"]
                bench = bench[(bench["date"] >= start_date) & (bench["date"] <= end_date)]
                if not bench.empty:
                    first_close = float(bench.iloc[0]["close"])
                    bench["VNINDEX"] = bench["close"].div(first_close).sub(1.0).mul(100) if first_close else 0.0
                    benchmark_return = float(bench.iloc[-1]["VNINDEX"])
                    comparison = comparison.merge(bench[["date", "VNINDEX"]], on="date", how="left")
                    comparison["VNINDEX"] = comparison["VNINDEX"].ffill().fillna(0.0)

    return {
        "paper": {
            **performance,
            "total_return": strategy_return,
            "max_drawdown": overview.max_drawdown_pct,
        },
        "historical": baseline,
        "benchmark_return": benchmark_return,
        "alpha_vs_vnindex": strategy_return - benchmark_return,
        "comparison": comparison,
        "sample_warning": int(performance["total_trades"]) < 30,
    }
