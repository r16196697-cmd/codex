"""T12 combined-mode and Purge recovery drill from a clone of the live Hosted root."""

import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.client.hosted import CodexHostedBridge
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.memory import MemoryService
from kernel.object.errors import PurgedObject
from kernel.purge import PurgeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime, RuntimeModeService
from kernel.runtime.errors import RuntimeDenied
from kernel.verification import VerificationService


class PersistentRootCloneRecoveryTests(unittest.TestCase):
    def test_persistent_root_clone_modes_and_purge_snapshot_replay(self):
        repo = Path(__file__).resolve().parents[2]
        live_root = repo / "nexus" / "data" / "codex-hosted-attached"
        if not (live_root / "nexus.sqlite").is_file():
            self.skipTest("persistent Hosted data root is not present in this checkout")
        temp = tempfile.TemporaryDirectory(prefix="nexus-t12-root-clone-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        clone = base / "clone"
        clone.mkdir()
        source = sqlite3.connect(live_root / "nexus.sqlite")
        target = sqlite3.connect(clone / "nexus.sqlite")
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        shutil.copytree(live_root / "objects", clone / "objects")
        shutil.copy2(live_root / "policy.json", clone / "policy.json")
        policy = json.loads((clone / "policy.json").read_text(encoding="utf-8"))
        journal = base / "independent" / "purge.jsonl"
        store = ObjectStore(clone, policy=policy, independent_purge_journal_path=journal)
        self.addCleanup(store.close)
        self.assertEqual(store._current_runtime_mode(), "NORMAL")
        authority = AuthorityService(store, policy)
        budget = BudgetService(store)
        trace = TraceRuntime(store, authority)
        runtime = DeterministicRuntime(store, authority, budget, trace)
        verifier = VerificationService(store, authority)
        bridge = CodexHostedBridge(store=store, authority=authority, budget=budget, trace=trace, runtime=runtime, verifier=verifier)

        task_id, root_run, agent = "t12-clone-task", "t12-clone-root", "t12-clone-agent"
        input_id, contract_id, manifest_id = "t12-clone-input", "t12-clone-contract", "t12-clone-root-manifest"
        mode_commands = {"SAFE": "t12-mode-safe", "STATELESS": "t12-mode-stateless", "NORMAL": "t12-mode-normal", "RECOVERY": "t12-mode-recovery"}
        event_ids = ["evt-t12-create-root-root-create", "evt-t12-create-root-trace-input", "evt-t12-create-root-root-ready", "evt-t12-create-root-root-running",
                     "evt-t12-root-verifying", "evt-t12-root-succeeded", *("evt-" + item for item in mode_commands.values()),
                     "evt-t12-post-recovery-safe", "evt-t12-post-recovery-stateless", "evt-t12-post-recovery-normal"]
        resources = ["runtime-mode:instance", root_run, input_id, contract_id, manifest_id, "t12-purge-plan", "t12-purge-record", *event_ids]
        now = datetime.now(timezone.utc)
        authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":agent,"principal_type":"SERVICE","status":"ACTIVE"}, "t12-register-agent")
        grant_id = "t12-clone-grant"
        authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":grant_id,
            "issued_by":"nexus-local-pilot-operator","granted_to":agent,"task_scope":[task_id],"resource_scope":resources,
            "action_scope":["RUN_CREATE","RUN_TRANSITION","TRACE_APPEND","OBJECT_WRITE","CLASSIFY","VERIFY","INSPECT","RUNTIME_CONFIGURE","MEMORY_RETAIN","MEMORY_SEARCH","PURGE_EXECUTE"],
            "audience_scope":["nexus-runtime","nexus-inspect"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),
            "status":"ACTIVE","policy_version":"1"}, "t12-create-root-grant")
        def classify(assertion_id, subject_type, subject_ref):
            authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,
                "assertion_id":assertion_id,"subject_type":subject_type,"subject_ref":subject_ref,"sensitivity_level":"PUBLIC",
                "handling_tags":[],"policy_version":"1","reason":"isolated persistent-root clone recovery test","actor_id":agent},
                grant_id=grant_id,task_id=task_id,audience="nexus-runtime",command_id="t12-classify-"+assertion_id)

        boundary = {"allowed_classifications":["PUBLIC"],"handling_tags":[]}
        classes = {
            "root_run": ("t12-class-root", "RUN", root_run),
            "root_created_event": ("t12-class-root-created", "TRACE_EVENT", event_ids[0]),
            "input_object": ("t12-class-input", "OBJECT", input_id),
            "input_event": ("t12-class-input-event", "TRACE_EVENT", event_ids[1]),
            "task_contract": ("t12-class-contract", "OBJECT", contract_id),
            "root_manifest": ("t12-class-manifest", "OBJECT", manifest_id),
            "root_ready_event": ("t12-class-ready", "TRACE_EVENT", event_ids[2]),
            "root_running_event": ("t12-class-running", "TRACE_EVENT", event_ids[3]),
        }
        contract = {"schema_id":"nexus.task_contract","schema_version":1,"goal":"T12 synthetic purge/recovery acceptance",
            "constraints":[],"success_criteria":["purge data remains unavailable after old snapshot replay"],"risk_class":"STANDARD",
            "routing_constraints":{"allowed_providers":["codex-host"],"forbidden_providers":[],"locality":"LOCAL_ONLY","network_required":False,"modalities":["text"]},
            "routing_preferences":{"optimize_for":"QUALITY"},"created_at":now.isoformat()}
        result = bridge.create_task_root(command_id="t12-create-root",task_id=task_id,requester_id="nexus-local-pilot-operator",grant_id=grant_id,
            root_run_id=root_run,budget_account_id="t12-budget",budget_limits={"amount_limit":1,"unit":"test","model_call_limit":0,"tool_call_limit":0,"child_run_limit":0},
            input_object_id=input_id,input_payload=b"synthetic purge source: purge marker",task_contract=contract,contract_object_id=contract_id,
            dag_nodes=[],root_manifest_object_id=manifest_id,data_boundary=boundary,
            classifications={key:{"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":value[0],"subject_type":value[1],"subject_ref":value[2],"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"isolated persistent-root clone recovery test","actor_id":agent} for key,value in classes.items()})
        self.assertEqual(result["status"], "RUNNING")
        for command, event_id in zip(("t12-root-verifying", "t12-root-succeeded", *mode_commands.values()), event_ids[4:]):
            classify("class-"+command, "TRACE_EVENT", event_id)
        modes = RuntimeModeService(store, authority)
        modes.set_mode(command_id=mode_commands["SAFE"],grant_id=grant_id,task_id=task_id,mode="SAFE",classification_assertion_ref="class-"+mode_commands["SAFE"])
        with self.assertRaises(RuntimeDenied):
            MemoryService(store,authority,verifier).retain_raw(command_id="t12-safe-retain-denied",object_id=input_id,run_id=root_run)
        modes.set_mode(command_id=mode_commands["STATELESS"],grant_id=grant_id,task_id=task_id,mode="STATELESS",classification_assertion_ref="class-"+mode_commands["STATELESS"])
        with self.assertRaises(RuntimeDenied):
            MemoryService(store,authority,verifier).search_raw(query="purge marker",run_id=root_run)
        modes.set_mode(command_id=mode_commands["NORMAL"],grant_id=grant_id,task_id=task_id,mode="NORMAL",classification_assertion_ref="class-"+mode_commands["NORMAL"])

        memory = MemoryService(store,authority,verifier)
        memory.retain_raw(command_id="t12-retain-raw",object_id=input_id,run_id=root_run)
        trace.transition_run(command_id="t12-root-verifying",run_id=root_run,expected_state="RUNNING",next_state="VERIFYING",classification_assertion_ref="class-t12-root-verifying")
        trace.transition_run(command_id="t12-root-succeeded",run_id=root_run,expected_state="VERIFYING",next_state="SUCCEEDED",classification_assertion_ref="class-t12-root-succeeded")
        purge = PurgeService(store,authority,memory,independent_journal_path=journal)
        plan = purge.plan(command_id="t12-plan-command",plan_id="t12-purge-plan",task_id=task_id,target_refs=[input_id])
        approval = {"schema_id":"nexus.approval_decision","schema_version":1,"approval_id":"t12-purge-approval",
            "approver_principal_id":"nexus-local-pilot-operator","target_type":"PURGE_EXECUTE","target_ref":"t12-purge-plan",
            "effect_id":"t12-purge-record","payload_integrity_hash":plan["plan_hash"],"decision":"APPROVE",
            "approved_scope":["PURGE_EXECUTE","t12-purge-plan"],"policy_version":"1","issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat()}
        authority.create_approval(approval,"t12-create-purge-approval")
        snapshot = base / "pre-purge-snapshot"
        snapshot.mkdir()
        dest = sqlite3.connect(snapshot / "nexus.sqlite")
        try:
            with store._connection() as src:
                src.backup(dest)
        finally:
            dest.close()
        shutil.copytree(clone / "objects",snapshot / "objects")
        shutil.copy2(clone / "policy.json",snapshot / "policy.json")
        outcome = purge.execute(command_id="t12-purge-execute",record_id="t12-purge-record",barrier_id="t12-purge-barrier",plan=plan,
            grant_id=grant_id,task_id=task_id,approval_id="t12-purge-approval")
        self.assertEqual(outcome["status"], "COMPLETED")
        self.assertEqual(memory.search_raw(query="purge marker",run_id=root_run), [])
        store.close()

        restored = base / "restored"
        restored.mkdir()
        shutil.copy2(snapshot / "nexus.sqlite",restored / "nexus.sqlite")
        shutil.copytree(snapshot / "objects",restored / "objects")
        shutil.copy2(snapshot / "policy.json",restored / "policy.json")
        # This is a genuine pre-purge NORMAL snapshot. Recovery must be forced
        # out-of-band before any Runtime/Memory/Inspect API is constructed.
        import sqlite3
        with sqlite3.connect(restored / "nexus.sqlite") as snapshot_conn:
            self.assertEqual(snapshot_conn.execute("SELECT mode FROM runtime_mode_state").fetchone()[0], "NORMAL")
        recovery_store = ObjectStore(restored,policy=policy,force_recovery=True,independent_purge_journal_path=journal)
        recovery_authority = AuthorityService(recovery_store,policy)
        recovery_memory = MemoryService(recovery_store,recovery_authority,VerificationService(recovery_store,recovery_authority))
        recovery_purge = PurgeService(recovery_store,recovery_authority,recovery_memory,independent_journal_path=journal)
        self.addCleanup(recovery_store.close)
        self.assertEqual(recovery_store._current_runtime_mode(), "RECOVERY")
        with self.assertRaises(RuntimeDenied):
            recovery_memory.search_raw(query="purge marker",run_id=root_run)
        recovery_runtime = DeterministicRuntime(recovery_store,recovery_authority,BudgetService(recovery_store),TraceRuntime(recovery_store,recovery_authority))
        with self.assertRaises(RuntimeDenied):
            recovery_runtime.inspect_task(grant_id=grant_id,task_id=task_id)
        report = recovery_purge.replay_independent_journal()
        self.assertTrue(report["normal_allowed"])
        self.assertEqual(report["held_refs"], 0)
        completed = RuntimeModeService(recovery_store,recovery_authority).complete_validated_recovery(command_id="t12-complete-recovery",purge_service=recovery_purge)
        self.assertEqual(completed["mode"], "NORMAL")
        with self.assertRaises(PurgedObject):
            recovery_store.get_payload(input_id)
        self.assertEqual(recovery_memory.search_raw(query="purge marker",run_id=root_run), [])
        def classify_recovery(assertion_id, subject_ref):
            recovery_authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,
                "assertion_id":assertion_id,"subject_type":"TRACE_EVENT","subject_ref":subject_ref,"sensitivity_level":"PUBLIC",
                "handling_tags":[],"policy_version":"1","reason":"isolated persistent-root clone recovery test","actor_id":agent},
                grant_id=grant_id,task_id=task_id,audience="nexus-runtime",command_id="t12-classify-"+assertion_id)
        for command in ("t12-post-recovery-safe", "t12-post-recovery-stateless", "t12-post-recovery-normal"):
            classify_recovery("class-"+command,"evt-"+command)
        recovery_modes = RuntimeModeService(recovery_store,recovery_authority)
        recovery_modes.set_mode(command_id="t12-post-recovery-safe",grant_id=grant_id,task_id=task_id,mode="SAFE",classification_assertion_ref="class-t12-post-recovery-safe")
        with self.assertRaises(RuntimeDenied):
            recovery_memory.retain_raw(command_id="t12-post-purge-safe-retain-denied",object_id=input_id,run_id=root_run)
        recovery_modes.set_mode(command_id="t12-post-recovery-stateless",grant_id=grant_id,task_id=task_id,mode="STATELESS",classification_assertion_ref="class-t12-post-recovery-stateless")
        with self.assertRaises(RuntimeDenied):
            recovery_memory.search_raw(query="purge marker",run_id=root_run)
        recovery_modes.set_mode(command_id="t12-post-recovery-normal",grant_id=grant_id,task_id=task_id,mode="NORMAL",classification_assertion_ref="class-t12-post-recovery-normal")
        self.assertEqual(recovery_store._current_runtime_mode(),"NORMAL")
        with recovery_store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 20)
            self.assertEqual(conn.execute("SELECT payload_state FROM object_states WHERE object_id=?",(input_id,)).fetchone()[0], "PURGED")
        recovery_store.close()
        reopened = ObjectStore(restored,policy=policy,independent_purge_journal_path=journal)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened._current_runtime_mode(),"NORMAL")
        with self.assertRaises(PurgedObject):
            reopened.get_payload(input_id)
        self.assertEqual(MemoryService(reopened,AuthorityService(reopened,policy),VerificationService(reopened,AuthorityService(reopened,policy))).search_raw(query="purge marker",run_id=root_run),[])
