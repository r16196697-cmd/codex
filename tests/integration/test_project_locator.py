from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.client.__main__ import _parser, main
from adapters.client.project_locator import (
    ProjectLocatorError,
    attach_project,
    default_project_registry_path,
    find_project_manifest,
    locate_project,
    read_host_registry,
    read_project_manifest,
    resolve_project_registry_path,
)
from tests.support.test_store import open_test_store


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
        self.assertEqual(_files_commitment(self.data_root, self.journal.parent), before_instance)

    def test_denied_confirmation_leaves_manifest_registry_and_instance_unchanged(self):
        before_instance = _files_commitment(self.data_root, self.journal.parent)
        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_ATTACH_CONFIRMATION_DENIED"):
            self._attach(confirmation=lambda _expected, _summary: False)
        self.assertFalse((self.repo / ".nexus").exists())
        self.assertFalse(self.registry_path.exists())
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

    def test_exact_attach_replay_never_prompts_or_rewrites_files(self):
        self._attach()
        manifest = self.repo / ".nexus" / "project.json"
        before = (manifest.read_bytes(), self.registry_path.read_bytes(), manifest.stat().st_mtime_ns, self.registry_path.stat().st_mtime_ns)
        result = self._attach(confirmation=lambda *_args: self.fail("exact replay prompted"))
        after = (manifest.read_bytes(), self.registry_path.read_bytes(), manifest.stat().st_mtime_ns, self.registry_path.stat().st_mtime_ns)
        self.assertEqual(result["status"], "PROJECT_ALREADY_ATTACHED")
        self.assertEqual(after, before)

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
        result = locate_project(start_dir=nested, registry_path=self.registry_path)
        after = _files_commitment(self.data_root, self.journal.parent)
        self.assertEqual(result["status"], "PROJECT_LOCATED")
        self.assertEqual(result["project_id"], "project-test")
        self.assertEqual(result["instance_id"], self.store_instance_id())
        self.assertEqual(result["data_root"], str(self.data_root.resolve()))
        self.assertEqual(after, before)

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

    def test_locate_rejects_every_changed_registered_identity_or_path(self):
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

        with self.assertRaisesRegex(ProjectLocatorError, "PROJECT_ATTACHMENT_CONFLICT"):
            attach_project(
                project_root=self.repo, project_id="project-test", data_root=self.data_root,
                policy_path=policy_copy, independent_purge_journal_path=self.journal,
                registry_path=self.registry_path, confirmation=lambda *_args: self.fail("path conflict prompted"),
            )

    def test_explicit_data_root_parser_remains_supported_and_root_is_optional(self):
        explicit = _parser().parse_args(["--data-root", "existing", "context", "read", "--latest"])
        automatic = _parser().parse_args(["context", "read", "--latest"])
        self.assertEqual(explicit.data_root, Path("existing"))
        self.assertIsNone(automatic.data_root)


if __name__ == "__main__":
    unittest.main()
