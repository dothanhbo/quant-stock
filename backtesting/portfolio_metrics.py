from __future__ import annotations

import math
from typing import Any

import pandas as pd


TRADING_DAYS_PER_YEAR = 252


def calculate_max_drawdown_pct(
    equity_curve: pd.DataFrame,
) -> float:
    if (
        equity_curve.empty
        or "drawdown_pct" not in equity_curve.columns
    ):
        return 0.0

    return float(
        equity_curve["drawdown_pct"].min()
    )


def calculate_total_return_pct(
    equity_curve: pd.DataFrame,
) -> float:
    if (
        equity_curve.empty
        or "equity" not in equity_curve.columns
        or len(equity_curve) < 2
    ):
        return 0.0

    initial_equity = float(
        equity_curve["equity"].iloc[0]
    )
    final_equity = float(
        equity_curve["equity"].iloc[-1]
    )

    if initial_equity <= 0:
        return 0.0

    return (
        final_equity / initial_equity - 1
    ) * 100


def calculate_daily_returns(
    equity_curve: pd.DataFrame,
) -> pd.Series:
    if (
        equity_curve.empty
        or "equity" not in equity_curve.columns
    ):
        return pd.Series(dtype=float)

    equity = pd.to_numeric(
        equity_curve["equity"],
        errors="coerce",
    ).dropna()

    if len(equity) < 2:
        return pd.Series(dtype=float)

    return equity.pct_change().dropna()


def calculate_annualized_volatility_pct(
    daily_returns: pd.Series,
) -> float:
    if daily_returns.empty:
        return 0.0

    volatility = daily_returns.std(ddof=1)

    if pd.isna(volatility):
        return 0.0

    return float(
        volatility
        * math.sqrt(TRADING_DAYS_PER_YEAR)
        * 100
    )


def calculate_sharpe_ratio(
    daily_returns: pd.Series,
    risk_free_rate_pct: float = 0.0,
) -> float:
    if daily_returns.empty:
        return 0.0

    volatility = daily_returns.std(ddof=1)

    if pd.isna(volatility) or volatility == 0:
        return 0.0

    daily_risk_free_rate = (
        risk_free_rate_pct / 100
    ) / TRADING_DAYS_PER_YEAR

    excess_returns = (
        daily_returns - daily_risk_free_rate
    )

    return float(
        excess_returns.mean()
        / volatility
        * math.sqrt(TRADING_DAYS_PER_YEAR)
    )


def calculate_sortino_ratio(
    daily_returns: pd.Series,
    risk_free_rate_pct: float = 0.0,
) -> float:
    if daily_returns.empty:
        return 0.0

    daily_risk_free_rate = (
        risk_free_rate_pct / 100
    ) / TRADING_DAYS_PER_YEAR

    excess_returns = (
        daily_returns - daily_risk_free_rate
    )

    downside_returns = excess_returns[
        excess_returns < 0
    ]

    if downside_returns.empty:
        return 0.0

    downside_deviation = (
        downside_returns.pow(2).mean()
    ) ** 0.5

    if downside_deviation == 0:
        return 0.0

    return float(
        excess_returns.mean()
        / downside_deviation
        * math.sqrt(TRADING_DAYS_PER_YEAR)
    )


def calculate_cagr_pct(
    equity_curve: pd.DataFrame,
) -> float:
    if (
        equity_curve.empty
        or "equity" not in equity_curve.columns
        or len(equity_curve) < 2
    ):
        return 0.0

    initial_equity = float(
        equity_curve["equity"].iloc[0]
    )
    final_equity = float(
        equity_curve["equity"].iloc[-1]
    )

    if initial_equity <= 0 or final_equity <= 0:
        return 0.0

    if "date" not in equity_curve.columns:
        return 0.0

    dates = pd.to_datetime(
        equity_curve["date"],
        errors="coerce",
    ).dropna()

    if len(dates) < 2:
        return 0.0

    days = (
        dates.iloc[-1] - dates.iloc[0]
    ).days

    if days <= 0:
        return 0.0

    years = days / 365.25

    return float(
        (
            (final_equity / initial_equity)
            ** (1 / years)
            - 1
        )
        * 100
    )


def calculate_calmar_ratio(
    cagr_pct: float,
    max_drawdown_pct: float,
) -> float:
    drawdown = abs(max_drawdown_pct)

    if drawdown == 0:
        return 0.0

    return float(cagr_pct / drawdown)


def calculate_portfolio_metrics(
    equity_curve: pd.DataFrame,
    *,
    final_equity: float,
    risk_free_rate_pct: float = 0.0,
) -> dict[str, Any]:
    daily_returns = calculate_daily_returns(
        equity_curve
    )

    max_drawdown_pct = calculate_max_drawdown_pct(
        equity_curve
    )

    cagr_pct = calculate_cagr_pct(
        equity_curve
    )

    return {
        "final_equity": float(final_equity),
        "total_return_pct": (
            calculate_total_return_pct(
                equity_curve
            )
        ),
        "max_drawdown_pct": max_drawdown_pct,
        "cagr_pct": cagr_pct,
        "annualized_volatility_pct": (
            calculate_annualized_volatility_pct(
                daily_returns
            )
        ),
        "sharpe_ratio": calculate_sharpe_ratio(
            daily_returns,
            risk_free_rate_pct,
        ),
        "sortino_ratio": calculate_sortino_ratio(
            daily_returns,
            risk_free_rate_pct,
        ),
        "calmar_ratio": calculate_calmar_ratio(
            cagr_pct,
            max_drawdown_pct,
        ),
    }