from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.bootstrap.service import adopt_policy_binding, authority_bootstrap, initialize_instance
from adapters.bootstrap.__main__ import main as bootstrap_cli_main
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.instance_binding import policy_sha256
from kernel.object.errors import MigrationError
from kernel.purge.journal import IndependentPurgeJournal
from tests.support.test_store import open_legacy_fixture_store


class FreshInstanceBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        repo = Path(__file__).resolve().parents[2]
        self.policy = json.loads((repo / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["operator-human"]
        self.policy_path = self.base / "local-policy.json"
        self.policy_path.write_text(json.dumps(self.policy, ensure_ascii=False, indent=2), encoding="utf-8")
        self.data_root = self.base / "nexus-data"
        self.journal = self.base / "nexus-purge.jsonl"

    def test_fresh_init_binds_policy_and_journal_without_user_authority_or_work(self):
        result = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-1")
        self.assertEqual(result["status"], "INITIALIZED_UNAUTHORIZED")
        self.assertEqual(result["policy_sha256"], policy_sha256(self.policy))
        self.assertNotIn(str(self.data_root), json.dumps(result))
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        try:
            status = store.get_instance_binding_status()
            self.assertEqual(status["state"], "FRESH_BOUND_INSTANCE")
            with store._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 28)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM principals WHERE principal_id<>'nexus-core-recovery'").fetchone()[0], 0)
        finally:
            store.close()

        replay = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-1")
        self.assertEqual(replay["instance_id"], result["instance_id"])
        self.assertEqual(replay["policy_sha256"], result["policy_sha256"])

        # A committed binding is self-contained. A restored database may not
        # carry the local bootstrap intent sidecar, but must still reopen.
        (self.data_root / ".nexus-instance-init-v1.json").unlink()
        reopened = ObjectStore(self.data_root, policy=self.policy,
                               independent_purge_journal_path=self.journal)
        reopened.close()

    def test_ordinary_open_refuses_missing_database_and_legacy_without_migrating(self):
        with self.assertRaisesRegex(MigrationError, "INSTANCE_INITIALIZATION_REQUIRED"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)
        self.assertFalse((self.data_root / "nexus.sqlite").exists())

        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-legacy")
        intent = self.data_root / ".nexus-instance-init-v1.json"
        intent.unlink()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
        with self.assertRaisesRegex(MigrationError, "POLICY_BINDING_ADOPTION_REQUIRED"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 27)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='instance_policy_binding'").fetchone())

        # Applying the new schema to a pre-binding root never copies the
        # currently loaded policy into the binding table.
        legacy = open_legacy_fixture_store(self.data_root, policy=self.policy,
                                           independent_purge_journal_path=self.journal)
        legacy.close()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 28)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM instance_policy_binding").fetchone()[0], 0)
        with self.assertRaisesRegex(MigrationError, "INSTANCE_BINDING_MISSING_OR_AMBIGUOUS"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)

    def test_legacy_adoption_requires_operator_confirmation_and_binds_only_after_checks(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-adopt")
        (self.data_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
        declined = []
        with self.assertRaisesRegex(MigrationError, "OPERATOR_CONFIRMATION_DENIED"):
            adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                 independent_purge_journal=self.journal, command_id="adopt-1",
                                 confirmation=lambda phrase, summary: declined.append((phrase, summary)) or False)
        self.assertEqual(declined[0][1]["status"], "LEGACY_UNBOUND_INSTANCE")
        result = adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                      independent_purge_journal=self.journal, command_id="adopt-1",
                                      confirmation=lambda _phrase, _summary: True)
        self.assertEqual(result["status"], "LEGACY_ADOPTED_BOUND_INSTANCE")
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        try:
            self.assertEqual(store.get_instance_binding_status()["binding_source"], "LEGACY_OPERATOR_ADOPTION")
            with store._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 28)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 0)
        finally:
            store.close()

        replay = adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                      independent_purge_journal=self.journal, command_id="adopt-1")
        self.assertEqual(replay["status"], "LEGACY_ADOPTED_BOUND_INSTANCE")
        with self.assertRaisesRegex(MigrationError, "INSTANCE_BINDING_CONFLICT"):
            adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                 independent_purge_journal=self.journal, command_id="adopt-other")

    def test_legacy_adoption_preserves_revoked_and_expired_grant_history(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-terminal-history")
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        try:
            authority = AuthorityService(store, self.policy)
            authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                                          "principal_id": "operator-human", "principal_type": "HUMAN", "status": "ACTIVE"}, "legacy-human")
            authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                                          "principal_id": "legacy-service", "principal_type": "SERVICE", "status": "ACTIVE"}, "legacy-service")
            authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
                                             "anchor_id": "legacy-anchor", "principal_id": "operator-human", "policy_ref": "1"}, "legacy-anchor")
            expired_issued = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            expired_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            for grant_id in ("legacy-revoked", "legacy-expired"):
                authority.create_grant({
                    "schema_id": "nexus.delegation_grant", "schema_version": 1,
                    "grant_id": grant_id, "issued_by": "operator-human", "granted_to": "legacy-service",
                    "task_scope": ["legacy-task"], "resource_scope": ["legacy-task"],
                    "action_scope": ["RUN_CREATE"], "audience_scope": ["nexus-runtime"],
                    "issued_at": expired_issued, "expires_at": expired_at,
                    "status": "ACTIVE", "policy_version": "1",
                }, "create-" + grant_id)
            authority.revoke_grant("legacy-revoked", "revoke-legacy-grant")
            with store._connection() as conn:
                conn.execute("UPDATE delegation_grants SET status='EXPIRED' WHERE grant_id='legacy-expired'")
        finally:
            store.close()

        (self.data_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
        result = adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                      independent_purge_journal=self.journal, command_id="adopt-terminal-history",
                                      confirmation=lambda *_: True)
        self.assertEqual(result["status"], "LEGACY_ADOPTED_BOUND_INSTANCE")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            statuses = dict(conn.execute("SELECT grant_id,status FROM delegation_grants"))
            self.assertEqual(statuses["legacy-revoked"], "REVOKED")
            self.assertEqual(statuses["legacy-expired"], "EXPIRED")

    def test_legacy_adoption_accepts_existing_non_human_trust_anchor_without_rewriting_it(self):
        policy = json.loads(json.dumps(self.policy))
        policy["trust_anchors"] = ["legacy-service-anchor"]
        policy_path = self.base / "legacy-service-policy.json"
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        legacy_root = self.base / "legacy-service-data"
        legacy_journal = self.base / "legacy-service-purge.jsonl"
        initialize_instance(data_root=legacy_root, policy_path=policy_path,
                            independent_purge_journal=legacy_journal, command_id="init-legacy-service")
        store = ObjectStore(legacy_root, policy=policy, independent_purge_journal_path=legacy_journal)
        try:
            authority = AuthorityService(store, policy)
            authority.register_principal({
                "schema_id": "nexus.principal", "schema_version": 1,
                "principal_id": "legacy-service-anchor", "principal_type": "SERVICE", "status": "ACTIVE",
            }, "legacy-service:principal")
            authority.register_trust_anchor({
                "schema_id": "nexus.trust_anchor", "schema_version": 1,
                "anchor_id": "legacy-service-anchor-id", "principal_id": "legacy-service-anchor",
                "policy_ref": policy["policy_version"],
            }, "legacy-service:anchor")
        finally:
            store.close()

        (legacy_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(legacy_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
            before = tuple(conn.execute(
                "SELECT ta.anchor_id,ta.principal_id,ta.policy_ref,p.principal_type,p.status "
                "FROM trust_anchors ta JOIN principals p USING(principal_id)"
            ).fetchone())

        result = adopt_policy_binding(
            data_root=legacy_root, policy_path=policy_path,
            independent_purge_journal=legacy_journal, command_id="adopt-legacy-service",
            confirmation=lambda *_: True,
        )
        self.assertEqual(result["status"], "LEGACY_ADOPTED_BOUND_INSTANCE")
        with closing(sqlite3.connect(legacy_root / "nexus.sqlite")) as conn:
            after = tuple(conn.execute(
                "SELECT ta.anchor_id,ta.principal_id,ta.policy_ref,p.principal_type,p.status "
                "FROM trust_anchors ta JOIN principals p USING(principal_id)"
            ).fetchone())
        self.assertEqual(after, before)
        self.assertEqual(after[3], "SERVICE")

    def test_legacy_adoption_rejects_foreign_key_integrity_failure_before_migration(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-corrupt-legacy")
        (self.data_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("INSERT INTO trust_anchors(anchor_id,principal_id,policy_ref) VALUES('orphan-anchor','operator-human','1')")
            conn.commit()
        with self.assertRaisesRegex(MigrationError, "LEGACY_ADOPTION_INCOMPATIBLE:.*SQLITE_FOREIGN_KEY_CHECK_FAILED"):
            adopt_policy_binding(data_root=self.data_root, policy_path=self.policy_path,
                                 independent_purge_journal=self.journal, command_id="adopt-corrupt",
                                 confirmation=lambda *_: True)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 27)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='instance_policy_binding'").fetchone())

    def test_policy_version_and_digest_mismatches_fail_closed(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-policy")
        changed = json.loads(json.dumps(self.policy))
        changed["trace"]["max_fields"] -= 1
        with self.assertRaisesRegex(MigrationError, "POLICY_BINDING_MISMATCH"):
            ObjectStore(self.data_root, policy=changed,
                        independent_purge_journal_path=self.journal)
        changed_version = json.loads(json.dumps(self.policy))
        changed_version["policy_version"] = "2"
        with self.assertRaisesRegex(MigrationError, "POLICY_ROTATION_UNSUPPORTED"):
            ObjectStore(self.data_root, policy=changed_version,
                        independent_purge_journal_path=self.journal)

    def test_authority_bootstrap_uses_authority_service_and_exact_narrow_scope(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-authority")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "operator-human",
            "runtime_principal_id": "project-nexus-service",
            "anchor_id": "project-nexus-anchor",
            "grant_id": "project-nexus-bootstrap-grant",
            "task_id": "project-nexus-bootstrap-task",
            "resource_scope": ["project-nexus-bootstrap-task", "project-nexus-root-run", "project-nexus-input", "project-nexus-contract", "project-nexus-manifest", "project-nexus-budget", "evt-project-nexus-root-create"],
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "OBJECT_WRITE", "TRACE_APPEND", "CLASSIFY", "VERIFY", "MEMORY_ADMIT", "MEMORY_SEARCH"],
            "audience_scope": ["nexus-runtime"],
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
            "command_id_prefix": "project-nexus-first-authority",
        }
        result = authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, plan=plan,
                                     confirmation=lambda phrase, _summary: phrase.startswith("BOOTSTRAP "))
        self.assertEqual(result["status"], "AUTHORITY_BOOTSTRAP_COMPLETE")
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        try:
            authority = AuthorityService(store, self.policy)
            chain = authority.validate_delegation_chain(plan["grant_id"])
            grant = chain[-1]
            self.assertEqual(grant["task_scope"], [plan["task_id"]])
            self.assertEqual(set(grant["resource_scope"]), set(plan["resource_scope"]))
            self.assertTrue(set(grant["action_scope"]).isdisjoint({"DELEGATE", "EGRESS", "EFFECT_COMMIT", "EFFECT_EXECUTE"}))
            with store._connection() as conn:
                self.assertEqual(conn.execute("SELECT principal_type FROM principals WHERE principal_id=?", (plan["operator_principal_id"],)).fetchone()[0], "HUMAN")
                self.assertEqual(conn.execute("SELECT principal_type FROM principals WHERE principal_id=?", (plan["runtime_principal_id"],)).fetchone()[0], "SERVICE")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        finally:
            store.close()

        replay = authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, plan=plan,
                                     confirmation=lambda *_: True)
        self.assertEqual(replay, result)
        changed = json.loads(json.dumps(plan))
        changed["task_id"] = "different-task"
        with self.assertRaisesRegex(MigrationError, "AUTHORITY_BOOTSTRAP_INTENT_CONFLICT"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=changed,
                                confirmation=lambda *_: True)
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        try:
            with store._connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM principals WHERE principal_id<>'nexus-core-recovery'").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 1)
        finally:
            store.close()

    def test_authority_bootstrap_exact_retry_resumes_after_partial_authority_prefix(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-authority-resume")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "runtime-service",
            "anchor_id": "anchor-resume", "grant_id": "grant-resume", "task_id": "task-resume",
            "resource_scope": ["task-resume", "root-run-resume", "input-resume"],
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "OBJECT_WRITE", "TRACE_APPEND", "CLASSIFY"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(), "command_id_prefix": "resume-authority",
        }
        original = AuthorityService.register_trust_anchor

        def commit_anchor_then_lose_response(service, anchor, command_id):
            original(service, anchor, command_id)
            raise RuntimeError("simulated authority bootstrap interruption")

        with mock.patch.object(AuthorityService, "register_trust_anchor", commit_anchor_then_lose_response):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, plan=plan,
                                    confirmation=lambda *_: True)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 1)

        result = authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, plan=plan,
                                     confirmation=lambda *_: True)
        self.assertEqual(result["status"], "AUTHORITY_BOOTSTRAP_COMPLETE")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM principals WHERE principal_id<>'nexus-core-recovery'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 1)
            commands = [row[0] for row in conn.execute("SELECT command_id FROM command_ledger WHERE command_id LIKE 'resume-authority:%' ORDER BY rowid")]
            self.assertEqual(commands, [
                "resume-authority:human", "resume-authority:service",
                "resume-authority:anchor", "resume-authority:grant",
            ])

    def test_future_issued_at_is_rejected_before_authority_mutation(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-future-grant")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "future-runtime",
            "anchor_id": "future-anchor", "grant_id": "future-grant", "task_id": "future-task",
            "resource_scope": ["future-task"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"],
            "issued_at": (now + timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=8)).isoformat(),
            "command_id_prefix": "future-authority",
        }
        with self.assertRaisesRegex(MigrationError, "AUTHORITY_BOOTSTRAP_PLAN_NOT_CURRENT"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan,
                                confirmation=lambda *_: True)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM principals WHERE principal_id<>'nexus-core-recovery'"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 0)
        self.assertFalse((self.data_root / ".nexus-authority-bootstrap-v1.json").exists())

    def test_expired_partial_authority_plan_keeps_prefix_and_refuses_grant(self):
        from adapters.bootstrap import service as bootstrap_service

        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-expired-resume")
        start = datetime.now(timezone.utc)
        expires = start + timedelta(minutes=2)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "expiry-runtime",
            "anchor_id": "expiry-anchor", "grant_id": "expiry-grant", "task_id": "expiry-task",
            "resource_scope": ["expiry-task"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"],
            "issued_at": (start - timedelta(minutes=1)).isoformat(),
            "expires_at": expires.isoformat(), "command_id_prefix": "expiry-authority",
        }

        class ControlledDateTime(datetime):
            now_value = start

            @classmethod
            def now(cls, tz=None):
                return cls.now_value

        original_anchor = AuthorityService.register_trust_anchor

        def anchor_then_interrupt(authority, anchor, command_id):
            original_anchor(authority, anchor, command_id)
            raise RuntimeError("simulated after anchor")

        with mock.patch.object(bootstrap_service, "datetime", ControlledDateTime), \
             mock.patch.object(AuthorityService, "register_trust_anchor", anchor_then_interrupt):
            with self.assertRaisesRegex(RuntimeError, "simulated after anchor"):
                authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, plan=plan,
                                    confirmation=lambda *_: True)
        ControlledDateTime.now_value = expires + timedelta(seconds=1)
        with mock.patch.object(bootstrap_service, "datetime", ControlledDateTime):
            with self.assertRaisesRegex(MigrationError, "AUTHORITY_BOOTSTRAP_PLAN_NOT_CURRENT"):
                authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, plan=plan,
                                    confirmation=lambda *_: True)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM principals WHERE principal_id IN ('operator-human','expiry-runtime')"
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors WHERE anchor_id='expiry-anchor'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id='expiry-grant'").fetchone()[0], 0)

    def test_completed_authority_bootstrap_replays_after_grant_naturally_expires(self):
        from adapters.bootstrap import service as bootstrap_service

        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-completed-expiry")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=1)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "completed-runtime",
            "anchor_id": "completed-anchor", "grant_id": "completed-grant", "task_id": "completed-task",
            "resource_scope": ["completed-task"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"], "issued_at": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": expires.isoformat(), "command_id_prefix": "completed-authority",
        }
        first = authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, plan=plan,
                                    confirmation=lambda *_: True)

        class ExpiredDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return expires + timedelta(seconds=1)

        with mock.patch.object(bootstrap_service, "datetime", ExpiredDateTime):
            replay = authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                         independent_purge_journal=self.journal, plan=plan,
                                         confirmation=lambda *_: self.fail("committed replay must not ask to confirm again"))
        self.assertEqual(replay, first)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id='completed-grant'").fetchone()[0], 1)

    def test_authority_bootstrap_rejects_unlisted_and_recovery_operator_before_mutation(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-authority-denied")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "unlisted-human", "runtime_principal_id": "runtime-denied",
            "anchor_id": "anchor-denied", "grant_id": "grant-denied", "task_id": "task-denied",
            "resource_scope": ["task-denied"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "command_id_prefix": "deny-unlisted",
        }
        with self.assertRaisesRegex(MigrationError, "TRUST_ANCHOR_NOT_CONFIGURED_BY_POLICY"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan,
                                confirmation=lambda *_: True)
        plan["operator_principal_id"] = "nexus-core-recovery"
        with self.assertRaisesRegex(MigrationError, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan,
                                confirmation=lambda *_: True)
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM principals WHERE principal_id<>'nexus-core-recovery'").fetchone()[0], 0)

    def test_authority_bootstrap_refuses_legacy_unbound_instance(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-stage-two-legacy")
        (self.data_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
        with self.assertRaisesRegex(MigrationError, "POLICY_BINDING_ADOPTION_REQUIRED"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan={})
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 27)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trust_anchors").fetchone()[0], 0)

    def test_stage_two_rejects_wildcards_forbidden_actions_and_noninteractive_tty(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-deny")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "runtime-service",
            "anchor_id": "anchor-deny", "grant_id": "grant-deny", "task_id": "task-deny",
            "resource_scope": ["*"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "command_id_prefix": "deny-bootstrap",
        }
        with self.assertRaisesRegex(MigrationError, "WILDCARD_SCOPE_DENIED"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan,
                                confirmation=lambda *_: True)
        plan["resource_scope"] = ["task-deny"]
        plan["action_scope"] = ["EFFECT_COMMIT"]
        with self.assertRaisesRegex(MigrationError, "FORBIDDEN_ACTION"):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan,
                                confirmation=lambda *_: True)

        from unittest.mock import patch
        import io
        plan["action_scope"] = ["RUN_CREATE"]
        with patch("adapters.bootstrap.service.sys.stdin", io.StringIO("BOOTSTRAP no\n")), \
             patch("adapters.bootstrap.service.sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(MigrationError, "INTERACTIVE_TTY_REQUIRED"):
                authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, plan=plan)

    def test_real_tty_confirmation_phrases_gate_stage_two_and_legacy_adoption(self):
        import builtins
        from unittest.mock import MagicMock, patch

        initialized = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                          independent_purge_journal=self.journal, command_id="init-tty")
        now = datetime.now(timezone.utc)
        plan = {
            "operator_principal_id": "operator-human", "runtime_principal_id": "runtime-tty",
            "anchor_id": "anchor-tty", "grant_id": "grant-tty", "task_id": "task-tty",
            "resource_scope": ["task-tty", "run-tty"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "command_id_prefix": "tty-authority",
        }
        tty_in = MagicMock()
        tty_out = MagicMock()
        tty_in.isatty.return_value = True
        tty_out.isatty.return_value = True
        with patch("adapters.bootstrap.service.sys.stdin", tty_in), \
             patch("adapters.bootstrap.service.sys.stdout", tty_out), \
             patch.object(builtins, "input", return_value="BOOTSTRAP " + initialized["instance_id"][-12:]):
            authority_bootstrap(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, plan=plan)

        # A separate legacy root needs a distinct confirmation.
        legacy_root = self.base / "legacy-data"
        legacy_journal = self.base / "legacy-purge.jsonl"
        initialize_instance(data_root=legacy_root, policy_path=self.policy_path,
                            independent_purge_journal=legacy_journal, command_id="init-for-legacy")
        (legacy_root / ".nexus-instance-init-v1.json").unlink()
        with closing(sqlite3.connect(legacy_root / "nexus.sqlite")) as conn:
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=27")
            conn.commit()
        with patch("adapters.bootstrap.service.sys.stdin", tty_in), \
             patch("adapters.bootstrap.service.sys.stdout", tty_out), \
             patch.object(builtins, "input", return_value="ADOPT LEGACY POLICY"):
            adopted = adopt_policy_binding(data_root=legacy_root, policy_path=self.policy_path,
                                           independent_purge_journal=legacy_journal, command_id="adopt-tty")
        self.assertEqual(adopted["status"], "LEGACY_ADOPTED_BOUND_INSTANCE")

    def test_stage_one_resumes_partial_migration_without_migration_policy_adoption(self):
        with mock.patch.object(ObjectStore, "_commit_instance_binding", side_effect=RuntimeError("simulated crash")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, command_id="init-resume")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 28)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM instance_policy_binding").fetchone()[0], 0)
            watermark = conn.execute("SELECT journal_identity,sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
            self.assertEqual(tuple(watermark), (ObjectStore._journal_identity_for_path(self.journal), 0, "0" * 64))
        with self.assertRaisesRegex(MigrationError, "INSTANCE_INITIALIZATION_INCOMPLETE"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)
        result = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-resume")
        self.assertEqual(result["status"], "INITIALIZED_UNAUTHORIZED")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM instance_policy_binding").fetchone()[0], 1)

    def test_stage_one_lost_binding_response_replays_committed_row_once(self):
        original = ObjectStore._commit_instance_binding

        def commit_then_lose_response(store, **kwargs):
            original(store, **kwargs)
            raise RuntimeError("lost response")

        with mock.patch.object(ObjectStore, "_commit_instance_binding", commit_then_lose_response):
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, command_id="init-response-loss")
        result = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-response-loss")
        self.assertEqual(result["status"], "INITIALIZED_UNAUTHORIZED")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM instance_policy_binding").fetchone()[0], 1)

    def test_partial_intent_rejects_changed_command_or_policy_and_unsafe_roots(self):
        with mock.patch.object(ObjectStore, "_initialize_fresh_instance", side_effect=RuntimeError("after intent")):
            with self.assertRaisesRegex(RuntimeError, "after intent"):
                initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, command_id="init-fixed")
        with self.assertRaisesRegex(MigrationError, "BOOTSTRAP_INTENT_CONFLICT"):
            initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, command_id="init-changed")
        changed = json.loads(json.dumps(self.policy))
        changed["trace"]["max_fields"] -= 1
        changed_path = self.base / "changed-policy.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "BOOTSTRAP_INTENT_CONFLICT"):
            initialize_instance(data_root=self.data_root, policy_path=changed_path,
                                independent_purge_journal=self.journal, command_id="init-fixed")

        nonempty = self.base / "nonempty-root"
        nonempty.mkdir()
        (nonempty / "foreign.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(MigrationError, "FRESH_INSTANCE_ROOT_NOT_EMPTY"):
            initialize_instance(data_root=nonempty, policy_path=self.policy_path,
                                independent_purge_journal=self.base / "nonempty-journal.jsonl", command_id="init-nonempty")
        with self.assertRaisesRegex(MigrationError, "BOOTSTRAP_ROOT_MUST_BE_REPO_EXTERNAL"):
            initialize_instance(data_root=Path(__file__).resolve().parents[2] / "temp-bootstrap-root",
                                policy_path=self.policy_path,
                                independent_purge_journal=self.base / "repo-root-journal.jsonl", command_id="init-repo")

    def test_symlinked_root_is_rejected_when_platform_supports_symlinks(self):
        target = self.base / "real-target"
        target.mkdir()
        alias = self.base / "root-alias"
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink unavailable: {type(exc).__name__}")
        with self.assertRaisesRegex(MigrationError, "BOOTSTRAP_PATH_SYMLINK_DENIED|BOOTSTRAP_PATH_ALIAS_DENIED"):
            initialize_instance(data_root=alias, policy_path=self.policy_path,
                                independent_purge_journal=self.base / "symlink-journal.jsonl", command_id="init-symlink")

    def test_hardlinked_purge_journal_path_is_rejected_when_platform_supports_links(self):
        original = self.base / "journal-original.jsonl"
        alias = self.base / "journal-alias.jsonl"
        original.write_bytes(b"")
        try:
            alias.hardlink_to(original)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {type(exc).__name__}")
        with self.assertRaisesRegex(MigrationError, "PURGE_JOURNAL_PATH_ALIAS_DENIED"):
            initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=alias, command_id="init-hardlink")

    def test_nonempty_purge_journal_cannot_be_adopted_by_fresh_initialization(self):
        journal = IndependentPurgeJournal(self.journal, self.data_root)
        journal.append(action="BARRIER_INSTALLED", barrier_id="barrier", plan_id="plan",
                       plan_hash="a" * 64, lineage_revision=0, protected_refs=["object"], task_id="task")
        with self.assertRaisesRegex(MigrationError, "FRESH_PURGE_JOURNAL_CONFLICT"):
            initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                independent_purge_journal=self.journal, command_id="init-journal-history")
        self.assertFalse((self.data_root / "nexus.sqlite").exists())

    def test_nonempty_purge_journal_is_rejected_before_fresh_intent(self):
        target = self.base / "journal-preflight-root"
        history = self.base / "historical-purge.jsonl"
        history.write_bytes(b"malformed or historical data\n")
        with self.assertRaisesRegex(MigrationError, "FRESH_PURGE_JOURNAL_CONFLICT"):
            initialize_instance(data_root=target, policy_path=self.policy_path,
                                independent_purge_journal=history, command_id="preflight-journal")
        self.assertFalse(target.exists())
        self.assertFalse((target / ".nexus-instance-init-v1.json").exists())
        self.assertFalse((target / "nexus.sqlite").exists())
        self.assertFalse((target / "objects").exists())

        corrected_empty_journal = self.base / "corrected-empty-purge.jsonl"
        corrected_empty_journal.write_bytes(b"")
        result = initialize_instance(data_root=target, policy_path=self.policy_path,
                                     independent_purge_journal=corrected_empty_journal,
                                     command_id="preflight-journal")
        self.assertEqual(result["status"], "INITIALIZED_UNAUTHORIZED")

    def test_bootstrap_cli_sanitizes_writer_lock_error_as_json(self):
        initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="init-cli-lock")
        plan_path = self.base / "bootstrap-plan.json"
        plan_path.write_text("{}", encoding="utf-8")
        store = ObjectStore(self.data_root, policy=self.policy,
                            independent_purge_journal_path=self.journal)
        stderr = io.StringIO()
        stdout = io.StringIO()
        try:
            with redirect_stderr(stderr), redirect_stdout(stdout):
                exit_code = bootstrap_cli_main([
                    "authority-bootstrap", "--data-root", str(self.data_root),
                    "--policy", str(self.policy_path), "--independent-purge-journal", str(self.journal),
                    "--plan", str(plan_path),
                ])
        finally:
            store.close()
        self.assertNotEqual(exit_code, 0)
        response = json.loads(stderr.getvalue())
        self.assertEqual(response["error"], "NEXUS_WRITER_ALREADY_RUNNING")
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertNotIn(str(self.base), stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_bootstrap_cli_hides_unexpected_error_prose_and_path(self):
        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch(
            "adapters.bootstrap.__main__.initialize_instance",
            side_effect=RuntimeError("unexpected internal failure at " + str(self.base)),
        ), redirect_stderr(stderr), redirect_stdout(stdout):
            exit_code = bootstrap_cli_main([
                "initialize-instance", "--data-root", str(self.data_root),
                "--policy", str(self.policy_path), "--independent-purge-journal", str(self.journal),
                "--command-id", "cli-sanitizer-test",
            ])
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "BOOTSTRAP_INTERNAL_ERROR"})
        self.assertNotIn(str(self.base), stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_stage_one_crash_after_journal_creation_is_partial_and_exactly_resumable(self):
        with mock.patch.object(ObjectStore, "_initialize_database", side_effect=RuntimeError("crash after journal")):
            with self.assertRaisesRegex(RuntimeError, "crash after journal"):
                initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, command_id="init-after-journal")
        self.assertTrue(self.journal.exists())
        self.assertFalse((self.data_root / "nexus.sqlite").exists())
        with self.assertRaisesRegex(MigrationError, "INSTANCE_INITIALIZATION_INCOMPLETE"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)
        result = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-after-journal")
        self.assertEqual(result["status"], "INITIALIZED_UNAUTHORIZED")

    def test_malformed_fresh_intent_is_ambiguous_not_classified_as_partial(self):
        with mock.patch.object(ObjectStore, "_initialize_database", side_effect=RuntimeError("stop after journal")):
            with self.assertRaisesRegex(RuntimeError, "stop after journal"):
                initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                    independent_purge_journal=self.journal, command_id="init-malformed-intent")
        (self.data_root / ".nexus-instance-init-v1.json").write_bytes(b"not-json")
        with self.assertRaisesRegex(MigrationError, "INSTANCE_BINDING_MISSING_OR_AMBIGUOUS"):
            ObjectStore(self.data_root, policy=self.policy,
                        independent_purge_journal_path=self.journal)

    def test_instance_binding_is_immutable_and_absolute_paths_are_not_persisted(self):
        result = initialize_instance(data_root=self.data_root, policy_path=self.policy_path,
                                     independent_purge_journal=self.journal, command_id="init-private")
        with closing(sqlite3.connect(self.data_root / "nexus.sqlite")) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE instance_policy_binding SET policy_sha256=? WHERE singleton=1", ("0" * 64,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM instance_policy_binding WHERE singleton=1")
            binding = conn.execute("SELECT * FROM instance_policy_binding").fetchone()
        persisted = json.dumps(list(binding), ensure_ascii=False)
        self.assertNotIn(str(self.base), persisted)
        self.assertNotIn(str(self.data_root), json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
