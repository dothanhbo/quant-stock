from __future__ import annotations

"""Immutable point-in-time equity memberships for causal breadth features."""

from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from core.database_coverage import CoverageUniverseIndex
from .contracts import canonical_json


def _symbols(values: Iterable[str]) -> frozenset[str]:
    return frozenset(
        str(value).strip().upper()
        for value in values
        if str(value).strip() and str(value).strip().upper() != "VNINDEX"
    )


@dataclass(frozen=True, slots=True)
class PointInTimeUniverseContext:
    """Actual immutable membership content plus its deterministic identity."""

    universe_mode: str
    candidate_symbols: tuple[str, ...]
    session_dates: tuple[str, ...]
    membership_identity: str
    metadata: Mapping[str, object]
    _members_by_session: Mapping[str, frozenset[str]]

    @classmethod
    def from_memberships(
        cls,
        *,
        universe_mode: str,
        memberships: Mapping[str, Iterable[str]],
        metadata: Mapping[str, object] | None = None,
    ) -> "PointInTimeUniverseContext":
        normalized = {str(date): _symbols(values) for date, values in memberships.items()}
        dates = tuple(sorted(normalized))
        payload = {
            "universe_mode": str(universe_mode),
            "memberships": [[date, sorted(normalized[date])] for date in dates],
        }
        identity = sha256(canonical_json(payload)).hexdigest()
        candidates = tuple(sorted(set().union(*normalized.values()))) if normalized else ()
        return cls(
            str(universe_mode), candidates, dates, identity,
            MappingProxyType(dict(metadata or {})),
            MappingProxyType({date: normalized[date] for date in dates}),
        )

    @classmethod
    def static(cls, symbols: Iterable[str], session_dates: Iterable[str]) -> "PointInTimeUniverseContext":
        dates = tuple(sorted({str(item) for item in session_dates}))
        members = _symbols(symbols)
        return cls.from_memberships(
            universe_mode="explicit_static_symbols",
            memberships={date: members for date in dates},
            metadata={"limitation": "explicit static universe; not historical VN100"},
        )

    @classmethod
    def from_coverage_index(cls, index: CoverageUniverseIndex) -> "PointInTimeUniverseContext":
        return cls.from_memberships(
            universe_mode="database_coverage",
            memberships={date: index.members_as_of(date) for date in index.session_dates},
            metadata={
                "minimum_history_sessions": index.minimum_history_sessions,
                "maximum_staleness_sessions": index.maximum_staleness_sessions,
                "limitation": "database coverage is data availability, not reconstructed historical VN100",
            },
        )

    def members_as_of(self, date_text: str) -> frozenset[str]:
        position = bisect_right(self.session_dates, str(date_text)) - 1
        return frozenset() if position < 0 else self._members_by_session[self.session_dates[position]]


BREADTH_CONTEXT_KEY = "__QUANTLAB_HISTORICAL_BREADTH_CONTEXT__"
