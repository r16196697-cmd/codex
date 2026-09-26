import hashlib
import json
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from jsonschema import ValidationError

from adapters.storage import ObjectStore
from kernel.purge import PurgeService
from kernel.purge.journal import IndependentPurgeJournal
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime import RuntimeModeService
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            watermark = conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
            self.assertEqual((watermark[0], watermark[1], watermark[2]), (self.store.independent_purge_journal.identity, 0, "0" * 64))
            rows = conn.execute("SELECT version,name,length(checksum) FROM schema_migrations").fetchall()
            self.assertEqual([(row[0], row[1], row[2]) for row in rows][-4:], [(16, "0016_purge_replay_commitments_and_task_provenance.sql", 64), (17, "0017_governed_reference_redaction.sql", 64), (18, "0018_scheduler_setup_request_binding.sql", 64), (19, "0019_independent_purge_journal_watermark.sql", 64)])
            self.assertIn("task_id", {row[1] for row in conn.execute("PRAGMA table_info(purge_plan_records)")})
            kernel = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id='nexus-core-recovery'").fetchone()
            self.assertEqual(tuple(kernel), ("SERVICE", "ACTIVE"))
            self.assertIsNone(conn.execute("SELECT 1 FROM trust_anchors WHERE principal_id='nexus-core-recovery'").fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE principals SET status='REVOKED' WHERE principal_id='nexus-core-recovery'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM principals WHERE principal_id='nexus-core-recovery'")

    def test_v14_database_guards_recovery_identity_from_ordinary_authority_rows(self) -> None:
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            grant_values = ("direct-recovery-grant", None, "test_actor", "nexus-core-recovery", "[]", "[]", "[]", "[]", "2026-09-26T00:00:00Z", "2026-09-27T00:00:00Z", "ACTIVE", "1", None)
            sql = "INSERT INTO delegation_grants(grant_id,parent_grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version,credential_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
            with self.assertRaisesRegex(sqlite3.IntegrityError, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
                conn.execute(sql, grant_values)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
                conn.execute(sql, ("direct-recovery-issued-grant", None, "nexus-core-recovery", "test_actor", "[]", "[]", "[]", "[]", "2026-09-26T00:00:00Z", "2026-09-27T00:00:00Z", "ACTIVE", "1", None))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
                conn.execute("INSERT INTO trust_anchors(anchor_id,principal_id,policy_ref) VALUES('direct-recovery-anchor','nexus-core-recovery','1')")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
                conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('direct-recovery-task','nexus-core-recovery','CREATED','2026-09-26T00:00:00Z','direct-recovery-task-command',NULL)")

    def _historical_database(self, version: int, label: str) -> Path:
        source = Path(__file__).resolve().parents[2] / "migrations"
        target = self.root / (label + "-migrations")
        target.mkdir()
        for migration in sorted(source.glob("*.sql")):
            number = int(migration.name[:4])
            if number <= version:
                shutil.copy2(migration, target / migration.name)
        data_root = self.root / label
        try:
            store = ObjectStore(data_root, migrations_dir=target)
            store.close()
        except sqlite3.OperationalError as exc:
            # v6 predates runtime_mode_state; migration application itself is
            # complete before the old runtime's post-migration mode read.
            if version != 6 or "runtime_mode_state" not in str(exc):
                raise
        return data_root / "nexus.sqlite"

    def _restore_persisted_v13(self) -> Path:
        self.store.close()
        database_path = self._historical_database(13, "persisted-v13")
        with closing(sqlite3.connect(database_path)) as legacy:
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('test_actor','HUMAN','ACTIVE')")
            legacy.commit()
        return database_path

    def test_persisted_v13_database_applies_v14_and_v15_identity_guards(self) -> None:
        self._restore_persisted_v13()
        self.store = ObjectStore(self.root / "data")
        self.addCleanup(self.store.close)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT name FROM schema_migrations WHERE version=14").fetchone()[0], "0014_kernel_recovery_identity_isolation.sql")
            self.assertEqual(conn.execute("SELECT name FROM schema_migrations WHERE version=15").fetchone()[0], "0015_kernel_recovery_identity_preexisting_guard.sql")
            triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertTrue({"kernel_recovery_no_delegation_grant", "kernel_recovery_no_trust_anchor", "kernel_recovery_no_task_requester"}.issubset(triggers))

    def test_persisted_v15_database_upgrades_to_latest_with_fk_integrity(self) -> None:
        self.store.close()
        database_path = self._historical_database(15, "persisted-v15")
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual([row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")], list(range(1,20)))
        finally:
            upgraded.close()

    def test_persisted_v14_database_upgrades_to_latest_with_fk_integrity(self) -> None:
        self.store.close()
        database_path = self._historical_database(14, "persisted-v14")
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual([row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")], list(range(1,20)))
        finally:
            upgraded.close()

    def _assert_v13_contamination_rejected(self, table: str, statement: str) -> None:
        database_path = self._restore_persisted_v13()
        with closing(sqlite3.connect(database_path)) as legacy:
            legacy.execute(statement)
            original = legacy.execute(f"SELECT * FROM {table} WHERE rowid=(SELECT MAX(rowid) FROM {table})").fetchone()
            legacy.commit()
        reopened = None
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                reopened = ObjectStore(database_path.parent)
        finally:
            if reopened is not None:
                reopened.close()
        with closing(sqlite3.connect(database_path)) as unchanged:
            self.assertEqual(unchanged.execute("PRAGMA user_version").fetchone()[0], 14)
            self.assertEqual(unchanged.execute("SELECT name FROM schema_migrations WHERE version=14").fetchone()[0], "0014_kernel_recovery_identity_isolation.sql")
            self.assertIsNone(unchanged.execute("SELECT 1 FROM schema_migrations WHERE version=15").fetchone())
            self.assertEqual(unchanged.execute(f"SELECT * FROM {table} WHERE rowid=(SELECT MAX(rowid) FROM {table})").fetchone(), original)
            self.assertEqual(unchanged.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_v13_recovery_grant_issuer_contamination_fails_closed(self) -> None:
        self._assert_v13_contamination_rejected("delegation_grants", "INSERT INTO delegation_grants(grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version) VALUES('contaminated-grant','nexus-core-recovery','test_actor','[]','[]','[]','[]','2026-09-26T00:00:00Z','2026-09-27T00:00:00Z','ACTIVE','1')")

    def test_v13_recovery_grant_recipient_contamination_fails_closed(self) -> None:
        self._assert_v13_contamination_rejected("delegation_grants", "INSERT INTO delegation_grants(grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version) VALUES('contaminated-grant','test_actor','nexus-core-recovery','[]','[]','[]','[]','2026-09-26T00:00:00Z','2026-09-27T00:00:00Z','ACTIVE','1')")

    def test_v13_recovery_trust_anchor_contamination_fails_closed(self) -> None:
        self._assert_v13_contamination_rejected("trust_anchors", "INSERT INTO trust_anchors(anchor_id,principal_id,policy_ref) VALUES('contaminated-anchor','nexus-core-recovery','1')")

    def test_v13_recovery_task_requester_contamination_fails_closed(self) -> None:
        self._assert_v13_contamination_rejected("tasks", "INSERT INTO tasks(task_id,requester_id,status,created_at,command_id) VALUES('contaminated-task','nexus-core-recovery','CREATED','2026-09-26T00:00:00Z','contaminated-task-command')")

    def test_runtime_mode_lookup_failure_does_not_assume_normal(self) -> None:
        with mock.patch.object(self.store, "_connection", side_effect=sqlite3.OperationalError("mode table unavailable")):
            with self.assertRaisesRegex(MigrationError, "could not be read safely"):
                self.store._require_mode("core_write")

    def test_ordinary_startup_forces_recovery_when_external_journal_is_ahead(self) -> None:
        journal = IndependentPurgeJournal(self.store.independent_purge_journal_path, self.store.data_root)
        journal.append(action="BARRIER_INSTALLED", barrier_id="ahead-barrier", plan_id="ahead-plan", plan_hash="a" * 64, lineage_revision=0, protected_refs=["ahead-object"], task_id="ahead-task")
        self.store.close()
        reopened = ObjectStore(self.root / "data", independent_purge_journal_path=journal.path)
        try:
            self.assertEqual(reopened._current_runtime_mode(), "RECOVERY")
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                reopened.get_object_metadata("ahead-object")
            with reopened._recovery_maintenance():
                with reopened._connection() as conn:
                    self.assertEqual(conn.execute("SELECT sequence FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()[0], 0)
        finally:
            reopened.close()

    def test_ordinary_startup_fails_closed_on_journal_corruption(self) -> None:
        journal = IndependentPurgeJournal(self.store.independent_purge_journal_path, self.store.data_root)
        journal.append(action="BARRIER_INSTALLED", barrier_id="corrupt-barrier", plan_id="corrupt-plan", plan_hash="b" * 64, lineage_revision=0, protected_refs=["corrupt-object"], task_id="corrupt-task")
        journal.path.write_text(journal.path.read_text(encoding="utf-8").replace('"record_hash":"', '"record_hash":"f'), encoding="utf-8")
        self.store.close()
        reopened = ObjectStore(self.root / "data", independent_purge_journal_path=journal.path)
        try:
            self.assertEqual(reopened._current_runtime_mode(), "RECOVERY")
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                reopened.get_object_metadata("corrupt-object")
        finally:
            reopened.close()

    def test_ordinary_startup_fails_closed_on_journal_truncation_or_missing_file(self) -> None:
        journal = IndependentPurgeJournal(self.store.independent_purge_journal_path, self.store.data_root)
        journal.append(action="BARRIER_INSTALLED", barrier_id="truncate-barrier", plan_id="truncate-plan", plan_hash="d" * 64, lineage_revision=0, protected_refs=["truncate-object"], task_id="truncate-task")
        with self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._acknowledge_purge_journal_head(conn, identity=journal.identity, sequence=1, record_hash=journal.verified_head()[1])
            conn.commit()
        journal.path.write_text("", encoding="utf-8")
        self.store.close()
        reopened = ObjectStore(self.root / "data", independent_purge_journal_path=journal.path)
        try:
            self.assertEqual(reopened._current_runtime_mode(), "RECOVERY")
        finally:
            reopened.close()
        journal.path.unlink()
        missing = ObjectStore(self.root / "data", independent_purge_journal_path=journal.path)
        try:
            self.assertEqual(missing._current_runtime_mode(), "RECOVERY")
            purge = PurgeService(missing, None, None, independent_journal_path=journal.path)
            with self.assertRaisesRegex(RuntimeDenied, "PURGE_JOURNAL_MISSING"):
                purge.replay_independent_journal()
        finally:
            missing.close()

    def test_historical_database_with_nonempty_journal_and_no_watermark_forces_recovery(self) -> None:
        self.store.close()
        database_path = self._historical_database(15, "historical-journal")
        data_root = database_path.parent
        journal_path = data_root.parent / (data_root.name + ".purge-journal.jsonl")
        journal = IndependentPurgeJournal(journal_path, data_root)
        journal.append(action="BARRIER_INSTALLED", barrier_id="historical-barrier", plan_id="historical-plan", plan_hash="c" * 64, lineage_revision=0, protected_refs=["historical-object"], task_id="historical-task")
        reopened = ObjectStore(data_root, independent_purge_journal_path=journal_path)
        try:
            self.assertEqual(reopened._current_runtime_mode(), "RECOVERY")
            with reopened._recovery_maintenance():
                with reopened._connection() as conn:
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
                    self.assertIsNone(conn.execute("SELECT 1 FROM independent_purge_journal_watermark WHERE singleton=1").fetchone())
        finally:
            reopened.close()

    def test_v6_snapshot_replays_v7_through_v19_migrations_deterministically(self) -> None:
        self.store.close()
        database_path = self._historical_database(6, "persisted-v6")
        self.store = ObjectStore(database_path.parent)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            row = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            self.assertEqual(tuple(row), (19, "0019_independent_purge_journal_watermark.sql"))
            self.assertEqual(conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()[0], "NORMAL")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_real_v11_legacy_purge_plan_migrates_unbound_and_cannot_execute_or_inspect(self) -> None:
        payload = b"synthetic legacy payload must remain"
        object_id = "v11-legacy-purge-target"
        command_id = "v11-legacy-plan-command"
        plan_id = "v11-legacy-plan"
        created_at = "2026-09-25T00:00:00Z"
        plan = {"schema_id":"nexus.purge_plan","schema_version":1,"plan_id":plan_id,"target_refs":[object_id],
            "descendant_refs":[],"affected_indexes":["raw_history","admitted_memory"],
            "planned_actions":["QUIESCE_RUNS","RECONCILE_EFFECTS","DELETE_PAYLOADS","DELETE_INDEX_ROWS","REDACT_DERIVED_METADATA","VERIFY_UNAVAILABLE"],
            "lineage_revision":0,"created_at":created_at,"policy_version":"1"}
        plan["plan_hash"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        self.store.close()
        database_path = self._historical_database(11, "persisted-v11")
        payload_root = database_path.parent / "objects" / "sha256"
        digest = hashlib.sha256(payload).hexdigest()
        payload_path = payload_root / digest[:2] / digest
        payload_path.parent.mkdir(parents=True)
        payload_path.write_bytes(payload)
        legacy = sqlite3.connect(database_path)
        try:
            legacy.execute("INSERT INTO objects(object_id) VALUES(?)", (object_id,))
            legacy.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES(?,'CURRENT','ACTIVE','VALID','AVAILABLE')", (object_id,))
            legacy.execute("INSERT INTO object_envelopes(object_id,object_type,schema_id,schema_version,payload_uri,hash_profile_ref,hash_profile_version,integrity_hash,semantic_hash,created_by_run,classification_assertion_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (object_id,"artifact","nexus.artifact",1,f"objects/sha256/{digest[:2]}/{digest}","raw-sha256",1,digest,None,"legacy-run","legacy-class","2026-09-25T00:00:00Z"))
            legacy.execute("INSERT INTO command_ledger(command_id,operation,request_hash,result_json,status,created_at) VALUES(?,?,?,?,?,?)",
                (command_id,"create_purge_plan","a"*64,json.dumps({"plan_id":plan_id,"plan_hash":plan["plan_hash"]},sort_keys=True,separators=(",",":")),"SUCCEEDED",created_at))
            legacy.execute("INSERT INTO purge_plan_records(plan_id,plan_hash,lineage_revision,plan_json,command_id,created_at) VALUES(?,?,?,?,?,?)",
                (plan_id,plan["plan_hash"],plan["lineage_revision"],json.dumps(plan,sort_keys=True,separators=(",",":")),command_id,created_at))
            legacy.commit()
        finally:
            legacy.close()
        self.store = ObjectStore(database_path.parent)
        self.addCleanup(self.store.close)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            migrated = conn.execute("SELECT task_id,plan_json FROM purge_plan_records WHERE plan_id=?", (plan_id,)).fetchone()
            self.assertIsNone(migrated["task_id"])
            migrated_plan = json.loads(migrated["plan_json"])
            self.assertEqual(migrated_plan, plan)
            self.assertNotIn("task_id", migrated_plan)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0], 0)
        purge = PurgeService(self.store, None, None, independent_journal_path=self.store.independent_purge_journal_path)
        if self.store._current_runtime_mode() == "RECOVERY":
            RuntimeModeService(self.store, None).complete_validated_recovery(command_id="legacy-recovery-complete", purge_service=purge)
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
