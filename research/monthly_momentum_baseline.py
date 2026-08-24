from __future__ import annotations

import math
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class MonthlyMomentumConfig:
    top_n: int = 5
    gross_exposure_pct: float = 80.0
    minimum_history_rows: int = 252
    minimum_adtv20: float = 10_000_000_000.0
    maximum_volatility63_pct: float = 60.0
    lot_size: int = 100
    commission_pct: float = 0.15
    sell_tax_pct: float = 0.10
    slippage_pct: float = 0.05
    entry_rank_max: int | None = None
    hold_rank_max: int | None = None
    regime_exposure_enabled: bool = False
    bull_exposure_pct: float = 80.0
    sideway_exposure_pct: float = 40.0
    bear_exposure_pct: float = 0.0
    daily_exit_enabled: bool = False
    daily_exit_ema200_enabled: bool = False
    daily_exit_momentum_enabled: bool = False
    rebalance_tolerance_pct: float = 0.0
    breadth_exposure_enabled: bool = False
    breadth_review_frequency: str = "MONTHLY"
    breadth_risk_on_pct: float = 60.0
    breadth_neutral_pct: float = 40.0
    breadth_recovery_confirmation_periods: int = 1
    breadth_recovery_frequency: str = "REVIEW"
    execution_delay_sessions: int = 0

    def validate(self) -> None:
        if self.top_n < 1 or self.minimum_history_rows < 126:
            raise ValueError("top_n/history không hợp lệ.")
        if not 0 < self.gross_exposure_pct <= 100:
            raise ValueError("gross_exposure_pct phải nằm trong (0, 100].")
        if self.minimum_adtv20 < 0 or self.maximum_volatility63_pct <= 0:
            raise ValueError("Liquidity/volatility limits không hợp lệ.")
        if self.lot_size < 1:
            raise ValueError("lot_size phải từ 1 trở lên.")
        entry_rank = self.entry_rank_max or self.top_n
        hold_rank = self.hold_rank_max or entry_rank
        if entry_rank < self.top_n or hold_rank < entry_rank:
            raise ValueError("Rank entry/hold phải thỏa top_n <= entry <= hold.")
        exposures = (
            self.bull_exposure_pct,
            self.sideway_exposure_pct,
            self.bear_exposure_pct,
        )
        if any(value < 0 or value > 100 for value in exposures):
            raise ValueError("Regime exposure phải nằm trong [0, 100].")
        if self.rebalance_tolerance_pct < 0 or self.rebalance_tolerance_pct > 100:
            raise ValueError("rebalance_tolerance_pct phải nằm trong [0, 100].")
        if self.breadth_review_frequency not in {"MONTHLY", "WEEKLY", "DAILY"}:
            raise ValueError("breadth_review_frequency không hợp lệ.")
        if self.breadth_recovery_confirmation_periods < 1:
            raise ValueError("breadth_recovery_confirmation_periods phải từ 1 trở lên.")
        if self.breadth_recovery_frequency not in {"REVIEW", "MONTHLY"}:
            raise ValueError("breadth_recovery_frequency không hợp lệ.")
        if self.execution_delay_sessions < 0:
            raise ValueError("execution_delay_sessions không được âm.")
        if not (
            0 <= self.breadth_neutral_pct
            <= self.breadth_risk_on_pct
            <= 100
        ):
            raise ValueError("Breadth thresholds phải thỏa 0 <= neutral <= risk-on <= 100.")

    @property
    def effective_entry_rank(self) -> int:
        return self.entry_rank_max or self.top_n

    @property
    def effective_hold_rank(self) -> int:
        return self.hold_rank_max or self.effective_entry_rank


def prepare_symbol_features(
    prices: pd.DataFrame,
    *,
    price_scale: float = 1.0,
) -> pd.DataFrame:
    required = {"time", "open", "close", "volume"}
    if prices.empty or not required.issubset(prices.columns):
        return pd.DataFrame()
    if not math.isfinite(price_scale) or price_scale <= 0:
        raise ValueError("price_scale phải lớn hơn 0.")
    data = prices.copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce").dt.normalize()
    for column in ("open", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in ("open", "close"):
        data[column] = data[column] * price_scale
    data = (
        data.dropna(subset=["time", "open", "close", "volume"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )
    close = data["close"]
    daily_return = close.pct_change()
    data["history_rows"] = np.arange(1, len(data) + 1)
    data["ema50"] = close.ewm(span=50, adjust=False).mean()
    data["ema200"] = close.ewm(span=200, adjust=False).mean()
    data["momentum_6_1_pct"] = (close.shift(21) / close.shift(126) - 1.0) * 100.0
    data["adtv20"] = (close * data["volume"]).rolling(20, min_periods=20).mean()
    data["volatility63_pct"] = (
        daily_return.rolling(63, min_periods=63).std(ddof=1)
        * math.sqrt(252)
        * 100.0
    )
    return data


def monthly_signal_execution_dates(
    calendar: pd.DatetimeIndex,
    *,
    start_date,
    end_date,
    execution_delay_sessions: int = 0,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if execution_delay_sessions < 0:
        raise ValueError("execution_delay_sessions không được âm.")
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().unique().sort_values()
    dates = dates[(dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))]
    if len(dates) < 2:
        return []
    series = pd.Series(dates, index=dates)
    signals = series.groupby(dates.to_period("M")).last().tolist()
    pairs = []
    for signal in signals:
        later = dates[dates > signal]
        if len(later) > execution_delay_sessions:
            pairs.append((
                pd.Timestamp(signal),
                pd.Timestamp(later[execution_delay_sessions]),
            ))
    return pairs


def build_universe_snapshot(
    feature_cache: dict[str, pd.DataFrame],
    *,
    signal_date,
    config: MonthlyMomentumConfig,
    current_symbols: set[str] | None = None,
) -> pd.DataFrame:
    rows = []
    signal = pd.Timestamp(signal_date)
    for symbol, features in feature_cache.items():
        eligible_rows = features[features["time"] <= signal]
        if eligible_rows.empty:
            continue
        latest = eligible_rows.iloc[-1]
        stale_days = int((signal - pd.Timestamp(latest["time"])).days)
        row = {
            "signal_date": signal.date(),
            "symbol": symbol,
            "data_date": pd.Timestamp(latest["time"]).date(),
            "stale_days": stale_days,
            "history_rows": int(latest["history_rows"]),
            "close": float(latest["close"]),
            "ema200": float(latest["ema200"]),
            "momentum_6_1_pct": float(latest["momentum_6_1_pct"]),
            "adtv20": float(latest["adtv20"]),
            "volatility63_pct": float(latest["volatility63_pct"]),
        }
        finite_features = all(
            math.isfinite(row[name])
            for name in ("momentum_6_1_pct", "adtv20", "volatility63_pct")
        )
        row["eligible"] = bool(
            stale_days <= 7
            and row["history_rows"] >= config.minimum_history_rows
            and finite_features
            and row["close"] > row["ema200"]
            and row["momentum_6_1_pct"] > 0
            and row["adtv20"] >= config.minimum_adtv20
            and row["volatility63_pct"] <= config.maximum_volatility63_pct
        )
        rows.append(row)
    snapshot = pd.DataFrame(rows)
    if snapshot.empty:
        return snapshot
    snapshot["momentum_rank"] = np.nan
    eligible = snapshot[snapshot["eligible"]].sort_values(
        ["momentum_6_1_pct", "adtv20", "symbol"],
        ascending=[False, False, True],
    )
    snapshot.loc[eligible.index, "momentum_rank"] = range(1, len(eligible) + 1)
    current = current_symbols or set()
    retained = snapshot[
        snapshot["symbol"].isin(current)
        & snapshot["eligible"]
        & snapshot["momentum_rank"].le(config.effective_hold_rank)
    ].sort_values("momentum_rank")
    selected_symbols = retained["symbol"].tolist()[:config.top_n]
    entries = snapshot[
        snapshot["eligible"]
        & snapshot["momentum_rank"].le(config.effective_entry_rank)
        & ~snapshot["symbol"].isin(selected_symbols)
    ].sort_values("momentum_rank")
    slots = max(0, config.top_n - len(selected_symbols))
    selected_symbols.extend(entries["symbol"].tolist()[:slots])
    snapshot["selected"] = snapshot["symbol"].isin(selected_symbols)
    snapshot["selection_reason"] = ""
    snapshot.loc[
        snapshot["selected"] & snapshot["symbol"].isin(current),
        "selection_reason",
    ] = "HOLD_BUFFER"
    snapshot.loc[
        snapshot["selected"] & ~snapshot["symbol"].isin(current),
        "selection_reason",
    ] = "NEW_ENTRY"
    return snapshot.sort_values(["selected", "momentum_rank", "symbol"], ascending=[False, True, True])


def prepare_market_regime(benchmark: pd.DataFrame) -> pd.DataFrame:
    required = {"time", "close"}
    if benchmark.empty or not required.issubset(benchmark.columns):
        return pd.DataFrame(columns=["time", "close", "ema50", "ema200", "regime"])
    market = benchmark[["time", "close"]].copy()
    market["time"] = pd.to_datetime(market["time"], errors="coerce").dt.normalize()
    market["close"] = pd.to_numeric(market["close"], errors="coerce")
    market = (
        market.dropna(subset=["time", "close"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )
    market["ema50"] = market["close"].ewm(span=50, adjust=False).mean()
    market["ema200"] = market["close"].ewm(span=200, adjust=False).mean()
    bull = (market["close"] > market["ema200"]) & (market["ema50"] > market["ema200"])
    sideway = (market["close"] > market["ema200"]) | (market["ema50"] > market["ema200"])
    market["regime"] = np.select([bull, sideway], ["BULL", "SIDEWAY"], default="BEAR")
    return market


def regime_exposure_pct(
    regime: str,
    config: MonthlyMomentumConfig,
) -> float:
    if not config.regime_exposure_enabled:
        return config.gross_exposure_pct
    return {
        "BULL": config.bull_exposure_pct,
        "SIDEWAY": config.sideway_exposure_pct,
        "BEAR": config.bear_exposure_pct,
    }.get(regime, config.bear_exposure_pct)


def prepare_market_breadth(
    feature_cache: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    frames = []
    for symbol, features in feature_cache.items():
        required = {"time", "close", "ema50", "history_rows"}
        if features.empty or not required.issubset(features.columns):
            continue
        frame = features[["time", "close", "ema50", "history_rows"]].copy()
        frame["symbol"] = symbol
        frames.append(frame)
    if not frames:
        return pd.DataFrame(
            columns=["time", "breadth_above_ema50_pct", "breadth_symbols"]
        )
    data = pd.concat(frames, ignore_index=True)
    data["time"] = pd.to_datetime(data["time"], errors="coerce").dt.normalize()
    data = data[
        data["time"].notna()
        & data["close"].notna()
        & data["ema50"].notna()
        & (data["history_rows"] >= 50)
    ].copy()
    data["above_ema50"] = data["close"] > data["ema50"]
    breadth = data.groupby("time").agg(
        breadth_symbols=("symbol", "nunique"),
        breadth_above_ema50=("above_ema50", "sum"),
    ).reset_index()
    breadth["breadth_above_ema50_pct"] = (
        breadth["breadth_above_ema50"]
        / breadth["breadth_symbols"]
        * 100.0
    )
    return breadth


def breadth_exposure_pct(
    *,
    market_regime: str,
    breadth_pct: float,
    config: MonthlyMomentumConfig,
) -> float:
    if not config.breadth_exposure_enabled:
        return regime_exposure_pct(market_regime, config)
    if (
        not math.isfinite(breadth_pct)
        or market_regime == "BEAR"
        or breadth_pct < config.breadth_neutral_pct
    ):
        return config.bear_exposure_pct
    if (
        market_regime == "BULL"
        and breadth_pct >= config.breadth_risk_on_pct
    ):
        return config.bull_exposure_pct
    return config.sideway_exposure_pct


def apply_breadth_review_policy(
    *,
    active_exposure_pct: float,
    proposed_exposure_pct: float,
    recovery_confirmation_periods: int,
    recovery_allowed: bool,
    pending_recovery_exposure_pct: float | None,
    pending_recovery_periods: int,
) -> tuple[float, float | None, int]:
    """Resolve one causal breadth review with asymmetric hysteresis.

    Risk reductions are always accepted immediately. Exposure increases can be
    blocked until the monthly rebalance or confirmed over consecutive reviews.
    When proposed recovery levels differ, only the lowest level observed during
    the confirmation window is approved.
    """
    if recovery_confirmation_periods < 1:
        raise ValueError("recovery_confirmation_periods phải từ 1 trở lên.")
    if proposed_exposure_pct < active_exposure_pct:
        return proposed_exposure_pct, None, 0
    if math.isclose(
        proposed_exposure_pct,
        active_exposure_pct,
        abs_tol=1e-9,
    ):
        return active_exposure_pct, None, 0
    if not recovery_allowed:
        return active_exposure_pct, None, 0
    if recovery_confirmation_periods == 1:
        return proposed_exposure_pct, None, 0

    if (
        pending_recovery_exposure_pct is None
        or pending_recovery_periods <= 0
    ):
        candidate_exposure = proposed_exposure_pct
        confirmation_count = 1
    else:
        candidate_exposure = min(
            pending_recovery_exposure_pct,
            proposed_exposure_pct,
        )
        confirmation_count = pending_recovery_periods + 1

    if confirmation_count >= recovery_confirmation_periods:
        return candidate_exposure, None, 0
    return active_exposure_pct, candidate_exposure, confirmation_count


def _trade_rates(config: MonthlyMomentumConfig) -> tuple[float, float, float]:
    return (
        config.commission_pct / 100.0,
        config.sell_tax_pct / 100.0,
        config.slippage_pct / 100.0,
    )


def simulate_monthly_momentum(
    *,
    feature_cache: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    start_date,
    end_date,
    initial_capital: float,
    config: MonthlyMomentumConfig,
) -> dict[str, pd.DataFrame | float | int]:
    config.validate()
    if initial_capital <= 0:
        raise ValueError("initial_capital phải lớn hơn 0.")
    market = prepare_market_regime(benchmark)
    breadth = prepare_market_breadth(feature_cache)
    breadth_native = breadth.set_index("time", drop=False).sort_index()
    calendar = pd.DatetimeIndex(market["time"])
    calendar = calendar[(calendar >= pd.Timestamp(start_date)) & (calendar <= pd.Timestamp(end_date))]
    pairs = monthly_signal_execution_dates(
        calendar,
        start_date=start_date,
        end_date=end_date,
        execution_delay_sessions=config.execution_delay_sessions,
    )
    execution_map = {execution: signal for signal, execution in pairs}
    next_session = {
        pd.Timestamp(calendar[index]): pd.Timestamp(calendar[index + 1])
        for index in range(len(calendar) - 1)
    }
    delayed_execution_session = {
        pd.Timestamp(calendar[index]): pd.Timestamp(
            calendar[index + 1 + config.execution_delay_sessions]
        )
        for index in range(
            len(calendar) - 1 - config.execution_delay_sessions
        )
    }

    native = {
        symbol: frame.set_index("time", drop=False).sort_index()
        for symbol, frame in feature_cache.items() if not frame.empty
    }
    cash = float(initial_capital)
    holdings: dict[str, int] = {}
    last_close: dict[str, float] = {}
    last_close_date: dict[str, pd.Timestamp] = {}
    equity_rows = []
    order_rows = []
    snapshot_frames = []
    stale_rows = []
    pending_exits: dict[
        str,
        tuple[pd.Timestamp, pd.Timestamp, str],
    ] = {}
    commission_rate, tax_rate, slippage_rate = _trade_rates(config)
    total_traded_notional = 0.0
    total_cost = 0.0
    active_regime = "BEAR"
    active_exposure_pct = 0.0
    active_breadth_pct = math.nan
    active_selected: list[str] = []
    pending_breadth_rebalance: tuple[
        pd.Timestamp, pd.Timestamp, float, str, float
    ] | None = None
    pending_recovery_exposure_pct: float | None = None
    pending_recovery_periods = 0

    def price(symbol: str, date: pd.Timestamp, column: str):
        frame = native.get(symbol)
        if frame is None or date not in frame.index:
            return None
        value = frame.loc[date, column]
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None

    def breadth_on(date: pd.Timestamp) -> float:
        if date not in breadth_native.index:
            return math.nan
        value = breadth_native.loc[date, "breadth_above_ema50_pct"]
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        return float(value)

    def execute_exposure_rebalance(
        *,
        signal_date: pd.Timestamp,
        execution_date: pd.Timestamp,
        selected: list[str],
        target_exposure_pct: float,
        reason: str,
    ) -> None:
        nonlocal cash, total_traded_notional, total_cost
        open_values = {}
        for symbol in set(holdings) | set(selected):
            value = price(symbol, execution_date, "open")
            if value is not None:
                open_values[symbol] = value
        equity_open = cash + sum(
            quantity * open_values.get(symbol, last_close.get(symbol, 0.0))
            for symbol, quantity in holdings.items()
        )
        target_value = (
            equity_open * target_exposure_pct / 100.0 / len(selected)
            if selected else 0.0
        )
        desired = {}
        for symbol in selected:
            raw_open = open_values.get(symbol)
            if raw_open is None:
                desired[symbol] = holdings.get(symbol, 0)
                continue
            desired[symbol] = int(
                target_value // (raw_open * config.lot_size)
            ) * config.lot_size
            current_quantity = holdings.get(symbol, 0)
            if current_quantity > 0 and target_value > 0:
                deviation_pct = abs(
                    current_quantity * raw_open / target_value - 1.0
                ) * 100.0
                if deviation_pct <= config.rebalance_tolerance_pct:
                    desired[symbol] = current_quantity

        for symbol, current_quantity in list(holdings.items()):
            sell_quantity = max(0, current_quantity - desired.get(symbol, 0))
            raw_open = open_values.get(symbol)
            if sell_quantity <= 0 or raw_open is None:
                continue
            fill = raw_open * (1.0 - slippage_rate)
            notional = fill * sell_quantity
            cost = notional * (commission_rate + tax_rate)
            cash += notional - cost
            total_traded_notional += notional
            total_cost += cost + raw_open * sell_quantity * slippage_rate
            holdings[symbol] = current_quantity - sell_quantity
            if holdings[symbol] == 0:
                del holdings[symbol]
                pending_exits.pop(symbol, None)
            order_rows.append({
                "signal_date": signal_date.date(),
                "execution_date": execution_date.date(),
                "symbol": symbol,
                "side": "SELL",
                "quantity": sell_quantity,
                "reference_open": raw_open,
                "fill_price": fill,
                "notional": notional,
                "fees_tax": cost,
                "reason": reason,
            })

        for symbol in selected:
            buy_quantity = max(0, desired[symbol] - holdings.get(symbol, 0))
            raw_open = open_values.get(symbol)
            if buy_quantity <= 0 or raw_open is None:
                continue
            fill = raw_open * (1.0 + slippage_rate)
            unit_cash = fill * (1.0 + commission_rate)
            affordable = int(
                cash // (unit_cash * config.lot_size)
            ) * config.lot_size
            buy_quantity = min(buy_quantity, affordable)
            if buy_quantity <= 0:
                continue
            notional = fill * buy_quantity
            commission = notional * commission_rate
            cash -= notional + commission
            total_traded_notional += notional
            total_cost += commission + raw_open * buy_quantity * slippage_rate
            holdings[symbol] = holdings.get(symbol, 0) + buy_quantity
            order_rows.append({
                "signal_date": signal_date.date(),
                "execution_date": execution_date.date(),
                "symbol": symbol,
                "side": "BUY",
                "quantity": buy_quantity,
                "reference_open": raw_open,
                "fill_price": fill,
                "notional": notional,
                "fees_tax": commission,
                "reason": reason,
            })

    for date in calendar:
        date = pd.Timestamp(date)
        monthly_breadth_override: tuple[
            pd.Timestamp, pd.Timestamp, float, str, float
        ] | None = None
        for symbol, (
            exit_signal_date,
            exit_execution_date,
            reason,
        ) in list(pending_exits.items()):
            quantity = holdings.get(symbol, 0)
            if date < exit_execution_date:
                continue
            raw_open = price(symbol, date, "open")
            if quantity <= 0:
                del pending_exits[symbol]
                continue
            if raw_open is None:
                continue
            fill = raw_open * (1.0 - slippage_rate)
            notional = fill * quantity
            cost = notional * (commission_rate + tax_rate)
            cash += notional - cost
            total_traded_notional += notional
            total_cost += cost + raw_open * quantity * slippage_rate
            del holdings[symbol]
            del pending_exits[symbol]
            active_selected = [
                active_symbol
                for active_symbol in active_selected
                if active_symbol != symbol
            ]
            order_rows.append({
                "signal_date": exit_signal_date.date(),
                "execution_date": date.date(),
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "reference_open": raw_open,
                "fill_price": fill,
                "notional": notional,
                "fees_tax": cost,
                "reason": reason,
            })

        if pending_breadth_rebalance is not None:
            (
                breadth_signal_date,
                breadth_execution_date,
                breadth_target_exposure,
                breadth_regime,
                breadth_value,
            ) = pending_breadth_rebalance
            if date < breadth_execution_date:
                pass
            elif date not in execution_map:
                execute_exposure_rebalance(
                    signal_date=breadth_signal_date,
                    execution_date=date,
                    selected=active_selected,
                    target_exposure_pct=breadth_target_exposure,
                    reason="BREADTH_EXPOSURE_REBALANCE",
                )
                active_regime = breadth_regime
                active_exposure_pct = breadth_target_exposure
                active_breadth_pct = breadth_value
                pending_recovery_exposure_pct = None
                pending_recovery_periods = 0
            else:
                monthly_breadth_override = pending_breadth_rebalance
            if date >= breadth_execution_date:
                pending_breadth_rebalance = None

        if date in execution_map:
            signal_date = execution_map[date]
            regime_row = market.loc[market["time"] <= signal_date].iloc[-1]
            active_regime = str(regime_row["regime"])
            active_breadth_pct = breadth_on(signal_date)
            monthly_proposed_exposure = breadth_exposure_pct(
                market_regime=active_regime,
                breadth_pct=active_breadth_pct,
                config=config,
            )
            confirm_recovery_on_reviews = bool(
                config.breadth_exposure_enabled
                and config.breadth_review_frequency != "MONTHLY"
                and config.breadth_recovery_frequency == "REVIEW"
                and config.breadth_recovery_confirmation_periods > 1
            )
            if monthly_breadth_override is not None:
                active_exposure_pct = monthly_breadth_override[2]
                pending_recovery_exposure_pct = None
                pending_recovery_periods = 0
            elif (
                confirm_recovery_on_reviews
                and monthly_proposed_exposure > active_exposure_pct
            ):
                # A monthly rebalance is not an extra breadth observation. Keep
                # the current risk until consecutive weekly reviews confirm it.
                pass
            else:
                active_exposure_pct = monthly_proposed_exposure
                pending_recovery_exposure_pct = None
                pending_recovery_periods = 0
            snapshot = build_universe_snapshot(
                feature_cache,
                signal_date=signal_date,
                config=config,
                current_symbols=set(holdings),
            )
            if not snapshot.empty:
                snapshot.insert(1, "execution_date", date.date())
                snapshot.insert(2, "market_regime", active_regime)
                snapshot.insert(3, "target_gross_exposure_pct", active_exposure_pct)
                snapshot.insert(4, "breadth_above_ema50_pct", active_breadth_pct)
                snapshot_frames.append(snapshot)
            selected = (
                snapshot.loc[snapshot["selected"], "symbol"].tolist()
                if not snapshot.empty else []
            )
            active_selected = list(selected)
            open_values = {}
            for symbol in set(holdings) | set(selected):
                value = price(symbol, date, "open")
                if value is not None:
                    open_values[symbol] = value
            equity_open = cash + sum(
                quantity * open_values.get(symbol, last_close.get(symbol, 0.0))
                for symbol, quantity in holdings.items()
            )
            target_value = (
                equity_open * active_exposure_pct / 100.0 / len(selected)
                if selected else 0.0
            )
            desired = {}
            for symbol in selected:
                raw_open = open_values.get(symbol)
                if raw_open is None:
                    desired[symbol] = holdings.get(symbol, 0)
                else:
                    desired[symbol] = int(
                        target_value // (raw_open * config.lot_size)
                    ) * config.lot_size
                    current_quantity = holdings.get(symbol, 0)
                    if current_quantity > 0 and target_value > 0:
                        current_value = current_quantity * raw_open
                        deviation_pct = abs(
                            current_value / target_value - 1.0
                        ) * 100.0
                        if deviation_pct <= config.rebalance_tolerance_pct:
                            desired[symbol] = current_quantity

            for symbol, current_quantity in list(holdings.items()):
                sell_quantity = max(0, current_quantity - desired.get(symbol, 0))
                raw_open = open_values.get(symbol)
                if sell_quantity <= 0 or raw_open is None:
                    continue
                fill = raw_open * (1.0 - slippage_rate)
                notional = fill * sell_quantity
                cost = notional * (commission_rate + tax_rate)
                cash += notional - cost
                total_traded_notional += notional
                total_cost += cost + raw_open * sell_quantity * slippage_rate
                holdings[symbol] = current_quantity - sell_quantity
                if holdings[symbol] == 0:
                    del holdings[symbol]
                    pending_exits.pop(symbol, None)
                order_rows.append({
                    "signal_date": signal_date.date(), "execution_date": date.date(),
                    "symbol": symbol, "side": "SELL", "quantity": sell_quantity,
                    "reference_open": raw_open, "fill_price": fill,
                    "notional": notional, "fees_tax": cost, "reason": "MONTHLY_REBALANCE",
                })

            for symbol in selected:
                buy_quantity = max(0, desired[symbol] - holdings.get(symbol, 0))
                raw_open = open_values.get(symbol)
                if buy_quantity <= 0 or raw_open is None:
                    continue
                fill = raw_open * (1.0 + slippage_rate)
                unit_cash = fill * (1.0 + commission_rate)
                affordable = int(cash // (unit_cash * config.lot_size)) * config.lot_size
                buy_quantity = min(buy_quantity, affordable)
                if buy_quantity <= 0:
                    continue
                notional = fill * buy_quantity
                commission = notional * commission_rate
                cash -= notional + commission
                total_traded_notional += notional
                total_cost += commission + raw_open * buy_quantity * slippage_rate
                holdings[symbol] = holdings.get(symbol, 0) + buy_quantity
                order_rows.append({
                    "signal_date": signal_date.date(), "execution_date": date.date(),
                    "symbol": symbol, "side": "BUY", "quantity": buy_quantity,
                    "reference_open": raw_open, "fill_price": fill,
                    "notional": notional, "fees_tax": commission, "reason": "MONTHLY_REBALANCE",
                })

        market_value = 0.0
        for symbol, quantity in holdings.items():
            close_value = price(symbol, date, "close")
            if close_value is None:
                close_value = last_close.get(symbol)
                stale_rows.append({
                    "valuation_date": date.date(),
                    "symbol": symbol,
                    "quantity": quantity,
                    "last_price_date": (
                        last_close_date[symbol].date()
                        if symbol in last_close_date else None
                    ),
                    "carried_close": close_value,
                })
            if close_value is not None:
                last_close[symbol] = close_value
                if price(symbol, date, "close") is not None:
                    last_close_date[symbol] = date
                market_value += quantity * close_value
        close_breadth_pct = breadth_on(date)
        equity_rows.append({
            "date": date.date(), "cash": cash, "market_value": market_value,
            "equity": cash + market_value, "positions": len(holdings),
            "market_regime": active_regime,
            "target_gross_exposure_pct": active_exposure_pct,
            "breadth_above_ema50_pct": close_breadth_pct,
        })

        if (
            config.daily_exit_enabled
            or config.daily_exit_ema200_enabled
            or config.daily_exit_momentum_enabled
        ):
            for symbol in holdings:
                if symbol in pending_exits:
                    continue
                frame = native.get(symbol)
                if frame is None or date not in frame.index:
                    continue
                row = frame.loc[date]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
                close_value = float(row["close"])
                ema200 = float(row["ema200"])
                momentum = float(row["momentum_6_1_pct"])
                ema_exit_enabled = (
                    config.daily_exit_enabled
                    or config.daily_exit_ema200_enabled
                )
                momentum_exit_enabled = (
                    config.daily_exit_enabled
                    or config.daily_exit_momentum_enabled
                )
                if ema_exit_enabled and close_value < ema200:
                    execution_date = delayed_execution_session.get(date)
                    if execution_date is not None:
                        pending_exits[symbol] = (
                            date,
                            execution_date,
                            "DAILY_EXIT_BELOW_EMA200",
                        )
                elif (
                    momentum_exit_enabled
                    and math.isfinite(momentum)
                    and momentum <= 0
                ):
                    execution_date = delayed_execution_session.get(date)
                    if execution_date is not None:
                        pending_exits[symbol] = (
                            date,
                            execution_date,
                            "DAILY_EXIT_MOMENTUM_NON_POSITIVE",
                        )

        if (
            config.breadth_exposure_enabled
            and config.breadth_review_frequency != "MONTHLY"
            and date in next_session
            and pending_breadth_rebalance is None
        ):
            following = next_session[date]
            should_review = config.breadth_review_frequency == "DAILY"
            if config.breadth_review_frequency == "WEEKLY":
                should_review = (
                    date.isocalendar()[:2]
                    != following.isocalendar()[:2]
                )
            if should_review:
                regime_row = market.loc[market["time"] <= date].iloc[-1]
                review_regime = str(regime_row["regime"])
                review_breadth = breadth_on(date)
                review_exposure = breadth_exposure_pct(
                    market_regime=review_regime,
                    breadth_pct=review_breadth,
                    config=config,
                )
                (
                    review_exposure,
                    pending_recovery_exposure_pct,
                    pending_recovery_periods,
                ) = apply_breadth_review_policy(
                    active_exposure_pct=active_exposure_pct,
                    proposed_exposure_pct=review_exposure,
                    recovery_confirmation_periods=(
                        config.breadth_recovery_confirmation_periods
                    ),
                    recovery_allowed=(
                        config.breadth_recovery_frequency == "REVIEW"
                    ),
                    pending_recovery_exposure_pct=(
                        pending_recovery_exposure_pct
                    ),
                    pending_recovery_periods=pending_recovery_periods,
                )
                if not math.isclose(
                    review_exposure,
                    active_exposure_pct,
                    abs_tol=1e-9,
                ):
                    execution_date = delayed_execution_session.get(date)
                    if execution_date is not None:
                        pending_breadth_rebalance = (
                            date,
                            execution_date,
                            review_exposure,
                            review_regime,
                            review_breadth,
                        )

    equity = pd.DataFrame(equity_rows)
    if not equity.empty:
        equity["running_peak"] = equity["equity"].cummax()
        equity["drawdown_pct"] = (
            equity["equity"] / equity["running_peak"] - 1.0
        ) * 100.0
    snapshots = pd.concat(snapshot_frames, ignore_index=True) if snapshot_frames else pd.DataFrame()
    stale_events = pd.DataFrame(
        stale_rows,
        columns=[
            "valuation_date",
            "symbol",
            "quantity",
            "last_price_date",
            "carried_close",
        ],
    )
    orders = pd.DataFrame(
        order_rows,
        columns=[
            "signal_date",
            "execution_date",
            "symbol",
            "side",
            "quantity",
            "reference_open",
            "fill_price",
            "notional",
            "fees_tax",
            "reason",
        ],
    )
    final_equity = float(equity.iloc[-1]["equity"]) if not equity.empty else initial_capital
    years = max((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25, 1 / 365.25)
    average_equity = (
        float(equity["equity"].mean())
        if not equity.empty and float(equity["equity"].mean()) > 0
        else initial_capital
    )
    return {
        "equity": equity,
        "orders": orders,
        "snapshots": snapshots,
        "stale_events": stale_events,
        "final_equity": final_equity,
        "total_return_pct": (final_equity / initial_capital - 1.0) * 100.0,
        "max_drawdown_pct": float(equity["drawdown_pct"].min()) if not equity.empty else 0.0,
        "total_transaction_cost": total_cost,
        "annualized_turnover_pct": total_traded_notional / average_equity / years * 100.0,
        "annualized_turnover_on_initial_pct": (
            total_traded_notional / initial_capital / years * 100.0
        ),
        "average_equity": average_equity,
        "stale_valuation_events": len(stale_events),
    }
