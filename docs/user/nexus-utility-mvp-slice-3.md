# Nexus Utility MVP — Slice 3

**Status: IMPLEMENTED FOR REVIEW.** This adds a production Registry for explicitly registered Agent Skills packages. The package format remains the Agent Skills `SKILL.md` format; Nexus adds governed identity, integrity, review, resolution, and usage records around that format.

## Registration and trust

Registration is explicit and reads only a package named by the caller under a configured logical source root. It does not scan a computer or import Host Memory. The package must contain UTF-8 `SKILL.md` with YAML frontmatter `name` and `description`; `name` must match the final package directory name. Present standard optional fields (`license`, `compatibility`, `metadata`, and `allowed-tools`) are type/constraint checked but are not required. Package file count and byte limits are enforced; traversal, escaping symlinks, malformed YAML, YAML aliases, duplicate frontmatter keys, and unsupported file types fail closed. Scripts are never executed. Package bodies are kept in ObjectStore only for the governed `SKILL.md` snapshot; Registry metadata stores sorted relative paths, sizes, and hashes, not file bodies or private absolute paths.

Identity includes name, source scope, source namespace, relative source reference, and package manifest hash. The service calculates both the `SKILL.md` content hash and deterministic package manifest hash. New entries begin `REGISTERED`; `ENABLED` requires an existing, unexpired `APPROVE` ApprovalDecision from an active Human, bound to the Skill ID, exact package revision, status, Task/Run, and governing grant. `DISABLED`/`REJECTED` still require normal Run-grant authorization. Review mutations use the canonical command ledger, so replaying an old enable command returns its original result without reapplying it after a later disable. Package revision changes mark older entries `STALE`. Same-name registrations remain separate and can yield `AMBIGUOUS` rather than merging.

## Progressive disclosure and resolution

Discovery and candidate selection use only persisted name and description metadata. Candidate ranking is deterministic term overlap with skill ID as tie-break; tied top results return `AMBIGUOUS`. Skill bodies are not loaded for search. Resolution order is `HOST_NATIVE` → `NEXUS_FALLBACK` → `UNSUPPORTED`.

The Host adapter is a narrow injected inventory interface whose entries identify `ADAPTER_DISCOVERY` or `HOST_DECLARED` provenance. This repository does not inspect Codex private state. Host availability means only available; it does not establish selected, loaded, delivered, model-visible, or used. A native match records resolution and leaves the Nexus instruction body unloaded.

Fallback is available only in Participation Mode `ACTIVE`, after explicit enablement, when the Host inventory explicitly reports the Skill unavailable and the package has no unhydrated auxiliary resources. The service verifies the package revision and creates a governed Artifact from the exact `SKILL.md` bytes with `derived_from` lineage to the registered source snapshot. Its canonical payload is deterministic for exact command replay. The Artifact reference can be supplied to the existing Context Pack service; no parallel prompt pipeline is introduced. References, assets, and scripts are not expanded or executed. Purging the source snapshot or fallback Artifact detaches its exact Object reference and prevents stale fallback loading. Artifact creation does not prove Host delivery or model-visible exposure; both remain `UNKNOWN`.

`OBSERVE` records no automatic Skill selection or fallback load. `BYPASS` likewise performs no Nexus resolution and retains Registry/history state. Runtime safety/recovery modes are unchanged. Academy fixtures are not a Registry source.

## Panel and metering

The command-scoped Operator Panel's SKILLS page reads a sanitized projection with counts, identity/revision summaries, last resolution, load state, and truthful delivery/model-visible status. It never displays instruction bodies. It is not a concurrent attachment to another writer; there is no daemon or IPC endpoint.

VALUE reports measured candidate count, exact loaded instruction byte size, and selection latency when a persisted resolution record provides them. Skill instruction tokens remain `null / UNAVAILABLE`; bytes are not converted to token estimates. Host-native availability, selection, load, delivery, use, and task outcome remain separate facts. Selecting a Skill does not imply token savings.

Phase 6 remains `CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED`. This Slice ran no Host experiment and does not certify Skills. Host portability, DSH, and Second Host remain deferred.
