import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from kernel.effect import AmbiguousDispatch, DeterministicEffectService


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "academy" / "failure-recovery-phase3-fixture.json"
RESULT = ROOT / "eval" / "academy" / "results" / "failure-recovery-phase3.json"
RUNNER_PATH = ROOT / "scripts" / "eval" / "run_failure_recovery_phase3.py"
SPEC = importlib.util.spec_from_file_location("academy_phase3_runner", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(RUNNER)


class FailureRecoveryPhase3Tests(unittest.TestCase):
    def test_committed_object_response_loss_replays_once_after_reopen(self):
        from tests.integration.test_object_store import ObjectStoreTests
        from kernel.object.errors import CommandConflict
        from adapters.storage import ObjectStore

        fixture = ObjectStoreTests("test_payload_is_content_addressed_verified_and_idempotent")
        fixture.setUp()
        reopened = None
        try:
            object_id = "p3-response-loss-object"
            payload = b"synthetic response-loss payload"
            class_ref = fixture._classify(object_id)
            request = {"command_id":"p3-response-loss-command", "object_id":object_id,
                "payload":payload, "object_type":"artifact", "created_by_run":"run_test",
                "classification_assertion_ref":class_ref}
            # The caller intentionally drops the committed API return value.
            fixture.store.put_object(**request)
            with fixture.store._connection() as conn:
                before = tuple(conn.execute("SELECT "
                    "(SELECT COUNT(*) FROM object_envelopes),"
                    "(SELECT COUNT(*) FROM runs),"
                    "(SELECT COUNT(*) FROM effects),"
                    "(SELECT COUNT(*) FROM budget_reservations),"
                    "(SELECT COUNT(*) FROM command_ledger),"
                    "(SELECT COUNT(*) FROM trace_events)").fetchone())
            fixture.store.close()
            reopened = ObjectStore(fixture.root / "data")
            replayed = reopened.put_object(**request)
            self.assertEqual(replayed, object_id)
            with reopened._connection() as conn:
                after = tuple(conn.execute("SELECT "
                    "(SELECT COUNT(*) FROM object_envelopes),"
                    "(SELECT COUNT(*) FROM runs),"
                    "(SELECT COUNT(*) FROM effects),"
                    "(SELECT COUNT(*) FROM budget_reservations),"
                    "(SELECT COUNT(*) FROM command_ledger),"
                    "(SELECT COUNT(*) FROM trace_events)").fetchone())
            self.assertEqual(after, before)
            with self.assertRaises(CommandConflict):
                reopened.put_object(**{**request, "payload":b"changed synthetic request"})
            with reopened._connection() as conn:
                self.assertEqual(tuple(conn.execute("SELECT "
                    "(SELECT COUNT(*) FROM object_envelopes),"
                    "(SELECT COUNT(*) FROM runs),"
                    "(SELECT COUNT(*) FROM effects),"
                    "(SELECT COUNT(*) FROM budget_reservations),"
                    "(SELECT COUNT(*) FROM command_ledger),"
                    "(SELECT COUNT(*) FROM trace_events)").fetchone()), before)
        finally:
            if reopened is not None:
                reopened.close()
            fixture.doCleanups()
            fixture.temp.cleanup()

    def test_failure_matrix_is_frozen_and_result_matches_its_hash(self):
        raw = FIXTURE.read_bytes()
        fixture = json.loads(raw)
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        self.assertTrue(fixture["frozen_before_evaluation"])
        self.assertTrue(fixture["synthetic_only"])
        self.assertEqual(len(fixture["cases"]), 27)
        self.assertEqual(len({case["case_id"] for case in fixture["cases"]}), 27)
        self.assertEqual(result["fixture_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["case_count"], 27)
        self.assertEqual(result["unique_evidence_test_count"], 23)
        self.assertEqual(result["mapped_cases_passed"], 27)
        self.assertEqual(result["cases_with_denial_expectation"], 27)
        self.assertEqual(result["mapped_denial_cases_passed"], 27)
        self.assertEqual(result["evidence_class"], "REGRESSION_BACKED_OPERATIONAL_QUALIFICATION")
        self.assertNotIn("observed_fail_closed_count", result)
        self.assertNotIn("recovery_success_count", result)
        self.assertEqual(result["blocker_case_count"], 0)
        self.assertEqual(result["inconclusive_case_count"], 0)

    def test_phase2_truthful_provenance_correction_is_preserved(self):
        fixture = json.loads((ROOT / "eval" / "academy" / "retrieval-evidence-phase2-fixture.json").read_text(encoding="utf-8"))
        result = json.loads((ROOT / "eval" / "academy" / "results" / "retrieval-evidence-phase2.json").read_text(encoding="utf-8"))
        self.assertTrue(fixture["synthetic_nonproduction_fixture"])
        self.assertTrue(fixture["contains_mixed_test_classifications"])
        self.assertIn("PERSONAL", {row["classification"] for row in fixture["items"]})
        receipt = result["real_public_search"]
        self.assertEqual(receipt["observation_source"], "CALLER_OBSERVED_HOST_SEARCH")
        self.assertFalse(receipt["network_search_executed_by_runner"])
        self.assertTrue(receipt["receipt_persisted_by_runner"])
        self.assertEqual(receipt["reproducibility"], "HOST_OBSERVATION_NOT_REPERFORMED_BY_COMMITTED_HARNESS")

    def test_journal_identity_mismatch_forces_recovery(self):
        from tests.integration.test_object_store import ObjectStoreTests
        from adapters.storage import ObjectStore
        from kernel.purge.journal import IndependentPurgeJournal
        fixture = ObjectStoreTests("test_payload_is_content_addressed_verified_and_idempotent")
        fixture.setUp()
        reopened = None
        try:
            data_root = fixture.root / "data"
            fixture.store.close()
            alternate_path = fixture.root / "alternate-purge-journal.jsonl"
            alternate = IndependentPurgeJournal(alternate_path, data_root)
            alternate.ensure_empty_exists()
            reopened = ObjectStore(data_root, independent_purge_journal_path=alternate_path)
            self.assertEqual(reopened._current_runtime_mode(), "RECOVERY")
            from kernel.runtime.errors import RuntimeDenied
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                reopened.get_object_metadata("not-present")
        finally:
            if reopened is not None:
                reopened.close()
            fixture.doCleanups()
            fixture.temp.cleanup()

    def test_fake_dispatch_outcomes_reconcile_without_blind_retry(self):
        # Reuses only the repository's fully synthetic Runtime/Authority fixture.
        from tests.integration.test_deterministic_runtime import DeterministicRuntimeTests

        scenarios = (
            ("COMMITTED", None, "COMMITTED"),
            ("NOT_COMMITTED", None, "NOT_COMMITTED"),
            ("AMBIGUOUS", None, "UNKNOWN"),
            ("AMBIGUOUS", "COMMITTED", "COMMITTED"),
            ("AMBIGUOUS", "NOT_COMMITTED", "NOT_COMMITTED"),
            ("AMBIGUOUS", "INCONCLUSIVE", "UNKNOWN"),
        )
        observed = []
        for index, (dispatch_result, reconciliation_result, expected) in enumerate(scenarios):
            with self.subTest(dispatch=dispatch_result, reconciliation=reconciliation_result):
                fixture = DeterministicRuntimeTests("test_unknown_effect_is_reconciled_without_retry_and_compensation_is_independent")
                fixture.setUp()
                try:
                    fixture.runtime.create_dag(command_id=f"p3-dag-{index}", task_id="task-1", root_run_id="run-root", nodes=fixture._nodes())
                    fixture._activate_root()

                    class Dispatcher:
                        calls = 0
                        def dispatch(self, **kwargs):
                            self.calls += 1
                            if dispatch_result == "AMBIGUOUS":
                                raise AmbiguousDispatch("synthetic acknowledgement loss")
                            return {"outcome": dispatch_result, "receipt_ref": f"synthetic-receipt-{index}"}

                    class Reconciliation:
                        channel_id = "fake-authoritative"
                        authoritative = True
                        calls = 0
                        def query(self, **kwargs):
                            self.calls += 1
                            result = reconciliation_result or "INCONCLUSIVE"
                            payload = {"outcome": result}
                            if result in {"COMMITTED", "NOT_COMMITTED"}:
                                payload["evidence_ref"] = f"synthetic-reconciliation-evidence-{index}"
                            return payload

                    dispatcher, channel = Dispatcher(), Reconciliation()
                    effects = DeterministicEffectService(fixture.store, fixture.authority, fixture.trace,
                        fixture.budget, dispatchers={"tool-read": dispatcher},
                        reconciliation_ports={"fake-authoritative": channel})
                    descriptor = {"schema_id":"nexus.tool_descriptor","schema_version":1,"tool_id":"tool-read","version":"1",
                        "input_schema_id":"nexus.object@1.schema.json","output_schema_id":"nexus.object@1.schema.json",
                        "effect_class":"EXTERNAL_REVERSIBLE","required_authority":["FAKE_WRITE"],"required_classifications":["PUBLIC"],
                        "idempotency_support":True,"reconciliation_capability":"fake-authoritative",
                        "compensation_capability":"fake-compensate","network_egress":False,"risk_tags":["synthetic"],"review_status":"APPROVED"}
                    effects.register_descriptor(command_id=f"p3-desc-{index}",grant_id="grant-root",task_id="task-1",descriptor=descriptor)
                    fixture._schedule("node-tool","run-tool", "tool-agent", "grant-tool", command_id=f"p3-schedule-{index}")
                    fixture._advance("run-tool",f"p3-running-{index}","READY","RUNNING","tool-agent")
                    digest = fixture.store.get_object_metadata("input-1")["integrity_hash"]
                    effect_id = f"p3-effect-{index}"
                    approval_id = f"p3-approval-{index}"
                    fixture.authority.create_approval({"schema_id":"nexus.approval_decision","schema_version":1,
                        "approval_id":approval_id,"approver_principal_id":"human-root","target_type":"FAKE_WRITE",
                        "target_ref":"sandbox-target","effect_id":effect_id,"payload_integrity_hash":digest,
                        "decision":"APPROVE","approved_scope":["FAKE_WRITE","sandbox-target"],"policy_version":"1",
                        "issued_at":fixture._now()},f"p3-create-approval-{index}")
                    effect = {"schema_id":"nexus.effect","schema_version":1,"effect_id":effect_id,"run_id":"run-tool",
                        "tool_id":"tool-read","action_type":"FAKE_WRITE","target_ref":"sandbox-target",
                        "payload_integrity_hash":digest,"idempotency_key":f"p3-idem-{index}","grant_id":"grant-tool",
                        "approval_ref":approval_id,"execution_state":"DECLARED","effect_outcome":"UNDETERMINED",
                        "reconciliation_status":"NOT_REQUIRED"}
                    effects.create_effect(command_id=f"p3-create-effect-{index}",effect=effect,payload_object_ref="input-1",
                        classification_assertion_ref=fixture._event_class(f"p3-create-effect-{index}","tool-agent"))
                    effects.prepare(command_id=f"p3-prepare-{index}",effect_id=effect_id,
                        classification_assertion_ref=fixture._event_class(f"p3-prepare-{index}","tool-agent"))
                    effects.authorize(command_id=f"p3-authorize-{index}",effect_id=effect_id,
                        classification_assertion_ref=fixture._event_class(f"p3-authorize-{index}","tool-agent"))
                    commit_class = fixture._event_class(f"p3-commit-{index}-outcome","tool-agent")
                    start_class = fixture._event_class(f"p3-commit-{index}-start","tool-agent")
                    committed = effects.commit(command_id=f"p3-commit-{index}",effect_id=effect_id,
                        classification_assertion_ref=commit_class,start_classification_assertion_ref=start_class)
                    self.assertEqual(committed["effect_outcome"], "UNKNOWN" if dispatch_result == "AMBIGUOUS" else dispatch_result)
                    self.assertEqual(dispatcher.calls, 1)
                    if reconciliation_result is not None:
                        resolved = effects.reconcile(command_id=f"p3-reconcile-{index}",effect_id=effect_id,
                            classification_assertion_ref=fixture._event_class(f"p3-reconcile-{index}","tool-agent"))
                        self.assertEqual(resolved["effect_outcome"], expected)
                    replay = effects.commit(command_id=f"p3-commit-{index}",effect_id=effect_id,
                        classification_assertion_ref=commit_class,start_classification_assertion_ref=start_class)
                    self.assertEqual(replay, committed)
                    self.assertEqual(dispatcher.calls, 1, "exact replay must not redispatch")
                    if reconciliation_result is not None:
                        self.assertEqual(channel.calls, 1)
                    self.assertEqual(effects._get(effect_id)["effect_outcome"], expected)
                    observed.append(expected)
                finally:
                    fixture.doCleanups()
        self.assertEqual(observed, [row[2] for row in scenarios])


if __name__ == "__main__":
    unittest.main()
