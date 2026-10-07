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


def _emit_json_document(document: dict, *, stream=None) -> None:
    """Write machine JSON as UTF-8 when stdout/stderr is redirected.

    Keep the active console encoding for an interactive operator terminal, but
    don't let the Windows host code page corrupt JSON consumed by other tools.
    """
    target = sys.stdout if stream is None else stream
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    buffer = getattr(target, "buffer", None)
    if buffer is not None and not target.isatty():
        buffer.write((text + "\n").encode("utf-8"))
        buffer.flush()
    else:
        print(text, file=target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="Nexus local operator and Agent Host Integration surface")
    parser.add_argument("--data-root", type=Path, help="Existing Nexus data root; the CLI never initializes a database")
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

    mcp = commands.add_parser("mcp", help="Governed local read plane (stdio only)")
    mcp_commands = mcp.add_subparsers(dest="mcp_action", required=True)
    mcp_serve = mcp_commands.add_parser("serve", help="Serve fixed read tools over stdio; explicit consumer profile required for reads")
    mcp_serve.add_argument("--reader-profile", choices=["local-no-egress"],
                           help="Operator no-egress consumer assertion; never use with remote-model Hosts")
    mcp_serve.add_argument("--project-root", type=Path, help="Local operator-selected Project discovery start; defaults to cwd")
    mcp_remote = mcp_commands.add_parser("remote-serve", help="Serve only the exact released Remote Read snapshot over local stdio")
    mcp_remote.add_argument("--project-root", type=Path, help="Project discovery start; defaults to cwd")

    remote_read = commands.add_parser("remote-read", help="Prepare or inspect a HUMAN-released immutable Remote Read snapshot")
    remote_read_commands = remote_read.add_subparsers(dest="remote_read_action", required=True)
    remote_prepare = remote_read_commands.add_parser("prepare", help="Preview and HUMAN-release one exact-hash selected snapshot")
    remote_prepare.add_argument("--project-root", type=Path, help="Project discovery start; defaults to cwd")
    remote_status = remote_read_commands.add_parser("status", help="Show the host-local Remote Read profile state")
    remote_status.add_argument("--project-root", type=Path, help="Project discovery start; defaults to cwd")
    remote_revoke = remote_read_commands.add_parser("revoke", help="Disable the host-local Remote Read profile")
    remote_revoke.add_argument("--project-root", type=Path, help="Project discovery start; defaults to cwd")

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
    task_finish = task_commands.add_parser("finish", help="finish one exact daily Task Root and revoke its original Grant")
    task_finish.add_argument("--plan", required=True, type=Path, help="process-local strict JSON task-finish plan")

    checkpoint = commands.add_parser("checkpoint", help="一次 HUMAN 授权完成项目 Checkpoint")
    checkpoint.add_argument("--proposal", type=Path, help="仅含四个产品语义字段的 UTF-8 JSON")
    checkpoint.add_argument("--accepted-revision", help="已独立接受的 Git revision")
    checkpoint.add_argument("--objective", help="当前 objective（HUMAN assertion）")
    checkpoint.add_argument("--next", dest="next_step", help="下一步（HUMAN assertion）")
    checkpoint.add_argument("--completed", help="本次完成摘要（HUMAN assertion）")
    checkpoint.add_argument("--id", dest="checkpoint_id", help="可选公开 Checkpoint identity；相同请求直接重试")

    continuation = commands.add_parser(
        "continuation", help="commit one HUMAN-authorized continuation state from an active daily Task",
    )
    continuation_commands = continuation.add_subparsers(dest="continuation_action", required=True)
    continuation_commit = continuation_commands.add_parser(
        "commit", help="write Current State + What Changed and compile a fresh Context Pack",
    )
    continuation_commit.add_argument("--plan", required=True, type=Path, help="process-local strict JSON continuation plan")
    continuation_commit.add_argument("--repo", required=True, type=Path,
                                     help="process-local repository used to verify the declared Git fact; never persisted")

    project = commands.add_parser("project", help="locate or attach this repository to an existing host-local Nexus instance")
    project_commands = project.add_subparsers(dest="project_action", required=True)
    project_attach = project_commands.add_parser("attach", help="HUMAN-confirm an existing Nexus instance attachment")
    project_attach.add_argument("--project-root", type=Path, help="repository root (default: current directory)")
    project_attach.add_argument("--project-id", help="portable Nexus project identity")
    project_locate = project_commands.add_parser("locate", help="resolve the nearest Project Manifest and verify its host binding")
    project_locate.add_argument("--project-root", type=Path, help="directory from which to search upward (default: current directory)")
    project_repair = project_commands.add_parser(
        "repair-policy", help="HUMAN-confirm relocation of an attached policy into the durable host policy store",
    )
    project_repair.add_argument("--project-root", type=Path, help="directory from which to find the Project Manifest")

    status = commands.add_parser("status", help="show verified read-only Project Nexus presence and continuity")
    status.add_argument("--json", action="store_true", dest="json_output", help="emit stable machine-readable JSON")
    experience = commands.add_parser("experience", help="show a bounded read-only projection of one governed Task")
    experience.add_argument("task_id", help="exact Nexus Task ID")
    experience.add_argument("--json", action="store_true", dest="json_output", help="emit stable machine-readable JSON")
    continuation_view = commands.add_parser("continue", help="show a compact read-only Project Workspace")
    continuation_view.add_argument("--json", action="store_true", dest="json_output", help="emit stable machine-readable JSON")
    doctor = commands.add_parser("doctor", help="diagnose Project Locator, Nexus and Codex Hook availability")
    doctor.add_argument("--json", action="store_true", dest="json_output", help="emit stable machine-readable JSON")
    hook = commands.add_parser("hook", help=argparse.SUPPRESS)
    hook_commands = hook.add_subparsers(dest="hook_action", required=True)
    hook_commands.add_parser("session-start", help=argparse.SUPPRESS)

    host = commands.add_parser("host", help="manage optional Agent Host lifecycle adapters")
    host_commands = host.add_subparsers(dest="host_action", required=True)
    for action in ("install", "status", "uninstall"):
        host_command = host_commands.add_parser(action)
        host_command.add_argument("host_name", choices=("codex",))
        if action == "status":
            host_command.add_argument("--json", action="store_true", dest="json_output")

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


def _safe_task_finish_error_reason(exc: Exception) -> str:
    from adapters.client.task_finish import DailyTaskFinishError

    reason = getattr(exc, "reason_code", None)
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    if isinstance(exc, DailyTaskFinishError):
        return "DAILY_TASK_FINISH_FAILED"
    return "DAILY_TASK_FINISH_FAILED"


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


def _require_daily_task_finish_tty() -> None:
    from adapters.client.task_finish import DailyTaskFinishError

    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise DailyTaskFinishError("INTERACTIVE_TTY_REQUIRED")
    except DailyTaskFinishError:
        raise
    except Exception:
        raise DailyTaskFinishError("INTERACTIVE_TTY_REQUIRED") from None


def _confirm_daily_task_finish(expected: str, summary: dict[str, Any]) -> bool:
    _require_daily_task_finish_tty()
    print("Confirm finish of this exact daily Task:", file=sys.stdout)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _require_continuation_tty() -> None:
    from adapters.client.continuation import ContinuationCommitError

    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ContinuationCommitError("INTERACTIVE_TTY_REQUIRED")
    except ContinuationCommitError:
        raise
    except Exception:
        raise ContinuationCommitError("INTERACTIVE_TTY_REQUIRED") from None


def _confirm_continuation(expected: str, summary: dict[str, Any]) -> bool:
    _require_continuation_tty()
    print("Confirm this exact continuation commit:", file=sys.stdout)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _require_project_attach_tty() -> None:
    from adapters.client.project_locator import ProjectLocatorError

    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ProjectLocatorError("INTERACTIVE_TTY_REQUIRED")
    except ProjectLocatorError:
        raise
    except Exception:
        raise ProjectLocatorError("INTERACTIVE_TTY_REQUIRED") from None


def _confirm_project_attach(expected: str, summary: dict[str, Any]) -> bool:
    _require_project_attach_tty()
    print("Attach this Project to the verified existing Nexus instance:", file=sys.stdout)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _confirm_project_policy_relocation(expected: str, summary: dict[str, Any]) -> bool:
    _require_project_attach_tty()
    print("Relocate this attachment's verified policy into the durable host policy store:", file=sys.stdout)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _confirm_host_adapter(expected: str, summary: dict[str, Any]) -> bool:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from adapters.client.codex_host import CodexHostError
        raise CodexHostError("INTERACTIVE_TTY_REQUIRED")
    labels = {
        "host": "Host",
        "operation": "操作",
        "target": "配置范围",
        "event": "事件",
        "sources": "来源",
        "command": "命令",
        "read_only": "只读",
    }
    print("确认修改用户级 Codex Hook 配置：", file=sys.stdout)
    for key in ("host", "operation", "target", "event", "sources", "command", "read_only"):
        value = summary.get(key)
        if key == "read_only":
            value = "是" if value else "否"
        print(f"{labels[key]}  {', '.join(value) if isinstance(value, list) else value}", file=sys.stdout)
    try:
        return input(f"Type {expected} to confirm: ") == expected
    except (EOFError, OSError):
        return False


def _render_host_status(result: dict[str, Any]) -> str:
    labels = {
        "NOT_INSTALLED": "未安装",
        "INSTALLED": "已安装",
        "INSTALLED_TRUST_UNKNOWN": "已安装；信任状态未知",
        "CONFLICT": "配置冲突",
        "UNAVAILABLE": "不可读取",
    }
    lines = ["Nexus Host Adapter · Codex", f"状态  {labels.get(result['status'], result['status'])}"]
    if result["status"] == "INSTALLED_TRUST_UNKNOWN":
        lines.append("信任  UNKNOWN（请在 Codex 中运行 /hooks 检查）")
    if result.get("reason"):
        lines.append(f"原因  {result['reason']}")
    return "\n".join(lines)


def _safe_continuation_error_reason(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    if not isinstance(reason, str):
        args = getattr(exc, "args", ())
        reason = args[0] if args else None
    if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
        return reason
    return "CONTINUATION_COMMIT_FAILED"


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


def _confirm_checkpoint(phrase: str, summary: dict) -> bool:
    from adapters.client.checkpoint import CheckpointError
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise CheckpointError("INTERACTIVE_TTY_REQUIRED")
    print("Project Nexus Checkpoint")
    print(f"Current State: r{summary['previous_revision'] or 1} → r{summary['new_revision']}")
    print(f"Accepted Git: {summary['accepted_revision']}")
    print(f"完成摘要: {summary['recent_work']}")
    print(f"当前目标: {summary['current_objective']}")
    print(f"下一步: {summary['next_step']}")
    print("授权范围: 一个 bounded Task；提交 Current State / What Changed / Context；完成 Task 并撤销原 Grant。")
    print("上述工作摘要、目标和下一步为 HUMAN_OPERATOR_ASSERTION；不自动证明工作质量或模型可见性。")
    return input(f"输入 {phrase} 确认: ") == phrase


def _confirm_remote_snapshot(phrase: str, summary: dict) -> bool:
    from adapters.client.remote_read import RemoteReadError
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RemoteReadError("INTERACTIVE_TTY_REQUIRED")
    print("Nexus Remote Read 快照发布")
    print(f"项目: {summary['project_id']}")
    print(f"目标: {summary['target']}")
    print(f"Current State revision: {summary['state_revision']}")
    print(f"Accepted revision: {summary['accepted_revision']}")
    print(f"发布字段: {', '.join(summary['selected_fields'])}")
    print("将发布的实际字段值:")
    print(json.dumps(summary["selected_projection"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"能力: {', '.join(summary['abilities'])}")
    print(f"分类目标: {summary['release_classification']}")
    print(f"快照 SHA-256: {summary['snapshot_content_hash']}")
    print(f"大小: {summary['estimated_bytes']} bytes")
    print("快照文本会作为不可信数据提供；不会建立网络连接或授予写入能力。")
    return input(f"输入 {phrase} 确认: ") == phrase


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = None
    try:
        if args.command == "mcp":
            # MCP stdout belongs exclusively to the official SDK protocol.
            if any(value is not None for value in (args.data_root, args.policy, args.independent_purge_journal)):
                print("READ_PLANE_INVALID_ARGUMENT", file=sys.stderr)
                return 3
            try:
                if args.mcp_action == "remote-serve":
                    from adapters.mcp.remote_server import serve
                    serve(start_dir=args.project_root)
                else:
                    from adapters.mcp.server import serve
                    serve(start_dir=args.project_root, reader_profile=args.reader_profile)
                return 0
            except Exception:
                print("READ_PLANE_UNAVAILABLE", file=sys.stderr)
                return 3
        if args.command == "remote-read":
            if any(value is not None for value in (args.data_root, args.policy, args.independent_purge_journal)):
                from adapters.client.remote_read import RemoteReadError
                raise RemoteReadError("REMOTE_READ_PATH_ARGUMENTS_UNSUPPORTED")
            from adapters.client.remote_read import (
                RemoteReadError, prepare_remote_read, remote_reader_status, revoke_remote_reader,
            )
            if args.remote_read_action == "prepare":
                result = prepare_remote_read(start_dir=args.project_root, confirmation=_confirm_remote_snapshot)
            elif args.remote_read_action == "status":
                result = remote_reader_status(start_dir=args.project_root)
            else:
                result = revoke_remote_reader(start_dir=args.project_root)
            _emit_json_document(result)
            return 0 if result["status"] in {
                "REMOTE_READ_SNAPSHOT_RELEASED", "REMOTE_READ_SNAPSHOT_ALREADY_RELEASED",
                "REMOTE_READER_READ_READY", "REMOTE_READER_PROFILE_ACTIVE",
                "REMOTE_READER_NOT_CONFIGURED", "REMOTE_READER_REVOKED", "REMOTE_READER_EXPIRED",
            } else 3
        if args.command == "checkpoint":
            from adapters.client.checkpoint import CheckpointError, checkpoint_project, read_checkpoint_proposal
            if any(value is not None for value in (args.data_root, args.policy, args.independent_purge_journal)):
                raise CheckpointError("CHECKPOINT_PATH_ARGUMENTS_UNSUPPORTED")
            semantics = (args.accepted_revision, args.objective, args.next_step, args.completed)
            if args.proposal is not None:
                if any(value is not None for value in semantics):
                    raise CheckpointError("CHECKPOINT_PROPOSAL_INVALID")
                proposal = read_checkpoint_proposal(args.proposal)
            else:
                proposal = dict(zip(("accepted_revision", "current_objective", "next_step", "recent_work"), semantics))
            result = checkpoint_project(proposal=proposal, checkpoint_id=args.checkpoint_id,
                confirmation=_confirm_checkpoint)
            _emit_json_document(result)
            return 0 if result["status"] == "CHECKPOINT_COMPLETED" else 3

        if args.command == "hook" and args.hook_action == "session-start":
            # This installed CLI is the Codex Host Adapter entrypoint. It never
            # executes scripts or commands supplied by the current repository.
            try:
                from adapters.client.codex_host import parse_session_start_json, session_start_output
                event = parse_session_start_json(sys.stdin.read(65537))
                context = session_start_output(event)
                if context:
                    print(context)
            except Exception:
                pass
            return 0

        if args.command == "host":
            if args.data_root is not None or args.policy is not None or args.independent_purge_journal is not None:
                from adapters.client.codex_host import CodexHostError
                raise CodexHostError("HOST_COMMAND_PATH_ARGUMENTS_UNSUPPORTED")
            from adapters.client.codex_host import (
                CodexHostError, codex_host_status, install_codex_host, uninstall_codex_host,
            )
            if args.host_action == "status":
                result = codex_host_status()
            elif args.host_action == "install":
                result = install_codex_host(confirmation=_confirm_host_adapter)
            else:
                result = uninstall_codex_host(confirmation=_confirm_host_adapter)
            if getattr(args, "json_output", False):
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif args.host_action == "status":
                print(_render_host_status(result))
            else:
                labels = {
                    "HOST_ADAPTER_INSTALLED": "已安装",
                    "HOST_ADAPTER_ALREADY_INSTALLED": "已安装（无需重写）",
                    "HOST_ADAPTER_UNINSTALLED": "已卸载",
                    "HOST_ADAPTER_NOT_INSTALLED": "未安装（无需修改）",
                }
                print(f"Nexus Host Adapter · Codex  {labels.get(result['status'], result['status'])}（{result['status']}）")
            return 0

        if args.command in {"status", "continue", "doctor"}:
            if args.data_root is not None or args.policy is not None or args.independent_purge_journal is not None:
                from adapters.client.project_locator import ProjectLocatorError
                raise ProjectLocatorError("PRESENCE_PATH_ARGUMENTS_UNSUPPORTED")
            from adapters.client.presence import (
                build_project_workspace, doctor_project, render_continue, render_doctor, render_status,
            )

            if args.command == "doctor":
                result = doctor_project()
                if args.json_output:
                    _emit_json_document(result)
                else:
                    print(render_doctor(result))
                return 0
            result = build_project_workspace()
            if args.json_output:
                _emit_json_document(result)
            else:
                print(render_status(result) if args.command == "status" else render_continue(result))
            return 0

        if args.command == "project":
            from adapters.client.project_locator import attach_project, locate_project, repair_project_policy

            if args.project_action == "locate":
                if args.data_root is not None or args.policy is not None or args.independent_purge_journal is not None:
                    from adapters.client.project_locator import ProjectLocatorError
                    raise ProjectLocatorError("PROJECT_LOCATE_PATH_ARGUMENTS_UNSUPPORTED")
                result = locate_project(start_dir=args.project_root)
            elif args.project_action == "repair-policy":
                if args.data_root is not None or args.policy is not None or args.independent_purge_journal is not None:
                    from adapters.client.project_locator import ProjectLocatorError
                    raise ProjectLocatorError("PROJECT_REPAIR_PATH_ARGUMENTS_UNSUPPORTED")
                result = repair_project_policy(
                    start_dir=args.project_root,
                    confirmation=_confirm_project_policy_relocation,
                )
            else:
                if args.data_root is None:
                    from adapters.client.project_locator import ProjectLocatorError
                    raise ProjectLocatorError("PROJECT_DATA_ROOT_REQUIRED")
                if args.independent_purge_journal is None:
                    from adapters.client.project_locator import ProjectLocatorError
                    raise ProjectLocatorError("PROJECT_JOURNAL_REQUIRED")
                result = attach_project(
                    project_root=args.project_root or Path.cwd(),
                    project_id=args.project_id,
                    data_root=args.data_root,
                    policy_path=args.policy,
                    independent_purge_journal_path=args.independent_purge_journal,
                    confirmation=_confirm_project_attach,
                )
            _emit_json_document(result)
            return 0

        if args.data_root is None:
            from adapters.client.project_locator import ProjectLocatorError, resolve_project_runtime

            if (
                args.policy is not None
                or args.independent_purge_journal is not None
                or (args.command == "recovery" and args.purge_ledger is not None)
            ):
                raise ProjectLocatorError("PROJECT_LOCATOR_PATHS_REQUIRE_DATA_ROOT")
            resolved = resolve_project_runtime()
            args.data_root = Path(resolved["data_root"])
            args.policy = Path(resolved["policy_path"]) if resolved["policy_path"] is not None else None
            args.independent_purge_journal = Path(resolved["independent_purge_journal_path"])

        task_start_plan = None
        if args.command == "task" and args.task_action == "start":
            from adapters.client.task_start import read_task_start_plan
            task_start_plan = read_task_start_plan(args.plan)
            # Refuse non-interactive starts before composing the ordinary
            # writer store, whose startup path may perform writer-side work.
            _require_daily_task_start_tty()
        task_finish_plan = None
        if args.command == "task" and args.task_action == "finish":
            from adapters.client.task_finish import read_task_finish_plan
            task_finish_plan = read_task_finish_plan(args.plan)
            # Refuse non-interactive finishes before composing the ordinary
            # writer store. The application skips phrase entry on exact replay.
            _require_daily_task_finish_tty()
        if args.command == "continuation" and args.continuation_action == "commit":
            from adapters.client.continuation import (
                ContinuationCommitError, commit_continuation, prepare_continuation_commit,
                read_continuation_plan,
            )
            from adapters.panel.application import open_panel_application
            from kernel.authority import AuthorityService

            plan = read_continuation_plan(args.plan)
            read_app = open_panel_application(
                args.data_root, policy_path=args.policy,
                independent_purge_journal_path=args.independent_purge_journal,
                read_only=True,
            )
            try:
                read_authority = AuthorityService(read_app.store, read_app.store.policy)
                preflight = prepare_continuation_commit(
                    store=read_app.store, authority=read_authority,
                    context_packs=read_app.context_packs, plan=plan, git_repo=args.repo,
                )
            finally:
                read_app.close()
            human_confirmed = False
            if not preflight["request_bound"]:
                if not _confirm_continuation(preflight["confirmation_phrase"], preflight["summary"]):
                    raise ContinuationCommitError("CONTINUATION_CONFIRMATION_DENIED")
                human_confirmed = True

            store, authority, _budget, _trace, _runtime_instance = _runtime(
                args.data_root, args.policy, independent_purge_journal_path=args.independent_purge_journal,
            )
            from kernel.context import ContextPackService
            from kernel.metering import MeteringService

            participation = ParticipationModeService(store)
            memory = MemoryService(store, authority, verifier=None)
            context_packs = ContextPackService(
                store=store, authority=authority, participation=participation,
                memory=memory, metering=MeteringService(store, authority, participation),
            )
            result = commit_continuation(
                store=store, authority=authority, context_packs=context_packs,
                plan=plan, git_repo=args.repo,
                confirmation=lambda *_: human_confirmed,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
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
            _emit_json_document(result)
            return 0
        if args.command == "experience":
            from adapters.panel.application import open_panel_application
            from kernel.experience import ExperienceProjectionService, LocalOperatorReadContext

            # Experience is deliberately composed over an already verified
            # read-only instance. Explicit --data-root remains supported; when
            # omitted, normal Project Locator resolution above supplies paths.
            application = open_panel_application(
                args.data_root, policy_path=args.policy,
                independent_purge_journal_path=args.independent_purge_journal,
                read_only=True,
            )
            try:
                # This CLI is an explicit local-operator surface: the host OS
                # user may inspect the verified instance. Task execution Grants
                # do not authorize reads. Remote/MCP adapters must inject their
                # own authenticated task reader context.
                projection = ExperienceProjectionService(
                    application.store, read_context=LocalOperatorReadContext(),
                    context_packs=application.context_packs,
                ).project_task(args.task_id)
            finally:
                application.close()
            if args.json_output:
                _emit_json_document(projection)
            else:
                from kernel.experience.service import render_experience
                print(render_experience(projection))
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
        elif args.command == "task" and args.task_action == "finish":
            from adapters.client.task_finish import finish_daily_task
            result = finish_daily_task(
                store=store, authority=authority, trace=trace, plan=task_finish_plan,
                confirmation=_confirm_daily_task_finish,
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
        if args.command in {"status", "continue", "doctor"}:
            reason = _safe_context_read_error_reason(exc)
            if getattr(args, "json_output", False):
                print(json.dumps({"status": "DENIED_OR_FAILED", "reason": reason}, ensure_ascii=False), file=sys.stderr)
            else:
                label = {"status": "状态", "continue": "Continue", "doctor": "Doctor"}.get(args.command, "Nexus")
                print(f"Nexus {label} 无法完成：{reason}", file=sys.stderr)
            return 2
        elif args.command == "host":
            reason = _safe_context_read_error_reason(exc)
            print(json.dumps({"status": "DENIED_OR_FAILED", "reason": reason}, ensure_ascii=False), file=sys.stderr)
            return 2
        elif args.command == "checkpoint":
            reason = _safe_context_read_error_reason(exc)
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason) is None:
                reason = "CHECKPOINT_FAILED"
        elif args.command == "project":
            reason = _safe_context_read_error_reason(exc)
        elif args.data_root is None:
            reason = _safe_context_read_error_reason(exc)
        elif args.command == "import-git-source":
            reason = _safe_import_error_reason(exc)
        elif args.command == "continuation":
            reason = _safe_continuation_error_reason(exc)
        elif args.command == "project-nexus-selfhost":
            reason = _safe_stage3_error_reason(exc)
        elif args.command == "context":
            reason = _safe_context_read_error_reason(exc)
        elif args.command == "experience":
            reason = _safe_context_read_error_reason(exc)
        elif args.command == "task" and args.task_action == "finish":
            reason = _safe_task_finish_error_reason(exc)
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
