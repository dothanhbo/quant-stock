from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from app.daily_pipeline import DailyPipelineResult, PipelineStageResult
from manager.view_models import build_dashboard_model, build_run_history_model
from quantctl.commands import history
from quantctl.cli import main as quantctl_main
from quantctl.operations import execute_operation, get_operation
from quantctl.run_history import (
    HISTORY_PATH_ENV,
    RUN_ID_ENV,
    OperationHistoryStore,
    RunDisplayStatus,
    RunStep,
    RunStatus,
    classify_run_for_display,
    record_daily_pipeline_steps,
    run_tracked_entrypoint,
    sanitize_captured_output,
    sanitize_diagnostic,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _completed(return_code: int = 0, stderr: str = ""):
    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, return_code, "output", stderr)
    return runner


def test_successful_operation_creates_one_run_with_capabilities(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    result = execute_operation(
        "update", root=PROJECT_ROOT, process_runner=_completed(), history_path=database
    )
    runs = OperationHistoryStore(database).list_runs()
    assert result.success and result.run_id
    assert len(runs) == 1
    assert runs[0].run_id == result.run_id
    assert runs[0].status is RunStatus.SUCCESS
    assert runs[0].capabilities == tuple(item.value for item in get_operation("update").capabilities)
    assert runs[0].finished_at_utc is not None
    assert runs[0].duration_ms is not None and runs[0].duration_ms >= 0


@pytest.mark.parametrize("operation", ("update", "scan", "daily"))
def test_each_mutating_operation_persists_its_declared_capabilities(
    operation: str, tmp_path: Path
) -> None:
    database = tmp_path / f"{operation}.db"
    result = execute_operation(
        operation, root=PROJECT_ROOT, process_runner=_completed(), history_path=database
    )
    detail = OperationHistoryStore(database).get_run(result.run_id or "")
    assert detail is not None
    assert detail.summary.capabilities == tuple(
        item.value for item in get_operation(operation).capabilities
    )


def test_failure_is_recorded_and_sensitive_error_is_redacted(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    secret = "super-secret-value"
    result = execute_operation(
        "scan",
        root=PROJECT_ROOT,
        process_runner=_completed(7, f"TOKEN={secret} provider failed"),
        history_path=database,
    )
    detail = OperationHistoryStore(database).get_run(result.run_id or "")
    assert detail is not None
    assert detail.summary.status is RunStatus.FAILED
    assert detail.summary.exit_code == 7
    assert detail.summary.error_type == "SubprocessExit"
    assert secret not in (detail.summary.error_message or "")
    assert "[REDACTED]" in (detail.summary.error_message or "")
    assert secret not in result.stderr
    assert "[REDACTED]" in result.stderr


def test_exit_130_is_recorded_as_cancelled(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    result = execute_operation(
        "daily", root=PROJECT_ROOT, process_runner=_completed(130, "cancelled"), history_path=database
    )
    detail = OperationHistoryStore(database).get_run(result.run_id or "")
    assert detail is not None
    assert result.status == RunStatus.CANCELLED.value
    assert detail.summary.status is RunStatus.CANCELLED
    assert detail.summary.exit_code == 130


def test_read_only_inspection_never_creates_history_store(tmp_path: Path) -> None:
    database = tmp_path / "data" / "operation_history.db"
    result = execute_operation("data-status", root=tmp_path, history_path=database)
    assert result.success and result.run_id is None
    assert history.render_list(root=tmp_path) == "RUN HISTORY\n\nNo operational runs recorded."
    assert not database.exists()
    assert not (tmp_path / "data").exists()


def test_run_ids_are_unique_and_history_is_newest_first(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    first = execute_operation("update", root=PROJECT_ROOT, process_runner=_completed(), history_path=database)
    second = execute_operation("daily", root=PROJECT_ROOT, process_runner=_completed(), history_path=database)
    assert first.run_id != second.run_id
    runs = OperationHistoryStore(database).list_runs()
    assert tuple(item.run_id for item in runs) == (second.run_id, first.run_id)
    assert OperationHistoryStore(database).get_run(first.run_id or "") is not None
    assert OperationHistoryStore(database).get_run("missing") is None


def test_running_display_classification_is_day_based_and_read_only(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    store = OperationHistoryStore(database)
    reference = date(2026, 9, 30)
    stale_id = store.begin_run(
        "daily",
        ("LOCAL_WRITE",),
        started_at_utc="2026-09-29T23:59:59+00:00",
    )
    current_id = store.begin_run(
        "scan",
        ("LOCAL_WRITE",),
        started_at_utc="2026-09-30T00:00:00+00:00",
    )
    before = database.read_bytes()

    stale = store.get_run(stale_id)
    current = store.get_run(current_id)
    assert stale is not None and current is not None
    assert classify_run_for_display(stale.summary, as_of_date=reference) is RunDisplayStatus.STALE_RUNNING
    assert classify_run_for_display(current.summary, as_of_date=reference) is RunDisplayStatus.RUNNING
    assert database.read_bytes() == before


def test_terminal_display_classification_preserves_failure_and_cancellation(tmp_path: Path) -> None:
    database = tmp_path / "history.db"
    store = OperationHistoryStore(database)
    statuses = (
        (RunStatus.SUCCESS, RunDisplayStatus.COMPLETED),
        (RunStatus.FAILED, RunDisplayStatus.FAILED),
        (RunStatus.CANCELLED, RunDisplayStatus.CANCELLED),
    )
    for persisted, expected in statuses:
        run_id = store.begin_run(persisted.value.lower(), ())
        store.finish_run(run_id, status=persisted, exit_code=0, duration_ms=1)
        detail = store.get_run(run_id)
        assert detail is not None
        assert classify_run_for_display(detail.summary) is expected


def test_cli_marks_prior_date_running_record_stale_without_repair(tmp_path: Path) -> None:
    database = tmp_path / "data" / "operation_history.db"
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    store = OperationHistoryStore(database)
    run_id = store.begin_run(
        "daily",
        ("LOCAL_WRITE",),
        started_at_utc=f"{yesterday.isoformat()}T12:00:00+00:00",
    )
    before = database.read_bytes()

    rendered = history.render_show(run_id, root=tmp_path)

    assert "Status: STALE_RUNNING" in rendered
    assert "Persisted status: RUNNING" in rendered
    assert "no repair was attempted" in rendered
    assert database.read_bytes() == before


def test_daily_steps_use_only_canonical_pipeline_stage_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "history.db"
    store = OperationHistoryStore(database)
    run_id = store.begin_run("daily", ("LOCAL_WRITE",), started_at_utc="2026-09-28T08:00:00+00:00")
    monkeypatch.setenv(RUN_ID_ENV, run_id)
    monkeypatch.setenv(HISTORY_PATH_ENV, str(database))
    result = DailyPipelineResult(
        started_at=datetime.now(),
        finished_at=datetime.now(),
        stages=[
            PipelineStageResult("Update Market Data", True, 1.25),
            PipelineStageResult("Market Data Integrity", True, 0.25, warning="safe warning"),
            PipelineStageResult("Forward Validation", False, 0.5, error="RuntimeError: failed"),
        ],
    )
    record_daily_pipeline_steps(result, root=tmp_path)
    detail = store.get_run(run_id)
    assert detail is not None
    assert tuple(item.step_name for item in detail.steps) == (
        "Update Market Data", "Market Data Integrity", "Forward Validation"
    )
    assert tuple(item.sequence for item in detail.steps) == (1, 2, 3)
    assert detail.steps[-1].status is RunStatus.FAILED
    assert detail.steps[1].metadata["warning"] == "safe warning"


def test_direct_daily_entrypoint_propagates_failed_stage_to_run_summary(tmp_path: Path) -> None:
    def daily() -> int:
        result = DailyPipelineResult(
            started_at=datetime.now(),
            finished_at=datetime.now(),
            stages=(
                PipelineStageResult("Update Market Data", True, 0.01),
                PipelineStageResult("Market Data Integrity", False, 0.01, error="invalid data"),
            ),
        )
        record_daily_pipeline_steps(result)
        return 1

    assert run_tracked_entrypoint("daily", daily, root=tmp_path) == 1
    detail = OperationHistoryStore(tmp_path / "data" / "operation_history.db").latest_run()
    assert detail is not None
    assert detail.summary.status is RunStatus.FAILED
    assert detail.summary.failed_step == "Market Data Integrity"
    assert tuple(item.step_name for item in detail.steps) == (
        "Update Market Data", "Market Data Integrity"
    )


def test_history_show_and_missing_run_are_clear(tmp_path: Path) -> None:
    database = tmp_path / "data" / "operation_history.db"
    store = OperationHistoryStore(database)
    run_id = store.begin_run("update", ("MARKET_DATA_WRITE",))
    store.finish_run(run_id, status=RunStatus.SUCCESS, exit_code=0, duration_ms=123)
    shown = history.render_show(run_id, root=tmp_path)
    assert f"Run: {run_id}" in shown
    assert "Operation: UPDATE" in shown
    assert "Steps\n  UNAVAILABLE" in shown
    assert history.run_show("missing", root=tmp_path) == 1


def test_cli_history_routes_list_limit_and_show(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "quantctl.commands.history.run_list",
        lambda *, limit=20: calls.append(("list", limit)) or 0,
    )
    monkeypatch.setattr(
        "quantctl.commands.history.run_show",
        lambda run_id: calls.append(("show", run_id)) or 0,
    )
    assert quantctl_main(["history", "--limit", "7"]) == 0
    assert quantctl_main(["history", "show", "run-123"]) == 0
    assert calls == [("list", 7), ("show", "run-123")]


def test_error_sanitizer_is_bounded_and_redacts_common_secret_shapes() -> None:
    value = sanitize_diagnostic("Bearer abc TOKEN=def api_key=ghi password=jkl " + "x" * 1000)
    assert value is not None and len(value) <= 500
    assert all(secret not in value for secret in ("abc", "def", "ghi", "jkl"))
    assert value.count("[REDACTED]") == 4


def test_captured_operation_output_is_redacted_and_bounded() -> None:
    secret = "never-render-this-token"
    value = sanitize_captured_output(f"TOKEN={secret}\n" + "x" * 30_000)

    assert secret not in value
    assert "TOKEN=[REDACTED]" in value
    assert len(value) == 20_000
    assert value.endswith("[OUTPUT TRUNCATED]")


def test_manager_dashboard_and_history_page_share_persisted_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "research").mkdir()
    store = OperationHistoryStore(tmp_path / "data" / "operation_history.db")
    run_id = store.begin_run("scan", ("TELEGRAM_SEND",))
    store.finish_run(run_id, status=RunStatus.SUCCESS, exit_code=0, duration_ms=10)
    dashboard = build_dashboard_model(root=tmp_path, environ={})
    model = build_run_history_model(root=tmp_path)
    assert dashboard.latest_run is not None and dashboard.latest_run.summary.run_id == run_id
    assert model.latest is not None and model.latest.summary.run_id == run_id
    assert model.recent[0].summary.run_id == run_id

    source = (PROJECT_ROOT / "manager/pages/run_history.py").read_text(encoding="utf-8")
    assert "execute_operation" not in source
    assert "subprocess" not in source
    assert "Run History" in source


def test_operations_page_is_not_a_second_persistence_writer() -> None:
    source = (PROJECT_ROOT / "manager/pages/operations.py").read_text(encoding="utf-8")
    assert "OperationHistoryStore" not in source
    assert "Open Run History" in source


def test_temporary_history_does_not_touch_canonical_path(tmp_path: Path) -> None:
    canonical = PROJECT_ROOT / "data" / "operation_history.db"
    existed = canonical.exists()
    execute_operation("update", root=PROJECT_ROOT, process_runner=_completed(), history_path=tmp_path / "history.db")
    assert (tmp_path / "history.db").is_file()
    assert canonical.exists() is existed


def test_malformed_history_degrades_without_traceback_or_manager_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "research").mkdir()
    (data / "operation_history.db").write_text("not a sqlite database", encoding="utf-8")

    assert history.run_list(root=tmp_path) == 1
    assert "unavailable or malformed" in capsys.readouterr().out
    assert "Traceback" not in history.render_list(root=tmp_path)
    dashboard = build_dashboard_model(root=tmp_path, environ={})
    manager_history = build_run_history_model(root=tmp_path)
    assert dashboard.latest_run is None
    assert dashboard.history_warning == "Operation history is unavailable or malformed."
    assert manager_history.recent == ()
    assert manager_history.warning == dashboard.history_warning


def test_run_history_page_handles_empty_and_failed_stepped_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from streamlit.testing.v1 import AppTest

    database = tmp_path / "operation_history.db"
    monkeypatch.setenv(HISTORY_PATH_ENV, str(database))
    app = AppTest.from_file(str(PROJECT_ROOT / "manager" / "app.py"), default_timeout=15).run()
    app.sidebar.radio[0].set_value("OPERATIONS  /  Run History")
    app.run()
    assert not app.exception
    assert any("No operational runs recorded" in item.value for item in app.caption)

    store = OperationHistoryStore(database)
    run_id = store.begin_run("daily", ("LOCAL_WRITE",), started_at_utc="2026-01-01T00:00:00+00:00")
    store.record_steps(
        run_id,
        (
            RunStep(
                run_id,
                1,
                "Scanner",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:01+00:00",
                RunStatus.FAILED,
                1000,
                1,
                "Unicode child output failed",
            ),
        ),
    )
    store.finish_run(
        run_id,
        status=RunStatus.FAILED,
        exit_code=1,
        duration_ms=1000,
        failed_step="Scanner",
        error_message="Unicode child output failed",
    )

    app.run()
    assert not app.exception
    assert any(item.value == "Unicode child output failed" for item in app.error)
    assert any(item.label.startswith("Run ") for item in app.expander)
    assert len(app.dataframe) >= 2
