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
