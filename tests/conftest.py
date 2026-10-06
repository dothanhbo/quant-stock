"""Suite-wide test isolation.

Several production entrypoints configure the process environment directly
(``scripts.run_daily.main``, the V2/V3 paper wrappers and
``config.paper_store.apply_active_paper_store_environment`` write
``PAPER_DATABASE_PATH``, ``PAPER_STRATEGY_VERSION``, ``TRADING_*`` ...). When a
test exercises them without ``monkeypatch``, those values leaked into every
later test, making results order-dependent and, because the store paths are
repository-anchored, able to point later tests at the real ``data/*.db``
files. Restoring ``os.environ`` after each test removes that coupling without
changing any production behaviour.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _restore_process_environment():
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        if os.environ != snapshot:
            os.environ.clear()
            os.environ.update(snapshot)



# Tests never read the developer's real ``.env`` (it may hold secrets and
# machine-local strategy overrides). Several modules call ``load_dotenv()`` at
# import time, i.e. during collection, which would otherwise copy the local
# ``.env`` into ``os.environ`` for the whole session. Since the strategy-
# contract gate fails closed on non-canonical configuration (B6), such a file
# would decide test outcomes. This is installed when conftest is imported,
# before any test module (and therefore any production module) is imported.
# Production entrypoints are unchanged; tests that need values set them with
# ``monkeypatch``.
def _no_dotenv(*_args, **_kwargs) -> bool:
    return False


try:
    import dotenv as _dotenv
    import dotenv.main as _dotenv_main
except ImportError:  # pragma: no cover
    pass
else:
    _dotenv.load_dotenv = _no_dotenv
    _dotenv_main.load_dotenv = _no_dotenv
