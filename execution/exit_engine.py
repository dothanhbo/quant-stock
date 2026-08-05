from __future__ import annotations

from dataclasses import dataclass

from execution.exit_models import (
    ExitBar,
    ExitDecision,
    ExitReason,
    PositionExitState,
    SameBarExitPolicy,
)


@dataclass(frozen=True, slots=True)
class ExitEngineConfig:
    same_bar_policy: SameBarExitPolicy = (
        SameBarExitPolicy.CONSERVATIVE_STOP
    )
    enable_stop_loss: bool = True
    enable_take_profit: bool = True
    enable_trailing_stop: bool = True
    enable_time_exit: bool = True


class ExitEngine:
    """
    Pure exit-decision engine.

    It does not submit SELL orders, mutate broker positions,
    persist SQLite state, or send Telegram messages.
    """

    def __init__(
        self,
        config: ExitEngineConfig | None = None,
    ) -> None:
        self.config = (
            config
            if config is not None
            else ExitEngineConfig()
        )

    def evaluate(
        self,
        *,
        state: PositionExitState,
        bar: ExitBar,
        exit_signal: bool = False,
    ) -> ExitDecision:
        self._validate_symbols(
            state=state,
            bar=bar,
        )

        if (
            bar.valuation_date
            < state.entry_date
        ):
            raise ValueError(
                "valuation_date không được trước "
                "entry_date."
            )

        holding_days = (
            bar.valuation_date
            - state.entry_date
        ).days

        highest_price = max(
            state.current_highest_price,
            bar.high_price,
        )

        # The trailing stop carried from the previous session is the
        # only trailing level allowed to trigger on the current bar.
        # A new level calculated from today's high becomes active on
        # the next session, avoiding same-bar look-ahead bias.
        active_trailing_stop = (
            state.trailing_stop_price
            if self.config.enable_trailing_stop
            else None
        )

        active_stop_price = (
            self._calculate_effective_stop(
                state=state,
                trailing_stop_price=(
                    active_trailing_stop
                ),
            )
        )

        stop_reason = (
            ExitReason.TRAILING_STOP
            if (
                active_trailing_stop
                is not None
                and active_trailing_stop
                >= state.stop_price
                and active_stop_price
                == active_trailing_stop
            )
            else ExitReason.STOP_LOSS
        )

        if (
            self.config.enable_stop_loss
            and bar.open_price
            <= active_stop_price
        ):
            return self._exit(
                reason=stop_reason,
                trigger_price=active_stop_price,
                execution_price=bar.open_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Giá mở cửa thấp hơn hoặc bằng "
                    "mức dừng lỗ."
                ),
            )

        if (
            self.config.enable_take_profit
            and state.take_profit_price
            is not None
            and bar.open_price
            >= state.take_profit_price
        ):
            return self._exit(
                reason=ExitReason.TAKE_PROFIT,
                trigger_price=(
                    state.take_profit_price
                ),
                execution_price=bar.open_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Giá mở cửa cao hơn hoặc bằng "
                    "mục tiêu lợi nhuận."
                ),
            )

        stop_touched = (
            self.config.enable_stop_loss
            and bar.low_price
            <= active_stop_price
        )

        target_touched = (
            self.config.enable_take_profit
            and state.take_profit_price
            is not None
            and bar.high_price
            >= state.take_profit_price
        )

        if stop_touched and target_touched:
            if (
                self.config.same_bar_policy
                == SameBarExitPolicy
                .OPTIMISTIC_TARGET
            ):
                return self._exit(
                    reason=ExitReason.TAKE_PROFIT,
                    trigger_price=(
                        state.take_profit_price
                    ),
                    execution_price=(
                        state.take_profit_price
                    ),
                    bar=bar,
                    highest_price=highest_price,
                    effective_stop_price=(
                        active_stop_price
                    ),
                    trailing_stop_price=(
                        active_trailing_stop
                    ),
                    holding_days=holding_days,
                    details=(
                        "Cùng một nến chạm cả stop "
                        "và target; áp dụng chính sách "
                        "ưu tiên target."
                    ),
                )

            return self._exit(
                reason=stop_reason,
                trigger_price=active_stop_price,
                execution_price=active_stop_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Cùng một nến chạm cả stop "
                    "và target; áp dụng chính sách "
                    "thận trọng ưu tiên stop."
                ),
            )

        if stop_touched:
            return self._exit(
                reason=stop_reason,
                trigger_price=active_stop_price,
                execution_price=active_stop_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Giá thấp nhất trong phiên "
                    "chạm mức dừng lỗ."
                ),
            )

        if target_touched:
            return self._exit(
                reason=ExitReason.TAKE_PROFIT,
                trigger_price=(
                    state.take_profit_price
                ),
                execution_price=(
                    state.take_profit_price
                ),
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Giá cao nhất trong phiên "
                    "chạm mục tiêu lợi nhuận."
                ),
            )

        if (
            self.config.enable_time_exit
            and state.maximum_holding_days
            is not None
            and holding_days
            >= state.maximum_holding_days
        ):
            return self._exit(
                reason=ExitReason.TIME_EXIT,
                trigger_price=bar.close_price,
                execution_price=bar.close_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Đã đạt số ngày nắm giữ tối đa."
                ),
            )

        if exit_signal:
            return self._exit(
                reason=ExitReason.EXIT_SIGNAL,
                trigger_price=bar.close_price,
                execution_price=bar.close_price,
                bar=bar,
                highest_price=highest_price,
                effective_stop_price=(
                    active_stop_price
                ),
                trailing_stop_price=(
                    active_trailing_stop
                ),
                holding_days=holding_days,
                details=(
                    "Chiến lược phát tín hiệu thoát."
                ),
            )

        next_trailing_stop = (
            self._calculate_trailing_stop(
                state=state,
                bar=bar,
                highest_price=highest_price,
            )
        )

        next_effective_stop = (
            self._calculate_effective_stop(
                state=state,
                trailing_stop_price=(
                    next_trailing_stop
                ),
            )
        )

        return ExitDecision.hold(
            valuation_date=bar.valuation_date,
            highest_price=highest_price,
            effective_stop_price=(
                next_effective_stop
            ),
            trailing_stop_price=(
                next_trailing_stop
            ),
            holding_days=holding_days,
            details=(
                "Chưa có điều kiện thoát vị thế."
            ),
        )

    def _calculate_trailing_stop(
        self,
        *,
        state: PositionExitState,
        bar: ExitBar,
        highest_price: float,
    ) -> float | None:
        existing_stop = (
            state.trailing_stop_price
        )

        if (
            not self.config
            .enable_trailing_stop
        ):
            return existing_stop

        multiplier = (
            state.trailing_atr_multiplier
        )

        if (
            multiplier is None
            or bar.atr is None
        ):
            return existing_stop

        candidate = (
            highest_price
            - multiplier * bar.atr
        )

        candidate = max(
            candidate,
            state.stop_price,
        )

        if existing_stop is None:
            return candidate

        # A trailing stop can only move upward.
        return max(
            existing_stop,
            candidate,
        )

    @staticmethod
    def _calculate_effective_stop(
        *,
        state: PositionExitState,
        trailing_stop_price: float | None,
    ) -> float:
        if trailing_stop_price is None:
            return state.stop_price

        return max(
            state.stop_price,
            trailing_stop_price,
        )

    @staticmethod
    def _validate_symbols(
        *,
        state: PositionExitState,
        bar: ExitBar,
    ) -> None:
        if state.symbol != bar.symbol:
            raise ValueError(
                "Symbol của state và bar "
                "không khớp."
            )

    @staticmethod
    def _exit(
        *,
        reason: ExitReason,
        trigger_price: float,
        execution_price: float,
        bar: ExitBar,
        highest_price: float,
        effective_stop_price: float,
        trailing_stop_price: float | None,
        holding_days: int,
        details: str,
    ) -> ExitDecision:
        return ExitDecision(
            should_exit=True,
            reason=reason,
            trigger_price=trigger_price,
            execution_price=execution_price,
            valuation_date=(
                bar.valuation_date
            ),
            highest_price=highest_price,
            effective_stop_price=(
                effective_stop_price
            ),
            trailing_stop_price=(
                trailing_stop_price
            ),
            holding_days=holding_days,
            details=details,
        )
