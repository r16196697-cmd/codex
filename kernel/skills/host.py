"""Host-neutral and Codex filesystem inventory boundaries for native Skills."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


_MAX_NATIVE_SKILLS = 256
_MAX_NATIVE_MANIFEST_BYTES = 64 * 1_048_576


class HostSkillInventoryAdapter(Protocol):
    """Return attributable availability only; never claim activation or use."""

    def inventory(self) -> dict:
        """Return {adapter_id, skills:[{name, availability, provenance, revision_sha256?}]}.

        `inventory_complete` states whether absence from `skills` is evidence
        of unavailability. Each `provenance` is `ADAPTER_DISCOVERY` or
        `HOST_DECLARED`. Implementations must not expose private paths or
        opaque Host state through this boundary.
        """
        ...


class CodexAgentSkillsInventoryAdapter:
    """Inspect only explicitly configured public Agent Skills filesystem roots.

    A discovered package means only that a matching package revision is
    available to Codex from a standard root. It says nothing about selection,
    loading, delivery, model visibility, or use.
    """

    adapter_id = "codex-agent-skills-filesystem-v1"

    def __init__(self, *, package_reader, roots: dict[str, str | Path | list[str | Path] | tuple[str | Path, ...]],
                 roots_are_exhaustive: bool = False):
        self._package_reader = package_reader
        self._roots_are_exhaustive = roots_are_exhaustive
        configured = []
        for scope, values in roots.items():
            if scope not in {"REPO", "USER"}:
                continue
            paths = values if isinstance(values, (list, tuple)) else [values]
            configured.extend((scope, Path(root).expanduser().resolve()) for root in paths)
        self._roots = list(dict.fromkeys(configured))

    def inventory(self) -> dict:
        skills = []
        complete = bool(self._roots) and self._roots_are_exhaustive
        total_bytes = 0
        try:
            for scope, root in sorted(self._roots, key=lambda item: (item[0], str(item[1]))):
                if not root.is_dir():
                    complete = False
                    continue
                try:
                    with os.scandir(root) as scanner:
                        entries = []
                        for item in scanner:
                            try:
                                entries.append((item.name, item.is_dir(follow_symlinks=False), item.is_symlink()))
                            except OSError:
                                complete = False
                except (OSError, UnicodeError):
                    complete = False
                    continue
                entries.sort(key=lambda item: item[0].encode("utf-8", errors="strict"))
                for entry_name, is_directory, is_symlink in entries:
                    if is_symlink:
                        complete = False
                        continue
                    if not is_directory:
                        continue
                    if len(skills) >= _MAX_NATIVE_SKILLS:
                        complete = False
                        break
                    try:
                        package = self._package_reader(scope, entry_name, root_override=root)
                    except Exception:
                        complete = False
                        continue
                    total_bytes += sum(item["size_bytes"] for item in package["manifest"])
                    if total_bytes > _MAX_NATIVE_MANIFEST_BYTES:
                        complete = False
                        break
                    skills.append({
                        "name": package["name"],
                        "availability": "AVAILABLE",
                        "provenance": "ADAPTER_DISCOVERY",
                        "revision_sha256": package["package_manifest_sha256"],
                    })
                if len(skills) >= _MAX_NATIVE_SKILLS or total_bytes > _MAX_NATIVE_MANIFEST_BYTES:
                    break
        except (OSError, UnicodeError):
            complete = False
        names = [item["name"] for item in skills]
        if len(names) != len(set(names)):
            complete = False
        skills.sort(key=lambda item: (item["name"], item["revision_sha256"]))
        return {"adapter_id": self.adapter_id, "inventory_complete": complete, "skills": skills}
