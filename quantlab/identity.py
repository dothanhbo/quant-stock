from __future__ import annotations

"""Small dependency-neutral helpers for deterministic research identities."""

from enum import Enum
import json
import math
from typing import Any, Mapping


def canonical_json(value: Any) -> bytes:
    """Encode compact UTF-8 JSON with deterministic mapping-key ordering."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_identity_value(value: Any) -> Any:
    """Convert identity content to deterministic JSON-safe primitives."""
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (AttributeError, ValueError):
            pass
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__nonfinite_float__": "NaN"}
        if math.isinf(value):
            return {"__nonfinite_float__": "Infinity" if value > 0 else "-Infinity"}
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("identity mapping keys must be strings")
        return {
            key: canonical_identity_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (tuple, list)):
        return [canonical_identity_value(item) for item in value]
    raise TypeError(f"unsupported identity value: {type(value).__name__}")
