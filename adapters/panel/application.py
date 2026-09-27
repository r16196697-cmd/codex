"""Composition root for the local companion panel; requires an existing Nexus root."""

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

from adapters.panel.viewmodel import PanelViewModel


class PanelApplication:
    def __init__(self, *, store, view_model, context_packs, metering):
        self.store = store
        self.view_model = view_model
        self.context_packs = context_packs
        self.metering = metering

    def close(self) -> None:
        self.store.close()


def open_panel_application(
    data_root: str | Path,
    *,
    policy_path: str | Path | None = None,
    independent_purge_journal_path: str | Path | None = None,
) -> PanelApplication:
    """Open a local panel against an existing Nexus database; never bootstrap one."""
    root = Path(data_root).expanduser().resolve()
    if not (root / "nexus.sqlite").is_file():
        raise ValueError("No existing Nexus database at --data-root; the panel will not initialize one.")
    policy = None
    if policy_path is not None:
        policy = json.loads(Path(policy_path).expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    store = ObjectStore(
        root,
        policy=policy,
        independent_purge_journal_path=independent_purge_journal_path,
    )
    try:
        authority = AuthorityService(store, store.policy)
        budget = BudgetService(store)
        trace = TraceRuntime(store, authority)
        runtime = DeterministicRuntime(store, authority, budget, trace)
        participation = ParticipationModeService(store)
        memory = MemoryService(store, authority, verifier=None)
        queries = PanelQueryService(store)
        metering = MeteringService(store, authority, participation)
        context_packs = ContextPackService(
            store=store, authority=authority, participation=participation,
            memory=memory, metering=metering,
        )
        view_model = PanelViewModel(
            runtime=runtime,
            participation=participation,
            panel_queries=queries,
            memory=memory,
            context_packs=context_packs,
            metering=metering,
        )
        return PanelApplication(store=store, view_model=view_model,
                                context_packs=context_packs, metering=metering)
    except Exception:
        store.close()
        raise
