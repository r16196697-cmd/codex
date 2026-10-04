"""Single-process SQLite metadata and immutable SHA-256 filesystem storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from adapters.storage.migration_checksum import classify_migration_checksum

from kernel.object_refs import known_object_refs, redact_governed_object_values, resolve_governed_object_resource
from kernel.object.errors import (
    CommandConflict,
    ConcurrentModification,
    IntegrityMismatch,
    LineageCycle,
    MigrationError,
    ObjectNotFound,
    PurgeBarrierActive,
    PurgedObject,
    SchemaUnsupported,
    WriterAlreadyRunning,
)


_LINEAGE_TYPES = ("derived_from", "generated_from", "supersedes")
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_MEMORY_TABLES = {"raw_history_rows", "admitted_memory_rows", "memory_candidates", "memory_candidate_evidence"}
_MEMORY_INDEX_PREFIXES = ("raw_history_fts", "admitted_memory_fts")
_RECOVERY_SESSIONS_MIGRATION = 17
_INSTANCE_BINDING_MIGRATION = 28
_BOOTSTRAP_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_READ_ONLY_MUTATION_CAPABILITIES = frozenset({
    "core_write", "run_execute", "trace_write", "memory_write", "effect_commit",
    "egress", "learning_write",
})


class StartupPurpose(str, Enum):
    ORDINARY = "ORDINARY"
    INSTANCE_INITIALIZE = "INSTANCE_INITIALIZE"
    LEGACY_ADOPTION = "LEGACY_ADOPTION"
    RECOVERY = "RECOVERY"


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

    def __init__(
        self,
        data_root: str | Path,
        schema_dir: str | Path | None = None,
        policy: dict[str, Any] | None = None,
        *,
        force_recovery: bool = False,
        migrations_dir: str | Path | None = None,
        independent_purge_journal_path: str | Path | None = None,
        startup_purpose: StartupPurpose | str = StartupPurpose.ORDINARY,
        read_only: bool = False,
    ):
        self.data_root = Path(data_root).expanduser().resolve()
        self.database_path = self.data_root / "nexus.sqlite"
        self.blob_root = self.data_root / "objects" / "sha256"
        self._format_checker = FormatChecker()
        project_root = Path(__file__).resolve().parents[2]
        self.schema_dir = Path(schema_dir).resolve() if schema_dir else project_root / "schemas"
        policy_path = project_root / "policies" / "default-policy.json"
        from kernel.instance_binding import parse_json_object, policy_sha256

        self.policy = json.loads(json.dumps(policy)) if policy is not None else parse_json_object(policy_path.read_bytes())
        policy_schema = parse_json_object((project_root / "policies" / "nexus.policy@1.schema.json").read_bytes())
        Draft202012Validator.check_schema(policy_schema)
        Draft202012Validator(policy_schema, format_checker=self._format_checker).validate(self.policy)
        self.policy_sha256 = policy_sha256(self.policy)
        self.migrations_dir = Path(migrations_dir).resolve() if migrations_dir else project_root / "migrations"
        self._lock = threading.RLock()
        self._writer_lock_file = None
        self._closed = False
        self._purge_redaction_local = threading.local()
        self._binding_write_local = threading.local()
        self._bootstrap_schema_write_local = threading.local()
        self._recovery_local = threading.local()
        self._schemas: dict[str, dict[str, Any]] = {}
        self._journal_bootstrap_needed = False
        self._force_recovery = force_recovery
        self.read_only = bool(read_only)
        self._mode_cache = "NORMAL"
        try:
            self.startup_purpose = StartupPurpose.RECOVERY if force_recovery else StartupPurpose(startup_purpose)
        except ValueError as exc:
            raise MigrationError("INVALID_STARTUP_PURPOSE") from exc
        if force_recovery and StartupPurpose(startup_purpose) is not StartupPurpose.ORDINARY:
            raise MigrationError("RECOVERY_PURPOSE_CONFLICT")
        if not force_recovery and self.startup_purpose is StartupPurpose.RECOVERY:
            raise MigrationError("RECOVERY_PURPOSE_REQUIRES_EXPLICIT_RECOVERY_FLAG")

        configured_journal = independent_purge_journal_path or os.environ.get("NEXUS_INDEPENDENT_PURGE_JOURNAL")
        if configured_journal is None:
            configured_journal = self.data_root.parent / (self.data_root.name + ".purge-journal.jsonl")
        self._configured_journal_path = Path(configured_journal).expanduser().resolve()
        self._journal_identity_expected = self._journal_identity_for_path(self._configured_journal_path)
        self.independent_purge_journal = None
        self.independent_purge_journal_path = self._configured_journal_path
        self.instance_binding_status = "UNVERIFIED"
        self.instance_binding = None
        self._instance_restricted = False
        self._startup_intent = None

        if self.startup_purpose is StartupPurpose.ORDINARY:
            if not self.read_only:
                self._reject_prebinding_legacy_before_migration()
            if not self._configured_journal_path.parent.is_dir():
                raise MigrationError("PURGE_JOURNAL_UNAVAILABLE")
        elif self.startup_purpose is StartupPurpose.INSTANCE_INITIALIZE:
            self._startup_intent = self._load_startup_intent("FRESH_INITIALIZE")
            if not self.data_root.is_dir():
                raise MigrationError("INSTANCE_INITIALIZATION_INCOMPLETE")
            if not self._configured_journal_path.parent.is_dir():
                raise MigrationError("PURGE_JOURNAL_PARENT_MISSING")
        elif self.startup_purpose is StartupPurpose.LEGACY_ADOPTION:
            if self._intent_path("LEGACY_OPERATOR_ADOPTION").exists():
                self._startup_intent = self._load_startup_intent("LEGACY_OPERATOR_ADOPTION")
            if not self.database_path.is_file():
                raise MigrationError("LEGACY_INSTANCE_REQUIRED")
            if not self._configured_journal_path.parent.is_dir():
                raise MigrationError("PURGE_JOURNAL_UNAVAILABLE")
        else:
            if not self.database_path.is_file():
                raise MigrationError("FORCED_RECOVERY_REQUIRES_EXISTING_DATABASE")

        from kernel.purge.journal import IndependentPurgeJournal

        if self.read_only:
            if not self.data_root.is_dir() or not self.database_path.is_file():
                raise MigrationError("READ_ONLY_EXISTING_INSTANCE_REQUIRED")
            if self.startup_purpose is not StartupPurpose.ORDINARY or force_recovery:
                raise MigrationError("READ_ONLY_STARTUP_PURPOSE_UNSUPPORTED")
        try:
            self.independent_purge_journal = IndependentPurgeJournal(
                self._configured_journal_path, self.data_root, read_only=self.read_only
            )
        except (OSError, ValueError) as exc:
            reason = "PURGE_JOURNAL_UNAVAILABLE" if self.read_only else "PURGE_JOURNAL_PATH_INVALID"
            raise MigrationError(reason) from exc
        try:
            if self.read_only:
                self._open_read_only_instance()
                return
            self._acquire_writer_lock()
            self._mode_cache = self._detect_startup_mode() if self.database_path.is_file() else "NORMAL"
            if self.startup_purpose is StartupPurpose.RECOVERY:
                if not self._has_migration_table():
                    raise MigrationError("FORCED_RECOVERY_REQUIRES_MIGRATED_DATABASE")
                self._validate_recovery_database()
                if self._force_recovery:
                    # Explicit Recovery may start from a previously NORMAL
                    # snapshot. Set the in-memory capability boundary before
                    # maintenance; the durable mode transition is committed
                    # with the recovery-session record below.
                    self._mode_cache = "RECOVERY"
                    legacy_recovery = (
                        self._is_provable_prebinding_legacy()
                        or self._has_open_forced_legacy_recovery_session()
                    )
                    if legacy_recovery:
                        # Persist generation provenance before migration 0028
                        # removes the only schema-version evidence that this
                        # unbound database predates policy binding.
                        with self._recovery_maintenance():
                            self._open_recovery_session(source_mode="FORCED_LEGACY_UNBOUND")
                            self._initialize_database()
                    else:
                        with self._recovery_maintenance():
                            self._initialize_database()
                            self._open_recovery_session()
                return

            if self.startup_purpose is StartupPurpose.INSTANCE_INITIALIZE:
                self._initialize_fresh_instance()
                return

            if self.startup_purpose is StartupPurpose.LEGACY_ADOPTION:
                self._open_for_legacy_adoption()
                return

            # An existing instance in Runtime recovery keeps the established
            # Recovery behavior; Recovery never creates or adopts a binding.
            if self.startup_purpose is StartupPurpose.ORDINARY:
                self._reject_prebinding_legacy_before_migration()
            startup_mode = self._detect_startup_mode()
            if startup_mode != "NORMAL":
                self._mode_cache = startup_mode
                # Runtime safety modes restrict capabilities, but they do not
                # waive the instance's policy binding. Validate it before
                # exposing even the read-only recovery projection.
                binding = self._read_instance_binding()
                if binding is None:
                    if self._intent_exists("FRESH_INITIALIZE"):
                        self._require_matching_startup_intent("FRESH_INITIALIZE", "INSTANCE_INITIALIZATION_INCOMPLETE")
                    raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
                self._validate_instance_binding(binding)
                self.instance_binding = binding
                self.instance_binding_status = "BOUND"
                self._validate_recovery_database()
                return
            self._mode_cache = "NORMAL"
            self._initialize_database()
            self._mode_cache = self._read_persisted_mode()
            if self._mode_cache != "NORMAL":
                self._validate_recovery_database()
                return
            binding = self._read_instance_binding()
            if binding is None:
                if self._intent_exists("FRESH_INITIALIZE"):
                    self._require_matching_startup_intent("FRESH_INITIALIZE", "INSTANCE_INITIALIZATION_INCOMPLETE")
                if self._intent_exists("LEGACY_OPERATOR_ADOPTION"):
                    self._require_matching_startup_intent("LEGACY_OPERATOR_ADOPTION", "POLICY_BINDING_ADOPTION_IN_PROGRESS")
                raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
            self._validate_instance_binding(binding)
            journal_state = self._journal_startup_state()
            if journal_state == "recovery":
                self._validate_recovery_database()
                self._mode_cache = "RECOVERY"
                with self._recovery_maintenance():
                    self._open_recovery_session(source_mode="JOURNAL_MISMATCH")
                return
            self._bootstrap_or_validate_journal_watermark()
            self._create_object_directories()
            self._cleanup_orphan_payloads()
            self.instance_binding = binding
            self.instance_binding_status = "BOUND"
        except Exception:
            self.close()
            raise

    @staticmethod
    def _journal_identity_for_path(path: Path) -> str:
        material = "nexus-independent-purge-journal-v1\0" + os.path.normcase(str(path))
        return _sha256(material.encode("utf-8"))

    def _intent_path(self, source: str) -> Path:
        name = {
            "FRESH_INITIALIZE": ".nexus-instance-init-v1.json",
            "LEGACY_OPERATOR_ADOPTION": ".nexus-policy-adoption-v1.json",
        }.get(source)
        if name is None:
            raise MigrationError("INVALID_BOOTSTRAP_INTENT_KIND")
        return self.data_root / name

    @staticmethod
    def _strict_json_object(raw: bytes) -> dict[str, Any]:
        def pairs_hook(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook)
        if not isinstance(value, dict):
            raise ValueError("bootstrap intent must be an object")
        if raw != _canonical_json(value).encode("utf-8"):
            raise ValueError("bootstrap intent must use canonical JSON")
        return value

    def _load_startup_intent(self, source: str) -> dict[str, Any]:
        from kernel.instance_binding import BOOTSTRAP_PROTOCOL_VERSION, request_sha256

        path = self._intent_path(source)
        try:
            intent = self._strict_json_object(path.read_bytes())
        except FileNotFoundError as exc:
            raise MigrationError("BOOTSTRAP_INTENT_REQUIRED") from exc
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise MigrationError("BOOTSTRAP_INTENT_INVALID") from exc
        expected_kind = "FRESH_INITIALIZE" if source == "FRESH_INITIALIZE" else "LEGACY_OPERATOR_ADOPTION"
        if intent.get("source") != expected_kind or intent.get("protocol_version") != BOOTSTRAP_PROTOCOL_VERSION:
            raise MigrationError("BOOTSTRAP_INTENT_INVALID")
        required = {
            "protocol_version", "source", "instance_id", "command_id", "policy_version",
            "policy_sha256", "journal_identity", "request_sha256", "created_at",
        }
        if set(intent) != required:
            raise MigrationError("BOOTSTRAP_INTENT_INVALID")
        request = {key: intent[key] for key in required - {"request_sha256", "created_at"}}
        if request_sha256(request) != intent["request_sha256"]:
            raise MigrationError("BOOTSTRAP_INTENT_INVALID")
        instance_id = intent["instance_id"]
        command_id = intent["command_id"]
        if (
            not isinstance(instance_id, str)
            or not re.fullmatch(r"nexus-instance-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", instance_id)
            or not isinstance(command_id, str)
            or not _BOOTSTRAP_LOGICAL_ID.fullmatch(command_id)
            or PureWindowsPath(command_id).drive
            or Path(command_id).is_absolute()
        ):
            raise MigrationError("BOOTSTRAP_INTENT_INVALID")
        try:
            created_at = datetime.fromisoformat(intent["created_at"].replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError("timezone required")
        except (AttributeError, TypeError, ValueError) as exc:
            raise MigrationError("BOOTSTRAP_INTENT_INVALID") from exc
        if (
            intent["policy_version"] != self.policy["policy_version"]
            or intent["policy_sha256"] != self.policy_sha256
            or intent["journal_identity"] != self._journal_identity_expected
        ):
            raise MigrationError("BOOTSTRAP_INTENT_CONFLICT")
        return intent

    def _intent_exists(self, source: str) -> bool:
        return self._intent_path(source).exists()

    def _database_schema_version(self) -> int | None:
        if not self.database_path.is_file():
            return None
        try:
            with closing(sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)) as conn:
                has_migrations = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
                if not has_migrations:
                    return -1
                return int(conn.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise MigrationError("DATABASE_SCHEMA_STATE_INVALID") from exc

    def _reject_prebinding_legacy_before_migration(self) -> None:
        version = self._database_schema_version()
        if version is None:
            if self._intent_exists("FRESH_INITIALIZE"):
                self._require_matching_startup_intent("FRESH_INITIALIZE", "INSTANCE_INITIALIZATION_INCOMPLETE")
            if self._intent_exists("LEGACY_OPERATOR_ADOPTION"):
                self._require_matching_startup_intent("LEGACY_OPERATOR_ADOPTION", "POLICY_BINDING_ADOPTION_IN_PROGRESS")
            raise MigrationError("INSTANCE_INITIALIZATION_REQUIRED")
        if 0 <= version < _INSTANCE_BINDING_MIGRATION:
            if self._intent_exists("FRESH_INITIALIZE"):
                self._require_matching_startup_intent("FRESH_INITIALIZE", "INSTANCE_INITIALIZATION_INCOMPLETE")
            if self._intent_exists("LEGACY_OPERATOR_ADOPTION"):
                self._require_matching_startup_intent("LEGACY_OPERATOR_ADOPTION", "POLICY_BINDING_ADOPTION_IN_PROGRESS")
            raise MigrationError("POLICY_BINDING_ADOPTION_REQUIRED")
        if version < 0:
            raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
        # At/after the binding migration, defer reading the singleton binding
        # until after the writer lease and checksum-verified migration pass.

    def _startup_intent_matches_current(self, source: str) -> bool:
        try:
            self._load_startup_intent(source)
            return True
        except MigrationError:
            return False

    def _require_matching_startup_intent(self, source: str, incomplete_reason: str) -> None:
        if not self._startup_intent_matches_current(source):
            raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
        raise MigrationError(incomplete_reason)

    def _create_object_directories(self) -> None:
        self.blob_root.mkdir(parents=True, exist_ok=True)

    def _open_read_only_instance(self) -> None:
        """Open an existing bound instance without startup repair or filesystem writes."""
        if not self._configured_journal_path.is_file():
            raise MigrationError("PURGE_JOURNAL_UNAVAILABLE")
        self._acquire_writer_lock(read_only=True)
        self._assert_read_only_sqlite_state()
        self._validate_database_read_only(require_current=True)
        self._mode_cache = self._detect_startup_mode()
        if self._mode_cache == "RECOVERY":
            raise MigrationError("READ_ONLY_RUNTIME_MODE_UNSUPPORTED")
        binding = self._read_instance_binding()
        if binding is None:
            raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
        self._validate_instance_binding(binding)
        self._validate_read_only_journal_binding()
        self.instance_binding = binding
        self.instance_binding_status = "BOUND"

    def _assert_read_only_sqlite_state(self) -> None:
        """Immutable SQLite reads ignore WAL, so reject any uncheckpointed WAL state."""
        wal_path = Path(str(self.database_path) + "-wal")
        rollback_journal_path = Path(str(self.database_path) + "-journal")
        try:
            if wal_path.exists() and wal_path.stat().st_size > 0:
                raise MigrationError("READ_ONLY_WAL_STATE_UNSUPPORTED")
            if rollback_journal_path.exists() and rollback_journal_path.stat().st_size > 0:
                raise MigrationError("READ_ONLY_SQLITE_RECOVERY_REQUIRED")
        except OSError as exc:
            raise MigrationError("READ_ONLY_SQLITE_STATE_UNAVAILABLE") from exc

    def _validate_read_only_journal_binding(self) -> None:
        try:
            sequence, record_hash = self.independent_purge_journal.verified_head()
        except (OSError, UnicodeError, ValueError) as exc:
            raise MigrationError("PURGE_JOURNAL_INVALID") from exc
        with self._connection() as conn:
            row = conn.execute(
                "SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1"
            ).fetchone()
        if not row:
            raise MigrationError("PURGE_JOURNAL_WATERMARK_MISSING")
        if tuple(row) != (self.independent_purge_journal.identity, sequence, record_hash):
            raise MigrationError("PURGE_JOURNAL_WATERMARK_MISMATCH")

    def _read_instance_binding(self) -> dict[str, Any] | None:
        try:
            with self._connection() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instance_policy_binding'"
                ).fetchone()
                if not exists:
                    return None
                row = conn.execute("SELECT * FROM instance_policy_binding WHERE singleton=1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise MigrationError("INSTANCE_BINDING_UNREADABLE") from exc
        return dict(row) if row else None

    def _validate_instance_binding(self, binding: dict[str, Any]) -> None:
        from kernel.instance_binding import request_sha256

        if binding["policy_version"] != self.policy["policy_version"]:
            raise MigrationError("POLICY_ROTATION_UNSUPPORTED")
        if binding["policy_sha256"] != self.policy_sha256:
            raise MigrationError("POLICY_BINDING_MISMATCH")
        if binding["journal_identity"] != self._journal_identity_expected:
            raise MigrationError("PURGE_JOURNAL_IDENTITY_MISMATCH")
        if binding["binding_source"] not in {"FRESH_INITIALIZE", "LEGACY_OPERATOR_ADOPTION"}:
            raise MigrationError("INSTANCE_BINDING_SOURCE_INVALID")
        request = {
            "instance_id": binding["instance_id"],
            "binding_source": binding["binding_source"],
            "policy_version": binding["policy_version"],
            "policy_sha256": binding["policy_sha256"],
            "journal_identity": binding["journal_identity"],
            "bootstrap_protocol_version": binding["bootstrap_protocol_version"],
            "binding_command_id": binding["binding_command_id"],
        }
        if request_sha256(request) != binding["binding_request_sha256"]:
            raise MigrationError("INSTANCE_BINDING_REQUEST_INTEGRITY_FAILED")
        source = binding["binding_source"]
        # The immutable database binding is the durable identity. Intents are
        # retained provenance and support exact bootstrap retries, but backup
        # and restore flows need not copy process-local initialization files.
        # If one is present, verify it; its absence cannot unbind a committed
        # instance.
        if self._intent_exists(source):
            intent = self._load_startup_intent(source)
            if intent["instance_id"] != binding["instance_id"] or intent["command_id"] != binding["binding_command_id"]:
                raise MigrationError("INSTANCE_BINDING_INTENT_MISMATCH")

    def get_instance_binding_status(self) -> dict[str, Any]:
        """Return a path-free binding projection for operator diagnostics."""
        binding = self.instance_binding or self._read_instance_binding()
        if not binding:
            version = self._database_schema_version()
            if self._intent_exists("FRESH_INITIALIZE"):
                state = (
                    "PARTIAL_FRESH_BOOTSTRAP"
                    if self._startup_intent_matches_current("FRESH_INITIALIZE")
                    else "CONFLICT_OR_RECOVERY_REQUIRED"
                )
            elif version is not None and 0 <= version < _INSTANCE_BINDING_MIGRATION:
                state = "LEGACY_UNBOUND_INSTANCE"
            elif (
                version is not None
                and version >= _INSTANCE_BINDING_MIGRATION
                and self._has_forced_legacy_recovery_provenance()
            ):
                state = "LEGACY_UNBOUND_INSTANCE"
            else:
                state = "CONFLICT_OR_RECOVERY_REQUIRED"
            return {"state": state, "policy_content_binding": "UNVERIFIED"}
        state = "FRESH_BOUND_INSTANCE" if binding["binding_source"] == "FRESH_INITIALIZE" else "LEGACY_ADOPTED_BOUND_INSTANCE"
        return {
            "state": state,
            "policy_content_binding": "BOUND",
            "instance_id": binding["instance_id"],
            "policy_version": binding["policy_version"],
            "policy_sha256": binding["policy_sha256"],
            "journal_identity": binding["journal_identity"],
            "binding_source": binding["binding_source"],
            "bound_at": binding["bound_at"],
        }

    def _commit_instance_binding(self, *, source: str, intent: dict[str, Any]) -> dict[str, Any]:
        from kernel.instance_binding import request_sha256

        if source not in {"FRESH_INITIALIZE", "LEGACY_OPERATOR_ADOPTION"}:
            raise MigrationError("INVALID_BINDING_SOURCE")
        binding_request = {
            "instance_id": intent["instance_id"],
            "binding_source": source,
            "policy_version": self.policy["policy_version"],
            "policy_sha256": self.policy_sha256,
            "journal_identity": self._journal_identity_expected,
            "bootstrap_protocol_version": intent["protocol_version"],
            "binding_command_id": intent["command_id"],
        }
        digest = request_sha256(binding_request)
        with self._lock:
            self._binding_write_local.depth = getattr(self._binding_write_local, "depth", 0) + 1
            try:
                with self._connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        exists = conn.execute("SELECT * FROM instance_policy_binding WHERE singleton=1").fetchone()
                        if exists:
                            prior = dict(exists)
                            if (
                                prior["instance_id"] == binding_request["instance_id"]
                                and prior["binding_source"] == source
                                and prior["policy_version"] == binding_request["policy_version"]
                                and prior["policy_sha256"] == binding_request["policy_sha256"]
                                and prior["journal_identity"] == binding_request["journal_identity"]
                                and prior["bootstrap_protocol_version"] == binding_request["bootstrap_protocol_version"]
                                and prior["binding_command_id"] == binding_request["binding_command_id"]
                                and prior["binding_request_sha256"] == digest
                            ):
                                conn.commit()
                                return prior
                            raise MigrationError("INSTANCE_BINDING_CONFLICT")
                        conn.execute(
                            "INSERT INTO instance_policy_binding(singleton,instance_id,binding_source,policy_version,policy_sha256,journal_identity,bound_at,bootstrap_protocol_version,binding_command_id,binding_request_sha256) VALUES(1,?,?,?,?,?,?,?,?,?)",
                            (
                                binding_request["instance_id"], source, binding_request["policy_version"],
                                binding_request["policy_sha256"], binding_request["journal_identity"],
                                _utc_now(), binding_request["bootstrap_protocol_version"],
                                binding_request["binding_command_id"], digest,
                            ),
                        )
                        row = conn.execute("SELECT * FROM instance_policy_binding WHERE singleton=1").fetchone()
                        conn.commit()
                        return dict(row)
                    except Exception:
                        if conn.in_transaction:
                            conn.rollback()
                        raise
            finally:
                self._binding_write_local.depth -= 1

    def _initialize_fresh_instance(self) -> None:
        if self._intent_exists("LEGACY_OPERATOR_ADOPTION"):
            raise MigrationError("BOOTSTRAP_INTENT_CONFLICT")
        if not self._resume_root_has_only_expected_fresh_residue():
            raise MigrationError("FRESH_INSTANCE_ROOT_NOT_EMPTY")
        self._mode_cache = self._detect_startup_mode() if self.database_path.is_file() else "NORMAL"
        if self._mode_cache != "NORMAL":
            raise MigrationError("FRESH_INSTANCE_RUNTIME_STATE_INVALID")
        journal_state = self._journal_startup_state()
        if journal_state == "recovery":
            raise MigrationError("FRESH_PURGE_JOURNAL_CONFLICT")
        self._initialize_database()
        self._mode_cache = self._read_persisted_mode()
        if self._mode_cache != "NORMAL":
            raise MigrationError("FRESH_INSTANCE_RUNTIME_STATE_INVALID")
        self._bootstrap_or_validate_journal_watermark()
        binding = self._read_instance_binding()
        if binding is None:
            self._validate_fresh_instance_empty_state()
            binding = self._commit_instance_binding(source="FRESH_INITIALIZE", intent=self._startup_intent)
        elif binding["binding_source"] != "FRESH_INITIALIZE":
            raise MigrationError("INSTANCE_BINDING_CONFLICT")
        self._validate_instance_binding(binding)
        self.instance_binding = binding
        self.instance_binding_status = "BOUND"
        self._create_object_directories()
        self._cleanup_orphan_payloads()

    def _resume_root_has_only_expected_fresh_residue(self) -> bool:
        allowed = {
            ".nexus-instance-init-v1.json", "nexus.sqlite", "nexus.sqlite-wal", "nexus.sqlite-shm",
            "nexus.writer.lock", "objects",
        }
        try:
            if any(child.name not in allowed for child in self.data_root.iterdir()):
                return False
            objects = self.data_root / "objects"
            if objects.exists():
                if objects.is_symlink() or not objects.is_dir():
                    return False
                for path in objects.rglob("*"):
                    if path.is_symlink():
                        return False
                    if path.is_file():
                        return False
                    if path.name not in {"sha256"} and path != objects:
                        return False
            return True
        except OSError:
            return False

    def _validate_fresh_instance_empty_state(self) -> None:
        with closing(sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("FRESH_DATABASE_INTEGRITY_FAILED")
            checks = {
                "trust_anchors": "SELECT COUNT(*) FROM trust_anchors",
                "delegation_grants": "SELECT COUNT(*) FROM delegation_grants",
                "approval_decisions": "SELECT COUNT(*) FROM approval_decisions",
                "classification_assertions": "SELECT COUNT(*) FROM classification_assertions",
                "tasks": "SELECT COUNT(*) FROM tasks",
                "runs": "SELECT COUNT(*) FROM runs",
                "objects": "SELECT COUNT(*) FROM objects",
                "trace_events": "SELECT COUNT(*) FROM trace_events",
                "authority_events": "SELECT COUNT(*) FROM authority_events",
                "command_ledger": "SELECT COUNT(*) FROM command_ledger",
            }
            for table, query in checks.items():
                if conn.execute(query).fetchone()[0] != 0:
                    raise MigrationError("FRESH_DATABASE_CONTAINS_ORDINARY_RECORDS")
            principals = conn.execute("SELECT principal_id,principal_type,status FROM principals").fetchall()
            if [tuple(row) for row in principals] != [("nexus-core-recovery", "SERVICE", "ACTIVE")]:
                raise MigrationError("FRESH_DATABASE_PRINCIPAL_STATE_INVALID")
            watermark = conn.execute(
                "SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1"
            ).fetchone()
        if not watermark or tuple(watermark) != (self._journal_identity_expected, 0, "0" * 64):
            raise MigrationError("FRESH_PURGE_WATERMARK_INVALID")

    def _open_for_legacy_adoption(self) -> None:
        if self._intent_exists("FRESH_INITIALIZE"):
            raise MigrationError("INSTANCE_INITIALIZATION_INCOMPLETE")
        if self._mode_cache != "NORMAL":
            raise MigrationError("LEGACY_INSTANCE_RUNTIME_NOT_NORMAL")
        binding = self._read_instance_binding()
        if binding is not None:
            self._validate_instance_binding(binding)
            if binding["binding_source"] != "LEGACY_OPERATOR_ADOPTION":
                raise MigrationError("INSTANCE_BINDING_CONFLICT")
            self.instance_binding = binding
            self.instance_binding_status = "BOUND_FROM_ADOPTION"
        else:
            version = self._database_schema_version()
            if version is None or version < 0:
                raise MigrationError("LEGACY_DATABASE_SCHEMA_INVALID")
            if (
                version >= _INSTANCE_BINDING_MIGRATION
                and self._startup_intent is None
                and not self._has_forced_legacy_recovery_provenance()
            ):
                raise MigrationError("INSTANCE_BINDING_MISSING_OR_AMBIGUOUS")
            if version < _INSTANCE_BINDING_MIGRATION and self._startup_intent is not None:
                raise MigrationError("LEGACY_ADOPTION_INTENT_CONFLICT")
            self.instance_binding_status = "UNVERIFIED"
        self._instance_restricted = True

    def accept_legacy_adoption_intent(self, intent: dict[str, Any]) -> None:
        if self.startup_purpose is not StartupPurpose.LEGACY_ADOPTION or not self._instance_restricted:
            raise MigrationError("LEGACY_ADOPTION_MODE_REQUIRED")
        if self._startup_intent is not None and intent != self._startup_intent:
            raise MigrationError("BOOTSTRAP_INTENT_CONFLICT")
        self._startup_intent = intent

    def prepare_legacy_adoption(self) -> None:
        if self.startup_purpose is not StartupPurpose.LEGACY_ADOPTION or not self._instance_restricted:
            raise MigrationError("LEGACY_ADOPTION_MODE_REQUIRED")
        if self._startup_intent is None:
            raise MigrationError("BOOTSTRAP_INTENT_REQUIRED")
        self._bootstrap_schema_write_local.depth = getattr(self._bootstrap_schema_write_local, "depth", 0) + 1
        try:
            self._initialize_database()
        finally:
            self._bootstrap_schema_write_local.depth -= 1
        self._mode_cache = self._read_persisted_mode()
        if self._mode_cache != "NORMAL":
            raise MigrationError("LEGACY_INSTANCE_RUNTIME_NOT_NORMAL")
        if self._read_instance_binding() is not None:
            raise MigrationError("INSTANCE_BINDING_CONFLICT")
        if self._journal_startup_state() == "recovery":
            raise MigrationError("LEGACY_PURGE_JOURNAL_CONFLICT")
        self._bootstrap_schema_write_local.depth += 1
        try:
            self._bootstrap_or_validate_journal_watermark()
        finally:
            self._bootstrap_schema_write_local.depth -= 1
        self._adoption_schema_ready = True

    def commit_legacy_policy_binding(self) -> dict[str, Any]:
        if self.startup_purpose is not StartupPurpose.LEGACY_ADOPTION or not self._instance_restricted or not getattr(self, "_adoption_schema_ready", False):
            raise MigrationError("LEGACY_ADOPTION_MODE_REQUIRED")
        binding = self._commit_instance_binding(source="LEGACY_OPERATOR_ADOPTION", intent=self._startup_intent)
        self._validate_instance_binding(binding)
        self.instance_binding = binding
        self.instance_binding_status = "BOUND_FROM_ADOPTION"
        return binding

    def legacy_adoption_compatibility(self, proposed_policy: dict[str, Any]) -> dict[str, Any]:
        """Read-only, sanitized checks required before a legacy binding is adopted."""
        from kernel.authority import AuthorityService

        if self.startup_purpose is not StartupPurpose.LEGACY_ADOPTION or not self._instance_restricted:
            raise MigrationError("LEGACY_ADOPTION_MODE_REQUIRED")
        issues: list[str] = []
        policy_version = proposed_policy.get("policy_version")
        db_path = f"file:{self.database_path.as_posix()}?mode=ro"
        anchors = active_grants = available_objects = []
        active_barrier = watermark = None
        try:
            with closing(sqlite3.connect(db_path, uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    issues.append("SQLITE_INTEGRITY_FAILED")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    issues.append("SQLITE_FOREIGN_KEY_CHECK_FAILED")
                rows = conn.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version").fetchall()
                user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                migration_bytes = {path.name: path.read_bytes() for path in self.migrations_dir.glob("*.sql")}
                if [row["version"] for row in rows] != list(range(1, user_version + 1)):
                    issues.append("MIGRATION_SEQUENCE_INVALID")
                for row in rows:
                    content = migration_bytes.get(row["name"])
                    if content is None or _sha256(content) != row["checksum"]:
                        issues.append("MIGRATION_CHECKSUM_MISMATCH")
                        break
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required_tables = {
                    "schema_migrations", "principals", "trust_anchors", "delegation_grants",
                    "approval_decisions", "classification_assertions", "object_states",
                }
                if not required_tables.issubset(tables):
                    issues.append("LEGACY_DATABASE_SCHEMA_INVALID")
                if "instance_policy_binding" in tables and conn.execute(
                    "SELECT 1 FROM instance_policy_binding WHERE singleton=1"
                ).fetchone():
                    issues.append("INSTANCE_ALREADY_BOUND")
                anchors = conn.execute(
                    "SELECT ta.principal_id,ta.policy_ref,p.principal_type "
                    "FROM trust_anchors ta LEFT JOIN principals p USING(principal_id)"
                ).fetchall()
                for row in anchors:
                    if row["principal_type"] is None:
                        issues.append("TRUST_ANCHOR_PRINCIPAL_MISSING")
                    if row["principal_id"] not in proposed_policy["trust_anchors"]:
                        issues.append("TRUST_ANCHOR_NOT_ALLOWED_BY_POLICY")
                    if row["policy_ref"] != policy_version:
                        issues.append("TRUST_ANCHOR_POLICY_VERSION_MISMATCH")
                for table, column in (
                    ("delegation_grants", "policy_version"),
                    ("classification_assertions", "policy_version"),
                    ("approval_decisions", "policy_version"),
                ):
                    if table in tables and conn.execute(
                        f"SELECT 1 FROM {table} WHERE {column}<>? LIMIT 1", (policy_version,)
                    ).fetchone():
                        issues.append("PERSISTED_POLICY_VERSION_MISMATCH")
                active_grants = conn.execute(
                    "SELECT grant_id FROM delegation_grants WHERE status='ACTIVE' ORDER BY grant_id"
                ).fetchall()
                available_objects = conn.execute(
                    "SELECT object_id FROM object_states WHERE payload_state='AVAILABLE' ORDER BY object_id"
                ).fetchall()
                active_barrier = (
                    conn.execute("SELECT 1 FROM purge_barriers WHERE status IN ('ACTIVE','PARTIAL') LIMIT 1").fetchone()
                    if "purge_barriers" in tables else None
                )
                watermark = (
                    conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
                    if "independent_purge_journal_watermark" in tables else None
                )
        except sqlite3.DatabaseError:
            issues.append("LEGACY_DATABASE_SCHEMA_INVALID")
            anchors = active_grants = available_objects = []
            active_barrier = watermark = None
        if active_barrier:
            issues.append("PURGE_BARRIER_UNRESOLVED")
        try:
            journal_sequence, journal_hash = self.independent_purge_journal.verified_head()
            if not self.independent_purge_journal.path.is_file():
                issues.append("PURGE_JOURNAL_MISSING")
            if watermark and tuple(watermark) != (self.independent_purge_journal.identity, journal_sequence, journal_hash):
                issues.append("PURGE_JOURNAL_WATERMARK_MISMATCH")
            elif not watermark and (journal_sequence != 0 or any(
                self._table_has_rows(table) for table in ("purge_barriers", "purge_ledger", "purge_execution_records")
            )):
                issues.append("PURGE_HISTORY_WITHOUT_WATERMARK")
        except Exception:
            issues.append("PURGE_JOURNAL_UNREADABLE")
        for row in available_objects:
            try:
                self.verify_object(row["object_id"])
            except Exception:
                issues.append("OBJECT_PAYLOAD_INTEGRITY_FAILED")
                break
        authority = AuthorityService(self, proposed_policy)
        for row in active_grants:
            try:
                authority.validate_delegation_chain(row["grant_id"])
            except Exception:
                issues.append("ACTIVE_GRANT_CHAIN_INVALID")
                break
        unique_issues = sorted(set(issues))
        return {
            "compatible": not unique_issues,
            "policy_version": policy_version,
            "policy_sha256": self.policy_sha256,
            "trust_anchor_count": len(anchors),
            "active_grant_count": len(active_grants),
            "available_object_count": len(available_objects),
            "issues": unique_issues,
        }

    def _table_has_rows(self, table: str) -> bool:
        with self._connection() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return bool(exists and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone())

    def _acquire_writer_lock(self, *, read_only: bool = False) -> None:
        lock_path = self.data_root / "nexus.writer.lock"
        if read_only:
            if not lock_path.is_file() or lock_path.is_symlink():
                raise MigrationError("NEXUS_WRITER_LOCK_MISSING_OR_INVALID")
            try:
                self._writer_lock_file = lock_path.open("rb")
            except OSError as exc:
                raise MigrationError("NEXUS_WRITER_LOCK_UNAVAILABLE") from exc
            self._writer_lock_file.seek(0, os.SEEK_END)
            if self._writer_lock_file.tell() < 1:
                self._writer_lock_file.close()
                self._writer_lock_file = None
                raise MigrationError("NEXUS_WRITER_LOCK_MISSING_OR_INVALID")
        else:
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

                lock_kind = fcntl.LOCK_SH if read_only else fcntl.LOCK_EX
                fcntl.flock(self._writer_lock_file.fileno(), lock_kind | fcntl.LOCK_NB)
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
        if getattr(self, "read_only", False):
            conn = sqlite3.connect(
                self._sqlite_readonly_uri(self.database_path, immutable=True),
                uri=True, timeout=10.0, isolation_level=None,
            )
        else:
            conn = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if getattr(self, "read_only", False):
            conn.execute("PRAGMA query_only = ON")
        else:
            conn.execute("PRAGMA synchronous = FULL")
        conn.create_function("nexus_sha256", 1, lambda value: _sha256(str(value).encode("utf-8")))
        conn.create_function(
            "nexus_purge_redaction",
            0,
            lambda: int(getattr(self._purge_redaction_local, "depth", 0) > 0),
        )
        conn.create_function("nexus_exact_purge_json", 2, self._exact_purge_json)
        conn.create_function("nexus_redact_purge_json", 1, self._redact_purge_json)
        conn.create_function("nexus_purge_scope_json", 1, self._purge_scope_json)
        conn.create_function("nexus_effect_purge_json", 5, self._effect_purge_json)
        conn.create_function("nexus_redact_effect_json", 4, self._redact_effect_json)
        conn.create_function("nexus_effect_receipt_cleanup_json", 2, self._effect_receipt_cleanup_json)
        conn.set_authorizer(self._sqlite_authorizer)
        return conn

    @contextmanager
    def _allow_purge_redaction(self, object_ids=()):
        """Permit only the purge service's guarded one-way redaction triggers."""
        self._purge_redaction_local.depth = getattr(self._purge_redaction_local, "depth", 0) + 1
        previous = getattr(self._purge_redaction_local, "object_ids", frozenset())
        self._purge_redaction_local.object_ids = frozenset(previous) | frozenset(object_ids)
        try:
            yield
        finally:
            self._purge_redaction_local.object_ids = previous
            self._purge_redaction_local.depth -= 1

    def _exact_purge_json(self, old_json, new_json):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return 0
        try:
            old = json.loads(old_json)
            new = json.loads(new_json)
            refs = getattr(self._purge_redaction_local, "object_ids", frozenset())
            return int(_canonical_json(redact_governed_object_values(old, set(refs))) == _canonical_json(new))
        except (TypeError, ValueError):
            return 0

    def _redact_purge_json(self, value):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return value
        try:
            refs = set(getattr(self._purge_redaction_local, "object_ids", frozenset()))
            return _canonical_json(redact_governed_object_values(json.loads(value), refs))
        except (TypeError, ValueError):
            return value

    def _purge_scope_json(self, value):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return value
        try:
            refs = set(getattr(self._purge_redaction_local, "object_ids", frozenset()))
            scope = json.loads(value)
            if not isinstance(scope, list):
                return value
            return _canonical_json([
                item for item in scope
                if not (isinstance(item, str) and (item in refs or item.startswith("object:") and item[7:] in refs))
            ])
        except (TypeError, ValueError):
            return value

    def _effect_purge_json(self, old_json, new_json, effect_id, payload_object_ref, target_ref):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return 0
        try:
            expected = self._effect_purge_projection(old_json, effect_id, payload_object_ref, target_ref)
            return int(expected == _canonical_json(json.loads(new_json)))
        except (TypeError, ValueError):
            return 0

    def _effect_receipt_cleanup_json(self, old_json, new_json):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return 0
        try:
            old = json.loads(old_json)
            new = json.loads(new_json)
            if not isinstance(old, dict) or "external_receipt_ref" not in old:
                return 0
            old.pop("external_receipt_ref")
            return int(_canonical_json(old) == _canonical_json(new))
        except (TypeError, ValueError):
            return 0

    def _redact_effect_json(self, old_json, effect_id, payload_object_ref, target_ref):
        if getattr(self._purge_redaction_local, "depth", 0) <= 0:
            return old_json
        try:
            return self._effect_purge_projection(old_json, effect_id, payload_object_ref, target_ref)
        except (TypeError, ValueError):
            return old_json

    def _effect_purge_projection(self, old_json, effect_id, payload_object_ref, target_ref):
        refs = set(getattr(self._purge_redaction_local, "object_ids", frozenset()))
        old = redact_governed_object_values(json.loads(old_json), refs)
        payload_purged = payload_object_ref in refs
        target_value = target_ref[7:] if isinstance(target_ref, str) and target_ref.startswith("object:") else target_ref
        target_purged = target_value in refs
        if payload_purged or target_purged:
            old["target_ref"] = "REDACTED_PURGED"
        if payload_purged:
            old["payload_integrity_hash"] = "0" * 64
            old["idempotency_key"] = "REDACTED_PURGED:" + effect_id
        if payload_purged or target_purged:
            old.pop("external_receipt_ref", None)
        return _canonical_json(old)

    def _sqlite_authorizer(self, action: int, arg1: str | None, arg2: str | None, database: str | None, source: str | None) -> int:
        if getattr(self, "read_only", False):
            mutation_actions = {
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
                sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
                sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX,
                sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW,
                sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
            }
            if action in mutation_actions or action == sqlite3.SQLITE_PRAGMA and arg2 is not None:
                return sqlite3.SQLITE_DENY
        if self._instance_restricted:
            if getattr(self._bootstrap_schema_write_local, "depth", 0) > 0:
                return sqlite3.SQLITE_OK
            if getattr(self._binding_write_local, "depth", 0) > 0:
                if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                              sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}:
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_INSERT and arg1 == "instance_policy_binding":
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if self._mode_cache != "RECOVERY" or getattr(self._recovery_local, "depth", 0) > 0:
            if self._mode_cache not in {"SAFE", "STATELESS"}:
                return sqlite3.SQLITE_OK
            is_memory_table = bool(arg1 and (arg1 in _MEMORY_TABLES or arg1.startswith(_MEMORY_INDEX_PREFIXES)))
            is_memory_write = action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            if is_memory_table and (is_memory_write or self._mode_cache == "STATELESS" and action == sqlite3.SQLITE_READ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and arg1 in {"runtime_mode_state", "instance_policy_binding", "sqlite_master"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _detect_startup_mode(self) -> str:
        if not self.database_path.exists():
            return "NORMAL"
        try:
            with closing(sqlite3.connect(
                self._sqlite_readonly_uri(self.database_path, immutable=getattr(self, "read_only", False)), uri=True
            )) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_mode_state'").fetchone()
                if not exists:
                    return "NORMAL"
                row = conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise MigrationError("Cannot safely read Runtime mode during startup.") from exc
        if not row or row[0] not in {"NORMAL", "SAFE", "STATELESS", "RECOVERY"}:
            raise MigrationError("Runtime mode state is corrupt; startup denied.")
        return row[0]

    @staticmethod
    def _sqlite_readonly_uri(path: Path, *, immutable: bool = False) -> str:
        query = "mode=ro&immutable=1" if immutable else "mode=ro"
        return path.resolve().as_uri() + "?" + query

    def _has_migration_table(self) -> bool:
        with closing(sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)) as conn:
            return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone())

    def _journal_startup_state(self) -> str:
        """Return current, bootstrap, or recovery based on the external journal head."""
        if not self.database_path.is_file():
            try:
                records = self.independent_purge_journal.read()
                if records:
                    return "recovery"
                self.independent_purge_journal.ensure_empty_exists()
                self._journal_bootstrap_needed = True
                return "bootstrap"
            except Exception:
                return "recovery"
        try:
            with closing(sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                watermark = None
                if "independent_purge_journal_watermark" in tables:
                    watermark = conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
                historical_purge = False
                for table, query in (
                    ("purge_barriers", "SELECT 1 FROM purge_barriers LIMIT 1"),
                    ("purge_ledger", "SELECT 1 FROM purge_ledger LIMIT 1"),
                    ("purge_execution_records", "SELECT 1 FROM purge_execution_records LIMIT 1"),
                ):
                    if table in tables and conn.execute(query).fetchone():
                        historical_purge = True
                        break
            journal_exists = self.independent_purge_journal.path.is_file()
            records = self.independent_purge_journal.read()
            sequence = len(records)
            record_hash = records[-1]["record_hash"] if records else "0" * 64
            if watermark is None:
                if records or historical_purge or not journal_exists:
                    return "recovery"
                self._journal_bootstrap_needed = True
                return "bootstrap"
            if not journal_exists:
                return "recovery"
            if watermark[0] != self.independent_purge_journal.identity:
                return "recovery"
            if sequence != watermark[1] or record_hash != watermark[2]:
                return "recovery"
            return "current"
        except Exception:
            return "recovery"

    def _bootstrap_or_validate_journal_watermark(self) -> None:
        if not self.independent_purge_journal.path.is_file():
            raise MigrationError("Configured independent purge journal disappeared during startup.")
        identity = self.independent_purge_journal.identity
        sequence, record_hash = self.independent_purge_journal.verified_head()
        with self._connection() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='independent_purge_journal_watermark'").fetchone():
                # Explicit historical migration-prefix fixtures stop before v19.
                return
            row = conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
            if row:
                if (row[0], row[1], row[2]) != (identity, sequence, record_hash):
                    raise MigrationError("Independent purge journal watermark changed during startup.")
                return
            if sequence != 0 or not self._journal_bootstrap_needed:
                raise MigrationError("Missing purge journal watermark cannot acknowledge journal history.")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO independent_purge_journal_watermark(singleton,journal_identity,sequence,record_hash,acknowledged_at) VALUES(1,?,0,?,?)",
                (identity, record_hash, _utc_now()),
            )
            conn.commit()
        self._journal_bootstrap_needed = False

    def _acknowledge_purge_journal_head(self, conn, *, identity: str, sequence: int, record_hash: str) -> None:
        if not self.independent_purge_journal.path.is_file():
            raise MigrationError("Configured independent purge journal is missing during acknowledgement.")
        if identity != self.independent_purge_journal.identity:
            raise MigrationError("Configured independent purge journal identity changed during recovery.")
        current_sequence, current_hash = self.independent_purge_journal.verified_head()
        if (sequence, record_hash) != (current_sequence, current_hash):
            raise MigrationError("Independent purge journal advanced during recovery validation.")
        prior = conn.execute("SELECT 1 FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
        if prior is None and sequence == 0:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("purge_barriers", "purge_ledger", "purge_execution_records"):
                if table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise MigrationError("Empty journal cannot acknowledge historical purge facts without a prior watermark.")
        conn.execute(
            "INSERT INTO independent_purge_journal_watermark(singleton,journal_identity,sequence,record_hash,acknowledged_at) VALUES(1,?,?,?,?) "
            "ON CONFLICT(singleton) DO UPDATE SET sequence=excluded.sequence,record_hash=excluded.record_hash,acknowledged_at=excluded.acknowledged_at",
            (identity, sequence, record_hash, _utc_now()),
        )

    def _open_recovery_session(self, *, source_mode: str = "FORCED_RECOVERY") -> None:
        """Persist an infrastructure recovery session without user authority fiction."""
        from uuid import uuid4
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = None
            if source_mode == "FORCED_LEGACY_UNBOUND":
                existing = conn.execute(
                    "SELECT session_id FROM recovery_sessions WHERE source_mode=? AND status='OPEN' ORDER BY opened_at DESC LIMIT 1",
                    (source_mode,),
                ).fetchone()
            elif source_mode == "FORCED_RECOVERY":
                # A crash after migration but before recovery completion must
                # resume the legacy provenance session rather than replacing
                # it with a generic Recovery event.
                existing = conn.execute(
                    "SELECT session_id FROM recovery_sessions WHERE source_mode='FORCED_LEGACY_UNBOUND' AND status='OPEN' ORDER BY opened_at DESC LIMIT 1"
                ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO recovery_sessions(session_id,opened_at,source_mode,status) VALUES(?,?,?,'OPEN')",
                    ("recovery-session-" + str(uuid4()), _utc_now(), source_mode),
                )
            conn.execute(
                "UPDATE runtime_mode_state SET mode='RECOVERY',updated_at=?,updated_by='nexus-core-recovery',command_id=NULL WHERE singleton=1",
                (_utc_now(),),
            )
            conn.commit()
        self._mode_cache = "RECOVERY"

    def _recovery_session_source_exists(self, source_mode: str, *, status: str | None = None) -> bool:
        if source_mode != "FORCED_LEGACY_UNBOUND" or status not in {None, "OPEN", "COMPLETED"}:
            return False
        version = self._database_schema_version()
        if version is None or version < _RECOVERY_SESSIONS_MIGRATION:
            return False
        try:
            maintenance = self._recovery_maintenance() if self._mode_cache == "RECOVERY" else nullcontext()
            with maintenance:
                with self._connection() as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_sessions'"
                    ).fetchone()
                    if not exists:
                        return False
                    if status is None:
                        row = conn.execute(
                            "SELECT 1 FROM recovery_sessions WHERE source_mode=? AND status IN ('OPEN','COMPLETED') LIMIT 1",
                            (source_mode,),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT 1 FROM recovery_sessions WHERE source_mode=? AND status=? LIMIT 1",
                            (source_mode, status),
                        ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise MigrationError("RECOVERY_PROVENANCE_UNREADABLE") from exc
        return bool(row)

    def _has_forced_legacy_recovery_provenance(self) -> bool:
        return self._recovery_session_source_exists("FORCED_LEGACY_UNBOUND")

    def _has_open_forced_legacy_recovery_session(self) -> bool:
        return self._recovery_session_source_exists("FORCED_LEGACY_UNBOUND", status="OPEN")

    def _is_provable_prebinding_legacy(self) -> bool:
        """Classify only a checksum-validated pre-0028 unbound root for Recovery provenance."""
        version = self._database_schema_version()
        if (
            version is None
            or version < _RECOVERY_SESSIONS_MIGRATION
            or version >= _INSTANCE_BINDING_MIGRATION
            or self._intent_exists("FRESH_INITIALIZE")
            or self._intent_exists("LEGACY_OPERATOR_ADOPTION")
        ):
            return False
        return self._read_instance_binding() is None

    def _read_persisted_mode(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()
        if not row or row[0] not in {"NORMAL", "SAFE", "STATELESS", "RECOVERY"}:
            raise MigrationError("Runtime mode state is corrupt; startup denied.")
        return row[0]

    def _validate_recovery_database(self) -> None:
        """Read-only compatibility check; Recovery startup never applies migrations or cleans files."""
        self._validate_database_read_only(require_current=False)

    def _validate_database_read_only(self, *, require_current: bool) -> None:
        """Validate schema history through a SQLite read-only connection."""
        try:
            migrations = {path.name: path.read_bytes() for path in self.migrations_dir.glob("*.sql")}
            with closing(sqlite3.connect(
                self._sqlite_readonly_uri(self.database_path, immutable=getattr(self, "read_only", False)), uri=True
            )) as conn:
                rows = conn.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version").fetchall()
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        except (OSError, sqlite3.DatabaseError) as exc:
            if require_current:
                raise MigrationError("READ_ONLY_DATABASE_INVALID") from exc
            raise
        if integrity != "ok":
            raise MigrationError("Recovery database integrity check failed.")
        if user_version != len(rows):
            raise MigrationError("Recovery database migration state is inconsistent.")
        for version, name, checksum in rows:
            if name not in migrations or classify_migration_checksum(migrations[name], checksum) == "MISMATCH":
                raise MigrationError("MIGRATION_CHECKSUM_MISMATCH")
        if require_current and (
            user_version != len(migrations)
            or [row[0] for row in rows] != list(range(1, len(migrations) + 1))
        ):
            raise MigrationError("READ_ONLY_SCHEMA_NOT_CURRENT")

    def _assert_writable(self) -> None:
        if getattr(self, "read_only", False):
            raise MigrationError("READ_ONLY_STORE_MUTATION_DENIED")

    def _guard_read_only_capability(self, capability: str) -> None:
        if capability in _READ_ONLY_MUTATION_CAPABILITIES:
            self._assert_writable()

    @contextmanager
    def _recovery_maintenance(self):
        if self._mode_cache != "RECOVERY":
            raise MigrationError("Recovery maintenance is available only in RECOVERY mode.")
        self._recovery_local.depth = getattr(self._recovery_local, "depth", 0) + 1
        try:
            yield
        finally:
            self._recovery_local.depth -= 1

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _current_runtime_mode(self) -> str:
        """Read persisted mode without exposing the raw setting to clients."""
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()
        except sqlite3.OperationalError as exc:
            raise MigrationError("Runtime mode state could not be read safely.") from exc
        if not row or row["mode"] not in {"NORMAL", "SAFE", "STATELESS", "RECOVERY"}:
            raise MigrationError("Runtime mode state is missing or invalid.")
        return row["mode"]

    def _require_mode(self, capability: str) -> None:
        self._guard_read_only_capability(capability)
        if self._instance_restricted and capability != "core_read":
            raise MigrationError("POLICY_BINDING_ADOPTION_REQUIRED")
        from kernel.runtime.modes import require_mode_permission

        require_mode_permission(self._current_runtime_mode(), capability)

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
                    if applied["name"] != path.name or classify_migration_checksum(sql_bytes, applied["checksum"]) == "MISMATCH":
                        raise MigrationError("MIGRATION_CHECKSUM_MISMATCH")
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
                rebuilding_tables = version in {17, 27}
                try:
                    if rebuilding_tables:
                        conn.execute("PRAGMA foreign_keys = OFF")
                        conn.execute("PRAGMA legacy_alter_table = ON")
                    redaction_scope = nullcontext()
                    if version in {21, 22}:
                        purged_ids = tuple(row[0] for row in conn.execute(
                            "SELECT object_id FROM object_states WHERE payload_state='PURGED'"
                        ))
                        redaction_scope = self._allow_purge_redaction(purged_ids)
                    with redaction_scope:
                        conn.executescript(script)
                    if rebuilding_tables:
                        conn.execute("PRAGMA legacy_alter_table = OFF")
                        conn.execute("PRAGMA foreign_keys = ON")
                        if version == 17:
                            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                        else:
                            violations = [
                                violation
                                for table in ("skill_registry_entries", "skill_resolution_records")
                                for violation in conn.execute(f"PRAGMA foreign_key_check({table})")
                            ]
                        if violations:
                            raise MigrationError("Purge redaction migration produced a foreign-key violation.")
                except Exception:
                    if conn.in_transaction:
                        conn.rollback()
                    if rebuilding_tables:
                        conn.execute("PRAGMA legacy_alter_table = OFF")
                        conn.execute("PRAGMA foreign_keys = ON")
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
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise SchemaUnsupported("SCHEMA_UNSUPPORTED") from exc
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

    def bind_command_request(self, *, command_id: str, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        """Durably bind a command identity to a request without storing the request.

        This narrow CommandLedger primitive is for multi-stage mutations whose
        later stages cannot otherwise detect a retargeted request after an
        earlier stage has committed. Only the request hash and a fixed safe
        result are persisted.
        """
        self._require_mode("core_write")
        if not isinstance(operation, str) or not operation or len(operation) > 64:
            raise ValueError("COMMAND_OPERATION_INVALID")
        if not isinstance(request, dict):
            raise ValueError("COMMAND_REQUEST_INVALID")
        try:
            request_hash = self._request_hash(operation, request)
        except (TypeError, ValueError) as exc:
            raise ValueError("COMMAND_REQUEST_INVALID") from exc
        result = {"status": "REQUEST_BOUND"}
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def _replay_command(
        self, conn: sqlite3.Connection, command_id: str, operation: str, request_hash: str
    ) -> dict[str, Any] | None:
        row = conn.execute("SELECT operation,request_hash,result_json,result_state FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()
        if not row:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise CommandConflict("COMMAND_CONFLICT")
        # Purge redacts the persisted result projection while preserving the
        # committed command identity and immutable result commitment.
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
        result_json = _canonical_json(result)
        conn.execute(
            "INSERT INTO command_ledger(command_id,operation,request_hash,result_json,status,created_at,result_commitment,result_state) "
            "VALUES(?,?,?,?,?,?,?,'LIVE')",
            (command_id, operation, request_hash, result_json, "SUCCEEDED", created_at, _sha256(result_json.encode("utf-8"))),
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
        row = conn.execute(
            "SELECT 1 FROM object_states WHERE payload_state='PURGED' "
            f"AND object_id IN ({placeholders}) LIMIT 1",
            ids,
        ).fetchone()
        if row:
            raise PurgedObject("PURGED_OBJECT_REFERENCE_DENIED")

    @staticmethod
    def _assert_unbarred_object_resources(conn: sqlite3.Connection, resources: Iterable[str | None]) -> None:
        """Guard exact object IDs/canonical object: IDs without guessing strings."""
        existing = {
            object_id for resource in resources
            if (object_id := resolve_governed_object_resource(conn, resource)) is not None
        }
        ObjectStore._assert_unbarred(conn, existing)

    @staticmethod
    def _assert_readable(conn: sqlite3.Connection, object_ids: Iterable[str]) -> None:
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
        self._require_mode("core_write")
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("object_id must be a non-empty caller-generated identifier")
        digest = _sha256(payload)
        governed_inputs: list[str] = []
        if object_type in {"run_manifest", "task_contract"}:
            try:
                governed_doc = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrityMismatch("GOVERNED_OBJECT_PAYLOAD_INVALID") from exc
            schema_id = governed_doc.get("schema_id")
            schema_version = governed_doc.get("schema_version")
            if object_type == "run_manifest" and schema_id == "nexus.run_manifest":
                self._validate(f"nexus.run_manifest@{schema_version}.schema.json", governed_doc)
            elif object_type == "task_contract" and schema_id == "nexus.task_contract":
                self._validate(f"nexus.task_contract@{schema_version}.schema.json", governed_doc)
            else:
                raise IntegrityMismatch("GOVERNED_OBJECT_SCHEMA_MISMATCH")
            governed_inputs = known_object_refs(governed_doc)
        explicit_sources = sorted(set(derived_from))
        source_ids = sorted(set(explicit_sources) | set(governed_inputs))
        request_base = {
            "payload_integrity_hash": digest,
            "object_id": object_id,
            "object_type": object_type,
            "created_by_run": created_by_run,
            "classification_assertion_ref": classification_assertion_ref,
        }
        operation = "put_object"
        request_hash = self._request_hash(operation, {**request_base, "derived_from": source_ids})
        compatible_hashes = {request_hash}
        if object_type == "task_contract":
            compatible_hashes.add(self._request_hash(operation, {**request_base, "derived_from": explicit_sources}))
        elif object_type == "run_manifest":
            legacy_inputs = governed_doc.get("input_object_refs", [])
            if not isinstance(legacy_inputs, list):
                legacy_inputs = []
            legacy_sources = sorted(set(explicit_sources) | {item for item in legacy_inputs if isinstance(item, str) and item})
            compatible_hashes.add(self._request_hash(operation, {**request_base, "derived_from": legacy_sources}))
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = conn.execute(
                    "SELECT operation,request_hash,result_json FROM command_ledger WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                if prior:
                    if prior["operation"] != operation or prior["request_hash"] not in compatible_hashes:
                        raise CommandConflict("COMMAND_CONFLICT")
                    replay = json.loads(prior["result_json"])
                    if replay.get("object_id") not in {object_id, "REDACTED_PURGED"}:
                        raise CommandConflict("COMMAND_CONFLICT")
                    projection = conn.execute(
                        "SELECT e.object_type,e.integrity_hash,e.created_by_run,e.classification_assertion_ref,s.payload_state "
                        "FROM objects o JOIN object_states s USING(object_id) LEFT JOIN object_envelopes e USING(object_id) "
                        "WHERE o.object_id=?",
                        (object_id,),
                    ).fetchone()
                    if not projection:
                        raise CommandConflict("COMMAND_CONFLICT")
                    if projection["payload_state"] == "PURGED":
                        result = "REDACTED_PURGED"
                    elif (projection["object_type"] != object_type or projection["integrity_hash"] != digest
                          or projection["created_by_run"] != created_by_run
                          or projection["classification_assertion_ref"] != classification_assertion_ref):
                        raise CommandConflict("COMMAND_CONFLICT")
                    else:
                        linked = {row[0] for row in conn.execute(
                            "SELECT to_id FROM object_relations WHERE from_id=? AND relation_type='derived_from'",
                            (object_id,),
                        )}
                        if not set(explicit_sources).issubset(linked):
                            raise CommandConflict("COMMAND_CONFLICT")
                        result = object_id
                    conn.commit()
                    return result
                if conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone():
                    raise CommandConflict("OBJECT_ID_ALREADY_EXISTS")
                self._assert_unbarred(conn, source_ids)
                if explicit_sources:
                    placeholders = ",".join("?" for _ in explicit_sources)
                    found = {row[0] for row in conn.execute(
                        f"SELECT object_id FROM objects WHERE object_id IN ({placeholders})", explicit_sources
                    )}
                    if found != set(explicit_sources):
                        raise ObjectNotFound("LINEAGE_SOURCE_NOT_FOUND")
                # A MODEL/TOOL manifest may declare its route before the route
                # artifact is persisted. Keep the caller request hash stable,
                # but create the route lineage edge atomically when the object
                # already exists; the Host must bind a later route before READY.
                lineage_sources = [source_id for source_id in source_ids if conn.execute(
                    "SELECT 1 FROM objects WHERE object_id=?", (source_id,)
                ).fetchone()]
                manifest_inputs = [ref for ref in governed_inputs if ref in lineage_sources]
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
                for source_id in lineage_sources:
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
                    for source_id in lineage_sources
                ]
                for source_id, relation in zip(lineage_sources, relations):
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
                for source_id in lineage_sources:
                    conn.execute("INSERT INTO object_relations(from_id,relation_type,to_id) VALUES(?,?,?)", (object_id, "derived_from", source_id))
                if object_type == "run_manifest":
                    conn.executemany("INSERT OR IGNORE INTO run_manifest_inputs(run_id,manifest_object_id,input_object_id) VALUES(?,?,?)", ((created_by_run, object_id, input_id) for input_id in manifest_inputs))
                result = {"object_id": object_id}
                self._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
                return object_id
            except Exception:
                conn.rollback()
                raise

    def add_relation(self, *, command_id: str, from_id: str, relation_type: str, to_id: str) -> dict[str, str]:
        self._require_mode("core_write")
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
        self._require_mode("core_write")
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
        self._require_mode("core_write")
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
        self._require_mode("core_write")
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
        self._require_mode("core_read")
        with self._connection() as conn:
            self._assert_readable(conn, (object_id,))
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
            self._assert_readable(conn, [row[0] for row in sources] + [row[0] for row in descendants])
        return {"sources": [row[0] for row in sources], "derived": [row[0] for row in descendants]}

    def get_object_metadata(self, object_id: str) -> dict[str, Any]:
        self._require_mode("core_read")
        with self._connection() as conn:
            self._assert_readable(conn, (object_id,))
            row = conn.execute(
                "SELECT e.*,s.revision,s.lifecycle,s.validity,s.payload_state FROM objects o "
                "LEFT JOIN object_envelopes e USING(object_id) JOIN object_states s USING(object_id) WHERE o.object_id=?",
                (object_id,),
            ).fetchone()
        if not row:
            raise ObjectNotFound("OBJECT_NOT_FOUND")
        if row["payload_state"] == "PURGED" or row["object_type"] is None:
            return {"payload_state": "PURGED"}
        return dict(row)

    def get_payload(self, object_id: str) -> bytes:
        self._require_mode("core_read")
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
        self._require_mode("core_read")
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
