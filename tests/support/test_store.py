"""Test-only helper that explicitly exercises the supported fresh bootstrap path."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from contextlib import closing
from uuid import uuid4

from adapters.bootstrap.service import initialize_instance
from adapters.storage import ObjectStore
from kernel.purge.journal import IndependentPurgeJournal
from jsonschema import FormatChecker


def _policy_path(root: Path) -> Path:
    return root.parent / f".{root.name}.test-policy.json"


def open_test_store(data_root, *, policy=None, independent_purge_journal_path=None, migrations_dir=None):
    root = Path(data_root).resolve()
    policy_path = _policy_path(root)
    if policy is None:
        if policy_path.is_file():
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        else:
            repository = Path(__file__).resolve().parents[2]
            policy = json.loads((repository / "policies" / "default-policy.json").read_text(encoding="utf-8"))
            policy["trust_anchors"] = ["human-root"]
    policy = json.loads(json.dumps(policy))
    if len(policy.get("trust_anchors", [])) != 1:
        raise AssertionError("test fresh-instance policy must name exactly one explicit trust anchor")
    if not policy_path.exists():
        policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    elif json.loads(policy_path.read_text(encoding="utf-8")) != policy:
        raise AssertionError("test root reused with a different policy")

    journal = Path(independent_purge_journal_path) if independent_purge_journal_path else root.parent / f"{root.name}.purge-journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    is_legacy_fixture = (root / "nexus.sqlite").is_file() and (
        _schema_version(root) < 28 or not _has_binding(root)
    )
    if migrations_dir is not None or is_legacy_fixture:
        return open_legacy_fixture_store(
            root, policy=policy,
            independent_purge_journal_path=independent_purge_journal_path,
            migrations_dir=migrations_dir,
        )
    if not (root / "nexus.sqlite").is_file():
        initialize_instance(
            data_root=root,
            policy_path=policy_path,
            independent_purge_journal=journal,
            command_id="test-init-" + uuid4().hex,
        )
    return ObjectStore(root, policy=policy, migrations_dir=migrations_dir,
                       independent_purge_journal_path=journal)


def _schema_version(root: Path) -> int:
    with closing(sqlite3.connect(root / "nexus.sqlite")) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _has_binding(root: Path) -> bool:
    with closing(sqlite3.connect(root / "nexus.sqlite")) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instance_policy_binding'"
        ).fetchone()
        return bool(table and conn.execute("SELECT 1 FROM instance_policy_binding WHERE singleton=1").fetchone())


def open_legacy_fixture_store(data_root, *, policy=None, independent_purge_journal_path=None, migrations_dir=None):
    """Apply migrations to an explicitly test-only unbound historical database.

    This bypasses the production constructor by design: migration tests need
    to inspect legacy schemas without silently adopting the current policy.
    Never use this helper from application code.
    """
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).resolve().parents[2]
    schema_dir = repository / "schemas"
    migration_root = Path(migrations_dir).resolve() if migrations_dir else repository / "migrations"
    journal_path = Path(independent_purge_journal_path) if independent_purge_journal_path else root.parent / (root.name + ".purge-journal.jsonl")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    store = ObjectStore.__new__(ObjectStore)
    store.data_root = root
    store.database_path = root / "nexus.sqlite"
    store.blob_root = root / "objects" / "sha256"
    store.schema_dir = schema_dir
    store.migrations_dir = migration_root
    store.policy = policy or {}
    store._format_checker = FormatChecker()
    store._lock = threading.RLock()
    store._writer_lock_file = None
    store._closed = False
    store._purge_redaction_local = threading.local()
    store._binding_write_local = threading.local()
    store._bootstrap_schema_write_local = threading.local()
    store._recovery_local = threading.local()
    store._schemas = {}
    store._journal_bootstrap_needed = True
    store._force_recovery = False
    store._mode_cache = "NORMAL"
    store._instance_restricted = False
    store.instance_binding_status = "UNVERIFIED"
    store.instance_binding = None
    store.startup_purpose = None
    store._configured_journal_path = journal_path.resolve()
    store._journal_identity_expected = store._journal_identity_for_path(store._configured_journal_path)
    store.independent_purge_journal_path = store._configured_journal_path
    store.independent_purge_journal = IndependentPurgeJournal(store._configured_journal_path, root)
    if not store.independent_purge_journal.path.exists():
        store.independent_purge_journal.ensure_empty_exists()
    else:
        store.independent_purge_journal.read()
    store._acquire_writer_lock()
    try:
        store._initialize_database()
        with store._connection() as conn:
            has_mode = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_mode_state'").fetchone()
            has_recovery_sessions = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_sessions'").fetchone()
            has_watermark = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='independent_purge_journal_watermark'").fetchone()
            has_purge_history = any(
                table in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in ("purge_barriers", "purge_ledger", "purge_execution_records")
            )
        store._mode_cache = store._read_persisted_mode() if has_mode else "NORMAL"
        journal_records = store.independent_purge_journal.read()
        if has_watermark and not has_purge_history and not journal_records:
            store._bootstrap_or_validate_journal_watermark()
        elif (has_purge_history or journal_records) and has_recovery_sessions:
            store._mode_cache = "RECOVERY"
            with store._recovery_maintenance():
                store._open_recovery_session(source_mode="TEST_LEGACY_FIXTURE")
        store._create_object_directories()
        return store
    except Exception:
        store.close()
        raise
