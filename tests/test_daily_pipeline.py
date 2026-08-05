from app.daily_pipeline import (
    DailyPipeline,
)


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
