from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from adapters.client.project_nexus_selfhost import (
    ProjectNexusSelfHostError,
    _required_resources,
    run_project_nexus_selfhost_bootstrap,
)
from adapters.client.hosted import CodexHostedBridge
from adapters.client.__main__ import main as client_main
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.context import ContextPackService
from kernel.memory.service import MemoryService
from kernel.metering import MeteringService
from kernel.object.errors import CommandConflict
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.verification import VerificationService
from tests.support.test_store import open_test_store


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, shell=False)
    if result.returncode:
        raise AssertionError("temporary local Git fixture command failed")
    return result.stdout.decode("ascii").strip()


class ProjectNexusSelfHostCompositionTests(unittest.TestCase):
    SOURCE_IDS = [
        "src-phase6-closure-v1",
        "src-utility-pilot-freeze-v1",
        "src-selfhost-design-v1",
        "src-fresh-bootstrap-guide-v1",
        "src-git-source-import-guide-v1",
        "src-foundation-overview-v1",
        "src-slice3-closure-v1",
        "src-bootstrap-contract-v1",
    ]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-stage3-composition-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        repo_root = Path(__file__).resolve().parents[2]
        self.policy = json.loads((repo_root / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["stage3-human"]
        self.policy_path = self.base / "policy.json"
        self.policy_path.write_text(json.dumps(self.policy, ensure_ascii=False), encoding="utf-8")
        self.data_root = self.base / "nexus-data"
        self.journal = self.base / "purge-journal.jsonl"
        self.store = open_test_store(self.data_root, policy=self.policy,
                                     independent_purge_journal_path=self.journal)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "stage3-human", "principal_type": "HUMAN", "status": "ACTIVE",
        }, "stage3-register-human")
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "project-nexus-service", "principal_type": "SERVICE", "status": "ACTIVE",
        }, "stage3-register-service")
        self.authority.register_trust_anchor({
            "schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "stage3-anchor", "principal_id": "stage3-human", "policy_ref": "1",
        }, "stage3-register-anchor")
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "stage3-control-grant", "issued_by": "stage3-human",
            "granted_to": "project-nexus-service", "task_scope": ["stage3-control-task"],
            "resource_scope": ["stage3-control-resource"], "action_scope": ["RUN_CREATE"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "stage3-create-control-grant")
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.verifier = VerificationService(self.store, self.authority)
        self.memory = MemoryService(self.store, self.authority, self.verifier)
        self.participation = ParticipationModeService(self.store)
        self.context = ContextPackService(
            store=self.store, authority=self.authority, participation=self.participation,
            memory=self.memory, metering=MeteringService(self.store, self.authority, self.participation),
        )
        self.repo = self.base / "frozen-source"
        self.repo.mkdir()
        _git(self.repo, "init", "--quiet")
        _git(self.repo, "config", "core.autocrlf", "false")
        self.paths = [
            "docs/history/phase6.md", "eval/utility-pilot.md", "docs/design/selfhost.md",
            "docs/user/bootstrap.md", "docs/user/git-import.md", "docs/design/foundation.md",
            "docs/user/slice3.md", "docs/design/bootstrap-contract.md",
        ]
        for index, relative in enumerate(self.paths):
            path = self.repo / Path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Sanitized fixture source record {index + 1}.\n", encoding="utf-8")
        _git(self.repo, "add", "--all")
        _git(self.repo, "-c", "user.name=Fixture Operator", "-c", "user.email=fixture@example.invalid",
             "commit", "--quiet", "-m", "frozen fixture")
        self.commit = _git(self.repo, "rev-parse", "HEAD")
        self.object_format = _git(self.repo, "rev-parse", "--show-object-format")
        self.manifest = self._manifest()

    def _classification(self, assertion_id, subject_type, subject_ref):
        return {
            "schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": assertion_id, "subject_type": subject_type,
            "subject_ref": subject_ref, "sensitivity_level": "PUBLIC", "handling_tags": [],
            "policy_version": "1", "reason": "Frozen Project Nexus bootstrap fixture.",
            "actor_id": "project-nexus-service",
        }

    def _manifest(self):
        binding = self.store.get_instance_binding_status()
        stage_id = "stage3-project-nexus-v1"
        root_command = "root-project-nexus-v1"
        task_id = "task-project-nexus-bootstrap-v1"
        run_id = "run-project-nexus-root-v1"
        grant_id = "grant-project-nexus-work-v1"
        state_id = "artifact-project-nexus-current-state-v1"
        sources = []
        artifact_ids = list(self.SOURCE_IDS)
        evidence_ids = ["evidence-" + item for item in self.SOURCE_IDS]
        for index, (source_id, path) in enumerate(zip(artifact_ids, self.paths)):
            commit_oid = self.commit
            blob_oid = _git(self.repo, "rev-parse", f"{commit_oid}:{path}")
            prefix = f"stage3-git-source-{index + 1:02d}"
            sources.append({
                "expected_git_object_format": self.object_format,
                "expected_blob_oid": blob_oid,
                "import_plan": {
                    "repository_id": "project-nexus-repo", "commit_oid": commit_oid, "path": path,
                    "task_id": task_id, "run_id": run_id, "grant_id": grant_id,
                    "artifact_object_id": source_id,
                    "artifact_classification_assertion_id": "class-" + source_id,
                    "evidence_object_id": evidence_ids[index],
                    "evidence_classification_assertion_id": "class-" + evidence_ids[index],
                    "sensitivity_level": "PUBLIC", "handling_tags": [], "command_id_prefix": prefix,
                },
            })
        now = datetime.now(timezone.utc)
        issued = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = (now + timedelta(days=2)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        boundary = {"allowed_classifications": ["PUBLIC"], "handling_tags": []}
        classes = {
            "root_run": self._classification("class-root-run", "RUN", run_id),
            "root_created_event": self._classification("class-root-created-event", "TRACE_EVENT", "evt-" + root_command + "-root-create"),
            "input_object": self._classification("class-root-input", "OBJECT", "object-project-nexus-input-v1"),
            "input_event": self._classification("class-root-input-event", "TRACE_EVENT", "evt-" + root_command + "-trace-input"),
            "task_contract": self._classification("class-root-contract", "OBJECT", "object-project-nexus-contract-v1"),
            "root_manifest": self._classification("class-root-manifest", "OBJECT", "object-project-nexus-manifest-v1"),
            "root_ready_event": self._classification("class-root-ready-event", "TRACE_EVENT", "evt-" + root_command + "-root-ready"),
            "root_running_event": self._classification("class-root-running-event", "TRACE_EVENT", "evt-" + root_command + "-root-running"),
        }
        root = {
            "created_at": issued, "create_task_root_command_id": root_command,
            "task_id": task_id, "requester_id": "stage3-human", "grant_id": grant_id,
            "root_run_id": run_id, "budget_account_id": "budget-project-nexus-v1",
            "budget_limits": {"amount_limit": 0, "unit": "test-units", "model_call_limit": 0,
                              "tool_call_limit": 0, "child_run_limit": 0},
            "input_object_id": "object-project-nexus-input-v1",
            "input_payload": "Create the frozen Project Nexus governance bootstrap task.",
            "task_contract": {
                "schema_id": "nexus.task_contract", "schema_version": 1,
                "goal": "Create initial governed Nexus project state from the supplied frozen source documents.",
                "constraints": ["Do not claim model-visible delivery."],
                "success_criteria": ["Persist the frozen source chain and quarantined claims."],
                "risk_class": "STANDARD",
                "routing_constraints": {"allowed_providers": ["codex-host"], "forbidden_providers": [],
                                        "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]},
                "routing_preferences": {"optimize_for": "QUALITY"}, "created_at": issued,
            },
            "contract_object_id": "object-project-nexus-contract-v1", "dag_nodes": [],
            "root_manifest_object_id": "object-project-nexus-manifest-v1",
            "data_boundary": boundary, "classifications": classes,
        }
        claims = []
        claim_ids = [
            "claim-phase6-closed-v1", "claim-utility-state-insufficient-v1",
            "claim-product-scope-v1", "claim-pre-nexus-provenance-v1", "claim-next-priority-v1",
        ]
        for index, claim_id in enumerate(claim_ids):
            evidence_refs = [evidence_ids[index]]
            claims.append({
                "claim_id": claim_id,
                "payload": {"claim_id": claim_id, "statement": f"Sanitized fixture claim {index + 1}.",
                            "truth_state": "INFERRED", "source_refs": [artifact_ids[index]]},
                "evidence_refs": evidence_refs,
                "classification_assertion": self._classification("class-" + claim_id, "OBJECT", claim_id),
                "classify_command_id": "classify-" + claim_id,
                "put_command_id": "put-" + claim_id,
            })
        verifications = []
        for index, source_id in enumerate(artifact_ids):
            verifications.append({"verification_id": f"t1-source-{index + 1:02d}", "target_ref": source_id,
                                  "evidence_refs": [evidence_ids[index]]})
        verifications.append({"verification_id": "t1-current-state", "target_ref": state_id,
                              "evidence_refs": sorted(evidence_ids)})
        for index, claim in enumerate(claims):
            verifications.append({"verification_id": f"t1-claim-{index + 1:02d}", "target_ref": claim["claim_id"],
                                  "evidence_refs": list(claim["evidence_refs"])})
        memory_candidates = []
        for index, claim in enumerate(claims):
            verification = verifications[8 + 1 + index]
            memory_candidates.append({
                "candidate_id": f"memory-candidate-project-nexus-{index + 1:02d}",
                "claim_ref": claim["claim_id"], "evidence_refs": list(claim["evidence_refs"]),
                "verification_ref": verification["verification_id"], "owner": "project-nexus",
                "review_trigger": "Independent source-backed review is required before admission.",
                "classification_assertion_ref": claim["classification_assertion"]["assertion_id"],
                "command_id": f"memory-create-project-nexus-{index + 1:02d}",
            })
        required_context = [state_id, "src-phase6-closure-v1", "src-utility-pilot-freeze-v1",
                            "src-selfhost-design-v1", "src-fresh-bootstrap-guide-v1", "src-git-source-import-guide-v1"]
        manifest = {
            "protocol_version": "project-nexus-selfhost-bootstrap-v2", "stage3_command_id": stage_id,
            "accepted_execution_sha": self.commit,
            "instance_expectation": {"instance_id": binding["instance_id"], "policy_version": binding["policy_version"],
                                     "policy_sha256": binding["policy_sha256"], "journal_identity": binding["journal_identity"]},
            "control_grant": {"grant_id": "stage3-control-grant", "revoke_command_id": "stage3-revoke-control"},
            "work_grant": {
                "grant": {"schema_id": "nexus.delegation_grant", "schema_version": 1,
                          "grant_id": grant_id, "issued_by": "stage3-human", "granted_to": "project-nexus-service",
                          "task_scope": [task_id], "resource_scope": [],
                          "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE",
                                           "CLASSIFY", "VERIFY", "MEMORY_ADMIT", "INSPECT"],
                          "audience_scope": ["nexus-runtime", "nexus-inspect"],
                          "issued_at": issued, "expires_at": expires, "status": "ACTIVE", "policy_version": "1"},
                "create_command_id": "stage3-create-work-grant", "revoke_command_id": "stage3-revoke-work-grant",
            },
            "root": root,
            "git_sources": sources,
            "current_state": {
                "object_id": state_id,
                "payload": {"project": "Nexus", "status": "self-host bootstrap fixture", "source_count": 8},
                "source_refs": sorted(artifact_ids),
                "classification_assertion": self._classification("class-project-current-state", "OBJECT", state_id),
                "classify_command_id": "classify-project-current-state", "put_command_id": "put-project-current-state",
            },
            "claims": claims,
            "verifications": verifications,
            "memory_candidates": memory_candidates,
            "context_pack": {
                "pack_object_id": "context-pack-project-nexus-v1",
                "classification_assertion": self._classification("class-project-context-pack", "OBJECT", "context-pack-project-nexus-v1"),
                "source_refs": required_context, "memory_query": None,
                "classification_command_id": "classify-project-context-pack", "command_id": "compile-project-context-pack",
            },
            "root_close": {
                "verifying": {"command_id": "root-project-nexus-verifying",
                              "classification_command_id": "classify-root-project-nexus-verifying",
                              "classification_assertion": self._classification("class-root-verifying-event", "TRACE_EVENT", "evt-root-project-nexus-verifying")},
                "succeeded": {"command_id": "root-project-nexus-succeeded",
                              "classification_command_id": "classify-root-project-nexus-succeeded",
                              "classification_assertion": self._classification("class-root-succeeded-event", "TRACE_EVENT", "evt-root-project-nexus-succeeded")},
            },
        }
        manifest["work_grant"]["grant"]["resource_scope"] = sorted(_required_resources(manifest))
        return manifest

    def _run(self, manifest=None):
        return run_project_nexus_selfhost_bootstrap(
            store=self.store, authority=self.authority, budget=self.budget, trace=self.trace,
            runtime=self.runtime, verifier=self.verifier, memory=self.memory, context_packs=self.context,
            repo_path=self.repo, manifest=manifest or self.manifest,
        )

    def _assert_pre_mutation_rejection(self, manifest, expected_reason):
        with self.store._connection() as conn:
            before = {
                "objects": conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                "runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                "grants": conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0],
            }
        with self.assertRaises(ProjectNexusSelfHostError) as caught:
            self._run(manifest)
        self.assertEqual(caught.exception.reason_code, expected_reason)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], before["objects"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], before["tasks"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], before["runs"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], before["grants"])
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM command_ledger WHERE command_id=?",
                (manifest["stage3_command_id"] + ":request",),
            ).fetchone())

    def test_context_sources_must_be_closed_over_this_import_set_before_mutation(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["git_sources"][3]["import_plan"]["artifact_object_id"] = "src-unimported-replacement-v1"
        manifest["current_state"]["source_refs"] = sorted(
            item["import_plan"]["artifact_object_id"] for item in manifest["git_sources"]
        )
        self._assert_pre_mutation_rejection(manifest, "STAGE3_CONTEXT_PLAN_INVALID")

    def test_current_state_requires_exact_sorted_eight_source_lineage(self):
        for refs in (sorted(self.SOURCE_IDS)[:-1], sorted(self.SOURCE_IDS)[:1]):
            with self.subTest(source_count=len(refs)):
                manifest = copy.deepcopy(self.manifest)
                manifest["current_state"]["source_refs"] = refs
                self._assert_pre_mutation_rejection(
                    manifest, "STAGE3_CURRENT_STATE_SOURCE_CLOSURE_INVALID",
                )

    def test_memory_candidates_form_a_bijection_with_claims_and_verifications(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["memory_candidates"][1]["claim_ref"] = manifest["memory_candidates"][0]["claim_ref"]
        self._assert_pre_mutation_rejection(manifest, "STAGE3_MEMORY_BINDING_INVALID")

    def test_durable_command_closure_rejects_root_top_level_collision(self):
        manifest = copy.deepcopy(self.manifest)
        root = manifest["root"]
        root["create_task_root_command_id"] = manifest["git_sources"][0]["import_plan"]["command_id_prefix"] + ":artifact"
        root_command = root["create_task_root_command_id"]
        root["classifications"]["root_created_event"]["subject_ref"] = "evt-" + root_command + "-root-create"
        root["classifications"]["input_event"]["subject_ref"] = "evt-" + root_command + "-trace-input"
        root["classifications"]["root_ready_event"]["subject_ref"] = "evt-" + root_command + "-root-ready"
        root["classifications"]["root_running_event"]["subject_ref"] = "evt-" + root_command + "-root-running"
        self._assert_pre_mutation_rejection(manifest, "STAGE3_DUPLICATE_COMMAND_ID")

    def test_durable_command_closure_rejects_context_object_and_root_nested_collisions(self):
        variants = []
        context_collision = copy.deepcopy(self.manifest)
        context_collision["context_pack"]["command_id"] = "shared-context-command"
        context_collision["memory_candidates"][0]["command_id"] = "shared-context-command-object"
        variants.append(context_collision)
        for suffix in ("bind-contract-object", "bind-contract-logical-ref"):
            nested_collision = copy.deepcopy(self.manifest)
            nested_collision["memory_candidates"][0]["command_id"] = (
                nested_collision["root"]["create_task_root_command_id"] + "-" + suffix
            )
            variants.append(nested_collision)
        for manifest in variants:
            with self.subTest(collision=manifest["memory_candidates"][0]["command_id"]):
                self._assert_pre_mutation_rejection(manifest, "STAGE3_DUPLICATE_COMMAND_ID")

    def test_static_task_contract_classification_dag_and_budget_preflight(self):
        invalids = []
        contract = copy.deepcopy(self.manifest)
        contract["root"]["task_contract"]["goal"] = ""
        invalids.append((contract, "STAGE3_TASK_CONTRACT_INVALID"))
        classification = copy.deepcopy(self.manifest)
        classification["root"]["classifications"]["root_run"]["handling_tags"] = "not-a-list"
        invalids.append((classification, "STAGE3_CLASSIFICATION_INVALID"))
        dag = copy.deepcopy(self.manifest)
        dag["root"]["dag_nodes"] = [{"not": "supported"}]
        invalids.append((dag, "STAGE3_DAG_UNSUPPORTED"))
        for field in ("amount_limit", "model_call_limit", "tool_call_limit", "child_run_limit"):
            budget = copy.deepcopy(self.manifest)
            budget["root"]["budget_limits"][field] = 1
            invalids.append((budget, "STAGE3_BUDGET_UNSUPPORTED"))
        for index, (manifest, reason) in enumerate(invalids):
            with self.subTest(case=index, reason=reason):
                self._assert_pre_mutation_rejection(manifest, reason)

    def test_frozen_manifest_runs_full_canonical_composition_and_completed_replay(self):
        result = self._run()
        self.assertEqual(result["status"], "STAGE3_CANONICAL_COMPLETE")
        self.assertEqual(result["source_count"], 8)
        self.assertEqual(result["memory_candidate_count"], 5)
        self.assertEqual(self.manifest["current_state"]["source_refs"], sorted(self.SOURCE_IDS))
        self.assertEqual({item["claim_ref"] for item in self.manifest["memory_candidates"]},
                         {item["claim_id"] for item in self.manifest["claims"]})
        self.assertEqual(len({item["verification_ref"] for item in self.manifest["memory_candidates"]}), 5)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id=?", (self.manifest["root"]["root_run_id"],)).fetchone()[0], "SUCCEEDED")
            self.assertEqual(conn.execute("SELECT status FROM delegation_grants WHERE grant_id=?", (self.manifest["work_grant"]["grant"]["grant_id"],)).fetchone()[0], "REVOKED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM verification_results WHERE run_id=?", (self.manifest["root"]["root_run_id"],)).fetchone()[0], 14)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_candidates WHERE status='QUARANTINED' AND truth_state='INFERRED'").fetchone()[0], 5)
        for claim in self.manifest["claims"]:
            self.assertEqual(self.store.get_object_metadata(claim["claim_id"])["object_type"], "claim")
        context = self.context.pack_status(self.manifest["context_pack"]["pack_object_id"])
        self.assertEqual(context["model_visible_exposure"], "UNKNOWN")
        with mock.patch("adapters.client.project_nexus_selfhost.inspect_local_git_head", side_effect=AssertionError("completed replay reread Git")), \
             mock.patch.object(self.authority, "validate_delegation_chain", side_effect=AssertionError("completed replay checked current authority")), \
             mock.patch.object(self.participation, "current", side_effect=AssertionError("completed replay read current mode")):
            self.assertEqual(self._run(), result)
        changed = copy.deepcopy(self.manifest)
        changed["current_state"]["payload"]["status"] = "changed"
        with self.assertRaises(Exception):
            self._run(changed)

    def test_master_commit_precedes_grant_creation_and_control_revocation(self):
        events = []
        create_grant = self.authority.create_grant
        revoke_grant = self.authority.revoke_grant

        def checked_create(grant, command_id):
            if command_id == self.manifest["work_grant"]["create_command_id"]:
                with self.store._connection() as conn:
                    row = conn.execute("SELECT operation,result_json FROM command_ledger WHERE command_id=?", (self.manifest["stage3_command_id"] + ":request",)).fetchone()
                self.assertEqual(row["operation"], "project_nexus_selfhost_request")
                self.assertEqual(json.loads(row["result_json"]), {"status": "REQUEST_BOUND"})
                events.append("work-grant")
            return create_grant(grant, command_id)

        def checked_revoke(grant_id, command_id):
            if command_id == self.manifest["control_grant"]["revoke_command_id"]:
                with self.store._connection() as conn:
                    work = conn.execute("SELECT 1 FROM delegation_grants WHERE grant_id=?", (self.manifest["work_grant"]["grant"]["grant_id"],)).fetchone()
                self.assertIsNotNone(work)
                events.append("control-revoke")
            return revoke_grant(grant_id, command_id)

        with mock.patch.object(self.authority, "create_grant", side_effect=checked_create), \
             mock.patch.object(self.authority, "revoke_grant", side_effect=checked_revoke):
            self._run()
        self.assertEqual(events[:2], ["work-grant", "control-revoke"])

    def test_master_bound_changed_tail_conflicts_before_continuation(self):
        class ProcessLoss(BaseException):
            pass

        original = self.authority.create_grant

        def commit_then_lose(grant, command_id):
            result = original(grant, command_id)
            if command_id == self.manifest["work_grant"]["create_command_id"]:
                raise ProcessLoss()
            return result

        with mock.patch.object(self.authority, "create_grant", side_effect=commit_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()
        variants = []
        item = copy.deepcopy(self.manifest)
        item["git_sources"][3]["import_plan"]["path"] = "docs/design/bootstrap-contract.md"
        variants.append(item)
        item = copy.deepcopy(self.manifest)
        item["claims"][0]["payload"]["statement"] = "changed claim"
        variants.append(item)
        item = copy.deepcopy(self.manifest)
        # Change a valid semantic field while preserving all manifest bindings,
        # so the durable master request ledger (rather than static validation)
        # is what rejects the retargeted request.
        item["verifications"][0]["verification_id"] += "-revised"
        variants.append(item)
        item = copy.deepcopy(self.manifest)
        item["memory_candidates"][0]["review_trigger"] = "changed review trigger"
        variants.append(item)
        item = copy.deepcopy(self.manifest)
        item["context_pack"]["source_refs"].reverse()
        variants.append(item)
        with self.store._connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
            grants = conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0]
        for changed in variants:
            with self.subTest(tail=changed["git_sources"][3]["import_plan"]["path"]):
                with self.assertRaises(CommandConflict):
                    self._run(changed)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], grants)

    def test_exact_partial_import_retry_resumes_without_second_object(self):
        import adapters.client.project_nexus_selfhost as composition
        original = composition.import_git_source
        interrupted = {"done": False}

        class ProcessLoss(BaseException):
            pass

        def import_then_lose(**kwargs):
            result = original(**kwargs)
            if not interrupted["done"]:
                interrupted["done"] = True
                raise ProcessLoss()
            return result

        with mock.patch.object(composition, "import_git_source", side_effect=import_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()
        first_source = self.manifest["git_sources"][0]["import_plan"]
        with self.store._connection() as conn:
            artifact_count = conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (first_source["artifact_object_id"],)).fetchone()[0]
            evidence_count = conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (first_source["evidence_object_id"],)).fetchone()[0]
        self.assertEqual((artifact_count, evidence_count), (1, 1))
        result = self._run()
        self.assertEqual(result["status"], "STAGE3_CANONICAL_COMPLETE")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (first_source["artifact_object_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (first_source["evidence_object_id"],)).fetchone()[0], 1)

    def test_ready_boundary_terminal_resume_does_not_reenter_business_services(self):
        import adapters.client.project_nexus_selfhost as composition
        original_ready = composition._record_ready

        class ProcessLoss(BaseException):
            pass

        def record_ready_then_lose(store, command_id, request):
            original_ready(store, command_id, request)
            raise ProcessLoss()

        with mock.patch.object(composition, "_record_ready", side_effect=record_ready_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()
        with self.store._connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (self.manifest["stage3_command_id"] + ":ready-to-close",)).fetchone())
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id=?", (self.manifest["root"]["root_run_id"],)).fetchone()[0], "VERIFYING")
        with mock.patch.object(composition, "import_git_source", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.verifier, "verify_object_integrity", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.memory, "create_candidate", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.context, "compile", side_effect=AssertionError("business re-entry")):
            result = self._run()
        self.assertEqual(result["status"], "STAGE3_CANONICAL_COMPLETE")

    def test_ready_with_revoked_work_grant_fails_without_business_reentry(self):
        import adapters.client.project_nexus_selfhost as composition
        original_ready = composition._record_ready

        class ProcessLoss(BaseException):
            pass

        def record_ready_then_lose(store, command_id, request):
            original_ready(store, command_id, request)
            raise ProcessLoss()

        with mock.patch.object(composition, "_record_ready", side_effect=record_ready_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()
        self.authority.revoke_grant(self.manifest["work_grant"]["grant"]["grant_id"], "external-stage3-grant-revoke")
        with mock.patch.object(composition, "import_git_source", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.verifier, "verify_object_integrity", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.memory, "create_candidate", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.context, "compile", side_effect=AssertionError("business re-entry")):
            with self.assertRaises(ProjectNexusSelfHostError) as caught:
                self._run()
        self.assertEqual(caught.exception.reason_code, "STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE")
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (self.manifest["root_close"]["succeeded"]["command_id"],)).fetchone())

    def test_ready_with_expired_work_grant_fails_without_business_reentry(self):
        import adapters.client.project_nexus_selfhost as composition
        original_ready = composition._record_ready

        class ProcessLoss(BaseException):
            pass

        def record_ready_then_lose(store, command_id, request):
            original_ready(store, command_id, request)
            raise ProcessLoss()

        with mock.patch.object(composition, "_record_ready", side_effect=record_ready_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()

        future = datetime.now(timezone.utc) + timedelta(days=3)
        real_datetime = datetime

        class FutureClock:
            @classmethod
            def now(cls, tz=None):
                return future if tz is not None else future.replace(tzinfo=None)

            @classmethod
            def fromisoformat(cls, value):
                return real_datetime.fromisoformat(value)

            @classmethod
            def strptime(cls, value, fmt):
                return real_datetime.strptime(value, fmt)

        with mock.patch.object(composition, "datetime", FutureClock), \
             mock.patch.object(composition, "import_git_source", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.verifier, "verify_object_integrity", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.memory, "create_candidate", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.context, "compile", side_effect=AssertionError("business re-entry")):
            with self.assertRaises(ProjectNexusSelfHostError) as caught:
                self._run()
        self.assertEqual(caught.exception.reason_code, "STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE")
        with self.store._connection() as conn:
            grant = conn.execute("SELECT status FROM delegation_grants WHERE grant_id=?", (self.manifest["work_grant"]["grant"]["grant_id"],)).fetchone()
            self.assertEqual(grant["status"], "ACTIVE")
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (self.manifest["stage3_command_id"],)).fetchone())

    def test_terminal_commit_response_loss_then_revoked_grant_exact_replay(self):
        import adapters.client.project_nexus_selfhost as composition
        original_revoke = self.authority.revoke_grant
        grant_id = self.manifest["work_grant"]["grant"]["grant_id"]

        class ProcessLoss(BaseException):
            pass

        def revoke_then_lose(target, command_id):
            result = original_revoke(target, command_id)
            if target == grant_id and command_id == self.manifest["work_grant"]["revoke_command_id"]:
                raise ProcessLoss()
            return result

        with mock.patch.object(self.authority, "revoke_grant", side_effect=revoke_then_lose):
            with self.assertRaises(ProcessLoss):
                self._run()
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id=?", (self.manifest["root"]["root_run_id"],)).fetchone()[0], "SUCCEEDED")
            self.assertEqual(conn.execute("SELECT status FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()[0], "REVOKED")
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (self.manifest["stage3_command_id"],)).fetchone())
        with mock.patch.object(composition, "import_git_source", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.verifier, "verify_object_integrity", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.memory, "create_candidate", side_effect=AssertionError("business re-entry")), \
             mock.patch.object(self.context, "compile", side_effect=AssertionError("business re-entry")):
            self.assertEqual(self._run()["status"], "STAGE3_CANONICAL_COMPLETE")

    def test_grant_not_current_before_ready_does_not_create_replacement(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["work_grant"]["grant"]["issued_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        manifest["work_grant"]["grant"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # Refresh the exact resource closure because timestamps remain bound to
        # the request but do not alter its resource set.
        with self.assertRaises(ProjectNexusSelfHostError) as caught:
            self._run(manifest)
        self.assertEqual(caught.exception.reason_code, "STAGE3_WORK_GRANT_EXPIRED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id=?", (manifest["work_grant"]["grant"]["grant_id"],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id=?", (manifest["root"]["task_id"],)).fetchone()[0], 0)

    def test_operator_cli_composes_existing_bound_instance_and_sanitizes_output(self):
        plan = self.base / "stage3-plan.json"
        plan.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
        self.store.close()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = client_main([
                "--data-root", str(self.data_root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal),
                "project-nexus-selfhost", "--repo", str(self.repo), "--plan", str(plan),
            ])
        self.assertEqual(status, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "STAGE3_CANONICAL_COMPLETE")
        rendered = stdout.getvalue() + stderr.getvalue()
        for private_path in (str(self.data_root), str(self.policy_path), str(self.journal), str(self.repo), str(plan)):
            self.assertNotIn(private_path, rendered)
        failure = io.StringIO()
        failure_out = io.StringIO()
        with contextlib.redirect_stdout(failure_out), contextlib.redirect_stderr(failure):
            failure_status = client_main(["--data-root", str(self.base / "missing"), "project-nexus-selfhost",
                                          "--repo", str(self.repo), "--plan", str(plan)])
        self.assertEqual(failure_status, 2)
        safe_error = json.loads(failure.getvalue())
        self.assertEqual(safe_error["status"], "DENIED_OR_FAILED")
        self.assertIn("reason", safe_error)
        self.assertNotIn("Traceback", failure.getvalue())
        self.assertNotIn(str(self.base), failure.getvalue() + failure_out.getvalue())


if __name__ == "__main__":
    unittest.main()
