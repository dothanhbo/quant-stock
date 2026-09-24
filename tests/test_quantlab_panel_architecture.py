from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


TRADING_MODULES = (
    "strategy", "backtesting", "execution", "quantlab.alpha",
    "quantlab.candidates", "quantlab.outcomes", "quantlab.evaluation",
)
FEATURE_IMPLEMENTATION_MODULES = (
    "quantlab.features.builtins",
    "quantlab.features.registry",
)


ARCHITECTURE_CASES = (
    pytest.param(
        ("quantlab.panels", "quantlab.panels.observation_index"),
        TRADING_MODULES,
        False,
        True,
        (),
        id="observation-index",
    ),
    pytest.param(
        ("quantlab.panels", "quantlab.panels.feature_panel"),
        (*TRADING_MODULES, *FEATURE_IMPLEMENTATION_MODULES),
        False,
        True,
        (),
        id="feature-panel",
    ),
    pytest.param(
        ("quantlab.panels", "quantlab.panels.neutral_features"),
        (*TRADING_MODULES, *FEATURE_IMPLEMENTATION_MODULES),
        False,
        True,
        (),
        id="neutral-feature-panel",
    ),
    pytest.param(
        ("quantlab.panels.outcome_contracts", "quantlab.panels.outcome_panel"),
        (
            "strategy", "backtesting", "quantlab.alpha", "quantlab.candidates",
            *FEATURE_IMPLEMENTATION_MODULES,
        ),
        True,
        True,
        (),
        id="outcome-panel",
    ),
    pytest.param(
        (
            "quantlab.panels.research_dataset_contracts",
            "quantlab.panels.research_dataset",
        ),
        (
            "strategy", "execution", "backtesting", "quantlab.candidates",
            "quantlab.alpha",
        ),
        True,
        True,
        (),
        id="research-dataset",
    ),
)


@pytest.mark.parametrize(
    (
        "import_targets", "forbidden_modules", "set_database_path",
        "block_network", "expected_created",
    ),
    ARCHITECTURE_CASES,
)
def test_fresh_process_panel_import_isolation(
    tmp_path: Path,
    import_targets: tuple[str, ...],
    forbidden_modules: tuple[str, ...],
    set_database_path: bool,
    block_network: bool,
    expected_created: tuple[str, ...],
) -> None:
    target = tmp_path / "must-not-exist.db"
    code = (
        "import importlib, json, pathlib, sys; "
        f"work=pathlib.Path({str(tmp_path)!r}); before={{str(p) for p in work.iterdir()}}; "
        "network_calls=('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'); "
        f"block_network={block_network!r}; "
        "sys.addaudithook(lambda event, args: (_ for _ in ()).throw(RuntimeError('network access during import')) if block_network and event in network_calls else None); "
        f"[importlib.import_module(name) for name in {import_targets!r}]; "
        f"forbidden={forbidden_modules!r}; "
        "loaded=sorted(name for name in sys.modules if any(name == item or name.startswith(item + '.') for item in forbidden)); "
        "created=sorted(str(p) for p in work.iterdir() if str(p) not in before); "
        f"print(json.dumps({{'loaded': loaded, 'created': created, 'database_created': pathlib.Path({str(target)!r}).exists()}}))"
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if set_database_path:
        environment["MARKET_DATABASE_PATH"] = str(target)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "loaded": [],
        "created": list(expected_created),
        "database_created": False,
    }
