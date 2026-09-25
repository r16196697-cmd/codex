import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from jsonschema import ValidationError

from adapters.storage import ObjectStore
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            rows = conn.execute("SELECT version,name,length(checksum) FROM schema_migrations").fetchall()
            self.assertEqual([(row[0], row[1], row[2]) for row in rows], [(1, "0001_initial.sql", 64), (2, "0002_authority_budget.sql", 64), (3, "0003_trace_state.sql", 64), (4, "0004_runtime.sql", 64), (5, "0005_effect_gate.sql", 64), (6, "0006_memory_purge.sql", 64), (7, "0007_runtime_modes.sql", 64)])

    def test_v6_snapshot_replays_v7_runtime_mode_migration(self) -> None:
        database_path = self.root / "data" / "nexus.sqlite"
        self.store.close()
        legacy_conn = sqlite3.connect(database_path)
        try:
            conn = legacy_conn
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TRIGGER IF EXISTS runtime_mode_events_no_update")
            conn.execute("DROP TRIGGER IF EXISTS runtime_mode_events_no_delete")
            conn.execute("DROP TABLE IF EXISTS runtime_mode_events")
            conn.execute("DROP TABLE IF EXISTS runtime_mode_state")
            conn.execute("DELETE FROM schema_migrations WHERE version=7")
            conn.execute("PRAGMA user_version=6")
            conn.commit()
        finally:
            legacy_conn.close()

        self.store = ObjectStore(self.root / "data")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 7)
            row = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            self.assertEqual(tuple(row), (7, "0007_runtime_modes.sql"))
            self.assertEqual(conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()[0], "NORMAL")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

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
