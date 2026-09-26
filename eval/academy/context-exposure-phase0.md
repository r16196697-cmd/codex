# Bootstrap Academy — Context Exposure Phase 0

Status: `IN PROGRESS — Phase 0`
Baseline: `nexus-v0.1-audited` → `8c3451a937337d0cb3c58031eebb7afa8b7ef729`
Core independent audit: `CLOSED / PASS`
Production qualification: `NOT STARTED`

## Hypotheses

1. Nexus can keep durable history discoverable while a MODEL Run declares zero historical `context_object_refs`.
2. When a task needs history, an authorized, classification-compatible, admitted subset can be declared instead of exposing the whole corpus.
3. A broad but still eligible context set may add bytes and distractors; Phase 0 measures this without assuming it harms task quality.
4. Capability presence may affect otherwise unchanged task behavior. A late-invocation condition is useful only if actual exposure can be observed and recorded.

## Audited v0.1 surface used

- `MemoryService.search_raw` and `search_admitted` are present. Search is tied to a Run Grant, resource scope, Run data boundary, classification tags, expiry, payload availability, and active/partial purge barriers.
- `MemoryService.create_candidate` keeps unsupported or unapproved claims quarantined. T1 byte-integrity PASS is not treated as semantic support; this fixture uses T3 human/domain approval for admitted examples and leaves unapproved/conflicting examples `QUARANTINED`.
- `CodexHostedBridge.model_manifest` records actual host-declared `context_object_refs`, `execution_source=CODEX_HOST_DECLARED`, and `model_identity_status=UNAVAILABLE`.
- Hosted Bridge persists Task, Run, Manifest, route/attempt, budget, output object, T1 verification, and Trace through Core APIs. The Bridge records receipts; it does not invoke a model or isolate its context.
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

The runnable fixture is `scripts/eval/run_academy_phase0.py`. It refuses to overwrite a non-empty data root or journal and uses the repository's default fail-closed policy with only a synthetic local trust-anchor addition. It creates the Task/DAG through `CodexHostedBridge`, and creates synthetic Objects, classification assertions, raw rows, T3-approved admitted candidates, and T1-quarantined candidates through public Core APIs. It does not write business rows directly to SQLite.

The corpus contains 28 synthetic entries represented by 56 claim/evidence PUBLIC Objects: six admitted fact candidates (including a same-topic production distractor), two unapproved/conflicting quarantined candidates, and twenty unrelated entries. Raw retention is used only as a retrieval/discovery surface. Quarantined IDs are explicitly checked absent from admitted-search results and are never placed in declared context.

Five synthetic task probes are held constant across:

- `C0 — NONE`: no historical refs in MODEL `context_object_refs`.
- `C1 — MINIMAL RELEVANT`: only the four admitted objects needed by the fact/composition/distinction probes.
- `C2 — BROAD SAFE`: all six admitted objects; no unauthorized, quarantined, denied, barriered, or purged content.

Capability-presence probes hold a simple provided-text summary task constant:

- `P0 — ABSENT`: no capability object declared.
- `P1 — METADATA`: only the bounded read-only capability metadata object.
- `P2 — FULL INSTRUCTION`: the full synthetic instruction object.
- `P3 — INVOCATION`: `NOT RUN`; v0.1 has no late context-pack/exposure event to prove the exact time at which invocation-only material entered MODEL context. Do not infer this from a tool call alone.

## Measurements and result interpretation

The machine-readable run record is `results/context-exposure-phase0.json`. It records raw search candidates, admitted eligible candidates, declared exposed refs, serialized context bytes/chars, Run/Trace/budget counts, T1 verdicts, and safe exact replay counts. `model_input_tokens`, `model_output_tokens`, and `provider_cost` are `UNAVAILABLE`; no proxy estimate is substituted. Wall-clock is not reported as a model latency metric.

The stored C0/C1/C2 output payloads are explicitly `SYNTHETIC_FIXTURE_NOT_MODEL_INVOCATION`; they are deterministic harness fixtures, not measured Codex responses. The fixture-answer rubric is useful only to validate the measurement format. Model task success is `UNAVAILABLE`. Nexus T1 verification only establishes output object integrity; it does **not** certify semantic correctness. Host-declared output persistence is not evidence that the current Codex backend received only the manifest-declared context. The current Bridge does not invoke a model, and this single orchestration cannot provide a blinded/isolated behavioral A/B. Therefore C0/C1/C2 results establish Core-side retrieval eligibility and durable exposure declarations only—not model quality or overexposure effects.

The intended future comparison is `verified success + exposure size + retry/tool overhead + latency`; cost-per-verified-success remains unavailable until truthful Host telemetry exists. No aggregate score is computed here.

### Observed disposable-instance measurements

The synthetic Academy instance reached schema v22, Task `academy-context-phase0` and its root Run reached `SUCCEEDED`, and all six declared Hosted child Runs reached `SUCCEEDED`. The five task probes were represented by three C-condition runs; the three presence probes used separate runs. Across the instance there were 7 Runs, 42 Trace events, 6 reservations, and 14 Verification records (6 T3 admissions, 2 T1 integrity checks for quarantined candidates, and 6 T1 output-integrity checks). These statuses describe Nexus persistence/verification outcomes, not model-generated task success.

| Condition | Retrieved candidates | Eligible candidates | Declared context objects | Serialized context bytes/chars |
|---|---:|---:|---:|---:|
| C0 NONE | 0 | 0 | 0 | 2 / 2 (`[]`) |
| C1 MINIMAL RELEVANT | 10 | 4 | 4 | 390 / 390 |
| C2 BROAD SAFE | 14 | 6 | 6 | 581 / 581 |
| P0 ABSENT | 0 | 0 | 0 | 2 / 2 (`[]`) |
| P1 METADATA | 1 | 1 | 1 | 148 / 148 |
| P2 FULL INSTRUCTION | 1 | 1 | 1 | 265 / 265 |
| P3 INVOCATION | — | — | 0 | 2 / 2 (`[]`), not run |

The 6 admitted candidates were discoverable through authorized admitted search; 2 quarantined candidates remained absent from all selected exposure sets. A post-close/reopen `mode show` returned `NORMAL`. Replaying the same committed safe `put_object` command before and after reopen returned the original object ID; object/Run/reservation/Trace counts stayed at 79/7/6/42. Reusing that command with a changed payload returned `COMMAND_CONFLICT`. The journal remained at its verified empty head (sequence 0, zero hash), matching the persisted watermark. Scoped CLI `inspect task`, `inspect object`, and `inspect route` succeeded using the synthetic INSPECT-only grant `academy-inspect-grant`.

The 5/5 and 1/5 fixture rubric matches recorded in the JSON are computed from deterministic harness strings only. They are not observations about Codex behavior, and are intentionally excluded from model task-success claims.

## Candidate decisions (not production policy)

- `Context Strategy Candidate`: keep history discoverable by authorized retrieval and default MODEL context refs to empty; add only the minimal relevant, eligible refs required by a task. This is a design candidate supported by the Core's representable empty/non-empty manifest fields and exposure-size contrast, not a demonstrated task-success advantage or production policy change.
- Do not auto-select or auto-promote. Collect repeated, context-isolated Host observations first.
- `P3 Invocation` and behavioral presence effects remain unresolved pending Host instrumentation or an explicit exposure event.

## Limitations / safety checks

- This synthetic probe is small, deterministic, and not statistically powered.
- No provider/model identity or token/cost telemetry is available; model identity stays `UNAVAILABLE`.
- No private/user data, secrets, network fetch, external-write Effect, policy broadening, or Academy automation is used.
- The root/policy/journal are disposable and are not Git artifacts.
- C0 attaches Nexus governance while declaring zero historical context: attached Nexus is not equivalent to loaded history.
