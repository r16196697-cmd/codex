"""Small local operator CLI over Runtime APIs (never queries SQLite itself)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.memory.service import MemoryService
from kernel.participation import ParticipationModeService
from kernel.purge.service import PurgeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.skills import compose_skill_application, default_codex_skill_roots


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="Codex-hosted Nexus local operator surface")
    parser.add_argument("--data-root", required=True, type=Path, help="Existing Nexus data root; the CLI never initializes a database")
    parser.add_argument("--policy", type=Path, help="Optional existing nexus.policy@1 JSON file; read-only and schema-validated")
    parser.add_argument("--independent-purge-journal", type=Path, help="Configured independent Purge Journal; defaults to NEXUS_INDEPENDENT_PURGE_JOURNAL or a sibling of --data-root")
    parser.add_argument("--repo-skill-root", type=Path, default=Path(".agents/skills"),
                        help="Configured project Agent Skills root (default: .agents/skills in the current directory)")
    parser.add_argument("--codex-repo-skill-root", type=Path, default=Path(".codex/skills"),
                        help="Project Codex-native Agent Skills root used for Host inventory (default: .codex/skills)")
    parser.add_argument("--user-skill-root", type=Path,
                        help="Optional configured user Agent Skills root (default: $CODEX_HOME/skills or ~/.codex/skills)")
    parser.add_argument("--explicit-import-root", type=Path,
                        help="Process-local root for explicitly imported Skill packages; never persisted")
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
    open_recovery = recovery_commands.add_parser("open")
    open_recovery.add_argument("--purge-ledger", required=True, type=Path, help="independent Purge Ledger path outside --data-root")
    complete = recovery_commands.add_parser("complete")
    complete.add_argument("--command-id", required=True)
    complete.add_argument("--purge-ledger", required=True, type=Path, help="independent Purge Ledger path outside --data-root")

    context = commands.add_parser("context", help="read an already compiled Context Pack through the read-only operator surface")
    context_commands = context.add_subparsers(dest="context_action", required=True)
    context_read = context_commands.add_parser("read", help="return one validated compiled Context Pack")
    context_target = context_read.add_mutually_exclusive_group(required=True)
    context_target.add_argument("--pack-ref")
    context_target.add_argument("--latest", action="store_true")

    skill = commands.add_parser("skill", help="governed Agent Skills registration, review, and resolution")
    skill_commands = skill.add_subparsers(dest="skill_action", required=True)
    register = skill_commands.add_parser("register", help="register one explicit package; registration does not enable it")
    register.add_argument("--source-scope", required=True, choices=("REPO", "USER", "EXPLICIT_IMPORT"))
    register.add_argument("--source-namespace", required=True)
    register.add_argument("--source-ref", required=True, help="relative package directory under the configured root")
    register.add_argument("--task-id", required=True)
    register.add_argument("--run-id", required=True)
    register.add_argument("--grant-id", required=True)
    register.add_argument("--classification-assertion-ref", required=True)
    register.add_argument("--command-id", required=True)

    for action in ("enable", "disable", "reject"):
        review = skill_commands.add_parser(action, help=f"operator review: {action} one exact registered Skill")
        review.add_argument("skill_id")
        review.add_argument("--task-id", required=True)
        review.add_argument("--run-id", required=True)
        review.add_argument("--grant-id", required=True)
        review.add_argument("--command-id", required=True)

    resolve = skill_commands.add_parser("resolve", help="resolve using positive Codex inventory evidence and governed Nexus fallback")
    resolve.add_argument("query")
    resolve.add_argument("--task-id", required=True)
    resolve.add_argument("--run-id", required=True)
    resolve.add_argument("--grant-id", required=True)
    resolve.add_argument("--classification-assertion-ref", required=True)
    resolve.add_argument("--command-id", required=True)
    discover = skill_commands.add_parser("discover", help="search safe Registry metadata only")
    discover.add_argument("query")

    git_source = commands.add_parser("import-git-source", help="import one exact local Git blob as governed Artifact and Evidence")
    git_source.add_argument("--repo", required=True, type=Path, help="process-local path to an existing local Git worktree; never persisted")
    git_source.add_argument("--plan", required=True, type=Path, help="process-local JSON import plan with logical IDs and exact commit/path")

    task = commands.add_parser("task", help="explicit operator actions for an existing Nexus instance")
    task_commands = task.add_subparsers(dest="task_action", required=True)
    task_start = task_commands.add_parser("start", help="authorize and start one exact daily Task Root")
    task_start.add_argument("--plan", required=True, type=Path, help="process-local strict JSON task-start plan")

    selfhost = commands.add_parser("project-nexus-selfhost", help="run/replay the frozen Project Nexus Stage 3 application plan")
    selfhost.add_argument("--repo", required=True, type=Path, help="process-local path to the frozen local source repository")
    selfhost.add_argument("--plan", required=True, type=Path, help="process-local strict Stage 3 v2 manifest")

    commands.add_parser("panel", help="open the native panel inside this writer-owned process")
    return parser


def _strict_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("GIT_SOURCE_PLAN_INVALID")
        result[key] = value
    return result


def _read_git_source_plan(path: Path) -> dict:
    from adapters.source.git import GitSourceImportError

    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"), object_pairs_hook=_strict_object_pairs)
    except Exception:
        raise GitSourceImportError("GIT_SOURCE_PLAN_INVALID") from None
    if not isinstance(value, dict):
        raise GitSourceImportError("GIT_SOURCE_PLAN_INVALID")
    return value


def _safe_import_error_reason(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    if not isinstance(reason, str):
        args = getattr(exc, "args", ())
        reason = args[0] if args else None
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    return type(exc).__name__


def _safe_stage3_error_reason(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    if not isinstance(reason, str):
        args = getattr(exc, "args", ())
        reason = args[0] if args else None
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    return type(exc).__name__


def _safe_context_read_error_reason(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    if not isinstance(reason, str):
        args = getattr(exc, "args", ())
        reason = args[0] if args else None
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    return type(exc).__name__


def _safe_task_start_error_reason(exc: Exception) -> str:
    from adapters.client.task_start import DailyTaskStartError

    reason = getattr(exc, "reason_code", None)
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    if isinstance(exc, DailyTaskStartError):
        return "DAILY_TASK_START_FAILED"
    return "DAILY_TASK_START_FAILED"


def _require_daily_task_start_tty() -> None:
    from adapters.client.task_start import DailyTaskStartError

    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise DailyTaskStartError("INTERACTIVE_TTY_REQUIRED")
    except DailyTaskStartError:
        raise
    except Exception:
        raise DailyTaskStartError("INTERACTIVE_TTY_REQUIRED") from None


def _confirm_daily_task_start(expected: str, summary: dict[str, Any]) -> bool:
    _require_daily_task_start_tty()
    print("Authorize this exact daily Task start:", file=sys.stdout)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _runtime(data_root: Path, policy_path: Path | None = None, *, force_recovery: bool = False, independent_purge_journal_path: Path | None = None):
    root = data_root.expanduser().resolve()
    if not (root / "nexus.sqlite").is_file():
        raise ValueError("No existing Nexus database at --data-root; use the separately reviewed initialization procedure.")
    policy = json.loads(policy_path.expanduser().resolve(strict=True).read_text(encoding="utf-8")) if policy_path else None
    store = ObjectStore(root, policy=policy, force_recovery=force_recovery, independent_purge_journal_path=independent_purge_journal_path)
    authority = AuthorityService(store, store.policy)
    budget = BudgetService(store)
    trace = TraceRuntime(store, authority)
    return store, authority, budget, trace, DeterministicRuntime(store, authority, budget, trace)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = None
    try:
        task_start_plan = None
        if args.command == "task" and args.task_action == "start":
            from adapters.client.task_start import read_task_start_plan
            task_start_plan = read_task_start_plan(args.plan)
            # Refuse non-interactive starts before composing the ordinary
            # writer store, whose startup path may perform writer-side work.
            _require_daily_task_start_tty()
        if args.command == "context" and args.context_action == "read":
            from adapters.panel.application import open_panel_application

            application = open_panel_application(
                args.data_root,
                policy_path=args.policy,
                independent_purge_journal_path=args.independent_purge_journal,
                read_only=True,
            )
            try:
                result = (application.context_packs.read_latest_compiled() if args.latest
                          else application.context_packs.read_compiled(args.pack_ref))
            finally:
                application.close()
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        force_recovery = args.command == "recovery" and args.recovery_action == "open"
        journal_path = getattr(args, "purge_ledger", None) or args.independent_purge_journal
        store, authority, _budget, trace, runtime = _runtime(args.data_root, args.policy, force_recovery=force_recovery, independent_purge_journal_path=journal_path)
        participation = None
        skill_application = None
        if args.command in {"skill", "panel"}:
            participation = ParticipationModeService(store)
            roots = default_codex_skill_roots(
                repo_root=args.repo_skill_root, user_root=args.user_skill_root,
                explicit_import_root=args.explicit_import_root,
            )
            skill_application = compose_skill_application(
                store=store, authority=authority, participation=participation, source_roots=roots,
                host_roots={
                    "REPO": [roots["REPO"], args.codex_repo_skill_root.expanduser().resolve()],
                    "USER": roots["USER"],
                },
            )
        if args.command == "panel":
            from adapters.panel.application import open_panel_application
            from adapters.panel.ui import launch_panel
            from kernel.context import ContextPackService
            from kernel.metering import MeteringService
            from kernel.runtime.panel import PanelQueryService

            memory = MemoryService(store, authority, verifier=None)
            metering = MeteringService(store, authority, participation)
            writer_services = {
                "store": store,
                "runtime": runtime,
                "participation": participation,
                "panel_queries": PanelQueryService(store),
                "memory": memory,
                "context_packs": ContextPackService(
                    store=store, authority=authority, participation=participation,
                    memory=memory, metering=metering,
                ),
                "metering": metering,
                "skills": skill_application,
            }
            application = open_panel_application(args.data_root, writer_services=writer_services)
            try:
                launch_panel(application.view_model)
            finally:
                application.close()
            return 0

        client = OperatorClient(runtime)
        if args.command == "import-git-source":
            from adapters.source.git import import_git_source
            result = import_git_source(
                store=store, authority=authority, trace=trace,
                repository_path=args.repo, plan=_read_git_source_plan(args.plan),
            )
        elif args.command == "project-nexus-selfhost":
            from adapters.client.project_nexus_selfhost import read_stage3_manifest, run_project_nexus_selfhost_bootstrap
            from kernel.context import ContextPackService
            from kernel.metering import MeteringService
            from kernel.verification import VerificationService

            verifier = VerificationService(store, authority)
            memory = MemoryService(store, authority, verifier)
            participation = ParticipationModeService(store)
            context_packs = ContextPackService(
                store=store, authority=authority, participation=participation, memory=memory,
                metering=MeteringService(store, authority, participation),
            )
            result = run_project_nexus_selfhost_bootstrap(
                store=store, authority=authority, budget=_budget, trace=trace, runtime=runtime,
                verifier=verifier, memory=memory, context_packs=context_packs,
                repo_path=args.repo, manifest=read_stage3_manifest(args.plan),
            )
        elif args.command == "task" and args.task_action == "start":
            from adapters.client.task_start import start_daily_task
            from kernel.verification import VerificationService

            verifier = VerificationService(store, authority)
            result = start_daily_task(
                store=store, authority=authority, budget=_budget, trace=trace, runtime=runtime,
                verifier=verifier, plan=task_start_plan,
                confirmation=_confirm_daily_task_start,
            )
        elif args.command == "skill" and args.skill_action == "register":
            result = skill_application.register_package(
                source_scope=args.source_scope, source_namespace=args.source_namespace,
                source_ref=args.source_ref, task_id=args.task_id, run_id=args.run_id,
                grant_id=args.grant_id, classification_assertion_ref=args.classification_assertion_ref,
                command_id=args.command_id,
            )
        elif args.command == "skill" and args.skill_action in {"enable", "disable", "reject"}:
            if args.skill_action == "enable":
                result = skill_application.enable(skill_id=args.skill_id, task_id=args.task_id,
                    run_id=args.run_id, grant_id=args.grant_id, command_id=args.command_id)
            else:
                result = skill_application.review(skill_id=args.skill_id,
                    next_status="DISABLED" if args.skill_action == "disable" else "REJECTED",
                    task_id=args.task_id, run_id=args.run_id, grant_id=args.grant_id,
                    command_id=args.command_id)
        elif args.command == "skill" and args.skill_action == "resolve":
            result = skill_application.resolve(query=args.query, task_id=args.task_id, run_id=args.run_id,
                grant_id=args.grant_id, classification_assertion_ref=args.classification_assertion_ref,
                command_id=args.command_id)
        elif args.command == "skill" and args.skill_action == "discover":
            result = skill_application.discover(args.query)
        elif args.command == "mode" and args.mode_action == "show":
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
        elif args.command == "recovery" and args.recovery_action == "open":
            memory = MemoryService(store, authority, verifier=None)
            purge = PurgeService(store, authority, memory, independent_journal_path=args.purge_ledger)
            result = {"status": "RECOVERY_OPEN", "journal_replay": purge.replay_independent_journal()}
        else:
            memory = MemoryService(store, authority, verifier=None)
            purge = PurgeService(store, authority, memory, independent_journal_path=args.purge_ledger)
            result = runtime.complete_validated_recovery(command_id=args.command_id, purge_service=purge)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        if args.command == "import-git-source":
            reason = _safe_import_error_reason(exc)
        elif args.command == "project-nexus-selfhost":
            reason = _safe_stage3_error_reason(exc)
        elif args.command == "context":
            reason = _safe_context_read_error_reason(exc)
        elif args.command == "task":
            reason = _safe_task_start_error_reason(exc)
        else:
            reason = str(getattr(exc, "args", [None])[0] or type(exc).__name__)
        print(json.dumps({"status": "DENIED_OR_FAILED", "reason": reason}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
