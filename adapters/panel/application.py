"""Composition root for the command-scoped Operator Panel."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.context import ContextPackService
from kernel.metering import MeteringService
from kernel.memory.service import MemoryService
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.runtime.panel import PanelQueryService
from kernel.skills import SkillRegistryService

from adapters.panel.viewmodel import PanelViewModel


class PanelApplication:
    def __init__(self, *, store, view_model, context_packs, metering, owns_store):
        self.store = store
        self.view_model = view_model
        self.context_packs = context_packs
        self.metering = metering
        self._owns_store = owns_store

    def close(self) -> None:
        if self._owns_store:
            self.store.close()


def open_panel_application(
    data_root: str | Path,
    *,
    policy_path: str | Path | None = None,
    independent_purge_journal_path: str | Path | None = None,
    writer_services: dict | None = None,
    read_only: bool = False,
) -> PanelApplication:
    """Compose from writer-owned services or own a standalone store.

    The writer-owned command path injects its services; that composition never
    opens a second ObjectStore or attempts to acquire another writer lock.
    ``read_only`` explicitly opens an existing bound instance without startup maintenance.
    """
    if writer_services is not None and read_only:
        raise ValueError("PANEL_READ_ONLY_WRITER_COMPOSITION_INVALID")
    root = Path(data_root).expanduser().resolve()
    if not (root / "nexus.sqlite").is_file():
        raise ValueError("No existing Nexus database at --data-root; the panel will not initialize one.")
    if writer_services is not None:
        required = {"store", "runtime", "participation", "panel_queries", "memory", "context_packs", "metering", "skills"}
        if set(writer_services) != required:
            raise ValueError("PANEL_WRITER_SERVICE_BUNDLE_INVALID")
        store = writer_services["store"]
        if Path(store.data_root).resolve() != root:
            raise ValueError("PANEL_WRITER_DATA_ROOT_MISMATCH")
        return _compose_panel(store=store, services=writer_services, owns_store=False)
    policy = None
    if policy_path is not None:
        policy = json.loads(Path(policy_path).expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    store = ObjectStore(
        root,
        policy=policy,
        independent_purge_journal_path=independent_purge_journal_path,
        read_only=read_only,
    )
    try:
        authority = AuthorityService(store, store.policy)
        participation = ParticipationModeService(store)
        memory = MemoryService(store, authority, verifier=None)
        metering = MeteringService(store, authority, participation)
        skills = SkillRegistryService(store=store, authority=authority, participation=participation)
        services = {
            "store": store,
            "runtime": DeterministicRuntime(store, authority, BudgetService(store), TraceRuntime(store, authority)),
            "participation": participation,
            "panel_queries": PanelQueryService(store),
            "memory": memory,
            "context_packs": ContextPackService(store=store, authority=authority, participation=participation,
                                                  memory=memory, metering=metering),
            "metering": metering,
            "skills": skills,
        }
        return _compose_panel(store=store, services=services, owns_store=True)
    except Exception:
        store.close()
        raise


def _compose_panel(*, store, services: dict, owns_store: bool) -> PanelApplication:
    view_model = PanelViewModel(
        runtime=services["runtime"], participation=services["participation"],
        panel_queries=services["panel_queries"], memory=services["memory"],
        context_packs=services["context_packs"], metering=services["metering"],
        skills=services["skills"], read_only=bool(getattr(store, "read_only", False)),
    )
    return PanelApplication(store=store, view_model=view_model,
                            context_packs=services["context_packs"], metering=services["metering"],
                            owns_store=owns_store)
