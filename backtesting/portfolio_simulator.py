from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from backtesting.portfolio import Portfolio
from backtesting.trade import Trade

@dataclass(slots=True)
class RejectedTrade:
    trade: Trade
    reason: str

@dataclass(slots=True)
class PortfolioSimulationResult:
    executed_trades: list[Trade]
    rejected_trades: list[RejectedTrade]
    equity_curve: pd.DataFrame
    final_cash: float
    final_market_value: float
    final_equity: float
    final_open_positions: int


class PortfolioSimulator:
    def __init__(
        self,
        initial_cash: float,
        position_size_pct: float = 20.0,
        max_positions: int = 5,
        lot_size: int = 100,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be greater than 0")

        if not 0 < position_size_pct <= 100:
            raise ValueError(
                "position_size_pct must be between 0 and 100"
            )

        if max_positions < 1:
            raise ValueError("max_positions must be at least 1")

        if lot_size < 1:
            raise ValueError("lot_size must be at least 1")

        self.portfolio = Portfolio(
            initial_cash=initial_cash,
            allow_duplicate_symbols=False,
        )

        self.position_size_pct = position_size_pct
        self.max_positions = max_positions
        self.lot_size = lot_size

    def _calculate_quantity(self, entry_price: float) -> int:
        allocated_cash = (
            self.portfolio.equity()
            * self.position_size_pct
            / 100
        )

        usable_cash = min(
            allocated_cash,
            self.portfolio.cash,
        )

        raw_quantity = int(usable_cash / entry_price)

        return (
            raw_quantity // self.lot_size
        ) * self.lot_size

    def simulate(
        self,
        candidate_trades: list[Trade],
    ) -> PortfolioSimulationResult:
        executed_trades: list[Trade] = []
        rejected_trades: list[RejectedTrade] = []
        equity_rows: list[dict] = []

        events: list[tuple[datetime, int, Trade]] = []

        for trade in candidate_trades:
            if not trade.is_closed:
                continue

            # Exit chạy trước entry nếu cùng ngày để giải phóng tiền.
            events.append((trade.exit_date, 0, trade))
            events.append((trade.entry_date, 1, trade))

        events.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2].symbol,
            )
        )

        active_trades: dict[int, Trade] = {}

        for event_date, event_type, candidate in events:
            candidate_id = id(candidate)

            # EXIT
            if event_type == 0:
                active_trade = active_trades.get(candidate_id)

                if active_trade is None:
                    continue

                closed_trade = self.portfolio.close_position(
                    symbol=active_trade.symbol,
                    exit_date=candidate.exit_date,
                    exit_price=candidate.exit_price,
                    reason=candidate.exit_reason,
                    execution=candidate.execution,
                )

                executed_trades.append(closed_trade)
                del active_trades[candidate_id]

            # ENTRY
            else:
                if len(self.portfolio.open_positions) >= self.max_positions:
                    rejected_trades.append(
                        RejectedTrade(
                            trade=candidate,
                            reason="max_positions",
                        )
                    )
                    continue

                if self.portfolio.has_open_position(candidate.symbol):
                    rejected_trades.append(
                        RejectedTrade(
                            trade=candidate,
                            reason="duplicate_symbol",
                        )
                    )
                    continue

                quantity = self._calculate_quantity(
                    candidate.entry_price
                )

                if quantity <= 0:
                    rejected_trades.append(
                        RejectedTrade(
                            trade=candidate,
                            reason="insufficient_cash",
                        )
                    )
                    continue

                opened_trade = self.portfolio.open_position(
                    symbol=candidate.symbol,
                    entry_date=candidate.entry_date,
                    entry_price=candidate.entry_price,
                    quantity=quantity,
                )

                active_trades[candidate_id] = opened_trade

            equity_rows.append(
                {
                    "date": event_date,
                    "cash": self.portfolio.cash,
                    "market_value": self.portfolio.market_value(),
                    "equity": self.portfolio.equity(),
                    "open_positions": len(
                        self.portfolio.open_positions
                    ),
                    "closed_positions": len(
                        self.portfolio.closed_positions
                    ),
                }
            )

        equity_curve = pd.DataFrame(equity_rows)

        if not equity_curve.empty:
            equity_curve = (
                equity_curve
                .sort_values("date")
                .reset_index(drop=True)
            )

            equity_curve["equity_peak"] = (
                equity_curve["equity"].cummax()
            )

            equity_curve["drawdown_pct"] = (
                equity_curve["equity"]
                / equity_curve["equity_peak"]
                - 1
            ) * 100

        return PortfolioSimulationResult(
            executed_trades=executed_trades,
            rejected_trades=rejected_trades,
            equity_curve=equity_curve,
            final_cash=self.portfolio.cash,
            final_market_value=self.portfolio.market_value(),
            final_equity=self.portfolio.equity(),
            final_open_positions=len(
                self.portfolio.open_positions
            ),
        )