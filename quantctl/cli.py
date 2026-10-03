from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from functools import partial

from quantctl import cafef_monitor
from quantctl.commands import doctor, history, operations, research, state, status, version
from quantctl.registry import CommandSafety


Handler = Callable[[], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantctl", description="Read-only quant-stock management CLI")
    parser.set_defaults(safety=CommandSafety.READ_ONLY)
    commands = parser.add_subparsers(dest="command", required=True)

    version_parser = commands.add_parser("version", help="show repository and manager identity")
    version_parser.set_defaults(handler=version.run)

    status_parser = commands.add_parser("status", help="show a concise system snapshot")
    status_parser.set_defaults(handler=status.run)

    doctor_parser = commands.add_parser("doctor", help="run read-only diagnostics")
    doctor_parser.set_defaults(handler=doctor.run)

    history_parser = commands.add_parser("history", help="inspect operational run history")
    history_parser.add_argument("--limit", type=int, default=20)
    history_commands = history_parser.add_subparsers(dest="history_command")
    history_show = history_commands.add_parser("show", help="show one operational run")
    history_show.add_argument("run_id")

    research_parser = commands.add_parser("research", help="inspect current Quant Lab research")
    research_commands = research_parser.add_subparsers(dest="research_command", required=True)
    research_list = research_commands.add_parser("list", help="list active Quant Lab runners")
    research_list.set_defaults(handler=research.run_list)
    research_status = research_commands.add_parser("status", help="show canonical research decisions")
    research_status.set_defaults(handler=research.run_status)

    data_parser = commands.add_parser("data", help="inspect market data")
    data_commands = data_parser.add_subparsers(dest="data_command", required=True)
    data_status = data_commands.add_parser("status", help="show read-only market-data facts")
    data_status.set_defaults(handler=operations.run_data_status)
    data_monitor = data_commands.add_parser(
        "cafef-monitor", help="monitor retained CafeF RAW archives without database writes"
    )
    data_monitor.add_argument("--archive", type=str, required=True)
    data_monitor.add_argument("--session", type=str, required=True)
    data_monitor.add_argument("--expected-sha256", required=True)
    data_monitor.add_argument("--previous-archive", type=str)
    data_monitor.add_argument("--previous-session", type=str)
    data_monitor.add_argument("--previous-expected-sha256")
    data_monitor.add_argument("--canonical-database", default="data/market.db")
    data_monitor.add_argument("--output-directory", required=True)
    data_monitor.add_argument("--expected-universe-size", type=int, default=100)
    data_monitor.add_argument("--price-threshold-pct", default="5")
    data_monitor.add_argument("--volume-multiple", default="5")

    for name, help_text in (
        ("update", "run the canonical market-data updater"),
        ("scan", "run the canonical scanner"),
        ("daily", "run the canonical daily pipeline"),
    ):
        operation_parser = commands.add_parser(name, help=help_text)
        operation_parser.set_defaults(handler=partial(operations.run_named, name))

    paper_parser = commands.add_parser("paper", help="inspect paper-trading state")
    paper_commands = paper_parser.add_subparsers(dest="paper_command", required=True)
    paper_status = paper_commands.add_parser("status", help="show read-only paper state")
    paper_status.set_defaults(handler=state.run_paper_status)

    forward_parser = commands.add_parser("forward", help="inspect Forward validation state")
    forward_commands = forward_parser.add_subparsers(dest="forward_command", required=True)
    forward_status = forward_commands.add_parser("status", help="show read-only Forward state")
    forward_status.set_defaults(handler=state.run_forward_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "data" and arguments.data_command == "cafef-monitor":
        from datetime import date
        from decimal import Decimal

        report = cafef_monitor.build_monitoring_report(
            archive_path=arguments.archive,
            session=date.fromisoformat(arguments.session),
            expected_sha256=arguments.expected_sha256,
            canonical_database=arguments.canonical_database,
            previous_archive_path=arguments.previous_archive,
            previous_session=(
                None if arguments.previous_session is None
                else date.fromisoformat(arguments.previous_session)
            ),
            previous_expected_sha256=arguments.previous_expected_sha256,
            expected_universe_size=arguments.expected_universe_size,
            price_threshold_pct=Decimal(arguments.price_threshold_pct),
            volume_multiple=Decimal(arguments.volume_multiple),
        )
        output = cafef_monitor.write_monitoring_report(report, arguments.output_directory)
        print(output)
        return 0
    if arguments.command == "history":
        if arguments.history_command == "show":
            return history.run_show(arguments.run_id)
        return history.run_list(limit=arguments.limit)
    handler: Handler = arguments.handler
    return handler()
