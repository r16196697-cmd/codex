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


def resolve_governed_object_resource(conn, resource: str | None) -> str | None:
    """Resolve only an exact object ID or canonical ``object:<id>`` resource.

    A resource is governed only when its normalized value exists in both the
    Object identity and state tables. URLs and arbitrary strings are untouched.
    """
    if not isinstance(resource, str) or not resource:
        return None
    object_id = resource[7:] if resource.startswith("object:") else resource
    if not object_id:
        return None
    row = conn.execute(
        "SELECT o.object_id FROM objects o JOIN object_states s USING(object_id) WHERE o.object_id=?",
        (object_id,),
    ).fetchone()
    return row[0] if row else None


def redact_governed_object_values(value: Any, purged_ids: set[str]) -> Any:
    """Recursively redact exact IDs and their exact canonical resource form."""
    if isinstance(value, str):
        normalized = value[7:] if value.startswith("object:") else value
        return "REDACTED_PURGED" if normalized in purged_ids and value in {normalized, "object:" + normalized} else value
    if isinstance(value, list):
        return [redact_governed_object_values(item, purged_ids) for item in value]
    if isinstance(value, dict):
        return {key: redact_governed_object_values(item, purged_ids) for key, item in value.items()}
    return value
