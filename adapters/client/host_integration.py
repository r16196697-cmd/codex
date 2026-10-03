"""Host-neutral, read-only boundary for translating a host event to Nexus context."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from adapters.client.project_locator import find_project_manifest, locate_project
from adapters.client.presence import build_project_workspace


HOST_SESSION_SOURCES = frozenset({
    "NEW_SESSION", "RESUMED_SESSION", "RESET_SESSION", "COMPACTED_SESSION",
})


@dataclass(frozen=True)
class HostSessionStartInput:
    """Normalized lifecycle input shared by all future Host adapters."""

    cwd: str
    host_name: str
    host_version: str | None = None
    session_id: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class NexusHostContext:
    """Disposable derived Host context; never persisted as canonical state."""

    schema_version: int
    project_id: str
    workspace: dict[str, Any]
    state_ref: str | None
    freshness: dict[str, Any]
    exposure_status: str

    def to_document(self) -> dict[str, Any]:
        return asdict(self)


def build_host_context(
    request: HostSessionStartInput, *, locator=None, workspace_builder=None,
) -> NexusHostContext:
    """Resolve one project before touching host binding or Nexus storage.

    The manifest check is intentionally first so a user-level lifecycle adapter
    running in ordinary non-Nexus repositories exits without reading the host
    registry or opening a Nexus instance.
    """
    if not isinstance(request.cwd, str) or not request.cwd.strip():
        raise ValueError("HOST_SESSION_START_INPUT_INVALID")
    if request.source is not None and request.source not in HOST_SESSION_SOURCES:
        raise ValueError("HOST_SESSION_START_INPUT_INVALID")
    manifest = find_project_manifest(Path(request.cwd))
    selected_locator = locator or locate_project
    resolved = selected_locator(start_dir=manifest.project_root)
    selected_builder = workspace_builder or build_project_workspace
    workspace = selected_builder(
        start_dir=manifest.project_root,
        locator=lambda **_kwargs: resolved,
    )
    return NexusHostContext(
        schema_version=1,
        project_id=manifest.project_id,
        workspace=workspace,
        state_ref=workspace.get("current_state", {}).get("ref"),
        freshness=workspace.get("freshness", {"status": "UNKNOWN"}),
        exposure_status=workspace.get("context", {}).get("model_visible_exposure", "UNKNOWN"),
    )


def render_host_workspace(context: NexusHostContext) -> str:
    """Small plain-text projection that does not imply model-visible receipt."""
    workspace = context.workspace
    state = workspace.get("current_state", {})
    context_info = workspace.get("context", {})
    lines = [f"Nexus Project Workspace · {context.project_id}"]
    if state.get("status") == "AVAILABLE":
        revision = state.get("revision")
        lines.extend((
            f"Current State: r{revision if isinstance(revision, int) else 'UNKNOWN'}",
            f"Accepted revision: {state.get('accepted_revision') or 'UNKNOWN'}",
            f"当前目标: {state.get('objective') or 'UNAVAILABLE'}",
            f"下一步: {state.get('next_step') or 'UNAVAILABLE'}",
        ))
        last = workspace.get("last_completed") or {}
        if last.get("summary"):
            lines.append(f"最近完成: {last['summary']}")
        constraints = state.get("critical_constraints") or []
        lines.extend(f"当前约束: {item}" for item in constraints[:3])
    else:
        lines.append("Current State: UNAVAILABLE")
    lines.append(
        f"Context: {context_info.get('pack_id') or 'UNAVAILABLE'}; "
        f"model visibility: {context.exposure_status or 'UNKNOWN'}"
    )
    return "\n".join(lines)
