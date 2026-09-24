import unittest
import tempfile
import contextlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from adapters.client.__main__ import _parser, _runtime, main
from kernel.authority import AuthorityService
from kernel.run import TraceRuntime


class _RuntimeStub:
    def current_mode(self):
        return {"mode": "RECOVERY"}

    def inspect_effect(self, **kwargs):
        return {"effect_id": "e", "execution_state": "FINISHED", "effect_outcome": "UNKNOWN", "reconciliation_status": "HUMAN_REQUIRED"}

    def inspect_purge(self, **kwargs):
        return {"executions": [{"status": "PARTIAL", "unresolved": ["ACTIVE_RUN:r"]}], "barriers": [{"status": "PARTIAL"}]}


class OperatorClientTests(unittest.TestCase):
    def setUp(self):
        self.client = OperatorClient(_RuntimeStub())

    def test_mode_view_explains_recovery_boundary(self):
        view = self.client.mode()
        self.assertEqual(view["mode"], "RECOVERY")
        self.assertIn("isolated", view["guidance"])

    def test_unknown_effect_is_never_rendered_as_failure_or_retryable(self):
        view = self.client.inspect_effect(grant_id="g", task_id="t", effect_id="e")
        self.assertEqual(view["effect_outcome"], "UNKNOWN")
        self.assertIn("do not retry commit", view["display_state"])

    def test_partial_purge_is_never_rendered_as_completed(self):
        view = self.client.inspect_purge(grant_id="g", task_id="t", plan_id="p")
        self.assertIn("PARTIAL", view["display_state"])
        self.assertNotEqual(view["display_state"], "COMPLETED")

    def test_cli_has_documented_mode_and_inspect_surfaces(self):
        parsed = _parser().parse_args(["--data-root", "nexus-data", "inspect", "effect", "fx-1", "--task-id", "task-1", "--grant-id", "grant-1"])
        self.assertEqual((parsed.command, parsed.inspect_kind, parsed.effect_id), ("inspect", "effect", "fx-1"))
        mode = _parser().parse_args(["--data-root", "nexus-data", "--policy", "policy.json", "mode", "set", "SAFE", "--command-id", "mode-1", "--grant-id", "grant-1", "--task-id", "task-1", "--classification-assertion-ref", "class-evt-mode-1"])
        self.assertEqual((mode.command, mode.mode, mode.classification_assertion_ref), ("mode", "SAFE", "class-evt-mode-1"))

    def test_cli_mode_change_uses_policy_and_persists_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            policy_path = Path(directory) / "nexus-policy.json"
            policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
            policy["trust_anchors"] = ["human-root"]
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            store = ObjectStore(root, policy=policy)
            authority = AuthorityService(store, policy)
            now = datetime.now(timezone.utc)
            authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":"human-root","principal_type":"HUMAN","status":"ACTIVE"}, "cli-human")
            authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":"operator","principal_type":"SERVICE","status":"ACTIVE"}, "cli-operator")
            authority.register_trust_anchor({"schema_id":"nexus.trust_anchor","schema_version":1,"anchor_id":"cli-anchor","principal_id":"human-root","policy_ref":"1"}, "cli-anchor")
            authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"cli-grant","issued_by":"human-root","granted_to":"operator","task_scope":["cli-task"],"resource_scope":["runtime-mode:instance","cli-run","evt-cli-run-create","evt-cli-mode-safe"],"action_scope":["RUN_CREATE","RUNTIME_CONFIGURE","TRACE_APPEND","CLASSIFY"],"audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "cli-grant-create")
            trace = TraceRuntime(store, authority)
            trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"cli-task","requester_id":"human-root","status":"CREATED","created_at":now.isoformat(),"command_id":"cli-task-create"})
            for assertion_id, subject_type, subject_ref in (("cli-run-class", "RUN", "cli-run"), ("cli-run-event-class", "TRACE_EVENT", "evt-cli-run-create")):
                authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":subject_type,"subject_ref":subject_ref,"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"isolated CLI integration test","actor_id":"operator"}, grant_id="cli-grant", task_id="cli-task", audience="nexus-runtime", command_id="classify-"+assertion_id)
            trace.create_run({"schema_id":"nexus.run","schema_version":1,"run_id":"cli-run","task_id":"cli-task","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"cli-grant","data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},"classification_assertion_ref":"cli-run-class","created_at":now.isoformat()}, command_id="cli-run-create", event_classification_assertion_ref="cli-run-event-class")
            authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"cli-mode-event-class","subject_type":"TRACE_EVENT","subject_ref":"evt-cli-mode-safe","sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"isolated CLI mode test","actor_id":"operator"}, grant_id="cli-grant", task_id="cli-task", audience="nexus-runtime", command_id="classify-cli-mode-event")
            store.close()

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["--data-root", str(root), "--policy", str(policy_path), "mode", "set", "SAFE", "--command-id", "cli-mode-safe", "--grant-id", "cli-grant", "--task-id", "cli-task", "--classification-assertion-ref", "cli-mode-event-class"])
            self.assertEqual(exit_code, 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertEqual(result["trace_event_id"], "evt-cli-mode-safe")
            reopened = ObjectStore(root, policy=policy)
            self.assertEqual(reopened._current_runtime_mode(), "SAFE")
            with reopened._connection() as conn:
                event = conn.execute("SELECT event_json FROM trace_events WHERE event_id='evt-cli-mode-safe'").fetchone()
            self.assertEqual(json.loads(event["event_json"])["typed_metadata"], {"previous_mode":"NORMAL","next_mode":"SAFE"})
            reopened.close()

    def test_cli_refuses_to_create_a_database_as_a_side_effect(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "No existing Nexus database"):
                _runtime(Path(root))
            self.assertFalse((Path(root) / "nexus.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
