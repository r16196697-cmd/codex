"""Attached-product application boundary for the canonical Skill Registry."""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from kernel.runtime.errors import RuntimeDenied
from kernel.skills.host import CodexAgentSkillsInventoryAdapter
from kernel.skills.service import SkillRegistryService


class OperatorConfirmation(Protocol):
    def confirm_enable(self, summary: dict) -> bool: ...


class TtyOperatorConfirmation:
    """Explicit local confirmation; it does not authenticate a human cryptographically."""

    def confirm_enable(self, summary: dict) -> bool:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeDenied("SKILL_OPERATOR_CONFIRMATION_REQUIRES_TTY")
        print("Enable this Skill revision? Nexus will mark it ENABLED.")
        for label, key in (("Name", "name"), ("Source", "source"), ("Package revision", "package_revision"),
                           ("Task", "task_id"), ("Run", "run_id")):
            print(f"{label}: {summary[key]}")
        return input("Type ENABLE to confirm: ").strip() == "ENABLE"


def default_codex_skill_roots(*, cwd: str | Path | None = None,
                              repo_root: str | Path | None = None,
                              user_root: str | Path | None = None,
                              explicit_import_root: str | Path | None = None) -> dict[str, Path]:
    """Resolve standard, bounded roots for this process; roots are never persisted."""
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    base = Path.cwd() if cwd is None else Path(cwd)
    roots = {
        "REPO": Path(repo_root) if repo_root is not None else base / ".agents" / "skills",
        "USER": Path(user_root) if user_root is not None else codex_home / "skills",
    }
    if explicit_import_root is not None:
        roots["EXPLICIT_IMPORT"] = Path(explicit_import_root)
    return {scope: path.expanduser().resolve() for scope, path in roots.items()}


class SkillApplicationService:
    """Production entrypoints that compose Registry, roots, Host inventory and approval."""

    def __init__(self, *, registry: SkillRegistryService, host_inventory,
                 authority, operator_confirmation: OperatorConfirmation):
        self.registry = registry
        self.host_inventory = host_inventory
        self.authority = authority
        self.operator_confirmation = operator_confirmation

    def register_package(self, **request) -> dict:
        return self.registry.register_package(**request)

    def snapshot(self) -> dict:
        return self.registry.snapshot()

    def discover(self, query: str, *, limit: int = 20) -> dict:
        return self.registry.discover(query, limit=limit)

    def resolve(self, **request) -> dict:
        return self.registry.resolve(**request, host_inventory=self.host_inventory)

    def review(self, *, skill_id: str, next_status: str, task_id: str, run_id: str,
               grant_id: str, command_id: str) -> dict:
        if next_status not in {"DISABLED", "REJECTED"}:
            raise RuntimeDenied("SKILL_OPERATOR_REVIEW_ACTION_INVALID")
        return self.registry.review(skill_id=skill_id, next_status=next_status, task_id=task_id,
                                    run_id=run_id, grant_id=grant_id, command_id=command_id)

    def enable(self, *, skill_id: str, task_id: str, run_id: str,
               grant_id: str, command_id: str) -> dict:
        approval_id = _stable_identity("skill-approval-", command_id)
        replay = self.registry.replay_review_command(
            skill_id=skill_id, next_status="ENABLED", task_id=task_id,
            run_id=run_id, grant_id=grant_id, command_id=command_id,
            approval_id=approval_id,
        )
        if replay is not None:
            return replay

        review_context = self.registry.prepare_review(
            skill_id=skill_id, task_id=task_id, run_id=run_id, grant_id=grant_id)
        entry = review_context["entry"]
        summary = {
            "skill_id": skill_id, "name": entry["name"],
            "source": f"{entry['source_scope']}:{entry['source_namespace']}:{entry['source_ref']}",
            "package_revision": entry["package_manifest_sha256"],
            "task_id": task_id, "run_id": run_id,
        }
        if not self.operator_confirmation.confirm_enable(summary):
            raise RuntimeDenied("SKILL_ENABLE_NOT_CONFIRMED")

        approver = self.authority.active_human_for_grant(review_context["governing_grant_id"])
        review_request = {
            "skill_id": skill_id, "next_status": "ENABLED",
            "package_manifest_sha256": entry["package_manifest_sha256"],
            "approval_id": approval_id, "task_id": task_id, "run_id": run_id,
            "grant_id": review_context["governing_grant_id"],
        }
        payload_hash = self.registry.review_approval_payload_hash(review_request)
        approval = {
            "schema_id": "nexus.approval_decision", "schema_version": 1,
            "approval_id": approval_id, "approver_principal_id": approver,
            "target_type": "OBJECT_WRITE", "target_ref": skill_id,
            "payload_integrity_hash": payload_hash,
            "decision": "APPROVE", "approved_scope": ["OBJECT_WRITE", skill_id],
            "policy_version": self.authority.policy["policy_version"],
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "reason": "Explicit local operator confirmation for exact Skill revision.",
            "request_ref": skill_id,
        }
        self._create_or_reuse_approval(approval, command_id)
        return self.registry.review(skill_id=skill_id, next_status="ENABLED", task_id=task_id,
                                    run_id=run_id, grant_id=grant_id, command_id=command_id,
                                    approval_id=approval_id)

    def _create_or_reuse_approval(self, approval: dict, command_id: str) -> None:
        existing = self.authority.get_approval_metadata(approval["approval_id"])
        if existing:
            expected = {
                "approver_principal_id": approval["approver_principal_id"],
                "target_type": approval["target_type"], "target_ref": approval["target_ref"],
                "effect_id": None, "payload_integrity_hash": approval["payload_integrity_hash"],
                "decision": approval["decision"], "approved_scope": approval["approved_scope"],
                "policy_version": approval["policy_version"],
            }
            if (any(existing.get(key) != value for key, value in expected.items())
                    or existing.get("principal_type") != "HUMAN"):
                raise RuntimeDenied("SKILL_APPROVAL_ID_CONFLICT")
            return
        self.authority.create_approval(approval, _stable_identity("skill-approval-command-", command_id))


def compose_skill_application(*, store, authority, participation,
                              source_roots: dict[str, str | Path] | None = None,
                              host_roots: dict[str, str | Path | list[str | Path] | tuple[str | Path, ...]] | None = None,
                              host_inventory_roots_exhaustive: bool = False,
                              operator_confirmation: OperatorConfirmation | None = None,
                              host_inventory=None) -> SkillApplicationService:
    roots = ({scope: Path(path).expanduser().resolve() for scope, path in source_roots.items()}
             if source_roots is not None else default_codex_skill_roots())
    registry = SkillRegistryService(store=store, authority=authority,
                                    participation=participation, source_roots=roots)
    native = host_inventory or CodexAgentSkillsInventoryAdapter(
        package_reader=registry.inventory_package_metadata,
        roots=host_roots or {scope: path for scope, path in roots.items() if scope in {"REPO", "USER"}},
        roots_are_exhaustive=host_inventory_roots_exhaustive,
    )
    return SkillApplicationService(
        registry=registry, host_inventory=native, authority=authority,
        operator_confirmation=operator_confirmation or TtyOperatorConfirmation(),
    )


def _stable_identity(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()
