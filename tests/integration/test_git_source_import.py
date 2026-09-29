from __future__ import annotations

import contextlib
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

from adapters.client.__main__ import main
from adapters.source.git import GitSourceImportError, import_git_source
from kernel.authority import AuthorityService
from kernel.authority.errors import AuthorizationDenied
from kernel.run import TraceRuntime
from tests.support.test_store import open_test_store


def _local_git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    options = {"stdin": subprocess.DEVNULL} if input_bytes is None else {}
    completed = subprocess.run(["git", *args], cwd=repo, input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False, **options)
    if completed.returncode:
        raise AssertionError("local Git fixture setup failed")
    return completed.stdout.strip()


class GitSourceImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-git-source-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "nexus-data"
        self.policy_path = self.base / "nexus-policy.json"
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["git-human"]
        self.policy_path.write_text(json.dumps(self.policy, ensure_ascii=False), encoding="utf-8")
        self.store = open_test_store(self.root, policy=self.policy)
        self.addCleanup(self._close_store)
        self.authority = AuthorityService(self.store, self.policy)
        self.trace = TraceRuntime(self.store, self.authority)
        self._create_task_run()
        self.repo = self.base / "source-repo"
        self.repo.mkdir()
        _local_git(self.repo, "init", "-q")
        _local_git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / "source.md").write_bytes(b"committed source A\r\n\x00")
        _local_git(self.repo, "add", "--", "source.md")
        self.commit_a = self._commit("initial")
        self.blob_a = _local_git(self.repo, "rev-parse", f"{self.commit_a}:source.md").decode("ascii")

    def _close_store(self):
        if self.store is not None:
            self.store.close()
            self.store = None

    def _create_task_run(self, *, allow_object_write=True, resource_limited=False):
        now = datetime.now(timezone.utc)
        self.authority = AuthorityService(self.store, self.policy)
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "git-human", "principal_type": "HUMAN", "status": "ACTIVE"}, "git-principal-human")
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "git-agent", "principal_type": "SERVICE", "status": "ACTIVE"}, "git-principal-agent")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "git-anchor", "principal_id": "git-human", "policy_ref": self.policy["policy_version"]}, "git-anchor")
        self.task_id, self.run_id, self.grant_id = "git-task", "git-run", "git-grant"
        resources = [self.run_id, "evt-git-run-create"]
        if not resource_limited:
            resources += ["artifact-source", "evidence-source", "artifact-source-2", "evidence-source-2",
                          "artifact-sha256", "evidence-sha256"]
        actions = ["RUN_CREATE", "CLASSIFY"]
        if allow_object_write:
            actions.append("OBJECT_WRITE")
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": self.grant_id, "issued_by": "git-human", "granted_to": "git-agent",
            "task_scope": [self.task_id], "resource_scope": resources, "action_scope": actions,
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE",
            "policy_version": self.policy["policy_version"]}, "git-grant-create")
        self.trace = TraceRuntime(self.store, self.authority)
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1,
            "task_id": self.task_id, "requester_id": "git-human", "status": "CREATED",
            "created_at": now.isoformat(), "command_id": "git-task-create"})
        run_class = self._classification("git-run-class", "RUN", self.run_id)
        event_class = self._classification("git-run-event-class", "TRACE_EVENT", "evt-git-run-create")
        for assertion in (run_class, event_class):
            self.authority.record_classification_assertion(assertion, grant_id=self.grant_id,
                task_id=self.task_id, audience="nexus-runtime", command_id="record-" + assertion["assertion_id"])
        self.trace.create_run({"schema_id": "nexus.run", "schema_version": 1,
            "run_id": self.run_id, "task_id": self.task_id, "executor_kind": "ORCHESTRATOR",
            "status": "CREATED", "grant_id": self.grant_id,
            "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
            "classification_assertion_ref": run_class["assertion_id"], "created_at": now.isoformat()},
            command_id="git-run-create", event_classification_assertion_ref=event_class["assertion_id"])

    def _classification(self, assertion_id, subject_type, subject_ref):
        return {"schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": assertion_id, "subject_type": subject_type, "subject_ref": subject_ref,
            "sensitivity_level": "PUBLIC", "handling_tags": [],
            "policy_version": self.policy["policy_version"], "reason": "Git source importer test.",
            "actor_id": "git-agent"}

    def _commit(self, message: str) -> str:
        _local_git(self.repo, "add", "--all")
        return self._commit_index(message)

    def _commit_index(self, message: str) -> str:
        _local_git(self.repo, "-c", "user.name=Fixture Operator", "-c", "user.email=fixture@example.invalid",
                   "commit", "--quiet", "-m", message)
        return _local_git(self.repo, "rev-parse", "HEAD").decode("ascii")

    def _plan(self, **changes):
        plan = {
            "repository_id": "project-nexus-repo", "commit_oid": self.commit_a,
            "path": "source.md", "task_id": self.task_id, "run_id": self.run_id,
            "grant_id": self.grant_id, "artifact_object_id": "artifact-source",
            "artifact_classification_assertion_id": "class-artifact-source",
            "evidence_object_id": "evidence-source",
            "evidence_classification_assertion_id": "class-evidence-source",
            "sensitivity_level": "PUBLIC", "handling_tags": [], "command_id_prefix": "git-import-1",
        }
        plan.update(changes)
        return plan

    def _import(self, *, plan=None, repo=None):
        return import_git_source(store=self.store, authority=self.authority, trace=self.trace,
            repository_path=repo or self.repo, plan=plan or self._plan())

    def test_imports_exact_commit_blob_and_persisted_hash_with_lineage(self):
        result = self._import()
        self.assertEqual(result["blob_oid"], self.blob_a)
        self.assertEqual(result["byte_size"], len(b"committed source A\r\n\x00"))
        self.assertEqual(self.store.get_payload("artifact-source"), b"committed source A\r\n\x00")
        artifact = self.store.get_object_metadata("artifact-source")
        self.assertEqual(result["artifact_sha256"], artifact["integrity_hash"])
        self.assertEqual(artifact["integrity_hash"], hashlib.sha256(b"committed source A\r\n\x00").hexdigest())
        self.assertEqual(artifact["created_by_run"], self.run_id)
        evidence_bytes = self.store.get_payload("evidence-source")
        evidence = json.loads(evidence_bytes)
        self.store._validate("nexus.git_source_evidence@1.schema.json", evidence)
        self.assertEqual(evidence["git_object_format"], _local_git(self.repo, "rev-parse", "--show-object-format").decode("ascii"))
        self.assertEqual(evidence["commit_oid"], self.commit_a)
        self.assertEqual(evidence["blob_oid"], self.blob_a)
        self.assertEqual(evidence["artifact_sha256"], artifact["integrity_hash"])
        self.assertEqual(evidence["provenance"], "IMPORTED_FROM_PRE_NEXUS_HISTORY")
        self.assertEqual(self.store.get_object_metadata("evidence-source")["created_by_run"], self.run_id)
        with self.store._connection() as conn:
            relation = conn.execute("SELECT to_id FROM object_relations WHERE from_id=? AND relation_type='derived_from'", ("evidence-source",)).fetchone()
        self.assertEqual(relation["to_id"], "artifact-source")

    def test_dirty_working_tree_does_not_change_exact_commit_import(self):
        (self.repo / "source.md").write_bytes(b"working tree version B\n")
        self.assertTrue(_local_git(self.repo, "status", "--porcelain"))
        result = self._import()
        self.assertEqual(result["commit_oid"], self.commit_a)
        self.assertEqual(result["blob_oid"], self.blob_a)
        self.assertEqual(self.store.get_payload("artifact-source"), b"committed source A\r\n\x00")

    def test_exact_replay_is_stable_and_does_not_add_objects(self):
        first = self._import()
        with self.store._connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        second = self._import()
        with self.store._connection() as conn:
            after = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        self.assertEqual(first, second)
        self.assertEqual(before, after)

    def test_response_loss_after_artifact_and_evidence_commits_replays_exactly(self):
        original = self.store.put_object
        (self.repo / "source.md").write_bytes(b"different source for evidence replay\n")
        commit_b = self._commit("second source")
        for lost_type in ("artifact", "evidence"):
            with self.subTest(lost_type=lost_type):
                if lost_type == "evidence":
                    # Use a fresh pair of durable IDs for the second interruption point.
                    plan = self._plan(artifact_object_id="artifact-source-2",
                        artifact_classification_assertion_id="class-artifact-source-2",
                        evidence_object_id="evidence-source-2",
                        evidence_classification_assertion_id="class-evidence-source-2",
                        command_id_prefix="git-import-2", commit_oid=commit_b)
                    expected_blob = b"different source for evidence replay\n"
                else:
                    plan = self._plan(command_id_prefix="git-import-loss-artifact")
                    expected_blob = b"committed source A\r\n\x00"
                lost = {"done": False}

                def put_then_lose(**kwargs):
                    result = original(**kwargs)
                    if kwargs["object_type"] == lost_type and not lost["done"]:
                        lost["done"] = True
                        raise RuntimeError("simulated response loss")
                    return result

                with mock.patch.object(self.store, "put_object", side_effect=put_then_lose):
                    with self.assertRaisesRegex(RuntimeError, "response loss"):
                        self._import(plan=plan)
                replay = self._import(plan=plan)
                self.assertEqual(replay["status"], "IMPORTED")
                self.assertEqual(self.store.get_payload(plan["artifact_object_id"]), expected_blob)
                self.assertEqual(self.store.get_object_metadata(plan["evidence_object_id"])["payload_state"], "AVAILABLE")

    def test_same_command_identity_with_changed_source_conflicts(self):
        self._import()
        (self.repo / "source.md").write_bytes(b"new committed source B\n")
        commit_b = self._commit("source changed")
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(plan=self._plan(commit_oid=commit_b))
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_IMPORT_CONFLICT")

    def test_same_command_identity_with_changed_repository_source_identity_conflicts(self):
        self._import()
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(plan=self._plan(repository_id="another-logical-repo"))
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_IMPORT_CONFLICT")

    def test_branch_abbreviation_and_missing_commit_are_rejected(self):
        for commit, expected in (("HEAD", "GIT_SOURCE_COMMIT_INVALID"),
                                 (self.commit_a[:12], "GIT_SOURCE_COMMIT_INVALID"),
                                 ("f" * len(self.commit_a), "GIT_SOURCE_COMMIT_INVALID")):
            with self.subTest(commit=commit):
                with self.assertRaises(GitSourceImportError) as caught:
                    self._import(plan=self._plan(commit_oid=commit))
                self.assertEqual(caught.exception.reason_code, expected)

    def test_unsafe_paths_directories_symlinks_and_gitlinks_fail_closed(self):
        for path in ("/source.md", "../source.md", "nested/../source.md", "C:/source.md", "C:source.md", ""):
            with self.subTest(path=path):
                with self.assertRaises(GitSourceImportError) as caught:
                    self._import(plan=self._plan(path=path))
                self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_PATH_INVALID")
        (self.repo / "folder").mkdir()
        (self.repo / "folder" / "file.txt").write_bytes(b"nested")
        commit = self._commit("directory entry")
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(plan=self._plan(commit_oid=commit, path="folder"))
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_ENTRY_UNSUPPORTED")

        target_oid = _local_git(self.repo, "hash-object", "-w", "--stdin", input_bytes=b"target").decode("ascii")
        _local_git(self.repo, "update-index", "--add", "--cacheinfo", f"120000,{target_oid},link.txt")
        symlink_commit = self._commit_index("symlink entry")
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(plan=self._plan(commit_oid=symlink_commit, path="link.txt"))
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_ENTRY_UNSUPPORTED")

        gitlink = f"160000,{commit},submodule"
        _local_git(self.repo, "update-index", "--add", "--cacheinfo", gitlink)
        gitlink_commit = self._commit_index("gitlink entry")
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(plan=self._plan(commit_oid=gitlink_commit, path="submodule"))
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_ENTRY_UNSUPPORTED")

    def test_missing_local_blob_fails_without_lazy_fetch(self):
        blob_path = self.repo / ".git" / "objects" / self.blob_a[:2] / self.blob_a[2:]
        self.assertTrue(blob_path.is_file())
        blob_path.chmod(0o600)
        blob_path.unlink()
        original = subprocess.run
        seen_env = []

        def observe(*args, **kwargs):
            seen_env.append(kwargs.get("env", {}).copy())
            return original(*args, **kwargs)

        with mock.patch("adapters.source.git.subprocess.run", side_effect=observe):
            with self.assertRaises(GitSourceImportError) as caught:
                self._import()
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_OBJECT_UNAVAILABLE")
        self.assertTrue(seen_env)
        self.assertTrue(all(item.get("GIT_NO_LAZY_FETCH") == "1" for item in seen_env))

    def test_repo_alias_and_non_repository_are_rejected(self):
        alias = self.base / "repo-alias"
        alias.mkdir()
        original_resolve = Path.resolve

        def resolve_with_alias(path, strict=False):
            if os.path.normcase(str(path)) == os.path.normcase(str(alias)):
                return self.repo
            return original_resolve(path, strict=strict)

        with mock.patch("adapters.source.git.Path.resolve", side_effect=resolve_with_alias):
            with self.assertRaises(GitSourceImportError) as caught:
                self._import(repo=alias)
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_REPOSITORY_INVALID")
        with self.assertRaises(GitSourceImportError) as caught:
            self._import(repo=self.base)
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_REPOSITORY_INVALID")

    def test_authority_run_binding_and_data_boundary_are_enforced_without_task_creation(self):
        for changes in ({"task_id": "other-task"}, {"grant_id": "broader-other-grant"},
                        {"sensitivity_level": "PERSONAL"}, {"handling_tags": ["NO_EXTERNAL_EGRESS"]}):
            with self.subTest(changes=changes):
                with self.assertRaises(GitSourceImportError) as caught:
                    self._import(plan=self._plan(**changes))
                self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_AUTHORITY_DENIED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='other-task'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id IN ('artifact-source','evidence-source')").fetchone()[0], 0)

    def test_object_write_and_classification_denials_fail_closed(self):
        # The exact Run grant lacks OBJECT_WRITE, so no classification or object is created.
        self._close_store()
        self.root = self.base / "nexus-data-no-write"
        self.store = open_test_store(self.root, policy=self.policy)
        self._create_task_run(allow_object_write=False)
        with self.assertRaises(GitSourceImportError) as caught:
            self._import()
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_AUTHORITY_DENIED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id='artifact-source'").fetchone()[0], 0)

        # Exercise the adapter boundary when Core rejects CLASSIFY after write authorization.
        self._close_store()
        self.root = self.base / "nexus-data-classify-denied"
        self.store = open_test_store(self.root, policy=self.policy)
        self._create_task_run()
        with mock.patch.object(self.authority, "record_classification_assertion", side_effect=AuthorizationDenied("SCOPE_DENIED")):
            with self.assertRaises(GitSourceImportError) as caught:
                self._import()
        self.assertEqual(caught.exception.reason_code, "GIT_SOURCE_AUTHORITY_DENIED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id='artifact-source'").fetchone()[0], 0)

    def test_task_run_counts_are_unchanged_by_importer(self):
        with self.store._connection() as conn:
            before = (conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        self._import()
        with self.store._connection() as conn:
            after = (conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        self.assertEqual(before, after)

    def test_local_git_invocation_is_allowlisted_and_does_not_persist_repo_path(self):
        original = subprocess.run
        commands = []

        def observe(args, **kwargs):
            commands.append(list(args))
            return original(args, **kwargs)

        with mock.patch("adapters.source.git.subprocess.run", side_effect=observe):
            self._import()
        self.assertTrue(commands)
        self.assertTrue(all(cmd[0] == "git" for cmd in commands))
        self.assertFalse(any(any(word in {"fetch", "pull", "clone", "checkout", "switch"} for word in cmd) for cmd in commands))
        metadata = self.store.get_object_metadata("artifact-source")
        self.assertNotIn(str(self.repo), json.dumps(metadata))
        evidence = self.store.get_payload("evidence-source")
        self.assertNotIn(str(self.repo).encode(), evidence)
        with self.store._connection() as conn:
            ledger = conn.execute("SELECT result_json,request_hash FROM command_ledger").fetchall()
        self.assertNotIn(str(self.repo), json.dumps([dict(row) for row in ledger]))

    def test_sha256_git_object_format_is_supported_when_available(self):
        repo = self.base / "sha256-repo"
        repo.mkdir()
        init = subprocess.run(["git", "init", "-q", "--object-format=sha256"], cwd=repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, shell=False)
        if init.returncode:
            self.skipTest("installed Git does not support SHA-256 repositories")
        _local_git(repo, "config", "core.autocrlf", "false")
        (repo / "sha.txt").write_bytes(b"sha256 object format\x00")
        _local_git(repo, "add", "--", "sha.txt")
        _local_git(repo, "-c", "user.name=Fixture Operator", "-c", "user.email=fixture@example.invalid",
                   "commit", "--quiet", "-m", "sha256 source")
        commit = _local_git(repo, "rev-parse", "HEAD").decode("ascii")
        plan = self._plan(commit_oid=commit, path="sha.txt", artifact_object_id="artifact-sha256",
            artifact_classification_assertion_id="class-artifact-sha256", evidence_object_id="evidence-sha256",
            evidence_classification_assertion_id="class-evidence-sha256", command_id_prefix="git-import-sha256")
        result = self._import(plan=plan, repo=repo)
        self.assertEqual(len(result["commit_oid"]), 64)
        evidence = json.loads(self.store.get_payload("evidence-sha256"))
        self.assertEqual(evidence["git_object_format"], "sha256")

    def test_cli_success_and_writer_lock_errors_are_sanitized(self):
        plan_path = self.base / "import-plan.json"
        plan_path.write_text(json.dumps(self._plan()), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["--data-root", str(self.root), "--policy", str(self.policy_path),
                              "import-git-source", "--repo", str(self.repo), "--plan", str(plan_path)])
        self.assertEqual(exit_code, 2)
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["reason"], "NEXUS_WRITER_ALREADY_RUNNING")
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())
        self.assertNotIn(str(self.repo), stderr.getvalue())

        self._close_store()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["--data-root", str(self.root), "--policy", str(self.policy_path),
                              "import-git-source", "--repo", str(self.repo), "--plan", str(plan_path)])
        self.assertEqual(exit_code, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "IMPORTED")
        self.assertNotIn(str(self.repo), stdout.getvalue())
        self.assertNotIn(str(self.root), stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
