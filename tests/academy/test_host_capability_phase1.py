import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "eval" / "discover_host_capabilities.py"
SPEC = importlib.util.spec_from_file_location("discover_host_capabilities", MODULE_PATH)
DISCOVERY = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(DISCOVERY)


def skill(frontmatter: str, body: str) -> str:
    return f"---\n{frontmatter}\n---\n\n{body}\n"


class HostCapabilityPhase1Tests(unittest.TestCase):
    def test_metadata_inventory_never_returns_skill_body_and_keeps_duplicate_classes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root_a, root_b = base / "root-a", base / "root-b"
            first = root_a / "alpha" / "SKILL.md"
            exact = root_b / "alpha-copy" / "SKILL.md"
            duplicate_name = root_b / "alpha-different" / "SKILL.md"
            first.parent.mkdir(parents=True)
            exact.parent.mkdir(parents=True)
            duplicate_name.parent.mkdir(parents=True)
            text = skill("name: duplicate-capability\ndescription: safe metadata\nversion: 1\nenabled: true\nnetwork: false\nwrite: false\nsecret: false", "PRIVATE_BODY_SENTINEL_A")
            first.write_text(text, encoding="utf-8")
            exact.write_text(text, encoding="utf-8")
            duplicate_name.write_text(skill("name: duplicate-capability\ndescription: alternate safe metadata\nversion: 1", "PRIVATE_BODY_SENTINEL_B"), encoding="utf-8")
            rows = DISCOVERY.scan_skill_roots([(root_a, "ROOT_A"), (root_b, "ROOT_B")])
            valid = [row for row in rows if row["discovery_status"] == "DISCOVERED"]
            self.assertEqual(len(valid), 3)
            self.assertEqual(len({row["display_name"] for row in valid}), 1)
            self.assertEqual(len({row["instruction_hash_if_safe"] for row in valid}), 2)
            self.assertEqual(sum(row["instruction_hash_if_safe"] == valid[0]["instruction_hash_if_safe"] for row in valid), 2)
            self.assertTrue(all(row["instruction_loaded_to_model_context"] is False for row in valid))
            self.assertTrue(all("PRIVATE_BODY_SENTINEL" not in json.dumps(row) for row in rows))
            self.assertEqual(next(row for row in valid if row["source_kind"] == "ROOT_A")["host_enabled_status"], "ENABLED_DECLARED")

    def test_malformed_invalid_unknown_version_disabled_and_risk_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            malformed = root / "malformed" / "SKILL.md"
            invalid = root / "invalid" / "SKILL.md"
            disabled = root / "disabled" / "SKILL.md"
            risky = root / "risky" / "SKILL.md"
            unknown = root / "unknown" / "SKILL.md"
            for path in (malformed, invalid, disabled, risky, unknown):
                path.parent.mkdir(parents=True)
            malformed.write_text("---\nname: broken\ndescription: missing closing delimiter\n", encoding="utf-8")
            invalid.write_bytes(b"---\nname: invalid\ndescription: invalid utf8\n---\n\xff")
            disabled.write_text(skill("name: disabled-capability\ndescription: metadata only\nenabled: false", "not exposed"), encoding="utf-8")
            risky.write_text(skill("name: risky-capability\ndescription: network write secret\nnetwork: true\nwrite: true\nsecret: true", "unsafe instruction"), encoding="utf-8")
            unknown.write_text(skill("name: future-capability\ndescription: unknown version\nversion: 99", "future body"), encoding="utf-8")
            rows = DISCOVERY.scan_skill_roots([(root, "SYNTHETIC")])
            self.assertEqual(sum(row["discovery_status"] == "MALFORMED_FRONTMATTER" for row in rows), 1)
            self.assertEqual(sum(row["discovery_status"] == "UNREADABLE_OR_INVALID" for row in rows), 1)
            by_name = {row["display_name"]: row for row in rows}
            self.assertEqual(by_name["disabled-capability"]["host_enabled_status"], "DISABLED_DECLARED")
            self.assertEqual(by_name["risky-capability"]["permissions_risk_summary"], {"network": True, "write": True, "secret": True})
            self.assertEqual(by_name["future-capability"]["version_if_observable"], "99")
            self.assertEqual(by_name["future-capability"]["version_status"], "UNKNOWN_VERSION")
            self.assertEqual(by_name["future-capability"]["nexus_lifecycle_status"], "DISCOVERED")

    def test_symlink_escape_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, outside = base / "root", base / "outside"
            (root / "safe").mkdir(parents=True)
            (root / "safe" / "SKILL.md").write_text(skill("name: safe\ndescription: safe", "body"), encoding="utf-8")
            (outside / "escaped").mkdir(parents=True)
            (outside / "escaped" / "SKILL.md").write_text(skill("name: escape\ndescription: outside", "private outside body"), encoding="utf-8")
            link = root / "escape-link"
            # Simulate the filesystem identifying this directory as a symlink.
            # This exercises the no-follow branch even on Windows hosts where
            # creating real symlinks requires privileges.
            original_is_symlink = Path.is_symlink
            with mock.patch.object(Path, "is_symlink", autospec=True,
                                   side_effect=lambda path: path.name == "escape-link" or original_is_symlink(path)):
                rows = DISCOVERY.scan_skill_roots([(root, "SYNTHETIC")])
            self.assertEqual([row["display_name"] for row in rows if row["discovery_status"] == "DISCOVERED"], ["safe"])

    def test_host_inventory_summary_keeps_provider_surfaces_unavailable_and_baseline_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            home, repo = base / "home", base / "repo"
            repo.mkdir()
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "AGENTS.md").write_text("Synthetic global instruction.", encoding="utf-8")
            inventory, summary = DISCOVERY.build_inventory(home=home, repo_root=repo, skill_roots=[], runtime_tools=5, mcp_tools=2, mcp_servers=1)
            self.assertEqual(inventory["host_baseline_instructions"][0]["content_stored"], False)
            self.assertEqual(summary["host_baseline_instruction"]["always_loaded_status"], "UNKNOWN_NOT_EXPOSED_BY_FILESYSTEM")
            self.assertEqual(summary["host_tool_registry"]["tool_count"], 5)
            self.assertEqual(summary["host_tool_registry"]["ui_skill_catalog_count"], "UNAVAILABLE")
            self.assertEqual(summary["plugin_catalog"]["status"], "UNAVAILABLE_BY_SUPPORTED_ENUMERATION_INTERFACE")
            self.assertEqual(summary["tokenizer_telemetry"], "UNAVAILABLE")

    def test_runtime_tool_counts_are_explicitly_caller_observations_not_harness_enumeration(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            home, repo = base / "home", base / "repo"
            repo.mkdir()
            _, summary = DISCOVERY.build_inventory(home=home, repo_root=repo, skill_roots=[],
                runtime_tools=186, mcp_tools=172, mcp_servers=3)
            registry = summary["host_tool_registry"]
            self.assertEqual(registry["observation_source"], "CALLER_SUPPLIED_RUNTIME_TOOL_COUNTS")
            self.assertEqual(registry["reproducibility"], "NOT_ENUMERATED_BY_COMMITTED_DISCOVERY_HARNESS")
            self.assertEqual((registry["tool_count"], registry["mcp_tool_count"], registry["mcp_server_count"]), (186, 172, 3))
            result = json.loads((ROOT / "eval" / "academy" / "results" / "host-capability-phase1-fixture.json").read_text(encoding="utf-8"))
            self.assertEqual(result["host_tool_registry"]["observation_source"], "CALLER_OBSERVED_ACTIVE_RUNTIME_TOOL_METADATA")
            self.assertEqual(result["host_tool_registry"]["reproducibility"], "NOT_ENUMERATED_BY_COMMITTED_DISCOVERY_HARNESS")


if __name__ == "__main__":
    unittest.main()
