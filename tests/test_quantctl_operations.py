from __future__ import annotations

import os
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
    OperationSpec,
    execute_operation,
    get_operation,
    list_operations,
)
from quantctl.run_history import HISTORY_PATH_ENV, RUN_ID_ENV, OperationHistoryStore, RunStatus
from quantctl.run_history import sanitize_captured_output


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
    environment = calls[0][1]["env"]
    assert isinstance(environment, dict)
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"


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


def test_wrapper_propagates_tracking_environment_without_duplicate_run(tmp_path: Path) -> None:
    database = tmp_path / "operation_history.db"
    captured: dict[str, str] = {}

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured[RUN_ID_ENV] = str(environment[RUN_ID_ENV])
        captured[HISTORY_PATH_ENV] = str(environment[HISTORY_PATH_ENV])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    result = execute_operation(
        "daily",
        root=PROJECT_ROOT,
        process_runner=runner,
        history_path=database,
    )

    assert captured[RUN_ID_ENV] == result.run_id
    assert Path(captured[HISTORY_PATH_ENV]) == database
    assert len(OperationHistoryStore(database).list_runs()) == 1


def test_keyboard_interrupt_is_recorded_as_cancelled_and_reraised(tmp_path: Path) -> None:
    database = tmp_path / "operation_history.db"

    def interrupted(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_operation(
            "scan",
            root=PROJECT_ROOT,
            process_runner=interrupted,
            history_path=database,
        )

    runs = OperationHistoryStore(database).list_runs()
    assert len(runs) == 1
    assert runs[0].status is RunStatus.CANCELLED
    assert runs[0].exit_code == 130


def test_real_child_process_forces_utf8_and_safely_captures_unicode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "unicode_operation_fixture.py"
    module.write_text(
        "import sys\n"
        "print('📊 Thị trường Việt Nam')\n"
        "print('⚠️ lỗi tiếng Việt', file=sys.stderr)\n"
        "print('TOKEN=child-secret')\n"
        "print('ữ' * 30000)\n"
        "print('PASSWORD=stderr-secret', file=sys.stderr)\n"
        "print('ỗ' * 30000, file=sys.stderr)\n",
        encoding="utf-8",
    )
    spec = OperationSpec(
        "unicode-fixture",
        "Unicode subprocess regression fixture.",
        (OperationCapability.LOCAL_WRITE,),
        "unicode_operation_fixture",
    )
    monkeypatch.setattr("quantctl.operations.get_operation", lambda name: spec)
    monkeypatch.setattr("quantctl.operations.operation_available", lambda spec, root: True)
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join((str(tmp_path), str(PROJECT_ROOT))),
    )

    result = execute_operation(
        "unicode-fixture",
        root=tmp_path,
        history_path=tmp_path / "history.db",
    )

    assert result.success
    assert "📊 Thị trường Việt Nam" in result.stdout
    assert "⚠️ lỗi tiếng Việt" in result.stderr
    assert "child-secret" not in result.stdout
    assert "stderr-secret" not in result.stderr
    assert "TOKEN=[REDACTED]" in result.stdout
    assert "PASSWORD=[REDACTED]" in result.stderr
    assert len(result.stdout) == 20_000
    assert len(result.stderr) == 20_000
    assert result.stdout.endswith("[OUTPUT TRUNCATED]")
    assert result.stderr.endswith("[OUTPUT TRUNCATED]")
    runs = OperationHistoryStore(tmp_path / "history.db").list_runs()
    assert len(runs) == 1 and runs[0].status is RunStatus.SUCCESS


def test_direct_tracked_entrypoint_reconfigures_cp1252_stdio(tmp_path: Path) -> None:
    script = tmp_path / "direct_unicode.py"
    script.write_text(
        "from quantctl.run_history import run_tracked_entrypoint\n"
        "def main():\n"
        "    print('📊 Việt Nam trực tiếp')\n"
        "    return 0\n"
        "raise SystemExit(run_tracked_entrypoint('scan', main))\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONIOENCODING": "cp1252",
            "PYTHONUTF8": "0",
            "PYTHONPATH": str(PROJECT_ROOT),
            RUN_ID_ENV: "existing-test-run",
        }
    )

    completed = subprocess.run(
        (sys.executable, str(script)),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "📊 Việt Nam trực tiếp"
    assert completed.stderr == ""


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
        self.errors: list[str] = []
        self.codes: list[str] = []
        self.expanders: list[str] = []

    def __getattr__(self, name: str):
        if name in {
            "header", "warning", "subheader", "write", "caption", "divider",
            "success", "json", "markdown", "metric", "rerun",
        }:
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def spinner(self, *args: object, **kwargs: object):
        return self

    def container(self, *args: object, **kwargs: object):
        return self

    def expander(self, *args: object, **kwargs: object):
        self.expanders.append(str(args[0]))
        return self

    def error(self, value: object, *args: object, **kwargs: object) -> None:
        self.errors.append(str(value))

    def code(self, value: object, *args: object, **kwargs: object) -> None:
        self.codes.append(str(value))

    def columns(self, count: int):
        return [self for _ in range(count)]

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


def test_failure_ux_keeps_bounded_redacted_output_in_technical_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_streamlit = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_streamlit)
    output = sanitize_captured_output("TOKEN=hidden\n" + "ữ" * 30_000)
    result = OperationResult(
        "scan",
        False,
        1,
        "Operation scan failed with exit code 1.",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00",
        MappingProxyType({"module_target": "strategy.scanner"}),
        stderr=output,
        run_id="12345678-1234-1234-1234-123456789abc",
        duration_ms=1000,
        status="FAILED",
    )

    operations_page._display_result(result)

    assert fake_streamlit.errors == ["Run Scanner failed."]
    assert fake_streamlit.expanders == ["Technical details"]
    assert all("hidden" not in value for value in fake_streamlit.codes)
    assert any("TOKEN=[REDACTED]" in value for value in fake_streamlit.codes)
    assert any(value.endswith("[OUTPUT TRUNCATED]") for value in fake_streamlit.codes)
