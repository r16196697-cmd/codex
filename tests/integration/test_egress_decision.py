"""Authority amendment fixtures only; no external destination is contacted."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.panel.application import open_panel_application
from kernel.authority import AuthorityService, AuthorizationDenied
from kernel.object.errors import MigrationError
from tests.support.test_store import open_test_store


def commitment(root):
    return {path.relative_to(root).as_posix():
            (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(root.rglob("*")) if path.is_file()}


class EgressDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-egress-decision-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies/default-policy.json").read_text())
        self.policy["trust_anchors"] = ["human-root"]
        self.policy["egress"]["allowed_destinations"] = {
            "fixture-target": {"max_sensitivity": "PROJECT_PRIVATE", "accepted_tags": ["A", "B", "LOCAL_ONLY", "NO_EXTERNAL_EGRESS"]}}
        self.store = open_test_store(self.root / "data", policy=self.policy)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "human-root", "principal_type": "HUMAN", "status": "ACTIVE"}, "principal")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "anchor", "principal_id": "human-root", "policy_ref": "1"}, "anchor")
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "grant", "issued_by": "human-root", "granted_to": "human-root",
            "task_scope": ["task"], "resource_scope": ["object-a", "object-b", "object-c", "fixture-target"],
            "action_scope": ["CLASSIFY", "CLASSIFICATION_LOWER", "EGRESS"], "audience_scope": ["fixture-target"],
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
            "status": "ACTIVE", "policy_version": "1"}, "grant")

    def assertion(self, key="initial-a", subject="object-a", level="PROJECT_PRIVATE", tags=None, parent=None):
        value = {"schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": key, "subject_type": "OBJECT", "subject_ref": subject,
            "sensitivity_level": level, "handling_tags": tags or [], "policy_version": "1",
            "reason": "synthetic classification", "actor_id": "human-root"}
        if parent is not None:
            value["supersedes"] = parent
        return value

    def object(self, key="object-a", level="PROJECT_PRIVATE", tags=None):
        assertion = self.assertion("initial-" + key[-1], key, level, tags)
        self.authority.record_classification_assertion(assertion, grant_id="grant", task_id="task",
            audience="fixture-target", command_id="class-" + key)
        self.store.put_object(command_id="put-" + key, object_id=key, payload=("synthetic " + key).encode(),
            object_type="artifact", created_by_run="fixture-run", classification_assertion_ref=assertion["assertion_id"])

    def lower(self):
        lowered = self.assertion("lowered-a", tags=[], parent="initial-a")
        self.authority.create_approval({"schema_id": "nexus.approval_decision", "schema_version": 1,
            "approval_id": "approval", "approver_principal_id": "human-root", "target_type": "CLASSIFICATION_LOWER",
            "target_ref": "object-a", "decision": "APPROVE", "approved_scope": ["CLASSIFICATION_LOWER", "object-a"],
            "payload_integrity_hash": self.store.get_object_metadata("object-a")["integrity_hash"],
            "policy_version": "1", "issued_at": datetime.now(timezone.utc).isoformat()}, "approval")
        self.authority.record_classification_assertion(lowered, grant_id="grant", task_id="task",
            audience="fixture-target", command_id="lower", approval_id="approval")

    def reader(self):
        self.store.close()
        self.before_read = commitment(self.root)
        app = open_panel_application(self.root / "data", policy_path=self.root / ".data.test-policy.json",
            independent_purge_journal_path=self.root / "data.purge-journal.jsonl", read_only=True)
        self.addCleanup(app.close)
        return AuthorityService(app.store, app.store.policy)

    def counts(self, authority):
        with authority.store._connection() as conn:
            return tuple(conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                         for name in ("authority_events", "command_ledger"))

    def corrupt_insert(self, assertion):
        # Malformed-history adversarial fixture only. Happy paths exclusively
        # use real classification, HUMAN Approval and Object primitives.
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?,?,?)", (
                assertion["assertion_id"], assertion["subject_type"], assertion["subject_ref"],
                assertion["sensitivity_level"], json.dumps(assertion["handling_tags"]),
                assertion["policy_version"], assertion["reason"], assertion["actor_id"], assertion.get("supersedes")))

    def test_approved_lowering_resolves_current_leaf_without_rewriting_envelope(self):
        self.object(tags=["LOCAL_ONLY"])
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-a"])["reason_code"], "EGRESS_RESTRICTIVE_TAG")
        self.lower()
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT classification_assertion_ref FROM object_envelopes WHERE object_id='object-a'").fetchone()[0], "initial-a")
        reader = self.reader()
        before, counts = self.before_read, self.counts(reader)
        self.assertEqual(reader.current_classification("object-a"), {
            "assertion_id": "lowered-a", "sensitivity_level": "PROJECT_PRIVATE", "handling_tags": [], "policy_version": "1"})
        self.assertEqual(reader.decide_egress("fixture-target", ["object-a"])["decision"], "ALLOW")
        self.assertEqual(self.counts(reader), counts)
        reader.store.close()
        self.assertEqual(commitment(self.root), before)

    def test_read_only_execution_surfaces_remain_blocked(self):
        self.object()
        reader = self.reader()
        before = self.before_read
        for call in (lambda: reader.evaluate_egress("fixture-target", ["object-a"]),
                     lambda: reader.authorize_egress(grant_id="grant", task_id="task", destination="fixture-target",
                         audience="fixture-target", object_ids=["object-a"], command_id="deny")):
            with self.assertRaisesRegex(MigrationError, "READ_ONLY_STORE_MUTATION_DENIED"):
                call()
        reader.store.close()
        self.assertEqual(commitment(self.root), before)

    def test_read_only_denials_are_structured_and_have_zero_writes(self):
        self.object(tags=["LOCAL_ONLY", "NO_EXTERNAL_EGRESS"])
        reader = self.reader()
        before, counts = self.before_read, self.counts(reader)
        for destination, objects, reason in (
                ("fixture-target", [], "EGRESS_EMPTY"),
                ("fixture-target", ["missing"], "EGRESS_OBJECT_NOT_FOUND"),
                ("unknown", ["object-a"], "EGRESS_DESTINATION_DENIED"),
                ("fixture-target", ["object-a"], "EGRESS_RESTRICTIVE_TAG")):
            with self.subTest(reason=reason):
                result = reader.decide_egress(destination, objects)
                self.assertEqual((result["decision"], result["reason_code"]), ("DENY", reason))
        self.assertEqual(self.counts(reader), counts)
        reader.store.close()
        self.assertEqual(commitment(self.root), before)

    def test_each_restrictive_tag_is_denied_even_if_destination_accepts_it(self):
        for tag in ("LOCAL_ONLY", "NO_EXTERNAL_EGRESS"):
            key = "object-a" if tag == "LOCAL_ONLY" else "object-b"
            self.object(key, tags=[tag])
            self.assertEqual(self.authority.decide_egress("fixture-target", [key])["reason_code"], "EGRESS_RESTRICTIVE_TAG")

    def test_multiple_objects_use_max_sensitivity_union_and_unique_count(self):
        self.object(level="PUBLIC", tags=["B"])
        self.object("object-b", tags=["A"])
        reader = self.reader()
        result = reader.decide_egress("fixture-target", ["object-a", "object-b", "object-a"])
        self.assertEqual(result, reader.decide_egress("fixture-target", ["object-b", "object-a"]))
        self.assertEqual((result["decision"], result["effective_sensitivity"], result["effective_handling_tags"], result["object_count"]),
            ("ALLOW", "PROJECT_PRIVATE", ["A", "B"], 2))

    def test_policy_maximum_and_accepted_tags(self):
        self.object(level="CONFIDENTIAL")
        self.object("object-b", tags=["UNACCEPTED"])
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-a"])["reason_code"], "EGRESS_SENSITIVITY_EXCEEDED")
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-b"])["reason_code"], "EGRESS_TAG_NOT_ACCEPTED")

    def test_writer_authorization_uses_current_leaf_and_retains_denial_audit(self):
        self.object(tags=["LOCAL_ONLY"])
        before = self.counts(self.authority)
        with self.assertRaisesRegex(AuthorizationDenied, "EGRESS_POLICY_DENIED"):
            self.authority.authorize_egress(grant_id="grant", task_id="task", destination="fixture-target",
                audience="fixture-target", object_ids=["object-a"], command_id="egress-denied")
        self.assertGreater(self.counts(self.authority)[0], before[0])
        self.assertGreater(self.counts(self.authority)[1], before[1])
        self.lower()
        self.assertTrue(self.authority.evaluate_egress("fixture-target", ["object-a"]))
        self.assertTrue(self.authority.authorize_egress(grant_id="grant", task_id="task", destination="fixture-target",
            audience="fixture-target", object_ids=["object-a"], command_id="egress-allowed"))
        with self.assertRaises(AuthorizationDenied):
            self.authority.authorize_egress(grant_id="missing", task_id="task", destination="fixture-target",
                audience="fixture-target", object_ids=["object-a"], command_id="missing-grant")

    def assert_invalid_history(self, reason):
        reader = self.reader()
        before, counts = self.before_read, self.counts(reader)
        with self.assertRaisesRegex(AuthorizationDenied, reason):
            reader.current_classification("object-a")
        self.assertEqual(reader.decide_egress("fixture-target", ["object-a"])["reason_code"], reason)
        self.assertEqual(self.counts(reader), counts)
        reader.store.close()
        self.assertEqual(commitment(self.root), before)

    def test_multiple_leaves_fail_closed(self):
        self.object()
        self.corrupt_insert(self.assertion("branch-a", parent="initial-a"))
        self.corrupt_insert(self.assertion("branch-b", parent="initial-a"))
        self.assert_invalid_history("EGRESS_CLASSIFICATION_AMBIGUOUS")

    def test_disconnected_same_subject_is_not_selected(self):
        self.object()
        self.corrupt_insert(self.assertion("unanchored"))
        self.assert_invalid_history("EGRESS_CLASSIFICATION_AMBIGUOUS")

    def test_wrong_subject_successor_fails_closed(self):
        self.object()
        self.corrupt_insert(self.assertion("alien", subject="object-b", parent="initial-a"))
        self.assert_invalid_history("EGRESS_CLASSIFICATION_UNAVAILABLE")

    def test_policy_mismatch_fails_closed(self):
        self.object()
        assertion = self.assertion("bad-policy", parent="initial-a")
        assertion["policy_version"] = "other"
        self.corrupt_insert(assertion)
        self.assert_invalid_history("EGRESS_CLASSIFICATION_UNAVAILABLE")

    def test_unknown_sensitivity_fails_closed(self):
        self.object()
        del self.authority.policy["classification"]["sensitivity_rank"]["PROJECT_PRIVATE"]
        with self.assertRaisesRegex(AuthorizationDenied, "EGRESS_CLASSIFICATION_UNAVAILABLE"):
            self.authority.current_classification("object-a")
        self.assertEqual(self.authority.decide_egress("fixture-target", ["object-a"])["decision"], "DENY")

    def test_broken_supersedes_chain_fails_closed(self):
        self.object()
        self.store.close()
        with closing(sqlite3.connect(self.root / "data/nexus.sqlite")) as conn:
            # Deliberate corrupt fixture: bypass immutability only in TEMP.
            conn.execute("DROP TRIGGER classification_assertions_no_update")
            conn.execute("UPDATE classification_assertions SET supersedes='missing' WHERE assertion_id='initial-a'")
            conn.commit()
        self.assert_invalid_history("EGRESS_CLASSIFICATION_UNAVAILABLE")

    def test_cycle_with_zero_leaves_fails_closed(self):
        self.object()
        self.corrupt_insert(self.assertion("cycle", parent="initial-a"))
        self.store.close()
        with closing(sqlite3.connect(self.root / "data/nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER classification_assertions_no_update")
            conn.execute("UPDATE classification_assertions SET supersedes='cycle' WHERE assertion_id='initial-a'")
            conn.commit()
        self.assert_invalid_history("EGRESS_CLASSIFICATION_AMBIGUOUS")

    def test_invalid_tags_and_bounded_history_fail_closed(self):
        self.object()
        malformed = self.assertion("bad-tags", parent="initial-a")
        malformed["handling_tags"] = {"unexpected": "metadata"}
        self.corrupt_insert(malformed)
        self.assert_invalid_history("EGRESS_CLASSIFICATION_UNAVAILABLE")

    def test_history_limit_fails_closed(self):
        self.object()
        parent = "initial-a"
        for index in range(256):
            key = "history-" + str(index)
            self.corrupt_insert(self.assertion(key, parent=parent))
            parent = key
        self.assert_invalid_history("EGRESS_CLASSIFICATION_UNAVAILABLE")

    def test_decision_has_no_authorization_parameters(self):
        self.object()
        for field in ("grant_id", "task_id", "command_id", "approval_id"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.authority.decide_egress("fixture-target", ["object-a"], **{field: "anything"})
        for objects in (None, "object-a", [None], [""], ["object-a"] * 257):
            self.assertEqual(self.authority.decide_egress("fixture-target", objects)["reason_code"], "EGRESS_INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
