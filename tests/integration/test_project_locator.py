from __future__ import annotations

import contextlib
import hashlib
import io
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.client import project_locator
from adapters.client.__main__ import _parser, main
from adapters.client.project_locator import (
    ProjectLocatorError,
    attach_project,
    default_project_registry_path,
    find_project_manifest,
    locate_project,
    read_host_registry,
    read_project_manifest,
    repair_project_policy,
    resolve_project_registry_path,
)
from kernel.instance_binding import parse_json_object, policy_sha256
from tests.support.test_store import open_test_store


def _concurrent_attach_worker(
    project_root: str, project_id: str, data_root: str, policy_path: str,
    journal_path: str, registry_path: str, confirmation_barrier, result_queue,
) -> None:
    try:
        def confirm(_expected, _summary):
            confirmation_barrier.wait(timeout=30)
            return True

        result = attach_project(
            project_root=project_root,
            project_id=project_id,
            data_root=data_root,
            policy_path=policy_path,
            independent_purge_journal_path=journal_path,
            registry_path=registry_path,
            confirmation=confirm,
        )
        result_queue.put(("ok", result["status"], project_id))
    except ProjectLocatorError as exc:
        result_queue.put(("error", exc.reason_code, project_id))
    except BaseException as exc:
        result_queue.put(("unexpected", type(exc).__name__, project_id))


def _concurrent_policy_repair_worker(
    project_root: str, registry_path: str, expected_binding: dict[str, str],
    confirmation_barrier, result_queue,
) -> None:
    try:
        def opener(data_root, *, policy_path, independent_purge_journal_path, read_only):
            if not read_only or str(data_root) != expected_binding["data_root"]:
                raise AssertionError("repair must use the expected read-only fixture binding")
            if str(independent_purge_journal_path) != expected_binding["independent_purge_journal_path"]:
                raise AssertionError("repair changed journal binding")
            actual_policy = parse_json_object(Path(policy_path).read_bytes())
            digest = policy_sha256(actual_policy)
            if digest != expected_binding["policy_sha256"]:
                raise AssertionError("repair changed policy content")
            status = {
                "state": "FRESH_BOUND_INSTANCE",
                "policy_content_binding": "BOUND",
                "instance_id": expected_binding["instance_id"],
                "policy_version": expected_binding["policy_version"],
                "policy_sha256": digest,
                "journal_identity": expected_binding["journal_identity"],
            }
            return type("FixtureApp", (), {
                "store": type("FixtureStore", (), {"get_instance_binding_status": lambda _self: status})(),
                "close": lambda _self: None,
            })()

        def confirm(_expected, _summary):
            confirmation_barrier.wait(timeout=30)
            return True

        result = repair_project_policy(
            start_dir=project_root,
            registry_path=registry_path,
            confirmation=confirm,
            opener=opener,
        )
        result_queue.put(("ok", result["status"]))
    except ProjectLocatorError as exc:
        result_queue.put(("error", exc.reason_code))
    except BaseException as exc:
        result_queue.put(("unexpected", type(exc).__name__))


def _exit_while_holding_registry_lock(registry_path: str, acquired_event) -> None:
    with project_locator._registry_mutation_lock(Path(registry_path)):
        acquired_event.set()
        os._exit(0)


def _files_commitment(*roots: Path) -> dict[str, str]:
    commitment: dict[str, str] = {}
    for index, root in enumerate(roots):
        if not root.exists():
            commitment[f"{index}:<missing>"] = "MISSING"
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            key = f"{index}:{relative}"
            if path.is_dir():
                commitment[key + "/"] = "DIR"
            elif path.is_file():
                commitment[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return commitment


class ProjectLocatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-project-locator-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.data_root = self.base / "instance"
        self.journal = self.base / "journal" / "purge.jsonl"
        self.store = open_test_store(self.data_root, independent_purge_journal_path=self.journal)
        self.store.close()
        self.policy_path = self.base / ".instance.test-policy.json"
        self.registry_path = self.base / "host" / "projects-v1.json"

    def _attach(self, *, confirmation=None, project_id="project-test"):
        return attach_project(
            project_root=self.repo,
            project_id=project_id,
            data_root=self.data_root,
            policy_path=self.policy_path,
            independent_purge_journal_path=self.journal,
            registry_path=self.registry_path,
            confirmation=confirmation or (lambda expected, _summary: expected == f"ATTACH {project_id}"),
        )

    def _attach_legacy_record(self, *, project_id="project-test"):
        binding = project_locator._verified_binding(
            data_root=self.data_root, policy_path=self.policy_path,
            journal_path=self.journal,
        )
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_bytes(project_locator._canonical_json({
            "schema_id": "nexus.host_project_registry",
            "schema_version": 1,
            "projects": {project_id: project_locator._attachment_record(binding)},
        }))
        manifest_dir = self.repo / ".nexus"
        manifest_dir.mkdir()
        (manifest_dir / "project.json").write_bytes(
            project_locator._canonical_json(project_locator._manifest_document(project_id))
        )

    def _policy_store_path(self):
        raw = self.policy_path.read_bytes()
        digest = policy_sha256(parse_json_object(raw))
        return self.registry_path.parent / "policies-v1" / f"{digest}.json"

    def test_manifest_discovery_walks_up_and_nearest_manifest_wins(self):
        (self.repo / ".nexus").mkdir()
        (self.repo / ".nexus" / "project.json").write_text(
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-parent"}',
            encoding="utf-8",
        )
        nested = self.repo / "src" / "deep"
        nested.mkdir(parents=True)
        found = find_project_manifest(nested)
        self.assertEqual(found.project_id, "project-parent")
        self.assertEqual(found.project_root, self.repo.resolve())

        (nested / ".nexus").mkdir()
        (nested / ".nexus" / "project.json").write_text(
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-near"}',
            encoding="utf-8",
        )
        self.assertEqual(find_project_manifest(nested).project_id, "project-near")

    def test_manifest_rejects_duplicate_unknown_wrong_schema_version_and_unsafe_ids(self):
        manifest = self.repo / ".nexus" / "project.json"
        manifest.parent.mkdir()
        bad_documents = (
            '{"schema_id":"nexus.project_manifest","schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-x"}',
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-x","extra":true}',
            '{"schema_id":"wrong","schema_version":1,"project_id":"project-x"}',
            '{"schema_id":"nexus.project_manifest","schema_version":true,"project_id":"project-x"}',
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"../outside"}',
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-*"}',
        )
        for document in bad_documents:
            with self.subTest(document=document):
                manifest.write_text(document, encoding="utf-8")
                with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_MANIFEST_INVALID"):
                    read_project_manifest(manifest)

    def test_no_manifest_is_not_attached(self):
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_NOT_ATTACHED"):
            find_project_manifest(self.repo)

    def test_registry_path_override_and_platform_defaults(self):
        override = self.base / "explicit-registry.json"
        self.assertEqual(
            resolve_project_registry_path(environ={"NEXUS_PROJECT_REGISTRY": str(override)}),
            override.resolve(),
        )
        windows_default = default_project_registry_path(
            platform="nt", environ={"LOCALAPPDATA": str(self.base / "local")},
        )
        self.assertEqual(windows_default, (self.base / "local" / "Nexus" / "projects-v1.json").resolve())
        xdg_default = default_project_registry_path(
            platform="posix", environ={"XDG_STATE_HOME": str(self.base / "state")},
        )
        self.assertEqual(xdg_default, self.base / "state" / "nexus" / "projects-v1.json")
        home_default = default_project_registry_path(
            platform="posix", environ={}, home=self.base / "home",
        )
        self.assertEqual(home_default, self.base / "home" / ".local" / "state" / "nexus" / "projects-v1.json")

    def test_read_registry_does_not_create_file_or_parent(self):
        absent = self.base / "absent" / "projects.json"
        self.assertEqual(read_host_registry(absent)["projects"], {})
        self.assertFalse(absent.parent.exists())

    def test_first_attach_requires_human_and_writes_path_free_manifest(self):
        before_instance = _files_commitment(self.data_root, self.journal.parent)
        seen = []

        def confirm(expected, summary):
            seen.append((expected, summary))
            return True

        result = self._attach(confirmation=confirm)
        self.assertEqual(result["status"], "PROJECT_ATTACHED")
        self.assertEqual(seen[0][0], "ATTACH project-test")
        self.assertEqual(seen[0][1]["project_id"], "project-test")
        self.assertTrue(seen[0][1]["data_root"])
        manifest_path = self.repo / ".nexus" / "project.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(set(document), {"schema_id", "schema_version", "project_id"})
        self.assertEqual(document["project_id"], "project-test")
        self.assertNotIn(str(self.data_root), manifest_path.read_text(encoding="utf-8"))
        self.assertNotIn(str(self.journal), manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(self.registry_path.is_file())
        record = read_host_registry(self.registry_path)["projects"]["project-test"]
        managed = self._policy_store_path()
        self.assertEqual(Path(record["policy_path"]), managed.resolve())
        self.assertEqual(managed.read_bytes(), self.policy_path.read_bytes())
        self.assertEqual(policy_sha256(parse_json_object(managed.read_bytes())), record["policy_sha256"])
        self.assertEqual(_files_commitment(self.data_root, self.journal.parent), before_instance)

    def test_denied_confirmation_leaves_manifest_registry_and_instance_unchanged(self):
        before_instance = _files_commitment(self.data_root, self.journal.parent)
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_ATTACH_CONFIRMATION_DENIED"):
            self._attach(confirmation=lambda _expected, _summary: False)
        self.assertFalse((self.repo / ".nexus").exists())
        self.assertFalse(self.registry_path.exists())
        self.assertFalse(self.registry_path.with_name(self.registry_path.name + ".lock").exists())
        self.assertFalse((self.registry_path.parent / "policies-v1").exists())
        self.assertEqual(_files_commitment(self.data_root, self.journal.parent), before_instance)

    def test_missing_confirmation_fails_without_writes(self):
        with self.assertRaisesRegex(ProjectLocatorError, "INTERACTIVE_TTY_REQUIRED"):
            attach_project(
                project_root=self.repo, project_id="project-test", data_root=self.data_root,
                policy_path=self.policy_path, independent_purge_journal_path=self.journal,
                registry_path=self.registry_path,
            )
        self.assertFalse((self.repo / ".nexus").exists())
        self.assertFalse(self.registry_path.exists())
        self.assertFalse((self.registry_path.parent / "policies-v1").exists())

    def test_exact_attach_replay_never_prompts_or_rewrites_files(self):
        self._attach()
        manifest = self.repo / ".nexus" / "project.json"
        lock_path = self.registry_path.with_name(self.registry_path.name + ".lock")
        managed_path = self._policy_store_path()
        before = (
            manifest.read_bytes(), self.registry_path.read_bytes(), lock_path.read_bytes(),
            managed_path.read_bytes(), managed_path.stat().st_mtime_ns,
            manifest.stat().st_mtime_ns, self.registry_path.stat().st_mtime_ns,
            lock_path.stat().st_mtime_ns,
        )
        result = self._attach(confirmation=lambda *_args: self.fail("exact replay prompted"))
        after = (
            manifest.read_bytes(), self.registry_path.read_bytes(), lock_path.read_bytes(),
            managed_path.read_bytes(), managed_path.stat().st_mtime_ns,
            manifest.stat().st_mtime_ns, self.registry_path.stat().st_mtime_ns,
            lock_path.stat().st_mtime_ns,
        )
        self.assertEqual(result["status"], "PROJECT_ALREADY_ATTACHED")
        self.assertEqual(after, before)

    def test_locate_survives_removal_of_original_temp_like_policy_source(self):
        self._attach()
        managed_path = self._policy_store_path()
        self.assertTrue(managed_path.is_file())
        self.policy_path.unlink()
        result = locate_project(start_dir=self.repo, registry_path=self.registry_path)
        self.assertEqual(result["status"], "PROJECT_LOCATED")
        self.assertEqual(Path(result["policy_path"]), managed_path.resolve())
        self.assertEqual(
            policy_sha256(parse_json_object(managed_path.read_bytes())),
            result["policy_sha256"],
        )

    def test_conflicting_managed_policy_fails_before_confirmation_or_attachment(self):
        managed_path = self._policy_store_path()
        managed_path.parent.mkdir(parents=True)
        managed_path.write_text("not-json", encoding="utf-8")
        before = _files_commitment(self.registry_path.parent, self.repo)
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_MANAGED_POLICY_INVALID"):
            self._attach(confirmation=lambda *_args: self.fail("invalid managed policy prompted"))
        self.assertEqual(_files_commitment(self.registry_path.parent, self.repo), before)

    def test_policy_source_symlink_is_rejected_without_following_it(self):
        link = self.base / "source-policy-link.json"
        try:
            link.symlink_to(self.policy_path)
        except (OSError, NotImplementedError):
            self.skipTest("Windows runner cannot create file symlinks")
        before = _files_commitment(self.registry_path.parent, self.repo)
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_POLICY_SOURCE_INVALID"):
            attach_project(
                project_root=self.repo, project_id="project-test", data_root=self.data_root,
                policy_path=link, independent_purge_journal_path=self.journal,
                registry_path=self.registry_path,
                confirmation=lambda *_args: self.fail("symlink policy source prompted"),
            )
        self.assertEqual(_files_commitment(self.registry_path.parent, self.repo), before)

    def test_managed_policy_symlink_is_rejected_without_following_it(self):
        managed_path = self._policy_store_path()
        managed_path.parent.mkdir(parents=True)
        try:
            managed_path.symlink_to(self.policy_path)
        except (OSError, NotImplementedError):
            self.skipTest("Windows runner cannot create file symlinks")
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_MANAGED_POLICY_INVALID"):
            self._attach(confirmation=lambda *_args: self.fail("managed policy symlink prompted"))

    def test_policy_repair_changes_only_policy_path_and_survives_source_removal(self):
        self._attach_legacy_record()
        manifest = self.repo / ".nexus" / "project.json"
        manifest_before = manifest.read_bytes()
        old = read_host_registry(self.registry_path)["projects"]["project-test"]
        confirmed = []
        result = repair_project_policy(
            start_dir=self.repo,
            registry_path=self.registry_path,
            confirmation=lambda phrase, summary: confirmed.append((phrase, summary)) or True,
        )
        updated = read_host_registry(self.registry_path)["projects"]["project-test"]
        managed_path = self._policy_store_path()
        self.assertEqual(result["status"], "PROJECT_POLICY_RELOCATED")
        self.assertFalse(result["replayed"])
        self.assertEqual(confirmed[0][0], "RELOCATE POLICY project-test")
        self.assertEqual(set(old) - {"policy_path"}, set(updated) - {"policy_path"})
        self.assertEqual({k: old[k] for k in old if k != "policy_path"},
                         {k: updated[k] for k in updated if k != "policy_path"})
        self.assertEqual(Path(updated["policy_path"]), managed_path.resolve())
        self.assertEqual(manifest.read_bytes(), manifest_before)
        self.assertEqual(managed_path.read_bytes(), self.policy_path.read_bytes())
        self.assertEqual(
            policy_sha256(parse_json_object(managed_path.read_bytes())), old["policy_sha256"],
        )
        self.assertEqual(locate_project(start_dir=self.repo, registry_path=self.registry_path)["status"],
                         "PROJECT_LOCATED")
        self.policy_path.unlink()
        self.assertEqual(locate_project(start_dir=self.repo, registry_path=self.registry_path)["status"],
                         "PROJECT_LOCATED")

    def test_policy_repair_exact_replay_does_not_prompt_or_rewrite(self):
        self._attach_legacy_record()
        repair_project_policy(
            start_dir=self.repo, registry_path=self.registry_path,
            confirmation=lambda *_args: True,
        )
        managed_path = self._policy_store_path()
        manifest = self.repo / ".nexus" / "project.json"
        lock_path = self.registry_path.with_name(self.registry_path.name + ".lock")
        paths = (manifest, self.registry_path, lock_path, managed_path)
        before = tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in paths)
        replay = repair_project_policy(
            start_dir=self.repo, registry_path=self.registry_path,
            confirmation=lambda *_args: self.fail("exact policy repair replay prompted"),
        )
        after = tuple((p.read_bytes(), p.stat().st_mtime_ns) for p in paths)
        self.assertEqual(replay["status"], "PROJECT_POLICY_ALREADY_DURABLE")
        self.assertTrue(replay["replayed"])
        self.assertEqual(after, before)

    def test_denied_policy_repair_has_zero_host_mutation(self):
        self._attach_legacy_record()
        before = _files_commitment(self.repo, self.registry_path.parent)
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_POLICY_RELOCATION_DENIED"):
            repair_project_policy(
                start_dir=self.repo, registry_path=self.registry_path,
                confirmation=lambda *_args: False,
            )
        self.assertEqual(_files_commitment(self.repo, self.registry_path.parent), before)

    def test_policy_repair_refuses_every_immutable_binding_change(self):
        self._attach_legacy_record()
        other_root = self.base / "other-instance"
        other_journal = self.base / "other-journal" / "purge.jsonl"
        other_store = open_test_store(other_root, independent_purge_journal_path=other_journal)
        other_store.close()
        baseline = json.loads(self.registry_path.read_text(encoding="utf-8"))
        changes = {
            "instance_id": "different-instance",
            "data_root": str(other_root.resolve()),
            "policy_sha256": "f" * 64,
            "journal_identity": "e" * 64,
            "independent_purge_journal_path": str(other_journal.resolve()),
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                candidate = json.loads(json.dumps(baseline))
                candidate["projects"]["project-test"][key] = value
                self.registry_path.write_bytes(project_locator._canonical_json(candidate))
                before = _files_commitment(self.registry_path.parent, self.repo)
                with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_INSTANCE_BINDING_MISMATCH"):
                    repair_project_policy(
                        start_dir=self.repo, registry_path=self.registry_path,
                        confirmation=lambda *_args: self.fail(f"{key} mismatch prompted"),
                    )
                self.assertEqual(_files_commitment(self.registry_path.parent, self.repo), before)
                self.registry_path.write_bytes(project_locator._canonical_json(baseline))

    def test_policy_repair_rereads_registry_under_lock_and_preserves_other_attachments(self):
        self._attach_legacy_record()
        original = read_host_registry(self.registry_path)["projects"]["project-test"]

        def confirm(expected, _summary):
            self.assertEqual(expected, "RELOCATE POLICY project-test")
            registry = read_host_registry(self.registry_path)
            registry["projects"]["project-concurrent"] = original
            self.registry_path.write_bytes(project_locator._canonical_json(registry))
            return True

        repair_project_policy(start_dir=self.repo, registry_path=self.registry_path, confirmation=confirm)
        registry = read_host_registry(self.registry_path)
        self.assertEqual(set(registry["projects"]), {"project-test", "project-concurrent"})
        self.assertEqual(
            registry["projects"]["project-test"]["policy_path"],
            str(self._policy_store_path().resolve()),
        )

    def test_concurrent_policy_repairs_serialize_and_exactly_one_relocates(self):
        self._attach_legacy_record()
        expected_binding = read_host_registry(self.registry_path)["projects"]["project-test"]
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_concurrent_policy_repair_worker,
                args=(str(self.repo), str(self.registry_path), expected_binding, barrier, result_queue),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(45)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
                self.fail("concurrent policy repair did not finish")
            self.assertEqual(process.exitcode, 0)
        results = [result_queue.get(timeout=5) for _ in processes]
        result_queue.close()
        result_queue.join_thread()
        self.assertEqual(sum(r == ("ok", "PROJECT_POLICY_RELOCATED") for r in results), 1, results)
        self.assertEqual(sum(r == ("ok", "PROJECT_POLICY_ALREADY_DURABLE") for r in results), 1, results)
        record = read_host_registry(self.registry_path)["projects"]["project-test"]
        self.assertEqual(Path(record["policy_path"]), self._policy_store_path().resolve())

    def test_existing_manifest_for_another_project_fails_closed(self):
        self._attach()
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_ALREADY_ATTACHED_DIFFERENT_PROJECT"):
            self._attach(project_id="project-other")

    def test_existing_project_registry_binding_conflict_fails_closed(self):
        self._attach()
        other_root = self.base / "other-instance"
        other_journal = self.base / "other-journal" / "purge.jsonl"
        other_store = open_test_store(other_root, independent_purge_journal_path=other_journal)
        other_store.close()
        other_policy = self.base / ".other-instance.test-policy.json"
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_ATTACHMENT_CONFLICT"):
            attach_project(
                project_root=self.repo, project_id="project-test", data_root=other_root,
                policy_path=other_policy, independent_purge_journal_path=other_journal,
                registry_path=self.registry_path, confirmation=lambda *_args: self.fail("conflict prompted"),
            )

    def test_registry_invalid_json_and_schema_are_rejected(self):
        self.registry_path.parent.mkdir(parents=True)
        for raw in (
            b'{"schema_id":"nexus.host_project_registry","schema_id":"nexus.host_project_registry","schema_version":1,"projects":{}}',
            b'{"schema_id":"wrong","schema_version":1,"projects":{}}',
            b'{"schema_id":"nexus.host_project_registry","schema_version":1,"projects":{},"extra":1}',
        ):
            self.registry_path.write_bytes(raw)
            with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_REGISTRY_INVALID"):
                read_host_registry(self.registry_path)

    def test_locate_nested_directory_revalidates_instance_read_only_without_mutation(self):
        self._attach()
        nested = self.repo / "pkg" / "module"
        nested.mkdir(parents=True)
        before = _files_commitment(self.data_root, self.journal.parent)
        with mock.patch.object(
            project_locator, "_registry_mutation_lock",
            side_effect=AssertionError("locate must not acquire mutation lock"),
        ):
            result = locate_project(start_dir=nested, registry_path=self.registry_path)
        after = _files_commitment(self.data_root, self.journal.parent)
        self.assertEqual(result["status"], "PROJECT_LOCATED")
        self.assertEqual(result["project_id"], "project-test")
        self.assertEqual(result["instance_id"], self.store_instance_id())
        self.assertEqual(result["data_root"], str(self.data_root.resolve()))
        self.assertEqual(after, before)

    def _make_concurrent_fixture(self, label: str, *, project_id: str | None = None):
        repo = self.base / f"repo-{label}"
        repo.mkdir()
        data_root = self.base / f"instance-{label}"
        journal = self.base / f"journal-{label}" / "purge.jsonl"
        store = open_test_store(data_root, independent_purge_journal_path=journal)
        store.close()
        policy = data_root.parent / f".{data_root.name}.test-policy.json"
        return repo, data_root, policy, journal

    def _run_two_process_attaches(self, first, second):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_concurrent_attach_worker,
                args=(
                    fixture[0], fixture[4], fixture[1], fixture[2], fixture[3],
                    self.registry_path, barrier, result_queue,
                ),
            )
            for fixture in (first, second)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(45)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
                self.fail("concurrent attach process did not finish")
            self.assertEqual(process.exitcode, 0)
        results = [result_queue.get(timeout=5) for _ in processes]
        result_queue.close()
        result_queue.join_thread()
        return results

    def test_concurrent_different_projects_preserve_both_registry_entries(self):
        first = self._make_concurrent_fixture("parallel-a")
        second = self._make_concurrent_fixture("parallel-b")
        results = self._run_two_process_attaches(
            (*first, "project-parallel-a"), (*second, "project-parallel-b"),
        )
        self.assertEqual({result[0] for result in results}, {"ok"})
        self.assertEqual({result[1] for result in results}, {"PROJECT_ATTACHED"})
        registry = read_host_registry(self.registry_path)
        self.assertEqual(
            set(registry["projects"]), {"project-parallel-a", "project-parallel-b"},
        )

    def test_concurrent_same_project_different_bindings_has_one_winner(self):
        first = self._make_concurrent_fixture("same-a")
        second = self._make_concurrent_fixture("same-b")
        results = self._run_two_process_attaches(
            (*first, "project-shared"), (*second, "project-shared"),
        )
        self.assertEqual(sum(result[0] == "ok" for result in results), 1, results)
        self.assertEqual(sum(result == ("error", "PROJECT_ATTACHMENT_CONFLICT", "project-shared") for result in results), 1, results)
        registry = read_host_registry(self.registry_path)
        self.assertEqual(set(registry["projects"]), {"project-shared"})
        winner_root = Path(registry["projects"]["project-shared"]["data_root"])
        attached_roots = [fixture[1].resolve() for fixture in (first, second)]
        self.assertIn(winner_root, attached_roots)
        manifest_count = sum((fixture[0] / ".nexus" / "project.json").is_file() for fixture in (first, second))
        self.assertEqual(manifest_count, 1)

    def test_registry_updated_after_preflight_is_reread_under_lock(self):
        concurrent_record = project_locator._attachment_record(project_locator._verified_binding(
            data_root=self.data_root, policy_path=self.policy_path,
            journal_path=self.journal,
        ))

        def confirm(expected, _summary):
            self.assertEqual(expected, "ATTACH project-test")
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            self.registry_path.write_bytes(project_locator._canonical_json({
                "schema_id": "nexus.host_project_registry",
                "schema_version": 1,
                "projects": {"project-concurrent": concurrent_record},
            }))
            return True

        result = self._attach(confirmation=confirm)
        self.assertEqual(result["status"], "PROJECT_ATTACHED")
        registry = read_host_registry(self.registry_path)
        self.assertEqual(set(registry["projects"]), {"project-concurrent", "project-test"})

    def test_registry_kernel_lock_is_released_after_process_termination(self):
        context = multiprocessing.get_context("spawn")
        acquired = context.Event()
        process = context.Process(
            target=_exit_while_holding_registry_lock,
            args=(str(self.registry_path), acquired),
        )
        process.start()
        self.assertTrue(acquired.wait(15), "child never acquired registry lock")
        process.join(15)
        self.assertEqual(process.exitcode, 0)
        # The persistent sidecar is not a stale lease: the OS released its
        # advisory lock when the process exited without running finally.
        with project_locator._registry_mutation_lock(self.registry_path):
            self.assertTrue(self.registry_path.with_name(self.registry_path.name + ".lock").is_file())

    def test_same_project_manifest_in_another_clone_uses_project_id_only(self):
        self._attach()
        clone = self.base / "second-clone"
        (clone / ".nexus").mkdir(parents=True)
        (clone / ".nexus" / "project.json").write_bytes(
            (self.repo / ".nexus" / "project.json").read_bytes()
        )
        result = locate_project(start_dir=clone, registry_path=self.registry_path)
        self.assertEqual(result["project_id"], "project-test")
        self.assertEqual(result["project_root"], str(clone.resolve()))
        self.assertEqual(result["data_root"], str(self.data_root.resolve()))

    def store_instance_id(self):
        from adapters.panel.application import open_panel_application

        app = open_panel_application(
            self.data_root, policy_path=self.policy_path,
            independent_purge_journal_path=self.journal, read_only=True,
        )
        try:
            return app.store.get_instance_binding_status()["instance_id"]
        finally:
            app.close()

    def test_locate_without_registry_entry_reports_host_binding_missing(self):
        (self.repo / ".nexus").mkdir()
        (self.repo / ".nexus" / "project.json").write_text(
            '{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-test"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_HOST_BINDING_MISSING"):
            locate_project(start_dir=self.repo, registry_path=self.registry_path)

    def test_locate_binding_mismatch_and_unavailable_instance_fail_closed(self):
        self._attach()
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        original_policy_sha = registry["projects"]["project-test"]["policy_sha256"]
        registry["projects"]["project-test"]["policy_sha256"] = "0" * 64
        self.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_INSTANCE_BINDING_MISMATCH"):
            locate_project(start_dir=self.repo, registry_path=self.registry_path)
        self.registry_path.unlink()
        self.data_root.rename(self.base / "instance-unavailable")
        registry["projects"]["project-test"]["policy_sha256"] = original_policy_sha
        self.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_DATA_ROOT_UNAVAILABLE"):
            locate_project(start_dir=self.repo, registry_path=self.registry_path)

    def test_cli_attach_rejects_non_tty_before_writing(self):
        output = io.StringIO()
        errors = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(self.registry_path)}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main([
                "--data-root", str(self.data_root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "project", "attach",
                "--project-root", str(self.repo), "--project-id", "project-test",
            ])
        self.assertEqual(code, 2)
        self.assertIn("INTERACTIVE_TTY_REQUIRED", errors.getvalue())
        self.assertFalse((self.repo / ".nexus").exists())
        self.assertFalse(self.registry_path.exists())

    def test_cli_policy_repair_rejects_non_tty_without_writing(self):
        self._attach_legacy_record()
        output = io.StringIO()
        errors = io.StringIO()
        before = _files_commitment(self.repo, self.registry_path.parent)
        with mock.patch("sys.stdin", io.StringIO("")), \
             mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(self.registry_path)}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["project", "repair-policy", "--project-root", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertIn("INTERACTIVE_TTY_REQUIRED", errors.getvalue())
        self.assertEqual(_files_commitment(self.repo, self.registry_path.parent), before)

    def test_cli_automatic_context_resolution_uses_attached_project(self):
        self._attach()
        nested = self.repo / "nested"
        nested.mkdir()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(self.registry_path)}), \
             mock.patch("pathlib.Path.cwd", return_value=nested), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["context", "read", "--latest"])
        self.assertEqual(code, 2)
        self.assertIn("CONTEXT_PACK_NOT_AVAILABLE", stderr.getvalue())
        self.assertNotIn("PROJECT_NOT_ATTACHED", stderr.getvalue())

    def test_cli_unattached_rootless_call_fails_without_scanning_or_creation(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        before = _files_commitment(self.base)
        with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(self.registry_path)}), \
             mock.patch("pathlib.Path.cwd", return_value=self.repo), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["context", "read", "--latest"])
        self.assertEqual(code, 2)
        self.assertIn("PROJECT_NOT_ATTACHED", stderr.getvalue())
        self.assertEqual(_files_commitment(self.base), before)
        self.assertFalse(self.registry_path.parent.exists())

    def test_project_locate_cli_returns_verified_attachment(self):
        self._attach()
        nested = self.repo / "one" / "two"
        nested.mkdir(parents=True)
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(self.registry_path)}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = main(["project", "locate", "--project-root", str(nested)])
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "PROJECT_LOCATED")
        self.assertEqual(result["project_id"], "project-test")
        self.assertEqual(result["data_root"], str(self.data_root.resolve()))

    def test_locate_rejects_changed_registered_identity_and_attach_uses_managed_policy_identity(self):
        self._attach()
        other_root = self.base / "other-instance"
        other_journal = self.base / "other-journal" / "purge.jsonl"
        other_store = open_test_store(other_root, independent_purge_journal_path=other_journal)
        other_store.close()
        other_policy = self.base / ".other-instance.test-policy.json"
        policy_copy = self.base / "same-content-policy-copy.json"
        policy_copy.write_bytes(self.policy_path.read_bytes())
        baseline = json.loads(self.registry_path.read_text(encoding="utf-8"))
        entry = baseline["projects"]["project-test"]
        changes = {
            "instance_id": "different-instance",
            "policy_version": "different-policy-version",
            "policy_sha256": "f" * 64,
            "journal_identity": "e" * 64,
            "data_root": str(other_root.resolve()),
            "independent_purge_journal_path": str(other_journal.resolve()),
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                candidate = json.loads(json.dumps(baseline))
                candidate["projects"]["project-test"][key] = value
                self.registry_path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_INSTANCE_BINDING_MISMATCH"):
                    locate_project(start_dir=self.repo, registry_path=self.registry_path)
        self.registry_path.write_text(json.dumps(baseline), encoding="utf-8")

        managed_path = self._policy_store_path()
        before = (
            self.registry_path.read_bytes(), managed_path.read_bytes(),
            self.registry_path.stat().st_mtime_ns, managed_path.stat().st_mtime_ns,
        )
        result = attach_project(
            project_root=self.repo, project_id="project-test", data_root=self.data_root,
            policy_path=policy_copy, independent_purge_journal_path=self.journal,
            registry_path=self.registry_path,
            confirmation=lambda *_args: self.fail("canonical-equivalent exact attach replay prompted"),
        )
        after = (
            self.registry_path.read_bytes(), managed_path.read_bytes(),
            self.registry_path.stat().st_mtime_ns, managed_path.stat().st_mtime_ns,
        )
        self.assertEqual(result["status"], "PROJECT_ALREADY_ATTACHED")
        self.assertEqual(after, before)

    def test_explicit_data_root_parser_remains_supported_and_root_is_optional(self):
        explicit = _parser().parse_args(["--data-root", "existing", "context", "read", "--latest"])
        automatic = _parser().parse_args(["context", "read", "--latest"])
        self.assertEqual(explicit.data_root, Path("existing"))
        self.assertIsNone(automatic.data_root)


if __name__ == "__main__":
    unittest.main()
