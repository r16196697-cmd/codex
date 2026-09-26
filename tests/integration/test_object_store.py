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
from datetime import datetime, timedelta, timezone

from jsonschema import ValidationError

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.purge import PurgeService
from kernel.purge.journal import IndependentPurgeJournal
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            watermark = conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
            self.assertEqual((watermark[0], watermark[1], watermark[2]), (self.store.independent_purge_journal.identity, 0, "0" * 64))
            rows = conn.execute("SELECT version,name,length(checksum) FROM schema_migrations").fetchall()
            self.assertEqual([(row[0], row[1], row[2]) for row in rows][-7:], [(16, "0016_purge_replay_commitments_and_task_provenance.sql", 64), (17, "0017_governed_reference_redaction.sql", 64), (18, "0018_scheduler_setup_request_binding.sql", 64), (19, "0019_independent_purge_journal_watermark.sql", 64), (20, "0020_known_schema_purge_guards.sql", 64), (21, "0021_canonical_purge_resource_redaction.sql", 64), (22, "0022_effect_receipt_purge_redaction.sql", 64)])
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
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
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual([row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")], list(range(1,23)))
        finally:
            upgraded.close()

    def test_persisted_v19_database_upgrades_to_latest_with_fk_integrity(self) -> None:
        self.store.close()
        database_path = self._historical_database(19, "persisted-v19")
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                row = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
                self.assertEqual(tuple(row), (22, "0022_effect_receipt_purge_redaction.sql"))
        finally:
            upgraded.close()

    def test_pre_v20_task_contract_and_manifest_put_commands_replay_after_v19_upgrade(self) -> None:
        """A v19 committed inner put survives the v20 known-ref hash change."""
        self.store.close()
        database_path = self._historical_database(19, "v19-put-object-replay")
        data_root = database_path.parent
        now = "2026-09-26T00:00:00+00:00"
        input_id = "v19-replay-input"
        contract_id = "v19-replay-contract"
        manifest_id = "v19-replay-manifest"
        contract_command = "v19-bind-contract-object"
        manifest_command = "v19-bind-manifest-object"
        input_payload = b"v19 replay input"
        contract = {
            "schema_id":"nexus.task_contract", "schema_version":1, "task_id":"v19-task",
            "requester_id":"v19-human", "goal":"replay exact committed put", "constraints":[],
            "input_object_refs":[input_id], "open_questions":[], "success_criteria":["preserve the commit"],
            "risk_class":"LOW", "budget_account_ref":"v19-budget",
            "routing_constraints":{"allowed_providers":[],"forbidden_providers":[],"locality":"ANY","network_required":False,"modalities":[]},
            "routing_preferences":{"optimize_for":"BALANCED"}, "created_at":now,
        }
        manifest = {
            "schema_id":"nexus.run_manifest", "schema_version":1, "executor_kind":"ORCHESTRATOR",
            "runtime_version":"v19", "policy_version":"1", "schema_versions":{"nexus.run_manifest":1},
            "input_object_refs":[input_id], "authority_grant_ref":"v19-grant", "budget_reservation_ref":"v19-reservation",
            "data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},
            "classification_assertion_ref":"v19-run-class", "task_contract_ref":contract_id,
            "dag_version":"v1", "scheduler_version":"v1",
        }

        def seed_object(conn, object_id, object_type, payload, run_id, class_id):
            digest = hashlib.sha256(payload).hexdigest()
            relative = f"objects/sha256/{digest[:2]}/{digest}"
            path = data_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?, 'OBJECT', ?,'PUBLIC','[]','1','v19 fixture','v19-actor')", (class_id, object_id))
            conn.execute("INSERT INTO objects(object_id) VALUES(?)", (object_id,))
            conn.execute("INSERT INTO object_envelopes(object_id,object_type,schema_id,schema_version,payload_uri,hash_profile_ref,hash_profile_version,integrity_hash,semantic_hash,created_by_run,classification_assertion_ref,created_at) VALUES(?,?, 'nexus.object',1,?,'raw-sha256',1,?,NULL,?,?,?)", (object_id, object_type, relative, digest, run_id, class_id, now))
            conn.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES(?,NULL,'ACTIVE','VALID','AVAILABLE')", (object_id,))
            return digest

        with closing(sqlite3.connect(database_path)) as legacy:
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('v19-actor','HUMAN','ACTIVE')")
            # Run rows are only needed by the manifest object. Foreign keys are
            # disabled on this offline fixture writer, while the upgraded
            # runtime still validates all object/command projections.
            legacy.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('v19-run-class','RUN','v19-child','PUBLIC','[]','1','v19 fixture','v19-actor')")
            legacy.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('v19-child','v19-task',NULL,'v19-root','MODEL','CREATED','v19-grant',NULL,'v19-reservation',?,'v19-run-class',?)", (json.dumps(manifest["data_boundary"],sort_keys=True,separators=(",",":")), now))
            seed_object(legacy, input_id, "artifact", input_payload, "v19-root", "v19-input-class")
            contract_payload = json.dumps(contract,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
            contract_digest = seed_object(legacy, contract_id, "task_contract", contract_payload, "v19-root", "v19-contract-class")
            manifest_payload = json.dumps(manifest,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
            manifest_digest = seed_object(legacy, manifest_id, "run_manifest", manifest_payload, "v19-child", "v19-manifest-class")
            legacy.execute("INSERT INTO object_relations(from_id,relation_type,to_id) VALUES(?, 'derived_from', ?)", (manifest_id, input_id))
            for command_id, object_id, digest, object_type, run_id, class_id, payload, sources in (
                (contract_command, contract_id, contract_digest, "task_contract", "v19-root", "v19-contract-class", contract_payload, []),
                (manifest_command, manifest_id, manifest_digest, "run_manifest", "v19-child", "v19-manifest-class", manifest_payload, [input_id]),
            ):
                request = {"payload_integrity_hash":digest,"object_id":object_id,"object_type":object_type,
                    "created_by_run":run_id,"classification_assertion_ref":class_id,"derived_from":sources}
                request_hash = self.store._request_hash("put_object", request)
                result = json.dumps({"object_id":object_id},sort_keys=True,separators=(",",":"))
                legacy.execute("INSERT INTO command_ledger(command_id,operation,request_hash,result_json,status,created_at,result_commitment,result_state) VALUES(?,'put_object',?,?, 'SUCCEEDED',?,?,'LIVE')", (command_id,request_hash,result,now,hashlib.sha256(result.encode()).hexdigest()))
            legacy.commit()

        upgraded = ObjectStore(data_root)
        try:
            self.assertEqual(upgraded.put_object(command_id=contract_command,object_id=contract_id,payload=contract_payload,
                object_type="task_contract",created_by_run="v19-root",classification_assertion_ref="v19-contract-class"), contract_id)
            self.assertEqual(upgraded.put_object(command_id=manifest_command,object_id=manifest_id,payload=manifest_payload,
                object_type="run_manifest",created_by_run="v19-child",classification_assertion_ref="v19-manifest-class"), manifest_id)
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id IN (?,?)",(contract_id,manifest_id)).fetchone()[0],2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM object_relations WHERE from_id=? AND relation_type='derived_from'",(manifest_id,)).fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id IN (?,?)",(contract_command,manifest_command)).fetchone()[0],2)
                contract_hash = conn.execute("SELECT request_hash FROM command_ledger WHERE command_id=?",(contract_command,)).fetchone()[0]
                changed = {**contract,"goal":"changed payload"}
                with self.assertRaises(CommandConflict):
                    upgraded.put_object(command_id=contract_command,object_id=contract_id,payload=json.dumps(changed,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(),
                        object_type="task_contract",created_by_run="v19-root",classification_assertion_ref="v19-contract-class")
                with self.assertRaises(CommandConflict):
                    upgraded.put_object(command_id=contract_command,object_id="different-object-id",payload=contract_payload,
                        object_type="task_contract",created_by_run="v19-root",classification_assertion_ref="v19-contract-class")
                self.assertEqual(conn.execute("SELECT request_hash FROM command_ledger WHERE command_id=?",(contract_command,)).fetchone()[0], contract_hash)
                self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='v19-bind-contract-outer' ").fetchone())
        finally:
            upgraded.close()

    def test_v19_inner_task_contract_put_commit_with_missing_outer_bind_replays_after_upgrade(self) -> None:
        self.store.close()
        database_path = self._historical_database(19, "v19-partial-task-contract-bind")
        data_root = database_path.parent
        migration_dir = data_root.parent / "v19-partial-task-contract-bind-migrations"
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        policy["trust_anchors"] = ["human-root"]
        old_store = ObjectStore(data_root, migrations_dir=migration_dir)
        now = datetime.now(timezone.utc)
        issued = now.isoformat()
        expires = (now + timedelta(days=30)).isoformat()
        try:
            authority = AuthorityService(old_store, policy)
            budget = BudgetService(old_store)
            trace = TraceRuntime(old_store, authority)
            runtime = DeterministicRuntime(old_store, authority, budget, trace)
            for principal_id, principal_type in (("human-root","HUMAN"),("agent","SERVICE")):
                authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":principal_id,"principal_type":principal_type,"status":"ACTIVE"}, "v19-principal-"+principal_id)
            authority.register_trust_anchor({"schema_id":"nexus.trust_anchor","schema_version":1,"anchor_id":"v19-anchor","principal_id":"human-root","policy_ref":"1"}, "v19-anchor-command")
            authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"v19-root-grant","issued_by":"human-root","granted_to":"agent","task_scope":["v19-task"],"resource_scope":["v19-root"],"action_scope":["RUN_CREATE","RUN_TRANSITION","TRACE_APPEND","OBJECT_WRITE"],"audience_scope":["nexus-runtime"],"issued_at":issued,"expires_at":expires,"status":"ACTIVE","policy_version":"1"}, "v19-root-grant-command")
            task = {"schema_id":"nexus.task","schema_version":1,"task_id":"v19-task","requester_id":"human-root","status":"CREATED","created_at":issued,"command_id":"v19-task-command"}
            trace.create_task(task)
            budget.create_account(command_id="v19-budget-command",account_id="v19-budget",task_id="v19-task",amount_limit=10,unit="credits",model_call_limit=0,tool_call_limit=0,child_run_limit=0)
            boundary = {"allowed_classifications":["PUBLIC"],"handling_tags":[]}
            with old_store._connection() as conn:
                conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('v19-root-run-class','RUN','v19-root','PUBLIC','[]','1','fixture','agent')")
                conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('v19-root-create-class','TRACE_EVENT','evt-v19-root-create','PUBLIC','[]','1','fixture','agent')")
            run = {"schema_id":"nexus.run","schema_version":1,"run_id":"v19-root","task_id":"v19-task","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"v19-root-grant","data_boundary":boundary,"classification_assertion_ref":"v19-root-run-class","created_at":issued}
            trace.create_run(run,command_id="v19-root-create",event_classification_assertion_ref="v19-root-create-class")
            with old_store._connection() as conn:
                conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('v19-input-class','OBJECT','v19-replay-input','PUBLIC','[]','1','fixture','agent')")
            old_store.put_object(command_id="v19-input-command",object_id="v19-replay-input",payload=b"v19 governed input",object_type="artifact",created_by_run="v19-root",classification_assertion_ref="v19-input-class")
            contract = {"schema_id":"nexus.task_contract","schema_version":1,"task_id":"v19-task","requester_id":"human-root","goal":"complete a previously interrupted bind","constraints":[],"input_object_refs":["v19-replay-input"],"open_questions":[],"success_criteria":["replay exact historical inner write"],"risk_class":"LOW","budget_account_ref":"v19-budget","routing_constraints":{"allowed_providers":[],"forbidden_providers":[],"locality":"ANY","network_required":False,"modalities":[]},"routing_preferences":{"optimize_for":"BALANCED"},"created_at":issued}
            contract_bytes = json.dumps(contract,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
            contract_hash = hashlib.sha256(contract_bytes).hexdigest()
            with old_store._connection() as conn:
                conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('v19-contract-class','OBJECT','v19-contract','PUBLIC','[]','1','fixture','agent')")
                rel = f"objects/sha256/{contract_hash[:2]}/{contract_hash}"
                payload_path = data_root / rel
                payload_path.parent.mkdir(parents=True,exist_ok=True)
                payload_path.write_bytes(contract_bytes)
                conn.execute("INSERT INTO objects(object_id) VALUES('v19-contract')")
                conn.execute("INSERT INTO object_envelopes(object_id,object_type,schema_id,schema_version,payload_uri,hash_profile_ref,hash_profile_version,integrity_hash,semantic_hash,created_by_run,classification_assertion_ref,created_at) VALUES('v19-contract','task_contract','nexus.object',1,?,'raw-sha256',1,?,NULL,'v19-root','v19-contract-class',?)", (rel,contract_hash,issued))
                conn.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES('v19-contract',NULL,'ACTIVE','VALID','AVAILABLE')")
                old_request = {"payload_integrity_hash":contract_hash,"object_id":"v19-contract","object_type":"task_contract","created_by_run":"v19-root","classification_assertion_ref":"v19-contract-class","derived_from":[]}
                legacy_hash = old_store._request_hash("put_object",old_request)
                result_json = json.dumps({"object_id":"v19-contract"},sort_keys=True,separators=(",",":"))
                conn.execute("INSERT INTO command_ledger(command_id,operation,request_hash,result_json,status,created_at,result_commitment,result_state) VALUES('v19-bind-contract-object','put_object',?,?,'SUCCEEDED',?,?,'LIVE')", (legacy_hash,result_json,issued,hashlib.sha256(result_json.encode()).hexdigest()))
            with old_store._connection() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='v19-bind-contract'").fetchone())
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM object_relations WHERE from_id='v19-contract'").fetchone()[0],0)
        finally:
            old_store.close()

        upgraded = ObjectStore(data_root)
        try:
            authority = AuthorityService(upgraded, policy)
            budget = BudgetService(upgraded)
            trace = TraceRuntime(upgraded, authority)
            runtime = DeterministicRuntime(upgraded, authority, budget, trace)
            revision = runtime.bind_task_contract(command_id="v19-bind-contract",root_run_id="v19-root",contract_object_id="v19-contract",classification_assertion_ref="v19-contract-class",contract=contract)
            self.assertEqual(revision,1)
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],22)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id='v19-contract'").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='v19-bind-contract-object'").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='v19-bind-contract'").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM logical_refs WHERE ref_id='task-contract:v19-task' AND current_object_id='v19-contract'").fetchone()[0],1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM object_relations WHERE from_id='v19-contract'").fetchone()[0],0)
            self.assertEqual(runtime.bind_task_contract(command_id="v19-bind-contract",root_run_id="v19-root",contract_object_id="v19-contract",classification_assertion_ref="v19-contract-class",contract=contract),1)
        finally:
            upgraded.close()

    def test_persisted_v20_canonical_approval_residue_is_redacted_during_v21_upgrade(self) -> None:
        self.store.close()
        database_path = self._historical_database(20, "persisted-v20-canonical-residue")
        now = "2026-09-26T00:00:00+00:00"
        with closing(sqlite3.connect(database_path)) as legacy:
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('audit-human','HUMAN','ACTIVE')")
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('legacy-agent','SERVICE','ACTIVE')")
            legacy.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('legacy-task','audit-human','ACTIVE',?,'legacy-task-command',NULL)", (now,))
            legacy.execute("INSERT INTO delegation_grants(grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version) VALUES('legacy-grant','audit-human','legacy-agent','[\"legacy-task\"]','[]','[]','[\"nexus-runtime\"]',?,?,'ACTIVE','1')", (now,"2027-09-26T00:00:00+00:00"))
            legacy.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('legacy-run-class','RUN','legacy-run','PUBLIC','[]','1','legacy fixture','legacy-agent')")
            legacy.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('legacy-run','legacy-task',NULL,NULL,'ORCHESTRATOR','SUCCEEDED','legacy-grant',NULL,NULL,'{\"allowed_classifications\":[\"PUBLIC\"],\"handling_tags\":[]}','legacy-run-class',?)", (now,))
            legacy.execute("INSERT INTO budget_accounts(account_id,task_id,amount_limit,unit,model_call_limit,tool_call_limit,child_run_limit) VALUES('legacy-account','legacy-task',1,'credits',0,1,1)")
            legacy.execute("INSERT INTO budget_reservations(reservation_id,account_id,run_id,amount,tool_calls,child_runs,state,command_id,created_at) VALUES('legacy-reservation','legacy-account','legacy-run',0,1,1,'CONSUMED','legacy-reservation-command',?)", (now,))
            legacy.execute("INSERT INTO objects(object_id) VALUES('legacy-purged-object')")
            legacy.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES('legacy-purged-object',NULL,'ACTIVE','VALID','AVAILABLE')")
            legacy.execute("INSERT INTO objects(object_id) VALUES('legacy-live-payload')")
            legacy.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES('legacy-live-payload',NULL,'ACTIVE','VALID','AVAILABLE')")
            legacy.execute("INSERT INTO approval_decisions(approval_id,approver_principal_id,target_type,target_ref,effect_id,payload_integrity_hash,decision,approved_scope_json,policy_version,issued_at,expires_at,reason,request_ref) VALUES('legacy-canonical-target','audit-human','TEST','object:legacy-purged-object',NULL,NULL,'APPROVE','[]','1',?,NULL,NULL,NULL)", (now,))
            legacy.execute("INSERT INTO approval_decisions(approval_id,approver_principal_id,target_type,target_ref,effect_id,payload_integrity_hash,decision,approved_scope_json,policy_version,issued_at,expires_at,reason,request_ref) VALUES('legacy-canonical-scope','audit-human','TEST','external-target',NULL,NULL,'APPROVE','[\"object:legacy-purged-object\",\"external-target\"]','1',?,NULL,NULL,NULL)", (now,))
            legacy.execute("INSERT INTO approval_decisions(approval_id,approver_principal_id,target_type,target_ref,effect_id,payload_integrity_hash,decision,approved_scope_json,policy_version,issued_at,expires_at,reason,request_ref) VALUES('legacy-canonical-request','audit-human','TEST','external-target',NULL,NULL,'APPROVE','[\"external-target\"]','1',?,NULL,NULL,'object:legacy-purged-object')", (now,))
            legacy.execute("INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) VALUES('legacy-canonical-effect','legacy-run','legacy-tool','1','WRITE','object:legacy-purged-object',?,'legacy-live-payload','legacy-effect-key','legacy-grant',NULL,'legacy-reservation','FINISHED','NOT_COMMITTED','RESOLVED','legacy-target-receipt',? ,?,?)", ("1"*64,json.dumps({"target_ref":"object:legacy-purged-object","payload_object_ref":"legacy-live-payload"},sort_keys=True,separators=(",",":")),now,now))
            legacy.execute("UPDATE object_states SET lifecycle='RETIRED',validity='INVALIDATED',payload_state='PURGED' WHERE object_id='legacy-purged-object'")
            legacy.commit()
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                rows = {row["approval_id"]:dict(row) for row in conn.execute("SELECT * FROM approval_decisions WHERE approval_id LIKE 'legacy-canonical-%'")}
                self.assertEqual(rows["legacy-canonical-target"]["target_ref"], "REDACTED_PURGED")
                self.assertEqual(rows["legacy-canonical-scope"]["approved_scope_json"], '["external-target"]')
                self.assertEqual(rows["legacy-canonical-scope"]["target_ref"], "external-target")
                self.assertIsNone(rows["legacy-canonical-request"]["request_ref"])
                effect = dict(conn.execute("SELECT target_ref,payload_object_ref,payload_integrity_hash,idempotency_key,external_receipt_ref,effect_json FROM effects WHERE effect_id='legacy-canonical-effect'").fetchone())
                self.assertEqual(effect["target_ref"], "REDACTED_PURGED")
                self.assertEqual(effect["payload_object_ref"], "legacy-live-payload")
                self.assertEqual(effect["payload_integrity_hash"], "1"*64)
                self.assertEqual(effect["idempotency_key"], "legacy-effect-key")
                self.assertIsNone(effect["external_receipt_ref"])
                self.assertNotIn("legacy-purged-object", effect["effect_json"])
                self.assertEqual(conn.execute("SELECT payload_state FROM object_states WHERE object_id='legacy-live-payload'").fetchone()[0], "AVAILABLE")
                self.assertNotIn("legacy-purged-object", json.dumps(list(rows.values())))
                self.assertNotIn("object:legacy-purged-object", json.dumps(list(rows.values())))
        finally:
            upgraded.close()

    def test_persisted_v21_redacted_effect_receipt_residue_is_removed_by_v22(self) -> None:
        self.store.close()
        database_path = self._historical_database(21, "persisted-v21-effect-receipt-residue")
        now = "2026-09-26T00:00:00+00:00"
        zero_hash = "0" * 64
        with closing(sqlite3.connect(database_path)) as legacy:
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('receipt-human','HUMAN','ACTIVE')")
            legacy.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES('receipt-agent','SERVICE','ACTIVE')")
            legacy.execute("INSERT INTO delegation_grants(grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version) VALUES('receipt-grant','receipt-human','receipt-agent','[]','[]','[]','[]',?,?,'ACTIVE','1')", (now,"2027-09-26T00:00:00+00:00"))
            legacy.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('receipt-task','receipt-human','SUCCEEDED',?,'receipt-task-command',NULL)", (now,))
            legacy.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('receipt-root-class','RUN','receipt-root','PUBLIC','[]','1','fixture','receipt-agent')")
            legacy.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('receipt-root','receipt-task',NULL,NULL,'ORCHESTRATOR','SUCCEEDED','receipt-grant',NULL,NULL,'{\"allowed_classifications\":[\"PUBLIC\"],\"handling_tags\":[]}','receipt-root-class',?)", (now,))
            legacy.execute("INSERT INTO budget_accounts(account_id,task_id,amount_limit,unit,model_call_limit,tool_call_limit,child_run_limit) VALUES('receipt-account','receipt-task',0,'credits',0,2,0)")
            for object_id in ("receipt-payload-redacted", "receipt-payload-unrelated"):
                legacy.execute("INSERT INTO objects(object_id) VALUES(?)", (object_id,))
                legacy.execute("INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES(?,NULL,'ACTIVE','VALID','AVAILABLE')", (object_id,))
            for suffix, object_id in (("redacted", "receipt-payload-redacted"), ("unrelated", "receipt-payload-unrelated")):
                run_id = "receipt-run-" + suffix
                reservation_id = "receipt-reservation-" + suffix
                effect_id = "receipt-effect-" + suffix
                legacy.execute("INSERT INTO budget_reservations(reservation_id,account_id,run_id,amount,model_calls,tool_calls,child_runs,state,command_id,created_at) VALUES(?,?,?,0,0,1,0,'CONSUMED',?,?)", (reservation_id,"receipt-account",run_id,"receipt-reserve-"+suffix,now))
                legacy.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?, 'RUN',?,'PUBLIC','[]','1','fixture','receipt-agent')", ("receipt-class-"+suffix,run_id))
                legacy.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES(?,'receipt-task',NULL,'receipt-root','TOOL','SUCCEEDED','receipt-grant',NULL,?,'{\"allowed_classifications\":[\"PUBLIC\"],\"handling_tags\":[]}',?,?)", (run_id,reservation_id,"receipt-class-"+suffix,now))
                target = "REDACTED_PURGED" if suffix == "redacted" else "https://example/unrelated"
                key = "REDACTED_PURGED:" + effect_id if suffix == "redacted" else "receipt-key-unrelated"
                effect_json = {"effect_id":effect_id,"run_id":run_id,"idempotency_key":key,"execution_state":"FINISHED","effect_outcome":"COMMITTED","reconciliation_status":"RESOLVED","target_ref":target,"external_receipt_ref":"legacy-receipt-"+suffix}
                sql_receipt = None if suffix == "redacted" else "legacy-receipt-unrelated"
                legacy.execute("INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) VALUES(?,?, 'receipt-tool','1','WRITE',?,?,?,?,'receipt-grant',NULL,?,'FINISHED','COMMITTED','RESOLVED',?,?,?,?)",
                               (effect_id,run_id,target,zero_hash if suffix == "redacted" else "1"*64,object_id,key,reservation_id,sql_receipt,json.dumps(effect_json,sort_keys=True,separators=(",",":")),now,now))
            legacy.commit()
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                redacted = dict(conn.execute("SELECT * FROM effects WHERE effect_id='receipt-effect-redacted'").fetchone())
                unrelated = dict(conn.execute("SELECT * FROM effects WHERE effect_id='receipt-effect-unrelated'").fetchone())
                self.assertIsNone(redacted["external_receipt_ref"])
                self.assertNotIn("external_receipt_ref", json.loads(redacted["effect_json"]))
                self.assertEqual((redacted["target_ref"],redacted["payload_object_ref"],redacted["payload_integrity_hash"],redacted["idempotency_key"],redacted["execution_state"],redacted["effect_outcome"],redacted["reconciliation_status"]),
                                 ("REDACTED_PURGED","receipt-payload-redacted",zero_hash,"REDACTED_PURGED:receipt-effect-redacted","FINISHED","COMMITTED","RESOLVED"))
                self.assertEqual(unrelated["external_receipt_ref"], "legacy-receipt-unrelated")
                self.assertEqual(json.loads(unrelated["effect_json"])["external_receipt_ref"], "legacy-receipt-unrelated")
        finally:
            upgraded.close()

    def test_persisted_v14_database_upgrades_to_latest_with_fk_integrity(self) -> None:
        self.store.close()
        database_path = self._historical_database(14, "persisted-v14")
        upgraded = ObjectStore(database_path.parent)
        try:
            with upgraded._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual([row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")], list(range(1,23)))
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
                    self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
                    self.assertIsNone(conn.execute("SELECT 1 FROM independent_purge_journal_watermark WHERE singleton=1").fetchone())
        finally:
            reopened.close()

    def test_v6_snapshot_replays_v7_through_v22_migrations_deterministically(self) -> None:
        self.store.close()
        database_path = self._historical_database(6, "persisted-v6")
        self.store = ObjectStore(database_path.parent)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
            row = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
            self.assertEqual(tuple(row), (22, "0022_effect_receipt_purge_redaction.sql"))
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
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 22)
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
