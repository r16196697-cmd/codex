import unittest
import tempfile
from pathlib import Path

from adapters.client.operator import OperatorClient
from adapters.client.__main__ import _parser, _runtime


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

    def test_cli_refuses_to_create_a_database_as_a_side_effect(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "No existing Nexus database"):
                _runtime(Path(root))
            self.assertFalse((Path(root) / "nexus.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
