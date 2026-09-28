from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

from manager.pages import operations as operations_page
from manager.view_models import OperationViewModel, OperationsViewModel, build_operations_model
from quantctl.cli import main
from quantctl.operations import (
    OperationCapability,
    OperationResult,
    execute_operation,
    get_operation,
    list_operations,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _result(name: str, *, success: bool = True, exit_code: int = 0) -> OperationResult:
    details = {
        "database": "MISSING",
        "latest_session": None,
        "session_count": None,
        "symbol_count": None,
        "latest_session_symbol_count": None,
    }
    return OperationResult(
        name,
        success,
        exit_code,
        f"{name} result",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00",
        MappingProxyType(details),
    )


def test_registry_exposes_exact_m2_operations() -> None:
    assert tuple(spec.name for spec in list_operations()) == (
        "data-status",
        "update",
        "scan",
        "daily",
    )


def test_capabilities_match_audited_side_effects() -> None:
    assert get_operation("data-status").capabilities == (OperationCapability.READ_ONLY,)
    assert get_operation("update").capabilities == (
        OperationCapability.LOCAL_WRITE,
        OperationCapability.MARKET_DATA_WRITE,
        OperationCapability.EXTERNAL_CALL,
    )
    assert get_operation("scan").capabilities == (
        OperationCapability.LOCAL_WRITE,
        OperationCapability.PAPER_WRITE,
        OperationCapability.EXTERNAL_CALL,
        OperationCapability.TELEGRAM_SEND,
    )
    assert get_operation("daily").capabilities == (
        OperationCapability.LOCAL_WRITE,
        OperationCapability.MARKET_DATA_WRITE,
        OperationCapability.PAPER_WRITE,
        OperationCapability.FORWARD_WRITE,
        OperationCapability.EXTERNAL_CALL,
        OperationCapability.TELEGRAM_SEND,
    )


def test_data_status_is_read_only_and_creates_nothing(tmp_path: Path) -> None:
    result = execute_operation("data-status", root=tmp_path)
    assert result.success
    assert result.details["database"] == "MISSING"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    ("argv", "operation"),
    ((["data", "status"], "data-status"), (["update"], "update"), (["scan"], "scan"), (["daily"], "daily")),
)
def test_cli_routes_each_m2_command_once(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    operation: str,
) -> None:
    calls: list[str] = []

    def fake_execute(name: str) -> OperationResult:
        calls.append(name)
        return _result(name)

    monkeypatch.setattr("quantctl.commands.operations.execute_operation", fake_execute)
    assert main(argv) == 0
    assert calls == [operation]


@pytest.mark.parametrize(
    ("operation", "module"),
    (("update", "scripts.update_data"), ("scan", "strategy.scanner"), ("daily", "scripts.run_daily")),
)
def test_wrappers_invoke_each_canonical_module_exactly_once(
    operation: str,
    module: str,
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "canonical output", "")

    result = execute_operation(
        operation,
        root=PROJECT_ROOT,
        process_runner=fake_runner,
        history_path=tmp_path / "operation_history.db",
    )

    assert result.success
    assert len(calls) == 1
    assert calls[0][0] == (sys.executable, "-m", module)
    assert calls[0][1]["cwd"] == PROJECT_ROOT


def test_wrapper_failure_propagates_clearly(tmp_path: Path) -> None:
    def failed(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 7, "", "canonical failure")

    result = execute_operation(
        "update",
        root=PROJECT_ROOT,
        process_runner=failed,
        history_path=tmp_path / "operation_history.db",
    )
    assert not result.success
    assert result.exit_code == 7
    assert result.stderr == "canonical failure"
    assert "failed with exit code 7" in result.message


def test_wrapper_contains_no_provider_or_business_logic() -> None:
    source = (PROJECT_ROOT / "quantctl" / "operations.py").read_text(encoding="utf-8")
    assert "from scripts.update_data" not in source
    assert "from strategy.scanner" not in source
    assert "Quote(" not in source
    assert "Vnstock(" not in source
    assert "TelegramClient(" not in source
    assert "run_scan(" not in source


def test_manager_uses_shared_operation_specs() -> None:
    model = build_operations_model(root=PROJECT_ROOT)
    assert tuple(item.spec for item in model.operations) == list_operations()
    assert all(item.available for item in model.operations)


class _FakeStreamlit:
    def __init__(self, *, confirm: bool = False, click: str | None = None) -> None:
        self.confirm = confirm
        self.click = click
        self.session_state: dict[str, object] = {}
        self.buttons: list[tuple[str, bool]] = []

    def __getattr__(self, name: str):
        if name in {"header", "warning", "subheader", "write", "caption", "divider", "success", "error", "json", "code"}:
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def checkbox(self, *args, **kwargs) -> bool:
        return self.confirm

    def button(self, label: str, *, disabled: bool, **kwargs) -> bool:
        self.buttons.append((label, disabled))
        return not disabled and label == self.click


def _operations_model() -> OperationsViewModel:
    return OperationsViewModel(tuple(OperationViewModel(spec, True) for spec in list_operations()))


def test_operations_page_never_executes_on_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_streamlit = _FakeStreamlit()
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "streamlit", fake_streamlit)
    monkeypatch.setattr(operations_page, "execute_operation", lambda name: calls.append(name))

    operations_page.render(_operations_model())

    assert calls == []
    assert ("Run Update", True) in fake_streamlit.buttons
    assert ("Run Scan", True) in fake_streamlit.buttons
    assert ("Run Daily", True) in fake_streamlit.buttons


def test_write_operation_requires_confirmation_and_explicit_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_streamlit = _FakeStreamlit(confirm=True, click="Run Update")
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "streamlit", fake_streamlit)

    def fake_execute(name: str) -> OperationResult:
        calls.append(name)
        return _result(name)

    monkeypatch.setattr(operations_page, "execute_operation", fake_execute)
    operations_page.render(_operations_model())

    assert calls == ["update"]
    assert fake_streamlit.session_state["quant_manager_last_operation_result"].operation == "update"
