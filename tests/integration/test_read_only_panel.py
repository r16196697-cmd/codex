from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from adapters.panel.application import open_panel_application
from adapters.storage import ObjectStore
from kernel.object.errors import MigrationError, WriterAlreadyRunning
from kernel.purge.journal import IndependentPurgeJournal
from tests.support.test_store import open_test_store


def _filesystem_snapshot(data_root: Path, journal_parent: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for label, root in (("data", data_root), ("journal", journal_parent)):
        if not root.exists():
            result[label + "/<missing-root>"] = "MISSING"
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            result[f"{label}/{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _read_only_connection(database: Path):
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


class ReadOnlyPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-read-only-panel-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "data"
        self.journal_dir = base / "journal"
        self.journal = self.journal_dir / "independent.jsonl"
        self.store = open_test_store(self.root, independent_purge_journal_path=self.journal)
        self.addCleanup(self.store.close)
        self.policy_path = base / ".data.test-policy.json"
        self.store.close()

    def _open_read_only(self, *, policy_path=None, journal_path=None):
        return open_panel_application(
            self.root,
            policy_path=policy_path or self.policy_path,
            independent_purge_journal_path=journal_path or self.journal,
            read_only=True,
        )

    def test_bound_normal_instance_opens_panel_read_only_and_snapshot_succeeds(self):
        application = self._open_read_only()
        try:
            snapshot = application.view_model.snapshot()
            self.assertEqual(application.store.instance_binding_status, "BOUND")
            self.assertTrue(application.view_model.read_only)
            self.assertEqual(snapshot["runtime_mode"], "NORMAL")
            self.assertEqual(snapshot["participation_mode"], "ACTIVE")
            self.assertEqual(snapshot["overview"]["task_count"], 0)
            self.assertEqual(snapshot["memory"]["admitted_count"], 0)
            self.assertEqual(snapshot["context_status"]["status"], "NOT COMPILED")
        finally:
            application.close()

    def test_read_only_panel_snapshot_has_zero_filesystem_delta(self):
        before = _filesystem_snapshot(self.root, self.journal_dir)
        app = self._open_read_only()
        try:
            app.view_model.snapshot()
        finally:
            app.close()
        after = _filesystem_snapshot(self.root, self.journal_dir)
        self.assertEqual(after, before)
        self.assertNotIn("data/nexus.sqlite-wal", after)
        self.assertNotIn("data/nexus.sqlite-shm", after)

    def test_read_only_open_does_not_initialize_or_cleanup(self):
        blob_dir = self.root / "objects" / "sha256" / "aa"
        blob_dir.mkdir(parents=True, exist_ok=True)
        orphan = blob_dir / "orphan-payload"
        temporary = blob_dir / ".tmp-orphan-payload"
        orphan.write_bytes(b"orphan")
        temporary.write_bytes(b"temporary")
        before = _filesystem_snapshot(self.root, self.journal_dir)
        with mock.patch.object(ObjectStore, "_initialize_database", side_effect=AssertionError("migration/init called")), \
             mock.patch.object(ObjectStore, "_create_object_directories", side_effect=AssertionError("directory creation called")), \
             mock.patch.object(ObjectStore, "_cleanup_orphan_payloads", side_effect=AssertionError("cleanup called")):
            app = self._open_read_only()
            try:
                app.view_model.snapshot()
            finally:
                app.close()
        self.assertTrue(orphan.is_file())
        self.assertTrue(temporary.is_file())
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_read_only_journal_never_creates_lock_sidecar(self):
        sidecar = self.journal.with_name(self.journal.name + ".lock")
        if sidecar.exists():
            sidecar.unlink()
        before = _filesystem_snapshot(self.root, self.journal_dir)
        app = self._open_read_only()
        try:
            app.view_model.snapshot()
        finally:
            app.close()
        self.assertFalse(sidecar.exists())
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_missing_writer_lock_fails_without_creating_it(self):
        lock_path = self.root / "nexus.writer.lock"
        lock_path.unlink()
        before = _filesystem_snapshot(self.root, self.journal_dir)
        with self.assertRaisesRegex(MigrationError, "NEXUS_WRITER_LOCK_MISSING_OR_INVALID"):
            self._open_read_only()
        self.assertFalse(lock_path.exists())
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_policy_mismatch_fails_closed_without_filesystem_delta(self):
        bad_policy_path = Path(self.temp.name) / "different-policy.json"
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy["trust_anchors"] = ["different-human"]
        bad_policy_path.write_text(json.dumps(policy), encoding="utf-8")
        before = _filesystem_snapshot(self.root, self.journal_dir)
        with self.assertRaisesRegex(MigrationError, "POLICY_BINDING_MISMATCH"):
            self._open_read_only(policy_path=bad_policy_path)
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_journal_identity_mismatch_fails_closed_without_filesystem_delta(self):
        other_journal_dir = Path(self.temp.name) / "other-journal"
        other_journal = other_journal_dir / "independent.jsonl"
        journal = IndependentPurgeJournal(other_journal, self.root)
        journal.ensure_empty_exists()
        before_data = _filesystem_snapshot(self.root, self.journal_dir)
        before_other = _filesystem_snapshot(self.root, other_journal_dir)
        with self.assertRaisesRegex(MigrationError, "PURGE_JOURNAL_IDENTITY_MISMATCH"):
            self._open_read_only(journal_path=other_journal)
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before_data)
        self.assertEqual(_filesystem_snapshot(self.root, other_journal_dir), before_other)

    def test_journal_head_mismatch_fails_without_recovery_mutation(self):
        journal = IndependentPurgeJournal(self.journal, self.root)
        journal.append(
            action="BARRIER_INSTALLED", barrier_id="fixture-barrier", plan_id="fixture-plan",
            plan_hash="a" * 64, lineage_revision=0, protected_refs=[], task_id="fixture-task",
        )
        before = _filesystem_snapshot(self.root, self.journal_dir)
        with closing(_read_only_connection(self.root / "nexus.sqlite")) as conn:
            before_sessions = conn.execute("SELECT COUNT(*) FROM recovery_sessions").fetchone()[0]
        with self.assertRaisesRegex(MigrationError, "PURGE_JOURNAL_WATERMARK_MISMATCH"):
            self._open_read_only()
        with closing(_read_only_connection(self.root / "nexus.sqlite")) as conn:
            after_sessions = conn.execute("SELECT COUNT(*) FROM recovery_sessions").fetchone()[0]
        self.assertEqual(after_sessions, before_sessions)
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_active_writer_denies_read_only_open_without_filesystem_delta(self):
        before = _filesystem_snapshot(self.root, self.journal_dir)
        self.store = open_test_store(self.root, independent_purge_journal_path=self.journal)
        with self.assertRaisesRegex(WriterAlreadyRunning, "NEXUS_WRITER_ALREADY_RUNNING"):
            self._open_read_only()
        self.store.close()
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_read_only_mutation_attempts_fail_with_stable_reason(self):
        before = _filesystem_snapshot(self.root, self.journal_dir)
        app = self._open_read_only()
        try:
            with self.assertRaisesRegex(MigrationError, "READ_ONLY_STORE_MUTATION_DENIED"):
                app.view_model.set_participation_mode("OBSERVE")
            with app.store._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            for capability in (
                "core_write", "run_execute", "trace_write", "memory_write",
                "effect_commit", "egress", "learning_write",
            ):
                with self.subTest(capability=capability), self.assertRaisesRegex(
                    MigrationError, "READ_ONLY_STORE_MUTATION_DENIED"
                ):
                    app.store._require_mode(capability)
            from kernel.authority import AuthorityService
            from kernel.runtime.modes import RuntimeModeService
            modes = RuntimeModeService(app.store, AuthorityService(app.store, app.store.policy))
            with self.assertRaisesRegex(MigrationError, "READ_ONLY_STORE_MUTATION_DENIED"):
                modes.set_mode(
                    command_id="readonly-mode-change", grant_id="unused", task_id="unused",
                    mode="SAFE", classification_assertion_ref="unused",
                )
            with self.assertRaisesRegex(ValueError, "READ_ONLY_STORE_MUTATION_DENIED"):
                app.store.independent_purge_journal.append(
                    action="BARRIER_INSTALLED", barrier_id="b", plan_id="p",
                    plan_hash="b" * 64, lineage_revision=0, protected_refs=[],
                )
        finally:
            app.close()
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)

    def test_read_only_composition_rejects_writer_services(self):
        with self.assertRaisesRegex(ValueError, "PANEL_READ_ONLY_WRITER_COMPOSITION_INVALID"):
            open_panel_application(self.root, writer_services={}, read_only=True)

    def test_panel_cli_exposes_explicit_read_only_flag(self):
        from adapters.panel import __main__ as panel_cli

        application = mock.Mock()
        with mock.patch.object(panel_cli, "open_panel_application", return_value=application) as open_panel, \
             mock.patch.object(panel_cli, "launch_panel"):
            self.assertEqual(panel_cli.main([
                "--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "--read-only",
            ]), 0)
        self.assertTrue(open_panel.call_args.kwargs["read_only"])
        application.close.assert_called_once_with()

    def test_read_only_projection_matches_existing_writable_projection(self):
        ordinary = open_panel_application(
            self.root, policy_path=self.policy_path,
            independent_purge_journal_path=self.journal,
        )
        try:
            expected = ordinary.view_model.snapshot()
        finally:
            ordinary.close()
        read_only = self._open_read_only()
        try:
            self.assertEqual(read_only.view_model.snapshot(), expected)
        finally:
            read_only.close()

    def test_nonempty_wal_fails_closed_without_side_effects(self):
        wal = Path(str(self.root / "nexus.sqlite") + "-wal")
        wal.write_bytes(b"uncheckpointed-wal-fixture")
        before = _filesystem_snapshot(self.root, self.journal_dir)
        with self.assertRaisesRegex(MigrationError, "READ_ONLY_WAL_STATE_UNSUPPORTED"):
            self._open_read_only()
        self.assertEqual(_filesystem_snapshot(self.root, self.journal_dir), before)


if __name__ == "__main__":
    unittest.main()
