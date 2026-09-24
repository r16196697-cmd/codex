import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"
POLICY_SCHEMA = ROOT / "policies" / "nexus.policy@1.schema.json"
FORMAT_CHECKER = FormatChecker()


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(path: Path, value: dict) -> None:
    schema = load_schema(path)
    Draft202012Validator(schema, format_checker=FORMAT_CHECKER).validate(value)


def boundary() -> dict:
    return {"allowed_classifications": ["PROJECT_PRIVATE"], "handling_tags": ["LOCAL_ONLY"]}


def common_manifest(kind: str) -> dict:
    return {
        "schema_id": "nexus.run_manifest",
        "schema_version": 1,
        "executor_kind": kind,
        "runtime_version": "0.1.0-dev",
        "policy_version": "1",
        "schema_versions": {"nexus.run_manifest": 1},
        "input_object_refs": ["obj_input_1"],
        "authority_grant_ref": "grant_local_test",
        "data_boundary": boundary(),
        "classification_assertion_ref": "class_local_test",
    }


class SchemaStructureTests(unittest.TestCase):
    def test_all_registered_schemas_are_valid_draft_2020_12(self) -> None:
        paths = sorted(SCHEMA_DIR.glob("*.schema.json")) + [POLICY_SCHEMA]
        self.assertGreaterEqual(len(paths), 10)
        for path in paths:
            with self.subTest(schema=path.name):
                schema = load_schema(path)
                Draft202012Validator.check_schema(schema)
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                for ref in self._refs(schema):
                    self.assertTrue(ref.startswith("#/"), f"Schema reference must be local and offline: {path.name} -> {ref}")

    @classmethod
    def _refs(cls, value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value["$ref"]
            for child in value.values():
                yield from cls._refs(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._refs(child)

    def test_task_positive_and_negative_examples(self) -> None:
        schema = SCHEMA_DIR / "nexus.task@1.schema.json"
        valid = {"schema_id": "nexus.task", "schema_version": 1, "task_id": "task_1", "requester_id": "principal_1", "status": "CREATED", "created_at": "2026-09-24T12:00:00Z", "command_id": "cmd_1"}
        validate(schema, valid)
        for invalid in (
            {k: v for k, v in valid.items() if k != "requester_id"},
            {**valid, "status": "UNKNOWN"},
            {**valid, "approved": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validate(schema, invalid)

    def test_root_and_child_run_executor_invariants(self) -> None:
        path = SCHEMA_DIR / "nexus.run@1.schema.json"
        root = {"schema_id": "nexus.run", "schema_version": 1, "run_id": "run_root", "task_id": "task_1", "executor_kind": "ORCHESTRATOR", "status": "CREATED", "grant_id": "grant_1", "data_boundary": boundary(), "created_at": "2026-09-24T12:00:00Z"}
        validate(path, root)
        child = {**root, "run_id": "run_child", "parent_run_id": "run_root", "executor_kind": "MODEL"}
        validate(path, child)
        for invalid in (
            {**root, "executor_kind": "MODEL"},
            {**child, "executor_kind": "ORCHESTRATOR"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validate(path, invalid)

    def test_run_manifests_require_exact_executor_specific_fields(self) -> None:
        path = SCHEMA_DIR / "nexus.run_manifest@1.schema.json"
        orchestrator = {**common_manifest("ORCHESTRATOR"), "task_contract_ref": "obj_contract", "dag_version": "1", "scheduler_version": "1"}
        model = {**common_manifest("MODEL"), "model_id": "fake-model", "provider": "fake", "model_class": "E1", "model_adapter_version": "fake-1", "prompt_version": "p1", "context_object_refs": [], "route_decision_ref": "route_1"}
        tool = {**common_manifest("TOOL"), "tool_id": "fake-read", "tool_descriptor_version": "1", "tool_adapter_version": "1", "input_ref": "obj_tool_input"}
        for value in (orchestrator, model, tool):
            with self.subTest(kind=value["executor_kind"]):
                validate(path, value)
        invalid_orchestrator = {**orchestrator, "model_id": "must-not-exist"}
        invalid_model = {**model, "executor_kind": "ORCHESTRATOR"}
        for value in (invalid_orchestrator, invalid_model, {k: v for k, v in orchestrator.items() if k != "scheduler_version"}):
            with self.subTest(invalid=value), self.assertRaises(ValidationError):
                validate(path, value)

    def test_effect_keeps_three_axes_and_rejects_invalid_cancel(self) -> None:
        path = SCHEMA_DIR / "nexus.effect@1.schema.json"
        valid = {"schema_id": "nexus.effect", "schema_version": 1, "effect_id": "effect_1", "run_id": "run_1", "tool_id": "fake-write", "action_type": "test", "target_ref": "sandbox_target", "idempotency_key": "idem_1", "grant_id": "grant_1", "execution_state": "FINISHED", "effect_outcome": "UNKNOWN", "reconciliation_status": "HUMAN_REQUIRED"}
        validate(path, valid)
        with self.assertRaises(ValidationError):
            validate(path, {k: v for k, v in valid.items() if k != "reconciliation_status"})
        with self.assertRaises(ValidationError):
            validate(path, {**valid, "execution_state": "CANCELLED", "effect_outcome": "COMMITTED"})
        with self.assertRaises(ValidationError):
            validate(path, {**valid, "effect_outcome": "UNDETERMINED"})
        with self.assertRaises(ValidationError):
            validate(path, {**valid, "reconciliation_status": "NOT_REQUIRED"})
        with self.assertRaises(ValidationError):
            validate(path, {**valid, "state": "COMPENSATED"})

    def test_trace_event_references_objects_and_rejects_payload_fields(self) -> None:
        path = SCHEMA_DIR / "nexus.trace_event@1.schema.json"
        valid = {"schema_id": "nexus.trace_event", "schema_version": 1, "event_id": "event_1", "run_id": "run_1", "seq_no": 1, "event_type": "nexus.run.started", "occurred_at": "2026-09-24T12:00:00Z", "actor_id": "principal_1", "object_refs": ["obj_input_1"], "effect_refs": [], "policy_refs": ["policy_1"], "authority_refs": ["grant_1"], "data_boundary": boundary(), "classification_assertion_ref": "class_1", "typed_metadata": {"attempt": 1}}
        validate(path, valid)
        for field in ("prompt", "tool_output", "output_preview", "payload"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate(path, {**valid, field: "must-be-an-object-ref"})

    def test_hash_profile_pins_integrity_to_sha256(self) -> None:
        path = SCHEMA_DIR / "nexus.hash_profile@1.schema.json"
        valid = {"schema_id": "nexus.hash_profile", "schema_version": 1, "profile_id": "raw-sha256", "profile_version": 1, "integrity_profile": {"representation": "RAW_BYTES", "hash_algorithm": "SHA-256"}}
        validate(path, valid)
        with self.assertRaises(ValidationError):
            validate(path, {**valid, "integrity_profile": {"representation": "RAW_BYTES", "hash_algorithm": "SHA-1"}})

    def test_identity_approval_and_object_contract_examples(self) -> None:
        identity_examples = (
            ("nexus.principal@1.schema.json", {"schema_id": "nexus.principal", "schema_version": 1, "principal_id": "principal_1", "principal_type": "HUMAN", "status": "ACTIVE"}),
            ("nexus.trust_anchor@1.schema.json", {"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor_1", "principal_id": "principal_1", "policy_ref": "policy_1"}),
            ("nexus.delegation_grant@1.schema.json", {"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant_1", "issued_by": "principal_1", "granted_to": "tool_1", "task_scope": ["task_1"], "resource_scope": ["local://test"], "action_scope": ["read"], "audience_scope": ["nexus"], "issued_at": "2026-09-24T12:00:00Z", "expires_at": "2026-09-25T12:00:00Z", "status": "ACTIVE", "policy_version": "1"}),
            ("nexus.approval_decision@1.schema.json", {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval_1", "approver_principal_id": "principal_1", "target_type": "Effect", "target_ref": "effect_1", "effect_id": "effect_1", "decision": "APPROVE", "approved_scope": ["sandbox write"], "policy_version": "1", "issued_at": "2026-09-24T12:00:00Z"}),
        )
        for name, value in identity_examples:
            with self.subTest(schema=name):
                validate(SCHEMA_DIR / name, value)
        with self.assertRaises(ValidationError):
            validate(SCHEMA_DIR / "nexus.approval_decision@1.schema.json", {**identity_examples[-1][1], "approved": True})

        object_value = {"schema_id": "nexus.object", "schema_version": 1, "object_id": "obj_1", "object_type": "artifact", "payload_uri": "objects/sha256/ab/hash", "hash_profile_ref": "hash_profile_1", "integrity_hash": "a" * 64, "created_by_run": "run_1", "classification_assertion_ref": "class_1"}
        validate(SCHEMA_DIR / "nexus.object@1.schema.json", object_value)
        with self.assertRaises(ValidationError):
            validate(SCHEMA_DIR / "nexus.object@1.schema.json", {**object_value, "payload": "inline payload is forbidden"})

    def test_schema_registration_and_migration_contract_examples(self) -> None:
        registration = {"schema_id": "nexus.schema_registration", "schema_version": 1, "registration_id": "schema_reg_1", "registered_schema_id": "nexus.task", "registered_schema_version": 1, "status": "ACTIVE", "schema_object_ref": "obj_schema_1", "created_at": "2026-09-24T12:00:00Z"}
        validate(SCHEMA_DIR / "nexus.schema_registration@1.schema.json", registration)
        migration = {"schema_id": "nexus.schema_migration", "schema_version": 1, "migration_id": "migration_1", "input_schema_id": "nexus.task", "input_version": 1, "output_schema_id": "nexus.task", "output_version": 2, "migration_version": "1.0.0", "lossless": False, "transform_ref": "code://migration_1"}
        validate(SCHEMA_DIR / "nexus.schema_migration@1.schema.json", migration)
        with self.assertRaises(ValidationError):
            validate(SCHEMA_DIR / "nexus.schema_migration@1.schema.json", {**migration, "lossless": "unknown"})

    def test_task_contract_and_classification_assertion_examples(self) -> None:
        task_contract = {"schema_id": "nexus.task_contract", "schema_version": 1, "task_id": "task_1", "requester_id": "principal_1", "goal": "Test a local artifact", "constraints": ["No network egress"], "success_criteria": ["Hash matches"], "risk_class": "STANDARD", "budget_account_ref": "budget_1", "routing_constraints": {"allowed_providers": [], "forbidden_providers": [], "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]}, "routing_preferences": {"optimize_for": "BALANCED"}, "created_at": "2026-09-24T12:00:00Z"}
        validate(SCHEMA_DIR / "nexus.task_contract@1.schema.json", task_contract)
        with self.assertRaises(ValidationError):
            validate(SCHEMA_DIR / "nexus.task_contract@1.schema.json", {**task_contract, "routing_constraints": {**task_contract["routing_constraints"], "locality": "LOCAL_ONLY", "provider_override": "unapproved"}})

        assertion = {"schema_id": "nexus.classification_assertion", "schema_version": 1, "assertion_id": "class_1", "subject_type": "OBJECT", "subject_ref": "obj_1", "sensitivity_level": "PROJECT_PRIVATE", "handling_tags": ["NO_EXTERNAL_EGRESS"], "policy_version": "1", "reason": "Local test data", "actor_id": "principal_1"}
        validate(SCHEMA_DIR / "nexus.classification_assertion@1.schema.json", assertion)
        with self.assertRaises(ValidationError):
            validate(SCHEMA_DIR / "nexus.classification_assertion@1.schema.json", {**assertion, "sensitivity_level": "UNKNOWN"})

    def test_default_policy_is_fail_closed(self) -> None:
        policy = json.loads((ROOT / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        validate(POLICY_SCHEMA, policy)
        self.assertEqual(policy["trust_anchors"], [])
        self.assertEqual(policy["egress"]["default"], "DENY")
        self.assertFalse(policy["learning"]["auto_promote"])
        self.assertTrue(policy["purge"]["execution_barrier_required"])


if __name__ == "__main__":
    unittest.main()
