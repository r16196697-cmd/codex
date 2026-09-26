# Bootstrap Academy Qualification Synthesis — Phases 0–4

## Status and boundary

Core Independent Audit: `CLOSED / PASS`\
Audited baseline: `nexus-v0.1-audited` → `8c3451a937337d0cb3c58031eebb7afa8b7ef729`\
Bootstrap Academy: `IN PROGRESS — Phase 4` (external review pending)\
Capability Certification: `NOT STARTED`\
Shadow: `NOT STARTED`\
Production qualification: `NOT STARTED`

This synthesis is Academy-only evidence and candidate tracking. It does not change Core behavior, policy, capability status, Host configuration, or the audited baseline.

## Phase evidence summary

| Phase | Question / evidence | Qualified scope | Not qualified | External review |
|---|---|---|---|---|
| 0 — Context Exposure | Structural synthetic protocol and proposed-context records; no synthetic MODEL receipt | Registry/retrieval/exposure sets and byte/character measurements can be represented without claiming a MODEL saw them | Behavioral context effects, answer quality, P3 invocation-only presence | CLOSED / ACCEPTED |
| 1 — Host & Capability Discovery | Read-only local discovery metadata plus explicitly caller-observed runtime tool metadata | Bounded local roots and sanitized aggregate inventory; metadata footprint is distinct from model context | UI catalog enumeration, complete Plugin/MCP visibility, actual enabled/loaded state, capability usefulness | CLOSED / ACCEPTED |
| 2 — Search / Evidence | Frozen synthetic corpus/query retrieval metrics and evidence qualification | Audited raw/admitted APIs, deterministic eligibility, synthetic conflict/insufficiency semantics | MODEL answer quality; live Search replay by committed harness; query-normalization relevance gain | CLOSED / ACCEPTED |
| 3 — Failure Recovery | `REGRESSION_BACKED_OPERATIONAL_QUALIFICATION`; 27 case mappings over 23 unique regression tests | Tested transaction/restart/replay behavior and synthetic failure paths on this Windows environment | Independent black-box resilience campaign, real OS crash, production recovery, all-platform physical durability | CLOSED / ACCEPTED |
| 4 — Routing Semantics | Frozen synthetic deterministic routing matrix; 18 cases mapped to 7 unique tests | Selected synthetic profile semantics, tested constraints, attempt/run/reservation separation, replay/conflict, selected denial paths | Real provider/model selection, comparative model quality/cost, several constraint/fallback cases, live MODEL execution | IN PROGRESS / external review pending |

Phase 3 count correction: the historic `165 tests total, 163 passed + 2 skipped` was the actual prior suite result. A previous summary saying `165 passed + 2 skipped` mislabeled the total as passes; it was not a separate run. This batch re-runs the full suite and records its exact new discovered/passed/failed/error/skipped counts in the final execution report and commit result.

Phase 1 provenance remains bounded: the 186 runtime tools / 172 MCP tools / 3 MCP sources were caller-observed active runtime tool metadata, not enumerated by the committed discovery harness. The UI catalog count cannot be independently verified through the committed supported interface. The 136 discovered local Skills remain `DISCOVERED / UNEVALUATED`; discovered filesystem presence is not proof of host enabled, loaded, or useful status.

## Candidate ledger

The machine-readable ledger is [bootstrap-academy-candidates.json](results/bootstrap-academy-candidates.json). Every entry has `promotion_allowed: false`. Candidate states are Academy documentation labels, not Core lifecycle enums. Evidence does not automatically promote any capability.

| Candidate | Current evidence state | Main missing evidence |
|---|---|---|
| Context Strategy | STRUCTURALLY_SUPPORTED | Controlled real MODEL exposure and behavioral comparison |
| Capability Registry | OBSERVED | Complete supported Host enumeration and utility evaluation |
| Startup Capability Discovery | OBSERVED | Host lifecycle/load observability and behavioral benefit |
| Context Exposure Policy | BEHAVIORALLY_UNTESTED | Exact model-visible context and controlled A/B |
| Capability Exposure Budget | INSUFFICIENT_EVIDENCE | Reliable token telemetry and behavior/cost outcomes |
| Legacy Experience Integration | DEFERRED | Compatibility evaluation; curator remains unchanged |
| Memory Retrieval Strategy | STRUCTURALLY_SUPPORTED | Broader independently frozen corpus and real task behavior |
| Query Normalization | INSUFFICIENT_EVIDENCE | Parser error reduction exists; retrieval relevance gain is unproven |
| Evidence Pack | STRUCTURALLY_SUPPORTED | Exact Host exposure and behavioral utility |
| Source/Freshness Policy | INSUFFICIENT_EVIDENCE | More independently sourced, time-aware adjudication cases |
| Conflict Handling | STRUCTURALLY_SUPPORTED | Wider real-world source behavior; no automatic truth claim |
| Failure Fixture | STRUCTURALLY_SUPPORTED | Operational ingestion/governance process remains a candidate |
| Recovery Qualification | STRUCTURALLY_SUPPORTED | Real OS crash and production operations qualification |
| Routing Policy | STRUCTURALLY_SUPPORTED (synthetic subset only) | Live provider facts, untested constraints, behavioral comparison |
| Escalation/Fallback | INSUFFICIENT_EVIDENCE | Fallback is not a distinct tested policy; inconclusive retry not represented |

## Capability certification gate

The 136 discovered Skills, Plugins/MCP/runtime capability surfaces, and `project-experience-curator` do not have evidence sufficient for `EVALUATED`, `SHADOW`, or `ACTIVE` certification. Skill discovery or host enablement does not establish utility. `project-experience-curator` remains `DISCOVERED / UNEVALUATED`, unchanged; its possible Candidate-producer/compatibility-layer relation remains untested.

## Shadow Entry Gate Candidate

This is a gate candidate only; no Shadow entry is authorized by this artifact.

| Gate | Evidence state | Interpretation |
|---|---|---|
| A. Core audited/frozen | SUPPORTED | Frozen audited baseline remains unchanged |
| B. Structural context isolation | SUPPORTED | Phase 0 structural evidence only |
| C. Capability inventory | SUPPORTED (scoped) | Local discovery plus caller-observed tool metadata; incomplete Host catalog |
| D. Deterministic retrieval/evidence | SUPPORTED (synthetic scope) | Phase 2 fixed synthetic qualification |
| E. Failure recovery | SUPPORTED (regression-backed scope) | Not black-box, OS-crash, production, or physical-durability qualification |
| F. Routing semantics | SUPPORTED (synthetic subset) | Not real provider/model routing |
| G. Exact real MODEL context exposure | UNAVAILABLE | No independently controlled invocation/visibility proof |
| H. Skill/capability behavioral A/B | NOT TESTED | No utility certification |
| I. Host Memory contamination | UNAVAILABLE | Tool-chat memory is forced on; cross-session effect with primary Memory off is unverified |
| J. Real token/cost telemetry | UNAVAILABLE | No reliable Host telemetry |
| K. Production/private-data exclusion | SUPPORTED for Academy runs | No production qualification; keep future data boundary explicit |
| L. No auto-promotion | SUPPORTED | No candidate is activated or promoted |

For claims that context or Skills improve model behavior, gates G and H are essential evidence, not optional proxies. Real dollar-cost telemetry may be unnecessary for a narrowly read-only Shadow, but no cost claim can be made without it. The current evidence does not qualify a Shadow transition.

## Frozen Host environment and unresolved gaps

The Host configuration remains unchanged: primary Memory `OFF`; tool-chat memory control `ON_FORCED / USER_NOT_CONTROLLABLE`; cross-session effect `NOT INDEPENDENTLY VERIFIED`; Custom Instructions on and unchanged; global AGENTS unchanged; `project-experience-curator` unchanged. No Phase 4 behavioral evaluation uses Host native Memory.

Known blockers/gaps remain: no independently controlled MODEL invocation per condition; no proof of exact model-visible context; P3 invocation-only presence unobserved; no Presence Regression evaluator; no reliable Host token/cost/model telemetry; Host tool-chat memory cross-session behavior unverified; filesystem Skill presence is not enabled/loaded proof; capability usefulness unevaluated; query normalization parser benefit does not yet establish relevance benefit; real multi-provider routing is not implemented; the Phase 4 untested/not-representable cases remain explicit in its result.

No Academy PASS, capability certification, Shadow readiness, or Production readiness is asserted. Await external review before any next phase.
