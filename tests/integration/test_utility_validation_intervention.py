from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from adapters.client.utility_validation import UtilityInterventionComposer
from kernel.skills.application import SkillApplicationService
from kernel.skills.host import CodexAgentSkillsInventoryAdapter
from scripts.eval.run_utility_validation_v1 import CodexCliController, inspect_frozen_workspace
from scripts.eval.utility_validation_observation import UtilityObservationLedger, sha256_bytes


class FakeInventory:
    def __init__(self, inventory):
        self._inventory = inventory

    def inventory(self):
        return self._inventory


class Tick:
    def __init__(self):
        self.value = 1_000_000_000

    def __call__(self):
        self.value += 1_000_000_000
        return self.value


class UtilityInterventionIntegrationTests(unittest.TestCase):
    """Use the existing real Core fixture; all CLI subprocesses are mocked."""

    def setUp(self):
        from tests.integration.test_context_metering import ContextMeteringTests
        self.core_fixture = ContextMeteringTests()
        self.core_fixture.setUp()
        self.addCleanup(self.core_fixture.doCleanups)
        self.fixture = self.core_fixture
        self.repo = Path(__file__).resolve().parents[2]
        self.tick = Tick()
        self.workspace = Path(self.fixture.temp.name) / "utility-frozen-source"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.workspace)], check=True, shell=False, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.name", "Utility Fixture"], check=True, shell=False, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "config", "user.email", "utility-fixture@example.invalid"], check=True, shell=False, capture_output=True)
        (self.workspace / "shared-source.txt").write_text("shared source baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.workspace), "add", "shared-source.txt"], check=True, shell=False, capture_output=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "--quiet", "-m", "Frozen shared source"], check=True, shell=False, capture_output=True)
        self.starting_commit = subprocess.run(
            ["git", "-C", str(self.workspace), "rev-parse", "HEAD"], check=True,
            shell=False, capture_output=True, text=True,
        ).stdout.strip()
        self.workspace_identity = inspect_frozen_workspace(
            self.workspace, repository_root=self.repo,
            expected_starting_commit=self.starting_commit,
        ).identity_sha256

    def _ledger_session(self, trial_id, task_input):
        ledger_root = Path(self.fixture.temp.name) / "private-utility-observations"
        ledger = UtilityObservationLedger(
            ledger_root / f"{trial_id}.jsonl", repository_root=self.repo,
            clock=lambda: "2026-09-28T00:00:00Z", monotonic_ns=self.tick,
        )
        session = ledger.open_trial(
            study_id="utility-instrumentation", pair_id="pair-context-1", trial_id=trial_id,
            assigned_condition="NEXUS_ACTIVE", task_card_sha256="a" * 64,
            task_variant_sha256="b" * 64, task_payload_sha256=sha256_bytes(task_input),
            starting_commit=self.starting_commit, workspace_identity_sha256=self.workspace_identity,
            environment_snapshot_ref="env:snapshot-1",
            environment_snapshot_sha256="d" * 64, acceptance_rubric_ref="rubric:v1",
            acceptance_rubric_sha256="e" * 64, nexus_task_id=self.fixture.task_id,
            nexus_run_id=self.fixture.run_id,
        )
        return ledger, session

    def _application(self, registry, inventory):
        return SkillApplicationService(
            registry=registry, host_inventory=inventory, authority=self.fixture.authority,
            operator_confirmation=SimpleNamespace(confirm_enable=lambda _summary: True),
        )

    @staticmethod
    def _cli_output(thread_id):
        events = (
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message", "text": "ok"}},
            {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 2}},
        )
        return b"".join(json.dumps(item).encode("utf-8") + b"\n" for item in events)

    def test_real_context_and_host_native_resolution_bind_to_same_mocked_cli_invocation(self):
        registry, package = self.fixture._skill_service_and_package()
        skill_id, package_doc = self.fixture._register_skill(service=registry, package=package, namespace="utility-native")
        inventory = FakeInventory({
            "adapter_id": "sanitized-test-adapter", "inventory_complete": False,
            "skills": [{"name": package_doc["name"], "availability": "AVAILABLE",
                        "provenance": "ADAPTER_DISCOVERY",
                        "revision_sha256": package_doc["package_manifest_sha256"]}],
        })
        app = self._application(registry, inventory)
        composer = UtilityInterventionComposer(context_packs=self.fixture.context, skill_application=app)
        task_input = b"Use the shared repository source and answer this frozen task.\n"
        ledger, session = self._ledger_session("native-context", task_input)
        # Simulate measurable Nexus preparation time after TRIAL_OPENED and
        # before the Host process timer starts.
        for _ in range(4):
            self.tick()
        invocation_id = "utility-native-invocation"
        with mock.patch.object(registry, "_read_package", side_effect=AssertionError("HOST_NATIVE must not load instruction body")):
            prepared = composer.prepare_b(
                session, task_input=task_input, task_id=self.fixture.task_id, run_id=self.fixture.run_id,
                grant_id="ctx-grant", classification_assertion_ref="class-ctx-pack-one",
                context_pack_request={"pack_object_id": self.fixture.pack_ids[0],
                                      "source_refs": [self.fixture.source_ids[0]]},
                skill_query="tiny helper", skill_resolution_command_id="utility-native-resolve",
                context_command_id="utility-native-context", controller_invocation_id=invocation_id,
            )
        self.assertIsNotNone(prepared)
        self.assertIsNone(prepared.skill_payload_sha256)
        self.assertIsNone(prepared.skill_instruction_object_ref)
        envelope = json.loads(prepared.payload)
        self.assertEqual(envelope["task_input_utf8"], task_input.decode())
        self.assertEqual(envelope["context_pack"]["run_id"], self.fixture.run_id)
        self.assertEqual(envelope["context_pack"]["entries"][0]["source_ref"], self.fixture.source_ids[0])
        self.assertEqual(registry.latest_resolution()["resolution"], "HOST_NATIVE")
        self.assertEqual(registry.latest_resolution()["instruction_load_status"], "NOT_LOADED")
        self.assertEqual(registry.latest_resolution()["model_visible_exposure"], "UNKNOWN")

        def runner(argv, **kwargs):
            self.assertEqual(kwargs["input"], prepared.payload)
            self.assertEqual(Path(kwargs["cwd"]).resolve(), self.workspace.resolve())
            return SimpleNamespace(returncode=0, stdout=self._cli_output("thread-native"), stderr=b"")

        controller = CodexCliController(ledger, repository_root=self.repo,
                                        subprocess_runner=runner, monotonic_ns=self.tick)
        invocation = controller.invoke(
            session, prepared.payload, workspace=self.workspace,
            controller_invocation_id=invocation_id,
            acceptance_evaluator=lambda **_kwargs: {
                "verdict": "PASS", "evidence_refs": ["acceptance:fixture"],
                "evidence_sha256": ["f" * 64], "inconclusive_reason": None,
            },
        )
        events = ledger.trial_events("utility-instrumentation", "native-context")
        intervention = next(event for event in events if event["event_type"] == "INTERVENTION_PREPARED")
        submitted = next(event for event in events if event["event_type"] == "HOST_INVOCATION_STARTED")
        self.assertEqual(intervention["payload"]["preparation_status"], "PREPARED_NOT_SUBMITTED")
        self.assertEqual(intervention["payload"]["skill_resolution_status"], "RESOLVED")
        self.assertEqual(intervention["payload"]["skill_resolution"], "HOST_NATIVE")
        self.assertEqual(intervention["payload"]["host_skill_availability"], "AVAILABLE")
        self.assertEqual(intervention["payload"]["host_inventory_provenance"], "ADAPTER_DISCOVERY")
        self.assertEqual(intervention["payload"]["skill_instruction_load_status"], "NOT_LOADED")
        self.assertIsNone(intervention["payload"]["skill_instruction_byte_size"])
        self.assertGreaterEqual(intervention["payload"]["skill_selection_latency_ms"], 0)
        self.assertEqual(intervention["payload"]["boundary"], "CODEX_CLI_INPUT")
        self.assertEqual(intervention["payload"]["controller_invocation_id"], submitted["payload"]["controller_invocation_id"])
        self.assertEqual(submitted["payload"]["input_sha256"], sha256_bytes(prepared.payload))
        self.assertEqual(submitted["payload"]["cwd_policy"], "FROZEN_REPO_SNAPSHOT")
        self.assertEqual(submitted["payload"]["workspace_starting_commit"], self.starting_commit)
        self.assertEqual(submitted["payload"]["workspace_identity_sha256"], self.workspace_identity)
        self.assertTrue(submitted["payload"]["workspace_clean_observed"])
        completed = next(event for event in events if event["event_type"] == "HOST_INVOCATION_COMPLETED")
        self.assertEqual(completed["payload"]["input_submission_status"], "CONTROLLER_SUBMITTED_TO_CODEX_CLI")
        self.assertEqual(completed["payload"]["received_intervention"], "EVAL_INTERVENTION_TRANSPORT")
        self.assertEqual(completed["payload"]["submitted_input_sha256"], sha256_bytes(prepared.payload))
        self.assertEqual(invocation["controller_invocation_id"], invocation_id)
        self.assertGreater(invocation["trial_wall_elapsed_ms"], invocation["host_process_elapsed_ms"])
        self.assertNotIn(package_doc["files"]["SKILL.md"].decode(), ledger.path.read_text(encoding="utf-8"))

    def test_governed_fallback_artifact_is_integrity_bound_inside_context_pack(self):
        registry, package = self.fixture._skill_service_and_package()
        skill_id, package_doc = self.fixture._register_skill(service=registry, package=package, namespace="utility-fallback")
        from kernel.skills.service import _canonical, _sha256
        resolution_command = "utility-fallback-resolve"
        fallback_ref = "skill-fallback-" + _sha256(_canonical({
            "command_id": resolution_command, "task_id": self.fixture.task_id,
            "run_id": self.fixture.run_id, "skill_id": skill_id,
            "instruction_sha256": package_doc["skill_md_sha256"],
        }))
        with mock.patch.object(self.fixture.authority, "evaluate_authorization", return_value=True):
            self.fixture.authority.record_classification_assertion(
                self.fixture._class("class-" + fallback_ref, "OBJECT", fallback_ref),
                grant_id="ctx-grant", task_id=self.fixture.task_id,
                audience="nexus-runtime", command_id="classify-utility-fallback",
            )
        app = self._application(registry, FakeInventory({
            "adapter_id": "controlled-complete-test-adapter", "inventory_complete": True, "skills": [],
        }))
        composer = UtilityInterventionComposer(context_packs=self.fixture.context, skill_application=app)
        task_input = b"Apply the registered portable capability to this task.\n"
        ledger, session = self._ledger_session("fallback-context", task_input)
        with mock.patch.object(self.fixture.authority, "evaluate_authorization", return_value=True):
            prepared = composer.prepare_b(
                session, task_input=task_input, task_id=self.fixture.task_id, run_id=self.fixture.run_id,
                grant_id="ctx-grant", classification_assertion_ref="class-" + fallback_ref,
                context_pack_request={"pack_object_id": self.fixture.pack_ids[0],
                                      "classification_assertion_ref": "class-ctx-pack-one", "source_refs": []},
                skill_query="tiny helper", skill_resolution_command_id=resolution_command,
                context_command_id="utility-fallback-context", controller_invocation_id="utility-fallback-invocation",
            )
        self.fixture.store.verify_object(fallback_ref)
        fallback_document = json.loads(self.fixture.store.get_payload(fallback_ref))
        self.assertEqual(fallback_document["instruction_sha256"], package_doc["skill_md_sha256"])
        self.assertEqual(prepared.skill_instruction_object_ref, fallback_ref)
        self.assertEqual(prepared.skill_payload_sha256, package_doc["skill_md_sha256"])
        self.assertEqual(prepared.context_pack_byte_size, len(self.fixture.store.get_payload(self.fixture.pack_ids[0])))
        pack_document = json.loads(self.fixture.store.get_payload(self.fixture.pack_ids[0]))
        entry = next(item for item in pack_document["entries"] if item["source_ref"] == fallback_ref)
        self.assertEqual(json.loads(entry["content"])["instruction_sha256"], package_doc["skill_md_sha256"])
        self.assertIn("Instruction body for tiny-helper", prepared.payload.decode())
        self.assertEqual(registry.latest_resolution()["resolution"], "NEXUS_FALLBACK")
        intervention = next(event for event in ledger.trial_events("utility-instrumentation", "fallback-context")
                            if event["event_type"] == "INTERVENTION_PREPARED")
        self.assertEqual(intervention["payload"]["skill_resolution"], "NEXUS_FALLBACK")
        self.assertEqual(intervention["payload"]["skill_candidate_count"], 1)
        self.assertEqual(intervention["payload"]["selected_skill_ref"], skill_id)
        self.assertEqual(intervention["payload"]["skill_instruction_load_status"], "LOADED_TO_GOVERNED_ARTIFACT")
        self.assertEqual(intervention["payload"]["skill_instruction_byte_size"], len(package_doc["files"]["SKILL.md"]))
        self.assertEqual(registry.latest_resolution()["delivery_status"], "UNKNOWN")
        self.assertEqual(registry.latest_resolution()["model_visible_exposure"], "UNKNOWN")
        self.assertNotIn("Instruction body for tiny-helper", ledger.path.read_text(encoding="utf-8"))

    def test_partial_inventory_absence_remains_unknown_and_never_fallback(self):
        registry, package = self.fixture._skill_service_and_package()
        self.fixture._register_skill(service=registry, package=package, namespace="utility-unknown")
        empty_root = Path(self.fixture.temp.name) / "empty-host-skills"
        empty_root.mkdir()
        inventory = CodexAgentSkillsInventoryAdapter(
            package_reader=registry.inventory_package_metadata, roots={"USER": empty_root},
        )
        app = self._application(registry, inventory)
        composer = UtilityInterventionComposer(context_packs=self.fixture.context, skill_application=app)
        task_input = b"Resolve this task with available governed capabilities.\n"
        ledger, session = self._ledger_session("partial-absence", task_input)
        with mock.patch.object(registry, "_read_package", side_effect=AssertionError("partial absence cannot load fallback")):
            prepared = composer.prepare_b(
                session, task_input=task_input, task_id=self.fixture.task_id, run_id=self.fixture.run_id,
                grant_id="ctx-grant", classification_assertion_ref="unused",
                context_pack_request=None,
                skill_query="tiny helper", skill_resolution_command_id="utility-partial-absence",
                context_command_id="utility-partial-context", controller_invocation_id="utility-partial-invocation",
            )
        self.assertIsNone(prepared)
        result = registry.latest_resolution()
        self.assertEqual(result["resolution"], "UNSUPPORTED")
        self.assertEqual(result["host_native_availability"], "UNKNOWN")
        self.assertEqual(result["reason"], "HOST_NATIVE_AVAILABILITY_UNKNOWN")
        self.assertEqual(result["instruction_load_status"], "NOT_LOADED")
        self.assertEqual(result["model_visible_exposure"], "UNKNOWN")
        self.assertNotEqual(result["resolution"], "NEXUS_FALLBACK")
        intervention = next(event for event in ledger.trial_events("utility-instrumentation", "partial-absence")
                            if event["event_type"] == "INTERVENTION_PREPARED")
        self.assertEqual(intervention["payload"]["skill_resolution_status"], "UNSUPPORTED")
        self.assertEqual(intervention["payload"]["skill_resolution"], "UNSUPPORTED")
        self.assertEqual(intervention["payload"]["host_skill_availability"], "UNKNOWN")
        self.assertIsNone(intervention["payload"]["skill_payload_sha256"])
        self.assertEqual(ledger.trial_events("utility-instrumentation", "partial-absence")[-1]["event_type"], "TRIAL_INCONCLUSIVE")
