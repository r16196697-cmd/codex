"""Bounded ExperienceProjection over existing canonical Nexus records.

This module is a read model only. It never creates an Experience record or
copies canonical facts into a second store. Callers must provide an existing
read-only ObjectStore opened through the reviewed instance-binding path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from kernel.metering.service import unavailable_metrics


_MAX_SOURCE_ROWS = 2000
_MAX_OUTPUT_ROWS = 100
_MAX_METERING_ROWS = 20
_MAX_CONTEXT_ROWS = 20
_MAX_TRACE_TYPES = 64
_MAX_JSON_BYTES = 65536
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_REASON_CODES = frozenset({
    "INITIAL", "RETRY", "UNSPECIFIED", "CODEX_HOST_DECLARED",
    "CODEX_HOST_TOOL_DECLARED", "LEGACY_SINGLE_RUN",
    "LEGACY_SINGLE_RUN_BACKFILL", "VERIFIER_REQUIRED_ESCALATION",
    "POLICY_DENIED", "BUDGET_DENIED", "AUTHORIZATION_DENIED",
    "DAILY_TASK_RESOURCE_SCOPE_INVALID", "DAILY_TASK_LOGICAL_ID_CONFLICT",
    "CONTINUATION_PLAN_INVALID", "CONTINUATION_STATE_CONFLICT",
})
_SAFE_METERING_UNITS = frozenset({
    "tokens", "milliseconds", "bytes", "runs", "records", "candidates", "credits",
    "microcredits", "calls", "boolean",
})
_SAFE_METERING_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CNY", "JPY"})
_SAFE_METERING_METRIC_NAMES = frozenset(unavailable_metrics())
_KNOWN_ACTIONS = frozenset({
    "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
    "INSPECT", "VERIFY", "MEMORY_ADMIT", "MEMORY_SEARCH", "TOOL_READ",
    "EFFECT_PREPARE", "EFFECT_COMMIT", "DELEGATE", "EGRESS",
    "RUNTIME_CONFIGURE", "INSPECT_PROTECTED", "SKILL_ADMIN",
})


class ExperienceProjectionError(RuntimeError):
    """Stable, sanitized failure from the Experience read surface."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason: str) -> None:
    raise ExperienceProjectionError(reason)


def _strict_json(raw: str | bytes) -> Any:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("non-standard JSON constant")

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    return json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_id(value: Any) -> str | None:
    if isinstance(value, str) and _LOGICAL_ID.fullmatch(value):
        return value
    return None


def _safe_reason(value: Any) -> str:
    return value if isinstance(value, str) and value in _SAFE_REASON_CODES else "UNKNOWN"


def _safe_label(value: Any) -> str:
    if isinstance(value, str) and _LOGICAL_ID.fullmatch(value) and "*" not in value:
        return value
    return "UNKNOWN"


def _bounded(rows: list[dict[str, Any]], limit: int = _MAX_OUTPUT_ROWS) -> dict[str, Any]:
    return {"items": rows[:limit], "total_count": len(rows), "truncated": len(rows) > limit}


def _bounded_refs(values, limit: int = _MAX_OUTPUT_ROWS) -> dict[str, Any]:
    safe = sorted({value for value in values if _safe_id(value)})
    return {"items": safe[:limit], "total_count": len(safe), "truncated": len(safe) > limit}


def _safe_metering_metrics(metrics: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    """Project typed Metering facts without copying Host-controlled text values."""
    projected: dict[str, Any] = {}
    redacted = False
    unrecognized_count = 0
    for name in sorted(metrics):
        metric = metrics[name]
        if name not in _SAFE_METERING_METRIC_NAMES:
            unrecognized_count += 1
            redacted = True
            continue
        value = metric["value"]
        if value is not None and (isinstance(value, bool) or isinstance(value, (int, float))):
            if isinstance(value, float) and not math.isfinite(value):
                value, value_status, redacted = None, "REDACTED", True
            else:
                value_status = "UNAVAILABLE" if metric["provenance"] == "UNAVAILABLE" else "AVAILABLE"
        elif value is None:
            value_status = "UNAVAILABLE"
        else:
            value, value_status, redacted = None, "REDACTED", True

        raw_unit = metric["unit"]
        if raw_unit is None:
            unit, unit_status = None, "NOT_RECORDED"
        elif raw_unit in _SAFE_METERING_UNITS or raw_unit in _SAFE_METERING_CURRENCIES:
            unit, unit_status = raw_unit, "KNOWN_SAFE"
        else:
            unit, unit_status, redacted = "OTHER", "REDACTED", True

        raw_basis = metric["basis"]
        if raw_basis is not None:
            redacted = True
        estimation_basis = metric["estimation_basis"]
        if estimation_basis is not None:
            redacted = True
        projected[name] = {
            "value": value,
            "value_status": value_status,
            "provenance": metric["provenance"],
            "unit": unit,
            "unit_status": unit_status,
            "basis": {"present": raw_basis is not None},
            "estimate_status": metric["estimate_status"],
            "estimation_basis": {
                "present": estimation_basis is not None,
            },
        }
    return projected, redacted, unrecognized_count


@dataclass(frozen=True)
class LocalOperatorReadContext:
    """Explicit local-CLI trust boundary; do not reuse for a remote/MCP caller.

    The local operator is trusted by the host OS to invoke the configured CLI
    against an already verified instance. Task execution Grants are not reader
    credentials. Remote adapters must provide their own authenticated context.
    """

    caller_kind: str = "LOCAL_OPERATOR"

    def authorize_task_read(self, task_id: str) -> bool:
        return self.caller_kind == "LOCAL_OPERATOR" and _safe_id(task_id) is not None


def _parse_boundary(raw: str) -> dict[str, set[str]]:
    try:
        boundary = _strict_json(raw)
    except Exception:
        _fail("EXPERIENCE_DATA_BOUNDARY_INVALID")
    classifications = boundary.get("allowed_classifications") if isinstance(boundary, dict) else None
    tags = boundary.get("handling_tags") if isinstance(boundary, dict) else None
    allowed = {"PUBLIC", "PERSONAL", "PROJECT_PRIVATE", "CONFIDENTIAL", "SECRET"}
    if (not isinstance(boundary, dict) or set(boundary) != {"allowed_classifications", "handling_tags"}
            or not isinstance(classifications, list) or len(set(classifications)) != len(classifications)
            or not set(classifications).issubset(allowed) or not isinstance(tags, list)
            or any(not isinstance(item, str) or not item for item in tags) or len(set(tags)) != len(tags)):
        _fail("EXPERIENCE_DATA_BOUNDARY_INVALID")
    return {"classifications": set(classifications), "tags": set(tags)}


def _visible(classification: dict[str, Any], boundary: dict[str, set[str]]) -> bool:
    try:
        tags = _strict_json(classification["handling_tags_json"])
    except Exception:
        _fail("EXPERIENCE_CLASSIFICATION_INVALID")
    return (
        classification.get("sensitivity_level") in boundary["classifications"]
        and isinstance(tags, list) and set(tags).issubset(boundary["tags"])
    )


def _load_limit(conn, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = [dict(row) for row in conn.execute(sql, (*parameters, _MAX_SOURCE_ROWS + 1)).fetchall()]
    if len(rows) > _MAX_SOURCE_ROWS:
        _fail("EXPERIENCE_SOURCE_LIMIT_EXCEEDED")
    return rows


class ExperienceProjectionService:
    """Project one exact Task from read-only canonical application records.

    ``project_task`` requires an explicit caller read context. The Task's
    execution Grant is never treated as proof that its holder may read this
    projection. The local CLI supplies ``LocalOperatorReadContext`` under the
    host OS-user trust model; any remote/MCP adapter must authenticate its
    caller and supply a separate authorized context before invoking this API.
    """

    def __init__(self, store, *, read_context=None, context_packs=None):
        self.store = store
        self.read_context = read_context
        self.context_packs = context_packs

    def project_task(self, task_id: str) -> dict[str, Any]:
        if not getattr(self.store, "read_only", False):
            _fail("EXPERIENCE_READ_ONLY_STORE_REQUIRED")
        if not isinstance(task_id, str) or not _safe_id(task_id):
            _fail("EXPERIENCE_TASK_ID_INVALID")
        if self.read_context is None:
            _fail("EXPERIENCE_READER_AUTHORIZATION_REQUIRED")
        authorize = getattr(self.read_context, "authorize_task_read", None)
        if not callable(authorize):
            _fail("EXPERIENCE_READER_AUTHORIZATION_INVALID")
        try:
            authorized = authorize(task_id)
        except Exception:
            _fail("EXPERIENCE_READER_AUTHORIZATION_DENIED")
        if authorized is not True:
            _fail("EXPERIENCE_READER_AUTHORIZATION_DENIED")
        self.store._require_mode("inspect")
        try:
            return self._project_task(task_id)
        except ExperienceProjectionError:
            raise
        except Exception as exc:
            reason = getattr(exc, "reason_code", None)
            if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
                _fail(reason)
            _fail("EXPERIENCE_CANONICAL_RECORD_INVALID")

    def _project_task(self, task_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            conn.execute("BEGIN")
            task_rows = conn.execute(
                "SELECT task_id,requester_id,status,created_at,command_id,root_run_id FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchall()
            if len(task_rows) != 1:
                _fail("EXPERIENCE_TASK_NOT_FOUND")
            task = dict(task_rows[0])
            task_doc = {
                "schema_id": "nexus.task", "schema_version": 1, "task_id": task["task_id"],
                "requester_id": task["requester_id"], "status": task["status"],
                "created_at": task["created_at"], "command_id": task["command_id"],
                "root_run_id": task["root_run_id"],
            }
            self.store._validate("nexus.task@1.schema.json", task_doc)
            if not task.get("root_run_id"):
                _fail("EXPERIENCE_ROOT_RUN_UNAVAILABLE")

            runs = _load_limit(conn,
                "SELECT run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,"
                "budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at "
                "FROM runs WHERE task_id=? ORDER BY created_at,run_id LIMIT ?", (task_id,))
            if not runs:
                _fail("EXPERIENCE_ROOT_RUN_UNAVAILABLE")
            run_ids = {row["run_id"] for row in runs}
            runs_by_id = {row["run_id"]: row for row in runs}
            root_candidates = [row for row in runs if row["run_id"] == task["root_run_id"]]
            if len(root_candidates) != 1 or root_candidates[0]["parent_run_id"] is not None:
                _fail("EXPERIENCE_TASK_RUN_BINDING_INVALID")
            root = root_candidates[0]
            if root["executor_kind"] != "ORCHESTRATOR" or root["task_id"] != task_id:
                _fail("EXPERIENCE_TASK_RUN_BINDING_INVALID")

            root_boundary = _parse_boundary(root["data_boundary_json"])
            run_classes: dict[str, dict[str, Any]] = {}
            for run in runs:
                try:
                    boundary_doc = _strict_json(run["data_boundary_json"])
                    run_doc = {
                        "schema_id": "nexus.run", "schema_version": 1, "run_id": run["run_id"],
                        "task_id": run["task_id"], "executor_kind": run["executor_kind"],
                        "status": run["status"], "grant_id": run["grant_id"],
                        "data_boundary": boundary_doc,
                        "classification_assertion_ref": run["classification_assertion_ref"],
                        "created_at": run["created_at"],
                    }
                    for optional in ("subtask_id", "parent_run_id", "manifest_ref", "budget_reservation_ref"):
                        if run[optional] is not None:
                            run_doc[optional] = run[optional]
                    self.store._validate("nexus.run@1.schema.json", run_doc)
                except Exception:
                    _fail("EXPERIENCE_RUN_RECORD_INVALID")
                boundary = _parse_boundary(run["data_boundary_json"])
                if not boundary["classifications"].issubset(root_boundary["classifications"]) or not boundary["tags"].issubset(root_boundary["tags"]):
                    _fail("EXPERIENCE_RUN_BOUNDARY_INVALID")
                cls = conn.execute(
                    "SELECT assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json "
                    "FROM classification_assertions WHERE assertion_id=?",
                    (run["classification_assertion_ref"],),
                ).fetchone()
                if not cls or cls["subject_type"] != "RUN" or cls["subject_ref"] != run["run_id"]:
                    _fail("EXPERIENCE_RUN_CLASSIFICATION_INVALID")
                if not _visible(dict(cls), root_boundary):
                    _fail("EXPERIENCE_CLASSIFICATION_OUTSIDE_BOUNDARY")
                run_classes[run["run_id"]] = dict(cls)
                if run["parent_run_id"] is not None and run["parent_run_id"] not in run_ids:
                    _fail("EXPERIENCE_RUN_PARENT_INVALID")

            grants = {}
            for grant_id in sorted({run["grant_id"] for run in runs}):
                row = conn.execute(
                    "SELECT grant_id,parent_grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,"
                    "action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version,credential_ref "
                    "FROM delegation_grants WHERE grant_id=?", (grant_id,),
                ).fetchone()
                if not row:
                    _fail("EXPERIENCE_GRANT_BINDING_INVALID")
                grant = dict(row)
                try:
                    parsed = {key: _strict_json(grant[key + "_json"]) for key in
                              ("task_scope", "resource_scope", "action_scope", "audience_scope")}
                    grant_doc = {
                        "schema_id": "nexus.delegation_grant", "schema_version": 1,
                        "grant_id": grant["grant_id"],
                        "issued_by": grant["issued_by"], "granted_to": grant["granted_to"],
                        **parsed, "issued_at": grant["issued_at"], "expires_at": grant["expires_at"],
                        "status": grant["status"], "policy_version": grant["policy_version"],
                    }
                    if grant["parent_grant_id"] is not None:
                        grant_doc["parent_grant_id"] = grant["parent_grant_id"]
                    if grant["credential_ref"] is not None:
                        grant_doc["credential_ref"] = grant["credential_ref"]
                    self.store._validate("nexus.delegation_grant@1.schema.json", grant_doc)
                except Exception:
                    _fail("EXPERIENCE_GRANT_RECORD_INVALID")
                if task_id not in parsed["task_scope"] or "*" in parsed["task_scope"]:
                    _fail("EXPERIENCE_GRANT_TASK_SCOPE_INVALID")
                grant["parsed"] = parsed
                grants[grant_id] = grant

            # All associations are constrained by the requested Task ID. No query
            # text or operator-supplied SQL is accepted by this service.
            subtasks = _load_limit(conn,
                "SELECT subtask_id,task_id,node_index,node_json,status,scheduled_run_id,final_attempt_id,"
                "final_outcome,finalized_at,created_at FROM subtasks WHERE task_id=? ORDER BY node_index,subtask_id LIMIT ?",
                (task_id,))
            attempts = _load_limit(conn,
                "SELECT attempt_id,task_id,subtask_id,attempt_no,run_id,route_decision_ref,requested_capability,"
                "attempt_reason,predecessor_attempt_id,outcome,created_at FROM subtask_attempts "
                "WHERE task_id=? ORDER BY subtask_id,attempt_no,attempt_id LIMIT ?", (task_id,))
            trace_rows = _load_limit(conn,
                "SELECT event_id,run_id,seq_no,event_json FROM trace_events "
                "WHERE run_id IN (SELECT run_id FROM runs WHERE task_id=?) ORDER BY run_id,seq_no,event_id LIMIT ?",
                (task_id,))
            verification_rows = _load_limit(conn,
                "SELECT verification_id,target_ref,verdict,verifier_kind,evidence_used_json,independence_json,"
                "result_json,attester_principal_id,approval_ref,run_id,created_at FROM verification_results "
                "WHERE run_id IN (SELECT run_id FROM runs WHERE task_id=?) ORDER BY created_at,verification_id LIMIT ?",
                (task_id,))
            effect_rows = _load_limit(conn,
                "SELECT effect_id,run_id,tool_id,action_type,grant_id,approval_ref,execution_state,effect_outcome,"
                "reconciliation_status,effect_json,created_at,updated_at FROM effects "
                "WHERE run_id IN (SELECT run_id FROM runs WHERE task_id=?) ORDER BY created_at,effect_id LIMIT ?",
                (task_id,))
            approval_rows = _load_limit(conn,
                "SELECT a.approval_id,a.approver_principal_id,a.target_type,a.target_ref,a.effect_id,a.payload_integrity_hash,a.decision,"
                "a.approved_scope_json,a.policy_version,a.issued_at,a.expires_at,a.reason,a.request_ref "
                "FROM approval_decisions a WHERE a.effect_id IN "
                "(SELECT e.effect_id FROM effects e JOIN runs r ON r.run_id=e.run_id WHERE r.task_id=?) "
                "OR a.target_ref IN (SELECT e.object_id FROM object_envelopes e JOIN runs r ON r.run_id=e.created_by_run WHERE r.task_id=?) "
                "OR a.target_ref IN (SELECT run_id FROM runs WHERE task_id=?) "
                "ORDER BY a.issued_at,a.approval_id LIMIT ?", (task_id, task_id, task_id))
            account_rows = conn.execute(
                "SELECT account_id,amount_limit,reserved,consumed,unit,model_call_limit,model_calls_reserved,"
                "model_calls_consumed,tool_call_limit,tool_calls_reserved,tool_calls_consumed,child_run_limit,"
                "child_runs_reserved,child_runs_consumed FROM budget_accounts WHERE task_id=?", (task_id,)
            ).fetchall()
            reservation_rows = _load_limit(conn,
                "SELECT reservation_id,run_id,amount,model_calls,tool_calls,child_runs,state,created_at "
                "FROM budget_reservations WHERE account_id IN (SELECT account_id FROM budget_accounts WHERE task_id=?) "
                "ORDER BY created_at,reservation_id LIMIT ?", (task_id,))
            metering_rows = _load_limit(conn,
                "SELECT record_id,task_id,run_id,record_source,participation_mode,context_pack_ref,metrics_json,"
                "metrics_sha256,recorded_at FROM value_metering_records WHERE task_id=? "
                "ORDER BY recorded_at,record_id LIMIT ?", (task_id,))
            context_rows = _load_limit(conn,
                "SELECT record_id,pack_ref,task_id,run_id,content_hash,serialized_byte_size,state,compiled_at "
                "FROM context_pack_records WHERE task_id=? ORDER BY compiled_at,pack_ref LIMIT ?", (task_id,))
            skill_rows = _load_limit(conn,
                "SELECT selection_id,task_id,run_id,query_sha256,candidate_count,result_status,resolution,skill_id,"
                "host_inventory_provenance,host_native_availability,instruction_object_ref,instruction_sha256,"
                "instruction_byte_size,instruction_load_status,delivery_status,model_visible_exposure,"
                "selection_latency_ms,created_at FROM skill_resolution_records WHERE task_id=? "
                "ORDER BY created_at,selection_id LIMIT ?", (task_id,))
            route_rows = _load_limit(conn,
                "SELECT d.route_decision_id,d.subtask_id,d.decision_object_id,d.decision_json,d.created_at "
                "FROM route_decisions d JOIN subtasks s ON s.subtask_id=d.subtask_id "
                "WHERE s.task_id=? ORDER BY d.created_at,d.route_decision_id LIMIT ?", (task_id,))
            candidate_rows = _load_limit(conn,
                "SELECT m.candidate_id,m.claim_ref,m.owner,m.classification_assertion_ref,m.verification_ref,"
                "m.truth_state,m.status,m.metadata_json,m.created_at,m.expires_at "
                "FROM memory_candidates m WHERE m.verification_ref IN "
                "(SELECT verification_id FROM verification_results WHERE run_id IN "
                "(SELECT run_id FROM runs WHERE task_id=?)) ORDER BY m.created_at,m.candidate_id LIMIT ?", (task_id,))
            authority_rows = _load_limit(conn,
                "SELECT event_seq,command_id,grant_id,event_type,reason_code,created_at FROM authority_events "
                "WHERE grant_id IN (SELECT DISTINCT grant_id FROM runs WHERE task_id=?) "
                "ORDER BY event_seq LIMIT ?", (task_id,))
            continuation_rows = _load_limit(conn,
                "SELECT e.object_id,e.object_type,e.schema_id,e.schema_version,e.integrity_hash,e.created_by_run,"
                "s.payload_state,c.subject_type,c.subject_ref,c.sensitivity_level,c.handling_tags_json "
                "FROM object_envelopes e JOIN object_states s USING(object_id) "
                "JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE e.created_by_run IN (SELECT run_id FROM runs WHERE task_id=?) "
                "AND e.object_type='artifact' AND e.schema_id='nexus.object' "
                "ORDER BY e.created_by_run,e.created_at,e.object_id LIMIT ?", (task_id,))

            trace_classifications: dict[str, dict[str, Any] | None] = {}
            for item in trace_rows:
                try:
                    event = _strict_json(item["event_json"])
                    self.store._validate("nexus.trace_event@1.schema.json", event)
                except Exception:
                    _fail("EXPERIENCE_TRACE_RECORD_INVALID")
                if (event.get("event_id") != item["event_id"] or event.get("run_id") != item["run_id"]
                        or event.get("seq_no") != item["seq_no"] or item["run_id"] not in run_ids):
                    _fail("EXPERIENCE_TRACE_BINDING_INVALID")
                assertion_id = event["classification_assertion_ref"]
                cls = conn.execute(
                    "SELECT assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json "
                    "FROM classification_assertions WHERE assertion_id=?", (assertion_id,)
                ).fetchone()
                if not cls or cls["subject_type"] != "TRACE_EVENT" or cls["subject_ref"] != item["event_id"]:
                    _fail("EXPERIENCE_TRACE_CLASSIFICATION_INVALID")
                trace_classifications[item["event_id"]] = dict(cls) if _visible(dict(cls), root_boundary) else None

        subtasks_out, attempts_out, route_out, failure_signals = self._subtasks(subtasks, attempts, route_rows, runs_by_id, root_boundary)
        trace = self._trace(trace_rows, trace_classifications)
        verifications = self._verifications(verification_rows, run_ids, root_boundary)
        effects, effect_failures = self._effects(effect_rows, run_ids)
        effect_ids = {row["effect_id"] for row in effect_rows}
        approvals = self._approvals(approval_rows, run_ids, effect_ids, run_classes, root_boundary)
        metering = self._metering(metering_rows, run_ids)
        contexts = self._contexts(context_rows, run_ids)
        skills = self._skills(skill_rows, task_id, runs_by_id, root_boundary)
        memory = self._memory(candidate_rows, verification_rows, root_boundary)
        continuations = self._continuations(continuation_rows, task_id, run_ids, root_boundary)

        if task["status"] in {"FAILED", "CANCELLED"}:
            failure_signals.append({"source_kind": "TASK_LIFECYCLE", "source_ref": task_id,
                                    "reason_code": task["status"], "observed_state": task["status"]})
        for run in runs:
            if run["status"] in {"FAILED", "CANCELLED"}:
                failure_signals.append({"source_kind": "RUN_LIFECYCLE", "source_ref": _safe_id(run["run_id"]) or "REDACTED",
                                        "reason_code": run["status"], "observed_state": run["status"]})
        failure_signals.extend(effect_failures)
        for item in verifications["items"]:
            if item["verdict"] in {"FAIL", "INCONCLUSIVE"}:
                failure_signals.append({"source_kind": "VERIFICATION", "source_ref": item["verification_id"],
                                        "reason_code": item["verdict"], "observed_state": item["verdict"]})
        for item in authority_rows:
            if item["event_type"] == "AUTHORIZATION_DENIED":
                failure_signals.append({"source_kind": "AUTHORITY_EVENT", "source_ref": _safe_id(item["command_id"]) or "REDACTED",
                                        "reason_code": _safe_reason(item["reason_code"]), "observed_state": item["event_type"]})
        failure_signals.sort(key=lambda item: (item["source_kind"], item["source_ref"], item["reason_code"]))

        root_grant = grants[root["grant_id"]]
        governed = task_id in root_grant["parsed"]["task_scope"] and root["task_id"] == task_id
        coverage = {
            "status": "NEXUS_GOVERNED" if governed else "UNKNOWN",
            "evidence": ["CANONICAL_TASK", "TASK_BOUND_ROOT_RUN", "RUN_BOUND_GRANT"] if governed else [],
            "complete_host_activity": "UNKNOWN",
            "host_native_tool_coverage": "UNKNOWN",
            "model_visible_context_consumption": "UNKNOWN",
            "security_visibility": "UNKNOWN",
        }
        human = {
            "interactive_tty_confirmation": "UNKNOWN",
            "approval_decisions": {
                "approve_count": None if approvals["status"] == "REDACTED" else approvals["decision_counts"].get("APPROVE", 0),
                "deny_count": None if approvals["status"] == "REDACTED" else approvals["decision_counts"].get("DENY", 0),
                "records": approvals["items"],
            },
            "t3_human_or_domain_verifications": None if verifications["status"] == "REDACTED" else verifications["verifier_kind_counts"].get("T3_HUMAN_OR_DOMAIN", 0),
            "continuation_operator_assertion": "RECORDED" if continuations["operator_assertion_count"] else "UNKNOWN",
        }
        unknowns = {
            "semantic_task_quality": "UNKNOWN",
            "whole_task_verification": verifications["whole_task_verification"],
            "root_cause": "UNKNOWN",
            "prompt_injection_presence_or_absence": "UNKNOWN",
            "complete_host_activity": "UNKNOWN",
            "model_context_consumption": "UNKNOWN",
            "model_skill_consumption": "UNKNOWN",
            "skill_effectiveness": "UNKNOWN",
            "unpersisted_human_activity": "UNKNOWN",
            "external_effect_truth": "UNKNOWN" if effects["items"] else "NOT_RECORDED",
            "provider_cost_if_missing": "UNKNOWN",
        }
        source_refs = {
            "task": _bounded_refs([task_id]),
            "runs": _bounded_refs(run_ids),
            "subtasks": _bounded_refs(row["subtask_id"] for row in subtasks),
            "attempts": _bounded_refs(row["attempt_id"] for row in attempts),
            "trace_events": _bounded_refs(row["event_id"] for row in trace_rows if trace_classifications.get(row["event_id"]) is not None),
            "verifications": _bounded_refs(item["verification_id"] for item in verifications["items"]),
            "effects": _bounded_refs(row["effect_id"] for row in effect_rows),
            "approval_decisions": _bounded_refs(item["approval_id"] for item in approvals["items"]),
            "grants": _bounded_refs(grants),
            "metering": _bounded_refs(row["record_id"] for row in metering_rows),
            "context_packs": _bounded_refs(row["pack_ref"] for row in context_rows if row["state"] == "COMPILED"),
            "skill_resolutions": _bounded_refs(row["selection_id"] for row in skill_rows),
            "route_decisions": _bounded_refs(row["route_decision_id"] for row in route_out),
            "memory_candidates": _bounded_refs(item["candidate_id"] for item in memory["items"]
                                                  if item["candidate_id"] not in {"REDACTED", "REDACTED_PURGED"}),
            "continuation": _bounded_refs(item["object_ref"] for item in continuations["commits"]),
        }
        projection = {
            "schema_id": "nexus.experience_projection", "schema_version": 1,
            "projection_kind": "DETERMINISTIC_READ_MODEL", "projection_status": "AVAILABLE",
            "identity": {"task_id": task_id, "requester_id": _safe_id(task["requester_id"]) or "REDACTED",
                         "root_run_id": _safe_id(task["root_run_id"]) or "REDACTED", "created_at": task["created_at"]},
            "lifecycle": {
                "task_status": task["status"], "root_run_status": root["status"],
                "task_created_at": task["created_at"],
                "runs": _bounded([{
                    "run_id": _safe_id(run["run_id"]) or "REDACTED",
                    "parent_run_id": _safe_id(run["parent_run_id"]) if run["parent_run_id"] is not None else None,
                    "subtask_id": _safe_id(run["subtask_id"]) if run["subtask_id"] is not None else None,
                    "executor_kind": run["executor_kind"],
                    "status": run["status"], "grant_id": _safe_id(run["grant_id"]) or "REDACTED",
                    "grant_status": grants[run["grant_id"]]["status"], "created_at": run["created_at"],
                } for run in runs]),
                "outcome_interpretation": "LIFECYCLE_ONLY",
                "semantic_quality": "UNKNOWN",
            },
            "subtasks_and_attempts": {
                "subtasks": _bounded(subtasks_out), "attempts": _bounded(attempts_out),
                "routing": ({"status": "REDACTED", "items": [], "total_count": None, "truncated": False}
                            if any(item.get("availability") != "AVAILABLE" for item in route_out)
                            else {"status": "AVAILABLE" if route_out else "NONE_RECORDED",
                                  **_bounded(route_out)}),
            },
            "trace_summary": trace,
            "verification": verifications,
            "effects": effects,
            "approvals": approvals,
            "budget": self._budget(account_rows, reservation_rows, run_ids),
            "metering": metering,
            "routing_and_capabilities": self._capabilities(task_id, runs, attempts_out, route_out, grants),
            "skill_resolution": skills,
            "context": contexts,
            "memory_participation": memory,
            "continuation": continuations,
            "failure_signals": _bounded(failure_signals),
            "governance_coverage": coverage,
            "human_signals": human,
            "unknowns": unknowns,
            "source_refs": source_refs,
            "provenance": {
                "task_and_run": "CANONICAL_LIFECYCLE_TABLES",
                "trace": "SCHEMA_VALIDATED_TRACE_EVENTS",
                "verification": "RUN_BOUND_VERIFICATION_RESULT",
                "metering": "HASH_AND_SCHEMA_VALIDATED_METERING_V2",
                "context": "CONTEXT_PACK_SERVICE_INTEGRITY_READ",
                "memory": "CANDIDATE_LINKED_THROUGH_VERIFICATION_RUN",
                "skill": "TASK_AND_RUN_BOUND_SKILL_RESOLUTION_RECORDS",
                "host_delivery": "NOT_INFERRED_FROM_SESSIONSTART_OR_CONTEXT_COMPILATION",
                "continuation": "VISIBLE_PAYLOAD_TASK_RUN_SHAPE_ONLY; UNAVAILABLE_ARTIFACTS_ARE_POTENTIAL_CONTINUATION_CANDIDATES; OFFICIAL_COMMAND_LEDGER_NOT_VERIFIED",
            },
        }
        encoded = _canonical_json(projection)
        if len(encoded) > _MAX_JSON_BYTES:
            _fail("EXPERIENCE_PROJECTION_SIZE_LIMIT_EXCEEDED")
        try:
            self.store._validate("nexus.experience_projection@1.schema.json", projection)
        except Exception:
            _fail("EXPERIENCE_PROJECTION_CONTRACT_INVALID")
        return projection

    def _subtasks(self, subtasks, attempts, route_rows, run_ids, boundary):
        attempts_by_subtask: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(attempts, key=lambda item: (item["subtask_id"], item["attempt_no"], item["attempt_id"])):
            if isinstance(run_ids, dict):
                linked_run = run_ids.get(row["run_id"])
                if (not linked_run or linked_run["task_id"] != row["task_id"]
                        or linked_run["subtask_id"] != row["subtask_id"]
                        or linked_run["parent_run_id"] is None):
                    _fail("EXPERIENCE_ATTEMPT_RUN_INVALID")
            elif row["run_id"] not in run_ids:
                _fail("EXPERIENCE_ATTEMPT_RUN_INVALID")
            if (isinstance(row["attempt_no"], bool) or not isinstance(row["attempt_no"], int)
                    or row["attempt_no"] < 1 or row["outcome"] not in {
                        "CREATED", "READY", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED",
                        "POLICY_DENIED", "BUDGET_DENIED", "INCONCLUSIVE"}):
                _fail("EXPERIENCE_ATTEMPT_RECORD_INVALID")
            attempts_by_subtask.setdefault(row["subtask_id"], []).append(row)
        subtask_ids = {row["subtask_id"] for row in subtasks}
        if any(row["subtask_id"] not in subtask_ids for row in attempts):
            _fail("EXPERIENCE_ATTEMPT_SUBTASK_INVALID")
        route_by_subtask: dict[str, list[dict[str, Any]]] = {}
        route_out = []
        for row in route_rows:
            try:
                decision = _strict_json(row["decision_json"])
                version = decision.get("schema_version")
                if type(version) is not int or version not in {1, 2}:
                    _fail("EXPERIENCE_ROUTE_DECISION_INVALID")
                self.store._validate(f"nexus.route_decision@{version}.schema.json", decision)
            except Exception:
                _fail("EXPERIENCE_ROUTE_DECISION_INVALID")
            decision_task_ref = (decision.get("subtask_id") if version == 2 else decision.get("run_or_subtask_id"))
            if decision.get("route_decision_id") != row["route_decision_id"] or decision_task_ref != row["subtask_id"]:
                _fail("EXPERIENCE_ROUTE_BINDING_INVALID")
            if row["decision_object_id"] != row["route_decision_id"] or row["created_at"] != decision.get("created_at"):
                _fail("EXPERIENCE_ROUTE_BINDING_INVALID")
            object_status = self._object_visibility(row["decision_object_id"], boundary)
            if object_status == "AVAILABLE":
                try:
                    payload = self.store.get_payload(row["decision_object_id"])
                    metadata = self.store.get_object_metadata(row["decision_object_id"])
                except Exception:
                    _fail("EXPERIENCE_ROUTE_OBJECT_INTEGRITY_INVALID")
                if (metadata.get("object_type") != "artifact" or payload != _canonical_json(decision)):
                    _fail("EXPERIENCE_ROUTE_OBJECT_INTEGRITY_INVALID")
                if version == 2:
                    item = {"route_decision_id": _safe_id(row["route_decision_id"]) or "REDACTED",
                            "schema_version": version, "subtask_id": _safe_id(row["subtask_id"]) or "REDACTED",
                            "attempt_no": decision["attempt_no"],
                            "requested_capability": decision["requested_capability"],
                            "actual_executor_kind": decision["actual_executor_kind"],
                            "execution_source": decision["execution_source"],
                            "model_identity_status": decision["model_identity_status"],
                            "reason_codes": sorted({_safe_reason(code) for code in decision["reason_codes"]}),
                            "created_at": row["created_at"], "availability": object_status}
                else:
                    item = {"route_decision_id": _safe_id(row["route_decision_id"]) or "REDACTED",
                            "schema_version": version, "subtask_id": _safe_id(row["subtask_id"]) or "REDACTED",
                            "task_class": _safe_label(decision["task_class"]),
                            "risk_class": decision["risk_class"], "quality_requirement": decision["quality_requirement"],
                            "selected_model_class": decision["selected_model_class"],
                            "reason_codes": sorted({_safe_reason(code) for code in decision["reason_codes"]}),
                            "created_at": row["created_at"], "availability": object_status}
            else:
                # The database row duplicates the RouteDecision payload. Once its
                # governed object is hidden or purged, none of those fields may
                # continue to escape through the projection.
                item = {"route_decision_id": object_status, "availability": object_status}
            route_out.append(item)
            route_by_subtask.setdefault(row["subtask_id"], []).append(item)
        subtask_out = []
        for row in subtasks:
            try:
                node = _strict_json(row["node_json"])
                self.store._validate("nexus.subtask@1.schema.json", node)
            except Exception:
                _fail("EXPERIENCE_SUBTASK_RECORD_INVALID")
            if node.get("task_id") != row["task_id"] or node.get("subtask_id") != row["subtask_id"]:
                _fail("EXPERIENCE_SUBTASK_BINDING_INVALID")
            attempts_for = attempts_by_subtask.get(row["subtask_id"], [])
            if attempts_for:
                if [item["attempt_no"] for item in attempts_for] != list(range(1, len(attempts_for) + 1)):
                    _fail("EXPERIENCE_ATTEMPT_SEQUENCE_INVALID")
                for index, attempt in enumerate(attempts_for):
                    expected_predecessor = attempts_for[index - 1]["attempt_id"] if index else None
                    if attempt["predecessor_attempt_id"] != expected_predecessor:
                        _fail("EXPERIENCE_ATTEMPT_PREDECESSOR_INVALID")
            if row["final_attempt_id"] is not None:
                final = [item for item in attempts_for if item["attempt_id"] == row["final_attempt_id"]]
                if (len(final) != 1 or final[0]["outcome"] != row["final_outcome"]
                        or row["finalized_at"] is None):
                    _fail("EXPERIENCE_SUBTASK_FINAL_ATTEMPT_INVALID")
            elif row["final_outcome"] is not None or row["finalized_at"] is not None:
                _fail("EXPERIENCE_SUBTASK_FINAL_ATTEMPT_INVALID")
            subtask_out.append({
                "subtask_id": _safe_id(row["subtask_id"]) or "REDACTED", "node_index": row["node_index"],
                "status": row["status"], "final_attempt_id": _safe_id(row["final_attempt_id"]) if row["final_attempt_id"] else None,
                "final_outcome": row["final_outcome"], "finalized_at": row["finalized_at"],
                "attempt_count": len(attempts_for), "created_at": row["created_at"],
            })
        attempt_out, failures = [], []
        for row in attempts:
            attempt_reason = _safe_reason(row["attempt_reason"])
            item = {
                "attempt_id": _safe_id(row["attempt_id"]) or "REDACTED",
                "subtask_id": _safe_id(row["subtask_id"]) or "REDACTED",
                "attempt_no": row["attempt_no"], "run_id": _safe_id(row["run_id"]) or "REDACTED",
                "requested_capability": _safe_label(row["requested_capability"]),
                "attempt_reason": attempt_reason,
                "predecessor_attempt_id": _safe_id(row["predecessor_attempt_id"]) if row["predecessor_attempt_id"] else None,
                "outcome": row["outcome"], "created_at": row["created_at"],
            }
            if row["route_decision_ref"]:
                item["route_decision"] = next((r["route_decision_id"] for r in route_by_subtask.get(row["subtask_id"], [])
                                                if r.get("attempt_no") == row["attempt_no"] and r["availability"] == "AVAILABLE"), None)
            attempt_out.append(item)
            if row["outcome"] in {"FAILED", "POLICY_DENIED", "BUDGET_DENIED", "INCONCLUSIVE"}:
                failures.append({"source_kind": "SUBTASK_ATTEMPT", "source_ref": _safe_id(row["attempt_id"]) or "REDACTED",
                                 "reason_code": row["outcome"] if row["outcome"] in {"POLICY_DENIED", "BUDGET_DENIED"} else attempt_reason,
                                 "observed_state": row["outcome"]})
            if row["predecessor_attempt_id"]:
                previous = [item for item in attempts_by_subtask[row["subtask_id"]]
                            if item["attempt_id"] == row["predecessor_attempt_id"]]
                if len(previous) != 1 or previous[0]["attempt_no"] >= row["attempt_no"]:
                    _fail("EXPERIENCE_ATTEMPT_PREDECESSOR_INVALID")
        return subtask_out, attempt_out, route_out, failures

    def _object_visibility(self, object_id: str, boundary: dict[str, set[str]]) -> str:
        try:
            metadata = self.store.get_object_metadata(object_id)
        except Exception as exc:
            if getattr(exc, "reason_code", None) == "PURGED_OBJECT":
                return "REDACTED_PURGED"
            _fail("EXPERIENCE_OBJECT_REFERENCE_INVALID")
        if metadata.get("payload_state") == "PURGED":
            return "REDACTED_PURGED"
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT c.subject_type,c.subject_ref,c.sensitivity_level,c.handling_tags_json "
                "FROM object_envelopes e JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref "
                "WHERE e.object_id=?", (object_id,)
            ).fetchone()
        if not row or row["subject_type"] != "OBJECT" or row["subject_ref"] != object_id:
            _fail("EXPERIENCE_OBJECT_CLASSIFICATION_INVALID")
        if not _visible(dict(row), boundary):
            return "REDACTED"
        return "AVAILABLE"

    def _trace(self, rows, classifications):
        if any(classifications.get(row["event_id"]) is None for row in rows):
            return {"status": "REDACTED", "event_count": None, "counts_by_event_type": {}, "items": [],
                    "truncated": False, "event_types_truncated": False}
        counts = Counter()
        items = []
        for row in rows:
            event = _strict_json(row["event_json"])
            counts[event["event_type"]] += 1
            items.append({"event_id": _safe_id(row["event_id"]) or "REDACTED",
                          "run_id": _safe_id(row["run_id"]) or "REDACTED", "seq_no": row["seq_no"],
                          "event_type": event["event_type"], "occurred_at": event["occurred_at"]})
        ordered_types = sorted(counts.items())
        return {"status": "AVAILABLE" if rows else "NONE_RECORDED", "event_count": len(rows),
                "counts_by_event_type": dict(ordered_types[:_MAX_TRACE_TYPES]),
                "items": items[:_MAX_OUTPUT_ROWS], "truncated": len(items) > _MAX_OUTPUT_ROWS,
                "event_types_truncated": len(ordered_types) > _MAX_TRACE_TYPES}

    def _verifications(self, rows, run_ids, boundary):
        output = []
        redacted = False
        for row in rows:
            try:
                doc = _strict_json(row["result_json"])
                self.store._validate("nexus.verification_result@1.schema.json", doc)
                evidence = _strict_json(row["evidence_used_json"])
                independence = _strict_json(row["independence_json"])
            except Exception:
                _fail("EXPERIENCE_VERIFICATION_INVALID")
            if (doc.get("verification_id") != row["verification_id"] or doc.get("run_id") != row["run_id"]
                    or doc.get("verdict") != row["verdict"] or doc.get("verifier_kind") != row["verifier_kind"]
                    or doc.get("target_ref") != row["target_ref"] or doc.get("evidence_used") != evidence
                    or doc.get("independence") != independence or row["run_id"] not in run_ids):
                _fail("EXPERIENCE_VERIFICATION_BINDING_INVALID")
            visibility = [self._object_visibility(ref, boundary) for ref in [row["target_ref"], *evidence]]
            if any(state != "AVAILABLE" for state in visibility):
                redacted = True
                continue
            output.append({
                "verification_id": _safe_id(row["verification_id"]) or "REDACTED",
                "run_id": _safe_id(row["run_id"]) or "REDACTED",
                "target_ref": _safe_id(row["target_ref"]) or "REDACTED", "target_scope": "TARGET_SPECIFIC",
                "verdict": row["verdict"], "verifier_kind": row["verifier_kind"],
                "attester_principal_id": _safe_id(row["attester_principal_id"]),
                "approval_ref": _safe_id(row["approval_ref"]), "created_at": row["created_at"],
                "evidence_ref_count": len(evidence), "missing_evidence_count": len(doc["missing_evidence"]),
                "conflict_count": len(doc["conflicts"]),
            })
        whole_posture = "UNKNOWN" if redacted or output else "NONE"
        return {"status": "REDACTED" if redacted else "AVAILABLE" if output else "NONE_RECORDED",
                "items": [] if redacted else output[:_MAX_OUTPUT_ROWS],
                "total_count": None if redacted else len(output), "truncated": False if redacted else len(output) > _MAX_OUTPUT_ROWS,
                "verifier_kind_counts": None if redacted else dict(sorted(Counter(item["verifier_kind"] for item in output).items())),
                "whole_task_verification": whole_posture, "semantic_quality": "UNKNOWN"}

    def _effects(self, rows, run_ids):
        output, failures = [], []
        for row in rows:
            try:
                doc = _strict_json(row["effect_json"])
                self.store._validate("nexus.effect@1.schema.json", doc)
            except Exception:
                _fail("EXPERIENCE_EFFECT_INVALID")
            for field in ("effect_id", "run_id", "grant_id", "tool_id", "action_type",
                          "execution_state", "effect_outcome", "reconciliation_status", "approval_ref"):
                if doc.get(field) != row[field]:
                    _fail("EXPERIENCE_EFFECT_BINDING_INVALID")
            if row["run_id"] not in run_ids:
                _fail("EXPERIENCE_EFFECT_RUN_INVALID")
            item = {"effect_id": _safe_id(row["effect_id"]) or "REDACTED",
                    "run_id": _safe_id(row["run_id"]) or "REDACTED",
                    "tool_id": _safe_label(row["tool_id"]), "action_type": _safe_label(row["action_type"]),
                    "execution_state": row["execution_state"], "effect_outcome": row["effect_outcome"],
                    "reconciliation_status": row["reconciliation_status"],
                    "approval_ref": _safe_id(row["approval_ref"]),
                    "created_at": row["created_at"], "updated_at": row["updated_at"]}
            output.append(item)
            if row["effect_outcome"] in {"UNKNOWN", "UNDETERMINED"} or row["reconciliation_status"] not in {"NOT_REQUIRED", "RESOLVED"}:
                failures.append({"source_kind": "EFFECT", "source_ref": _safe_id(row["effect_id"]) or "REDACTED",
                                 "reason_code": row["effect_outcome"], "observed_state": row["reconciliation_status"]})
        counts = dict(sorted(Counter(row["effect_outcome"] for row in output).items()))
        return {"status": "AVAILABLE" if output else "NONE_RECORDED", "items": output[:_MAX_OUTPUT_ROWS],
                "total_count": len(output), "truncated": len(output) > _MAX_OUTPUT_ROWS,
                "counts_by_outcome": counts}, failures

    def _approvals(self, rows, run_ids, effect_ids, run_classes, boundary):
        output = []
        redacted = False
        for row in rows:
            try:
                scope = _strict_json(row["approved_scope_json"])
                doc = {"schema_id": "nexus.approval_decision", "schema_version": 1,
                       "approval_id": row["approval_id"], "approver_principal_id": row["approver_principal_id"],
                       "target_type": row["target_type"], "target_ref": row["target_ref"],
                       "decision": row["decision"], "approved_scope": scope,
                       "policy_version": row["policy_version"], "issued_at": row["issued_at"]}
                for key in ("effect_id", "payload_integrity_hash", "expires_at", "reason", "request_ref"):
                    if row[key] is not None:
                        doc[key] = row[key]
                self.store._validate("nexus.approval_decision@1.schema.json", doc)
            except Exception:
                _fail("EXPERIENCE_APPROVAL_INVALID")
            target_type = row["target_type"]
            if row["target_ref"] in run_ids:
                target_visible = _visible(run_classes[row["target_ref"]], boundary)
            elif row["effect_id"] in effect_ids:
                target_visible = True
            else:
                try:
                    target_visible = self._object_visibility(row["target_ref"], boundary) == "AVAILABLE"
                except ExperienceProjectionError:
                    target_visible = False
            if not target_visible:
                redacted = True
                continue
            output.append({"approval_id": _safe_id(row["approval_id"]) or "REDACTED",
                           "approver_principal_id": _safe_id(row["approver_principal_id"]),
                           "target_type": _safe_label(row["target_type"]), "effect_id": _safe_id(row["effect_id"]),
                           "decision": row["decision"], "policy_version": _safe_label(row["policy_version"]),
                           "issued_at": row["issued_at"], "expires_at": row["expires_at"],
                           "association_basis": "TASK_EFFECT_OR_OBJECT_OR_RUN_TARGET"})
        output.sort(key=lambda item: (item["issued_at"], item["approval_id"]))
        return {"status": "REDACTED" if redacted else "AVAILABLE" if output else "NONE_RECORDED",
                "items": [] if redacted else output[:_MAX_OUTPUT_ROWS],
                "total_count": None if redacted else len(output),
                "truncated": False if redacted else len(output) > _MAX_OUTPUT_ROWS,
                "decision_counts": None if redacted else dict(sorted(Counter(item["decision"] for item in output).items()))}

    def _budget(self, account_rows, reservations, run_ids):
        if len(account_rows) > 1:
            _fail("EXPERIENCE_BUDGET_ACCOUNT_INVALID")
        if not account_rows:
            return {"status": "UNAVAILABLE", "reason": "NO_BUDGET_ACCOUNT_RECORDED", "actual_spend": "UNKNOWN"}
        account = dict(account_rows[0])
        # Units are caller-controlled metadata, not safe operator labels.
        known_unit = account["unit"] in (_SAFE_METERING_UNITS | _SAFE_METERING_CURRENCIES)
        limits = {key: account[key] for key in ("amount_limit", "model_call_limit", "tool_call_limit", "child_run_limit")}
        limits.update(unit=account["unit"] if known_unit else "OTHER",
                      unit_status="KNOWN_SAFE" if known_unit else "REDACTED")
        for row in reservations:
            if row["run_id"] not in run_ids:
                _fail("EXPERIENCE_BUDGET_RESERVATION_RUN_INVALID")
        return {
            "status": "AVAILABLE", "account_id": _safe_id(account["account_id"]) or "REDACTED",
            "limits": limits,
            "current_counters": {key: account[key] for key in ("reserved", "consumed", "model_calls_reserved", "model_calls_consumed", "tool_calls_reserved", "tool_calls_consumed", "child_runs_reserved", "child_runs_consumed")},
            "reservations": _bounded([{"reservation_id": _safe_id(row["reservation_id"]) or "REDACTED",
                                        "run_id": _safe_id(row["run_id"]) or "REDACTED",
                                        "amount": row["amount"], "model_calls": row["model_calls"],
                                        "tool_calls": row["tool_calls"], "child_runs": row["child_runs"],
                                        "state": row["state"], "created_at": row["created_at"]} for row in reservations]),
            "actual_spend": "UNKNOWN", "interpretation": "BUDGET_RESERVATION_IS_NOT_PROVIDER_SPEND",
        }

    def _metering(self, rows, run_ids):
        output = []
        for row in rows:
            if row["run_id"] not in run_ids:
                _fail("EXPERIENCE_METERING_RUN_INVALID")
            digest = hashlib.sha256(row["metrics_json"].encode("utf-8")).hexdigest()
            if digest != row["metrics_sha256"]:
                _fail("METERING_RECORD_INTEGRITY_FAILED")
            try:
                metrics = _strict_json(row["metrics_json"])
                doc = {"schema_id": "nexus.metering_record", "schema_version": 2,
                       "task_id": row["task_id"], "run_id": row["run_id"],
                       "record_source": row["record_source"], "participation_mode": row["participation_mode"],
                       "context_pack_ref": row["context_pack_ref"], "metrics": metrics,
                       "metrics_sha256": digest, "recorded_at": row["recorded_at"]}
                self.store._validate("nexus.metering_record@2.schema.json", doc)
            except Exception:
                _fail("EXPERIENCE_METERING_RECORD_INVALID")
            safe_metrics, text_redacted, unrecognized_metric_count = _safe_metering_metrics(metrics)
            output.append({"record_id": _safe_id(row["record_id"]) or "REDACTED",
                           "task_id": _safe_id(row["task_id"]) or "REDACTED",
                           "run_id": _safe_id(row["run_id"]) or "REDACTED",
                           "record_source": row["record_source"], "participation_mode": row["participation_mode"],
                           "context_pack_ref": _safe_id(row["context_pack_ref"]) if row["context_pack_ref"] else None,
                           "metrics_sha256": digest, "recorded_at": row["recorded_at"],
                           "metrics": safe_metrics,
                           "unrecognized_metric_count": unrecognized_metric_count,
                           "sensitive_text_redacted": text_redacted})
        sources = sorted(Counter(item["record_source"] for item in output).items())
        return {"status": "AVAILABLE" if output else "UNAVAILABLE", "reason": None if output else "NO_METERING_RECORDS",
                "total_count": len(output), "items": output[-_MAX_METERING_ROWS:],
                "truncated": len(output) > _MAX_METERING_ROWS,
                "counts_by_source": dict(sources),
                "metric_interpretation": "TYPED_METRICS_PRESERVED_FREE_TEXT_REDACTED"}

    def _contexts(self, rows, run_ids):
        output = []
        for row in rows:
            if row["run_id"] not in run_ids:
                _fail("EXPERIENCE_CONTEXT_RUN_INVALID")
            if row["state"] == "PURGED":
                output.append({"pack_ref": "REDACTED_PURGED", "run_id": _safe_id(row["run_id"]) or "REDACTED", "state": "PURGED",
                               "model_visible_exposure": "UNKNOWN", "compiled_at": row["compiled_at"]})
                continue
            if self.context_packs is None:
                _fail("EXPERIENCE_CONTEXT_INTEGRITY_READER_UNAVAILABLE")
            try:
                pack = self.context_packs.read_compiled(row["pack_ref"])
            except Exception:
                _fail("EXPERIENCE_CONTEXT_INTEGRITY_INVALID")
            # read_compiled() returns a verified read-result envelope, not the
            # persisted Context Pack document itself. Its serialized_byte_size
            # is the canonical document size recorded by ContextPackService;
            # serializing this envelope would include read-only metadata and
            # produce a different size for every valid production pack.
            size = pack.get("serialized_byte_size") if isinstance(pack, dict) else None
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0
                    or pack.get("pack_id") != row["pack_ref"]
                    or pack.get("task_id") != row["task_id"] or pack.get("run_id") != row["run_id"]
                    or pack.get("content_hash") != row["content_hash"] or size != row["serialized_byte_size"]
                    or pack.get("model_visible_exposure") != "UNKNOWN"):
                _fail("EXPERIENCE_CONTEXT_BINDING_INVALID")
            type_counts = Counter(item["source_type"] for item in pack.get("entries", []))
            output.append({"pack_ref": _safe_id(row["pack_ref"]) or "REDACTED",
                           "run_id": _safe_id(row["run_id"]) or "REDACTED", "state": row["state"],
                           "content_hash": row["content_hash"], "serialized_byte_size": row["serialized_byte_size"],
                           "source_count": len(pack["entries"]), "source_type_counts": dict(sorted(type_counts.items())),
                           "model_visible_exposure": pack["model_visible_exposure"],
                           "compiled_at": row["compiled_at"]})
        return {"status": "AVAILABLE" if output else "UNAVAILABLE", "reason": None if output else "NO_CONTEXT_PACK_RECORDED",
                "items": output[-_MAX_CONTEXT_ROWS:], "total_count": len(output), "truncated": len(output) > _MAX_CONTEXT_ROWS,
                "delivery": "UNKNOWN", "model_consumption": "UNKNOWN"}

    def _skills(self, rows, task_id, runs_by_id, boundary):
        output = []
        for row in rows:
            linked_run = runs_by_id.get(row["run_id"])
            if (row["task_id"] != task_id or linked_run is None
                    or linked_run["task_id"] != row["task_id"]):
                _fail("EXPERIENCE_SKILL_RUN_INVALID")
            digest_fields = (row["query_sha256"], row["instruction_sha256"])
            if (row["result_status"] not in {"RESOLVED", "NO_MATCH", "AMBIGUOUS", "UNSUPPORTED", "INACTIVE_MODE"}
                    or row["resolution"] not in {None, "HOST_NATIVE", "NEXUS_FALLBACK", "UNSUPPORTED"}
                    or row["host_inventory_provenance"] not in {None, "ADAPTER_DISCOVERY", "HOST_DECLARED"}
                    or row["host_native_availability"] not in {None, "AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
                    or row["instruction_load_status"] not in {"NOT_LOADED", "LOADED_TO_GOVERNED_ARTIFACT", "UNAVAILABLE"}
                    or row["delivery_status"] not in {"UNKNOWN", "HOST_DECLARED_DELIVERY", "OBSERVED_DELIVERY"}
                    or row["model_visible_exposure"] not in {"UNKNOWN", "UNAVAILABLE", "OBSERVED"}
                    or isinstance(row["selection_latency_ms"], bool)
                    or not isinstance(row["selection_latency_ms"], (int, float))
                    or not math.isfinite(row["selection_latency_ms"])
                    or row["selection_latency_ms"] < 0):
                _fail("EXPERIENCE_SKILL_RECORD_INVALID")
            if (isinstance(row["candidate_count"], bool) or not isinstance(row["candidate_count"], int)
                    or row["candidate_count"] < 0
                    or any(value is not None and (not isinstance(value, str)
                            or not re.fullmatch(r"[a-f0-9]{64}", value)) for value in digest_fields)
                    or (row["instruction_byte_size"] is not None
                        and (isinstance(row["instruction_byte_size"], bool)
                             or not isinstance(row["instruction_byte_size"], int)
                             or row["instruction_byte_size"] < 0))
                    or (row["instruction_object_ref"] is None
                        and any(value is not None for value in (row["instruction_sha256"], row["instruction_byte_size"])))
                    or (row["instruction_load_status"] == "LOADED_TO_GOVERNED_ARTIFACT"
                        and (row["instruction_object_ref"] is None or row["instruction_sha256"] is None
                             or row["instruction_byte_size"] is None))):
                _fail("EXPERIENCE_SKILL_RECORD_INVALID")
            ref = row["instruction_object_ref"]
            ref_state = self._object_visibility(ref, boundary) if ref else "UNAVAILABLE"
            instruction_integrity = "UNAVAILABLE" if ref_state != "AVAILABLE" else self._verify_skill_instruction(row)
            output.append({
                "selection_id": _safe_id(row["selection_id"]) or "REDACTED",
                "run_id": _safe_id(row["run_id"]) or "REDACTED",
                "candidate_count": row["candidate_count"], "result_status": row["result_status"],
                "resolution": row["resolution"], "skill_id": _safe_id(row["skill_id"]),
                "host_inventory_provenance": row["host_inventory_provenance"] or "UNKNOWN",
                "host_native_availability": row["host_native_availability"] or "UNKNOWN",
                "instruction_object_ref": (_safe_id(ref) or "REDACTED") if ref_state == "AVAILABLE" else ref_state,
                "instruction_sha256": row["instruction_sha256"] if ref_state == "AVAILABLE" else None,
                "instruction_byte_size": row["instruction_byte_size"] if ref_state == "AVAILABLE" else None,
                "instruction_load_status": row["instruction_load_status"], "delivery_status": row["delivery_status"],
                "model_visible_exposure": row["model_visible_exposure"],
                "selection_latency_ms": row["selection_latency_ms"], "created_at": row["created_at"],
                "instruction_integrity": instruction_integrity, "effectiveness": "UNKNOWN",
            })
        return {"status": "AVAILABLE" if output else "NONE_RECORDED", "items": output[-_MAX_OUTPUT_ROWS:],
                "total_count": len(output), "truncated": len(output) > _MAX_OUTPUT_ROWS,
                "model_consumption": "UNKNOWN", "causal_effectiveness": "UNKNOWN"}

    def _verify_skill_instruction(self, row):
        """Check a governed instruction snapshot without exposing its body."""
        try:
            metadata = self.store.get_object_metadata(row["instruction_object_ref"])
            payload = self.store.get_payload(row["instruction_object_ref"])
            document = _strict_json(payload)
            schema_id = document.get("schema_id")
            expected_fields = (
                {"schema_id", "schema_version", "skill_id", "package_revision", "instruction_sha256", "instruction_utf8"}
                if schema_id == "nexus.skill_instruction_snapshot" else
                {"schema_id", "schema_version", "skill_id", "package_revision", "instruction_sha256",
                 "source_scope", "source_namespace", "task_id", "run_id", "instruction_utf8"}
            )
            if (not isinstance(document, dict) or set(document) != expected_fields
                    or type(document.get("schema_version")) is not int or document["schema_version"] != 1
                    or schema_id not in {"nexus.skill_instruction_snapshot", "nexus.skill_instruction_artifact"}
                    or metadata.get("object_type") != ("skill" if schema_id == "nexus.skill_instruction_snapshot" else "artifact")
                    or (schema_id == "nexus.skill_instruction_artifact"
                        and (document.get("task_id") != row["task_id"] or document.get("run_id") != row["run_id"]
                             or metadata.get("created_by_run") != row["run_id"]))
                    or _canonical_json(document) != payload):
                _fail("EXPERIENCE_SKILL_INSTRUCTION_INVALID")
            instruction = document.get("instruction_utf8")
            instruction_bytes = instruction.encode("utf-8", errors="strict") if isinstance(instruction, str) else None
            if (document.get("skill_id") != row["skill_id"]
                    or document.get("instruction_sha256") != row["instruction_sha256"]
                    or instruction_bytes is None
                    or hashlib.sha256(instruction_bytes).hexdigest() != row["instruction_sha256"]
                    or len(instruction_bytes) != row["instruction_byte_size"]):
                _fail("EXPERIENCE_SKILL_INSTRUCTION_INTEGRITY_INVALID")
        except ExperienceProjectionError:
            raise
        except Exception:
            _fail("EXPERIENCE_SKILL_INSTRUCTION_INVALID")
        return "VERIFIED"

    def _memory(self, rows, verification_rows, boundary):
        verification_by_id = {row["verification_id"]: row for row in verification_rows}
        output = []
        for row in rows:
            verification = verification_by_id.get(row["verification_ref"])
            if not verification:
                _fail("EXPERIENCE_MEMORY_VERIFICATION_BINDING_INVALID")
            if row["status"] == "PURGED":
                output.append({"candidate_id": "REDACTED_PURGED", "status": "PURGED", "truth_state": "UNKNOWN",
                               "verification_ref": "REDACTED_PURGED", "admitted": None})
                continue
            cls = None
            with self.store._connection() as conn:
                cls = conn.execute("SELECT subject_type,subject_ref,sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?",
                                   (row["classification_assertion_ref"],)).fetchone()
                evidence_refs = [item[0] for item in conn.execute(
                    "SELECT evidence_object_id FROM memory_candidate_evidence WHERE candidate_id=? ORDER BY evidence_object_id", (row["candidate_id"],))]
                admitted = conn.execute("SELECT object_id FROM admitted_memory_rows WHERE candidate_id=?", (row["candidate_id"],)).fetchone()
            if not cls or cls["subject_type"] != "OBJECT" or cls["subject_ref"] != row["claim_ref"] or not _visible(dict(cls), boundary):
                output.append({"candidate_id": "REDACTED", "status": "REDACTED", "truth_state": "UNKNOWN",
                               "verification_ref": "REDACTED", "admitted": None})
                continue
            claim_visibility = self._object_visibility(row["claim_ref"], boundary)
            evidence_visibility = [self._object_visibility(ref, boundary) for ref in evidence_refs]
            if claim_visibility != "AVAILABLE" or any(state != "AVAILABLE" for state in evidence_visibility):
                output.append({"candidate_id": "REDACTED_PURGED" if claim_visibility == "REDACTED_PURGED" else "REDACTED",
                               "status": "REDACTED", "truth_state": "UNKNOWN",
                               "verification_ref": "REDACTED", "admitted": None})
                continue
            if admitted and admitted["object_id"] != row["claim_ref"]:
                _fail("EXPERIENCE_MEMORY_ADMISSION_BINDING_INVALID")
            try:
                doc = _strict_json(row["metadata_json"])
                self.store._validate("nexus.memory_candidate@1.schema.json", doc)
            except Exception:
                _fail("EXPERIENCE_MEMORY_CANDIDATE_INVALID")
            if (doc.get("candidate_id") != row["candidate_id"] or doc.get("claim_ref") != row["claim_ref"]
                    or doc.get("verification_ref") != row["verification_ref"] or doc.get("status") != row["status"]
                    or doc.get("truth_state") != row["truth_state"] or set(doc.get("evidence_refs", [])) != set(evidence_refs)):
                _fail("EXPERIENCE_MEMORY_CANDIDATE_BINDING_INVALID")
            admitted_visibility = self._object_visibility(admitted["object_id"], boundary) if admitted else None
            output.append({"candidate_id": _safe_id(row["candidate_id"]) or "REDACTED",
                           "status": row["status"], "truth_state": row["truth_state"],
                           "verification_ref": _safe_id(row["verification_ref"]) or "REDACTED",
                           "claim_ref": _safe_id(row["claim_ref"]) or "REDACTED",
                           "evidence_ref_count": len(evidence_refs), "admitted": admitted is not None,
                           "admitted_object_ref": (_safe_id(admitted["object_id"]) or "REDACTED") if admitted and admitted_visibility == "AVAILABLE" else admitted_visibility,
                           "created_at": row["created_at"]})
        states = dict(sorted(Counter(row["status"] for row in output if row["status"] != "REDACTED").items()))
        return {"status": "AVAILABLE" if output else "NONE_RECORDED", "items": output[:_MAX_OUTPUT_ROWS],
                "total_count": len(output), "truncated": len(output) > _MAX_OUTPUT_ROWS,
                "counts_by_status": states, "mutated": False}

    def _continuations(self, rows, task_id, run_ids, boundary):
        by_run: dict[str, dict[str, list[dict[str, Any]]]] = {}
        operator_assertion_count = 0
        unavailable_candidates: Counter[str] = Counter()
        for row in rows:
            if (row["created_by_run"] not in run_ids or row["object_type"] != "artifact"
                    or row["schema_id"] != "nexus.object" or row["schema_version"] != 1
                    or row["subject_type"] != "OBJECT" or row["subject_ref"] != row["object_id"]):
                _fail("EXPERIENCE_CONTINUATION_BINDING_INVALID")
            if row["payload_state"] == "PURGED":
                unavailable_candidates["PURGED"] += 1
                continue
            if not _visible(row, boundary):
                unavailable_candidates["REDACTED"] += 1
                continue
            try:
                payload = self.store.get_payload(row["object_id"])
            except Exception:
                _fail("EXPERIENCE_CONTINUATION_INTEGRITY_INVALID")
            try:
                doc = _strict_json(payload)
            except Exception:
                continue
            if not isinstance(doc, dict) or doc.get("schema_id") not in {
                    "nexus.continuation_state", "nexus.what_changed"}:
                continue
            if hashlib.sha256(payload).hexdigest() != row["integrity_hash"]:
                _fail("EXPERIENCE_CONTINUATION_INTEGRITY_INVALID")
            document_schema_id = doc.get("schema_id")
            if (type(doc.get("schema_version")) is not int or doc.get("schema_version") != 1
                    or _canonical_json(doc) != payload):
                _fail("EXPERIENCE_CONTINUATION_DOCUMENT_INVALID")
            run_id = row["created_by_run"]
            if document_schema_id == "nexus.continuation_state":
                accepted_git = doc.get("accepted_git")
                provenance = doc.get("provenance_by_fact")
                supersedes = doc.get("supersedes")
                previous = doc.get("previous_current_state")
                lifecycle = doc.get("task_run_lifecycle_at_commit")
                if (not isinstance(accepted_git, dict) or not re.fullmatch(r"[a-f0-9]{40,64}", str(doc.get("accepted_revision", "")))
                        or accepted_git.get("commit_sha") != doc.get("accepted_revision")
                        or not isinstance(provenance, dict) or not isinstance(supersedes, dict)
                        or not isinstance(previous, dict)
                        or not _safe_id(supersedes.get("object_id"))
                        or not re.fullmatch(r"[a-f0-9]{64}", str(supersedes.get("integrity_sha256", "")))
                        or previous.get("object_id") != supersedes.get("object_id")
                        or previous.get("integrity_sha256") != supersedes.get("integrity_sha256")
                        or not isinstance(doc.get("current_objective"), str)
                        or not isinstance(doc.get("current_operating_priority"), str)
                        or not isinstance(doc.get("recent_work"), list)
                        or not isinstance(lifecycle, dict)):
                    _fail("EXPERIENCE_CONTINUATION_DOCUMENT_INVALID")
                if (lifecycle.get("task_id") != task_id or lifecycle.get("root_run_id") != run_id
                        or lifecycle.get("task_status") != "ACTIVE" or lifecycle.get("root_run_status") != "RUNNING"):
                    _fail("EXPERIENCE_CONTINUATION_TASK_BINDING_INVALID")
                if any(item.get("kind") == "HUMAN_OPERATOR_ASSERTION"
                       for facts in provenance.values() if isinstance(facts, list)
                       for item in facts if isinstance(item, dict)):
                    operator_assertion_count += 1
                binding = doc.get("governed_work_reference")
            elif document_schema_id == "nexus.what_changed":
                binding = doc.get("responsible_task")
                if (not isinstance(doc.get("new_state"), dict) or not isinstance(doc.get("previous_state"), dict)
                        or not isinstance(doc.get("accepted_revision"), dict)
                        or not isinstance(doc.get("objective"), dict) or not isinstance(doc.get("next_step"), dict)
                        or not isinstance(doc.get("recent_work_added"), dict)
                        or not isinstance(doc.get("facts_superseded"), list)
                        or not isinstance(doc.get("facts_unchanged"), (list, dict))
                        or (isinstance(doc.get("facts_unchanged"), dict)
                            and any(not isinstance(key, str) for key in doc["facts_unchanged"]))):
                    _fail("EXPERIENCE_CONTINUATION_DOCUMENT_INVALID")
            else:
                _fail("EXPERIENCE_CONTINUATION_DOCUMENT_INVALID")
            if not isinstance(binding, dict) or binding.get("task_id") != task_id or binding.get("root_run_id") != run_id:
                _fail("EXPERIENCE_CONTINUATION_TASK_BINDING_INVALID")
            by_run.setdefault(run_id, {}).setdefault(document_schema_id, []).append({
                "object_ref": row["object_id"], "integrity_sha256": row["integrity_hash"],
                "run_id": run_id, "document": doc,
            })
        commits = []
        for run_id, schemas in sorted(by_run.items()):
            states = schemas.get("nexus.continuation_state", [])
            deltas = schemas.get("nexus.what_changed", [])
            for state in states:
                state_doc = state["document"]
                prior = state_doc.get("supersedes", {}).get("object_id")
                with self.store._connection() as conn:
                    relation = conn.execute(
                        "SELECT 1 FROM object_relations WHERE from_id=? AND relation_type='supersedes' AND to_id=?",
                        (state["object_ref"], prior),
                    ).fetchone()
                matching = next((delta for delta in deltas if self._delta_points_to(
                    delta["document"], state["object_ref"], state["integrity_sha256"], task_id, run_id,
                    prior, state_doc["previous_current_state"].get("integrity_sha256"))), None)
                complete = bool(matching and relation)
                commits.append({"run_id": _safe_id(run_id) or "REDACTED",
                                "object_ref": _safe_id(state["object_ref"]) or "REDACTED",
                                "current_state_integrity_sha256": state["integrity_sha256"],
                                "what_changed_ref": (_safe_id(matching["object_ref"]) or "REDACTED") if matching else None,
                                "what_changed_integrity_sha256": matching["integrity_sha256"] if matching else None,
                                "supersedes_ref": _safe_id(prior) or "REDACTED",
                                "supersession_relation": "RECORDED" if relation else "UNKNOWN",
                                "status": "STRUCTURALLY_COMPLETE" if complete else "STRUCTURALLY_PARTIAL",
                                "official_commit_provenance": "NOT_VERIFIED_FROM_COMMAND_LEDGER"})
        unavailable_count = sum(unavailable_candidates.values())
        if commits:
            status = "PARTIAL" if unavailable_count else "AVAILABLE"
        elif not unavailable_count:
            status = "NONE_RECORDED"
        elif set(unavailable_candidates) == {"PURGED"}:
            status = "PURGED"
        elif set(unavailable_candidates) == {"REDACTED"}:
            status = "REDACTED"
        else:
            status = "UNAVAILABLE"
        return {"status": status, "commits": commits[:_MAX_OUTPUT_ROWS],
                "total_count": len(commits), "truncated": len(commits) > _MAX_OUTPUT_ROWS,
                "operator_assertion_count": operator_assertion_count,
                "lifecycle_requirement": "OPTIONAL_INDEPENDENT_SURFACE",
                "coverage": "COMPLETE" if not unavailable_count else "PARTIAL" if commits else "UNKNOWN",
                "unavailable_candidate_count": unavailable_count,
                "unavailable_candidate_states": dict(sorted(unavailable_candidates.items())),
                "provenance_scope": "VISIBLE_PAYLOAD_TASK_RUN_SHAPE_ONLY; UNAVAILABLE_ARTIFACTS_ARE_POTENTIAL_CONTINUATION_CANDIDATES; OFFICIAL_COMMAND_LEDGER_NOT_VERIFIED"}

    @staticmethod
    def _delta_points_to(delta, state_ref, state_hash, task_id, run_id, previous_ref, previous_hash):
        if not isinstance(delta, dict) or delta.get("schema_id") != "nexus.what_changed" or delta.get("schema_version") != 1:
            return False
        new_state = delta.get("new_state")
        prior_state = delta.get("previous_state")
        responsible = delta.get("responsible_task")
        return (isinstance(new_state, dict) and new_state.get("object_id") == state_ref
                and new_state.get("integrity_sha256") == state_hash
                and isinstance(prior_state, dict) and prior_state.get("object_id") == previous_ref
                and prior_state.get("integrity_sha256") == previous_hash
                and isinstance(responsible, dict) and responsible.get("task_id") == task_id
                and responsible.get("root_run_id") == run_id)

    @staticmethod
    def _capabilities(task_id, runs, attempts, routes, grants):
        return {
            "run_executor_counts": dict(sorted(Counter(run["executor_kind"] for run in runs).items())),
            "requested_capability_counts": dict(sorted(Counter(item["requested_capability"] for item in attempts).items())),
            "route_decision_count": None if any(item.get("availability") != "AVAILABLE" for item in routes) else len(routes),
            "grant_action_scopes": _bounded([
                {"grant_id": _safe_id(grant_id) or "REDACTED",
                 "actions": sorted(set(grant["parsed"]["action_scope"]) & _KNOWN_ACTIONS),
                 "unrecognized_action_count": len(set(grant["parsed"]["action_scope"]) - _KNOWN_ACTIONS),
                 "audiences": sorted(set(grant["parsed"]["audience_scope"]) & {"nexus-runtime", "nexus-inspect"}),
                 "unrecognized_audience_count": len(set(grant["parsed"]["audience_scope"]) - {"nexus-runtime", "nexus-inspect"}),
                 "task_scope_exact_for_projection": grant["parsed"]["task_scope"] == [task_id]}
                for grant_id, grant in sorted(grants.items())
            ]),
            "host_native_tool_coverage": "UNKNOWN",
        }


def render_experience(projection: dict[str, Any]) -> str:
    """Concise Chinese-first rendering; JSON is the stable complete contract."""
    identity = projection["identity"]
    lifecycle = projection["lifecycle"]
    lines = [
        f"Nexus 工作经历投影  {identity['task_id']}",
        f"Task / Root Run    {lifecycle['task_status']} / {lifecycle['root_run_status']}",
        f"尝试次数           {projection['subtasks_and_attempts']['attempts']['total_count']}",
        f"Trace 事件数       {projection['trace_summary'].get('event_count') if projection['trace_summary'].get('event_count') is not None else projection['trace_summary']['status']}",
        f"Verification       {projection['verification']['status']} ({projection['verification']['total_count']})",
        f"Effect             {projection['effects']['counts_by_outcome']}",
        f"Metering           {projection['metering']['status']} ({projection['metering']['total_count']})",
        f"Context / Model    {projection['context']['status']} / UNKNOWN",
        f"Nexus 覆盖          {projection['governance_coverage']['status']}; Host 完整度 UNKNOWN",
        f"语义质量           {projection['unknowns']['semantic_task_quality']}",
    ]
    return "\n".join(lines)
