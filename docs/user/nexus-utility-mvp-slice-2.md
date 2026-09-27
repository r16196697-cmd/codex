# Nexus Utility MVP — Slice 2

**Status: IMPLEMENTED FOR REVIEW.** This is the production Context Pack and value-metering foundation; it does not claim Host-level exposure or savings.

## Context Pack

`ContextPackService` compiles an immutable Nexus Artifact bound to a Task and Run. Callers select explicit references; an optional query searches only governed, admitted Memory through `MemoryService.search_admitted`. Raw history, opaque Host Memory, Academy fixtures, and Skill catalogs are not queried. Sources must be authorized for inspection, active, valid, available, UTF-8, classified inside the Run boundary, and one of the eligible canonical Object types. Nesting a prior Context Pack is rejected.

Selection is deterministic: source types follow the frozen service order (`user_input`, `task_contract`, `evidence`, `claim`, `artifact`, `verification`, `admitted_memory`), then references sort by UTF-8 byte order. The compiler serializes canonical UTF-8 JSON with sorted keys and compact separators. The content hash commits to Task, Run, selection basis, source references, classifications, source integrity hashes, and selected content. The exact payload byte count and ObjectStore integrity hash are persisted; oversized packs fail closed.

The Context Pack Artifact is the canonical portable payload, independent of any model renderer. A Host adapter may consume its reference and declare it in a Run Manifest without changing the Core representation. A small SQLite index stores safe listing metadata and is redacted through the existing purge path. Provenance and `derived_from` Object relations preserve source lineage. The panel displays only references/counts, hashes, size, and status; it never renders the payload body.

Compilation status is `PACK_COMPILED`. A Run-bound Pack must use the exact grant stored on its target Run, and each source classification must satisfy that Run's own allowed classifications and handling tags. Delivery remains `NOT_DECLARED` until a Run Manifest for the same bound Run references the pack. A manifest that uses the existing Codex Hosted Bridge declaration is reported as `HOST_DECLARED_DELIVERY`; actual delivery remains `UNKNOWN`, and no model-visible exposure is inferred.

## Participation mode

The existing instance-scoped Participation Mode remains separate from Runtime safety/recovery mode. Context compilation is available only in `ACTIVE` and is subject to existing authorization and classification checks. `OBSERVE` and `BYPASS` do not compile Context Packs. The Hosted Bridge's existing ACTIVE-only guards continue to block automatic ingestion outside ACTIVE. Persisted Context Pack history is not deleted when changing modes. Existing fail-closed disengagement checks remain in force.

## Value metering

`MeteringService` appends integrity-checked records. Each metric carries a value, unit, source provenance (`OBSERVED`, `HOST_DECLARED`, `DERIVED`, or `UNAVAILABLE`), basis, and separate estimate status. Estimated values preserve their source provenance and require a persisted pricing ID, version, and formula. Records use `record_id` as an idempotent logical event identity: exact semantic replay returns the original record and conflicting payloads fail closed. Omitted Host metrics are `null / UNAVAILABLE`. Host `input_tokens` is recorded only as total-turn input; model-visible input requires a separate explicit Host declaration and basis. Cached input is separate; uncached input is derived only when both total and cached counts share a declared usage basis. Neither cache use nor pack size is credited as Nexus savings.

Exact serialized Context Pack bytes are recorded as `DERIVED`. Pack token count, Skill instruction tokens, duplicate/reused work, token savings, costs, and model-visible input remain unavailable unless supported by explicit evidence. Host-declared telemetry can be attached only to a matching ACTIVE Nexus Run; live OBSERVE metering is unsupported until an independent observation identity exists. No cost/token savings claim is made by this Slice.

The native Tkinter companion panel reads sanitized ViewModel projections from application services. A live companion must receive the writer-owned services through `writer_services`; this attach path does not acquire a second writer lock and closing the panel does not close the writer store. Standalone mode may own a store when no writer is active. CONTEXT reports the latest compiled pack metadata; VALUE reads persisted metering records. SKILLS remains `NOT IMPLEMENTED`. UI code does not access SQLite or ObjectStore directly.

Host portability remains deferred, not abandoned. This Slice ran no Host, DSH, or Second Host execution and did not modify the Foundation Contracts.
