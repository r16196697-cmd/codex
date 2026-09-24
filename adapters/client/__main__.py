"""Small local operator CLI over Runtime APIs (never queries SQLite itself)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.memory.service import MemoryService
from kernel.purge.service import PurgeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="Codex-hosted Nexus local operator surface")
    parser.add_argument("--data-root", required=True, type=Path, help="Existing Nexus data root; the CLI never initializes a database")
    commands = parser.add_subparsers(dest="command", required=True)

    mode = commands.add_parser("mode")
    mode_commands = mode.add_subparsers(dest="mode_action", required=True)
    mode_commands.add_parser("show")
    set_mode = mode_commands.add_parser("set")
    set_mode.add_argument("mode", choices=("NORMAL", "SAFE", "STATELESS", "RECOVERY"))
    set_mode.add_argument("--command-id", required=True)
    set_mode.add_argument("--grant-id", required=True)
    set_mode.add_argument("--task-id", required=True)
    set_mode.add_argument("--classification-assertion-ref", required=True, help="pre-authorized TRACE_EVENT assertion bound to evt-<command-id>")

    inspect = commands.add_parser("inspect")
    inspect_commands = inspect.add_subparsers(dest="inspect_kind", required=True)
    for kind, identifier in (("task", "task-id"), ("effect", "effect-id"), ("approval", "approval-id"), ("object", "object-id"), ("route", "route-id"), ("purge", "plan-id")):
        item = inspect_commands.add_parser(kind)
        item.add_argument(identifier.replace("-", "_"))
        item.add_argument("--grant-id", required=True)
        if kind != "task":
            item.add_argument("--task-id", required=True)
        if kind == "approval":
            item.add_argument("--show-payload-hash", action="store_true", help="requires the separate INSPECT_PROTECTED grant")
        if kind == "object":
            item.add_argument("--show-integrity-hash", action="store_true", help="requires the separate INSPECT_PROTECTED grant")

    recovery = commands.add_parser("recovery")
    recovery_commands = recovery.add_subparsers(dest="recovery_action", required=True)
    complete = recovery_commands.add_parser("complete")
    complete.add_argument("--command-id", required=True)
    complete.add_argument("--purge-ledger", required=True, type=Path, help="independent Purge Ledger path outside --data-root")
    return parser


def _runtime(data_root: Path):
    root = data_root.expanduser().resolve()
    if not (root / "nexus.sqlite").is_file():
        raise ValueError("No existing Nexus database at --data-root; use the separately reviewed initialization procedure.")
    store = ObjectStore(root)
    authority = AuthorityService(store, store.policy)
    budget = BudgetService(store)
    trace = TraceRuntime(store, authority)
    return store, authority, budget, trace, DeterministicRuntime(store, authority, budget, trace)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = None
    try:
        store, authority, _budget, _trace, runtime = _runtime(args.data_root)
        client = OperatorClient(runtime)
        if args.command == "mode" and args.mode_action == "show":
            result = client.mode()
        elif args.command == "mode":
            result = client.set_mode(mode=args.mode, command_id=args.command_id, grant_id=args.grant_id, task_id=args.task_id, classification_assertion_ref=args.classification_assertion_ref)
        elif args.command == "inspect":
            common = {"grant_id": args.grant_id}
            if args.inspect_kind == "task":
                result = client.inspect_task(**common, task_id=args.task_id)
            elif args.inspect_kind == "effect":
                result = client.inspect_effect(**common, task_id=args.task_id, effect_id=args.effect_id)
            elif args.inspect_kind == "approval":
                result = client.inspect_approval(**common, task_id=args.task_id, approval_id=args.approval_id, include_payload_hash=args.show_payload_hash)
            elif args.inspect_kind == "object":
                result = client.inspect_object(**common, task_id=args.task_id, object_id=args.object_id, include_integrity_hash=args.show_integrity_hash)
            elif args.inspect_kind == "route":
                result = client.inspect_route(**common, task_id=args.task_id, route_id=args.route_id)
            else:
                result = client.inspect_purge(**common, task_id=args.task_id, plan_id=args.plan_id)
        else:
            memory = MemoryService(store, authority, verifier=None)
            purge = PurgeService(store, authority, memory, independent_journal_path=args.purge_ledger)
            result = runtime.complete_validated_recovery(command_id=args.command_id, purge_service=purge)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        code = getattr(exc, "args", [None])[0]
        print(json.dumps({"status": "DENIED_OR_FAILED", "reason": str(code or type(exc).__name__)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
