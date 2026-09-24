from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from kernel.authority.errors import AuthorizationDenied
from kernel.runtime.errors import RuntimeDenied


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class AmbiguousDispatch(Exception):
    """The remote outcome cannot be inferred from a lost/ambiguous response."""


class Dispatcher(Protocol):
    def dispatch(self, *, idempotency_key: str, target_ref: str, payload: bytes) -> dict[str, str]: ...


class ReconciliationPort(Protocol):
    channel_id: str
    authoritative: bool

    def query(self, *, idempotency_key: str, target_ref: str) -> dict[str, str]: ...


class DeterministicEffectService:
    """Fail-closed Effect state machine. Network adapters are injected ports, never inferred from descriptors."""

    def __init__(self, store, authority, trace, budget, *, dispatchers: dict[str, Dispatcher] | None = None, reconciliation_ports: dict[str, ReconciliationPort] | None = None):
        self.store, self.authority, self.trace, self.budget = store, authority, trace, budget
        self.dispatchers = dispatchers or {}
        self.reconciliation_ports = reconciliation_ports or {}

    def register_descriptor(self, *, command_id: str, grant_id: str, task_id: str, descriptor: dict[str, Any]) -> None:
        self.store._validate("nexus.tool_descriptor@1.schema.json", descriptor)
        if descriptor["review_status"] != "APPROVED" or descriptor["effect_class"] == "READ_ONLY":
            raise RuntimeDenied("EFFECT_DESCRIPTOR_MUST_BE_REVIEWED_MUTATING_TOOL")
        for schema_id in (descriptor["input_schema_id"], descriptor["output_schema_id"]):
            if not schema_id.endswith(".schema.json") or "/" in schema_id or "\\" in schema_id:
                raise RuntimeDenied("TOOL_SCHEMA_REFERENCE_INVALID")
            self.store._schema(schema_id)
        self.authority.evaluate_authorization(grant_id, {"task": task_id, "resource": descriptor["tool_id"], "action": "RUNTIME_CONFIGURE", "audience": "nexus-runtime"}, command_id + "-authorize")
        operation = "register_effect_tool_descriptor"
        request = {"task_id": task_id, "descriptor": descriptor}
        digest = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            if self.store._replay_command(conn, command_id, operation, digest) is not None:
                return
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, digest) is not None:
                    conn.commit(); return
                self._authorize_locked(grant_id, {"task": task_id, "resource": descriptor["tool_id"], "action": "RUNTIME_CONFIGURE", "audience": "nexus-runtime"}, None, None, command_id + "-recheck")
                conn.execute("INSERT INTO tool_descriptors(tool_id,version,descriptor_json) VALUES(?,?,?)", (descriptor["tool_id"], descriptor["version"], _canonical(descriptor)))
                self.store._record_command(conn, command_id, operation, digest, {"tool_id": descriptor["tool_id"], "version": descriptor["version"]})
                conn.commit()
            except Exception:
                conn.rollback(); raise

    def create_effect(self, *, command_id: str, effect: dict[str, Any], payload_object_ref: str, classification_assertion_ref: str, compensates_effect_id: str | None = None) -> None:
        self.store._validate("nexus.effect@1.schema.json", effect)
        if effect["execution_state"] != "DECLARED" or effect["effect_outcome"] != "UNDETERMINED" or effect["reconciliation_status"] != "NOT_REQUIRED":
            raise RuntimeDenied("EFFECT_MUST_START_DECLARED")
        request = {"effect": effect, "payload_object_ref": payload_object_ref, "classification_assertion_ref": classification_assertion_ref, "compensates_effect_id": compensates_effect_id}
        digest = self.store._request_hash("create_effect", request)
        with self.store._connection() as conn:
            if self.store._replay_command(conn, command_id, "create_effect", digest) is not None:
                return
        meta = self.store.get_object_metadata(payload_object_ref)
        if meta.get("payload_state") != "AVAILABLE" or meta["integrity_hash"] != effect.get("payload_integrity_hash"):
            raise RuntimeDenied("EFFECT_PAYLOAD_HASH_MISMATCH")
        self.store.get_payload(payload_object_ref)
        with self.store._connection() as conn:
            run = conn.execute("SELECT r.*,t.task_id FROM runs r JOIN tasks t USING(task_id) WHERE r.run_id=?", (effect["run_id"],)).fetchone()
        if not run or run["executor_kind"] != "TOOL" or run["status"] != "RUNNING" or run["grant_id"] != effect["grant_id"]:
            raise RuntimeDenied("EFFECT_REQUIRES_ACTIVE_AUTHORIZED_TOOL_RUN")
        try:
            manifest = json.loads(self.store.get_payload(run["manifest_ref"]).decode("utf-8"))
        except Exception as exc:
            raise RuntimeDenied("EFFECT_TOOL_RUN_MANIFEST_UNAVAILABLE") from exc
        if manifest.get("tool_id") != effect["tool_id"]:
            raise RuntimeDenied("EFFECT_TOOL_MANIFEST_IDENTITY_MISMATCH")
        descriptor_row = None
        with self.store._connection() as conn:
            descriptor_row = conn.execute("SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? AND version=?", (effect["tool_id"], manifest["tool_descriptor_version"])).fetchone()
        if not descriptor_row:
            raise RuntimeDenied("EFFECT_TOOL_DESCRIPTOR_NOT_FOUND")
        descriptor = json.loads(descriptor_row["descriptor_json"])
        if descriptor["review_status"] != "APPROVED" or descriptor["effect_class"] == "READ_ONLY":
            raise RuntimeDenied("EFFECT_TOOL_DESCRIPTOR_NOT_APPROVED_FOR_MUTATION")
        if effect["action_type"] not in descriptor["required_authority"]:
            raise RuntimeDenied("TOOL_DESCRIPTOR_ACTION_NOT_DECLARED")
        if not self._has_required_actions(effect["grant_id"], descriptor["required_authority"]):
            raise AuthorizationDenied("TOOL_REQUIRED_AUTHORITY_NOT_GRANTED")
        self._authorize(effect["grant_id"], run["task_id"], effect["target_ref"], "EFFECT_PREPARE", command_id + "-authorize")
        if not self._has_required_actions(effect["grant_id"], descriptor["required_authority"]):
            raise AuthorizationDenied("TOOL_DESCRIPTOR_DOES_NOT_AUTHORIZE_ACTION")
        with self.store._connection() as conn:
            reservation = conn.execute("SELECT state,run_id FROM budget_reservations WHERE reservation_id=?", (run["budget_reservation_ref"],)).fetchone()
        if not reservation or reservation["state"] != "RESERVED" or reservation["run_id"] != run["run_id"]:
            raise RuntimeDenied("EFFECT_BUDGET_RESERVATION_INVALID")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, "create_effect", digest) is not None:
                    conn.commit(); return
                self._authorize_locked(effect["grant_id"], {"task": run["task_id"], "resource": effect["target_ref"], "action": "EFFECT_PREPARE", "audience": "nexus-runtime"}, None, None, command_id + "-recheck")
                effective = self.authority.compute_effective_authority(effect["grant_id"])
                if effect["action_type"] not in effective["action_scope"] or not set(descriptor["required_authority"]).issubset(effective["action_scope"]):
                    raise AuthorizationDenied("EFFECT_ACTION_NOT_AUTHORIZED")
                active_run = conn.execute("SELECT status,grant_id FROM runs WHERE run_id=?", (effect["run_id"],)).fetchone()
                active_reservation = conn.execute("SELECT state FROM budget_reservations WHERE reservation_id=?", (run["budget_reservation_ref"],)).fetchone()
                if not active_run or active_run["status"] != "RUNNING" or active_run["grant_id"] != effect["grant_id"] or not active_reservation or active_reservation["state"] != "RESERVED":
                    raise RuntimeDenied("EFFECT_RUN_OR_BUDGET_CHANGED_DURING_PREPARATION")
                self.store._assert_unbarred(conn, [payload_object_ref])
                if conn.execute("SELECT 1 FROM effects WHERE effect_id=? OR idempotency_key=?", (effect["effect_id"], effect["idempotency_key"])).fetchone():
                    raise RuntimeDenied("EFFECT_IDEMPOTENCY_CONFLICT")
                if compensates_effect_id:
                    original = conn.execute("SELECT effect_outcome,run_id FROM effects WHERE effect_id=?", (compensates_effect_id,)).fetchone()
                    if not original or original["effect_outcome"] != "COMMITTED" or original["run_id"] == effect["run_id"]:
                        raise RuntimeDenied("COMPENSATION_REQUIRES_COMMITTED_EFFECT_AND_NEW_RUN")
                    original_run = conn.execute("SELECT task_id FROM runs WHERE run_id=?", (original["run_id"],)).fetchone()
                    if not original_run or original_run["task_id"] != run["task_id"] or descriptor["compensation_capability"] == "NOT_APPLICABLE_READ_ONLY":
                        raise RuntimeDenied("COMPENSATION_DESCRIPTOR_OR_TASK_MISMATCH")
                    self._authorize_locked(effect["grant_id"], {"task": run["task_id"], "resource": effect["target_ref"], "action": "EFFECT_COMPENSATE", "audience": "nexus-runtime"}, None, None, command_id + "-compensate-authorize")
                self._assert_object_classification(conn, payload_object_ref, run, classification_assertion_ref)
                effect_json = _canonical(effect)
                conn.execute("INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (effect["effect_id"], effect["run_id"], effect["tool_id"], descriptor["version"], effect["action_type"], effect["target_ref"], effect["payload_integrity_hash"], payload_object_ref, effect["idempotency_key"], effect["grant_id"], effect.get("approval_ref"), run["budget_reservation_ref"], effect["execution_state"], effect["effect_outcome"], effect["reconciliation_status"], None, effect_json, _now(), _now()))
                if compensates_effect_id:
                    conn.execute("INSERT INTO effect_relations VALUES(?,?,?,?)", (effect["effect_id"], "COMPENSATES", compensates_effect_id, _now()))
                event_type = "nexus.effect.compensation_created" if compensates_effect_id else "nexus.effect.declared"
                metadata = {"effect_id": effect["effect_id"], "compensated_effect_id": compensates_effect_id} if compensates_effect_id else {"effect_id": effect["effect_id"]}
                self._append_fact(conn, command_id, run["run_id"], effect["effect_id"], event_type, metadata, classification_assertion_ref)
                self.store._record_command(conn, command_id, "create_effect", digest, {"effect_id": effect["effect_id"], "execution_state": "DECLARED"})
                conn.commit()
            except Exception:
                conn.rollback(); raise

    def prepare(self, *, command_id: str, effect_id: str, classification_assertion_ref: str) -> dict[str, str]:
        return self._transition(command_id, effect_id, "DECLARED", "PREPARED", "nexus.effect.prepared", {"effect_id": effect_id, "execution_state": "PREPARED"}, classification_assertion_ref, "EFFECT_PREPARE")

    def authorize(self, *, command_id: str, effect_id: str, classification_assertion_ref: str) -> dict[str, str]:
        row = self._get(effect_id)
        self._authorize(row["grant_id"], self._task_id(row["run_id"]), row["target_ref"], "EFFECT_COMMIT", command_id + "-authorize")
        return self._transition(command_id, effect_id, "PREPARED", "AUTHORIZED", "nexus.effect.authorized", {"effect_id": effect_id, "execution_state": "AUTHORIZED"}, classification_assertion_ref, "EFFECT_COMMIT")

    def commit(self, *, command_id: str, effect_id: str, classification_assertion_ref: str, start_classification_assertion_ref: str) -> dict[str, str]:
        operation = "commit_effect"
        request = {"effect_id": effect_id, "classification_assertion_ref": classification_assertion_ref, "start_classification_assertion_ref": start_classification_assertion_ref}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            self._settle_effect_budget(effect_id, command_id + "-budget")
            return prior
        preflight = self._get(effect_id)
        preflight_descriptor = self._descriptor(preflight["tool_id"], preflight["tool_descriptor_version"])
        if preflight_descriptor["network_egress"]:
            self.authority.authorize_egress(grant_id=preflight["grant_id"], task_id=self._task_id(preflight["run_id"]), destination=preflight["target_ref"], audience="nexus-runtime", object_ids=[preflight["payload_object_ref"]], command_id=command_id + "-egress")
        dispatch = None
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
                if not row:
                    raise RuntimeDenied("EFFECT_NOT_FOUND")
                if row["execution_state"] == "COMMITTING":
                    result = self._finish_locked(conn, row, "FINISHED", "UNKNOWN", "PENDING", classification_assertion_ref, command_id, operation, request_hash)
                    conn.commit()
                    self._settle_effect_budget(effect_id, command_id + "-budget")
                    return result
                if row["execution_state"] != "AUTHORIZED":
                    raise RuntimeDenied("EFFECT_NOT_AUTHORIZED_OR_ALREADY_DISPATCHED")
                run = conn.execute("SELECT task_id,grant_id,status FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
                if not run or run["status"] != "RUNNING" or run["grant_id"] != row["grant_id"]:
                    raise RuntimeDenied("EFFECT_TOOL_RUN_NOT_ACTIVE_AT_COMMIT")
                descriptor = json.loads(conn.execute("SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? AND version=?", (row["tool_id"], row["tool_descriptor_version"])).fetchone()[0])
                approval_id = row["approval_ref"]
                request_gate = {"task": run["task_id"], "resource": row["target_ref"], "action": "EFFECT_COMMIT", "audience": "nexus-runtime"}
                self._authorize_locked(row["grant_id"], request_gate, None, None, command_id + "-commit-gate")
                request_action = {**request_gate, "action": row["action_type"], "effect_id": effect_id}
                self._authorize_locked(row["grant_id"], request_action, approval_id, row["payload_integrity_hash"], command_id + "-commit-auth")
                if not self._has_required_actions(row["grant_id"], descriptor["required_authority"]):
                    raise AuthorizationDenied("TOOL_REQUIRED_AUTHORITY_NOT_GRANTED")
                if descriptor["effect_class"] in {"EXTERNAL_REVERSIBLE", "EXTERNAL_IRREVERSIBLE"} and not approval_id:
                    raise RuntimeDenied("EFFECT_APPROVAL_REQUIRED")
                payload = self.store.get_payload(row["payload_object_ref"])
                if self.store.get_object_metadata(row["payload_object_ref"])["integrity_hash"] != row["payload_integrity_hash"]:
                    raise RuntimeDenied("EFFECT_PAYLOAD_HASH_MISMATCH")
                conn.execute("UPDATE effects SET execution_state='COMMITTING',effect_outcome='UNKNOWN',reconciliation_status='PENDING',effect_json=?,updated_at=? WHERE effect_id=?", (self._with_state(row, "COMMITTING", "UNKNOWN", "PENDING"), _now(), effect_id))
                self._append_fact(conn, command_id + "-start", row["run_id"], effect_id, "nexus.effect.commit_started", {"effect_id": effect_id, "execution_state": "COMMITTING"}, start_classification_assertion_ref)
                conn.commit()
                dispatch = (descriptor, payload, row["idempotency_key"], row["target_ref"])
            except Exception:
                conn.rollback(); raise
        if dispatch is None:
            raise RuntimeDenied("EFFECT_DISPATCH_NOT_AVAILABLE")
        descriptor, payload, idempotency_key, target_ref = dispatch
        adapter = self.dispatchers.get(descriptor["tool_id"])
        try:
            if adapter is None:
                raise AmbiguousDispatch("NO_DISPATCHER")
            outcome = adapter.dispatch(idempotency_key=idempotency_key, target_ref=target_ref, payload=payload)
            if outcome.get("outcome") not in {"COMMITTED", "NOT_COMMITTED"}:
                raise AmbiguousDispatch("NON_AUTHORITATIVE_DISPATCH_RESULT")
        except Exception:
            result = self._finalize_dispatch(command_id, effect_id, "UNKNOWN", None, classification_assertion_ref, operation, request_hash)
        else:
            result = self._finalize_dispatch(command_id, effect_id, outcome["outcome"], outcome.get("receipt_ref"), classification_assertion_ref, operation, request_hash)
        self._settle_effect_budget(effect_id, command_id + "-budget")
        return result

    def reconcile(self, *, command_id: str, effect_id: str, classification_assertion_ref: str) -> dict[str, str]:
        row = self._get(effect_id)
        operation = "reconcile_effect"
        request = {"effect_id": effect_id, "classification_assertion_ref": classification_assertion_ref}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            if prior["effect_outcome"] in {"COMMITTED", "NOT_COMMITTED"}:
                self._settle_effect_budget(effect_id, command_id + "-budget")
            return prior
        if row["effect_outcome"] != "UNKNOWN":
            raise RuntimeDenied("ONLY_UNKNOWN_EFFECTS_CAN_BE_RECONCILED")
        self._authorize(row["grant_id"], self._task_id(row["run_id"]), row["target_ref"], "EFFECT_RECONCILE", command_id + "-authorize")
        descriptor = self._descriptor(row["tool_id"], row["tool_descriptor_version"])
        channel_id = descriptor["reconciliation_capability"]
        port = self.reconciliation_ports.get(channel_id)
        max_attempts = self.authority.policy["effects"]["max_reconciliation_attempts"]
        with self.store._lock, self.store._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM effect_reconciliation_attempts WHERE effect_id=?", (effect_id,)).fetchone()[0]
        if not port or port.channel_id != channel_id or not port.authoritative:
            return self._set_unknown_status(command_id, row, "HUMAN_REQUIRED", classification_assertion_ref)
        if count >= max_attempts:
            return self._set_unknown_status(command_id, row, "EXHAUSTED", classification_assertion_ref)
        self._authorize(row["grant_id"], self._task_id(row["run_id"]), row["target_ref"], "EFFECT_RECONCILE", command_id + "-authorize")
        observation = port.query(idempotency_key=row["idempotency_key"], target_ref=row["target_ref"])
        value = observation.get("outcome")
        evidence = observation.get("evidence_ref")
        if value not in {"COMMITTED", "NOT_COMMITTED", "INCONCLUSIVE"} or (value != "INCONCLUSIVE" and not evidence):
            raise RuntimeDenied("AUTHORITATIVE_RECONCILIATION_EVIDENCE_REQUIRED")
        result = self._record_reconciliation(command_id, row, value, evidence, count + 1, classification_assertion_ref, "PENDING" if count + 1 < max_attempts else "EXHAUSTED")
        if result["effect_outcome"] in {"COMMITTED", "NOT_COMMITTED"}:
            self._settle_effect_budget(effect_id, command_id + "-budget")
        return result

    def _record_reconciliation(self, command_id, row, observation, evidence, attempt_no, class_ref, fallback_status):
        operation = "reconcile_effect"
        request = {"effect_id": row["effect_id"], "classification_assertion_ref": class_ref}
        digest = self.store._request_hash(operation, request)
        resolved = observation in {"COMMITTED", "NOT_COMMITTED"}
        next_outcome = observation if resolved else "UNKNOWN"
        next_status = "RESOLVED" if resolved else fallback_status
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, digest) is not None:
                    prior = self.store._replay_command(conn, command_id, operation, digest); conn.commit(); return prior
                current = conn.execute("SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)).fetchone()
                if current["effect_outcome"] != "UNKNOWN":
                    raise RuntimeDenied("EFFECT_ALREADY_RECONCILED")
                self._authorize_locked(current["grant_id"], {"task": self._task_id(current["run_id"]), "resource": current["target_ref"], "action": "EFFECT_RECONCILE", "audience": "nexus-runtime"}, None, None, command_id + "-recheck")
                doc = self._with_state(current, "FINISHED", next_outcome, next_status)
                conn.execute("UPDATE effects SET execution_state='FINISHED',effect_outcome=?,reconciliation_status=?,effect_json=?,updated_at=? WHERE effect_id=?", (next_outcome, next_status, doc, _now(), current["effect_id"]))
                self._append_fact(conn, command_id, current["run_id"], current["effect_id"], "nexus.effect.reconciled", {"effect_id": current["effect_id"], "effect_outcome": next_outcome, "reconciliation_status": next_status}, class_ref)
                result = {"effect_id": current["effect_id"], "execution_state": "FINISHED", "effect_outcome": next_outcome, "reconciliation_status": next_status}
                self.store._record_command(conn, command_id, operation, digest, result)
                conn.execute("INSERT INTO effect_reconciliation_attempts VALUES(?,?,?,?,?,?,?,?)", ("recon-" + command_id, current["effect_id"], attempt_no, self._descriptor(current["tool_id"], current["tool_descriptor_version"])["reconciliation_capability"], observation, evidence, command_id, _now()))
                conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def _transition(self, command_id, effect_id, expected, target, event_type, metadata, class_ref, action):
        row = self._get(effect_id)
        operation = "effect_transition:" + target
        request = {"effect_id": effect_id, "target": target, "classification_assertion_ref": class_ref}
        digest = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, digest)
        if prior is not None:
            return prior
        self._authorize(row["grant_id"], self._task_id(row["run_id"]), row["target_ref"], action, command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store._replay_command(conn, command_id, operation, digest)
                if prior is not None: conn.commit(); return prior
                current = conn.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
                self._authorize_locked(current["grant_id"], {"task": self._task_id(current["run_id"]), "resource": current["target_ref"], "action": action, "audience": "nexus-runtime"}, None, None, command_id + "-recheck")
                if current["execution_state"] != expected: raise RuntimeDenied("INVALID_EFFECT_TRANSITION")
                doc = self._with_state(current, target, "UNDETERMINED", "NOT_REQUIRED")
                conn.execute("UPDATE effects SET execution_state=?,effect_json=?,updated_at=? WHERE effect_id=?", (target, doc, _now(), effect_id))
                self._append_fact(conn, command_id, current["run_id"], effect_id, event_type, metadata, class_ref)
                result = {"effect_id": effect_id, "execution_state": target, "effect_outcome": "UNDETERMINED", "reconciliation_status": "NOT_REQUIRED"}
                self.store._record_command(conn, command_id, operation, digest, result); conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def _finalize_dispatch(self, command_id, effect_id, outcome, receipt, class_ref, operation, request_hash):
        state = "FINISHED"
        status = "PENDING" if outcome == "UNKNOWN" else "NOT_REQUIRED"
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
                doc = json.loads(row["effect_json"]); doc.update(execution_state=state, effect_outcome=outcome, reconciliation_status=status)
                if receipt: doc["external_receipt_ref"] = receipt
                conn.execute("UPDATE effects SET execution_state=?,effect_outcome=?,reconciliation_status=?,external_receipt_ref=?,effect_json=?,updated_at=? WHERE effect_id=?", (state, outcome, status, receipt, _canonical(doc), _now(), effect_id))
                self._append_fact(conn, command_id + "-outcome", row["run_id"], effect_id, "nexus.effect.outcome_recorded", {"effect_id": effect_id, "execution_state": state, "effect_outcome": outcome, "reconciliation_status": status}, class_ref)
                result = {"effect_id": effect_id, "execution_state": state, "effect_outcome": outcome, "reconciliation_status": status}
                self.store._record_command(conn, command_id, operation, request_hash, result); conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def _finish_locked(self, conn, row, state, outcome, status, class_ref, command_id, operation, request_hash):
        doc = json.loads(row["effect_json"]); doc.update(execution_state=state, effect_outcome=outcome, reconciliation_status=status)
        conn.execute("UPDATE effects SET execution_state=?,effect_outcome=?,reconciliation_status=?,effect_json=?,updated_at=? WHERE effect_id=?", (state, outcome, status, _canonical(doc), _now(), row["effect_id"]))
        self._append_fact(conn, command_id + "-outcome", row["run_id"], row["effect_id"], "nexus.effect.outcome_recorded", {"effect_id": row["effect_id"], "execution_state": state, "effect_outcome": outcome, "reconciliation_status": status}, class_ref)
        result = {"effect_id": row["effect_id"], "execution_state": state, "effect_outcome": outcome, "reconciliation_status": status}
        self.store._record_command(conn, command_id, operation, request_hash, result)
        return result

    def _set_unknown_status(self, command_id, row, status, class_ref):
        operation = "effect_unknown_status"
        request = {"effect_id": row["effect_id"], "status": status}
        digest = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store._replay_command(conn, command_id, operation, digest)
                if prior is not None:
                    conn.commit(); return prior
                current = conn.execute("SELECT * FROM effects WHERE effect_id=?", (row["effect_id"],)).fetchone()
                self._authorize_locked(current["grant_id"], {"task": self._task_id(current["run_id"]), "resource": current["target_ref"], "action": "EFFECT_RECONCILE", "audience": "nexus-runtime"}, None, None, command_id + "-recheck")
                doc = self._with_state(current, "FINISHED", "UNKNOWN", status)
                conn.execute("UPDATE effects SET execution_state='FINISHED',reconciliation_status=?,effect_json=?,updated_at=? WHERE effect_id=?", (status, doc, _now(), row["effect_id"]))
                self._append_fact(conn, command_id, row["run_id"], row["effect_id"], "nexus.effect.reconciled", {"effect_id": row["effect_id"], "effect_outcome": "UNKNOWN", "reconciliation_status": status}, class_ref)
                result = {"effect_id": row["effect_id"], "execution_state": "FINISHED", "effect_outcome": "UNKNOWN", "reconciliation_status": status}
                self.store._record_command(conn, command_id, operation, digest, result); conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def _append_fact(self, conn, command_id, run_id, effect_id, event_type, metadata, class_ref):
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run: raise RuntimeDenied("EFFECT_RUN_NOT_FOUND")
        chain = self.authority.validate_delegation_chain(run["grant_id"])
        event = self.trace._make_event(conn, event_id="evt-" + command_id, run_id=run_id, seq_no=conn.execute("SELECT COALESCE(MAX(seq_no),0)+1 FROM trace_events WHERE run_id=?", (run_id,)).fetchone()[0], event_type=event_type, actor_id=chain[-1]["granted_to"], object_refs=[], effect_refs=[effect_id], policy_refs=[self.authority.policy["policy_version"]], authority_refs=[run["grant_id"]], classification_ref=class_ref, metadata=metadata)
        self.trace._insert_event(conn, event)

    def _assert_object_classification(self, conn, object_id, run, assertion_id):
        source = conn.execute("SELECT e.classification_assertion_ref FROM object_envelopes e WHERE e.object_id=?", (object_id,)).fetchone()
        run_class = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (run["classification_assertion_ref"],)).fetchone()
        event = conn.execute("SELECT subject_type,subject_ref,policy_version,actor_id,sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
        if not source or not run_class or not event or event["subject_type"] != "TRACE_EVENT" or event["policy_version"] != self.authority.policy["policy_version"]:
            raise RuntimeDenied("EFFECT_CLASSIFICATION_ASSERTION_INVALID")
        src = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (source["classification_assertion_ref"],)).fetchone()
        ranks = self.authority.policy["classification"]["sensitivity_rank"]
        boundary = json.loads(run["data_boundary_json"])
        if not src or src["sensitivity_level"] not in boundary["allowed_classifications"] or not set(json.loads(src["handling_tags_json"])).issubset(set(boundary["handling_tags"])):
            raise RuntimeDenied("EFFECT_PAYLOAD_OUTSIDE_RUN_BOUNDARY")
        if ranks.get(event["sensitivity_level"], -1) < max(ranks.get(src["sensitivity_level"], 99), ranks.get(run_class["sensitivity_level"], 99)) or not set(json.loads(src["handling_tags_json"])).issubset(set(json.loads(event["handling_tags_json"]))):
            raise RuntimeDenied("EFFECT_CLASSIFICATION_DOWNGRADE")

    def _authorize(self, grant_id, task_id, resource, action, command_id):
        return self.authority.evaluate_authorization(grant_id, {"task": task_id, "resource": resource, "action": action, "audience": "nexus-runtime"}, command_id)

    def _authorize_locked(self, grant_id, request, approval_id, payload_hash, command_id):
        authority = self.authority.compute_effective_authority(grant_id)
        for field, scope in (("task", "task_scope"), ("resource", "resource_scope"), ("action", "action_scope"), ("audience", "audience_scope")):
            if request[field] not in authority[scope]: raise AuthorizationDenied("SCOPE_DENIED")
        if approval_id: self.authority._validate_approval(approval_id, grant_id, request, payload_hash)

    def _has_required_actions(self, grant_id, actions):
        scopes = self.authority.compute_effective_authority(grant_id)["action_scope"]
        return set(actions).issubset(scopes)

    def _get(self, effect_id):
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        if not row: raise RuntimeDenied("EFFECT_NOT_FOUND")
        return row

    def _task_id(self, run_id):
        with self.store._connection() as conn:
            row = conn.execute("SELECT task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row: raise RuntimeDenied("EFFECT_RUN_NOT_FOUND")
        return row["task_id"]

    def _descriptor(self, tool_id, version):
        with self.store._connection() as conn:
            row = conn.execute("SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? AND version=?", (tool_id, version)).fetchone()
        if not row: raise RuntimeDenied("EFFECT_TOOL_DESCRIPTOR_NOT_FOUND")
        return json.loads(row["descriptor_json"])

    def _settle_effect_budget(self, effect_id, command_id):
        row = self._get(effect_id)
        with self.store._connection() as conn:
            reservation = conn.execute("SELECT state,amount FROM budget_reservations WHERE reservation_id=?", (row["budget_reservation_ref"],)).fetchone()
        if reservation and reservation["state"] == "RESERVED":
            self.budget.settle(command_id=command_id, reservation_id=row["budget_reservation_ref"], actual_amount=reservation["amount"])

    def _with_state(self, row, execution, outcome, reconciliation):
        doc = json.loads(row["effect_json"]); doc.update(execution_state=execution, effect_outcome=outcome, reconciliation_status=reconciliation)
        self.store._validate("nexus.effect@1.schema.json", doc)
        return _canonical(doc)
