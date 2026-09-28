from __future__ import annotations

import sys

from quantctl.operations import (
    OperationCapability,
    execute_operation,
    get_operation,
)


def _print_banner(name: str) -> None:
    spec = get_operation(name)
    if spec.capabilities == (OperationCapability.READ_ONLY,):
        return
    print("QUANT OPERATION")
    print("")
    print(f"Name: {spec.name}")
    print("Capabilities:")
    for capability in spec.capabilities:
        print(f"  {capability.value}")
    print("")


def run_data_status() -> int:
    result = execute_operation("data-status")
    details = result.details
    print("MARKET DATA STATUS")
    print("")
    print(f"Database: {details['database']}")
    print(f"Latest session: {details['latest_session'] or 'UNKNOWN'}")
    print(f"Sessions: {details['session_count'] if details['session_count'] is not None else 'UNKNOWN'}")
    print(f"Symbols: {details['symbol_count'] if details['symbol_count'] is not None else 'UNKNOWN'}")
    print(
        "Latest-session symbols: "
        + str(details["latest_session_symbol_count"] if details["latest_session_symbol_count"] is not None else "UNKNOWN")
    )
    return result.exit_code


def run_named(name: str) -> int:
    _print_banner(name)
    result = execute_operation(name)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    print(result.message)
    return result.exit_code
