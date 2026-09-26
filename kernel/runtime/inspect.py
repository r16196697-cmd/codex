"""Authorized, read-only projections for the local operator/client surface."""

from __future__ import annotations

import json
import uuid
from typing import Any

from kernel.runtime.errors import RuntimeDenied
from kernel.object_refs import resolve_governed_object_resource
from kernel.runtime.modes import RuntimeModeService


class InspectService:
    """Expose bounded metadata without giving clients a SQLite handle.

    Every projection is authorized against an exact resource and the stored
    Run classification/data boundary. Payload bytes are never returned here.
    """

    def __init__(self, store, authority):
        self.store = store
        self.authority = authority
        self.modes = RuntimeModeService(store, authority)

    def _authorize(self, grant_id: str, task_id: str, resource: str, action: str = "INSPECT") -> None:
        self.modes.require("inspect")
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": resource, "action": action, "audience": "nexus-inspect"},
            "inspect-denial-" + uuid.uuid4().hex,
        )

    @staticmethod
    def _classification_visible(row, boundary=None) -> bool:
        boundary = boundary or json.loads(row["data_boundary_json"])
        level = row["sensitivity_level"]
        tags = set(json.loads(row["handling_tags_json"]))
        return level in set(boundary["allowed_classifications"]) and tags.issubset(set(boundary["handling_tags"]))

    def _task_context(self, task_id: str):
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT t.task_id,t.status,t.created_at,t.root_run_id,r.data_boundary_json,"
                "c.sensitivity_level,c.handling_tags_json "
                "FROM tasks t LEFT JOIN runs r ON r.run_id=t.root_run_id "
                "LEFT JOIN classification_assertions c ON c.assertion_id=r.classification_assertion_ref "
                "WHERE t.task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        if not row["root_run_id"] or not row["data_boundary_json"] or not row["sensitivity_level"]:
            raise RuntimeDenied("INSPECT_CLASSIFICATION_UNAVAILABLE")
        if not self._classification_visible(row):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        return row

    @staticmethod
    def _object_task_owner(conn, object_id: str, direct_task_id: str | None, *, purged: bool) -> str:
        owners = []
        if direct_task_id is not None:
            owners.append(direct_task_id)
        direct = conn.execute(
            "SELECT r.task_id FROM object_envelopes e "
            "LEFT JOIN runs r ON r.run_id=e.created_by_run WHERE e.object_id=?",
            (object_id,),
        ).fetchall()
        if any(row["task_id"] is None for row in direct):
            raise RuntimeDenied("INSPECT_OBJECT_TASK_UNRESOLVED")
        owners.extend(row["task_id"] for row in direct)
        provenance = conn.execute(
            "SELECT DISTINCT pp.task_id FROM purge_execution_refs pr "
            "JOIN purge_execution_records pe USING(record_id) "
            "JOIN purge_plan_records pp USING(plan_id) WHERE pr.object_id=?",
            (object_id,),
        ).fetchall()
        barrier_provenance = conn.execute(
            "SELECT DISTINCT b.task_id FROM purge_barrier_refs br JOIN purge_barriers b USING(barrier_id) "
            "WHERE br.object_id=? AND b.task_id IS NOT NULL",
            (object_id,),
        ).fetchall()
        provenance = list(provenance) + list(barrier_provenance)
        if purged and not provenance:
            raise RuntimeDenied("INSPECT_OBJECT_TASK_UNRESOLVED")
        if not purged and not direct:
            raise RuntimeDenied("INSPECT_OBJECT_TASK_UNRESOLVED")
        if any(row["task_id"] is None for row in provenance):
            raise RuntimeDenied("INSPECT_OBJECT_TASK_UNRESOLVED")
        owners.extend(row["task_id"] for row in provenance)
        unique = set(owners)
        if len(unique) != 1:
            raise RuntimeDenied("INSPECT_OBJECT_TASK_UNRESOLVED")
        return next(iter(unique))

    def _is_purged_object(self, object_id: str | None) -> bool:
        if not object_id:
            return False
        with self.store._connection() as conn:
            resolved = resolve_governed_object_resource(conn, object_id)
            if resolved is None:
                return False
            row = conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (resolved,)).fetchone()
        return bool(row and row["payload_state"] == "PURGED")

    def task(self, *, grant_id: str, task_id: str) -> dict[str, Any]:
        self._authorize(grant_id, task_id, f"task:{task_id}")
        task = self._task_context(task_id)
        with self.store._connection() as conn:
            dag = conn.execute("SELECT dag_version,graph_hash,node_count FROM task_dags WHERE task_id=?", (task_id,)).fetchone()
            subtasks = [dict(row) for row in conn.execute(
                "SELECT subtask_id,node_index,status,scheduled_run_id,final_attempt_id,final_outcome,finalized_at FROM subtasks WHERE task_id=? ORDER BY node_index", (task_id,)
            )]
            attempts = [dict(row) for row in conn.execute(
                "SELECT a.attempt_id,a.task_id,a.subtask_id,a.attempt_no,a.run_id,CASE WHEN prar.attempt_id IS NOT NULL THEN 'REDACTED_PURGED' ELSE a.route_decision_ref END AS route_decision_ref,a.requested_capability,a.attempt_reason,a.predecessor_attempt_id,a.outcome,a.created_at,"
                "s.payload_state AS route_payload_state,c.sensitivity_level,c.handling_tags_json "
                "FROM subtask_attempts a LEFT JOIN object_states s ON s.object_id=a.route_decision_ref "
                "LEFT JOIN purge_redacted_attempt_routes prar ON prar.attempt_id=a.attempt_id "
                "LEFT JOIN object_envelopes e ON e.object_id=a.route_decision_ref "
                "LEFT JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE a.task_id=? ORDER BY a.subtask_id,a.attempt_no", (task_id,)
            )]
            runs = [dict(row) for row in conn.execute(
                "SELECT r.run_id,r.executor_kind,r.status,r.parent_run_id,"
                "CASE WHEN ms.payload_state='PURGED' THEN 'REDACTED_PURGED' ELSE r.manifest_ref END AS manifest_ref,r.budget_reservation_ref,"
                "c.sensitivity_level,c.handling_tags_json,r.data_boundary_json "
                "FROM runs r JOIN classification_assertions c ON c.assertion_id=r.classification_assertion_ref "
                "LEFT JOIN object_states ms ON ms.object_id=r.manifest_ref "
                "WHERE r.task_id=? ORDER BY r.created_at,r.run_id", (task_id,)
            )]
            account = conn.execute("SELECT * FROM budget_accounts WHERE task_id=?", (task_id,)).fetchone()
            reservations = [dict(row) for row in conn.execute(
                "SELECT br.reservation_id,br.run_id,br.amount,br.model_calls,br.tool_calls,br.child_runs,br.state "
                "FROM budget_reservations br JOIN budget_accounts ba USING(account_id) WHERE ba.task_id=? ORDER BY br.created_at", (task_id,)
            )]
            verifications = [dict(row) for row in conn.execute(
                "SELECT v.verification_id,CASE WHEN s.payload_state='PURGED' THEN 'REDACTED_PURGED' ELSE v.target_ref END AS target_ref,"
                "v.verdict,v.verifier_kind,v.run_id,v.created_at FROM verification_results v "
                "LEFT JOIN object_states s ON s.object_id=v.target_ref "
                "WHERE v.run_id IN (SELECT run_id FROM runs WHERE task_id=?) ORDER BY v.created_at", (task_id,)
            )]
            artifacts = [dict(row) for row in conn.execute(
                "SELECT e.object_id,e.object_type,e.schema_id,e.schema_version,s.payload_state,s.lifecycle,s.validity,"
                "c.sensitivity_level,c.handling_tags_json,e.created_by_run "
                "FROM object_envelopes e JOIN object_states s USING(object_id) "
                "JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE e.created_by_run IN (SELECT run_id FROM runs WHERE task_id=?) ORDER BY e.created_at", (task_id,)
            )]
        visible_runs = []
        for run in runs:
            if not self._classification_visible(run, json.loads(task["data_boundary_json"])):
                raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
            run.pop("data_boundary_json", None)
            run.pop("handling_tags_json", None)
            visible_runs.append(run)
        visible_attempts = []
        boundary = json.loads(task["data_boundary_json"])
        for attempt in attempts:
            route_ref = attempt.pop("route_decision_ref")
            route_class = attempt.pop("sensitivity_level")
            route_tags = attempt.pop("handling_tags_json")
            route_state = attempt.pop("route_payload_state")
            if route_ref and route_ref != "REDACTED_PURGED" and route_state != "PURGED" and (not route_class or route_class not in set(boundary["allowed_classifications"]) or not set(json.loads(route_tags or "[]")).issubset(set(boundary["handling_tags"]))):
                raise RuntimeDenied("INSPECT_ATTEMPT_CLASSIFICATION_OUTSIDE_BOUNDARY")
            attempt["route_decision_ref"] = "REDACTED_PURGED" if route_ref and route_state == "PURGED" else route_ref
            visible_attempts.append(attempt)
        visible_artifacts = []
        for artifact in artifacts:
            if artifact["sensitivity_level"] in set(json.loads(task["data_boundary_json"])["allowed_classifications"]) and set(json.loads(artifact["handling_tags_json"])).issubset(set(json.loads(task["data_boundary_json"])["handling_tags"])):
                artifact.pop("handling_tags_json", None)
                visible_artifacts.append(artifact)
        budget = dict(account) if account else None
        return {
            "task": {"task_id": task_id, "status": task["status"], "created_at": task["created_at"]},
            "dag": dict(dag) if dag else None,
            "subtasks": subtasks,
            "attempts": visible_attempts,
            "runs": visible_runs,
            "artifacts": visible_artifacts,
            "verifications": verifications,
            "budget_account": budget,
            "reservations": reservations,
        }

    def effect(self, *, grant_id: str, task_id: str, effect_id: str) -> dict[str, Any]:
        self.modes.require("inspect")
        self._authorize(grant_id, task_id, f"effect:{effect_id}")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT e.*,r.task_id,r.data_boundary_json,c.sensitivity_level,c.handling_tags_json "
                "FROM effects e JOIN runs r ON r.run_id=e.run_id "
                "JOIN classification_assertions c ON c.assertion_id=r.classification_assertion_ref WHERE e.effect_id=?",
                (effect_id,),
            ).fetchone()
        if not row:
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        if row["task_id"] != task_id:
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        task_context = self._task_context(task_id)
        if not self._classification_visible(row, json.loads(task_context["data_boundary_json"])):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        with self.store._connection() as conn:
            descriptor_row = conn.execute(
                "SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? AND version=?",
                (row["tool_id"], row["tool_descriptor_version"]),
            ).fetchone()
            relations = [dict(item) for item in conn.execute(
                "SELECT from_effect_id,relation_type,to_effect_id,created_at FROM effect_relations WHERE from_effect_id=? OR to_effect_id=? ORDER BY created_at",
                (effect_id, effect_id),
            )]
            attempts = [dict(item) for item in conn.execute(
                "SELECT attempt_no,channel_id,observation,authoritative_evidence_ref,created_at FROM effect_reconciliation_attempts WHERE effect_id=? ORDER BY attempt_no",
                (effect_id,),
            )]
        return {
            "effect_id": effect_id,
            "task_id": row["task_id"],
            "execution_state": row["execution_state"],
            "effect_outcome": row["effect_outcome"],
            "reconciliation_status": row["reconciliation_status"],
            "tool_id": row["tool_id"],
            "action_type": row["action_type"],
            "target_ref": "REDACTED_PURGED" if self._is_purged_object(row["payload_object_ref"]) or self._is_purged_object(row["target_ref"]) else row["target_ref"],
            "approval_ref": "REDACTED_PURGED" if row["approval_ref"] and self._is_purged_object(row["payload_object_ref"]) else row["approval_ref"],
            "reconciliation_capability": json.loads(descriptor_row["descriptor_json"])["reconciliation_capability"] if descriptor_row else "DESCRIPTOR_MISSING",
            "relations": relations,
            "reconciliation_attempts": attempts,
        }

    def approval(self, *, grant_id: str, task_id: str, approval_id: str, include_payload_hash: bool = False) -> dict[str, Any]:
        self.modes.require("inspect")
        self._authorize(grant_id, task_id, f"approval:{approval_id}")
        self._task_context(task_id)
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT a.*,e.run_id,e.payload_object_ref,COALESCE(r.task_id,po.task_id) AS task_id,r.data_boundary_json,c.sensitivity_level,c.handling_tags_json "
                "FROM approval_decisions a LEFT JOIN effects e ON e.effect_id=a.effect_id "
                "LEFT JOIN runs r ON r.run_id=e.run_id "
                "LEFT JOIN purge_redacted_approval_owners po ON po.approval_id=a.approval_id "
                "LEFT JOIN classification_assertions c ON c.assertion_id=r.classification_assertion_ref "
                "WHERE a.approval_id=?",
                (approval_id,),
            ).fetchone()
        if not row:
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        owner_task_id = row["task_id"]
        if row["target_type"] == "PURGE_EXECUTE":
            with self.store._connection() as conn:
                plan_owner = conn.execute("SELECT task_id,plan_json FROM purge_plan_records WHERE plan_id=?", (row["target_ref"],)).fetchall()
                if len(plan_owner) != 1 or plan_owner[0]["task_id"] is None:
                    raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
                plan_task_id = plan_owner[0]["task_id"]
                if owner_task_id is not None and owner_task_id != plan_task_id:
                    raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
                owner_task_id = plan_task_id
                purge_plan = json.loads(plan_owner[0]["plan_json"])
                for ref in set(purge_plan.get("target_refs", [])) | set(purge_plan.get("descendant_refs", [])):
                    state = conn.execute("SELECT s.payload_state,r.task_id FROM object_states s LEFT JOIN object_envelopes e USING(object_id) LEFT JOIN runs r ON r.run_id=e.created_by_run WHERE s.object_id=?", (ref,)).fetchone()
                    if not state:
                        raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
                    ref_owner = self._object_task_owner(conn, ref, state["task_id"], purged=state["payload_state"] == "PURGED")
                    if ref_owner != owner_task_id:
                        raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
        else:
            with self.store._connection() as conn:
                object_state = conn.execute(
                    "SELECT s.payload_state,e.classification_assertion_ref,c.sensitivity_level,c.handling_tags_json "
                    "FROM object_states s LEFT JOIN object_envelopes e USING(object_id) "
                    "LEFT JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                    "WHERE s.object_id=?",
                    (row["target_ref"],),
                ).fetchone()
                if object_state:
                    object_owner = self._object_task_owner(
                        conn, row["target_ref"], None, purged=object_state["payload_state"] == "PURGED"
                    )
                    if owner_task_id is not None and owner_task_id != object_owner:
                        raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
                    owner_task_id = object_owner
                    if object_state["payload_state"] != "PURGED":
                        if not object_state["sensitivity_level"] or not object_state["handling_tags_json"]:
                            raise RuntimeDenied("INSPECT_CLASSIFICATION_UNAVAILABLE")
                        object_class = {
                            "sensitivity_level": object_state["sensitivity_level"],
                            "handling_tags_json": object_state["handling_tags_json"],
                            "data_boundary_json": self._task_context(task_id)["data_boundary_json"],
                        }
                        if not self._classification_visible(object_class):
                            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
            if owner_task_id is None:
                raise RuntimeDenied("INSPECT_APPROVAL_TASK_UNRESOLVED")
        if owner_task_id != task_id:
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        if row["sensitivity_level"] and not self._classification_visible(row, json.loads(self._task_context(task_id)["data_boundary_json"])):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        purge_bound = row["target_ref"] == "REDACTED_PURGED" or row["effect_id"] is None and row["task_id"] is not None and self._is_purged_object(row["payload_object_ref"])
        purge_bound = purge_bound or self._is_purged_object(row["payload_object_ref"]) or self._is_purged_object(row["target_ref"])
        approved_scope = json.loads(row["approved_scope_json"])
        if purge_bound:
            approved_scope = ["REDACTED_PURGED" if item == row["target_ref"] else item for item in approved_scope]
        result = {
            "approval_id": approval_id,
            "approver_principal_id": row["approver_principal_id"],
            "target_type": row["target_type"],
            "target_ref": "REDACTED_PURGED" if purge_bound else row["target_ref"],
            "effect_id": "REDACTED_PURGED" if purge_bound and (row["effect_id"] or row["target_ref"] == "REDACTED_PURGED") else row["effect_id"],
            "decision": row["decision"],
            "approved_scope": approved_scope,
            "policy_version": row["policy_version"],
            "issued_at": row["issued_at"],
            "expires_at": row["expires_at"],
        }
        if include_payload_hash and not purge_bound:
            self._authorize(grant_id, task_id, f"approval:{approval_id}:payload-integrity", "INSPECT_PROTECTED")
            result["payload_integrity_hash"] = row["payload_integrity_hash"]
        else:
            result["payload_integrity_hash"] = "REDACTED_PURGED" if purge_bound else "REDACTED"
        return result

    def trace_events(self, *, grant_id: str, task_id: str, run_id: str) -> list[dict[str, Any]]:
        """Return an authorized, payload-free Trace projection with purged refs masked."""
        self.modes.require("inspect")
        self._authorize(grant_id, task_id, f"trace:{run_id}")
        task = self._task_context(task_id)
        with self.store._connection() as conn:
            run = conn.execute("SELECT task_id,data_boundary_json,classification_assertion_ref FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["task_id"] != task_id:
                raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
            run_class = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (run["classification_assertion_ref"],)).fetchone()
            if not run_class or not self._classification_visible(run_class, json.loads(task["data_boundary_json"])):
                raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
            rows = conn.execute("SELECT seq_no,event_json FROM trace_events WHERE run_id=? ORDER BY seq_no", (run_id,)).fetchall()
            purged_effects = {row[0] for row in conn.execute("SELECT e.effect_id FROM effects e JOIN object_states s ON s.object_id=e.payload_object_ref WHERE s.payload_state='PURGED'")}
        projected = []
        for row in rows:
            event = json.loads(row["event_json"])
            assertion_id = event.get("classification_assertion_ref")
            with self.store._connection() as conn:
                classification = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
            if not classification or not self._classification_visible(classification, json.loads(task["data_boundary_json"])):
                raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
            hidden_objects = {ref for ref in event.get("object_refs", []) if self._is_purged_object(ref)}
            hidden_effects = set(event.get("effect_refs", [])) & purged_effects
            hidden_values = hidden_objects | hidden_effects
            metadata = event.get("typed_metadata", {})
            def redact(value):
                if isinstance(value, str):
                    return "REDACTED_PURGED" if value in hidden_values else value
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if isinstance(value, dict):
                    return {key: redact(item) for key, item in value.items()}
                return value
            projected.append({
                "seq_no": row["seq_no"], "event_id": event["event_id"], "event_type": event["event_type"],
                "occurred_at": event["occurred_at"], "actor_id": event["actor_id"],
                "object_refs": ["REDACTED_PURGED" if ref in hidden_objects else ref for ref in event.get("object_refs", [])],
                "effect_refs": ["REDACTED_PURGED" if ref in hidden_effects else ref for ref in event.get("effect_refs", [])],
                "typed_metadata": redact(metadata),
            })
        return projected

    def route(self, *, grant_id: str, task_id: str, route_id: str) -> dict[str, Any]:
        self.modes.require("inspect")
        self._authorize(grant_id, task_id, f"route:{route_id}")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT d.decision_json,s.task_id,r.data_boundary_json,c.sensitivity_level,c.handling_tags_json,"
                "ds.payload_state AS decision_payload_state,dc.sensitivity_level AS decision_sensitivity,dc.handling_tags_json AS decision_tags "
                "FROM route_decisions d JOIN subtasks s ON s.subtask_id=d.subtask_id "
                "JOIN task_dags g ON g.task_id=s.task_id JOIN runs r ON r.run_id=g.root_run_id "
                "JOIN classification_assertions c ON c.assertion_id=r.classification_assertion_ref "
                "JOIN object_states ds ON ds.object_id=d.decision_object_id "
                "LEFT JOIN object_envelopes de ON de.object_id=d.decision_object_id "
                "LEFT JOIN classification_assertions dc ON dc.assertion_id=de.classification_assertion_ref WHERE d.route_decision_id=?",
                (route_id,),
            ).fetchone()
        if not row:
            with self.store._connection() as conn:
                state = conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (route_id,)).fetchone()
                if state and state["payload_state"] == "PURGED":
                    owner = self._object_task_owner(conn, route_id, None, purged=True)
                    if owner != task_id:
                        raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
                    return {"route_decision_id": "REDACTED_PURGED", "status": "REDACTED_PURGED"}
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        if row["task_id"] != task_id:
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        if not self._classification_visible(row, json.loads(self._task_context(task_id)["data_boundary_json"])):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        if row["decision_payload_state"] == "PURGED":
            return {"route_decision_id": route_id, "status": "REDACTED_PURGED"}
        boundary = json.loads(self._task_context(task_id)["data_boundary_json"])
        if not row["decision_sensitivity"] or row["decision_sensitivity"] not in set(boundary["allowed_classifications"]) or not set(json.loads(row["decision_tags"] or "[]")).issubset(set(boundary["handling_tags"])):
            raise RuntimeDenied("INSPECT_ROUTE_CLASSIFICATION_OUTSIDE_BOUNDARY")
        decision = json.loads(row["decision_json"])
        self.store._validate(f"nexus.route_decision@{decision.get('schema_version')}.schema.json", decision)
        return decision

    def object_metadata(self, *, grant_id: str, task_id: str, object_id: str, include_integrity_hash: bool = False) -> dict[str, Any]:
        self.modes.require("inspect")
        purged_lookup = self._is_purged_object(object_id)
        # A purge narrows object-specific grant resources. A redacted tombstone
        # may still be inspected through the caller's Task-scoped INSPECT
        # authority, after durable object-to-Task ownership is proven below.
        self._authorize(grant_id, task_id, f"task:{task_id}" if purged_lookup else f"object:{object_id}")
        self._task_context(task_id)
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT e.object_id,e.object_type,e.schema_id,e.schema_version,e.integrity_hash,e.created_by_run,"
                "s.lifecycle,s.validity,s.payload_state,r.task_id,r.data_boundary_json,"
                "c.sensitivity_level,c.handling_tags_json "
                "FROM objects o LEFT JOIN object_envelopes e USING(object_id) "
                "JOIN object_states s USING(object_id) LEFT JOIN runs r ON r.run_id=e.created_by_run "
                "LEFT JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE o.object_id=?", (object_id,)
            ).fetchone()
        if not row:
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        with self.store._connection() as conn:
            owner_task_id = self._object_task_owner(
                conn, object_id, row["task_id"], purged=row["payload_state"] == "PURGED"
            )
        if owner_task_id != task_id:
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        if row["sensitivity_level"] and not self._classification_visible(row, json.loads(self._task_context(task_id)["data_boundary_json"])):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        result = {key: row[key] for key in ("object_id", "object_type", "schema_id", "schema_version", "created_by_run", "lifecycle", "validity", "payload_state")}
        result["integrity_hash"] = "REDACTED"
        if include_integrity_hash:
            self._authorize(grant_id, task_id, f"object:{object_id}:integrity", "INSPECT_PROTECTED")
            result["integrity_hash"] = row["integrity_hash"]
        return result

    def purge(self, *, grant_id: str, task_id: str, plan_id: str) -> dict[str, Any]:
        self.modes.require("inspect")
        self._authorize(grant_id, task_id, f"purge:{plan_id}")
        task_context = self._task_context(task_id)
        with self.store._connection() as conn:
            row = conn.execute("SELECT plan_json,task_id FROM purge_plan_records WHERE plan_id=?", (plan_id,)).fetchone()
            executions = [dict(item) for item in conn.execute(
                "SELECT record_id,barrier_id,status,unresolved_json,started_at,completed_at FROM purge_execution_records WHERE plan_id=? ORDER BY started_at", (plan_id,)
            )]
            barriers = [dict(item) for item in conn.execute(
                "SELECT b.barrier_id,b.status,b.lineage_revision,b.created_at,COUNT(r.object_id) AS protected_object_count "
                "FROM purge_barriers b LEFT JOIN purge_barrier_refs r USING(barrier_id) WHERE b.plan_id=? GROUP BY b.barrier_id ORDER BY b.created_at", (plan_id,)
            )]
            ledger = [dict(item) for item in conn.execute(
                "SELECT l.action,l.created_at,b.status FROM purge_ledger l JOIN purge_barriers b USING(barrier_id) WHERE b.plan_id=? ORDER BY l.ledger_seq", (plan_id,)
            )]
        if not row:
            raise RuntimeDenied("INSPECT_NOT_FOUND")
        if row["task_id"] is None:
            raise RuntimeDenied("PURGE_PLAN_TASK_UNBOUND_LEGACY")
        if row["task_id"] != task_id:
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        plan = json.loads(row["plan_json"])
        # The plan is bound to this Task; also validate every still-live
        # reference against the authorized Task boundary before projection.
        with self.store._connection() as conn:
            refs = set(plan["target_refs"]) | set(plan["descendant_refs"])
            object_rows = conn.execute(
                "SELECT e.object_id,r.task_id,c.sensitivity_level,c.handling_tags_json "
                "FROM object_envelopes e JOIN runs r ON r.run_id=e.created_by_run "
                "JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE e.object_id IN (" + ",".join("?" for _ in refs) + ")", tuple(refs)
            ).fetchall() if refs else []
        boundary = json.loads(task_context["data_boundary_json"])
        if any(item["task_id"] != task_id for item in object_rows):
            raise RuntimeDenied("INSPECT_TASK_SCOPE_MISMATCH")
        if any(item["sensitivity_level"] not in set(boundary["allowed_classifications"]) or not set(json.loads(item["handling_tags_json"])).issubset(set(boundary["handling_tags"])) for item in object_rows):
            raise RuntimeDenied("INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY")
        return {"plan": {"plan_id": plan_id, "plan_hash": plan["plan_hash"], "target_count": len(plan["target_refs"]), "descendant_count": len(plan["descendant_refs"]), "affected_indexes": plan["affected_indexes"], "planned_actions": plan["planned_actions"]}, "executions": [{**{key: value for key, value in item.items() if key != "unresolved_json"}, "unresolved": json.loads(item["unresolved_json"])} for item in executions], "barriers": barriers, "ledger": ledger}
