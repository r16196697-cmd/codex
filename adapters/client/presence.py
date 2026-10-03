"""Read-only project presence and compact continuation workspace for Hosts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adapters.client.project_locator import (
    ProjectLocatorError,
    find_project_manifest,
    locate_project,
    read_host_registry,
    resolve_project_registry_path,
)


WORKSPACE_PROTOCOL = "nexus.project_workspace@1"
_TERMINAL_TASK_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_ACTIVE_RUN_STATES = {"CREATED", "READY", "RUNNING", "WAITING", "VERIFYING"}
_WORKSPACE_TOKEN_LIMIT = 1450


def _short(value: Any, limit: int = 320) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def _read_continuity_documents(pack: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, str]]:
    states = []
    deltas = []
    refs: dict[str, str] = {}
    if not isinstance(pack, dict):
        return None, None, refs
    for entry in pack.get("entries", []):
        try:
            document = json.loads(entry["content"], object_pairs_hook=_unique_pairs,
                                  parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        schema_id = document.get("schema_id")
        if schema_id == "nexus.continuation_state":
            if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
                continue
            states.append((document, entry))
        elif schema_id == "nexus.what_changed":
            if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
                continue
            deltas.append((document, entry))
    state = states[0][0] if len(states) == 1 else None
    delta = deltas[0][0] if len(deltas) == 1 else None
    if state is not None:
        refs["current_state"] = states[0][1]["source_ref"]
        refs["current_state_hash"] = states[0][1]["source_integrity_sha256"]
    if delta is not None:
        refs["what_changed"] = deltas[0][1]["source_ref"]
        refs["what_changed_hash"] = deltas[0][1]["source_integrity_sha256"]
    if state is not None and delta is not None:
        delta_state = delta.get("new_state")
        if not isinstance(delta_state, dict) or delta_state.get("object_id") != refs.get("current_state"):
            delta = None
            refs.pop("what_changed", None)
            refs.pop("what_changed_hash", None)
    return state, delta, refs


def _provenance_kinds(state: dict[str, Any], fact: str) -> list[str]:
    provenance = state.get("provenance_by_fact")
    items = provenance.get(fact) if isinstance(provenance, dict) else None
    if not isinstance(items, list):
        return []
    return sorted({item["kind"] for item in items if isinstance(item, dict) and isinstance(item.get("kind"), str)})


def _last_completed(state: dict[str, Any] | None, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if isinstance(state, dict):
        recent = state.get("recent_work")
        if isinstance(recent, list) and recent:
            item = recent[-1]
            if isinstance(item, dict):
                return {
                    "summary": _short(item.get("summary")),
                    "task_id": item.get("task_id") if isinstance(item.get("task_id"), str) else None,
                    "root_run_id": item.get("root_run_id") if isinstance(item.get("root_run_id"), str) else None,
                    "outcome": item.get("outcome") if isinstance(item.get("outcome"), str) else "UNKNOWN",
                    "outcome_source": _provenance_kinds(state, "recent_work"),
                }
    terminal = [item for item in tasks if isinstance(item, dict) and item.get("status") in _TERMINAL_TASK_STATES]
    if len(terminal) != 1:
        return None
    task = terminal[0]
    root_run_id = task.get("root_run_id")
    runs = task.get("runs") if isinstance(task.get("runs"), list) else []
    root_run = next((run for run in runs if isinstance(run, dict) and run.get("run_id") == root_run_id), None)
    return {
        "summary": None,
        "task_id": task.get("task_id"),
        "root_run_id": root_run_id,
        "outcome": task.get("status", "UNKNOWN"),
        "run_status": root_run.get("status") if isinstance(root_run, dict) else "UNKNOWN",
        "outcome_source": ["NEXUS_LIFECYCLE_FACT"],
    }


def _current_work(snapshot: dict[str, Any]) -> dict[str, Any]:
    overview = snapshot.get("overview") or {}
    active_count = overview.get("active_run_count")
    unfinished_count = overview.get("unfinished_task_count")
    active_tasks = [
        task for task in snapshot.get("tasks", [])
        if isinstance(task, dict)
        and (task.get("status") not in _TERMINAL_TASK_STATES
             or any(isinstance(run, dict) and run.get("status") in _ACTIVE_RUN_STATES
                    for run in task.get("runs", [])))
    ]
    if active_count == 0 and unfinished_count == 0:
        return {"status": "IDLE", "task": None}
    selected = active_tasks[0] if active_tasks else None
    if selected is None:
        return {"status": "UNKNOWN", "task": None}
    active_run = next((run for run in selected.get("runs", [])
                       if isinstance(run, dict) and run.get("status") in _ACTIVE_RUN_STATES), None)
    return {
        "status": "ACTIVE",
        "task": {
            "task_id": selected.get("task_id"), "task_status": selected.get("status"),
            "root_run_id": selected.get("root_run_id"),
            "run_status": active_run.get("status") if active_run else "UNKNOWN",
            "executor_kind": active_run.get("executor_kind") if active_run else "UNKNOWN",
        },
    }


def _current_state(state: dict[str, Any] | None, state_ref: str | None, state_hash: str | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {"status": "UNAVAILABLE"}
    accepted_git = state.get("accepted_git") if isinstance(state.get("accepted_git"), dict) else {}
    constraint_value = state.get("critical_constraints", state.get("current_constraints"))
    constraints = (
        [item for item in (_short(value, 200) for value in constraint_value) if item][:5]
        if isinstance(constraint_value, list) else None
    )
    return {
        "status": "AVAILABLE",
        "ref": state_ref,
        "integrity_sha256": state_hash,
        "revision": state.get("state_revision"),
        "as_of": state.get("as_of"),
        "accepted_revision": state.get("accepted_revision") or accepted_git.get("commit_sha"),
        "objective": _short(state.get("current_objective") or state.get("objective")),
        "next_step": _short(
            state.get("current_operating_priority") or state.get("next_step") or state.get("next_product_priority"),
            600,
        ),
        "critical_constraints": constraints,
        "supersedes": state.get("supersedes") if isinstance(state.get("supersedes"), dict) else None,
        "fact_sources": {
            key: _provenance_kinds(state, key) for key in
            ("accepted_revision", "current_objective", "current_operating_priority", "recent_work")
            if _provenance_kinds(state, key)
        },
    }


def _what_changed(delta: dict[str, Any] | None, ref: str | None, digest: str | None) -> dict[str, Any]:
    if not isinstance(delta, dict):
        return {"status": "UNAVAILABLE"}
    def transition(value):
        if not isinstance(value, dict):
            return None
        return {key: _short(value.get(key), 160) for key in ("before", "after")}

    recent = delta.get("recent_work_added")
    if isinstance(recent, dict):
        recent = {
            key: (_short(recent.get(key), 200) if key == "summary" else recent.get(key))
            for key in ("summary", "task_id", "root_run_id", "outcome", "outcome_source")
            if recent.get(key) is not None
        }
    else:
        recent = None
    def state_ref(value):
        if not isinstance(value, dict):
            return None
        return {
            key: value[key] for key in ("ref_id", "object_id", "integrity_sha256", "context_pack_ref")
            if isinstance(value.get(key), str)
        }

    return {
        "status": "AVAILABLE", "ref": ref, "integrity_sha256": digest,
        "previous_state": state_ref(delta.get("previous_state")),
        "new_state": state_ref(delta.get("new_state")),
        "accepted_revision": transition(delta.get("accepted_revision")),
        "objective": transition(delta.get("objective")),
        "next_step": transition(delta.get("next_step")),
        "recent_work_added": recent,
        "facts_superseded": delta.get("facts_superseded", [])[:6]
        if isinstance(delta.get("facts_superseded"), list) else [],
        "responsible_task": {
            key: delta["responsible_task"].get(key)
            for key in ("task_id", "root_run_id")
            if isinstance(delta.get("responsible_task"), dict)
            and isinstance(delta["responsible_task"].get(key), str)
        },
    }


def _workspace_token_estimate(workspace: dict[str, Any]) -> int:
    """Deterministic conservative size estimate: non-ASCII=2, ASCII=1/4."""
    text = json.dumps(workspace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ascii_count = sum(character.isascii() for character in text)
    non_ascii_count = len(text) - ascii_count
    return (ascii_count + 3) // 4 + 2 * non_ascii_count


def _fit_workspace_budget(workspace: dict[str, Any]) -> dict[str, Any]:
    """Trim only optional history/provenance, preserving current state and next step."""
    while _workspace_token_estimate(workspace) > _WORKSPACE_TOKEN_LIMIT:
        constraints = workspace["current_state"].get("critical_constraints", [])
        refs = workspace.get("available_refs", [])
        recent = workspace["current_state"].get("recent_work", [])
        superseded = workspace["what_changed"].get("facts_superseded", [])
        if constraints:
            constraints.pop()
        elif len(refs) > 4:
            refs.pop()
        elif len(recent) > 1:
            recent.pop(0)
        elif superseded:
            superseded.pop()
        else:
            # Long operator-authored text stays semantically intact where possible,
            # but is clipped deterministically to retain the workspace hard bound.
            candidates: list[tuple[int, dict[str, Any], str, str]] = []

            def add_candidate(container: Any, field: str) -> None:
                value = container.get(field) if isinstance(container, dict) else None
                if isinstance(value, str) and len(value) > 80:
                    candidates.append((len(value), container, field, value))

            state = workspace["current_state"]
            delta = workspace["what_changed"]
            for item in recent:
                add_candidate(item, "summary")
            add_candidate(workspace.get("last_completed"), "summary")
            for field in ("before", "after"):
                add_candidate(delta.get("objective"), field)
                add_candidate(delta.get("next_step"), field)
            if not candidates:
                # Required IDs and fixed projection keys alone exceed the budget.
                raise ProjectLocatorError("PROJECT_WORKSPACE_BUDGET_EXCEEDED")
            previous_values = [
                item for item in candidates
                if item[1] in (delta.get("objective"), delta.get("next_step")) and item[2] == "before"
            ]
            _, container, field, longest = max(previous_values or candidates, key=lambda item: item[0])
            shortened = _short(longest, max(80, len(longest) - 40))
            container[field] = shortened
    return workspace


def build_project_workspace(
    *, start_dir: str | Path | None = None, locator=None, app_opener=None,
) -> dict[str, Any]:
    """Resolve and verify one attached Project, then build a bounded workspace.

    The only semantic source is the latest verified compiled Context Pack. Panel
    data contributes runtime/lifecycle metadata; arbitrary Object payloads are
    never browsed. This function does not write Nexus state.
    """
    selected_locator = locator or locate_project
    resolved = selected_locator(start_dir=start_dir)
    if app_opener is None:
        from adapters.panel.application import open_panel_application
        app_opener = open_panel_application
    application = app_opener(
        resolved["data_root"], policy_path=resolved["policy_path"],
        independent_purge_journal_path=resolved["independent_purge_journal_path"],
        read_only=True,
    )
    try:
        snapshot = application.view_model.snapshot()
        context_meta = application.context_packs.latest()
        pack = None
        if context_meta.get("status") == "PACK_COMPILED" and isinstance(context_meta.get("pack_id"), str):
            pack = application.context_packs.read_compiled(context_meta["pack_id"])
        state_doc, delta_doc, entry_refs = _read_continuity_documents(pack)
    finally:
        application.close()

    state_ref = entry_refs.get("current_state")
    state_hash = entry_refs.get("current_state_hash")
    delta_ref = entry_refs.get("what_changed")
    delta_hash = entry_refs.get("what_changed_hash")
    tasks = snapshot.get("tasks", []) if isinstance(snapshot.get("tasks"), list) else []
    overview = snapshot.get("overview") if isinstance(snapshot.get("overview"), dict) else {}
    skills = snapshot.get("skill_status") if isinstance(snapshot.get("skill_status"), dict) else {}
    current_state = _current_state(state_doc, state_ref, state_hash)
    what_changed = _what_changed(delta_doc, delta_ref, delta_hash)
    last_completed = _last_completed(state_doc, tasks)
    if isinstance(delta_doc, dict):
        transitions = (
            ("accepted_revision", "accepted_revision"),
            ("objective", "objective"),
            ("next_step", "next_step"),
        )
        for delta_field, state_field in transitions:
            transition = what_changed.get(delta_field)
            if (isinstance(transition, dict)
                    and isinstance(transition.get("after"), str)
                    and transition["after"] == current_state.get(state_field)):
                transition.pop("after")
                transition["after_ref"] = f"current_state.{state_field}"
        added_work = what_changed.get("recent_work_added")
        if (isinstance(added_work, dict) and isinstance(last_completed, dict)
                and isinstance(added_work.get("summary"), str)
                and added_work["summary"] == last_completed.get("summary")):
            added_work.pop("summary")
            added_work["summary_ref"] = "last_completed.summary"
    context_available = isinstance(pack, dict)
    context_id = context_meta.get("pack_id") if context_available else None
    context = {
        "status": "READY" if context_available else "UNAVAILABLE",
        "pack_id": context_id,
        "content_hash": context_meta.get("content_hash") if context_available else None,
        "integrity_hash": context_meta.get("integrity_hash") if context_available else None,
        "serialized_byte_size": context_meta.get("serialized_byte_size") if context_available else None,
        "model_visible_exposure": (
            pack.get("model_visible_exposure", "UNKNOWN") if context_available else "UNKNOWN"
        ),
        "compiled_at": context_meta.get("compiled_at") if context_available else None,
    }
    available_refs = []
    if context_available:
        available_refs = [
            entry["source_ref"] for entry in pack.get("entries", [])
            if isinstance(entry, dict) and isinstance(entry.get("source_ref"), str)
        ][:10]
    result = {
        "status": "PROJECT_WORKSPACE",
        "protocol_version": WORKSPACE_PROTOCOL,
        "project": {"project_id": resolved["project_id"]},
        "instance": {
            "verification": "VERIFIED", "instance_id": resolved["instance_id"],
            "policy_version": resolved["policy_version"],
            "policy_sha256": resolved["policy_sha256"],
            "journal_identity": resolved["journal_identity"],
        },
        "runtime": {
            "mode": snapshot.get("runtime_mode", "UNKNOWN"),
            "participation_mode": snapshot.get("participation_mode", "UNKNOWN"),
        },
        "current_work": _current_work(snapshot),
        "overview": {
            key: overview.get(key) for key in
            ("task_count", "recorded_run_count", "unfinished_task_count", "active_run_count", "pending_effect_count")
        },
        "skills": {
            "status": skills.get("status", "UNKNOWN"),
            "registered_count": skills.get("registered_count"),
            "eligible_count": skills.get("eligible_count"),
        },
        "current_state": current_state,
        "what_changed": what_changed,
        "last_completed": last_completed,
        "references": {
            key: entry_refs.get(key) for key in
            ("current_state", "what_changed") if entry_refs.get(key)
        },
        "available_refs": available_refs,
        "context": context,
        "freshness": {
            "state_as_of": current_state.get("as_of"),
            "context_compiled_at": context.get("compiled_at"),
            "basis": "VERIFIED_LATEST_CONTEXT_PACK",
        },
    }
    return _fit_workspace_budget(result)


def render_status(workspace: dict[str, Any]) -> str:
    state = workspace["current_state"]
    last = workspace.get("last_completed") or {}
    context = workspace["context"]
    work = workspace["current_work"]
    runtime_labels = {"NORMAL": "正常（NORMAL）", "SAFE": "安全（SAFE）",
                      "STATELESS": "无状态（STATELESS）", "RECOVERY": "恢复模式（RECOVERY）"}
    participation_labels = {"ACTIVE": "启用（ACTIVE）", "OBSERVE": "观察（OBSERVE）",
                            "BYPASS": "旁路（BYPASS）"}
    work_labels = {"IDLE": "空闲", "ACTIVE": "进行中"}
    revision = state.get("revision")
    state_revision = f"r{revision}" if isinstance(revision, int) else "UNAVAILABLE"
    return "\n".join((
        "Nexus 状态",
        f"项目              {workspace['project']['project_id']}",
        "连接状态          已连接",
        f"实例              已验证（{workspace['instance']['instance_id']}）",
        f"运行模式          {runtime_labels.get(workspace['runtime']['mode'], workspace['runtime']['mode'])}",
        f"参与模式          {participation_labels.get(workspace['runtime']['participation_mode'], workspace['runtime']['participation_mode'])}",
        f"当前工作          {work_labels.get(work['status'], work['status'])}",
        f"Current State     {state_revision} · {state.get('accepted_revision') or 'UNAVAILABLE'}",
        f"上次完成          {last.get('summary') or last.get('task_id') or 'UNAVAILABLE'}",
        f"下一步            {state.get('next_step') or 'UNAVAILABLE'}",
        f"Context           {'已准备' if context['status'] == 'READY' else 'UNAVAILABLE'}",
        f"模型可见性        {context.get('model_visible_exposure', 'UNKNOWN')}",
    ))


def render_continue(workspace: dict[str, Any]) -> str:
    state = workspace["current_state"]
    changed = workspace["what_changed"]
    last = workspace.get("last_completed") or {}
    context = workspace["context"]
    lines = [f"Nexus 继续 · {workspace['project']['project_id']}"]
    state_revision = f"r{state['revision']}" if isinstance(state.get("revision"), int) else "UNAVAILABLE"
    lines.append(f"Current State: {state_revision} · accepted {state.get('accepted_revision') or 'UNAVAILABLE'}")
    lines.append(f"当前目标: {state.get('objective') or 'UNAVAILABLE'}")
    lines.append(f"下一步: {state.get('next_step') or 'UNAVAILABLE'}")
    lines.append(f"最近完成: {last.get('summary') or last.get('task_id') or 'UNAVAILABLE'}")
    work = workspace["current_work"]
    lines.append(f"当前工作: {'空闲' if work['status'] == 'IDLE' else '进行中' if work['status'] == 'ACTIVE' else work['status']}")
    if changed.get("status") == "AVAILABLE":
        old_state = (changed.get("previous_state") or {}).get("object_id")
        new_state = (changed.get("new_state") or {}).get("object_id")
        lines.append(f"Current State: {old_state or 'UNKNOWN'} → {new_state or 'UNKNOWN'}")
        accepted_change = changed.get("accepted_revision") or {}
        after_revision = accepted_change.get("after")
        if after_revision is None and accepted_change.get("after_ref") == "current_state.accepted_revision":
            after_revision = state.get("accepted_revision")
        lines.append(f"最近变化: accepted revision → {after_revision or 'UNKNOWN'}")
        objective = changed.get("objective") or {}
        objective_after = objective.get("after")
        if objective_after is None and objective.get("after_ref") == "current_state.objective":
            objective_after = state.get("objective")
        if objective.get("before") is not None or objective_after is not None:
            lines.append(f"Objective: {objective.get('before') or 'UNKNOWN'} → {objective_after or 'UNKNOWN'}")
        next_step = changed.get("next_step") or {}
        next_after = next_step.get("after")
        if next_after is None and next_step.get("after_ref") == "current_state.next_step":
            next_after = state.get("next_step")
        if next_step.get("before") is not None or next_after is not None:
            lines.append(f"Next: {next_step.get('before') or 'UNKNOWN'} → {next_after or 'UNKNOWN'}")
        lines.append(f"已 supersede: {old_state or 'UNKNOWN'}")
    lines.append(f"Context: {context['status']} · {context.get('pack_id') or 'UNAVAILABLE'}")
    lines.append(f"模型可见性: {context.get('model_visible_exposure', 'UNKNOWN')}")
    refs = workspace.get("references", {})
    if refs:
        lines.append("References: " + ", ".join(f"{key}={value}" for key, value in sorted(refs.items())))
    return "\n".join(lines)


def _doctor_check(name: str, status: str, detail: str, remediation: str | None = None) -> dict[str, str]:
    result = {"name": name, "status": status, "detail": detail}
    if remediation:
        result["remediation"] = remediation
    return result


def doctor_project(*, start_dir: str | Path | None = None,
                   host_hooks_path: str | Path | None = None) -> dict[str, Any]:
    """Read-only diagnostics. Host Hook trust has no supported observable API."""
    from kernel.instance_binding import parse_json_object, policy_sha256

    checks: list[dict[str, str]] = []
    manifest = None
    try:
        manifest = find_project_manifest(start_dir)
        checks.append(_doctor_check("project_manifest", "PASS", "Strict manifest found."))
    except Exception as exc:
        reason = getattr(exc, "reason_code", "PROJECT_NOT_ATTACHED")
        checks.append(_doctor_check("project_manifest", "FAIL", reason, "进入带有有效 .nexus/project.json 的项目目录。"))

    workspace = None
    if manifest is not None:
        try:
            registry = read_host_registry()
            checks.append(_doctor_check("host_registry", "PASS", "Host registry is readable."))
            binding = registry["projects"].get(manifest.project_id)
            if binding is None:
                checks.append(_doctor_check("project_binding", "FAIL", "PROJECT_HOST_BINDING_MISSING", "运行 `nexus project attach` 建立首次绑定。"))
            else:
                checks.append(_doctor_check("project_binding", "PASS", "Project binding found."))
                if binding["policy_path"] is None:
                    checks.append(_doctor_check("managed_policy", "PASS", "Default-policy binding is configured."))
                else:
                    policy_path = Path(binding["policy_path"])
                    try:
                        if policy_path.is_symlink() or not policy_path.is_file():
                            raise ValueError
                        policy = parse_json_object(policy_path.read_bytes())
                        if policy_sha256(policy) != binding["policy_sha256"]:
                            raise ValueError
                        registry_path = resolve_project_registry_path()
                        expected_path = registry_path.parent / "policies-v1" / f"{binding['policy_sha256']}.json"
                        if policy_path.resolve(strict=True) != expected_path.resolve(strict=False):
                            checks.append(_doctor_check(
                                "managed_policy", "WARN",
                                "Policy content is valid but still depends on a non-managed source path.",
                                "运行 `nexus project repair-policy` 将 policy 安全迁移到 host-local managed store。",
                            ))
                        else:
                            checks.append(_doctor_check("managed_policy", "PASS", "Managed policy content is available and matches its canonical digest."))
                    except Exception:
                        checks.append(_doctor_check("managed_policy", "FAIL", "Registered policy is missing or invalid.", "按受审查的 policy relocation 流程修复 host-local binding。"))
                root = Path(binding["data_root"])
                checks.append(_doctor_check(
                    "data_root", "PASS" if root.is_dir() and (root / "nexus.sqlite").is_file() else "FAIL",
                    "Existing Nexus data root is available." if root.is_dir() and (root / "nexus.sqlite").is_file() else "Existing Nexus data root or database is unavailable.",
                ))
                journal = Path(binding["independent_purge_journal_path"])
                checks.append(_doctor_check(
                    "purge_journal", "PASS" if journal.is_file() else "FAIL",
                    "Registered independent Purge Journal is available." if journal.is_file() else "Registered independent Purge Journal is unavailable.",
                ))
                try:
                    workspace = build_project_workspace(start_dir=manifest.project_root)
                    checks.append(_doctor_check("instance_verification", "PASS", "Read-only instance verification succeeded."))
                    state_status = workspace["current_state"]["status"]
                    checks.append(_doctor_check(
                        "current_state", "PASS" if state_status == "AVAILABLE" else "WARN",
                        "Current State is available from verified Context." if state_status == "AVAILABLE" else "Current State is unavailable in the verified Context.",
                    ))
                    context_status = workspace["context"]["status"]
                    checks.append(_doctor_check(
                        "latest_context", "PASS" if context_status == "READY" else "WARN",
                        "Latest compiled Context is readable." if context_status == "READY" else "Latest compiled Context is unavailable.",
                    ))
                    if workspace["what_changed"]["status"] == "AVAILABLE":
                        checks.append(_doctor_check("latest_what_changed", "PASS", "What Changed is available."))
                    else:
                        checks.append(_doctor_check("latest_what_changed", "WARN", "What Changed is not represented in the latest Context."))
                except Exception as exc:
                    reason = getattr(exc, "reason_code", "PROJECT_INSTANCE_UNAVAILABLE")
                    checks.append(_doctor_check("instance_verification", "FAIL", reason, "检查 registry 中的 instance binding、托管 policy 和 Purge Journal。"))
        except Exception as exc:
            reason = getattr(exc, "reason_code", "PROJECT_REGISTRY_UNAVAILABLE")
            checks.append(_doctor_check("host_registry", "FAIL", reason, "检查 NEXUS_PROJECT_REGISTRY 或 host-local registry。"))

    from adapters.client.codex_host import codex_host_status, codex_launcher_available

    host_status = codex_host_status(config_path=host_hooks_path)
    if host_status["status"] == "INSTALLED_TRUST_UNKNOWN":
        checks.append(_doctor_check(
            "session_start_hook", "PASS", "User-level Codex SessionStart registration is installed.",
        ))
        checks.append(_doctor_check(
            "hook_trust", "UNKNOWN", "Codex Hook trust is not observable through a supported API.",
            "在 Codex 中运行 `/hooks` 检查并按需信任 Nexus Host Adapter。",
        ))
    elif host_status["status"] == "NOT_INSTALLED":
        checks.append(_doctor_check(
            "session_start_hook", "WARN", "User-level Codex SessionStart adapter is not installed.",
            "运行 `nexus host install codex`，然后在 Codex 的 `/hooks` 中检查并信任它。",
        ))
    elif host_status["status"] == "CONFLICT":
        checks.append(_doctor_check(
            "session_start_hook", "FAIL", "Codex Hook configuration has a conflicting Nexus registration.",
            "检查用户级 Codex hooks.json；保留无关 Hook，并仅手动解决 Nexus 注册冲突。",
        ))
    else:
        checks.append(_doctor_check(
            "session_start_hook", "UNKNOWN", "User-level Codex Hook configuration is unavailable.",
            "检查 CODEX_HOME 与用户级 hooks.json 的可读性。",
        ))
    launcher_available = codex_launcher_available()
    checks.append(_doctor_check(
        "launcher", "PASS" if launcher_available else "WARN",
        "A nexus console launcher is installed for this user." if launcher_available else "The nexus console launcher was not found in the current environment.",
        None if launcher_available else "在项目 Python 环境执行 `python -m pip install -e .`，并确保 Scripts 目录在 PATH。",
    ))

    severity = {"FAIL": 3, "WARN": 2, "UNKNOWN": 1, "PASS": 0}
    status = max((item["status"] for item in checks), key=lambda value: severity[value]) if checks else "UNKNOWN"
    return {
        "status": "NEXUS_DOCTOR", "project_id": manifest.project_id if manifest else None,
        "checks": checks,
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def render_doctor(report: dict[str, Any]) -> str:
    lines = ["Nexus 诊断（Doctor）"]
    names = {
        "project_manifest": "Project Manifest", "host_registry": "Host Registry",
        "project_binding": "Project binding", "managed_policy": "托管 Policy",
        "data_root": "Nexus data root", "purge_journal": "Purge Journal",
        "instance_verification": "Instance 验证", "current_state": "Current State",
        "latest_context": "Latest Context", "latest_what_changed": "What Changed",
        "session_start_hook": "SessionStart Hook", "hook_trust": "Hook trust",
        "launcher": "nexus Launcher",
    }
    for item in report["checks"]:
        lines.append(f"{names.get(item['name'], item['name'])}  {item['status']}")
        if item["status"] != "PASS":
            lines.append(f"  {item['detail']}")
            if item.get("remediation"):
                lines.append(f"  建议：{item['remediation']}")
    return "\n".join(lines)
