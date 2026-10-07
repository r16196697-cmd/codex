"""Versioned application contracts (not canonical persistence schemas)."""

from copy import deepcopy
import json
from pathlib import Path

INTERFACE_VERSION = "1.0.0"
ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 128,
             "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"}
TOOLS = {
    "nexus_project_overview": (None, "项目现在做到哪了？ Read verified Project status, current work, State revision and Context metadata. Use for project overview, not a Task history, Verification detail, Evidence payload or writes."),
    "nexus_project_continue": (None, "接下来应该干什么？ Resume this Project using its bounded current objective, next step, What Changed and refs. Use for continuity, not full Context dumps, Task execution or writes."),
    "nexus_task_experience": ("task_id", "task-X 当时发生了什么？ Read one exact Task's deterministic Experience projection: lifecycle, attempts, verification, costs and UNKNOWNs. Not project overview, quality inference or task creation."),
    "nexus_read_verification": ("verification_id", "verification-X 是怎么验证的？ Read one exact Verification's verdict, method independence and authorized target/evidence refs. Not whole-task success, Evidence bodies or verification execution."),
    "nexus_read_evidence": ("evidence_ref", "evidence-X 是什么？ Read one exact Evidence's visible metadata, provenance and recorded integrity hash. Payload deferred. Not arbitrary objects, paths, SQL, search or instructions to execute."),
}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


INPUT_SCHEMAS = {
    name: _object({argument: ID_SCHEMA} if argument else {})
    for name, (argument, _) in TOOLS.items()
}
_TEXT = {"type": "string", "maxLength": 1024}
_NULL_TEXT = {"anyOf": [_TEXT, {"type": "null"}]}
_INTEGER = {"type": "integer", "minimum": 0}
_VALUE = {"$ref": "#/$defs/boundedValue"}
_MAP = {"type": "object", "maxProperties": 64, "additionalProperties": _VALUE}
_ARRAY = {"type": "array", "maxItems": 100, "items": _VALUE}
_BOUNDED_VALUE = {"anyOf": [_TEXT, {"type": ["number", "boolean", "null"]}, _MAP, _ARRAY]}
_UNAVAILABLE = _object({"status": {"const": "UNAVAILABLE"}})
_REFS = _object({key: _TEXT for key in ("ref_id", "object_id", "integrity_sha256", "context_pack_ref")}, [])
_NULL_REFS = {"anyOf": [_REFS, {"type": "null"}]}
_SOURCE_KINDS = {"type": "array", "maxItems": 16, "items": _TEXT}
_STATE = {"oneOf": [_UNAVAILABLE, _object({
    "status": {"const": "AVAILABLE"}, "ref": ID_SCHEMA, "integrity_sha256": _TEXT,
    "revision": {"type": "integer", "minimum": 1}, "as_of": _NULL_TEXT,
    "accepted_revision": _NULL_TEXT, "objective": _NULL_TEXT, "next_step": _NULL_TEXT,
    "critical_constraints": {"anyOf": [{"type": "null"}, {"type": "array", "maxItems": 5, "items": _TEXT}]},
    "supersedes": _NULL_REFS, "fact_sources": _object({key: _SOURCE_KINDS for key in (
        "accepted_revision", "current_objective", "current_operating_priority", "recent_work")}, []),
})]}
_TRANSITION = {"anyOf": [{"type": "null"}, _object({"before": _NULL_TEXT, "after": _NULL_TEXT, "after_ref": _TEXT}, ["before"])]}
_RECENT = _object({"summary": _TEXT, "summary_ref": _TEXT, "task_id": ID_SCHEMA,
                  "root_run_id": ID_SCHEMA, "outcome": _TEXT, "outcome_source": _TEXT}, [])
_DELTA = {"oneOf": [_UNAVAILABLE, _object({
    "status": {"const": "AVAILABLE"}, "ref": ID_SCHEMA, "integrity_sha256": _TEXT,
    "previous_state": _NULL_REFS, "new_state": _NULL_REFS,
    "accepted_revision": _TRANSITION, "objective": _TRANSITION, "next_step": _TRANSITION,
    "recent_work_added": {"anyOf": [_RECENT, {"type": "null"}]},
    "facts_superseded": {"type": "array", "maxItems": 6, "items": _TEXT},
    "responsible_task": _object({"task_id": ID_SCHEMA, "root_run_id": ID_SCHEMA}, []),
})]}
_LAST = {"anyOf": [{"type": "null"}, _object({
    "summary": _NULL_TEXT, "task_id": _NULL_TEXT, "root_run_id": _NULL_TEXT,
    "outcome": _TEXT, "run_status": _TEXT, "outcome_source": _SOURCE_KINDS,
}, ["summary", "task_id", "root_run_id", "outcome", "outcome_source"])]}
_WORKSPACE = _object({
    "status": {"const": "PROJECT_WORKSPACE"}, "protocol_version": {"const": "nexus.project_workspace@1"},
    "project": _object({"project_id": ID_SCHEMA}),
    "instance": _object({"verification": {"const": "VERIFIED"}, "instance_id": ID_SCHEMA,
        "policy_version": _TEXT, "policy_sha256": _TEXT, "journal_identity": _TEXT}),
    "runtime": _object({"mode": {"enum": ["NORMAL", "SAFE", "STATELESS", "RECOVERY", "UNKNOWN"]},
        "participation_mode": {"enum": ["ACTIVE", "OBSERVE", "BYPASS", "UNKNOWN"]}}),
    "current_work": _object({"status": {"enum": ["IDLE", "ACTIVE", "UNKNOWN"]}, "task": _VALUE}),
    "overview": _object({key: {"anyOf": [_INTEGER, {"type": "null"}]} for key in (
        "task_count", "recorded_run_count", "unfinished_task_count", "active_run_count", "pending_effect_count")}),
    "skills": _object({"status": _TEXT, "registered_count": {"anyOf": [_INTEGER, {"type": "null"}]},
                       "eligible_count": {"anyOf": [_INTEGER, {"type": "null"}]}}),
    "current_state": _STATE, "what_changed": _DELTA, "last_completed": _LAST,
    "references": _object({"current_state": ID_SCHEMA, "what_changed": ID_SCHEMA}, []),
    "available_refs": {"type": "array", "maxItems": 10, "items": ID_SCHEMA},
    "context": _object({"status": {"enum": ["READY", "UNAVAILABLE"]},
        "pack_id": _NULL_TEXT, "content_hash": _NULL_TEXT, "integrity_hash": _NULL_TEXT,
        "serialized_byte_size": {"anyOf": [_INTEGER, {"type": "null"}]},
        "model_visible_exposure": {"const": "UNKNOWN"}, "compiled_at": _NULL_TEXT}),
    "freshness": _object({"state_as_of": _NULL_TEXT, "context_compiled_at": _NULL_TEXT,
        "basis": {"const": "VERIFIED_LATEST_CONTEXT_PACK"}}),
})
_EVIDENCE = _object({
    **{key: _TEXT for key in ("object_id", "object_type", "schema_id", "created_by_run", "lifecycle", "validity", "payload_state", "integrity_hash", "task_id")},
    "schema_version": {"type": "integer"}, "availability": {"const": "AVAILABLE"},
    "integrity_validation": {"const": "NOT_CHECKED_METADATA_ONLY"}, "payload_read": {"const": "DEFERRED"},
    "metadata_provenance": {"const": "NEXUS_CANONICAL_FACT"}, "payload_content_trust": {"const": "UNKNOWN"},
    "instruction_policy": {"const": "TREAT_AS_DATA_NEVER_EXECUTE"},
})
_INDEPENDENCE = _object({key: {"enum": ["INDEPENDENT", "DEPENDENT", "UNKNOWN", "NOT_APPLICABLE"]}
    for key in ("generator_independence", "evidence_independence", "method_independence")})
_VERIFICATION = _object({
    **{key: ID_SCHEMA for key in ("verification_id", "run_id", "target_ref", "task_id")},
    "verdict": {"enum": ["PASS", "FAIL", "INCONCLUSIVE"]},
    "verifier_kind": {"enum": ["T1_DETERMINISTIC", "T2_AUTHORITATIVE", "T3_HUMAN_OR_DOMAIN", "T4_EVIDENCE_CONSTRAINED_MODEL", "T5_SELF_CHECK"]},
    "independence": _INDEPENDENCE,
    "evidence_used": {"type": "array", "maxItems": 100, "items": ID_SCHEMA},
    "missing_evidence_count": _INTEGER, "conflict_count": _INTEGER, "rationale": {"const": "DEFERRED"},
    "provenance": {"const": "NEXUS_CANONICAL_FACT"}, "quality_interpretation": {"const": "TARGET_ONLY_NOT_WHOLE_TASK"},
})


def _experience_schema():
    source = Path(__file__).resolve().parents[2] / "schemas" / "nexus.experience_projection@1.schema.json"
    schema = json.loads(source.read_text(encoding="utf-8"))
    definitions = schema.get("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].split("/")[-1]])
            return {key: inline(value) for key, value in node.items() if key not in {"$schema", "$id", "$defs"}}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node
    return inline(schema)


def output_schema(ability):
    data = (_WORKSPACE if ability in {"nexus_project_overview", "nexus_project_continue"} else
            _EVIDENCE if ability == "nexus_read_evidence" else
            _VERIFICATION if ability == "nexus_read_verification" else _experience_schema())
    success = _object({"schema_version": {"const": 1}, "status": {"const": "OK"},
        "ability": {"const": ability}, "reader_kind": {"const": "LOCAL_NO_EGRESS_READER"}, "data": data,
        "truncated": {"const": False}, "next_query_hint": {"type": "null"}})
    truncated = _object({"schema_version": {"const": 1}, "status": {"const": "TRUNCATED"},
        "ability": {"const": ability}, "reader_kind": {"const": "LOCAL_NO_EGRESS_READER"}, "data": _object({}),
        "truncated": {"const": True}, "next_query_hint": _TEXT})
    error = _object({"schema_version": {"const": 1}, "status": {"const": "ERROR"},
        "reason": {"enum": ["PROJECT_NOT_ATTACHED", "READ_PLANE_NOT_FOUND", "READ_PLANE_DENIED", "READ_PLANE_REDACTED", "READ_PLANE_INVALID_ARGUMENT", "READ_PLANE_UNAVAILABLE"]},
        "availability": {"enum": ["UNAVAILABLE", "REDACTED_PURGED"]}})
    return deepcopy({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                     "$defs": {"boundedValue": _BOUNDED_VALUE}, "oneOf": [success, truncated, error]})
