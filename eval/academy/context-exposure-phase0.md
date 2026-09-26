# Bootstrap Academy — Context Exposure Phase 0

Status: `STRUCTURAL BASELINE SUPPORTED — CORRECTIVE RECORDED`
Baseline: `nexus-v0.1-audited` → `8c3451a937337d0cb3c58031eebb7afa8b7ef729`
Core independent audit: `CLOSED / PASS`
Behavioral qualification: `UNAVAILABLE / NOT YET TESTED`
Production qualification: `NOT STARTED`

## Hypotheses

1. Nexus can keep durable history discoverable while an Academy-only proposal declares zero historical context refs.
2. When a task needs history, an authorized, classification-compatible, admitted subset can be represented as a proposed set without asserting that it was loaded.
3. A broad but still eligible proposed set can be measured for size, without assuming it harms task quality.
4. Capability presence may affect otherwise unchanged task behavior, but Phase 0 has no valid invocation-only exposure primitive.

## Corrective record after the first Phase 0 commit

The initial Phase 0 commit `1d7743a83c22a86b3fb71cd0880ab692b67b6a74` persisted synthetic conditions as Hosted MODEL Runs even though it labeled their payloads synthetic and model success unavailable. That still looked like a real Host model execution receipt and was semantically incorrect. The first commit is preserved as historical evidence; this later corrective run uses a fresh disposable root and creates **no MODEL Runs, no Hosted MODEL receipts, no bound proposed manifests, and no model outputs**. C0/C1/C2 and P0/P1/P2 are now only experiment records with `PROPOSED_MODEL_CONTEXT_NOT_EXECUTED` status.

## Audited v0.1 surface used

- `MemoryService.search_raw` and `search_admitted` are present. Search is tied to a Run Grant, resource scope, Run data boundary, classification tags, expiry, payload availability, and active/partial purge barriers.
- `MemoryService.create_candidate` keeps unsupported or unapproved claims quarantined. T1 byte-integrity PASS is not treated as semantic support; this fixture uses T3 human/domain approval for admitted examples and leaves unapproved/conflicting examples `QUARANTINED`.
- `CodexHostedBridge.model_manifest` builds metadata for a real attached-host MODEL Run. It does not invoke or isolate a model. The corrected fixture does not bind its proposed context sets as MODEL manifests.
- The corrective fixture uses public Core APIs for a Task/root ORCHESTRATOR, Objects, T3 admissions, T1 integrity checks, Memory search, Trace, and replay. It creates no child Run, route, budget reservation, or hosted output receipt.
- Authority and classification checks remain enabled; all fixture objects are synthetic `PUBLIC`.

## Not implemented / Academy candidates

- Context Exposure Policy: `NOT IMPLEMENTED / CANDIDATE`.
- Capability Exposure Budget: `NOT IMPLEMENTED / CANDIDATE`.
- Presence Regression evaluator: `NOT IMPLEMENTED / CANDIDATE`.
- Automatic context selector: `NOT IMPLEMENTED / CANDIDATE`.
- Host-observed token accounting: `NOT IMPLEMENTED / UNAVAILABLE`.
- Context-pack object/schema and late context-exposure event: `NOT IMPLEMENTED / ACADEMY GAP`.
- Independently controlled Codex model invocation by condition: `NOT AVAILABLE` through the current Attached Bridge.

## Protocol and conditions

The runnable fixture is `scripts/eval/run_academy_phase0.py`. It refuses to overwrite a non-empty data root or journal and uses the repository's default fail-closed policy with only a synthetic local trust-anchor addition. It creates the Task/root lifecycle through `CodexHostedBridge` with an empty DAG, then creates synthetic Objects, classification assertions, raw rows, T3-approved admitted candidates, and T1-quarantined candidates through public Core APIs. It does not write business rows directly to SQLite.

The corpus contains 28 synthetic entries represented by 56 claim/evidence PUBLIC Objects: six admitted fact candidates (including a same-topic production distractor), two unapproved/conflicting quarantined candidates, and twenty unrelated entries. Raw retention is used only as a retrieval/discovery surface. Quarantined IDs are explicitly checked absent from admitted-search results and are never placed in declared context.

Five synthetic task probes define proposed sets only; they were not sent to a model:

- `C0 — NONE`: proposed set has no historical refs.
- `C1 — MINIMAL RELEVANT`: proposed set contains four admitted objects chosen for the fact/composition/distinction probes.
- `C2 — BROAD SAFE`: proposed set contains all six admitted objects; no unauthorized, quarantined, denied, barriered, or purged content.

Capability-presence proposals hold a simple synthetic task prompt constant; they were not sent to a model:

- `P0 — ABSENT`: no capability object declared.
- `P1 — METADATA`: only the bounded read-only capability metadata object.
- `P2 — FULL INSTRUCTION`: the full synthetic instruction object.
- `P3 — INVOCATION`: `NOT RUN`; v0.1 has no late context-pack/exposure event to prove the exact time at which invocation-only material entered MODEL context. Do not infer this from a tool call alone.

## Measurements and result interpretation

The machine-readable run record is `results/context-exposure-phase0.json`. It records raw search candidates, admitted eligible candidates, proposed context refs, serialized proposal bytes/chars, Core Run/Trace/budget counts, T1/T3 facts, and safe exact replay counts. `model_input_tokens`, `model_output_tokens`, and `provider_cost` are `UNAVAILABLE`; no proxy estimate is substituted. Wall-clock is not reported as a model latency metric.

No output payload or output verification was created. `model_task_success` and behavioral overexposure effect are `UNAVAILABLE / NOT YET TESTED`. Two T1 PASS records validate the quarantined synthetic claim/evidence object integrity; six T3 PASS records support admitted candidate facts. T1 is not semantic verification. No claim is made that Codex received any proposed context set.

The intended future comparison is `verified success + exposure size + retry/tool overhead + latency`; cost-per-verified-success remains unavailable until truthful Host telemetry exists. No aggregate score is computed here.

### Observed disposable-instance measurements

The corrected synthetic Academy instance reached schema v22. Task `academy-context-phase0` and its root ORCHESTRATOR Run reached `SUCCEEDED`. It has 1 Run (ORCHESTRATOR), 0 MODEL Runs, 0 route decisions, 0 budget reservations, 6 Trace events, and 8 Verification records: 6 T3 admission facts and 2 T1 integrity checks for quarantined candidates. These are Nexus state facts, not model-generated task success.

| Proposal | Retrieved candidates | Eligible candidates | Proposed refs | Serialized proposal bytes/chars |
|---|---:|---:|---:|---:|
| C0 NONE | 0 | 0 | 0 | 2 / 2 (`[]`) |
| C1 MINIMAL RELEVANT | 10 | 4 | 4 | 390 / 390 |
| C2 BROAD SAFE | 14 | 6 | 6 | 581 / 581 |
| P0 ABSENT | 0 | 0 | 0 | 2 / 2 (`[]`) |
| P1 METADATA | 1 | 1 | 1 | 148 / 148 |
| P2 FULL INSTRUCTION | 1 | 1 | 1 | 265 / 265 |
| P3 INVOCATION | — | — | 0 | 2 / 2 (`[]`), not run |

The 6 admitted candidates were discoverable through authorized admitted search; 2 quarantined candidates remained absent from all proposed sets. The proposal record is Academy-only and is not bound to a MODEL Run manifest. An exact safe `put_object` replay returned the original object ID with object/Run/reservation/Trace counts unchanged; the same command with changed payload returned `COMMAND_CONFLICT`. After closing and reopening the same instance, exact replay again returned the original object ID; counts remained 61 Objects, 1 Run (0 MODEL), 0 reservations, 6 Trace events, and 8 Verification rows. The empty journal watermark remained sequence 0 with the zero hash. Host model identity, token/cost telemetry, task success, and overexposure effect remain unavailable.

## Candidate decisions (not production policy)

- `Context Strategy Candidate`: keep history discoverable by authorized retrieval and default to no exposed history; if a real task later needs it, consider only minimal eligible refs. This is supported only as a structural proposal with measurable size contrast, not as a demonstrated behavior advantage or production policy change.
- Do not auto-select or auto-promote. Collect repeated, context-isolated Host observations first.
- `P3 Invocation` and behavioral presence effects remain unresolved pending Host instrumentation or an explicit exposure event.

## Limitations / safety checks

- This synthetic probe is small, deterministic, and not statistically powered.
- No provider/model identity or token/cost telemetry is available; model identity stays `UNAVAILABLE`.
- Phase 0 structural baseline: `SUPPORTED`. Behavioral Context Strategy qualification: `UNAVAILABLE / NOT YET TESTED`.
- No private/user data, secrets, network fetch, external-write Effect, policy broadening, or Academy automation is used.
- The root/policy/journal are disposable and are not Git artifacts.
- C0 attaches Nexus governance while declaring zero historical context: attached Nexus is not equivalent to loaded history.
