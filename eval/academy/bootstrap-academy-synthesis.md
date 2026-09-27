# Bootstrap Academy Qualification Synthesis — Phases 0–6

## Status and boundary

Core Independent Audit: `CLOSED / PASS`\
Audited baseline: `nexus-v0.1-audited` → `8c3451a937337d0cb3c58031eebb7afa8b7ef729`\
Bootstrap Academy: `IN PROGRESS — Phase 6 formal matrix authorized; external execution pending`\
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
| 4 — Routing Semantics | Frozen synthetic deterministic routing matrix; 18 cases mapped to 7 unique tests | Selected synthetic profile semantics, tested constraints, attempt/run/reservation separation, replay/conflict, selected denial paths | Real provider/model selection, comparative model quality/cost, several constraint/fallback cases, live MODEL execution | CLOSED / ACCEPTED |
| 5 — Controlled Host Behavioral Bridge / Certification Preflight | First and only external PowerShell matrix completed: 18/18 eligible trials, 12 Context + 6 Presence; V2 incident and V3/external diagnostics preserved separately | Descriptive frozen-evaluator outcomes; external review accepted the bounded evidence scope | Causal exposure effects, full model-visible context, real Skill A/B, P3 invocation-only, provider/model identity, and dollar cost remain unverified | CLOSED / ACCEPTED |
| 6 — Presence Regression Qualification + Capability Certification Gate | Accepted V2; 4-family synthetic matrix externally authorized; no Host trials | Frozen packets, evaluator, semantic-monotonic order, separate relevant-utility/control-preservation outcomes, and once-only external adapter | Replicated Host behavior, real Skill instruction-artifact evaluation, P3 invocation-only, and native loaded-state proof | IN PROGRESS / FORMAL MATRIX AUTHORIZED — EXTERNAL EXECUTION PENDING |

Phase 3 count correction: the historic `165 tests total, 163 passed + 2 skipped` was the actual prior suite result. A previous summary saying `165 passed + 2 skipped` mislabeled the total as passes; it was not a separate run. In this matrix-results update, the full suite again reported 165 total, 163 passed, 0 failed, 0 errors, and 2 skipped; Academy tests are reported separately.

Phase 1 provenance remains bounded: the 186 runtime tools / 172 MCP tools / 3 MCP sources were caller-observed active runtime tool metadata, not enumerated by the committed discovery harness. The UI catalog count cannot be independently verified through the committed supported interface. The 136 discovered local Skills remain `DISCOVERED / UNEVALUATED`; discovered filesystem presence is not proof of host enabled, loaded, or useful status.

Phase 4 closure is `CLOSED / ACCEPTED` with its accepted scope unchanged: 12 mapped supported, 2 `NOT_REPRESENTABLE`, 4 `NOT_TESTED`; synthetic routing only; real provider/model routing remains `NOT TESTED / NOT IMPLEMENTED`.

Phase 5 retained the frozen child invocation `PHASE5_OFFICIAL_HOST_INVOCATION_V1`: `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -`. V1 was replaced before any formal behavioral trial. V2's one A17 attempt remains `INVOCATION_PROVENANCE_CONFLICT` and invalid as behavioral evidence because the committed V2 runner does not contain the reported argument and exact per-trial argv was not independently persisted. V3's canonical argv hardening and its internal pre-thread Host state-access failure remain in history. The separate external ordinary PowerShell liveness diagnostic succeeded but was non-behavioral and created no MODEL receipt. `NESTED_OUTER_SANDBOX_CONFOUND = SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC`, not proven as a sole root cause because parent-process environments differ. The external Windows PowerShell path completed the first and only eligible formal matrix: 18 attempted, 18 eligible, 12 Context + 6 Presence, 18 unique threads, zero duplicates, zero tool contamination, and zero incomplete/pre-model/timeouts. Trial-level outputs and provenance remain preserved in `behavioral-phase5.json`; deterministic frozen-evaluator aggregates are stored separately under `formal_matrix_result`. Historical incidents/diagnostics are excluded from those aggregates. External review set Phase 5 to `CLOSED / ACCEPTED`, scoped to these 18/18 eligible trials and deterministic scoring; acceptance does not mean all behavioral outcomes passed. No `CodexHostedBridge` MODEL Run or Core receipt was fabricated.

Descriptively, Context exact-correct counts were C0 4/4, C1 4/4, and C2 3/4; mean required-fact coverage was 1.000, 1.000, and 0.917 respectively. Presence exact outcomes were P0 2/2, P1 2/2, and P2 1/2; the unrelated legacy task was correct in P0 and P1 and incorrect in P2. The pilot had 2 unsupported assertions, 0 distractor adoptions, and correct conflict handling in 3/3 conflicting-source trials. External review classified L73 as `EXACT_CANONICALIZATION_MISMATCH` without changing its frozen score, and W34 as `INSTRUCTION_BLEED_OBSERVATION / PRESENCE_REGRESSION_OBSERVED`; W34's output RPAL-632 is the frozen Glyph Shift transform of KITE-309. These are descriptive pilot observations, not causal findings; cross-session Host Memory contamination remains uncharacterized.

## Candidate ledger

The machine-readable ledger is [bootstrap-academy-candidates.json](results/bootstrap-academy-candidates.json). Every entry has `promotion_allowed: false`. Candidate states are Academy documentation labels, not Core lifecycle enums. Evidence does not automatically promote any capability.

| Candidate | Current evidence state | Main missing evidence |
|---|---|---|
| Context Strategy | OBSERVED IN REAL HOST PILOT; CAUSAL EFFECT UNCHARACTERIZED | Independent replication, full model-visible context, and Host Memory contamination characterization |
| Capability Registry | OBSERVED | Complete supported Host enumeration and utility evaluation |
| Startup Capability Discovery | OBSERVED | Host lifecycle/load observability and behavioral benefit |
| Context Exposure Policy | OBSERVED IN REAL HOST PILOT; CAUSAL EFFECT UNCHARACTERIZED | Full model-visible context and independently verified cross-session Memory effects |
| Capability Exposure Budget | INSUFFICIENT_EVIDENCE | Per-exposure token attribution plus behavioral/cost outcome evidence |
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
| Synthetic Capability Presence | OBSERVED IN REAL HOST PILOT; CAUSAL EFFECT UNCHARACTERIZED | P3 invocation-only visibility, independent replication, and real Skill utility |
| Presence Regression Gate | CANDIDATE / PREREGISTRATION_REQUIRED | Independent replicated capability families, real Skill instruction-artifact evaluation, P3 invocation-only observability |
| Output Canonicalization Policy | INSUFFICIENT_EVIDENCE | Independent structured-output cases; L73 remains an exact canonicalization mismatch and is not rescored |

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
| G1. Explicit experiment packet delivery | SUPPORTED BY FORMAL REAL HOST MATRIX | All 18 eligible formal trials completed with frozen child invocation provenance |
| G2. Full model-visible context proof | UNAVAILABLE | Ambient Host context is held constant but only partially observable |
| H1. Synthetic capability presence behavior | OBSERVED IN REAL HOST PILOT | Synthetic Glyph Shift only; this does not certify any real Skill |
| H2. Real Skill capability utility | NOT TESTED | No real Skill was evaluated |
| I. Host Memory contamination | UNAVAILABLE / NOT INDEPENDENTLY VERIFIED | Tool-chat memory is forced on; cross-session effect with primary Memory off is unverified |
| J1. Host total-turn token telemetry | SUPPORTED BY REAL HOST | Host reports input, cached input, output, and reasoning output token totals |
| J2. Provider dollar cost | UNAVAILABLE | Billable dollar cost and Host model identity remain unavailable |
| K. Production/private-data exclusion | SUPPORTED for Academy runs | No production qualification; keep future data boundary explicit |
| L. No auto-promotion | SUPPORTED | No candidate is activated or promoted |

For claims that context or Skills improve model behavior, gates G1, G2, and H are essential evidence, not optional proxies. Real dollar-cost telemetry may be unnecessary for a narrowly read-only Shadow, but no cost claim can be made without it. The current evidence does not qualify a Shadow transition.

## Frozen Host environment and unresolved gaps

The Host configuration remains unchanged: primary Memory `OFF`; tool-chat memory control `ON_FORCED / USER_NOT_CONTROLLABLE`; cross-session effect `NOT INDEPENDENTLY VERIFIED`; Custom Instructions on and unchanged; global AGENTS unchanged; `project-experience-curator` unchanged. No Phase 4 behavioral evaluation uses Host native Memory.

Known blockers/gaps remain: no independently controlled MODEL invocation per condition; no proof of exact model-visible context; P3 invocation-only presence unobserved; Phase 6 formal matrix is authorized but not executed; per-exposure token attribution, provider dollar cost, and Host model identity remain unavailable; Host tool-chat memory cross-session behavior is unverified and `CROSS_SESSION_MEMORY_CONFOUND` is `UNCHARACTERIZED`; filesystem Skill presence is not enabled/loaded proof; capability usefulness unevaluated; query normalization parser benefit does not yet establish relevance benefit; real multi-provider routing is not implemented; the Phase 4 untested/not-representable cases remain explicit in its result.

Phase 5's synthetic presence pilot completed; no real installed Skill has been evaluated. Phase 6 V2 includes a reproducible, read-only static artifact inventory for 136 discovered Skill instruction artifacts (110 Codex Skills, 2 bundled-plugin Skills, 24 curated-plugin Skills). The ledger hashes each artifact without storing paths or instruction text; most semantic criteria remain `UNKNOWN` and block qualification. Zero selected means zero statically qualified under current known/unknown evidence, not zero Skills are suitable. This is not runtime safety verification. `project-experience-curator` has a known filesystem-mutation requirement and remains `DISCOVERED / UNEVALUATED`. Phase 6 uses four task-scoped synthetic capability families with 24 packets and 12 relevant/control pairs; all 24 model-visible texts are unchanged from V1. Incorrect P0 controls are classified as baseline failures, while incorrect P1/P2 controls are presence regressions. The Phase 6 adapter reuses Phase 5's frozen argv/cwd/stdin/event/capture machinery, refuses a second execution, and is authorized for external Windows PowerShell only. Formal count and MODEL receipts remain zero. Native Host Skill loaded-state evidence remains separate from any future explicit instruction-artifact evaluation.

Phase 5 is `CLOSED / ACCEPTED`; Phase 6 is `IN PROGRESS / FORMAL MATRIX AUTHORIZED — EXTERNAL EXECUTION PENDING`. No Academy-wide PASS, Capability Certification, Shadow readiness, or Production readiness is asserted. Capability Certification, Shadow, and Production remain `NOT STARTED`.
