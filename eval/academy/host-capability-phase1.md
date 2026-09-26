# Bootstrap Academy — Host & Capability Discovery / Registry Baseline

Status: `IN PROGRESS — Phase 1`
Baseline: `nexus-v0.1-audited` → `8c3451a937337d0cb3c58031eebb7afa8b7ef729`
Independent Audit: `CLOSED / PASS`
Production qualification: `NOT STARTED`

## Question and boundary

This phase measures what the current Host can discover as metadata without loading full capability instructions. It does not install, enable, disable, promote, or invoke capabilities. The inventory is a point-in-time view of supported local Skill roots and the runtime tool metadata available to this task; it is not a complete Codex product/UI catalog.

The governing distinctions are:

```text
Installed ≠ Loaded
Active ≠ Loaded
Discoverable ≠ model-visible
Registry ≠ Context
```

The long-term shape is recorded only as a candidate: capability lifecycle `DISCOVERED → CANDIDATE → SANDBOX → EVALUATED → SHADOW → ACTIVE`, independently from exposure lifecycle `HIDDEN → DISCOVERABLE → SURFACED → LOADED → RESIDENT`.

## Discovery method

`scripts/eval/discover_host_capabilities.py` reads local instruction-file sizes/hashes and Skill frontmatter metadata. It never writes Skill bodies to the registry or stdout; bodies are read locally only for byte/character counts and SHA-256. Detailed paths and capability names are saved to a private inventory outside the repository. The committed summary contains aggregate counts and sizes only, except for the explicitly named legacy `project-experience-curator` compatibility candidate.

The inventory inspected existing `~/.codex/skills`, bundled and curated Skill cache roots when present, and `~/.agents/skills` only if present. It checked global Codex instruction locations and the Academy worktree’s applicable ancestor chain. It did not read Plugin/MCP raw configuration, environment values, credentials, auth headers, or secrets. Credential presence for those uninspected config sources is `UNAVAILABLE`.

The active runtime exposed a metadata registry of 186 tools, including 172 MCP tools across 3 exposed MCP tool sources and 14 other runtime tools. This is the supported tool metadata surface available to this task, not a count of installed Plugins or all UI capabilities. A separate supported enumeration interface for the Plugin/Connector catalog was unavailable. The UI’s reported Skill count (including the mentioned 103) could not be independently verified; no browser scraping, OCR, hardcoding, or model-memory completion was used.

## Point-in-time measurements

See `results/host-capability-phase1-fixture.json` for the sanitized machine record. The local-only inventory with item metadata and local paths is outside Git.

| Measurement | Observed |
|---|---:|
| Skill files with valid metadata | 136 |
| `~/.codex/skills` | 110 |
| Bundled Skill cache | 2 |
| Curated Skill cache | 24 |
| `~/.agents/skills` | not present in inspected roots |
| Duplicate capability-name groups / exact duplicate Skills | 0 / 0 |
| Malformed frontmatter / unreadable-invalid files | 0 / 0 |
| Unknown declared Skill versions | 3 |
| Serialized metadata for the complete local registry | 110,866 bytes / 110,334 chars |
| Theoretical JSON-string array containing every full Skill instruction | 1,788,730 bytes / 1,778,546 chars |
| Representative `project-experience-curator` metadata record | 853 bytes |
| `project-experience-curator` full instruction file | 7,425 bytes |
| Global instruction source files observed | 1; 2,536 bytes / 998 chars |
| Project/subtree instruction source files in this worktree chain | 0 |

Filesystem discovery cannot establish that the global instruction is always loaded, so its load status is `UNKNOWN`. Project-chain load status is likewise not inferred from file presence. Tokens and cost are `UNAVAILABLE`; no chars-per-token estimate is reported. The 110,866-byte registry and the 1,788,730-byte theoretical full-instruction array demonstrate a footprint distinction, not actual model-context accounting.

Local Skill records are `HOST_PRESENT`; Host enabled status is `UNKNOWN` when not explicitly declared in safe metadata. Nexus lifecycle status remains `DISCOVERED` and evaluation status `UNEVALUATED`; no local Skill was labeled `ACTIVE` based on installation/presence alone. Metadata-only discovery marks instruction exposure as not loaded by this harness.

## Synthetic discovery tests

The tests create disposable synthetic roots and cover two roots, repeated names, exact duplicate content, malformed frontmatter, invalid UTF-8, disabled metadata, unknown versions, network/write/secret risk metadata, metadata-only output with body exclusion, and symlink/path-escape non-follow behavior (the symlink branch is simulated so it runs on Windows without symlink privileges). They also verify that UI/Plugin detail remains unavailable rather than guessed and that full-instruction presence does not imply exposure.

No real user Skill was modified. The synthetic suite records an environment-independent no-follow decision; the real Host scan itself does not traverse symlink directories.

## Legacy experience mechanism

`project-experience-curator` was found in the inspected roots. It remains a legacy experience mechanism, with Nexus status `DISCOVERED / UNEVALUATED`, not retired or migrated. A future compatibility relationship is only a candidate:

```text
project-experience-curator
→ potential Candidate producer / compatibility layer
```

No automatic experience import, Memory admission, or promotion occurred.

## Candidates and gaps

- **Capability Registry Candidate:** retain safe name/description/source/size/hash metadata separately from full instruction text. Not implemented in Core and not activated.
- **Startup Capability Discovery Candidate:** re-check supported roots at startup without auto-attaching instruction bodies. Not implemented.
- **Context Exposure Policy Candidate:** distinguish discoverable metadata from any explicit context exposure. Phase 0 structural baseline supports the distinction; behavioral value remains unavailable.
- **Capability Exposure Budget Candidate:** measure metadata/instruction exposure independently. No token budget can be calibrated from current telemetry.
- **Legacy Experience Integration Candidate:** evaluate a compatibility path for the legacy curator only after governance and provenance design. Not activated.

Known gaps: no supported Plugin catalog enumeration; no complete UI Skill catalog enumeration; no reliable host enabled/loaded state for filesystem Skills; no proof of which AGENTS instructions the Host actually loaded; no late invocation-only exposure event; no Host input/output token, provider, cost, or model identity telemetry; no behavior experiment isolating a real model under P0/P1/P2. A new capability is not evidence of improvement.

## Phase result

The supported local/runtime discovery surfaces are measurable without printing full Skill bodies or turning the full registry into model context. This is a registry/exposure structural baseline only. No model quality, productivity, capability usefulness, or production readiness conclusion is made. Phase 1 remains `IN PROGRESS` pending external review.
