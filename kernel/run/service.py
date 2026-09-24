from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.run.errors import InvalidRunTransition, TraceAdmissionDenied


_TRANSITIONS = {
    "CREATED": {"READY", "CANCELLED"},
    "READY": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING", "VERIFYING", "FAILED", "CANCELLED"},
    "WAITING": {"RUNNING", "FAILED", "CANCELLED"},
    "VERIFYING": {"SUCCEEDED", "FAILED", "CANCELLED", "WAITING"},
}
_METADATA_ALLOWLIST = {
    "nexus.run.created": {"task_id", "executor_kind"},
    "nexus.run.transitioned": {"from_status", "to_status"},
    "nexus.object.created": {"object_type"},
    "nexus.verification.inconclusive": {"reason_code", "verification_ref"},
    "nexus.effect.committed": {"effect_id"},
    "nexus.authority.denied": {"reason_code"},
    "nexus.trace.event_rejected": {"reason_code"},
}
_FORBIDDEN_KEYS = re.compile(r"(?i)(prompt|output|content|payload|document|preview|secret|token|password|authorization|credential|response|transcript)")
_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9]{20,}|AKIA[A-Z0-9]{16}|"
    r"(?:api[_-]?key|password|secret|token)\s*[:=]|BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class TraceRuntime:
    """Transactional Task/Run state, allowlisted Trace admission and replay."""

    def __init__(self, store: ObjectStore, authority: AuthorityService):
        self.store = store
        self.authority = authority
        if authority.store is not store:
            raise ValueError("TraceRuntime and AuthorityService must share one ObjectStore")

    def create_task(self, task: dict[str, Any]) -> None:
        self.store._validate("nexus.task@1.schema.json", task)
        if task["status"] != "CREATED" or "root_run_id" in task:
            raise TraceAdmissionDenied("TASK_MUST_START_CREATED_WITHOUT_ROOT")
        command_id = task["command_id"]
        operation = "create_task"
        request_hash = self.store._request_hash(operation, task)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                requester = conn.execute("SELECT status FROM principals WHERE principal_id=?", (task["requester_id"],)).fetchone()
                if not requester or requester["status"] != "ACTIVE":
                    raise TraceAdmissionDenied("TASK_REQUESTER_NOT_ACTIVE")
                conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES(?,?,'CREATED',?,?,NULL)", (task["task_id"], task["requester_id"], task["created_at"], command_id))
                self.store._record_command(conn, command_id, operation, request_hash, {"task_id": task["task_id"], "status": "CREATED"})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_run(self, run: dict[str, Any], *, command_id: str, event_classification_assertion_ref: str) -> dict[str, Any]:
        self.store._validate("nexus.run@1.schema.json", run)
        if run["status"] != "CREATED":
            raise TraceAdmissionDenied("RUN_MUST_START_CREATED")
        event_id = "evt-" + command_id
        body = {"run": run, "event_classification_assertion_ref": event_classification_assertion_ref}
        operation = "create_run"
        request_hash = self.store._request_hash(operation, body)
        with self.store._connection() as conn:
            prior_result = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior_result is not None:
            return prior_result
        chain = self.authority.validate_delegation_chain(run["grant_id"])
        actor_id = chain[-1]["granted_to"]
        request = {"task": run["task_id"], "resource": run["run_id"], "action": "RUN_CREATE", "audience": "nexus-runtime"}
        self.authority.evaluate_authorization(run["grant_id"], request, command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay
                task = conn.execute("SELECT status,root_run_id FROM tasks WHERE task_id=?", (run["task_id"],)).fetchone()
                if not task:
                    raise TraceAdmissionDenied("TASK_NOT_FOUND")
                if run.get("parent_run_id"):
                    parent = conn.execute("SELECT * FROM runs WHERE run_id=?", (run["parent_run_id"],)).fetchone()
                    if not parent or parent["task_id"] != run["task_id"]:
                        raise TraceAdmissionDenied("PARENT_RUN_INVALID")
                    self._assert_child_boundary(run, parent)
                else:
                    if task["status"] != "CREATED" or task["root_run_id"] is not None:
                        raise TraceAdmissionDenied("TASK_ALREADY_HAS_ROOT_RUN")
                run_class = self._assert_classification(conn, run["classification_assertion_ref"], "RUN", run["run_id"], run["data_boundary"])
                if run_class["actor_id"] not in {identity for grant in chain for identity in (grant["issued_by"], grant["granted_to"])}:
                    raise TraceAdmissionDenied("RUN_CLASSIFICATION_ACTOR_OUTSIDE_AUTHORITY_CHAIN")
                if run.get("parent_run_id"):
                    parent_class_row = conn.execute("SELECT * FROM classification_assertions WHERE assertion_id=?", (parent["classification_assertion_ref"],)).fetchone()
                    parent_class = self._classification_document(parent_class_row)
                    ranks = self.authority.policy["classification"]["sensitivity_rank"]
                    if ranks.get(run_class["sensitivity_level"], -1) < ranks.get(parent_class["sensitivity_level"], 99) or not set(parent_class["handling_tags"]).issubset(set(run_class["handling_tags"])):
                        raise TraceAdmissionDenied("CHILD_RUN_CLASSIFICATION_DOWNGRADE")
                conn.execute(
                    "INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run["run_id"], run["task_id"], run.get("subtask_id"), run.get("parent_run_id"), run["executor_kind"], "CREATED", run["grant_id"], run.get("manifest_ref"), run.get("budget_reservation_ref"), _canonical(run["data_boundary"]), run["classification_assertion_ref"], run["created_at"]),
                )
                event = self._make_event(
                    conn, event_id=event_id, run_id=run["run_id"], seq_no=1,
                    event_type="nexus.run.created", actor_id=actor_id, object_refs=[], effect_refs=[],
                    policy_refs=[self.authority.policy["policy_version"]], authority_refs=[run["grant_id"]],
                    classification_ref=event_classification_assertion_ref,
                    metadata={"task_id": run["task_id"], "executor_kind": run["executor_kind"]},
                )
                self._insert_event(conn, event)
                if not run.get("parent_run_id"):
                    conn.execute("UPDATE tasks SET root_run_id=?,status='ACTIVE' WHERE task_id=? AND status='CREATED'", (run["run_id"], run["task_id"]))
                result = {"run_id": run["run_id"], "status": "CREATED", "event_id": event_id, "seq_no": 1}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _assert_child_boundary(run: dict[str, Any], parent) -> None:
        parent_boundary = json.loads(parent["data_boundary_json"])
        child_boundary = run["data_boundary"]
        if not set(child_boundary["allowed_classifications"]).issubset(set(parent_boundary["allowed_classifications"])):
            raise TraceAdmissionDenied("CHILD_DATA_BOUNDARY_EXPANSION")
        if not set(parent_boundary["handling_tags"]).issubset(set(child_boundary["handling_tags"])):
            raise TraceAdmissionDenied("CHILD_HANDLING_TAG_DOWNGRADE")

    def _assert_classification(self, conn, assertion_id: str, subject_type: str, subject_ref: str, boundary: dict[str, Any]):
        row = conn.execute("SELECT * FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
        if not row or row["subject_type"] != subject_type or row["subject_ref"] != subject_ref:
            raise TraceAdmissionDenied("CLASSIFICATION_ASSERTION_REFERENCE_INVALID")
        assertion = self._classification_document(row)
        self.store._validate("nexus.classification_assertion@1.schema.json", assertion)
        if assertion["policy_version"] != self.authority.policy["policy_version"]:
            raise TraceAdmissionDenied("CLASSIFICATION_POLICY_VERSION_MISMATCH")
        if assertion["sensitivity_level"] not in boundary["allowed_classifications"]:
            raise TraceAdmissionDenied("CLASSIFICATION_OUTSIDE_DATA_BOUNDARY")
        if not set(assertion["handling_tags"]).issubset(set(boundary["handling_tags"])):
            raise TraceAdmissionDenied("CLASSIFICATION_TAGS_OUTSIDE_DATA_BOUNDARY")
        return assertion

    @staticmethod
    def _classification_document(row) -> dict[str, Any]:
        result = {"schema_id": "nexus.classification_assertion", "schema_version": 1, "assertion_id": row["assertion_id"], "subject_type": row["subject_type"], "subject_ref": row["subject_ref"], "sensitivity_level": row["sensitivity_level"], "handling_tags": json.loads(row["handling_tags_json"]), "policy_version": row["policy_version"], "reason": row["reason"], "actor_id": row["actor_id"]}
        if row["supersedes"]:
            result["supersedes"] = row["supersedes"]
        return result

    def _make_event(self, conn, *, event_id: str, run_id: str, seq_no: int, event_type: str, actor_id: str, object_refs: list[str], effect_refs: list[str], policy_refs: list[str], authority_refs: list[str], classification_ref: str, metadata: dict[str, Any]) -> dict[str, Any]:
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        task = conn.execute("SELECT task_id FROM tasks WHERE task_id=?", (run["task_id"],)).fetchone() if run else None
        if not run or not task:
            raise TraceAdmissionDenied("RUN_NOT_FOUND")
        chain = self.authority.validate_delegation_chain(run["grant_id"])
        chain_principals = {identity for grant in chain for identity in (grant["issued_by"], grant["granted_to"])}
        if actor_id != chain[-1]["granted_to"]:
            raise TraceAdmissionDenied("TRACE_ACTOR_MUST_MATCH_RUN_PRINCIPAL")
        actor = conn.execute("SELECT status FROM principals WHERE principal_id=?", (actor_id,)).fetchone()
        if not actor or actor["status"] != "ACTIVE":
            raise TraceAdmissionDenied("TRACE_ACTOR_NOT_ACTIVE")
        allowed = _METADATA_ALLOWLIST.get(event_type)
        if allowed is None or not isinstance(metadata, dict) or len(metadata) > self.authority.policy["trace"]["max_fields"]:
            raise TraceAdmissionDenied("TRACE_EVENT_TYPE_OR_METADATA_INVALID")
        if any(not isinstance(key, str) or key not in allowed or _FORBIDDEN_KEYS.search(key) for key in metadata):
            raise TraceAdmissionDenied("TRACE_METADATA_KEY_NOT_ALLOWED")
        self._validate_metadata_values(metadata)
        boundary = json.loads(run["data_boundary_json"])
        run_class = self._assert_classification(conn, run["classification_assertion_ref"], "RUN", run_id, boundary)
        event_class = self._assert_classification(conn, classification_ref, "TRACE_EVENT", event_id, boundary)
        if event_class["actor_id"] not in chain_principals:
            raise TraceAdmissionDenied("TRACE_CLASSIFICATION_ACTOR_OUTSIDE_AUTHORITY_CHAIN")
        class_ranks = self.authority.policy["classification"]["sensitivity_rank"]
        required_tags = set(run_class["handling_tags"])
        highest = run_class["sensitivity_level"]
        object_refs = sorted(set(object_refs))
        if any(len(refs) > self.authority.policy["trace"]["max_fields"] for refs in (object_refs, effect_refs, policy_refs, authority_refs)):
            raise TraceAdmissionDenied("TRACE_REFERENCE_COUNT_EXCEEDED")
        for object_id in object_refs:
            source = conn.execute("SELECT e.classification_assertion_ref,e.object_type,e.created_by_run,s.payload_state FROM object_envelopes e JOIN object_states s USING(object_id) WHERE e.object_id=?", (object_id,)).fetchone()
            if not source or source["payload_state"] == "PURGED":
                raise TraceAdmissionDenied("TRACE_OBJECT_REFERENCE_UNAVAILABLE")
            if event_type == "nexus.object.created" and (len(object_refs) != 1 or source["created_by_run"] != run_id or metadata.get("object_type") != source["object_type"]):
                raise TraceAdmissionDenied("TRACE_OBJECT_CREATED_FACT_MISMATCH")
            object_class_row = conn.execute("SELECT * FROM classification_assertions WHERE assertion_id=?", (source["classification_assertion_ref"],)).fetchone()
            if not object_class_row:
                raise TraceAdmissionDenied("TRACE_OBJECT_CLASSIFICATION_MISSING")
            object_class = self._classification_document(object_class_row)
            self.store._validate("nexus.classification_assertion@1.schema.json", object_class)
            if object_class["subject_type"] != "OBJECT" or object_class["subject_ref"] != object_id or object_class["policy_version"] != self.authority.policy["policy_version"]:
                raise TraceAdmissionDenied("TRACE_OBJECT_CLASSIFICATION_INVALID")
            if object_class["sensitivity_level"] not in boundary["allowed_classifications"] or not set(object_class["handling_tags"]).issubset(set(boundary["handling_tags"])):
                raise TraceAdmissionDenied("TRACE_OBJECT_OUTSIDE_DATA_BOUNDARY")
            highest = max((highest, object_class["sensitivity_level"]), key=lambda level: class_ranks.get(level, -1))
            required_tags.update(object_class["handling_tags"])
        if event_type == "nexus.object.created":
            prior_events = conn.execute("SELECT event_json FROM trace_events WHERE run_id=? AND event_type='nexus.object.created'", (run_id,)).fetchall()
            already_announced = {object_id for row in prior_events for object_id in json.loads(row["event_json"])["object_refs"]}
            if set(object_refs).intersection(already_announced):
                raise TraceAdmissionDenied("TRACE_OBJECT_ALREADY_ANNOUNCED")
        if class_ranks.get(event_class["sensitivity_level"], -1) < class_ranks.get(highest, 99) or not required_tags.issubset(set(event_class["handling_tags"])):
            raise TraceAdmissionDenied("TRACE_CLASSIFICATION_DOWNGRADE")
        event = {
            "schema_id": "nexus.trace_event", "schema_version": 1, "event_id": event_id,
            "run_id": run_id, "seq_no": seq_no, "event_type": event_type, "occurred_at": _now(),
            "actor_id": actor_id, "object_refs": object_refs, "effect_refs": sorted(set(effect_refs)),
            "policy_refs": sorted(set(policy_refs)), "authority_refs": sorted(set(authority_refs)),
            "data_boundary": boundary, "classification_assertion_ref": classification_ref,
            "typed_metadata": metadata,
        }
        self.store._validate("nexus.trace_event@1.schema.json", event)
        self._validate_trace_tree(event)
        encoded = _canonical(event).encode("utf-8")
        if len(encoded) > self.authority.policy["trace"]["max_string_bytes"] * self.authority.policy["trace"]["max_fields"]:
            raise TraceAdmissionDenied("TRACE_EVENT_TOO_LARGE")
        return event

    def _validate_metadata_values(self, metadata: dict[str, Any]) -> None:
        max_bytes = self.authority.policy["trace"]["max_string_bytes"]

        def visit(value: Any, depth: int) -> None:
            if depth > self.authority.policy["trace"]["max_depth"]:
                raise TraceAdmissionDenied("TRACE_METADATA_DEPTH_EXCEEDED")
            if isinstance(value, str):
                if len(value.encode("utf-8")) > max_bytes or _SECRET_VALUE.search(value):
                    raise TraceAdmissionDenied("TRACE_SECRET_OR_STRING_LIMIT")
                return
            if value is None or isinstance(value, bool) or (isinstance(value, int) and not isinstance(value, bool)):
                return
            if isinstance(value, float) and math.isfinite(value):
                return
            raise TraceAdmissionDenied("TRACE_METADATA_VALUE_NOT_SCALAR")

        for value in metadata.values():
            visit(value, 1)

    def _validate_trace_tree(self, value: Any) -> None:
        max_bytes = self.authority.policy["trace"]["max_string_bytes"]
        max_depth = self.authority.policy["trace"]["max_depth"]

        def visit(item: Any, depth: int) -> None:
            if depth > max_depth:
                raise TraceAdmissionDenied("TRACE_EVENT_DEPTH_EXCEEDED")
            if isinstance(item, str):
                if len(item.encode("utf-8")) > max_bytes or _SECRET_VALUE.search(item):
                    raise TraceAdmissionDenied("TRACE_SECRET_OR_STRING_LIMIT")
            elif isinstance(item, dict):
                if len(item) > self.authority.policy["trace"]["max_fields"]:
                    raise TraceAdmissionDenied("TRACE_FIELD_LIMIT_EXCEEDED")
                for key, child in item.items():
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            elif isinstance(item, list):
                if len(item) > self.authority.policy["trace"]["max_fields"]:
                    raise TraceAdmissionDenied("TRACE_FIELD_LIMIT_EXCEEDED")
                for child in item:
                    visit(child, depth + 1)
            elif item is None or isinstance(item, bool) or isinstance(item, int):
                return
            elif isinstance(item, float) and math.isfinite(item):
                return
            else:
                raise TraceAdmissionDenied("TRACE_VALUE_INVALID")

        visit(value, 1)

    @staticmethod
    def _insert_event(conn, event: dict[str, Any]) -> None:
        conn.execute("INSERT INTO trace_events(event_id,run_id,seq_no,event_type,occurred_at,actor_id,event_json) VALUES(?,?,?,?,?,?,?)", (event["event_id"], event["run_id"], event["seq_no"], event["event_type"], event["occurred_at"], event["actor_id"], _canonical(event)))

    def append_trace_event(self, *, command_id: str, run_id: str, event_type: str, classification_assertion_ref: str, typed_metadata: dict[str, Any], object_refs: list[str] | None = None, effect_refs: list[str] | None = None, policy_refs: list[str] | None = None, authority_refs: list[str] | None = None, actor_id: str | None = None) -> dict[str, Any]:
        if event_type != "nexus.object.created":
            raise TraceAdmissionDenied("TRACE_EVENT_TYPE_RESERVED_FOR_KERNEL")
        event_id = "evt-" + command_id
        request = {"run_id": run_id, "event_type": event_type, "classification_assertion_ref": classification_assertion_ref, "typed_metadata": typed_metadata, "object_refs": object_refs or [], "effect_refs": effect_refs or [], "policy_refs": policy_refs or [], "authority_refs": authority_refs or [], "actor_id": actor_id}
        operation = "append_trace_event"
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior_result = self.store._replay_command(conn, command_id, operation, request_hash)
            if prior_result is not None:
                return prior_result
            preflight_run = conn.execute("SELECT grant_id,task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not preflight_run:
            raise TraceAdmissionDenied("RUN_NOT_FOUND")
        self.authority.evaluate_authorization(preflight_run["grant_id"], {"task": preflight_run["task_id"], "resource": run_id, "action": "TRACE_APPEND", "audience": "nexus-runtime"}, command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay
                run = conn.execute("SELECT grant_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not run:
                    raise TraceAdmissionDenied("RUN_NOT_FOUND")
                chain = self.authority.validate_delegation_chain(run["grant_id"])
                effective_actor = actor_id or chain[-1]["granted_to"]
                seq_no = conn.execute("SELECT COALESCE(MAX(seq_no),0)+1 FROM trace_events WHERE run_id=?", (run_id,)).fetchone()[0]
                event = self._make_event(conn, event_id=event_id, run_id=run_id, seq_no=seq_no, event_type=event_type, actor_id=effective_actor, object_refs=object_refs or [], effect_refs=effect_refs or [], policy_refs=sorted(set(policy_refs or []).union({self.authority.policy["policy_version"]})), authority_refs=sorted(set(authority_refs or []).union({run["grant_id"]})), classification_ref=classification_assertion_ref, metadata=typed_metadata)
                self._insert_event(conn, event)
                self.store._record_command(conn, command_id, operation, request_hash, {"event_id": event_id, "run_id": run_id, "seq_no": seq_no})
                conn.commit()
                return {"event_id": event_id, "run_id": run_id, "seq_no": seq_no}
            except Exception:
                conn.rollback()
                raise

    def transition_run(self, *, command_id: str, run_id: str, expected_state: str, next_state: str, classification_assertion_ref: str) -> dict[str, Any]:
        operation = "transition_run"
        request = {"run_id": run_id, "expected_state": expected_state, "next_state": next_state, "classification_assertion_ref": classification_assertion_ref}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior_result = self.store._replay_command(conn, command_id, operation, request_hash)
            if prior_result is not None:
                return prior_result
            preflight_run = conn.execute("SELECT grant_id,task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not preflight_run:
            raise TraceAdmissionDenied("RUN_NOT_FOUND")
        self.authority.evaluate_authorization(preflight_run["grant_id"], {"task": preflight_run["task_id"], "resource": run_id, "action": "RUN_TRANSITION", "audience": "nexus-runtime"}, command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay
                run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if not run:
                    raise TraceAdmissionDenied("RUN_NOT_FOUND")
                chain = self.authority.validate_delegation_chain(run["grant_id"])
                if run["status"] != expected_state or next_state not in _TRANSITIONS.get(expected_state, set()):
                    raise InvalidRunTransition("INVALID_RUN_TRANSITION")
                event_id = "evt-" + command_id
                seq_no = conn.execute("SELECT COALESCE(MAX(seq_no),0)+1 FROM trace_events WHERE run_id=?", (run_id,)).fetchone()[0]
                event = self._make_event(conn, event_id=event_id, run_id=run_id, seq_no=seq_no, event_type="nexus.run.transitioned", actor_id=chain[-1]["granted_to"], object_refs=[], effect_refs=[], policy_refs=[self.authority.policy["policy_version"]], authority_refs=[run["grant_id"]], classification_ref=classification_assertion_ref, metadata={"from_status": expected_state, "to_status": next_state})
                conn.execute("UPDATE runs SET status=? WHERE run_id=? AND status=?", (next_state, run_id, expected_state))
                if conn.execute("SELECT changes()").fetchone()[0] != 1:
                    raise InvalidRunTransition("RUN_STATE_RACE")
                if run["parent_run_id"] is None:
                    task_state = {"WAITING": "WAITING", "SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}.get(next_state, "ACTIVE")
                    current_task = conn.execute("SELECT status FROM tasks WHERE task_id=?", (run["task_id"],)).fetchone()
                    if current_task and current_task["status"] != task_state:
                        conn.execute("UPDATE tasks SET status=? WHERE task_id=?", (task_state, run["task_id"]))
                self._insert_event(conn, event)
                result = {"run_id": run_id, "status": next_state, "event_id": event_id, "seq_no": seq_no}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _task_id(conn, run_id: str) -> str:
        row = conn.execute("SELECT task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise TraceAdmissionDenied("RUN_NOT_FOUND")
        return row["task_id"]

    def replay_run(self, run_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            run = conn.execute("SELECT status,task_id,executor_kind FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise TraceAdmissionDenied("RUN_NOT_FOUND")
            rows = conn.execute("SELECT seq_no,event_json FROM trace_events WHERE run_id=? ORDER BY seq_no", (run_id,)).fetchall()
        if not rows or rows[0]["seq_no"] != 1:
            raise TraceAdmissionDenied("TRACE_SEQUENCE_INCOMPLETE")
        state = None
        expected_seq = 1
        for row in rows:
            if row["seq_no"] != expected_seq:
                raise TraceAdmissionDenied("TRACE_SEQUENCE_GAP")
            event = json.loads(row["event_json"])
            self.store._validate("nexus.trace_event@1.schema.json", event)
            if event["run_id"] != run_id or event["seq_no"] != row["seq_no"]:
                raise TraceAdmissionDenied("TRACE_REPLAY_EVENT_IDENTITY_MISMATCH")
            if expected_seq == 1:
                if event["event_type"] != "nexus.run.created" or event["typed_metadata"].get("executor_kind") != run["executor_kind"] or event["typed_metadata"].get("task_id") != run["task_id"]:
                    raise TraceAdmissionDenied("TRACE_REPLAY_INVALID_ROOT_EVENT")
                state = "CREATED"
            elif event["event_type"] == "nexus.run.transitioned":
                before = event["typed_metadata"].get("from_status")
                after = event["typed_metadata"].get("to_status")
                if state != before or after not in _TRANSITIONS.get(before, set()):
                    raise TraceAdmissionDenied("TRACE_REPLAY_INVALID_TRANSITION")
                state = after
            elif event["event_type"] != "nexus.object.created":
                raise TraceAdmissionDenied("TRACE_REPLAY_UNSUPPORTED_EVENT")
            expected_seq += 1
        if state != run["status"]:
            raise TraceAdmissionDenied("TRACE_REPLAY_PROJECTION_MISMATCH")
        return {"run_id": run_id, "status": state, "last_seq": expected_seq - 1, "event_count": len(rows)}

    def replay_task(self, task_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            task = conn.execute("SELECT status,root_run_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise TraceAdmissionDenied("TASK_NOT_FOUND")
            if task["root_run_id"] is None:
                replayed = "CREATED"
            else:
                run = conn.execute("SELECT status FROM runs WHERE run_id=? AND task_id=? AND parent_run_id IS NULL", (task["root_run_id"], task_id)).fetchone()
                events = conn.execute("SELECT event_json FROM trace_events WHERE run_id=? ORDER BY seq_no", (task["root_run_id"],)).fetchall()
                if not run or not events:
                    raise TraceAdmissionDenied("TASK_ROOT_RUN_MISSING")
                first_event = json.loads(events[0]["event_json"])
                self.store._validate("nexus.trace_event@1.schema.json", first_event)
                if first_event["seq_no"] != 1 or first_event["event_type"] != "nexus.run.created":
                    raise TraceAdmissionDenied("TASK_ROOT_RUN_CREATE_EVENT_MISSING")
                root_status = "CREATED"
                for sequence, item in enumerate(events, 1):
                    event = json.loads(item["event_json"])
                    self.store._validate("nexus.trace_event@1.schema.json", event)
                    if event["run_id"] != task["root_run_id"] or event["seq_no"] != sequence:
                        raise TraceAdmissionDenied("TASK_TRACE_REPLAY_IDENTITY_MISMATCH")
                    if sequence == 1:
                        continue
                    if event["event_type"] == "nexus.run.transitioned":
                        transition = event["typed_metadata"]
                        if root_status != transition.get("from_status") or transition.get("to_status") not in _TRANSITIONS.get(root_status, set()):
                            raise TraceAdmissionDenied("TASK_TRACE_REPLAY_INVALID_TRANSITION")
                        root_status = transition["to_status"]
                if root_status != run["status"]:
                    raise TraceAdmissionDenied("TASK_ROOT_RUN_PROJECTION_MISMATCH")
                replayed = {"WAITING": "WAITING", "SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}.get(root_status, "ACTIVE")
        if replayed != task["status"]:
            raise TraceAdmissionDenied("TASK_REPLAY_PROJECTION_MISMATCH")
        return {"task_id": task_id, "status": replayed, "root_run_id": task["root_run_id"]}
