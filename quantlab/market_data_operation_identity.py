from __future__ import annotations

"""Durable deterministic identities for temporary-SQLite market-data shadowing."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
import sqlite3


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OperationIdentityConflict(RuntimeError):
    """An operation identity was reused with incompatible durable content."""


def _text(value: object, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _digest(value: str, name: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class OperationIdentityRequest:
    environment_identity: str
    database_identity: str
    entrypoint_namespace: str
    symbol: str
    provider_identity: str
    endpoint_identity: str
    package_name: str
    package_version: str
    requested_start: date
    requested_end: date
    venue: str
    calendar_identity: str
    calendar_version: str
    calendar_snapshot_reference: str
    target_session: date
    policy_version: str

    def __post_init__(self) -> None:
        for name in (
            "environment_identity", "database_identity", "entrypoint_namespace",
            "provider_identity", "endpoint_identity", "package_name", "package_version",
            "calendar_identity", "calendar_version", "calendar_snapshot_reference",
            "policy_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "entrypoint_namespace", self.entrypoint_namespace.lower())
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "venue", _text(self.venue, "venue").upper())
        for name in ("requested_start", "requested_end", "target_session"):
            value = getattr(self, name)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise ValueError(f"{name.replace('_', ' ')} must be a date")
        if self.requested_start > self.requested_end:
            raise ValueError("requested session range is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": "market-data-request-identity/v1",
            "environment_identity": self.environment_identity,
            "database_identity": self.database_identity,
            "entrypoint_namespace": self.entrypoint_namespace,
            "symbol": self.symbol,
            "provider_identity": self.provider_identity,
            "endpoint_identity": self.endpoint_identity,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "venue": self.venue,
            "calendar_identity": self.calendar_identity,
            "calendar_version": self.calendar_version,
            "calendar_snapshot_reference": self.calendar_snapshot_reference,
            "target_session": self.target_session.isoformat(),
            "policy_version": self.policy_version,
        }

    @property
    def request_identity(self) -> str:
        return _hash(self.as_dict())


@dataclass(frozen=True, slots=True)
class OperationIdentityAllocation:
    request_identity: str
    operation_id: str
    observation_generation: int
    payload_fingerprint: str
    revision_of: str | None
    idempotent_replay: bool


def create_operation_identity_schema(connection: sqlite3.Connection) -> None:
    """Create identity tables inside the caller's active migration transaction."""
    if not connection.in_transaction:
        raise ValueError("operation identity schema creation requires an active transaction")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS market_operation_requests (
            request_identity TEXT PRIMARY KEY,
            request_payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS market_operation_observations (
            operation_id TEXT PRIMARY KEY,
            request_identity TEXT NOT NULL
                REFERENCES market_operation_requests(request_identity) ON DELETE RESTRICT,
            observation_generation INTEGER NOT NULL CHECK (observation_generation >= 0),
            payload_fingerprint TEXT NOT NULL,
            revision_of TEXT
                REFERENCES market_operation_observations(operation_id) ON DELETE RESTRICT,
            operation_payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            UNIQUE(request_identity, observation_generation),
            UNIQUE(request_identity, payload_fingerprint)
        )
        """
    )


def allocate_operation_identity_in_transaction(
    connection: sqlite3.Connection,
    request: OperationIdentityRequest,
    payload_fingerprint: str,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> OperationIdentityAllocation:
    """Allocate or replay an observation while the caller holds BEGIN IMMEDIATE."""
    if not connection.in_transaction:
        raise ValueError("operation generation allocation requires an active transaction")
    inject = failure_injector or (lambda _stage: None)
    payload_fingerprint = _digest(payload_fingerprint, "payload fingerprint")
    request_payload_json = _json(request.as_dict())
    request_identity = request.request_identity
    existing_request = connection.execute(
        "SELECT request_payload_json FROM market_operation_requests WHERE request_identity=?",
        (request_identity,),
    ).fetchone()
    if existing_request is not None and existing_request[0] != request_payload_json:
        raise OperationIdentityConflict("request identity already names incompatible content")
    if existing_request is None:
        connection.execute(
            "INSERT INTO market_operation_requests VALUES(?,?,?)",
            (
                request_identity,
                request_payload_json,
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )

    replay = connection.execute(
        """
        SELECT operation_id,observation_generation,revision_of,operation_payload_json
        FROM market_operation_observations
        WHERE request_identity=? AND payload_fingerprint=?
        """,
        (request_identity, payload_fingerprint),
    ).fetchone()
    if replay is not None:
        operation_payload = {
            "contract": "market-data-operation-identity/v1",
            "request_identity": request_identity,
            "payload_fingerprint": payload_fingerprint,
            "observation_generation": replay[1],
        }
        expected_id = _hash(operation_payload)
        if replay[0] != expected_id or replay[3] != _json(operation_payload):
            raise OperationIdentityConflict("stored operation identity content is incompatible")
        return OperationIdentityAllocation(
            request_identity, replay[0], replay[1], payload_fingerprint, replay[2], True
        )

    previous = connection.execute(
        """
        SELECT operation_id,observation_generation
        FROM market_operation_observations
        WHERE request_identity=?
        ORDER BY observation_generation DESC
        LIMIT 1
        """,
        (request_identity,),
    ).fetchone()
    generation = 0 if previous is None else int(previous[1]) + 1
    revision_of = None if previous is None else str(previous[0])
    operation_payload = {
        "contract": "market-data-operation-identity/v1",
        "request_identity": request_identity,
        "payload_fingerprint": payload_fingerprint,
        "observation_generation": generation,
    }
    operation_payload_json = _json(operation_payload)
    operation_id = _hash(operation_payload)
    collision = connection.execute(
        """
        SELECT request_identity,payload_fingerprint,observation_generation,operation_payload_json
        FROM market_operation_observations WHERE operation_id=?
        """,
        (operation_id,),
    ).fetchone()
    expected = (request_identity, payload_fingerprint, generation, operation_payload_json)
    if collision is not None and tuple(collision) != expected:
        raise OperationIdentityConflict("operation id already names incompatible content")
    connection.execute(
        """
        INSERT INTO market_operation_observations(
            operation_id,request_identity,observation_generation,payload_fingerprint,
            revision_of,operation_payload_json,created_at_utc
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            operation_id,
            request_identity,
            generation,
            payload_fingerprint,
            revision_of,
            operation_payload_json,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    )
    inject("after_generation_insert")
    return OperationIdentityAllocation(
        request_identity, operation_id, generation, payload_fingerprint, revision_of, False
    )


def allocate_operation_identity(
    connection: sqlite3.Connection,
    request: OperationIdentityRequest,
    payload_fingerprint: str,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> OperationIdentityAllocation:
    """Standalone allocation wrapper serialized by BEGIN IMMEDIATE."""
    if connection.in_transaction:
        raise ValueError("standalone operation allocation requires an idle connection")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        result = allocate_operation_identity_in_transaction(
            connection,
            request,
            payload_fingerprint,
            failure_injector=failure_injector,
        )
        connection.commit()
        return result
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


__all__ = [
    "OperationIdentityAllocation",
    "OperationIdentityConflict",
    "OperationIdentityRequest",
    "allocate_operation_identity",
    "allocate_operation_identity_in_transaction",
    "create_operation_identity_schema",
]
