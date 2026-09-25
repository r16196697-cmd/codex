import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import ValidationError

from adapters.storage import ObjectStore
from kernel.purge import PurgeService
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.inspect import InspectService
from kernel.object.errors import (
    CommandConflict,
    ConcurrentModification,
    IntegrityMismatch,
    LineageCycle,
    MigrationError,
    ObjectNotFound,
    PurgeBarrierActive,
    SchemaUnsupported,
    WriterAlreadyRunning,
)


class ObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step2-")
        self.root = Path(self.temp.name)
        self.store = ObjectStore(self.root / "data")
        self.addCleanup(self.store.close)
        with self.store._connection() as conn:
            conn.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('test_actor','HUMAN','ACTIVE')")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def put(self, command_id: str, data: bytes) -> str:
        object_id = "obj-" + command_id
        classification_ref = self._classify(object_id)
        return self.store.put_object(
            command_id=command_id,
            object_id=object_id,
            payload=data,
            object_type="artifact",
            created_by_run="run_test",
            classification_assertion_ref=classification_ref,
        )

    def _classify(self, object_id: str, level: str = "PUBLIC", tags=()) -> str:
        assertion_id = "class-" + object_id
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?,?,?,?,?,?,?,?)",
                (assertion_id, "OBJECT", object_id, level, json.dumps(list(tags)), "1", "isolated fixture", "test_actor"),
            )
        return assertion_id

    def test_payload_is_content_addressed_verified_and_idempotent(self) -> None:
        payload = b"artifact bytes\x00\xff"
        object_id = self.put("cmd-put-1", payload)
        self.assertEqual(self.store.put_object(command_id="cmd-put-1", object_id="obj-cmd-put-1", payload=payload, object_type="artifact", created_by_run="run_test", classification_assertion_ref="class-obj-cmd-put-1"), object_id)
        self.assertEqual(self.store.get_payload(object_id), payload)
        metadata = self.store.get_object_metadata(object_id)
        self.assertEqual(metadata["integrity_hash"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(metadata["payload_uri"].startswith("objects/sha256/"))
        self.assertNotIn("payload", metadata)
        with self.assertRaises(CommandConflict):
            self.put("cmd-put-1", b"different request")

    def test_byte_tampering_and_missing_payload_never_verify(self) -> None:
        object_id = self.put("cmd-put-2", b"immutable payload")
        path = self.store._payload_path(self.store.get_object_metadata(object_id)["payload_uri"])
        path.write_bytes(b"tampered payload")
        with self.assertRaises(IntegrityMismatch):
            self.store.verify_object(object_id)
        path.unlink()
        with self.assertRaises(IntegrityMismatch):
            self.store.get_payload(object_id)

    def test_crash_after_atomic_payload_rename_before_db_commit_cleans_orphan_on_reopen(self) -> None:
        payload = b"synthetic crash-window payload"
        object_id = "obj-crash-window"
        classification_ref = self._classify(object_id)
        digest = hashlib.sha256(payload).hexdigest()
        payload_path = self.root / "data" / "objects" / "sha256" / digest[:2] / digest
        with self.store._connection() as conn:
            conn.execute(
                "CREATE TRIGGER simulate_metadata_commit_crash BEFORE INSERT ON objects "
                "WHEN NEW.object_id='obj-crash-window' BEGIN SELECT RAISE(ABORT,'SIMULATED_COMMIT_CRASH'); END"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "SIMULATED_COMMIT_CRASH"):
            self.store.put_object(command_id="cmd-crash-window", object_id=object_id, payload=payload, object_type="artifact", created_by_run="run_test", classification_assertion_ref=classification_ref)
        self.assertTrue(payload_path.is_file(), "atomic rename occurred before the simulated metadata commit failure")
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='cmd-crash-window'").fetchone())

        self.store.close()
        self.store = ObjectStore(self.root / "data")
        self.assertFalse(payload_path.exists(), "startup recovery must remove the unreferenced payload")
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone())
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_object_envelope_is_immutable_in_sqlite(self) -> None:
        object_id = self.put("cmd-immutable", b"immutable")
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE object_envelopes SET object_type='memory' WHERE object_id=?", (object_id,))
            self.assertEqual(conn.execute("SELECT object_type FROM object_envelopes WHERE object_id=?", (object_id,)).fetchone()[0], "artifact")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE hash_profiles SET hash_algorithm='SHA-1' WHERE profile_id='raw-sha256'")

    def test_lineage_self_and_indirect_cycles_reject_transactionally(self) -> None:
        a = self.put("cmd-a", b"a")
        b = self.put("cmd-b", b"b")
        c = self.put("cmd-c", b"c")
        self.store.add_relation(command_id="cmd-rel-ab", from_id=a, relation_type="derived_from", to_id=b)
        self.store.add_relation(command_id="cmd-rel-bc", from_id=b, relation_type="generated_from", to_id=c)
        before = self._relation_count()
        with self.assertRaises(LineageCycle):
            self.store.add_relation(command_id="cmd-rel-ca", from_id=c, relation_type="supersedes", to_id=a)
        with self.assertRaises(LineageCycle):
            self.store.add_relation(command_id="cmd-rel-aa", from_id=a, relation_type="derived_from", to_id=a)
        self.assertEqual(self._relation_count(), before)
        self.assertEqual(set(self.store.get_lineage(a)["sources"]), {b, c})
        self.assertEqual(set(self.store.get_lineage(c)["derived"]), {a, b})

    def test_cas_has_one_winner_and_command_retry_replays(self) -> None:
        first = self.put("cmd-ref-a", b"first")
        second = self.put("cmd-ref-b", b"second")
        third = self.put("cmd-ref-c", b"third")
        self.assertEqual(self.store.create_logical_ref(command_id="cmd-logical", ref_id="ref_1", ref_type="artifact", object_id=first, updated_by_run="run_test"), 1)
        start = threading.Barrier(3)
        results: list[tuple[str, object]] = []

        def update(command_id: str, obj: str) -> None:
            start.wait()
            try:
                results.append(("PASS", self.store.compare_and_swap_ref(command_id=command_id, ref_id="ref_1", expected_revision=1, new_object_id=obj, updated_by_run="run_test")))
            except ConcurrentModification:
                results.append(("CONFLICT", None))

        threads = [threading.Thread(target=update, args=("cmd-cas-1", second)), threading.Thread(target=update, args=("cmd-cas-2", third))]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(result[0] for result in results), ["CONFLICT", "PASS"])
        winner = next(thread for thread in ("cmd-cas-1", "cmd-cas-2") if self._command_exists(thread))
        self.assertEqual(self.store.compare_and_swap_ref(command_id=winner, ref_id="ref_1", expected_revision=1, new_object_id=second if winner == "cmd-cas-1" else third, updated_by_run="run_test"), 2)
        with self.assertRaises(ConcurrentModification):
            self.store.compare_and_swap_ref(command_id="cmd-cas-stale", ref_id="ref_1", expected_revision=1, new_object_id=first, updated_by_run="run_test")

    def test_missing_lineage_source_is_rejected_before_payload_write(self) -> None:
        with self.assertRaises(ObjectNotFound):
            classification_ref = self._classify("obj-cmd-orphan")
            self.store.put_object(command_id="cmd-orphan", object_id="obj-cmd-orphan", payload=b"orphan payload", object_type="artifact", created_by_run="run_test", classification_assertion_ref=classification_ref, derived_from=("missing_object",))
        payload_hash = hashlib.sha256(b"orphan payload").hexdigest()
        orphan = self.root / "data" / "objects" / "sha256" / payload_hash[:2] / payload_hash
        self.assertFalse(orphan.exists())
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-orphan'").fetchone()[0], 0)

    def test_invalid_object_schema_writes_no_payload_or_metadata(self) -> None:
        payload = b"must not be persisted"
        blob_root = self.root / "data" / "objects" / "sha256"
        before_files = {path for path in blob_root.rglob("*") if path.is_file()}
        with self.assertRaises(ValidationError):
            self.store.put_object(
                command_id="cmd-invalid-schema",
                object_id="obj-cmd-invalid-schema",
                payload=payload,
                object_type="not-a-contract-object-type",
                created_by_run="run_test",
                classification_assertion_ref="class_test",
            )
        after_files = {path for path in blob_root.rglob("*") if path.is_file()}
        self.assertEqual(after_files, before_files)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-invalid-schema'").fetchone()[0], 0)

    def test_purge_barrier_persists_and_blocks_derived_writes(self) -> None:
        protected = self.put("cmd-protected", b"to be purged")
        self.store.install_purge_barrier(command_id="cmd-barrier", barrier_id="barrier_1", plan_id="plan_1", protected_refs=[protected], lineage_revision=1)
        self.store.close()
        reopened = ObjectStore(self.root / "data")
        self.store = reopened
        with reopened._connection() as conn:
            status = conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier_1'").fetchone()[0]
        self.assertEqual(status, "ACTIVE")
        with self.assertRaises(PurgeBarrierActive):
            classification_ref = self._classify("obj-cmd-derived-blocked")
            reopened.put_object(command_id="cmd-derived-blocked", object_id="obj-cmd-derived-blocked", payload=b"derived", object_type="artifact", created_by_run="run_test", classification_assertion_ref=classification_ref, derived_from=(protected,))
        with self.assertRaises(PurgeBarrierActive):
            reopened.add_relation(command_id="cmd-relation-blocked", from_id=protected, relation_type="supports", to_id=protected)

    def test_derived_object_cannot_lower_source_classification(self) -> None:
        source_id = "obj-source-secret"
        source_classification = self._classify(source_id, "SECRET", ["NO_EXTERNAL_EGRESS"])
        self.store.put_object(command_id="cmd-source-secret", object_id=source_id, payload=b"secret source", object_type="evidence", created_by_run="run_test", classification_assertion_ref=source_classification)
        derived_id = "obj-derived-public"
        derived_classification = self._classify(derived_id, "PUBLIC", [])
        with self.assertRaises(IntegrityMismatch):
            self.store.put_object(command_id="cmd-derived-public", object_id=derived_id, payload=b"derived", object_type="artifact", created_by_run="run_test", classification_assertion_ref=derived_classification, derived_from=(source_id,))
        payload_hash = hashlib.sha256(b"derived").hexdigest()
        self.assertFalse((self.root / "data" / "objects" / "sha256" / payload_hash[:2] / payload_hash).exists())

    def test_only_one_writer_process_can_open_a_data_root(self) -> None:
        code = """
import sys
from adapters.storage import ObjectStore
from kernel.object.errors import WriterAlreadyRunning
try:
    store = ObjectStore(sys.argv[1])
except WriterAlreadyRunning:
    raise SystemExit(23)
store.close()
raise SystemExit(0)
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.root / "data")], capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 23, child.stderr)

    def test_migrations_are_recorded_and_sqlite_is_consistent(self) -> None:
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            rows = conn.execute("SELECT version,name,length(checksum) FROM schema_migrations").fetchall()
            self.assertEqual([(row[0], row[1], row[2]) for row in rows], [(1, "0001_initial.sql", 64), (2, "0002_authority_budget.sql", 64), (3, "0003_trace_state.sql", 64), (4, "0004_runtime.sql", 64), (5, "0005_effect_gate.sql", 64), (6, "0006_memory_purge.sql", 64), (7, "0007_runtime_modes.sql", 64), (8, "0008_purge_identifier_redaction.sql", 64), (9, "0009_purge_trace_state_facts.sql", 64), (10, "0010_purge_run_manifest_indexes.sql", 64), (11, "0011_subtask_attempts.sql", 64), (12, "0012_purge_plan_task_binding.sql", 64), (13, "0013_kernel_recovery_principal.sql", 64)])
            self.assertIn("task_id", {row[1] for row in conn.execute("PRAGMA table_info(purge_plan_records)")})
            kernel = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id='nexus-core-recovery'").fetchone()
            self.assertEqual(tuple(kernel), ("SERVICE", "ACTIVE"))
            self.assertIsNone(conn.execute("SELECT 1 FROM trust_anchors WHERE principal_id='nexus-core-recovery'").fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE principals SET status='REVOKED' WHERE principal_id='nexus-core-recovery'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM principals WHERE principal_id='nexus-core-recovery'")

    def test_runtime_mode_lookup_failure_does_not_assume_normal(self) -> None:
        with mock.patch.object(self.store, "_connection", side_effect=sqlite3.OperationalError("mode table unavailable")):
            with self.assertRaisesRegex(MigrationError, "could not be read safely"):
                self.store._require_mode("core_write")

    def test_v6_snapshot_replays_v7_through_v13_migrations_deterministically(self) -> None:
        database_path = self.root / "data" / "nexus.sqlite"
        self.store.close()
        legacy_conn = sqlite3.connect(database_path)
        try:
            conn = legacy_conn
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP INDEX IF EXISTS purge_plan_records_task_idx")
            conn.execute("ALTER TABLE purge_plan_records DROP COLUMN task_id")
            conn.execute("DROP TRIGGER IF EXISTS subtask_attempts_identity_immutable")
            conn.execute("DROP TRIGGER IF EXISTS subtask_attempts_no_delete")
            conn.execute("DROP TRIGGER IF EXISTS subtasks_identity_immutable")
            conn.execute("DROP TABLE IF EXISTS subtask_attempts")
            conn.execute("ALTER TABLE subtasks DROP COLUMN finalized_at")
            conn.execute("ALTER TABLE subtasks DROP COLUMN final_outcome")
            conn.execute("ALTER TABLE subtasks DROP COLUMN final_attempt_id")
            conn.executescript("""CREATE TRIGGER subtasks_identity_immutable BEFORE UPDATE ON subtasks
                WHEN NEW.subtask_id<>OLD.subtask_id OR NEW.task_id<>OLD.task_id OR NEW.node_index<>OLD.node_index
                  OR NEW.node_json<>OLD.node_json OR NEW.command_id<>OLD.command_id OR NEW.created_at<>OLD.created_at
                  OR (NEW.scheduled_run_id IS NOT OLD.scheduled_run_id AND NOT (OLD.status='PENDING' AND OLD.scheduled_run_id IS NULL AND NEW.scheduled_run_id IS NOT NULL AND NEW.status IN ('PENDING','READY')))
                  OR NOT (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('READY','CANCELLED','STALE'))
                    OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED','STALE'))
                    OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
                    OR (OLD.status='WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED')))
                BEGIN SELECT RAISE(ABORT,'INVALID_SUBTASK_TRANSITION'); END;""")
            conn.execute("DROP TRIGGER IF EXISTS runtime_mode_events_no_update")
            conn.execute("DROP TRIGGER IF EXISTS runtime_mode_events_no_delete")
            conn.execute("DROP TABLE IF EXISTS runtime_mode_events")
            conn.execute("DROP TABLE IF EXISTS runtime_mode_state")
            conn.execute("DROP TRIGGER IF EXISTS approval_decisions_no_update")
            conn.execute("DROP TRIGGER IF EXISTS effects_identity_immutable")
            conn.execute("DROP TRIGGER IF EXISTS trace_events_no_update")
            conn.execute("DROP TRIGGER IF EXISTS purge_refs_no_update")
            conn.executescript("""
                CREATE TRIGGER approval_decisions_no_update BEFORE UPDATE ON approval_decisions BEGIN SELECT RAISE(ABORT,'APPROVAL_DECISION_IMMUTABLE'); END;
                CREATE TRIGGER effects_identity_immutable BEFORE UPDATE ON effects
                WHEN NEW.effect_id<>OLD.effect_id OR NEW.run_id<>OLD.run_id OR NEW.tool_id<>OLD.tool_id
                 OR NEW.tool_descriptor_version<>OLD.tool_descriptor_version OR NEW.action_type<>OLD.action_type
                 OR NEW.target_ref<>OLD.target_ref OR NEW.payload_integrity_hash<>OLD.payload_integrity_hash
                 OR NEW.payload_object_ref<>OLD.payload_object_ref OR NEW.idempotency_key<>OLD.idempotency_key
                 OR NEW.grant_id<>OLD.grant_id OR NEW.approval_ref IS NOT OLD.approval_ref
                 OR NEW.budget_reservation_ref<>OLD.budget_reservation_ref OR NEW.created_at<>OLD.created_at
                 OR (OLD.effect_outcome IN ('COMMITTED','NOT_COMMITTED') AND NEW.effect_outcome<>OLD.effect_outcome)
                 OR NOT ((OLD.execution_state='DECLARED' AND NEW.execution_state IN ('PREPARED','CANCELLED'))
                   OR (OLD.execution_state='PREPARED' AND NEW.execution_state IN ('AUTHORIZED','CANCELLED'))
                   OR (OLD.execution_state='AUTHORIZED' AND NEW.execution_state IN ('COMMITTING','CANCELLED'))
                   OR (OLD.execution_state='COMMITTING' AND NEW.execution_state='FINISHED')
                   OR (OLD.execution_state='FINISHED' AND NEW.execution_state='FINISHED'))
                BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;
                CREATE TRIGGER trace_events_no_update BEFORE UPDATE ON trace_events BEGIN SELECT RAISE(ABORT,'TRACE_EVENT_IMMUTABLE'); END;
                CREATE TRIGGER purge_refs_no_update BEFORE UPDATE ON purge_execution_refs BEGIN SELECT RAISE(ABORT,'PURGE_EXECUTION_REF_IMMUTABLE'); END;
            """)
            conn.execute("DROP TRIGGER IF EXISTS kernel_recovery_principal_no_update")
            conn.execute("DROP TRIGGER IF EXISTS kernel_recovery_principal_no_delete")
            conn.execute("DELETE FROM principals WHERE principal_id='nexus-core-recovery'")
            conn.execute("DELETE FROM schema_migrations WHERE version>=7")
            conn.execute("PRAGMA user_version=6")
            conn.commit()
        finally:
            legacy_conn.close()

        self.store = ObjectStore(self.root / "data")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 13)
            row = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            self.assertEqual(tuple(row), (13, "0013_kernel_recovery_principal.sql"))
            self.assertEqual(conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()[0], "NORMAL")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_real_v11_legacy_purge_plan_migrates_unbound_and_cannot_execute_or_inspect(self) -> None:
        object_id = self.put("v11-legacy-purge-target", b"synthetic legacy payload must remain")
        command_id = "v11-legacy-plan-command"
        plan_id = "v11-legacy-plan"
        created_at = "2026-09-25T00:00:00Z"
        plan = {"schema_id":"nexus.purge_plan","schema_version":1,"plan_id":plan_id,"target_refs":[object_id],
            "descendant_refs":[],"affected_indexes":["raw_history","admitted_memory"],
            "planned_actions":["QUIESCE_RUNS","RECONCILE_EFFECTS","DELETE_PAYLOADS","DELETE_INDEX_ROWS","REDACT_DERIVED_METADATA","VERIFY_UNAVAILABLE"],
            "lineage_revision":0,"created_at":created_at,"policy_version":"1"}
        plan["plan_hash"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.store._validate("nexus.purge_plan@1.schema.json", plan)
        with self.store._connection() as conn:
            digest = self.store._request_hash("create_purge_plan", {"legacy_fixture":plan_id})
            self.store._record_command(conn, command_id, "create_purge_plan", digest, {"plan_id":plan_id,"plan_hash":plan["plan_hash"]})
            conn.execute("INSERT INTO purge_plan_records(plan_id,plan_hash,lineage_revision,plan_json,command_id,created_at,task_id) VALUES(?,?,?,?,?,?,NULL)",
                (plan_id,plan["plan_hash"],plan["lineage_revision"],json.dumps(plan,sort_keys=True,separators=(",",":")),command_id,created_at))
        database_path = self.store.database_path
        self.store.close()
        legacy = sqlite3.connect(database_path)
        try:
            legacy.execute("PRAGMA foreign_keys=OFF")
            legacy.execute("DROP INDEX purge_plan_records_task_idx")
            legacy.execute("ALTER TABLE purge_plan_records DROP COLUMN task_id")
            legacy.execute("DROP TRIGGER IF EXISTS kernel_recovery_principal_no_update")
            legacy.execute("DROP TRIGGER IF EXISTS kernel_recovery_principal_no_delete")
            legacy.execute("DELETE FROM principals WHERE principal_id='nexus-core-recovery'")
            legacy.execute("DELETE FROM schema_migrations WHERE version>=12")
            legacy.execute("PRAGMA user_version=11")
            legacy.commit()
        finally:
            legacy.close()
        self.store = ObjectStore(self.root / "data")
        self.addCleanup(self.store.close)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            migrated = conn.execute("SELECT task_id,plan_json FROM purge_plan_records WHERE plan_id=?", (plan_id,)).fetchone()
            self.assertIsNone(migrated["task_id"])
            migrated_plan = json.loads(migrated["plan_json"])
            self.assertEqual(migrated_plan, plan)
            self.assertNotIn("task_id", migrated_plan)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0], 0)
        purge = PurgeService(self.store, None, None, independent_journal_path=self.root / "independent" / "legacy-purge.jsonl")
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_PLAN_TASK_UNBOUND_LEGACY"):
            purge.execute(command_id="legacy-execute", record_id="legacy-record", barrier_id="legacy-barrier", plan=plan, grant_id="legacy-grant", task_id="task-legacy", approval_id="legacy-approval")
        inspector = InspectService(self.store, None)
        with mock.patch.object(inspector, "_authorize"), mock.patch.object(inspector, "_task_context"):
            with self.assertRaisesRegex(RuntimeDenied, "PURGE_PLAN_TASK_UNBOUND_LEGACY"):
                inspector.purge(grant_id="legacy-inspect", task_id="task-legacy", plan_id=plan_id)
        self.assertEqual(self.store.get_payload(object_id), b"synthetic legacy payload must remain")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0], 0)

    def test_changed_applied_migration_is_rejected(self) -> None:
        with self.store._connection() as conn:
            conn.execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
        self.store.close()
        with self.assertRaises(MigrationError):
            ObjectStore(self.root / "data")

    def test_unknown_schema_version_is_rejected_as_unsupported(self) -> None:
        with self.assertRaisesRegex(SchemaUnsupported, "SCHEMA_UNSUPPORTED"):
            self.store._validate("nexus.run_manifest@99.schema.json", {"schema_id": "nexus.run_manifest", "schema_version": 99})

    def test_command_and_purge_ledgers_are_schema_validated_and_idempotent(self) -> None:
        protected = self.put("cmd-ledger-object", b"ledger test")
        self.store.install_purge_barrier(command_id="cmd-ledger-barrier", barrier_id="barrier_ledger", plan_id="plan_ledger", protected_refs=[protected], lineage_revision=0)
        self.store.install_purge_barrier(command_id="cmd-ledger-barrier", barrier_id="barrier_ledger", plan_id="plan_ledger", protected_refs=[protected], lineage_revision=0)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0], 1)

    def _relation_count(self) -> int:
        with self.store._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM object_relations").fetchone()[0]

    def _command_exists(self, command_id: str) -> bool:
        with self.store._connection() as conn:
            return conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (command_id,)).fetchone() is not None


if __name__ == "__main__":
    unittest.main()
