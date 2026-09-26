"""Versioned extraction of governed NexusObject references from frozen documents.

This deliberately does not recursively scan arbitrary JSON: only fields declared
as object references by the corresponding frozen schema are considered.
"""

from __future__ import annotations

from typing import Any


def known_object_refs(document: dict[str, Any]) -> list[str]:
    schema_id = document.get("schema_id")
    version = document.get("schema_version")
    fields: tuple[str, ...]
    if (schema_id, version) == ("nexus.task_contract", 1):
        fields = ("input_object_refs",)
    elif (schema_id, version) == ("nexus.subtask", 1):
        fields = ("input_object_refs",)
    elif (schema_id, version) == ("nexus.run_manifest", 1):
        fields = (
            "input_object_refs", "task_contract_ref", "context_object_refs",
            "route_decision_ref", "input_ref",
        )
    elif (schema_id, version) == ("nexus.run_manifest", 2):
        fields = (
            "input_object_refs", "context_object_refs", "route_decision_ref",
        )
    else:
        return []

    refs: set[str] = set()
    for field in fields:
        value = document.get(field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item:
                refs.add(item)
    return sorted(refs)
