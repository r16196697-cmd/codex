# Legacy Bootstrap & Host Environment Dependency Audit

**Status:** `AUDIT_OBSERVATION`
**Architecture proposal:** `PROPOSED`
**Audit base:** `93f0ed6778a3edc996f159457015747163ee17a5`
**Phase 6:** `CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED` (unchanged; not reopened)

This is a read-only architecture and dependency audit. No Host execution, settings changes, Skill execution, migration, or Core change was performed. Local instruction text, Custom Instructions text, credentials, application state contents, and absolute personal paths are not included here.

## Evidence boundary and local metadata

File existence, byte counts, hashes, and marker checks were observed locally. Config entries are reported as declarations from the local Codex CLI config; they do not prove runtime availability, application-level state, model visibility, or loading. Logical artifact labels below are not filesystem paths.

| Logical artifact | Exists | Bytes | SHA-256 | Sanitized observations | Nexus reference/dependency |
|---|---:|---:|---|---|---|
| `USER_GLOBAL_AGENT_INSTRUCTIONS` | yes | 2,536 | `405869e1748ea376f1b9152be64a7cb73f9c4169d1d9aa1fb83183eb55327106` | `codex-efficient-learning` start/end markers present; topics include tool pre-inspection, verification, minimal changes, root-cause debugging, and project-experience curation | No runtime/test dependency found. Academy records the baseline as unchanged; effective loading in a particular Host invocation is not proven by file presence. |
| `PROJECT_EXPERIENCE_CURATOR_INSTRUCTIONS` | yes | 7,425 | `351748a259365dc561af6c6865b86c5a2ba3388c6a5c7bb350c0801c61e80c10` | Instruction artifact references project instruction files and maintaining verified project experience; it can write project guidance | Nexus discovery/static-screen references only. It is not imported or executed by Core. |
| `PROJECT_EXPERIENCE_CURATOR_DISPLAY_METADATA` | yes | 334 | `007db3f3f455f165705089fa980cf458da2740c48549da84860c49a5342d7728` | Companion metadata exists; only its `interface` key name was inspected | No Nexus runtime dependency found. Metadata does not prove that the Skill is loaded. |
| `CODEX_CLI_CONFIGURATION` | yes | 3,148 | `639b9c63898aa893c728c5a095ece55cb28c41c4c905ac517696902d470cb229` | Config declares 10 plugin entries, all marked enabled, and one MCP server. CLI feature flags `memories` and `chronicle` are false. These are CLI config declarations, not GUI settings or runtime proof. | Core/tests do not read this config. Capability inventory scripts use Codex directory conventions for default roots. |

The safely inspected CLI config and application-state key-name surface did not establish a mapping between GUI Custom Instructions and AGENTS. No Custom Instructions body was read. Their relationship is `UNKNOWN`. The CLI Memory flags above do not establish ChatGPT/Codex desktop Memory or tool-chat Memory state; that relationship is also `UNKNOWN`.

## Legacy behavior classification

Each behavior receives exactly one primary disposition. `MIGRATE_TO_NEXUS` means implement or retain the behavior as a Nexus-owned, testable invariant where it concerns Nexus evidence or state; it does not authorize a Core change in this audit.

| Legacy behavior/rule observed | Primary bucket | Evidence and rationale |
|---|---|---|
| Inspect available Skills, Plugins, MCP, CLI, libraries, scripts, and templates before extensive manual work | `DELEGATE_TO_HOST_NATIVE` | Discovery and availability are Host-specific. Nexus may consume a bounded Host profile, but should not impersonate Codex’s catalog or claim an artifact is loaded. |
| Verify claims with project source, tests, logs, and run output | `MIGRATE_TO_NEXUS` | The Academy already uses deterministic fixtures, hashes, provenance, and tests. Nexus evidence/verification records are the appropriate durable mechanism for Nexus-owned claims. |
| Make the smallest task-scoped change and preserve existing architecture | `KEEP_AS_USER_PREFERENCE` | This is a development collaboration preference, not a runtime policy or evidence object. |
| Reproduce/debug failures and establish root cause before changing code | `MIGRATE_TO_NEXUS` | Failure cases, immutable observations, explicit uncertainty, and replayable audit evidence fit Nexus’s existing verification/trace discipline. Root-cause claims must remain evidence-bounded. |
| Curate project experience through `project-experience-curator` | `DELEGATE_TO_HOST_NATIVE` | The local artifact exists and its workflow may write project guidance. No Nexus runtime invokes it; this audit does not execute or alter it. |
| Store durable project experience as canonical knowledge | `UNRESOLVED` | Current evidence identifies a Host-specific AGENTS workflow but establishes neither a portable canonical store nor a governed cross-Host synchronization contract. |
| Use Host-native Memory and Host-specific Skill/tool loading state | `DELEGATE_TO_HOST_NATIVE` | These are Host-owned mechanisms. Nexus can govern what it stores and observe reported metadata, but cannot infer hidden context or claim native loading. |
| Ask for clarification when the user must decide scope or preference | `KEEP_AS_USER_PREFERENCE` | This is interaction guidance; it does not imply a Nexus Core dependency. |
| Obsolete claim that the Phase 6 matrix was still pending external review | `RETIRE` | External review closed Phase 6 as inconclusive. Its machine execution record remains `STOPPED_ON_PROTOCOL_CONDITION`; this audit does not reopen or rewrite it. |

No local legacy rule was promoted to a frozen Nexus contract by this audit.

## Hidden Host dependency audit

The classification is scoped to the actual Nexus Core/runtime/tests unless a narrower Academy tool dependency is named.

| Surface | Classification | Evidence |
|---|---|---|
| Global AGENTS instructions | `AMBIENT_CONFOUND` | The local file exists and may affect a Codex session. Nexus Core does not read it, and no test requires it. Academy behavioral records treated it as unchanged ambient state; exact loaded content was not observable. |
| GUI Custom Instructions | `AMBIENT_CONFOUND` | Academy records them as unchanged during prior behavioral work. Local config inspection did not establish where GUI instructions are stored or loaded. No Core/test dependency found. |
| `project-experience-curator` | `SOFT_OPTIMIZATION` | Discovery and static screening mention the artifact, but do not invoke it or require it for runtime/tests. It remains `DISCOVERED / UNEVALUATED`. |
| Codex-native Memory | `AMBIENT_CONFOUND` | Academy docs mark cross-session effects as uncharacterized. Nexus has a separate governed `MemoryService`; Core does not need Codex Memory to admit/search/purge Nexus Memory. |
| Any installed Skill | `HARD_DEPENDENCY` for reproducing the local Academy Skill inventory only; `NO_DEPENDENCY_FOUND` for Core/runtime/tests | Discovery and Phase 6 static-screen scripts enumerate local Skill artifacts. They do not execute Skills. Core tests do not require an installed Skill. |
| Plugin/MCP availability | `NO_DEPENDENCY_FOUND` for Core/runtime/tests | The local config declares entries, but Core does not read that config or require those integrations. Academy discovery reports catalog limitations; caller-observed tool metadata is not treated as a committed enumeration. |
| Codex-specific tool/event semantics | `HARD_DEPENDENCY` for Phase 5/6 external behavioral harnesses; `NO_DEPENDENCY_FOUND` for general Core semantics | Phase 5 parses `codex exec --json` events and Codex command/tool event families. Its parser tests use sanitized fixtures. The Core’s dispatcher is injected through ports and is not a Codex CLI loop. |
| Codex-specific filesystem conventions | `HARD_DEPENDENCY` for default local capability-inventory reproduction; `NO_DEPENDENCY_FOUND` for Core/runtime | Discovery and static-screen builders use conventional Codex Skill/plugin roots (with explicit-root options in discovery). This is not a runtime Core storage requirement. |
| Codex CLI executable | `HARD_DEPENDENCY` for external Phase 5/6 Host experiment execution only | Those runners construct a frozen Codex CLI argv. Ordinary repository tests mock the invocation path; this audit did not run it. |

“Hard dependency” above is limited to the named audit/experiment tool, not Nexus Core or normal unit tests. The repository’s Host Bridge is an ingress adapter: `CodexHostedBridge` records caller/Host-declared work through Core APIs; it does not invoke a model or make a Core receipt from an external Academy CLI trial.

## HostEnvironmentManifest V0 — proposal

`HostEnvironmentManifest V0` is `PROPOSED`, not frozen. It should contain portable metadata only. Every field is wrapped in an evidence class so a Host declaration cannot be mistaken for independent observation.

```json
{
  "schema": "nexus.host_environment_manifest",
  "version": 0,
  "status": "PROPOSED",
  "host_id": {"value": null, "evidence": "UNKNOWN"},
  "surface_id": {"value": null, "evidence": "UNKNOWN"},
  "host_version": {"value": null, "evidence": "DECLARED_BY_HOST"},
  "adapter_version": {"value": null, "evidence": "UNKNOWN"},
  "global_instruction_digest": {"value": null, "evidence": "UNKNOWN"},
  "custom_instruction_state": {"value": "UNKNOWN", "evidence": "UNKNOWN"},
  "native_memory": {
    "capability": {"value": "UNKNOWN", "evidence": "UNKNOWN"},
    "state": {"value": "UNKNOWN", "evidence": "UNKNOWN"}
  },
  "skill_catalog_digest": {"value": null, "evidence": "UNKNOWN"},
  "tool_availability_profile": {"value": null, "evidence": "UNKNOWN"},
  "plugin_mcp_profile": {"value": null, "evidence": "UNKNOWN"},
  "sandbox_effect_boundary": {"value": null, "evidence": "UNKNOWN"},
  "unobservable_fields": [],
  "observed_at": null,
  "observation_source": null,
  "governance_coverage": {}
}
```

Allowed evidence labels are exactly `OBSERVED`, `DECLARED_BY_HOST`, `INFERRED`, and `UNKNOWN`. Digests bind artifacts without copying instruction bodies. Profiles should use bounded capability names/booleans or opaque digests; never private text, credentials, absolute personal paths, raw Host state, or thread/database contents. `observation_source` and `observed_at` are required when a field is observed; inferred values must state their derivation. Governance coverage should identify which fields were checked, which were unavailable, and the applicable policy/version, not claim completeness by default.

## Nexus / Host ownership boundary

The current doctrine separates governed Nexus state from Host capabilities. A Host-native improvement should increase Nexus utility; Nexus should govern exposure and evidence without cloning the Host’s private loading or execution machinery.

| Capability | Nexus role | Host role / duplication boundary |
|---|---|---|
| Memory | `OWN_CANONICAL_STATE` for Nexus-owned records; `GOVERN` admission, scope, expiry, search, and purge; optionally `OBSERVE` a bounded Host-reported capability state | `DELEGATE` native Host Memory behavior to the Host. Do not mirror its private store or conflate it with Nexus retrieval. |
| Skill selection | `GOVERN` eligibility/exposure; `ADAPT` a safe catalog profile; `OBSERVE` only explicit invocation evidence | `DELEGATE` native discovery, loading, and invocation. Do not duplicate a Host loader or infer loaded state from installed metadata. |
| Tool selection | `GOVERN` authority, task scope, data boundary, and allowed effects; `OBSERVE` receipts | `DELEGATE` availability and actual Host dispatch through reviewed adapters. Nexus owns effect lifecycle/reconciliation, not a duplicate Host tool catalog. |
| Model routing | `OWN_CANONICAL_STATE` for Nexus route decisions/profiles and budget reservations; `GOVERN` policy and constraints; `ADAPT` provider invocation | Host/provider adapters execute the selected route. Do not claim model identity or cost when the Host does not expose them. |
| Subagents | `OWN_CANONICAL_STATE` for Nexus Task/Run/attempt lineage; `GOVERN` delegated grants, budgets, and termination; `OBSERVE` completion receipts | `DELEGATE` host-native spawning/execution mechanics where used. Do not copy hidden Host conversation/thread state into Nexus. |
| Project experience | `GOVERN` provenance/admission if Nexus later stores portable project facts; `ADAPT` sanitized Host artifacts; `DELEGATE` current curator workflow | Whether Nexus should `OWN_CANONICAL_STATE` for project experience is `UNRESOLVED`; do not duplicate AGENTS generation or import it without an explicit governance contract. |
| Context preparation | `OWN_CANONICAL_STATE` for eligible evidence references/packs; `GOVERN` selection and disclosure; `ADAPT` serialization to a Host request; `OBSERVE` exposure only when instrumented | Host controls ambient/native context and final model-visible composition. Nexus must not claim full exposure from packet delivery alone. |

## Audit conclusion

The inspected local state confirms that the legacy global instruction block and project curator artifacts exist. It does not establish that every Codex surface loads them, or that GUI Custom Instructions map to AGENTS. Nexus Core already owns governed Memory, Run/Task lifecycle, route/budget records, and effect dispatch boundaries; the Host-specific dependencies found are concentrated in Academy inventory/behavioral harnesses and the named Codex ingress adapter. No settings or capability state were changed, and no local instruction content was copied into this artifact.
