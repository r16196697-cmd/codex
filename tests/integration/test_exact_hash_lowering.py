"""Exact-content selected-release evaluation, exclusively disposable fixtures."""

import hashlib
import inspect
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kernel.authority import AuthorizationDenied
from kernel.memory import MemoryService
from kernel.object.errors import CommandConflict, IntegrityMismatch, ObjectNotFound, PurgedObject
from kernel.purge import PurgeService
from kernel.run import TraceRuntime
from kernel.verification import VerificationService
from tests.integration import test_egress_decision as fixtures


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


class ExactHashLoweringTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EgressDecisionTests()
        self.fixture.addCleanup = self.addCleanup
        self.fixture.setUp()
        self.store = self.fixture.store
        self.authority = self.fixture.authority
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "evaluation-grant", "issued_by": "human-root", "granted_to": "human-root",
            "task_scope": ["task"], "resource_scope": ["object-a", "object-b", "object-c", "object-d", "object-unsafe",
                "fixture-run", "evt-create-fixture-run", "evt-cancel-fixture-run", "plan-fixture", "record-fixture"],
            "action_scope": ["CLASSIFY", "CLASSIFICATION_LOWER", "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "PURGE_EXECUTE"],
            "audience_scope": ["fixture-target", "nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "evaluation-grant")
        self.trace = TraceRuntime(self.store, self.authority)
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task",
            "requester_id": "human-root", "status": "CREATED", "created_at": now.isoformat(), "command_id": "create-fixture-task"})
        self.classify("fixture-run", "class-run", subject_type="RUN")
        self.classify("evt-create-fixture-run", "class-created", subject_type="TRACE_EVENT")
        self.trace.create_run({"schema_id": "nexus.run", "schema_version": 1, "run_id": "fixture-run", "task_id": "task",
            "executor_kind": "ORCHESTRATOR", "status": "CREATED", "grant_id": "evaluation-grant",
            "data_boundary": {"allowed_classifications": ["PUBLIC", "PROJECT_PRIVATE"], "handling_tags": ["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"]},
            "classification_assertion_ref": "class-run", "created_at": now.isoformat()},
            command_id="create-fixture-run", event_classification_assertion_ref="class-created")

    def classify(self, target, key, *, tags=(), level="PROJECT_PRIVATE", subject_type="OBJECT", parent=None, approval=None):
        assertion = self.fixture.assertion(key, target, level, list(tags), parent)
        assertion["subject_type"] = subject_type
        self.authority.record_classification_assertion(assertion, grant_id="evaluation-grant", task_id="task",
            audience="fixture-target", command_id="classify-" + key, approval_id=approval)
        return assertion

    def put(self, target, payload=None, *, tags=("LOCAL_ONLY",), sources=()):
        self.classify(target, "initial-" + target, tags=tags)
        self.store.put_object(command_id="put-" + target, object_id=target,
            payload=payload if payload is not None else canonical({"fixture": target}), object_type="artifact",
            created_by_run="fixture-run", classification_assertion_ref="initial-" + target, derived_from=sources)
        return self.store.get_object_metadata(target)["integrity_hash"]

    def approve(self, target, key, digest, **extra):
        approval = {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": key,
            "approver_principal_id": "human-root", "target_type": "CLASSIFICATION_LOWER", "target_ref": target,
            "decision": "APPROVE", "approved_scope": ["CLASSIFICATION_LOWER", target], "policy_version": "1",
            "issued_at": datetime.now(timezone.utc).isoformat(), **extra}
        if digest is not None:
            approval["payload_integrity_hash"] = digest
        self.authority.create_approval(approval, "approve-" + key)

    def lower(self, target, approval, key=None):
        return self.classify(target, key or "lowered-" + target, parent="initial-" + target, approval=approval)

    def assertion_count(self, key):
        with self.store._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE assertion_id=?", (key,)).fetchone()[0]

    def test_missing_wrong_and_other_object_hash_cannot_commit(self):
        digest_a = self.put("object-a")
        digest_b = self.put("object-b")
        self.assertNotEqual(digest_a, digest_b)
        for key, digest in (("missing-hash", None), ("wrong-hash", "0" * 64), ("other-hash", digest_b)):
            self.approve("object-a", key, digest)
            with self.assertRaisesRegex(AuthorizationDenied, "APPROVAL_PAYLOAD_HASH_MISMATCH"):
                self.lower("object-a", key, key)
            self.assertEqual(self.assertion_count(key), 0)
        self.approve("object-a", "exact-hash", digest_a)
        self.lower("object-a", "exact-hash")
        self.assertEqual(self.authority.current_classification("object-a")["handling_tags"], [])

    def test_cross_object_approval_is_rejected_even_with_valid_source_hash(self):
        digest = self.put("object-a")
        self.put("object-b")
        self.approve("object-a", "exact", digest)
        self.lower("object-a", "exact")
        with self.assertRaisesRegex(AuthorizationDenied, "APPROVAL_TARGET_MISMATCH"):
            self.lower("object-b", "exact")
        self.assertEqual(self.assertion_count("lowered-object-b"), 0)

    def test_exact_replay_after_revocation_and_approval_expiry_has_zero_writes(self):
        digest = self.put("object-a")
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.approve("object-a", "exact", digest, expires_at=expires.isoformat())
        assertion = self.lower("object-a", "exact")
        self.authority.revoke_grant("evaluation-grant", "revoke-evaluation")
        self.store.close()
        before = fixtures.commitment(self.fixture.root)
        # Read-only replay is not a write API. Reopen the writer solely to
        # return its historical CommandLedger result, then compare closed files.
        from tests.support.test_store import open_test_store
        self.store = open_test_store(self.fixture.root / "data", policy=self.fixture.policy)
        self.addCleanup(self.store.close)
        from kernel.authority import AuthorityService
        self.authority = AuthorityService(self.store, self.fixture.policy)
        with mock.patch("kernel.authority.service._now", return_value=expires + timedelta(seconds=1)), \
                mock.patch.object(self.store, "get_object_metadata", side_effect=AssertionError("replay must not resolve a new hash")):
            self.authority.record_classification_assertion(assertion, grant_id="evaluation-grant", task_id="task",
                audience="fixture-target", command_id="classify-lowered-object-a", approval_id="exact")
        self.assertEqual(self.assertion_count("lowered-object-a"), 1)
        self.store.close()
        self.assertEqual(fixtures.commitment(self.fixture.root), before)

    def test_initial_classify_needs_no_object_and_nonobject_lowering_is_unchanged(self):
        self.classify("object-a", "initial-only", tags=["LOCAL_ONLY"])
        with self.assertRaises(ObjectNotFound):
            self.store.get_object_metadata("object-a")
        for subject_type, target, parent in (("RUN", "fixture-run", "class-run"),
                                             ("TRACE_EVENT", "evt-create-fixture-run", "class-created")):
            key = "approval-" + subject_type
            self.approve(target, key, None)
            with mock.patch.object(self.store, "get_object_metadata", side_effect=AssertionError("nonobject lowering must not resolve Object metadata")):
                self.classify(target, "low-" + subject_type, level="PUBLIC", subject_type=subject_type, parent=parent, approval=key)

    def test_missing_object_and_invalid_canonical_hash_fail_closed(self):
        self.classify("object-a", "initial-object-a", tags=["LOCAL_ONLY"])
        self.approve("object-a", "missing-object", "0" * 64)
        with self.assertRaises(ObjectNotFound):
            self.lower("object-a", "missing-object")
        self.put("object-b")
        self.approve("object-b", "invalid-meta", "0" * 64)
        with mock.patch.object(self.store, "get_object_metadata", return_value={"object_id": "object-b", "payload_state": "AVAILABLE", "integrity_hash": "INVALID"}):
            with self.assertRaisesRegex(AuthorizationDenied, "CLASSIFICATION_LOWER_OBJECT_UNAVAILABLE"):
                self.lower("object-b", "invalid-meta")
        self.assertNotIn("payload_integrity_hash", inspect.signature(self.authority.record_classification_assertion).parameters)

    def selected_bytes(self, sources):
        fields = ["project_id", "current_state_revision", "accepted_revision", "work_status", "objective", "next_step", "model_visible_exposure"]
        workspace = {}
        for source in sorted(set(sources)):
            document = json.loads(self.store.get_payload(source))
            for field in fields:
                if field in document:
                    if field in workspace and workspace[field] != document[field]:
                        raise ValueError("fixture selection conflicts")
                    workspace[field] = document[field]
        if set(workspace) != set(fields):
            raise ValueError("fixture selection incomplete")
        return canonical({"projection_version": 1, "source_refs": sorted(set(sources)), "selected_fields": sorted(fields), "workspace": workspace})

    def selected(self):
        self.put("object-a", canonical({"project_id": "fixture-project", "current_state_revision": 7,
            "accepted_revision": "1" * 40, "work_status": "IDLE", "objective": "Evaluate selected release",
            "private_payload": "excluded A"}), tags=["LOCAL_ONLY"])
        self.put("object-b", canonical({"next_step": "Independent review", "model_visible_exposure": "UNKNOWN",
            "private_payload": "excluded B"}), tags=["NO_EXTERNAL_EGRESS"])
        sources = ["object-a", "object-b"]
        payload = self.selected_bytes(sources)
        digest = self.put("object-c", payload, tags=["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"], sources=sources)
        self.approve("object-c", "release-exact", digest)
        return payload, digest

    def test_selected_projection_inheritance_release_isolation_and_read_only_decisions(self):
        payload, digest = self.selected()
        self.assertEqual(payload, self.selected_bytes(["object-b", "object-a", "object-a"]))
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertNotIn(b"excluded", payload)
        self.assertEqual(self.authority.current_classification("object-c")["sensitivity_level"], "PROJECT_PRIVATE")
        self.assertEqual(self.authority.current_classification("object-c")["handling_tags"], ["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"])
        with self.store._connection() as conn:
            self.assertEqual({row[0] for row in conn.execute("SELECT to_id FROM object_relations WHERE from_id='object-c' AND relation_type='derived_from'")}, {"object-a", "object-b"})
            self.assertEqual(conn.execute("SELECT task_id FROM runs WHERE run_id=?", (self.store.get_object_metadata("object-c")["created_by_run"],)).fetchone()[0], "task")
        original = {key: (self.store.get_payload(key), self.store.get_object_metadata(key)["integrity_hash"], self.authority.current_classification(key)) for key in ("object-a", "object-b")}
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-c"])["reason_code"], "EGRESS_RESTRICTIVE_TAG")
        self.classify("object-unsafe", "class-unsafe", tags=[])
        with self.assertRaisesRegex(IntegrityMismatch, "DERIVED_CLASSIFICATION_DOWNGRADE"):
            self.store.put_object(command_id="unsafe-put", object_id="object-unsafe", payload=b"unsafe fixture", object_type="artifact",
                created_by_run="fixture-run", classification_assertion_ref="class-unsafe", derived_from=["object-a", "object-b"])
        self.classify("object-d", "class-unsafe-sensitivity", tags=["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"], level="PUBLIC")
        with self.assertRaisesRegex(IntegrityMismatch, "DERIVED_CLASSIFICATION_DOWNGRADE"):
            self.store.put_object(command_id="unsafe-sensitivity-put", object_id="object-d", payload=b"unsafe sensitivity fixture", object_type="artifact",
                created_by_run="fixture-run", classification_assertion_ref="class-unsafe-sensitivity", derived_from=["object-a", "object-b"])
        self.lower("object-c", "release-exact")
        self.assertEqual(self.authority.current_classification("object-c")["assertion_id"], "lowered-object-c")
        for key, source in original.items():
            self.assertEqual((self.store.get_payload(key), self.store.get_object_metadata(key)["integrity_hash"], self.authority.current_classification(key)), source)
        reader = self.fixture.reader()
        before, counts = self.fixture.before_read, self.fixture.counts(reader)
        self.assertEqual(reader.decide_egress("fixture-target", ["object-c"])["decision"], "ALLOW")
        for ids in (["object-a"], ["object-b"], ["object-a", "object-c"], ["object-b", "object-c"]):
            self.assertEqual(reader.decide_egress("fixture-target", ids)["reason_code"], "EGRESS_RESTRICTIVE_TAG")
        self.assertEqual(self.fixture.counts(reader), counts)
        reader.store.close()
        self.assertEqual(fixtures.commitment(self.fixture.root), before)

    def test_changed_content_needs_new_identity_and_new_approval(self):
        payload, digest = self.selected()
        self.lower("object-c", "release-exact")
        changed = canonical({**json.loads(payload), "projection_version": 2})
        with self.assertRaisesRegex(CommandConflict, "OBJECT_ID_ALREADY_EXISTS"):
            self.store.put_object(command_id="replace-object-c", object_id="object-c", payload=changed, object_type="artifact",
                created_by_run="fixture-run", classification_assertion_ref="initial-object-c", derived_from=["object-a", "object-b"])
        digest_d = self.put("object-d", changed, tags=["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"], sources=["object-a", "object-b"])
        self.assertNotEqual(digest, digest_d)
        with self.assertRaisesRegex(AuthorizationDenied, "APPROVAL_TARGET_MISMATCH"):
            self.lower("object-d", "release-exact")
        self.approve("object-d", "new-exact", digest_d)
        self.lower("object-d", "new-exact")
        self.assertEqual(self.store.get_payload("object-c"), payload)
        self.store.put_object(command_id="put-object-c", object_id="object-c", payload=payload, object_type="artifact",
            created_by_run="fixture-run", classification_assertion_ref="initial-object-c", derived_from=["object-b", "object-a"])
        with self.assertRaisesRegex(CommandConflict, "COMMAND_CONFLICT"):
            self.store.put_object(command_id="put-object-c", object_id="object-c", payload=changed, object_type="artifact",
                created_by_run="fixture-run", classification_assertion_ref="initial-object-c", derived_from=["object-a", "object-b"])

    def purge_source(self):
        self.classify("evt-cancel-fixture-run", "class-cancel", subject_type="TRACE_EVENT")
        self.trace.transition_run(command_id="cancel-fixture-run", run_id="fixture-run", expected_state="CREATED", next_state="CANCELLED", classification_assertion_ref="class-cancel")
        memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        purge = PurgeService(self.store, self.authority, memory, independent_journal_path=self.store.independent_purge_journal_path)
        plan = purge.plan(command_id="plan-fixture", plan_id="plan-fixture", task_id="task", target_refs=["object-a"])
        approval = {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "purge-exact",
            "approver_principal_id": "human-root", "target_type": "PURGE_EXECUTE", "target_ref": "plan-fixture", "effect_id": "record-fixture",
            "payload_integrity_hash": plan["plan_hash"], "decision": "APPROVE", "approved_scope": ["PURGE_EXECUTE", "plan-fixture"],
            "policy_version": "1", "issued_at": datetime.now(timezone.utc).isoformat()}
        self.authority.create_approval(approval, "purge-approval")
        result = purge.execute(command_id="execute-fixture-purge", record_id="record-fixture", barrier_id="barrier-fixture", plan=plan,
            grant_id="evaluation-grant", task_id="task", approval_id="purge-exact")
        self.assertEqual(result["status"], "COMPLETED")
        return plan

    def test_source_purge_covers_released_projection_and_redacts_provenance(self):
        payload, digest = self.selected()
        assertion = self.lower("object-c", "release-exact")
        plan = self.purge_source()
        self.assertEqual(plan["descendant_refs"], ["object-c"])
        for target in ("object-a", "object-c"):
            self.assertEqual(self.store.get_object_metadata(target), {"payload_state": "PURGED"})
            with self.assertRaises(PurgedObject):
                self.store.get_payload(target)
        self.assertFalse((self.store.blob_root / digest[:2] / digest).exists())
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-c"])["decision"], "DENY")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM object_relations WHERE from_id='object-c' OR to_id='object-a'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT subject_ref FROM classification_assertions WHERE assertion_id='lowered-object-c'").fetchone()[0], "REDACTED_PURGED")
            self.assertEqual(conn.execute("SELECT target_ref FROM approval_decisions WHERE approval_id='release-exact'").fetchone()[0], "REDACTED_PURGED")
        self.assertEqual(self.authority.current_classification("object-b")["handling_tags"], ["NO_EXTERNAL_EGRESS"])
        with mock.patch.object(self.store, "get_object_metadata", side_effect=AssertionError("historical replay must not read purged payload")):
            self.authority.record_classification_assertion(assertion, grant_id="evaluation-grant", task_id="task",
                audience="fixture-target", command_id="classify-lowered-object-c", approval_id="release-exact")
        with self.assertRaises(AuthorizationDenied):
            self.lower("object-c", "release-exact", "new-after-purge")

    def test_purge_before_metadata_resolution_denies_lowering(self):
        digest = self.put("object-a")
        self.approve("object-a", "exact", digest)
        metadata = self.store.get_object_metadata
        def interleave(target):
            self.purge_source()
            return metadata(target)
        with mock.patch.object(self.store, "get_object_metadata", side_effect=interleave):
            with self.assertRaisesRegex(AuthorizationDenied, "CLASSIFICATION_LOWER_OBJECT_UNAVAILABLE"):
                self.lower("object-a", "exact")
        self.assertEqual(self.assertion_count("lowered-object-a"), 0)

    def test_purge_between_approval_and_commit_blocks_new_lowering(self):
        digest = self.put("object-a")
        self.approve("object-a", "exact", digest)
        authorize = self.authority.evaluate_authorization
        def interleave(*args, **kwargs):
            result = authorize(*args, **kwargs)
            with mock.patch.object(self.authority, "evaluate_authorization", side_effect=authorize):
                self.purge_source()
            return result
        with mock.patch.object(self.authority, "evaluate_authorization", side_effect=interleave):
            with self.assertRaisesRegex(PurgedObject, "PURGED_OBJECT_REFERENCE_DENIED"):
                self.lower("object-a", "exact")
        self.assertEqual(self.assertion_count("lowered-object-a"), 0)
