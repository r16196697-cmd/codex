"""Single-process SQLite metadata and immutable SHA-256 filesystem storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from kernel.object.errors import (
    CommandConflict,
    ConcurrentModification,
    IntegrityMismatch,
    LineageCycle,
    MigrationError,
    ObjectNotFound,
    PurgeBarrierActive,
    PurgedObject,
    WriterAlreadyRunning,
)


_LINEAGE_TYPES = ("derived_from", "generated_from", "supersedes")
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStore:
    """Persist object metadata in SQLite and raw bytes in a content-addressed store.

    One process owns writes. Each operation uses an explicit SQLite transaction;
    the in-process lock also serializes payload rename and barrier checks.
    """

    def __init__(self, data_root: str | Path, schema_dir: str | Path | None = None, policy: dict[str, Any] | None = None):
        self.data_root = Path(data_root).expanduser().resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.blob_root = self.data_root / "objects" / "sha256"
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_root / "nexus.sqlite"
        self._format_checker = FormatChecker()
        project_root = Path(__file__).resolve().parents[2]
        self.schema_dir = Path(schema_dir).resolve() if schema_dir else project_root / "schemas"
        policy_path = project_root / "policies" / "default-policy.json"
        self.policy = json.loads(json.dumps(policy)) if policy is not None else json.loads(policy_path.read_text(encoding="utf-8"))
        policy_schema = json.loads((project_root / "policies" / "nexus.policy@1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(policy_schema)
        Draft202012Validator(policy_schema, format_checker=self._format_checker).validate(self.policy)
        self.migrations_dir = project_root / "migrations"
        self._lock = threading.RLock()
        self._writer_lock_file = None
        self._closed = False
        self._schemas: dict[str, dict[str, Any]] = {}
        self._acquire_writer_lock()
        try:
            self._initialize_database()
            self._cleanup_orphan_payloads()
        except Exception:
            self.close()
            raise

    def _acquire_writer_lock(self) -> None:
        lock_path = self.data_root / "nexus.writer.lock"
        self._writer_lock_file = lock_path.open("a+b")
        self._writer_lock_file.seek(0, os.SEEK_END)
        if self._writer_lock_file.tell() == 0:
            self._writer_lock_file.write(b"\0")
            self._writer_lock_file.flush()
        self._writer_lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._writer_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._writer_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._writer_lock_file.close()
            self._writer_lock_file = None
            raise WriterAlreadyRunning("NEXUS_WRITER_ALREADY_RUNNING") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            lock_file = self._writer_lock_file
            self._writer_lock_file = None
            self._closed = True
            if lock_file is None:
                return
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    def __enter__(self) -> "ObjectStore":
        if self._closed:
            raise RuntimeError("ObjectStore is closed")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("ObjectStore is closed")
        conn = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _initialize_database(self) -> None:
        migrations: list[tuple[int, Path, bytes]] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = _MIGRATION_NAME.fullmatch(path.name)
            if not match:
                raise MigrationError("Unrecognized migration filename.")
            migrations.append((int(match.group(1)), path, path.read_bytes()))
        if not migrations or [item[0] for item in migrations] != list(range(1, len(migrations) + 1)):
            raise MigrationError("Migration sequence must start at 0001 and be contiguous.")

        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                "applied_at TEXT NOT NULL)"
            )
            for version, path, sql_bytes in migrations:
                checksum = _sha256(sql_bytes)
                applied = conn.execute(
                    "SELECT name, checksum FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if applied:
                    if applied["name"] != path.name or applied["checksum"] != checksum:
                        raise MigrationError("An applied migration differs from its recorded checksum.")
                    continue
                sql = sql_bytes.decode("utf-8")
                migration_name = path.name.replace("'", "''")
                applied_at = _utc_now()
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + f"\nINSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES({version},'{migration_name}','{checksum}','{applied_at}');"
                    + f"\nPRAGMA user_version = {version};\nCOMMIT;"
                )
                try:
                    conn.executescript(script)
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    raise
            latest = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if latest != len(migrations) or user_version != latest:
                raise MigrationError("Database schema version does not match applied migrations.")
            for row in conn.execute("SELECT profile_id,profile_version,representation,hash_algorithm FROM hash_profiles"):
                profile = {
                    "schema_id": "nexus.hash_profile",
                    "schema_version": 1,
                    "profile_id": row["profile_id"],
                    "profile_version": row["profile_version"],
                    "integrity_profile": {"representation": row["representation"], "hash_algorithm": row["hash_algorithm"]},
                }
                self._validate("nexus.hash_profile@1.schema.json", profile)

    def _schema(self, filename: str) -> dict[str, Any]:
        if filename not in self._schemas:
            path = self.schema_dir / filename
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self._schemas[filename] = schema
        return self._schemas[filename]

    def _validate(self, filename: str, value: dict[str, Any]) -> None:
        Draft202012Validator(self._schema(filename), format_checker=self._format_checker).validate(value)

    def _payload_path(self, uri: str) -> Path:
        if not uri.startswith("objects/sha256/"):
            raise IntegrityMismatch("Payload URI is outside the content-addressed store.")
        path = (self.data_root / Path(uri)).resolve()
        try:
            path.relative_to(self.blob_root.resolve())
        except ValueError as exc:
            raise IntegrityMismatch("Payload URI escapes the content-addressed store.") from exc
        return path

    def _write_payload_atomically(self, payload: bytes, digest: str) -> str:
        relative = Path("objects") / "sha256" / digest[:2] / digest
        destination = self.data_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination.read_bytes()
            if _sha256(existing) != digest:
                raise IntegrityMismatch("Existing content-addressed bytes do not verify.")
            return relative.as_posix()

        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=destination.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if _sha256(temporary.read_bytes()) != digest:
                raise IntegrityMismatch("Temporary payload failed its SHA-256 check.")
            if destination.exists():
                if _sha256(destination.read_bytes()) != digest:
                    raise IntegrityMismatch("Concurrent content-addressed payload mismatch.")
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            return relative.as_posix()
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _request_hash(operation: str, request: dict[str, Any]) -> str:
        return _sha256(_canonical_json({"operation": operation, "request": request}).encode("utf-8"))

    def _replay_command(
        self, conn: sqlite3.Connection, command_id: str, operation: str, request_hash: str
    ) -> dict[str, Any] | None:
        row = conn.execute("SELECT operation,request_hash,result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()
        if not row:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise CommandConflict("COMMAND_CONFLICT")
        return json.loads(row["result_json"])

    def _record_command(
        self, conn: sqlite3.Connection, command_id: str, operation: str, request_hash: str, result: dict[str, Any]
    ) -> None:
        created_at = _utc_now()
        entry = {
            "schema_id": "nexus.command_ledger",
            "schema_version": 1,
            "command_id": command_id,
            "operation": operation,
            "request_hash": request_hash,
            "result_json": result,
            "status": "SUCCEEDED",
            "created_at": created_at,
        }
        result_ref = result.get("object_id")
        if result_ref:
            entry["result_ref"] = result_ref
        self._validate("nexus.command_ledger@1.schema.json", entry)
        conn.execute(
            "INSERT INTO command_ledger(command_id,operation,request_hash,result_json,status,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (command_id, operation, request_hash, _canonical_json(result), "SUCCEEDED", created_at),
        )

    @staticmethod
    def _assert_unbarred(conn: sqlite3.Connection, object_ids: Iterable[str]) -> None:
        ids = sorted(set(object_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        row = conn.execute(
            "SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) "
            f"WHERE b.status IN ('ACTIVE','PARTIAL') AND r.object_id IN ({placeholders}) LIMIT 1",
            ids,
        ).fetchone()
        if row:
            raise PurgeBarrierActive("PURGE_BARRIER_ACTIVE")

    @staticmethod
    def _assert_lineage_acyclic(conn: sqlite3.Connection, from_id: str, relation_type: str, to_id: str) -> None:
        if relation_type not in _LINEAGE_TYPES:
            return
        if from_id == to_id:
            raise LineageCycle("LINEAGE_CYCLE")
        placeholders = ",".join("?" for _ in _LINEAGE_TYPES)
        sql = (
            "WITH RECURSIVE reachable(object_id) AS ("
            "SELECT to_id FROM object_relations WHERE from_id=? AND relation_type IN (" + placeholders + ") "
            "UNION "
            "SELECT r.to_id FROM object_relations r JOIN reachable x ON r.from_id=x.object_id "
            "WHERE r.relation_type IN (" + placeholders + ")"
            ") SELECT 1 FROM reachable WHERE object_id=? LIMIT 1"
        )
        params = (to_id, *_LINEAGE_TYPES, *_LINEAGE_TYPES, from_id)
        if conn.execute(sql, params).fetchone():
            raise LineageCycle("LINEAGE_CYCLE")

    def put_object(
        self,
        *,
        command_id: str,
        object_id: str,
        payload: bytes,
        object_type: str,
        created_by_run: str,
        classification_assertion_ref: str,
        derived_from: Iterable[str] = (),
    ) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("object_id must be a non-empty caller-generated identifier")
        digest = _sha256(payload)
        manifest_inputs: list[str] = []
        if object_type == "run_manifest":
            try:
                manifest_doc = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrityMismatch("RUN_MANIFEST_PAYLOAD_INVALID") from exc
            self._validate("nexus.run_manifest@1.schema.json", manifest_doc)
            manifest_inputs = sorted(set(manifest_doc["input_object_refs"]))
        source_ids = sorted(set(derived_from) | set(manifest_inputs))
        request = {
            "payload_integrity_hash": digest,
            "object_id": object_id,
            "object_type": object_type,
            "created_by_run": created_by_run,
            "classification_assertion_ref": classification_assertion_ref,
            "derived_from": source_ids,
        }
        operation = "put_object"
        request_hash = self._request_hash(operation, request)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay["object_id"]
                if conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone():
                    raise CommandConflict("OBJECT_ID_ALREADY_EXISTS")
                self._assert_unbarred(conn, source_ids)
                if object_type == "run_manifest":
                    if not conn.execute("SELECT 1 FROM runs WHERE run_id=?", (created_by_run,)).fetchone():
                        raise ObjectNotFound("RUN_MANIFEST_RUN_NOT_FOUND")
                    for input_id in manifest_inputs:
                        state = conn.execute("SELECT s.payload_state,s.validity,s.lifecycle FROM objects o JOIN object_states s USING(object_id) WHERE o.object_id=?", (input_id,)).fetchone()
                        if not state:
                            raise ObjectNotFound("RUN_MANIFEST_INPUT_NOT_FOUND")
                        if state["payload_state"] != "AVAILABLE" or state["validity"] != "VALID" or state["lifecycle"] != "ACTIVE":
                            raise IntegrityMismatch("RUN_MANIFEST_INPUT_UNAVAILABLE")
                payload_uri = f"objects/sha256/{digest[:2]}/{digest}"
                envelope = {
                    "schema_id": "nexus.object",
                    "schema_version": 1,
                    "object_id": object_id,
                    "object_type": object_type,
                    "payload_uri": payload_uri,
                    "hash_profile_ref": "raw-sha256",
                    "integrity_hash": digest,
                    "created_by_run": created_by_run,
                    "classification_assertion_ref": classification_assertion_ref,
                }
                self._validate("nexus.object@1.schema.json", envelope)
                object_state = {
                    "schema_id": "nexus.object_state",
                    "schema_version": 1,
                    "object_id": object_id,
                    "lifecycle": "ACTIVE",
                    "validity": "VALID",
                    "payload_state": "AVAILABLE",
                }
                self._validate("nexus.object_state@1.schema.json", object_state)
                classification_row = conn.execute(
                    "SELECT * FROM classification_assertions WHERE assertion_id=?", (classification_assertion_ref,)
                ).fetchone()
                if not classification_row or classification_row["subject_type"] != "OBJECT" or classification_row["subject_ref"] != object_id:
                    raise IntegrityMismatch("CLASSIFICATION_ASSERTION_REFERENCE_INVALID")
                if classification_row["policy_version"] != self.policy["policy_version"]:
                    raise IntegrityMismatch("CLASSIFICATION_POLICY_VERSION_MISMATCH")
                classification = {
                    "schema_id": "nexus.classification_assertion",
                    "schema_version": 1,
                    "assertion_id": classification_row["assertion_id"],
                    "subject_type": classification_row["subject_type"],
                    "subject_ref": classification_row["subject_ref"],
                    "sensitivity_level": classification_row["sensitivity_level"],
                    "handling_tags": json.loads(classification_row["handling_tags_json"]),
                    "policy_version": classification_row["policy_version"],
                    "reason": classification_row["reason"],
                    "actor_id": classification_row["actor_id"],
                }
                if classification_row["supersedes"]:
                    classification["supersedes"] = classification_row["supersedes"]
                self._validate("nexus.classification_assertion@1.schema.json", classification)
                for source_id in source_ids:
                    if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (source_id,)).fetchone():
                        raise ObjectNotFound("OBJECT_NOT_FOUND")
                    source = conn.execute(
                        "SELECT c.sensitivity_level,c.handling_tags_json,c.policy_version FROM object_envelopes e "
                        "JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref WHERE e.object_id=?",
                        (source_id,),
                    ).fetchone()
                    if not source:
                        raise IntegrityMismatch("SOURCE_CLASSIFICATION_MISSING")
                    rank = self.policy["classification"]["sensitivity_rank"]
                    if classification["policy_version"] != source["policy_version"] or rank.get(classification["sensitivity_level"], -1) < rank.get(source["sensitivity_level"], 99) or not set(json.loads(source["handling_tags_json"])).issubset(set(classification["handling_tags"])):
                        raise IntegrityMismatch("DERIVED_CLASSIFICATION_DOWNGRADE")
                relations = [
                    {"schema_id": "nexus.object_relation", "schema_version": 1, "from_id": object_id, "relation_type": "derived_from", "to_id": source_id}
                    for source_id in source_ids
                ]
                for source_id, relation in zip(source_ids, relations):
                    self._validate("nexus.object_relation@1.schema.json", relation)
                    if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (source_id,)).fetchone():
                        raise ObjectNotFound("OBJECT_NOT_FOUND")
                    self._assert_lineage_acyclic(conn, object_id, "derived_from", source_id)
                payload_uri = self._write_payload_atomically(payload, digest)
                conn.execute("INSERT INTO objects(object_id) VALUES(?)", (object_id,))
                conn.execute(
                    "INSERT INTO object_envelopes(object_id,object_type,schema_id,schema_version,payload_uri,hash_profile_ref,hash_profile_version,integrity_hash,semantic_hash,created_by_run,classification_assertion_ref,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (object_id, object_type, "nexus.object", 1, payload_uri, "raw-sha256", 1, digest, None, created_by_run, classification_assertion_ref, _utc_now()),
                )
                conn.execute(
                    "INSERT INTO object_states(object_id,revision,lifecycle,validity,payload_state) VALUES(?,NULL,'ACTIVE','VALID','AVAILABLE')",
                    (object_id,),
                )
                for source_id in source_ids:
                    conn.execute("INSERT INTO object_relations(from_id,relation_type,to_id) VALUES(?,?,?)", (object_id, "derived_from", source_id))
                if object_type == "run_manifest":
                    conn.executemany("INSERT INTO run_manifest_inputs(run_id,manifest_object_id,input_object_id) VALUES(?,?,?)", ((created_by_run, object_id, input_id) for input_id in manifest_inputs))
                result = {"object_id": object_id}
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return object_id
            except Exception:
                conn.rollback()
                raise

    def add_relation(self, *, command_id: str, from_id: str, relation_type: str, to_id: str) -> dict[str, str]:
        relation = {"schema_id": "nexus.object_relation", "schema_version": 1, "from_id": from_id, "relation_type": relation_type, "to_id": to_id}
        self._validate("nexus.object_relation@1.schema.json", relation)
        operation = "add_relation"
        request_hash = self._request_hash(operation, relation)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay
                self._assert_unbarred(conn, (from_id, to_id))
                for object_id in (from_id, to_id):
                    if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone():
                        raise ObjectNotFound("OBJECT_NOT_FOUND")
                self._assert_lineage_acyclic(conn, from_id, relation_type, to_id)
                conn.execute("INSERT INTO object_relations(from_id,relation_type,to_id) VALUES(?,?,?)", (from_id, relation_type, to_id))
                result = {"from_id": from_id, "relation_type": relation_type, "to_id": to_id}
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def create_logical_ref(
        self, *, command_id: str, ref_id: str, ref_type: str, object_id: str, updated_by_run: str
    ) -> int:
        operation = "create_logical_ref"
        request = {"ref_id": ref_id, "ref_type": ref_type, "object_id": object_id, "updated_by_run": updated_by_run}
        request_hash = self._request_hash(operation, request)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay["revision"]
                self._assert_unbarred(conn, (object_id,))
                now = _utc_now()
                logical_ref = {"schema_id": "nexus.logical_ref", "schema_version": 1, "ref_id": ref_id, "ref_type": ref_type, "current_object_id": object_id, "revision": 1, "updated_at": now, "updated_by_run": updated_by_run}
                self._validate("nexus.logical_ref@1.schema.json", logical_ref)
                conn.execute(
                    "INSERT INTO logical_refs(ref_id,ref_type,current_object_id,revision,updated_at,updated_by_run) VALUES(?,?,?,1,?,?)",
                    (ref_id, ref_type, object_id, now, updated_by_run),
                )
                result = {"ref_id": ref_id, "revision": 1}
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return 1
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise CommandConflict("LOGICAL_REF_EXISTS_OR_OBJECT_MISSING") from exc
            except Exception:
                conn.rollback()
                raise

    def compare_and_swap_ref(
        self,
        *,
        command_id: str,
        ref_id: str,
        expected_revision: int,
        new_object_id: str,
        updated_by_run: str,
    ) -> int:
        operation = "compare_and_swap_ref"
        request = {"ref_id": ref_id, "expected_revision": expected_revision, "new_object_id": new_object_id, "updated_by_run": updated_by_run}
        request_hash = self._request_hash(operation, request)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay["revision"]
                self._assert_unbarred(conn, (new_object_id,))
                existing = conn.execute("SELECT ref_type FROM logical_refs WHERE ref_id=?", (ref_id,)).fetchone()
                if not existing:
                    raise ObjectNotFound("LOGICAL_REF_NOT_FOUND")
                if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (new_object_id,)).fetchone():
                    raise ObjectNotFound("OBJECT_NOT_FOUND")
                now = _utc_now()
                next_revision = expected_revision + 1
                logical_ref = {"schema_id": "nexus.logical_ref", "schema_version": 1, "ref_id": ref_id, "ref_type": existing["ref_type"], "current_object_id": new_object_id, "revision": next_revision, "updated_at": now, "updated_by_run": updated_by_run}
                self._validate("nexus.logical_ref@1.schema.json", logical_ref)
                cur = conn.execute(
                    "UPDATE logical_refs SET current_object_id=?,revision=revision+1,updated_at=?,updated_by_run=? "
                    "WHERE ref_id=? AND revision=?",
                    (new_object_id, now, updated_by_run, ref_id, expected_revision),
                )
                if cur.rowcount != 1:
                    raise ConcurrentModification("CONCURRENT_MODIFICATION")
                revision = next_revision
                result = {"ref_id": ref_id, "revision": revision}
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return revision
            except Exception:
                conn.rollback()
                raise

    def install_purge_barrier(
        self,
        *,
        command_id: str,
        barrier_id: str,
        plan_id: str,
        protected_refs: Iterable[str],
        lineage_revision: int,
    ) -> None:
        refs = sorted(set(protected_refs))
        operation = "install_purge_barrier"
        request = {"barrier_id": barrier_id, "plan_id": plan_id, "protected_refs": refs, "lineage_revision": lineage_revision}
        request_hash = self._request_hash(operation, request)
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return
                for object_id in refs:
                    if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone():
                        raise ObjectNotFound("OBJECT_NOT_FOUND")
                created_at = _utc_now()
                barrier = {"schema_id": "nexus.purge_barrier", "schema_version": 1, "barrier_id": barrier_id, "plan_id": plan_id, "protected_refs": refs, "lineage_revision": lineage_revision, "status": "ACTIVE", "active_run_refs": [], "created_at": created_at}
                self._validate("nexus.purge_barrier@1.schema.json", barrier)
                conn.execute("INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at) VALUES(?,?,?,'ACTIVE',?)", (barrier_id, plan_id, lineage_revision, created_at))
                conn.executemany("INSERT INTO purge_barrier_refs(barrier_id,object_id) VALUES(?,?)", ((barrier_id, obj_id) for obj_id in refs))
                ledger_seq = conn.execute("SELECT COALESCE(MAX(ledger_seq),0)+1 FROM purge_ledger").fetchone()[0]
                ledger_entry = {"schema_id": "nexus.purge_ledger", "schema_version": 1, "ledger_seq": ledger_seq, "command_id": command_id, "barrier_id": barrier_id, "action": "BARRIER_INSTALLED", "created_at": created_at}
                self._validate("nexus.purge_ledger@1.schema.json", ledger_entry)
                conn.execute("INSERT INTO purge_ledger(ledger_seq,command_id,barrier_id,action,created_at) VALUES(?,?,?,'BARRIER_INSTALLED',?)", (ledger_seq, command_id, barrier_id, created_at))
                self._record_command(conn, command_id, operation, request_hash, {"barrier_id": barrier_id, "status": "ACTIVE"})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get_lineage(self, object_id: str) -> dict[str, list[str]]:
        with self._connection() as conn:
            if not conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone():
                raise ObjectNotFound("OBJECT_NOT_FOUND")
            lineage_placeholders = ",".join("?" for _ in _LINEAGE_TYPES)
            sources = conn.execute(
                "WITH RECURSIVE reachable(object_id) AS ("
                f"SELECT to_id FROM object_relations WHERE from_id=? AND relation_type IN ({lineage_placeholders}) "
                "UNION SELECT r.to_id FROM object_relations r JOIN reachable x ON r.from_id=x.object_id "
                f"WHERE r.relation_type IN ({lineage_placeholders})"
                ") SELECT object_id FROM reachable ORDER BY object_id",
                (object_id, *_LINEAGE_TYPES, *_LINEAGE_TYPES),
            ).fetchall()
            descendants = conn.execute(
                "WITH RECURSIVE reachable(object_id) AS ("
                f"SELECT from_id FROM object_relations WHERE to_id=? AND relation_type IN ({lineage_placeholders}) "
                "UNION SELECT r.from_id FROM object_relations r JOIN reachable x ON r.to_id=x.object_id "
                f"WHERE r.relation_type IN ({lineage_placeholders})"
                ") SELECT object_id FROM reachable ORDER BY object_id",
                (object_id, *_LINEAGE_TYPES, *_LINEAGE_TYPES),
            ).fetchall()
        return {"sources": [row[0] for row in sources], "derived": [row[0] for row in descendants]}

    def get_object_metadata(self, object_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT e.*,s.revision,s.lifecycle,s.validity,s.payload_state FROM objects o "
                "LEFT JOIN object_envelopes e USING(object_id) JOIN object_states s USING(object_id) WHERE o.object_id=?",
                (object_id,),
            ).fetchone()
        if not row:
            raise ObjectNotFound("OBJECT_NOT_FOUND")
        if row["payload_state"] == "PURGED" or row["object_type"] is None:
            return {"object_id": object_id, "payload_state": "PURGED"}
        return dict(row)

    def get_payload(self, object_id: str) -> bytes:
        metadata = self.get_object_metadata(object_id)
        if metadata.get("payload_state") == "PURGED":
            raise PurgedObject("PURGED_OBJECT")
        digest = metadata["integrity_hash"]
        expected_uri = f"objects/sha256/{digest[:2]}/{digest}"
        if metadata["payload_uri"] != expected_uri:
            raise IntegrityMismatch("OBJECT_PATH_HASH_MISMATCH")
        path = self._payload_path(metadata["payload_uri"])
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise IntegrityMismatch("OBJECT_PAYLOAD_MISSING") from exc
        if _sha256(payload) != digest:
            raise IntegrityMismatch("HASH_MISMATCH")
        return payload

    def verify_object(self, object_id: str) -> bool:
        self.get_payload(object_id)
        return True

    def _cleanup_orphan_payloads(self) -> None:
        with self._connection() as conn:
            referenced = {row[0] for row in conn.execute("SELECT payload_uri FROM object_envelopes")}
        for path in self.blob_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.data_root).as_posix()
            if path.name.startswith(".tmp-") or relative not in referenced:
                path.unlink()
        for directory in sorted((p for p in self.blob_root.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
