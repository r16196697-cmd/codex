from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.client.__main__ import main as client_main
from adapters.panel.application import open_panel_application
from adapters.storage import ObjectStore
from jsonschema import ValidationError
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.context import ContextPackService
from kernel.context.service import _canonical
from kernel.memory.service import MemoryService
from kernel.metering import MeteringService
from kernel.participation import ParticipationModeService
from kernel.purge import PurgeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.runtime.errors import RuntimeDenied
from tests.support.test_store import open_test_store


def _file_commitment(data_root: Path, journal_parent: Path) -> dict[str, tuple[int, str]]:
    result = {}
    for label, root in (("data", data_root), ("journal", journal_parent)):
        if not root.exists():
            result[label + "/<missing>"] = (-1, "MISSING")
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            payload = path.read_bytes()
            result[f"{label}/{relative}"] = (len(payload), hashlib.sha256(payload).hexdigest())
    return result


class ContextPackReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-context-read-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "data"
        self.journal = self.base / "journal" / "purge.jsonl"
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        policy["trust_anchors"] = ["read-human"]
        self.store = open_test_store(self.root, policy=policy, independent_purge_journal_path=self.journal)
        self.addCleanup(self.store.close)
        self.policy_path = self.base / ".data.test-policy.json"
        self.authority = AuthorityService(self.store, policy)
        self.participation = ParticipationModeService(self.store)
        self.memory = MemoryService(self.store, self.authority, verifier=None)
        self.metering = MeteringService(self.store, self.authority, self.participation)
        self.context = ContextPackService(
            store=self.store, authority=self.authority, participation=self.participation,
            memory=self.memory, metering=self.metering,
        )
        self.task_id = "read-task"
        self.run_id = "read-run"
        self.pack_id = "read-pack"
        self.source_ids = ["read-source-a", "read-source-z"]
        self._seed_governed_fixture()
        self.context.compile(
            task_id=self.task_id, run_id=self.run_id, grant_id="read-grant",
            pack_object_id=self.pack_id, classification_assertion_ref="class-read-pack",
            command_id="compile-read-pack", source_refs=self.source_ids,
        )

    def _classification(self, assertion_id, subject_type, subject_ref):
        return {
            "schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": assertion_id, "subject_type": subject_type,
            "subject_ref": subject_ref, "sensitivity_level": "PUBLIC",
            "handling_tags": [], "policy_version": "1",
            "reason": "isolated Context Pack read fixture", "actor_id": "read-service",
        }

    def _seed_governed_fixture(self):
        now = datetime.now(timezone.utc)
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "read-human", "principal_type": "HUMAN", "status": "ACTIVE",
        }, "read-human-register")
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "read-service", "principal_type": "SERVICE", "status": "ACTIVE",
        }, "read-service-register")
        self.authority.register_trust_anchor({
            "schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "read-anchor", "principal_id": "read-human", "policy_ref": "1",
        }, "read-anchor-register")
        resources = [self.task_id, "task:" + self.task_id, self.run_id,
                     "evt-read-task-create", "evt-read-run-create", self.pack_id,
                     *self.source_ids, *("object:" + ref for ref in [self.pack_id, *self.source_ids])]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "read-grant", "issued_by": "read-human", "granted_to": "read-service",
            "task_scope": [self.task_id], "resource_scope": resources,
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "INSPECT"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"],
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=3)).isoformat(),
            "status": "ACTIVE", "policy_version": "1",
        }, "read-grant-create")
        self.trace = TraceRuntime(self.store, self.authority)
        self.trace.create_task({
            "schema_id": "nexus.task", "schema_version": 1, "task_id": self.task_id,
            "requester_id": "read-human", "status": "CREATED", "created_at": now.isoformat(),
            "command_id": "read-task-create",
        })
        assertions = [
            self._classification("class-read-run", "RUN", self.run_id),
            self._classification("class-read-run-event", "TRACE_EVENT", "evt-read-run-create"),
            self._classification("class-read-pack", "OBJECT", self.pack_id),
        ]
        assertions.extend(self._classification("class-" + ref, "OBJECT", ref) for ref in self.source_ids)
        for assertion in assertions:
            self.authority.record_classification_assertion(
                assertion, grant_id="read-grant", task_id=self.task_id,
                audience="nexus-runtime", command_id="record-" + assertion["assertion_id"],
            )
        self.trace.create_run({
            "schema_id": "nexus.run", "schema_version": 1, "run_id": self.run_id,
            "task_id": self.task_id, "executor_kind": "ORCHESTRATOR", "status": "CREATED",
            "grant_id": "read-grant",
            "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
            "classification_assertion_ref": "class-read-run", "created_at": now.isoformat(),
        }, command_id="read-run-create", event_classification_assertion_ref="class-read-run-event")
        for index, ref in enumerate(self.source_ids):
            self.store.put_object(
                command_id="put-" + ref, object_id=ref, payload=f"source {index} exact bytes\n".encode(),
                object_type="user_input", created_by_run=self.run_id,
                classification_assertion_ref="class-" + ref,
            )

    def _tampered_document(self, mutate, *, update_record=None):
        original_get_payload = self.store.get_payload
        original_get_metadata = self.store.get_object_metadata
        document = json.loads(original_get_payload(self.pack_id).decode("utf-8"))
        mutate(document)
        if update_record:
            update_record(document)
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        def get_payload(ref):
            return payload if ref == self.pack_id else original_get_payload(ref)
        def get_metadata(ref):
            metadata = original_get_metadata(ref)
            return {**metadata, "integrity_hash": digest} if ref == self.pack_id else metadata
        with mock.patch.object(self.store, "get_payload", side_effect=get_payload), \
             mock.patch.object(self.store, "get_object_metadata", side_effect=get_metadata):
            return self.context.read_compiled(self.pack_id)

    def _read_only_application(self):
        self.store.close()
        return open_panel_application(
            self.root, policy_path=self.policy_path,
            independent_purge_journal_path=self.journal, read_only=True,
        )

    def test_compile_read_latest_and_completed_historical_run(self):
        with self.store._connection() as conn:
            conn.execute("DROP TRIGGER runs_identity_immutable")
            conn.execute("DROP TRIGGER tasks_identity_immutable")
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id=?", (self.run_id,))
            conn.execute("UPDATE tasks SET status='SUCCEEDED' WHERE task_id=?", (self.task_id,))
        result = self.context.read_compiled(self.pack_id)
        latest = self.context.read_latest_compiled()
        self.assertEqual(result, latest)
        self.assertEqual(result["status"], "CONTEXT_PACK_READ")
        self.assertEqual(result["pack_id"], self.pack_id)
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(result["run_id"], self.run_id)
        self.assertEqual([entry["source_ref"] for entry in result["entries"]], self.source_ids)
        self.assertEqual(result["model_visible_exposure"], "UNKNOWN")

    def test_missing_pack_and_arbitrary_object_are_unavailable_without_payload_read(self):
        with mock.patch.object(self.store, "get_payload", side_effect=AssertionError("payload must not be opened")) as get_payload:
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
                self.context.read_compiled("missing-pack")
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
                self.context.read_compiled(self.source_ids[0])
        get_payload.assert_not_called()
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
            self.context.read_latest_compiled.__self__.read_compiled("")

    def test_noncompiled_record_is_denied(self):
        with self.store._connection() as conn:
            conn.execute("DROP TRIGGER context_pack_records_no_update")
            conn.execute("UPDATE context_pack_records SET state='PURGED' WHERE pack_ref=?", (self.pack_id,))
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
            self.context.read_compiled(self.pack_id)
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
            self.context.read_latest_compiled()

    def test_pack_unavailable_or_purged_is_denied(self):
        original = self.store.get_object_metadata
        for field, value in (("payload_state", "PURGED"), ("payload_state", "ARCHIVED"),
                             ("lifecycle", "RETIRED"), ("validity", "INVALIDATED")):
            def metadata(ref, *, field=field, value=value):
                result = original(ref)
                return {**result, field: value} if ref == self.pack_id else result
            with self.subTest(field=field, value=value), mock.patch.object(self.store, "get_object_metadata", side_effect=metadata):
                with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
                    self.context.read_compiled(self.pack_id)

    def test_pack_payload_integrity_mismatch_is_denied(self):
        metadata = self.store.get_object_metadata(self.pack_id)
        path = self.store._payload_path(metadata["payload_uri"])
        original = path.read_bytes()
        path.write_bytes(original + b"tamper")
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_INTEGRITY_INVALID"):
            self.context.read_compiled(self.pack_id)

    def test_schema_invalid_payload_is_denied(self):
        original = self.store._validate
        def validate(filename, value):
            if filename == "nexus.context_pack@1.schema.json":
                raise ValidationError("invalid fixture")
            return original(filename, value)
        with mock.patch.object(self.store, "_validate", side_effect=validate):
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_PAYLOAD_INVALID"):
                self.context.read_compiled(self.pack_id)
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_PAYLOAD_INVALID"):
            self._tampered_document(lambda doc: doc.__setitem__("unexpected_member", "not accepted"))

    def test_record_document_binding_size_basis_and_source_counts_are_checked(self):
        with self.subTest("task binding"):
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_RECORD_BINDING_MISMATCH"):
                self._tampered_document(lambda doc: doc.__setitem__("task_id", "other-task"))
        with self.subTest("run binding"):
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_RECORD_BINDING_MISMATCH"):
                self._tampered_document(lambda doc: doc.__setitem__("run_id", "other-run"))

        def sql_update(field, value):
            with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                conn.execute("BEGIN")
                conn.execute("DROP TRIGGER IF EXISTS context_pack_records_no_update")
                conn.execute(f"UPDATE context_pack_records SET {field}=? WHERE pack_ref=?", (value, self.pack_id))
                conn.commit()
        try:
            # Each mutation is isolated by restoring the small record projection.
            with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                record = conn.execute("SELECT serialized_byte_size,selection_basis_json,source_counts_json,content_hash FROM context_pack_records WHERE pack_ref=?", (self.pack_id,)).fetchone()
            for field, value in (("serialized_byte_size", record[0] + 1),
                                 ("selection_basis_json", "{}"),
                                 ("source_counts_json", "{}")):
                with self.subTest(record_field=field):
                    sql_update(field, value)
                    with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_RECORD_BINDING_MISMATCH"):
                        self.context.read_compiled(self.pack_id)
                    sql_update(field, record[{"serialized_byte_size":0,"selection_basis_json":1,"source_counts_json":2}[field]])
        finally:
            pass

    def test_content_hash_mismatch_is_denied(self):
        with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
            record_content_hash = conn.execute("SELECT content_hash FROM context_pack_records WHERE pack_ref=?", (self.pack_id,)).fetchone()[0]
        def update_doc_and_record(doc):
            doc["content_hash"] = "f" * 64
        def update_record(doc):
            with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                conn.execute("BEGIN")
                conn.execute("DROP TRIGGER IF EXISTS context_pack_records_no_update")
                conn.execute("UPDATE context_pack_records SET content_hash=? WHERE pack_ref=?", (doc["content_hash"], self.pack_id))
                conn.commit()
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_CONTENT_HASH_MISMATCH"):
            self._tampered_document(update_doc_and_record, update_record=update_record)
        with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
            conn.execute("BEGIN")
            conn.execute("UPDATE context_pack_records SET content_hash=? WHERE pack_ref=?", (record_content_hash, self.pack_id))
            conn.commit()

    def test_duplicate_order_and_embedded_source_integrity_are_denied(self):
        with self.subTest("duplicate ref"), self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_ENTRY_INVALID"):
            self._tampered_document(lambda doc: doc["entries"].__setitem__(1, dict(doc["entries"][0])))
        with self.subTest("wrong order"), self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_ENTRY_ORDER_INVALID"):
            self._tampered_document(lambda doc: doc["entries"].reverse())
        with self.subTest("embedded bytes hash"):
            with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                old_hash = conn.execute("SELECT content_hash FROM context_pack_records WHERE pack_ref=?", (self.pack_id,)).fetchone()[0]
            def mutate_and_hash(doc):
                doc["entries"][0]["content"] = "X" + doc["entries"][0]["content"][1:]
                basis = {key: doc[key] for key in ("task_id", "run_id", "selection_basis", "entries")}
                doc["content_hash"] = hashlib.sha256(_canonical(basis)).hexdigest()
            def update_record(doc):
                with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                    conn.execute("BEGIN")
                    conn.execute("DROP TRIGGER IF EXISTS context_pack_records_no_update")
                    conn.execute("UPDATE context_pack_records SET content_hash=? WHERE pack_ref=?", (doc["content_hash"], self.pack_id))
                    conn.commit()
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_ENTRY_INTEGRITY_MISMATCH"):
                self._tampered_document(mutate_and_hash, update_record=update_record)
            with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
                conn.execute("BEGIN")
                conn.execute("UPDATE context_pack_records SET content_hash=? WHERE pack_ref=?", (old_hash, self.pack_id))
                conn.commit()

    def test_current_source_integrity_and_lifecycle_are_rechecked_without_leaking_content(self):
        original = self.store.get_object_metadata
        def mismatched(ref):
            metadata = original(ref)
            return {**metadata, "integrity_hash": "0" * 64} if ref == self.source_ids[0] else metadata
        with mock.patch.object(self.store, "get_object_metadata", side_effect=mismatched):
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_UNAVAILABLE"):
                self.context.read_compiled(self.pack_id)

        with self.store._connection() as conn:
            conn.execute("UPDATE object_states SET payload_state='PURGED' WHERE object_id=?", (self.source_ids[0],))
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_UNAVAILABLE"):
            self.context.read_compiled(self.pack_id)

    def test_source_classification_must_remain_in_original_run_boundary(self):
        with contextlib.closing(sqlite3.connect(self.store.database_path)) as conn:
            conn.execute("BEGIN")
            conn.execute("DROP TRIGGER classification_assertions_no_update")
            conn.execute("UPDATE classification_assertions SET handling_tags_json='[\"LOCAL_ONLY\"]' WHERE assertion_id=?", ("class-" + self.source_ids[0],))
            conn.commit()
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_OUTSIDE_RUN_BOUNDARY"):
            self.context.read_compiled(self.pack_id)

    def test_purge_lineage_includes_pack_and_unavailable_source_denies_embedded_copy(self):
        purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal)
        with self.store._connection() as conn:
            closure = purge._closure([self.source_ids[0]], conn)
        self.assertIn(self.pack_id, closure)
        with self.store._connection() as conn:
            conn.execute("UPDATE object_states SET payload_state='PURGED' WHERE object_id=?", (self.source_ids[0],))
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_UNAVAILABLE"):
            self.context.read_compiled(self.pack_id)

    def test_active_purge_barrier_blocks_pack_and_source_reads(self):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at) "
                "VALUES('read-barrier','read-plan',0,'ACTIVE','2026-01-01T00:00:00Z')"
            )
            conn.execute("INSERT INTO purge_barrier_refs(barrier_id,object_id) VALUES('read-barrier',?)", (self.pack_id,))
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_NOT_AVAILABLE"):
            self.context.read_compiled(self.pack_id)
        with self.store._connection() as conn:
            conn.execute("DELETE FROM purge_barrier_refs WHERE barrier_id='read-barrier'")
            conn.execute("DELETE FROM purge_barriers WHERE barrier_id='read-barrier'")
            conn.execute(
                "INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at) "
                "VALUES('read-source-barrier','read-plan',0,'ACTIVE','2026-01-01T00:00:00Z')"
            )
            conn.execute("INSERT INTO purge_barrier_refs(barrier_id,object_id) VALUES('read-source-barrier',?)", (self.source_ids[0],))
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_UNAVAILABLE"):
            self.context.read_compiled(self.pack_id)

    def test_read_only_application_and_cli_return_pack_without_mutation(self):
        self.store.close()
        before = _file_commitment(self.root, self.journal.parent)
        app = self._read_only_application()
        try:
            result = app.context_packs.read_compiled(self.pack_id)
            snapshot = app.view_model.snapshot()
            self.assertNotIn("entries", snapshot["context_status"])
            self.assertEqual(snapshot["context_status"]["pack_id"], self.pack_id)
        finally:
            app.close()
        after = _file_commitment(self.root, self.journal.parent)
        self.assertEqual(after, before)
        self.assertFalse((self.root / "nexus.sqlite-wal").exists())
        self.assertFalse((self.root / "nexus.sqlite-shm").exists())
        sidecar = self.journal.with_name(self.journal.name + ".lock")
        self.assertEqual(sidecar.exists(), any("purge.jsonl.lock" in key for key in before))
        self.assertEqual(result["model_visible_exposure"], "UNKNOWN")

        stdout = io.StringIO()
        with mock.patch("adapters.client.__main__._runtime", side_effect=AssertionError("writable runtime was used")), \
             contextlib.redirect_stdout(stdout):
            code = client_main([
                "--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "context", "read", "--pack-ref", self.pack_id,
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertNotIn(str(self.root), stdout.getvalue())

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = client_main([
                "--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "context", "read", "--latest",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(_file_commitment(self.root, self.journal.parent), before)

        stderr = io.StringIO()
        with mock.patch(
            "adapters.panel.application.open_panel_application",
            side_effect=OSError("local diagnostic path " + str(self.root)),
        ), contextlib.redirect_stderr(stderr):
            code = client_main([
                "--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "context", "read", "--latest",
            ])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"status": "DENIED_OR_FAILED", "reason": "OSError"})
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_read_adds_no_governed_records(self):
        table_names = (
            "tasks", "runs", "delegation_grants", "trace_events", "objects",
            "memory_candidates", "memory_records", "effect_records", "context_pack_records",
            "value_metering_records", "run_manifest_inputs",
        )
        def counts():
            with self.store._connection() as conn:
                existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                        for table in table_names if table in existing}
        before = counts()
        self.context.read_compiled(self.pack_id)
        self.context.read_latest_compiled()
        self.assertEqual(counts(), before)


if __name__ == "__main__":
    unittest.main()
