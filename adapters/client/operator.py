"""Small Codex-hosted operator facade. It never opens SQLite directly."""

from __future__ import annotations

from typing import Any


MODE_GUIDANCE = {
    "NORMAL": "Core, policy-governed Memory, Trace, and authorized Effects are available.",
    "SAFE": "Memory is read-only; high-risk external Effects and egress are blocked.",
    "STATELESS": "Long-term Memory read/write is disabled; only allowlisted minimal Trace facts persist.",
    "RECOVERY": "v2 Core APIs are isolated; use validated recovery operations only.",
}


class OperatorClient:
    """User-facing command facade that delegates every operation to Runtime."""

    def __init__(self, runtime):
        self.runtime = runtime

    def mode(self) -> dict[str, str]:
        mode = self.runtime.current_mode()["mode"]
        return {"mode": mode, "guidance": MODE_GUIDANCE[mode]}

    def set_mode(self, *, mode: str, command_id: str, grant_id: str, task_id: str, classification_assertion_ref: str) -> dict[str, Any]:
        result = self.runtime.set_mode(command_id=command_id, grant_id=grant_id, task_id=task_id, mode=mode, classification_assertion_ref=classification_assertion_ref)
        return {**result, "guidance": MODE_GUIDANCE[result["mode"]]}

    def inspect_task(self, *, grant_id: str, task_id: str) -> dict[str, Any]:
        return self.runtime.inspect_task(grant_id=grant_id, task_id=task_id)

    def inspect_effect(self, *, grant_id: str, task_id: str, effect_id: str) -> dict[str, Any]:
        result = self.runtime.inspect_effect(grant_id=grant_id, task_id=task_id, effect_id=effect_id)
        result["display_state"] = self._effect_state(result)
        return result

    @staticmethod
    def _effect_state(effect: dict[str, Any]) -> str:
        if effect.get("effect_outcome") == "UNKNOWN":
            return "UNKNOWN — reconcile authoritatively; do not retry commit"
        if effect.get("reconciliation_status") in {"HUMAN_REQUIRED", "BLOCK_AND_ALERT", "EXHAUSTED"}:
            return f"{effect['effect_outcome']} — {effect['reconciliation_status']}"
        return f"{effect.get('effect_outcome')} — {effect.get('reconciliation_status')}"

    def inspect_approval(self, *, grant_id: str, task_id: str, approval_id: str, include_payload_hash: bool = False) -> dict[str, Any]:
        return self.runtime.inspect_approval(grant_id=grant_id, task_id=task_id, approval_id=approval_id, include_payload_hash=include_payload_hash)

    def inspect_object(self, *, grant_id: str, task_id: str, object_id: str, include_integrity_hash: bool = False) -> dict[str, Any]:
        return self.runtime.inspect_object(grant_id=grant_id, task_id=task_id, object_id=object_id, include_integrity_hash=include_integrity_hash)

    def inspect_route(self, *, grant_id: str, task_id: str, route_id: str) -> dict[str, Any]:
        return self.runtime.inspect_route(grant_id=grant_id, task_id=task_id, route_id=route_id)

    def inspect_purge(self, *, grant_id: str, task_id: str, plan_id: str) -> dict[str, Any]:
        result = self.runtime.inspect_purge(grant_id=grant_id, task_id=task_id, plan_id=plan_id)
        result["display_state"] = "PARTIAL — barrier remains active; purge is not complete" if any(
            item["status"] == "PARTIAL" for item in result["executions"]
        ) else "COMPLETED" if result["executions"] and all(
            item["status"] == "COMPLETED" for item in result["executions"]
        ) else "PENDING"
        return result
