from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from functools import partial

from quantctl.commands import doctor, operations, research, state, status, version
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

    research_parser = commands.add_parser("research", help="inspect current Quant Lab research")
    research_commands = research_parser.add_subparsers(dest="research_command", required=True)
    research_list = research_commands.add_parser("list", help="list active Quant Lab runners")
    research_list.set_defaults(handler=research.run_list)

    data_parser = commands.add_parser("data", help="inspect market data")
    data_commands = data_parser.add_subparsers(dest="data_command", required=True)
    data_status = data_commands.add_parser("status", help="show read-only market-data facts")
    data_status.set_defaults(handler=operations.run_data_status)

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
    handler: Handler = arguments.handler
    return handler()
