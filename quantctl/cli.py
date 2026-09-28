from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from quantctl.commands import doctor, research, status, version
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.safety is not CommandSafety.READ_ONLY:
        raise RuntimeError("quantctl M1 supports READ_ONLY commands only")
    handler: Handler = arguments.handler
    return handler()
