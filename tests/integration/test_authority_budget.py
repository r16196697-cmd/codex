import hashlib
import json
import sqlite3
import threading
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kernel.authority import ApprovalDenied, AuthorityService, AuthorizationDenied, InvalidDelegation
from kernel.object.errors import CommandConflict
from kernel.budget import BudgetExceeded, BudgetService
from adapters.storage import ObjectStore


class AuthorityBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step3-")
        self.root = Path(self.temp.name)
        self.store = ObjectStore(self.root / "data")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        policy_path = Path(__file__).resolve().parents[2] / "policies" / "default-policy.json"
        self.policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, self.policy, max_delegation_depth=4)
        self.budget = BudgetService(self.store)
        self._register("human-root", "HUMAN")
        self._register("agent", "SERVICE")
        self._register("tool", "TOOL")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor-human", "principal_id": "human-root", "policy_ref": "1"}, "cmd-anchor")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _register(self, principal_id, principal_type):
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": principal_id, "principal_type": principal_type, "status": "ACTIVE"}, f"cmd-principal-{principal_id}")

    def _grant(self, grant_id, issued_by, granted_to, *, parent=None, tasks=None, resources=None, actions=None, audiences=None, status="ACTIVE"):
        now = datetime.now(timezone.utc)
        grant = {
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": grant_id,
            "issued_by": issued_by, "granted_to": granted_to, "task_scope": tasks or ["task-1"],
            "resource_scope": resources or ["resource-1"], "action_scope": actions or ["READ", "DELEGATE"],
            "audience_scope": audiences or ["local"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": status, "policy_version": "1",
        }
        if parent:
            grant["parent_grant_id"] = parent
        self.authority.create_grant(grant, f"cmd-create-{grant_id}")
        return grant

    def _root_and_child(self):
        self._grant("grant-root", "human-root", "agent", tasks=["task-1", "task-2"], resources=["resource-1", "resource-2"], actions=["READ", "WRITE", "DELEGATE", "EGRESS"], audiences=["local", "provider-x"])
        self._grant("grant-child", "agent", "tool", parent="grant-root", tasks=["task-1"], resources=["resource-1"], actions=["READ"], audiences=["local"])

    def test_untrusted_root_rejected_and_child_authority_is_subset(self):
        self._register("attacker", "HUMAN")
        with self.assertRaises(InvalidDelegation):
            self._grant("forged-root", "attacker", "tool")
        self._root_and_child()
        effective = self.authority.compute_effective_authority("grant-child")
        self.assertEqual(effective["task_scope"], {"task-1"})
        self.assertEqual(effective["resource_scope"], {"resource-1"})
        self.assertTrue(self.authority.evaluate_authorization("grant-child", {"task": "task-1", "resource": "resource-1", "action": "READ", "audience": "local"}, "cmd-auth-ok"))
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", {"task": "task-1", "resource": "resource-2", "action": "READ", "audience": "local"}, "cmd-auth-deny")
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", {"task": "task-1", "resource": "resource-1", "action": "READ", "audience": "provider-x"}, "cmd-audience-deny")
        with self.store._connection() as conn:
            denial = conn.execute("SELECT event_type,reason_code FROM authority_events WHERE command_id='cmd-auth-deny'").fetchone()
        self.assertEqual(tuple(denial), ("AUTHORIZATION_DENIED", "SCOPE_DENIED"))

    def test_scope_expansion_and_excessive_chain_depth_reject(self):
        self._grant("grant-root", "human-root", "agent", resources=["resource-1"])
        with self.assertRaises(InvalidDelegation):
            self._grant("grant-expand", "agent", "tool", parent="grant-root", resources=["resource-1", "resource-2"])
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE delegation_grants SET action_scope_json='[]' WHERE grant_id='grant-root'")
        self._register("p2", "SERVICE")
        self._register("p3", "SERVICE")
        self._register("p4", "SERVICE")
        self._register("p5", "SERVICE")
        self._grant("g1", "human-root", "agent")
        self._grant("g2", "agent", "p2", parent="g1")
        self._grant("g3", "p2", "p3", parent="g2")
        self._grant("g4", "p3", "p4", parent="g3")
        self._grant("g5", "p4", "p5", parent="g4")
        with self.assertRaises(InvalidDelegation):
            self.authority.validate_delegation_chain("g5")

    def test_approval_hash_and_parent_revocation_are_rechecked(self):
        self._root_and_child()
        digest = hashlib.sha256(b"exact effect payload").hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        approval = {
            "schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-1",
            "approver_principal_id": "human-root", "target_type": "READ", "target_ref": "resource-1",
            "effect_id": "effect-1", "payload_integrity_hash": digest, "decision": "APPROVE",
            "approved_scope": ["READ", "resource-1"], "policy_version": "1", "issued_at": now,
        }
        self.authority.create_approval(approval, "cmd-approval")
        request = {"task": "task-1", "resource": "resource-1", "action": "READ", "audience": "local", "effect_id": "effect-1"}
        self.assertTrue(self.authority.evaluate_authorization("grant-child", request, "cmd-commit-ok", "approval-1", digest))
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", request, "cmd-commit-changed-payload", "approval-1", "0" * 64)
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", dict(request, effect_id="effect-2"), "cmd-commit-wrong-effect", "approval-1", digest)
        self.authority.revoke_grant("grant-root", "cmd-revoke-root")
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", request, "cmd-commit-after-revoke", "approval-1", digest)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM delegation_grants WHERE grant_id='grant-root'").fetchone()[0], "REVOKED")
            self.assertEqual(conn.execute("SELECT decision FROM approval_decisions WHERE approval_id='approval-1'").fetchone()[0], "APPROVE")

    def test_principal_revocation_invalidates_every_chain(self):
        self._root_and_child()
        self.authority.revoke_principal("agent", "cmd-revoke-agent")
        with self.assertRaises(AuthorizationDenied):
            self.authority.evaluate_authorization("grant-child", {"task": "task-1", "resource": "resource-1", "action": "READ", "audience": "local"}, "cmd-auth-revoked-agent")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM principals WHERE principal_id='agent'").fetchone()[0], "REVOKED")
            self.assertEqual(conn.execute("SELECT event_type FROM authority_events WHERE command_id='cmd-revoke-agent'").fetchone()[0], "PRINCIPAL_REVOKED")

    def test_egress_is_deny_by_default_and_classification_gated(self):
        self.assertFalse(self.authority.evaluate_egress("provider-x", ["missing-object"]))
        self._grant("grant-root", "human-root", "agent", resources=["provider-x", "object-1", "object-2"], actions=["EGRESS", "CLASSIFY", "DELEGATE"], audiences=["provider-x"])
        assertion = {"schema_id": "nexus.classification_assertion", "schema_version": 1, "assertion_id": "class-1", "subject_type": "OBJECT", "subject_ref": "object-1", "sensitivity_level": "PERSONAL", "handling_tags": ["PROJECT_A"], "policy_version": "1", "reason": "fixture classification", "actor_id": "agent"}
        self.authority.record_classification_assertion(assertion, grant_id="grant-root", task_id="task-1", audience="provider-x", command_id="cmd-class-1")
        self.store.put_object(command_id="cmd-store-object-1", object_id="object-1", payload=b"classified payload", object_type="artifact", created_by_run="run_test", classification_assertion_ref="class-1")
        with self.assertRaises(AuthorizationDenied):
            self.authority.authorize_egress(grant_id="grant-root", task_id="task-1", destination="provider-x", audience="provider-x", object_ids=["object-1"], command_id="cmd-egress-denied")
        policy = json.loads(json.dumps(self.policy))
        policy["egress"]["allowed_destinations"] = {"provider-x": {"max_sensitivity": "PERSONAL", "accepted_tags": ["PROJECT_A"]}}
        gated = AuthorityService(self.store, policy)
        self.assertTrue(gated.evaluate_egress("provider-x", ["object-1"]))
        high = dict(assertion, assertion_id="class-2", subject_ref="object-2", sensitivity_level="CONFIDENTIAL")
        self.authority.record_classification_assertion(high, grant_id="grant-root", task_id="task-1", audience="provider-x", command_id="cmd-class-2")
        self.store.put_object(command_id="cmd-store-object-2", object_id="object-2", payload=b"high classified payload", object_type="artifact", created_by_run="run_test", classification_assertion_ref="class-2")
        self.assertFalse(gated.evaluate_egress("provider-x", ["object-2"]))
        self.assertFalse(gated.evaluate_egress("unknown", ["object-1"]))
        self.assertTrue(gated.authorize_egress(grant_id="grant-root", task_id="task-1", destination="provider-x", audience="provider-x", object_ids=["object-1"], command_id="cmd-egress-allowed"))

    def test_classification_lowering_requires_policy_bound_human_approval(self):
        self._grant("grant-root", "human-root", "agent", resources=["object-1"], actions=["CLASSIFY", "CLASSIFICATION_LOWER", "DELEGATE"], audiences=["local"])
        initial = {"schema_id": "nexus.classification_assertion", "schema_version": 1, "assertion_id": "class-secret", "subject_type": "OBJECT", "subject_ref": "object-1", "sensitivity_level": "SECRET", "handling_tags": ["NO_EXTERNAL_EGRESS"], "policy_version": "1", "reason": "fixture classification", "actor_id": "agent"}
        self.authority.record_classification_assertion(initial, grant_id="grant-root", task_id="task-1", audience="local", command_id="cmd-class-secret")
        lowered = dict(initial, assertion_id="class-lowered", sensitivity_level="PUBLIC", handling_tags=[], supersedes="class-secret")
        with self.assertRaises(ApprovalDenied):
            self.authority.record_classification_assertion(lowered, grant_id="grant-root", task_id="task-1", audience="local", command_id="cmd-class-lowered-denied")
        unlinked = dict(lowered, assertion_id="class-unlinked", supersedes=None)
        unlinked.pop("supersedes")
        with self.assertRaises(AuthorizationDenied):
            self.authority.record_classification_assertion(unlinked, grant_id="grant-root", task_id="task-1", audience="local", command_id="cmd-class-unlinked")
        now = datetime.now(timezone.utc).isoformat()
        approval = {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-lower", "approver_principal_id": "human-root", "target_type": "CLASSIFICATION_LOWER", "target_ref": "object-1", "decision": "APPROVE", "approved_scope": ["CLASSIFICATION_LOWER", "object-1"], "policy_version": "1", "issued_at": now}
        self.authority.create_approval(approval, "cmd-approval-lower")
        self.authority.record_classification_assertion(lowered, grant_id="grant-root", task_id="task-1", audience="local", command_id="cmd-class-lowered-ok", approval_id="approval-lower")
        self.assertEqual(self.authority.effective_classification(["class-secret", "class-lowered"]), ("SECRET", {"NO_EXTERNAL_EGRESS"}))

    def test_classification_exact_replay_after_grant_revocation(self):
        self._grant("grant-class-replay", "human-root", "agent", resources=["object-replay"], actions=["CLASSIFY", "DELEGATE"], audiences=["local"])
        self._grant("grant-class-replay-alt", "human-root", "agent", resources=["object-replay"], actions=["CLASSIFY", "DELEGATE"], audiences=["local"])
        assertion = {"schema_id":"nexus.classification_assertion", "schema_version":1, "assertion_id":"class-replay", "subject_type":"OBJECT", "subject_ref":"object-replay", "sensitivity_level":"PUBLIC", "handling_tags":[], "policy_version":"1", "reason":"replay fixture", "actor_id":"agent"}
        args = {"assertion":assertion, "grant_id":"grant-class-replay", "task_id":"task-1", "audience":"local", "command_id":"cmd-class-replay"}
        self.authority.record_classification_assertion(**args)
        self.authority.revoke_grant("grant-class-replay", "cmd-class-replay-revoke")
        with self.store._connection() as conn:
            before = (conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE assertion_id='class-replay'").fetchone()[0],
                      conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-class-replay'").fetchone()[0],
                      conn.execute("SELECT COUNT(*) FROM authority_events").fetchone()[0])
        self.assertIsNone(self.authority.record_classification_assertion(**args))
        with self.assertRaises(CommandConflict):
            self.authority.record_classification_assertion(**{**args, "assertion":{**assertion, "reason":"changed"}})
        with self.assertRaises(CommandConflict):
            self.authority.record_classification_assertion(**{**args, "grant_id":"grant-class-replay-alt"})
        with self.store._connection() as conn:
            after_replay = (conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE assertion_id='class-replay'").fetchone()[0],
                            conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-class-replay'").fetchone()[0],
                            conn.execute("SELECT COUNT(*) FROM authority_events").fetchone()[0])
        self.assertEqual(after_replay, before)
        with self.assertRaises((AuthorizationDenied, InvalidDelegation)):
            self.authority.record_classification_assertion(**{**args, "command_id":"cmd-class-replay-fresh"})

    def test_committed_classification_replays_after_approval_expiry(self):
        self._grant("grant-class-expiry", "human-root", "agent", resources=["object-expiry"], actions=["CLASSIFY", "CLASSIFICATION_LOWER", "DELEGATE"], audiences=["local"])
        initial = {"schema_id":"nexus.classification_assertion", "schema_version":1, "assertion_id":"class-expiry-high", "subject_type":"OBJECT", "subject_ref":"object-expiry", "sensitivity_level":"SECRET", "handling_tags":["NO_EXTERNAL_EGRESS"], "policy_version":"1", "reason":"initial", "actor_id":"agent"}
        self.authority.record_classification_assertion(initial, grant_id="grant-class-expiry", task_id="task-1", audience="local", command_id="cmd-class-expiry-high")
        lowered = {**initial, "assertion_id":"class-expiry-low", "sensitivity_level":"PUBLIC", "handling_tags":[], "reason":"lowered", "supersedes":"class-expiry-high"}
        now = datetime.now(timezone.utc)
        approval = {"schema_id":"nexus.approval_decision", "schema_version":1, "approval_id":"approval-class-expiry", "approver_principal_id":"human-root", "target_type":"CLASSIFICATION_LOWER", "target_ref":"object-expiry", "decision":"APPROVE", "approved_scope":["CLASSIFICATION_LOWER", "object-expiry"], "policy_version":"1", "issued_at":now.isoformat(), "expires_at":(now + timedelta(seconds=5)).isoformat()}
        self.authority.create_approval(approval, "cmd-approval-class-expiry")
        args = {"assertion":lowered, "grant_id":"grant-class-expiry", "task_id":"task-1", "audience":"local", "command_id":"cmd-class-expiry-lower", "approval_id":"approval-class-expiry"}
        self.authority.record_classification_assertion(**args)
        from unittest import mock
        with mock.patch("kernel.authority.service._now", return_value=now + timedelta(seconds=10)):
            self.assertIsNone(self.authority.record_classification_assertion(**args))

    def test_budget_reservation_is_atomic_idempotent_and_task_unique(self):
        self.budget.create_account(command_id="cmd-budget-account", account_id="budget-1", task_id="task-1", amount_limit=10, unit="microcredits", model_call_limit=1, tool_call_limit=2, child_run_limit=2)
        self.budget.create_account(command_id="cmd-budget-account", account_id="budget-1", task_id="task-1", amount_limit=10, unit="microcredits", model_call_limit=1, tool_call_limit=2, child_run_limit=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.budget.create_account(command_id="cmd-budget-account-2", account_id="budget-2", task_id="task-1", amount_limit=10, unit="microcredits", model_call_limit=1, tool_call_limit=2, child_run_limit=2)
        start = threading.Barrier(3)
        results = []
        def reserve(command_id, run_id):
            start.wait()
            try:
                reservation = self.budget.reserve(command_id=command_id, account_id="budget-1", task_id="task-1", run_id=run_id, amount=7, model_calls=1)
                results.append(("PASS", reservation))
            except BudgetExceeded:
                results.append(("DENY", None))
        threads = [threading.Thread(target=reserve, args=("cmd-reserve-1", "run-1")), threading.Thread(target=reserve, args=("cmd-reserve-2", "run-2"))]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(item[0] for item in results), ["DENY", "PASS"])
        reservation_id = next(item[1] for item in results if item[0] == "PASS")
        command_id, run_id = self._reservation_request(reservation_id)
        self.assertEqual(self.budget.reserve(command_id=command_id, account_id="budget-1", task_id="task-1", run_id=run_id, amount=7, model_calls=1), reservation_id)
        self.budget.settle(command_id="cmd-settle-1", reservation_id=reservation_id, actual_amount=5)
        self.budget.settle(command_id="cmd-settle-1", reservation_id=reservation_id, actual_amount=5)
        with self.store._connection() as conn:
            account = conn.execute("SELECT reserved,consumed,model_calls_reserved,model_calls_consumed FROM budget_accounts WHERE account_id='budget-1'").fetchone()
        self.assertEqual(tuple(account), (0, 5, 0, 1))
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE budget_ledger SET amount=0 WHERE command_id=?", (command_id,))

    def test_budget_reservation_rejects_cross_task_account_without_side_effects(self):
        self.budget.create_account(command_id="budget-a", account_id="budget-a", task_id="task-a", amount_limit=20, unit="credits", model_call_limit=2, tool_call_limit=2, child_run_limit=2)
        self.budget.create_account(command_id="budget-b", account_id="budget-b", task_id="task-b", amount_limit=20, unit="credits", model_call_limit=2, tool_call_limit=2, child_run_limit=2)
        with self.assertRaisesRegex(BudgetExceeded, "BUDGET_TASK_MISMATCH"):
            self.budget.reserve(command_id="cross-task-reserve", account_id="budget-b", task_id="task-a", run_id="run-a", amount=5, child_runs=1)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_reservations WHERE command_id='cross-task-reserve'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_ledger WHERE command_id='cross-task-reserve'").fetchone()[0], 0)
            account = conn.execute("SELECT reserved,consumed,model_calls_reserved,tool_calls_reserved,child_runs_reserved FROM budget_accounts WHERE account_id='budget-b'").fetchone()
            self.assertEqual(tuple(account), (0, 0, 0, 0, 0))
        self._grant("budget-run-grant", "human-root", "agent", tasks=["task-a", "task-b"], resources=["run-a"], actions=["READ"], audiences=["local"])
        now = datetime.now(timezone.utc).isoformat()
        with self.store._connection() as conn:
            for task in ("task-a", "task-b"):
                conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES(?, 'human-root','CREATED',?,?,NULL)", (task, now, task))
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES('class-run-a','RUN','run-a','PUBLIC','[]','1','budget test','agent')")
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-a','task-a',NULL,NULL,'ORCHESTRATOR','CREATED','budget-run-grant',NULL,NULL,'{}','class-run-a',?)", (now,))
        with self.assertRaisesRegex(BudgetExceeded, "BUDGET_TASK_MISMATCH"):
            self.budget.reserve(command_id="run-account-cross-task", account_id="budget-b", task_id="task-b", run_id="run-a", amount=2)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_reservations WHERE command_id='run-account-cross-task'").fetchone()[0], 0)
            self.assertEqual(tuple(conn.execute("SELECT reserved,consumed FROM budget_accounts WHERE account_id='budget-b'").fetchone()), (0, 0))
        reservation = self.budget.reserve(command_id="same-task-reserve", account_id="budget-a", task_id="task-a", run_id="run-a", amount=5, child_runs=1)
        self.assertEqual(self.budget.reserve(command_id="same-task-reserve", account_id="budget-a", task_id="task-a", run_id="run-a", amount=5, child_runs=1), reservation)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_reservations WHERE command_id='same-task-reserve'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_ledger WHERE command_id='same-task-reserve' AND action='RESERVED'").fetchone()[0], 1)
        # Model a persisted reservation written by the pre-task-bound API.
        legacy_request = {"account_id":"budget-a","run_id":"legacy-run","amount":1,"model_calls":0,"tool_calls":0,"child_runs":0}
        legacy_hash = self.store._request_hash("reserve_budget", legacy_request)
        with self.store._connection() as conn:
            conn.execute("UPDATE budget_accounts SET reserved=reserved+1 WHERE account_id='budget-a'")
            conn.execute("INSERT INTO budget_reservations(reservation_id,account_id,run_id,amount,model_calls,tool_calls,child_runs,state,command_id,created_at) VALUES('legacy-reservation','budget-a','legacy-run',1,0,0,0,'RESERVED','legacy-reserve-command','2026-09-25T00:00:00Z')")
            self.store._record_command(conn, "legacy-reserve-command", "reserve_budget", legacy_hash, {"reservation_id":"legacy-reservation"})
            conn.execute("INSERT INTO budget_ledger(reservation_id,command_id,action,amount,created_at) VALUES('legacy-reservation','legacy-reserve-command','RESERVED',1,'2026-09-25T00:00:00Z')")
        self.assertEqual(self.budget.reserve(command_id="legacy-reserve-command", account_id="budget-a", task_id="task-a", run_id="legacy-run", amount=1), "legacy-reservation")

    def test_release_frees_reserved_budget_without_consuming_it(self):
        self.budget.create_account(command_id="cmd-release-account", account_id="budget-release", task_id="task-release", amount_limit=10, unit="microcredits", model_call_limit=2, tool_call_limit=2, child_run_limit=2)
        reservation = self.budget.reserve(command_id="cmd-release-reserve", account_id="budget-release", task_id="task-release", run_id="run-release", amount=8, tool_calls=1)
        self.budget.release(command_id="cmd-release-command", reservation_id=reservation)
        self.budget.release(command_id="cmd-release-command", reservation_id=reservation)
        with self.store._connection() as conn:
            account = conn.execute("SELECT reserved,consumed,tool_calls_reserved,tool_calls_consumed FROM budget_accounts WHERE account_id='budget-release'").fetchone()
        self.assertEqual(tuple(account), (0, 0, 0, 0))

    def _reservation_request(self, reservation_id):
        with self.store._connection() as conn:
            row = conn.execute("SELECT command_id,run_id FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            return row["command_id"], row["run_id"]


if __name__ == "__main__":
    unittest.main()
