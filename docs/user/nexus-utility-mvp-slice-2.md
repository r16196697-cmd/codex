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

The native Tkinter Panel is a command-scoped Operator Panel, not a concurrent live companion. Launch `python -m adapters.client --data-root <existing-data-root> panel` (optionally with the same `--policy` and `--independent-purge-journal` arguments used by the operator CLI) to open the canonical writer services and Panel in one process. That command owns the single writer for the Panel session; closing the Panel closes that command's writer. `python -m adapters.panel --data-root ...` is a standalone command that also owns the writer and cannot attach while another writer is open. There is no persistent writer daemon or IPC endpoint. Panel projections and Participation Mode controls use the command's writer-owned services. CONTEXT reports the latest compiled pack metadata; VALUE reads persisted metering records. SKILLS reads the production Registry projection. UI code does not access SQLite or ObjectStore directly.

Current CLI writer lifecycle is command-scoped: each `python -m adapters.client` invocation owns one `ObjectStore` and closes it on exit. The `panel` command is the supported same-process Panel composition path; this repository does not provide a separate persistent writer daemon or attach-to-another-process IPC endpoint.

## Metering record schema version

`nexus.metering_record@1` remains the historical contract, including its original `ESTIMATED` provenance representation. New records validate against `nexus.metering_record@2`, where source provenance and `estimate_status` are orthogonal. Migration 25 normalizes already-persisted migration-24 rows; no additional database migration is needed for the v2 JSON contract. Unknown schema versions fail closed through the normal schema registry lookup.

Host portability remains deferred, not abandoned. This Slice ran no Host, DSH, or Second Host execution and did not modify the Foundation Contracts.
