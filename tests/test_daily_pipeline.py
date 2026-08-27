from datetime import date

from app.daily_pipeline import (
    DailyPipeline,
)
from scripts import run_daily


def test_pipeline_runs_in_correct_order() -> None:
    calls: list[str] = []

    def update():
        calls.append(
            "update"
        )
        return (
            101,
            [],
        )

    def lifecycle():
        calls.append(
            "lifecycle"
        )

    def scan():
        calls.append(
            "scan"
        )

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
    ).run()

    assert result.success
    assert calls == [
        "update",
        "lifecycle",
        "scan",
    ]


def test_pipeline_continues_on_partial_data_errors() -> None:
    calls: list[str] = []

    def update():
        calls.append(
            "update"
        )
        return (
            99,
            [
                "FRT",
                "FTS",
            ],
        )

    def lifecycle():
        calls.append(
            "lifecycle"
        )

    def scan():
        calls.append(
            "scan"
        )

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
    ).run()

    assert result.success
    assert calls == [
        "update",
        "lifecycle",
        "scan",
    ]
    assert (
        result.stages[0].warning
        == "2 mã vẫn lỗi: FRT, FTS"
    )


def test_pipeline_can_stop_on_data_errors() -> None:
    calls: list[str] = []

    def update():
        calls.append(
            "update"
        )
        return (
            99,
            [
                "FRT",
                "FTS",
            ],
        )

    def lifecycle():
        calls.append(
            "lifecycle"
        )

    def scan():
        calls.append(
            "scan"
        )

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
    ).run(
        stop_on_data_errors=True
    )

    assert not result.success
    assert calls == [
        "update",
    ]


def test_pipeline_stops_after_lifecycle_failure() -> None:
    calls: list[str] = []

    def update():
        calls.append(
            "update"
        )
        return (
            101,
            [],
        )

    def lifecycle():
        calls.append(
            "lifecycle"
        )
        raise RuntimeError(
            "lifecycle failed"
        )

    def scan():
        calls.append(
            "scan"
        )

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
    ).run()

    assert not result.success
    assert calls == [
        "update",
        "lifecycle",
    ]


def test_run_daily_passes_pending_result_to_scanner(
    monkeypatch,
) -> None:
    pending_result = object()
    scanner_inputs: list[object] = []

    monkeypatch.setattr(
        run_daily,
        "update_market_data",
        lambda: (101, []),
    )
    monkeypatch.setattr(
        run_daily,
        "run_paper_lifecycle",
        lambda: pending_result,
    )
    monkeypatch.setattr(
        run_daily,
        "run_strategy_scanner",
        lambda pending_execution_result=None: (
            scanner_inputs.append(
                pending_execution_result
            )
        ),
    )
    monkeypatch.setattr(
        run_daily,
        "get_market_date",
        lambda: date.today().isoformat(),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_daily"],
    )

    assert run_daily.main() == 0
    assert scanner_inputs == [
        pending_result
    ]


def test_pipeline_skips_non_trading_day_after_update() -> None:
    calls: list[str] = []

    def update():
        calls.append("update")
        return (101, [])

    def lifecycle():
        calls.append("lifecycle")

    def scan():
        calls.append("scan")

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
        get_market_date=lambda: "2026-08-31",
        get_today=lambda: date(2026, 9, 2),
    ).run()

    assert result.success
    assert calls == ["update"]
    assert result.stages[-1].name == "Trading Session Guard"
    assert "Bỏ qua lifecycle và scanner" in result.stages[-1].warning


def test_pipeline_runs_when_market_has_today_session() -> None:
    calls: list[str] = []

    def update():
        calls.append("update")
        return (101, [])

    def lifecycle():
        calls.append("lifecycle")

    def scan():
        calls.append("scan")

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
        get_market_date=lambda: "2026-09-03",
        get_today=lambda: date(2026, 9, 3),
    ).run()

    assert result.success
    assert calls == ["update", "lifecycle", "scan"]


def test_pipeline_skip_update_preserves_manual_partial_run() -> None:
    calls: list[str] = []

    def update():
        calls.append("update")
        return (101, [])

    def lifecycle():
        calls.append("lifecycle")

    def scan():
        calls.append("scan")

    result = DailyPipeline(
        update_market_data=update,
        run_lifecycle=lifecycle,
        run_scanner=scan,
        get_market_date=lambda: "2026-08-31",
        get_today=lambda: date(2026, 9, 2),
    ).run(skip_update=True)

    assert result.success
    assert calls == ["lifecycle", "scan"]
