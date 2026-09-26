"""Read-only, metadata-only local Host capability discovery for Academy Phase 1.

Skill bodies are read only to compute a local hash/size and parse frontmatter;
they are never returned, printed, or placed in the model context by this tool.
Raw local inventory output must be written outside the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


KNOWN_VERSIONS = {"1", "1.0", "1.0.0"}
SENSITIVE_VALUE = re.compile(r"(?i)\b(api[_-]?key|secret|password|token|authorization|auth[_-]?header)\b\s*[:=]\s*[^\s,;]+")
FRONTMATTER_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_skill(path: Path, root: Path, *, source_kind: str) -> tuple[dict[str, Any], str | None]:
    """Return a metadata record and transient full body; the body is never serialized."""
    record: dict[str, Any] = {
        "capability_type": "SKILL",
        "source_kind": source_kind,
        "source_root": str(root.resolve()),
        "source_path": str(path.resolve(strict=False)),
        "discovery_evidence": "SKILL.md present beneath configured discovery root",
        "host_available_status": "HOST_PRESENT",
        "host_enabled_status": "UNKNOWN",
        "version_if_observable": None,
        "metadata_summary": "",
        "instruction_bytes_if_observable": None,
        "instruction_chars_if_observable": None,
        "instruction_hash_if_safe": None,
        "permissions_risk_summary": {"network": "UNKNOWN", "write": "UNKNOWN", "secret": "UNKNOWN"},
        "discovery_status": "DISCOVERED",
        "nexus_lifecycle_status": "DISCOVERED",
        "exposure_status": "DISCOVERABLE_NOT_LOADED_BY_DISCOVERY",
        "evaluation_status": "UNEVALUATED",
        "instruction_loaded_to_model_context": False,
        "credential_presence": "UNAVAILABLE_NOT_SCANNED",
    }
    if path.is_symlink():
        record.update({"capability_id": "unreadable-symlink", "display_name": "", "discovery_status": "SYMLINK_SKIPPED"})
        return record, None
    try:
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        record.update({"capability_id": "invalid-skill-file", "display_name": "", "discovery_status": "UNREADABLE_OR_INVALID"})
        return record, None

    record["instruction_bytes_if_observable"] = len(raw_bytes)
    record["instruction_chars_if_observable"] = len(text)
    record["full_instruction_serialized_bytes"] = len(json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    record["full_instruction_serialized_chars"] = len(json.dumps(text, ensure_ascii=False, separators=(",", ":")))
    record["instruction_hash_if_safe"] = hashlib.sha256(raw_bytes).hexdigest()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        record.update({"capability_id": "malformed-frontmatter", "display_name": "", "discovery_status": "MALFORMED_FRONTMATTER"})
        return record, None
    try:
        closing = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        record.update({"capability_id": "malformed-frontmatter", "display_name": "", "discovery_status": "MALFORMED_FRONTMATTER"})
        return record, None

    fields: dict[str, Any] = {}
    index = 1
    while index < closing:
        line = lines[index]
        match = FRONTMATTER_FIELD.match(line)
        if not match:
            index += 1
            continue
        key, raw_value = match.group(1).lower(), match.group(2)
        if raw_value.strip() in {"|", ">", "|-", ">-", "|+", ">+"}:
            block: list[str] = []
            index += 1
            while index < closing and (not lines[index].strip() or lines[index][:1].isspace()):
                block.append(lines[index].strip())
                index += 1
            fields[key] = " ".join(part for part in block if part)
            continue
        fields[key] = _scalar(raw_value)
        index += 1
    name = str(fields.get("name", "")).strip()
    description = str(fields.get("description", "")).strip()
    if not name or not description:
        record.update({"capability_id": "malformed-frontmatter", "display_name": name, "discovery_status": "MALFORMED_FRONTMATTER"})
        return record, None

    safe_description = SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", description)
    relative = path.relative_to(root).as_posix()
    metadata = {
        "name": name,
        "description": safe_description,
        "version": fields.get("version"),
        "enabled": fields.get("enabled"),
        "network": fields.get("network"),
        "write": fields.get("write"),
        "secret": fields.get("secret"),
    }
    record.update({
        "capability_id": f"skill:{source_kind}:{relative}",
        "display_name": name,
        "metadata_summary": safe_description,
        "metadata_bytes": len(_canonical_bytes(metadata)),
        "version_if_observable": fields.get("version"),
        "version_status": "UNAVAILABLE" if fields.get("version") is None else ("KNOWN" if str(fields.get("version")) in KNOWN_VERSIONS else "UNKNOWN_VERSION"),
        "host_enabled_status": "DISABLED_DECLARED" if fields.get("enabled") is False else ("ENABLED_DECLARED" if fields.get("enabled") is True else "UNKNOWN"),
        "permissions_risk_summary": {key: fields.get(key, "UNKNOWN") for key in ("network", "write", "secret")},
        "discovery_status": "DISCOVERED",
        "nexus_lifecycle_status": "DISCOVERED",
    })
    return record, text


def scan_skill_roots(roots: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root, source_kind in roots:
        if not root.exists() or not root.is_dir() or root.is_symlink():
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(d for d in dirs if not (current_path / d).is_symlink())
            if "SKILL.md" in files:
                record, _body = parse_skill(current_path / "SKILL.md", root, source_kind=source_kind)
                records.append(record)
    return sorted(records, key=lambda row: (row.get("display_name", "").casefold(), row.get("capability_id", "")))


def _instruction_paths(home: Path, repo_root: Path) -> list[tuple[Path, str]]:
    rows: list[tuple[Path, str]] = []
    for path in (home / "AGENTS.md", home / "AGENTS.override.md", home / ".codex" / "AGENTS.md", home / ".codex" / "AGENTS.override.md"):
        rows.append((path, "GLOBAL"))
    chain = list(reversed([repo_root, *repo_root.parents]))
    for directory in chain:
        for filename in ("AGENTS.md", "AGENTS.override.md"):
            rows.append((directory / filename, "PROJECT_CHAIN"))
    return rows


def collect_instruction_metadata(home: Path, repo_root: Path) -> list[dict[str, Any]]:
    result = []
    seen: set[str] = set()
    for path, category in _instruction_paths(home, repo_root):
        normalized = str(path.resolve(strict=False)).casefold()
        if normalized in seen or not path.is_file():
            continue
        seen.add(normalized)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError):
            result.append({"category": category, "source_path": str(path), "read_status": "UNAVAILABLE"})
            continue
        result.append({
            "category": category,
            "source_path": str(path.resolve()),
            "read_status": "READ_METADATA_ONLY",
            "bytes": len(raw),
            "chars": len(text),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "precedence": "global before applicable project/subtree; deeper project files have later precedence",
            "host_load_status": "UNKNOWN_NOT_EXPOSED_BY_FILESYSTEM",
            "content_stored": False,
        })
    return result


def build_inventory(*, home: Path, repo_root: Path, skill_roots: list[tuple[Path, str]], runtime_tools: int | None,
                    mcp_tools: int | None, mcp_servers: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
    skills = scan_skill_roots(skill_roots)
    instructions = collect_instruction_metadata(home, repo_root)
    valid = [row for row in skills if row["discovery_status"] == "DISCOVERED"]
    metadata_rows = [{key: row.get(key) for key in ("capability_id", "display_name", "capability_type", "source_kind", "version_if_observable", "metadata_summary", "metadata_bytes", "host_enabled_status", "permissions_risk_summary", "discovery_status", "nexus_lifecycle_status", "exposure_status", "evaluation_status")} for row in valid]
    metadata_footprint = len(_canonical_bytes(metadata_rows))
    full_instruction_bytes = 2 + sum(row["full_instruction_serialized_bytes"] for row in valid) + max(0, len(valid) - 1)
    full_instruction_chars = 2 + sum(row["full_instruction_serialized_chars"] for row in valid) + max(0, len(valid) - 1)
    names: dict[str, int] = {}
    exact: dict[tuple[str, str], int] = {}
    for row in valid:
        names[row["display_name"].casefold()] = names.get(row["display_name"].casefold(), 0) + 1
        exact_key = (row["display_name"].casefold(), row["instruction_hash_if_safe"] or "")
        exact[exact_key] = exact.get(exact_key, 0) + 1
    representative = next((row for row in metadata_rows if row["display_name"].casefold() == "project-experience-curator"), None)
    representative_full = next((row for row in valid if row["display_name"].casefold() == "project-experience-curator"), None)
    representative_bytes = len(_canonical_bytes([representative])) if representative else len(_canonical_bytes([]))
    global_rows = [row for row in instructions if row.get("category") == "GLOBAL" and row.get("read_status") == "READ_METADATA_ONLY"]
    project_rows = [row for row in instructions if row.get("category") == "PROJECT_CHAIN" and row.get("read_status") == "READ_METADATA_ONLY"]
    summary = {
        "schema_version": 1,
        "inventory_scope": "LOCAL_METADATA_ONLY",
        "skill_files_discovered": len(skills),
        "valid_skill_metadata_count": len(valid),
        "counts_by_source_kind": {kind: sum(row["source_kind"] == kind for row in valid) for kind in sorted({row["source_kind"] for row in valid})},
        "counts_by_capability_type": {"SKILL": len(valid), "RUNTIME_TOOL_REGISTRY": runtime_tools or 0},
        "duplicate_capability_name_groups": sum(count > 1 for count in names.values()),
        "duplicate_exact_skill_groups": sum(count > 1 for count in exact.values()),
        "malformed_frontmatter_count": sum(row["discovery_status"] == "MALFORMED_FRONTMATTER" for row in skills),
        "unreadable_or_invalid_count": sum(row["discovery_status"] == "UNREADABLE_OR_INVALID" for row in skills),
        "unknown_version_count": sum(row.get("version_status") == "UNKNOWN_VERSION" for row in valid),
        "host_enabled_status_counts": {status: sum(row["host_enabled_status"] == status for row in valid) for status in sorted({row["host_enabled_status"] for row in valid})},
        "declared_risk_counts": {risk: {str(value): sum(row["permissions_risk_summary"].get(risk) == value for row in valid) for value in (True, False, "UNKNOWN")} for risk in ("network", "write", "secret")},
        "metadata_registry_serialized_bytes": metadata_footprint,
        "metadata_registry_serialized_chars": len(_canonical_bytes(metadata_rows).decode("utf-8")),
        "all_skill_instructions_theoretical_bytes": full_instruction_bytes,
        "all_skill_instructions_theoretical_chars": full_instruction_chars,
        "representative_metadata_only_bytes": representative_bytes,
        "representative_skill_instruction_bytes": representative_full.get("instruction_bytes_if_observable") if representative_full else None,
        "representative_skill_name_is_project_experience_curator": bool(representative and representative["display_name"].casefold() == "project-experience-curator"),
        "host_baseline_instruction": {
            "global_source_count": len(global_rows),
            "global_bytes": sum(row.get("bytes", 0) for row in global_rows),
            "global_chars": sum(row.get("chars", 0) for row in global_rows),
            "project_chain_source_count": len(project_rows),
            "project_chain_bytes": sum(row.get("bytes", 0) for row in project_rows),
            "project_chain_chars": sum(row.get("chars", 0) for row in project_rows),
            "always_loaded_status": "UNKNOWN_NOT_EXPOSED_BY_FILESYSTEM",
        },
        "host_tool_registry": {"status": "CALLER_SUPPLIED_OBSERVATION" if runtime_tools is not None else "UNAVAILABLE",
            "observation_source": "CALLER_SUPPLIED_RUNTIME_TOOL_COUNTS" if runtime_tools is not None else "UNAVAILABLE",
            "reproducibility": "NOT_ENUMERATED_BY_COMMITTED_DISCOVERY_HARNESS",
            "tool_count": runtime_tools, "mcp_tool_count": mcp_tools, "mcp_server_count": mcp_servers,
            "ui_skill_catalog_count": "UNAVAILABLE"},
        "plugin_catalog": {"status": "UNAVAILABLE_BY_SUPPORTED_ENUMERATION_INTERFACE", "credential_presence": "UNAVAILABLE_NOT_SCANNED"},
        "mcp_server_details": {"status": "UNAVAILABLE_BY_LOCAL_DISCOVERY_HARNESS", "credential_values_read": False},
        "tokenizer_telemetry": "UNAVAILABLE",
        "provider_cost_telemetry": "UNAVAILABLE",
        "project_experience_curator": {"status": "FOUND" if summary_safe_name_present(skills) else "NOT_FOUND_IN_INSPECTED_SKILL_ROOTS",
            "nexus_lifecycle_status": "DISCOVERED_NOT_EVALUATED", "future_relation": "POTENTIAL_CANDIDATE_PRODUCER_OR_COMPATIBILITY_LAYER; NOT EVALUATED"},
    }
    inventory = {
        "inventory_kind": "LOCAL_PRIVATE_HOST_INVENTORY",
        "skills": skills,
        "host_baseline_instructions": instructions,
        "runtime_tool_registry_observation": {"tool_count": runtime_tools, "mcp_tool_count": mcp_tools, "mcp_server_count": mcp_servers,
            "ui_skill_catalog_count": "UNAVAILABLE", "plugin_names_or_config": "NOT_COLLECTED"},
        "summary": summary,
    }
    return inventory, summary


def summary_safe_name_present(skills: list[dict[str, Any]]) -> bool:
    return any(row.get("display_name", "").casefold() == "project-experience-curator" for row in skills)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Skill/AGENTS metadata inventory; never emits Skill bodies")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--external-output", type=Path, required=True, help="Private local inventory path outside the repository")
    parser.add_argument("--summary-output", type=Path, required=True, help="Sanitized aggregate JSON path inside the repository")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--skill-root", action="append", default=[], help="Optional extra Skill root as KIND=PATH")
    parser.add_argument("--runtime-tool-count", type=int)
    parser.add_argument("--mcp-tool-count", type=int)
    parser.add_argument("--mcp-server-count", type=int)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    external_output = args.external_output.resolve()
    summary_output = args.summary_output.resolve()
    if external_output == repo_root or repo_root in external_output.parents:
        raise SystemExit("Refusing to write private inventory inside the Git repository")
    if external_output.exists() or summary_output.exists():
        raise SystemExit("Refusing to overwrite existing inventory or summary")
    roots: list[tuple[Path, str]] = []
    defaults = [
        (args.home / ".codex" / "skills", "CODEX_SKILLS"),
        (args.home / ".agents" / "skills", "AGENTS_SKILLS"),
        (args.home / ".codex" / "plugins" / "cache" / "openai-bundled", "BUNDLED_PLUGIN_SKILLS"),
        (args.home / ".codex" / "plugins" / "cache" / "openai-curated-remote", "CURATED_PLUGIN_SKILLS"),
    ]
    roots.extend((path, kind) for path, kind in defaults if path.exists())
    for entry in args.skill_root:
        kind, sep, raw_path = entry.partition("=")
        if not sep:
            raise SystemExit("--skill-root must be KIND=PATH")
        roots.append((Path(raw_path).expanduser(), kind))
    inventory, summary = build_inventory(home=args.home.expanduser().resolve(), repo_root=repo_root, skill_roots=roots,
        runtime_tools=args.runtime_tool_count, mcp_tools=args.mcp_tool_count, mcp_servers=args.mcp_server_count)
    external_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    external_output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
