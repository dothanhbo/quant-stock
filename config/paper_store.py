from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping, MutableMapping

from dotenv import dotenv_values

from core.paths import PROJECT_ROOT


Q70_STRATEGY_IDENTITY = "Q70_FROZEN"
V3_STRATEGY_IDENTITY = "V3_BREADTH_40_60"


@dataclass(frozen=True, slots=True)
class PaperStoreDefinition:
    store_id: str
    display_name: str
    strategy_identity: str
    default_path: Path
    override_variable: str


@dataclass(frozen=True, slots=True)
class ResolvedPaperStore:
    store_id: str
    display_name: str
    strategy_identity: str
    database_path: Path
    override_variable: str
    writable_by_current_pipeline: bool = True


GENERIC_PAPER_STORE = PaperStoreDefinition(
    "generic-paper",
    "Generic Paper Store",
    "GENERIC_PAPER",
    Path("data/paper_trading.db"),
    "PAPER_DATABASE_PATH",
)
Q70_PAPER_STORE = PaperStoreDefinition(
    "q70-frozen",
    "Frozen Q70 Paper Store",
    Q70_STRATEGY_IDENTITY,
    Path("data/paper_trading_v2.db"),
    "PAPER_V2_DATABASE_PATH",
)
V3_PAPER_STORE = PaperStoreDefinition(
    "v3-breadth-40-60",
    "V3 Breadth Paper Store",
    V3_STRATEGY_IDENTITY,
    Path("data/paper_trading_v3.db"),
    "PAPER_V3_DATABASE_PATH",
)
KNOWN_PAPER_STORES = (GENERIC_PAPER_STORE, Q70_PAPER_STORE, V3_PAPER_STORE)


def effective_paper_strategy(environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    configured = str(values.get("PAPER_STRATEGY_VERSION", Q70_STRATEGY_IDENTITY)).strip().upper()
    return V3_STRATEGY_IDENTITY if configured == V3_STRATEGY_IDENTITY else Q70_STRATEGY_IDENTITY


def _definition_for_strategy(strategy_identity: str) -> PaperStoreDefinition:
    return V3_PAPER_STORE if strategy_identity == V3_STRATEGY_IDENTITY else Q70_PAPER_STORE


class AmbiguousPaperStoreError(RuntimeError):
    """Raised when the default paper store could silently resolve to two files."""


def _anchored_default_path(definition: PaperStoreDefinition, *, root: Path) -> Path:
    """Anchor a store's default path to the repository root, never the CWD.

    Before 2026-10-06 the default (no override variable) was returned as the
    bare relative path ``data/paper_trading*.db`` and therefore opened relative
    to the process working directory, while overrides, the market database and
    the QuantCtl/Manager inspectors were all repository-anchored. Launching
    from the repository root (the documented production contract) resolves to
    the same file either way. To make sure this change can never silently
    switch a running deployment to a different paper store, a distinct,
    already-existing CWD-relative store file stops resolution with an explicit
    error instead of being ignored or used.
    """
    anchored = (root / definition.default_path).resolve()
    if root.resolve() == PROJECT_ROOT.resolve():
        cwd_candidate = (Path.cwd() / definition.default_path).resolve()
        if cwd_candidate != anchored and cwd_candidate.is_file():
            raise AmbiguousPaperStoreError(
                f"{definition.display_name}: found a paper database relative to "
                f"the current working directory ({cwd_candidate}) that differs "
                f"from the repository-anchored default ({anchored}). Run from "
                f"the repository root or set {definition.override_variable} to "
                "the intended absolute path; no store was selected."
            )
    return anchored


def resolve_store_definition(
    definition: PaperStoreDefinition,
    environ: Mapping[str, str] | None = None,
    *,
    writable_by_current_pipeline: bool = False,
    root: Path = PROJECT_ROOT,
) -> ResolvedPaperStore:
    values = os.environ if environ is None else environ
    configured = str(values.get(definition.override_variable, "")).strip()
    if configured:
        database_path = Path(configured).expanduser()
        if not database_path.is_absolute():
            database_path = root / database_path
        database_path = database_path.resolve()
    else:
        database_path = _anchored_default_path(definition, root=root)
    return ResolvedPaperStore(
        definition.store_id,
        definition.display_name,
        definition.strategy_identity,
        database_path,
        definition.override_variable,
        writable_by_current_pipeline,
    )


def resolve_active_paper_store(
    environ: Mapping[str, str] | None = None,
    *,
    strategy_identity: str | None = None,
    root: Path = PROJECT_ROOT,
) -> ResolvedPaperStore:
    effective = (
        effective_paper_strategy(environ)
        if strategy_identity is None
        else effective_paper_strategy({"PAPER_STRATEGY_VERSION": strategy_identity})
    )
    return resolve_store_definition(
        _definition_for_strategy(effective),
        environ,
        writable_by_current_pipeline=True,
        root=root,
    )


def configured_paper_environment(
    *,
    root: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    values = {
        str(key): str(value)
        for key, value in dotenv_values(root / ".env").items()
        if value is not None
    }
    values.update(dict(os.environ if environ is None else environ))
    return values


def apply_active_paper_store_environment(
    environ: MutableMapping[str, str] | None = None,
    *,
    strategy_identity: str | None = None,
) -> ResolvedPaperStore:
    target = os.environ if environ is None else environ
    resolved = resolve_active_paper_store(target, strategy_identity=strategy_identity)
    target["PAPER_STRATEGY_VERSION"] = resolved.strategy_identity
    target["PAPER_DATABASE_PATH"] = str(resolved.database_path)
    return resolved
