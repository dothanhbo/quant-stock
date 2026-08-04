from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping


_REGIME_ALIASES = {
    "BULL": "BULL",
    "BULLISH": "BULL",
    "UPTREND": "BULL",
    "STRONG_BULL": "BULL",
    "SIDEWAY": "SIDEWAY",
    "SIDEWAYS": "SIDEWAY",
    "NEUTRAL": "SIDEWAY",
    "RANGE": "SIDEWAY",
    "RANGING": "SIDEWAY",
    "BEAR": "BEAR",
    "BEARISH": "BEAR",
    "DOWNTREND": "BEAR",
    "STRONG_BEAR": "BEAR",
    "UNKNOWN": "UNKNOWN",
}


@dataclass(slots=True, frozen=True)
class RegimePortfolioRule:
    """
    Portfolio constraints for one normalized market regime.

    `max_portfolio_heat_pct=None` means no explicit heat cap.
    `allow_new_positions=False` blocks all new entries.
    """

    allow_new_positions: bool
    max_positions: int
    max_portfolio_heat_pct: float | None

    def __post_init__(self) -> None:
        if self.max_positions < 0:
            raise ValueError(
                "max_positions không được âm."
            )

        if (
            self.allow_new_positions
            and self.max_positions < 1
        ):
            raise ValueError(
                "Khi cho phép mở vị thế, "
                "max_positions phải từ 1."
            )

        if self.max_portfolio_heat_pct is not None:
            heat = float(
                self.max_portfolio_heat_pct
            )

            if (
                not isfinite(heat)
                or heat <= 0
            ):
                raise ValueError(
                    "max_portfolio_heat_pct "
                    "phải lớn hơn 0 hoặc None."
                )


@dataclass(slots=True, frozen=True)
class RegimePortfolioDecision:
    raw_regime: str
    normalized_regime: str
    rule: RegimePortfolioRule

    @property
    def allow_new_positions(self) -> bool:
        return self.rule.allow_new_positions

    @property
    def max_positions(self) -> int:
        return self.rule.max_positions

    @property
    def max_portfolio_heat_pct(
        self,
    ) -> float | None:
        return (
            self.rule
            .max_portfolio_heat_pct
        )


class RegimePortfolioPolicy:
    """
    Resolve portfolio constraints from a market-regime label.

    The policy is stateless and independent from PortfolioSimulator.
    """

    def __init__(
        self,
        *,
        rules: Mapping[
            str,
            RegimePortfolioRule,
        ] | None = None,
        unknown_rule: (
            RegimePortfolioRule | None
        ) = None,
    ) -> None:
        default_rules = {
            "BULL": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=5,
                max_portfolio_heat_pct=5.0,
            ),
            "SIDEWAY": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=3,
                max_portfolio_heat_pct=4.0,
            ),
            "BEAR": RegimePortfolioRule(
                allow_new_positions=False,
                max_positions=0,
                max_portfolio_heat_pct=None,
            ),
        }

        supplied_rules = (
            default_rules
            if rules is None
            else dict(rules)
        )

        normalized_rules: dict[
            str,
            RegimePortfolioRule,
        ] = {}

        for regime, rule in (
            supplied_rules.items()
        ):
            normalized = (
                normalize_market_regime(
                    regime
                )
            )

            if normalized == "UNKNOWN":
                raise ValueError(
                    "Không được khai báo rule "
                    "UNKNOWN trong rules; hãy dùng "
                    "unknown_rule."
                )

            normalized_rules[
                normalized
            ] = rule

        required = {
            "BULL",
            "SIDEWAY",
            "BEAR",
        }

        missing = (
            required
            - set(normalized_rules)
        )

        if missing:
            raise ValueError(
                "Thiếu regime rule: "
                + ", ".join(
                    sorted(missing)
                )
            )

        self.rules = normalized_rules
        self.unknown_rule = (
            unknown_rule
            or RegimePortfolioRule(
                allow_new_positions=False,
                max_positions=0,
                max_portfolio_heat_pct=None,
            )
        )

    def resolve(
        self,
        regime: str | None,
    ) -> RegimePortfolioDecision:
        raw_regime = (
            ""
            if regime is None
            else str(regime)
        )

        normalized = (
            normalize_market_regime(
                raw_regime
            )
        )

        rule = (
            self.unknown_rule
            if normalized == "UNKNOWN"
            else self.rules[normalized]
        )

        return RegimePortfolioDecision(
            raw_regime=raw_regime,
            normalized_regime=normalized,
            rule=rule,
        )


def normalize_market_regime(
    regime: str | None,
) -> str:
    if regime is None:
        return "UNKNOWN"

    normalized = (
        str(regime)
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )

    if not normalized:
        return "UNKNOWN"

    return _REGIME_ALIASES.get(
        normalized,
        "UNKNOWN",
    )
