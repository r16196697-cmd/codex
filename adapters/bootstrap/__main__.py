"""Explicit local operator commands for initializing Nexus instances."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from adapters.bootstrap.service import (
    adopt_policy_binding,
    authority_bootstrap,
    initialize_instance,
)
from kernel.instance_binding import parse_json_object
from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from kernel.object.errors import NexusStoreError


_SAFE_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}(?::[A-Z][A-Z0-9_]{0,95}(?:,[A-Z][A-Z0-9_]{0,95})*)?$")


def _sanitized_error_code(exc: Exception) -> str:
    message = str(exc)
    if len(message) <= 192 and _SAFE_REASON_CODE.fullmatch(message):
        return message
    return type(exc).__name__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m adapters.bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("initialize-instance")
    _common_roots(initialize)
    initialize.add_argument("--command-id", required=True)

    adoption = commands.add_parser("adopt-policy-binding")
    _common_roots(adoption)
    adoption.add_argument("--command-id", required=True)

    authority = commands.add_parser("authority-bootstrap")
    _common_roots(authority)
    authority.add_argument("--plan", required=True, type=Path,
                           help="Local JSON file containing the exact normalized bootstrap plan")
    return parser


def _common_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--independent-purge-journal", required=True, type=Path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "initialize-instance":
            result = initialize_instance(
                data_root=args.data_root,
                policy_path=args.policy,
                independent_purge_journal=args.independent_purge_journal,
                command_id=args.command_id,
            )
        elif args.command == "adopt-policy-binding":
            result = adopt_policy_binding(
                data_root=args.data_root,
                policy_path=args.policy,
                independent_purge_journal=args.independent_purge_journal,
                command_id=args.command_id,
            )
        else:
            plan = parse_json_object(args.plan.read_bytes())
            result = authority_bootstrap(
                data_root=args.data_root,
                policy_path=args.policy,
                independent_purge_journal=args.independent_purge_journal,
                plan=plan,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (NexusStoreError, ApprovalDenied, AuthorizationDenied, InvalidDelegation) as exc:
        # Never expose arbitrary exception prose, reprs, causes, or tracebacks.
        print(json.dumps({"error": _sanitized_error_code(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print(json.dumps({"error": "BOOTSTRAP_INPUT_INVALID"}, sort_keys=True), file=sys.stderr)
        return 2
    except Exception:
        # Keep unforeseen implementation failures private at the CLI boundary.
        print(json.dumps({"error": "BOOTSTRAP_INTERNAL_ERROR"}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
