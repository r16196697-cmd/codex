# Nexus Utility MVP — Slice 2

**Status: IMPLEMENTED FOR REVIEW.** This is the production Context Pack and value-metering foundation; it does not claim Host-level exposure or savings.

## Context Pack

`ContextPackService` compiles an immutable Nexus Artifact bound to a Task and Run. Callers select explicit references; an optional query searches only governed, admitted Memory through `MemoryService.search_admitted`. Raw history, opaque Host Memory, Academy fixtures, and Skill catalogs are not queried. Sources must be authorized for inspection, active, valid, available, UTF-8, classified inside the Run boundary, and one of the eligible canonical Object types. Nesting a prior Context Pack is rejected.

Selection is deterministic: source types follow the frozen service order (`user_input`, `task_contract`, `evidence`, `claim`, `artifact`, `verification`, `admitted_memory`), then references sort by UTF-8 byte order. The compiler serializes canonical UTF-8 JSON with sorted keys and compact separators. The content hash commits to Task, Run, selection basis, source references, classifications, source integrity hashes, and selected content. The exact payload byte count and ObjectStore integrity hash are persisted; oversized packs fail closed.

The Context Pack Artifact is the canonical portable payload, independent of any model renderer. A Host adapter may consume its reference and declare it in a Run Manifest without changing the Core representation. A small SQLite index stores safe listing metadata and is redacted through the existing purge path. Provenance and `derived_from` Object relations preserve source lineage. The panel displays only references/counts, hashes, size, and status; it never renders the payload body.

Compilation status is `PACK_COMPILED`. Delivery remains `NOT_DECLARED` until a Run Manifest explicitly references the pack. A manifest that uses the existing Codex Hosted Bridge declaration is reported as `HOST_DECLARED_DELIVERY`; this records a Host declaration, not independent proof of delivery. `model_visible_exposure` remains `UNKNOWN` in all cases.

## Participation mode

The existing instance-scoped Participation Mode remains separate from Runtime safety/recovery mode. Context compilation is available only in `ACTIVE` and is subject to existing authorization and classification checks. `OBSERVE` and `BYPASS` do not compile Context Packs. The Hosted Bridge's existing ACTIVE-only guards continue to block automatic ingestion outside ACTIVE. Persisted Context Pack history is not deleted when changing modes. Existing fail-closed disengagement checks remain in force.

## Value metering

`MeteringService` appends integrity-checked records. Each metric carries a value, unit, provenance (`OBSERVED`, `HOST_DECLARED`, `DERIVED`, `UNAVAILABLE`, or `ESTIMATED`), and basis. Omitted Host metrics are `null / UNAVAILABLE`. Host `input_tokens` is recorded only as total-turn input; model-visible input requires a separate explicit Host declaration and basis. Cached input is separate; uncached input is derived only when both total and cached counts share a declared usage basis. Neither cache use nor pack size is credited as Nexus savings.

Exact serialized Context Pack bytes are recorded as `DERIVED`. Pack token count, Skill instruction tokens, duplicate/reused work, token savings, costs, and model-visible input remain unavailable unless supported by explicit evidence. Estimated cost requires persisted pricing ID, pricing version, and formula and retains `ESTIMATED` provenance. No cost/token savings claim is made by this Slice.

The native Tkinter companion panel reads sanitized ViewModel projections from the application services. CONTEXT reports the latest compiled pack metadata; VALUE reads persisted metering records. SKILLS remains `NOT IMPLEMENTED`. UI code does not access SQLite or ObjectStore directly.

Host portability remains deferred, not abandoned. This Slice ran no Host, DSH, or Second Host execution and did not modify the Foundation Contracts.
