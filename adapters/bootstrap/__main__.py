"""Explicit local operator commands for initializing Nexus instances."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adapters.bootstrap.service import (
    adopt_policy_binding,
    authority_bootstrap,
    initialize_instance,
)
from kernel.object.errors import MigrationError
from kernel.instance_binding import parse_json_object
from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from kernel.object.errors import CommandConflict


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
    except MigrationError as exc:
        # Bootstrap errors are stable reason codes; never print supplied paths.
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except (ApprovalDenied, AuthorizationDenied, InvalidDelegation, CommandConflict) as exc:
        print(json.dumps({"error": str(exc) or type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print(json.dumps({"error": "BOOTSTRAP_INPUT_INVALID"}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
