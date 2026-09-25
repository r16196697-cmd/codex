"""Thin, truthful ingress for work performed by the attached Codex Host.

This module records Host-declared receipts through Nexus Core APIs. It does not
invoke a model, plan work, or treat host-declared metadata as platform telemetry.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any
from datetime import datetime, timezone


class CodexHostedBridge:
    VERSION = "0.1"

    def __init__(self, *, store, authority, budget, trace, runtime, verifier):
        self.store = store
        self.authority = authority
        self.budget = budget
        self.trace = trace
        self.runtime = runtime
        self.verifier = verifier

    def create_task_root(
        self,
        *,
        command_id: str,
        task_id: str,
        requester_id: str,
        grant_id: str,
        root_run_id: str,
        budget_account_id: str,
        budget_limits: dict[str, int],
        input_object_id: str,
        input_payload: bytes,
        task_contract: dict[str, Any],
        contract_object_id: str,
        dag_nodes: list[dict[str, Any]],
        root_manifest_object_id: str,
        data_boundary: dict[str, Any],
        classifications: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a Task and active ORCHESTRATOR through the existing Core APIs.

        A caller must first establish an exact trusted Grant and predefine every
        referenced ID/classification. No policy or grant is created implicitly.
        """
        from datetime import datetime, timezone

        chain = self.authority.validate_delegation_chain(grant_id)
        actor = chain[-1]["granted_to"]
        created_at = datetime.now(timezone.utc).isoformat()
        self.trace.create_task({
            "schema_id": "nexus.task", "schema_version": 1, "task_id": task_id,
            "requester_id": requester_id, "status": "CREATED", "created_at": created_at,
            "command_id": command_id + "-task",
        })
        self.budget.create_account(
            command_id=command_id + "-budget-account", account_id=budget_account_id,
            task_id=task_id, **budget_limits,
        )
        self._record_classification(classifications["root_run"], grant_id, task_id, command_id + "-class-root-run")
        self._record_classification(classifications["root_created_event"], grant_id, task_id, command_id + "-class-root-created")
        root_run = {
            "schema_id": "nexus.run", "schema_version": 1, "run_id": root_run_id,
            "task_id": task_id, "executor_kind": "ORCHESTRATOR", "status": "CREATED",
            "grant_id": grant_id, "data_boundary": data_boundary,
            "classification_assertion_ref": classifications["root_run"]["assertion_id"],
            "created_at": created_at,
        }
        self.trace.create_run(root_run, command_id=command_id + "-root-create", event_classification_assertion_ref=classifications["root_created_event"]["assertion_id"])

        self._record_classification(classifications["input_object"], grant_id, task_id, command_id + "-class-input")
        self.authority.evaluate_authorization(grant_id, {"task": task_id, "resource": input_object_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"}, command_id + "-authorize-input")
        self.store.put_object(command_id=command_id + "-put-input", object_id=input_object_id, payload=input_payload, object_type="user_input", created_by_run=root_run_id, classification_assertion_ref=classifications["input_object"]["assertion_id"])
        self._record_classification(classifications["input_event"], grant_id, task_id, command_id + "-class-input-event")
        self.trace.append_trace_event(command_id=command_id + "-trace-input", run_id=root_run_id, event_type="nexus.object.created", classification_assertion_ref=classifications["input_event"]["assertion_id"], typed_metadata={"object_type": "user_input"}, object_refs=[input_object_id])

        contract = {**task_contract, "task_id": task_id, "requester_id": requester_id, "budget_account_ref": budget_account_id, "input_object_refs": [input_object_id]}
        self._record_classification(classifications["task_contract"], grant_id, task_id, command_id + "-class-contract")
        self.runtime.bind_task_contract(command_id=command_id + "-bind-contract", root_run_id=root_run_id, contract_object_id=contract_object_id, classification_assertion_ref=classifications["task_contract"]["assertion_id"], contract=contract)
        self.runtime.create_dag(command_id=command_id + "-create-dag", task_id=task_id, root_run_id=root_run_id, nodes=dag_nodes)

        root_manifest = {
            "schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "ORCHESTRATOR",
            "runtime_version": "0.1", "policy_version": self.authority.policy["policy_version"],
            "schema_versions": {"nexus.run_manifest": 1, "nexus.task_contract": 1},
            "input_object_refs": [input_object_id], "authority_grant_ref": grant_id,
            "data_boundary": data_boundary, "classification_assertion_ref": classifications["root_run"]["assertion_id"],
            "task_contract_ref": contract_object_id, "dag_version": "1", "scheduler_version": "1",
        }
        self._record_classification(classifications["root_manifest"], grant_id, task_id, command_id + "-class-root-manifest")
        self.runtime.bind_manifest(command_id=command_id + "-bind-root-manifest", run_id=root_run_id, manifest_object_id=root_manifest_object_id, manifest_classification_assertion_ref=classifications["root_manifest"]["assertion_id"], manifest=root_manifest)
        self._record_classification(classifications["root_ready_event"], grant_id, task_id, command_id + "-class-root-ready")
        self.trace.transition_run(command_id=command_id + "-root-ready", run_id=root_run_id, expected_state="CREATED", next_state="READY", classification_assertion_ref=classifications["root_ready_event"]["assertion_id"])
        self._record_classification(classifications["root_running_event"], grant_id, task_id, command_id + "-class-root-running")
        self.trace.transition_run(command_id=command_id + "-root-running", run_id=root_run_id, expected_state="READY", next_state="RUNNING", classification_assertion_ref=classifications["root_running_event"]["assertion_id"])
        return {"task_id": task_id, "root_run_id": root_run_id, "executor_kind": "ORCHESTRATOR", "status": "RUNNING", "input_object_id": input_object_id, "task_contract_ref": contract_object_id, "manifest_ref": root_manifest_object_id}

    def _record_classification(self, classification: dict[str, Any], grant_id: str, task_id: str, command_id: str) -> None:
        self.authority.record_classification_assertion(classification, grant_id=grant_id, task_id=task_id, audience="nexus-runtime", command_id=command_id)

    @staticmethod
    def model_manifest(*, common: dict[str, Any], context_object_refs: list[str], adapter_version: str = "0.1") -> dict[str, Any]:
        """Build v2 MODEL metadata without inventing a Provider/model identity."""
        manifest = {
            **common,
            "schema_id": "nexus.run_manifest",
            "schema_version": 2,
            "executor_kind": "MODEL",
            "execution_source": "CODEX_HOST_DECLARED",
            "host_kind": "CODEX",
            "host_adapter_version": adapter_version,
            "model_identity_status": "UNAVAILABLE",
            "context_object_refs": sorted(set(context_object_refs)),
        }
        return manifest

    def create_child_run(
        self,
        *,
        command_id: str,
        run: dict[str, Any],
        manifest: dict[str, Any],
        account_id: str,
        estimated_units: int,
        manifest_object_id: str,
        manifest_classification_assertion_ref: str,
        event_classification_assertion_refs: dict[str, str],
        parent_grant_id: str,
    ) -> dict[str, Any]:
        """Create and start a MODEL/TOOL child using Core-owned state and gates.

        The caller supplies exact pre-authorized run/object/event identities and
        classifications. The Runtime still validates Grant ancestry, boundaries,
        Manifest binding, budget and every state transition.
        """
        kind = run.get("executor_kind")
        if kind not in {"MODEL", "TOOL"} or run.get("parent_run_id") is None:
            raise ValueError("Hosted bridge only records child MODEL or TOOL Runs")
        if manifest.get("executor_kind") != kind:
            raise ValueError("Run and Manifest executor_kind differ")
        if kind == "MODEL" and (manifest.get("execution_source") != "CODEX_HOST_DECLARED" or manifest.get("model_identity_status") != "UNAVAILABLE"):
            raise ValueError("Attached MODEL Run must explicitly declare host execution and unavailable backend identity")

        child_chain = self.authority.validate_delegation_chain(run["grant_id"])
        parent_chain = self.authority.validate_delegation_chain(parent_grant_id)
        if len(child_chain) <= len(parent_chain) or [g["grant_id"] for g in child_chain[:len(parent_chain)]] != [g["grant_id"] for g in parent_chain]:
            raise ValueError("Hosted child Grant must descend from the Root Run Grant")
        self.authority.evaluate_authorization(
            run["grant_id"],
            {"task": run["task_id"], "resource": run["run_id"], "action": "RUN_CREATE", "audience": "nexus-runtime"},
            command_id + "-preauthorize",
        )
        run_probe = {**run, "status": "CREATED"}
        manifest_probe = {**manifest, "budget_reservation_ref": "hosted-reservation-validation-placeholder"}
        self.store._validate("nexus.run@1.schema.json", run_probe)
        self.store._validate(f"nexus.run_manifest@{manifest_probe.get('schema_version')}.schema.json", manifest_probe)

        reservation_id = self.budget.reserve(
            command_id=command_id + "-budget",
            account_id=account_id,
            run_id=run["run_id"],
            amount=estimated_units,
            model_calls=1 if kind == "MODEL" else 0,
            tool_calls=1 if kind == "TOOL" else 0,
            child_runs=1,
        )
        run = {**run, "status": "CREATED", "budget_reservation_ref": reservation_id}
        manifest = {**manifest, "budget_reservation_ref": reservation_id}
        create_command = command_id + "-create-run"
        ready_command = command_id + "-ready"
        running_command = command_id + "-running"
        self.trace.create_run(
            run,
            command_id=create_command,
            event_classification_assertion_ref=event_classification_assertion_refs["create"],
        )
        self.runtime.bind_manifest(
            command_id=command_id + "-bind-manifest",
            run_id=run["run_id"],
            manifest_object_id=manifest_object_id,
            manifest_classification_assertion_ref=manifest_classification_assertion_ref,
            manifest=manifest,
        )
        if run.get("subtask_id"):
            self.runtime.bind_hosted_run_to_subtask(
                command_id=command_id + "-bind-subtask",
                task_id=run["task_id"],
                root_run_id=run["parent_run_id"],
                subtask_id=run["subtask_id"],
                child_run_id=run["run_id"],
            )
        self.trace.transition_run(
            command_id=ready_command,
            run_id=run["run_id"],
            expected_state="CREATED",
            next_state="READY",
            classification_assertion_ref=event_classification_assertion_refs["ready"],
        )
        self.trace.transition_run(
            command_id=running_command,
            run_id=run["run_id"],
            expected_state="READY",
            next_state="RUNNING",
            classification_assertion_ref=event_classification_assertion_refs["running"],
        )
        return {"run_id": run["run_id"], "executor_kind": kind, "status": "RUNNING", "manifest_ref": manifest_object_id, "reservation_ref": reservation_id}

    def record_output(
        self,
        *,
        command_id: str,
        task_id: str,
        run_id: str,
        grant_id: str,
        artifact_id: str,
        payload: bytes,
        classification_assertion_ref: str,
        event_classification_assertion_ref: str,
        verifier_id: str,
    ) -> dict[str, Any]:
        """Persist an output object, append its reference, and run T1 integrity verification."""
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": artifact_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id + "-authorize-object",
        )
        self.store.put_object(
            command_id=command_id + "-put-object",
            object_id=artifact_id,
            payload=payload,
            object_type="artifact",
            created_by_run=run_id,
            classification_assertion_ref=classification_assertion_ref,
        )
        self.trace.append_trace_event(
            command_id=command_id + "-trace-object",
            run_id=run_id,
            event_type="nexus.object.created",
            classification_assertion_ref=event_classification_assertion_ref,
            typed_metadata={"object_type": "artifact"},
            object_refs=[artifact_id],
        )
        verification = self.verifier.verify_object_integrity(
            verification_id=verifier_id,
            target_ref=artifact_id,
            evidence_refs=[artifact_id],
            run_id=run_id,
        )
        return {"artifact_id": artifact_id, "verification": verification}

    def record_evidence(
        self,
        *,
        command_id: str,
        task_id: str,
        run_id: str,
        grant_id: str,
        evidence_id: str,
        source_url: str,
        retrieved_content: bytes,
        classification_assertion_ref: str,
        event_classification_assertion_ref: str,
        verifier_id: str,
    ) -> dict[str, Any]:
        """Persist a source-addressed evidence object and verify its exact bytes."""
        from urllib.parse import urlparse
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Evidence requires an absolute HTTP(S) source URL")
        if len(retrieved_content) > 4096:
            raise ValueError("Hosted Evidence excerpt exceeds the bridge's 4096-byte bound")
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": evidence_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id + "-authorize-evidence",
        )
        receipt = {
            "schema_id": "nexus.hosted_search_evidence",
            "schema_version": 1,
            "execution_source": "CODEX_HOST_DECLARED",
            "source_url": source_url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "excerpt": retrieved_content.decode("utf-8"),
        }
        self.store._validate("nexus.hosted_search_evidence@1.schema.json", receipt)
        payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.store.put_object(
            command_id=command_id + "-put-evidence",
            object_id=evidence_id,
            payload=payload,
            object_type="evidence",
            created_by_run=run_id,
            classification_assertion_ref=classification_assertion_ref,
        )
        self.trace.append_trace_event(
            command_id=command_id + "-trace-evidence",
            run_id=run_id,
            event_type="nexus.object.created",
            classification_assertion_ref=event_classification_assertion_ref,
            typed_metadata={"object_type": "evidence"},
            object_refs=[evidence_id],
        )
        verification = self.verifier.verify_object_integrity(
            verification_id=verifier_id,
            target_ref=evidence_id,
            evidence_refs=[evidence_id],
            run_id=run_id,
        )
        return {"evidence_id": evidence_id, "source_url": source_url, "verification": verification}

    def execute_read_only_file_probe(
        self,
        *,
        grant_id: str,
        task_id: str,
        tool_id: str,
        descriptor: dict[str, Any],
        allowed_root: Path,
        relative_path: str,
    ) -> bytes:
        """Perform one bounded local read after descriptor and Grant checks."""
        if descriptor.get("tool_id") != tool_id or descriptor.get("review_status") != "APPROVED" or descriptor.get("effect_class") != "READ_ONLY" or descriptor.get("network_egress") is not False:
            raise PermissionError("Only an approved, explicitly read-only, non-egress descriptor is accepted")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("Tool path must remain relative to its reviewed root")
        root = allowed_root.resolve(strict=True)
        target = (root / relative).resolve(strict=True)
        if not target.is_file() or not target.is_relative_to(root):
            raise PermissionError("Tool target is not a regular file contained by the reviewed root")
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": tool_id, "action": "TOOL_READ", "audience": "nexus-runtime"},
            "hosted-tool-read-auth-" + hashlib.sha256((task_id + "\0" + tool_id + "\0" + relative.as_posix()).encode()).hexdigest(),
        )
        raw = target.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("Read-only probe is limited to 1 MiB")
        return json.dumps(
            {"relative_path": relative.as_posix(), "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "utf8": raw.decode("utf-8")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def close_child(
        self,
        *,
        command_id: str,
        run_id: str,
        classification_assertion_refs: dict[str, str],
        succeeded: bool,
        reservation_id: str,
        actual_units: int,
    ) -> dict[str, Any]:
        terminal = "SUCCEEDED" if succeeded else "FAILED"
        self.trace.transition_run(
            command_id=command_id + "-verifying",
            run_id=run_id,
            expected_state="RUNNING",
            next_state="VERIFYING",
            classification_assertion_ref=classification_assertion_refs["verifying"],
        )
        result = self.trace.transition_run(
            command_id=command_id + "-terminal",
            run_id=run_id,
            expected_state="VERIFYING",
            next_state=terminal,
            classification_assertion_ref=classification_assertion_refs["terminal"],
        )
        self.budget.settle(command_id=command_id + "-settle-budget", reservation_id=reservation_id, actual_amount=actual_units)
        return result

    @staticmethod
    def tool_manifest(*, common: dict[str, Any], tool_id: str, descriptor_version: str, input_ref: str, adapter_version: str = "0.1") -> dict[str, Any]:
        return {
            **common,
            "schema_id": "nexus.run_manifest",
            "schema_version": 1,
            "executor_kind": "TOOL",
            "tool_id": tool_id,
            "tool_descriptor_version": descriptor_version,
            "tool_adapter_version": adapter_version,
            "input_ref": input_ref,
        }
