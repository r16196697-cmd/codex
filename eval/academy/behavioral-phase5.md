# Phase 5 — Controlled Host Behavioral Bridge + Certification Preflight

- Status: `CLOSED / ACCEPTED`
- Core Independent Audit: `CLOSED / PASS`
- Capability Certification: `NOT STARTED`
- Shadow: `NOT STARTED`
- Production qualification: `NOT STARTED`

## Host execution preflight

The frozen official child invocation remains `PHASE5_OFFICIAL_HOST_INVOCATION_V1`: `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -`, stdin only, no resume, a fresh thread, and a fresh empty temporary cwd outside the repository with no evaluator or ground-truth files. `--ask-for-approval` is not part of the invocation. V2's first A17 attempt is retained as an execution incident, but its formal behavioral evidence is invalid because the committed V2 runner blob does not contain the reported argument and exact per-trial argv was not independently persisted. V2 remains `STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT`, with one attempted packet and zero eligible trials. V3 centralizes argv construction and persists sanitized argv/hash before process outcome. The Codex Agent nested execution path remains `BLOCKED_BY_HOST_STATE_ACCESS`.

An external operator's ordinary Windows PowerShell diagnostic using Codex CLI `0.158.0-alpha.2.1`, the frozen child argument semantics, a fresh empty cwd outside the repository, and stdin `Return exactly: P5_OUTER_HOST_OK` exited 0; `thread.started` and `turn.completed` were observed, the output was `P5_OUTER_HOST_OK`, and tool-call count was 0. Thread ID: `01a0e1ed-4206-7671-98ba-431aac9ef614`. Host-reported total-turn telemetry was input 20481, cached input 7936, cache-write input 0, output 10, reasoning output 0. It is `NON_BEHAVIORAL_EXTERNAL_HOST_DIAGNOSTIC`, not a packet trial, and created no MODEL receipt. `NESTED_OUTER_SANDBOX_CONFOUND = SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC`; this is not proof of a sole root cause because parent-process environments differ. The formal controller was `EXTERNAL_WINDOWS_POWERSHELL`; the external operator completed the first and only eligible formal matrix execution from ordinary PowerShell. The outer controller did not remove the child `--sandbox read-only` restriction. Exposure-component attribution, provider dollar cost, and Host model identity remain unavailable.

The harness hashes and records the exact user packet sent on stdin, its UTF-8 byte and character sizes, and the observed final Host message. This supports exact **explicit packet** provenance. The complete ambient system/custom-instruction/skill context is not attested or fully enumerable by this CLI interface; no claim is made that the packet hash represents every token visible to the model. Ambient Host settings were not changed.

This harness did not invoke `CodexHostedBridge` and did not create a Core MODEL Run or Hosted MODEL receipt. Therefore `nexus_model_receipt_count` remains zero; external Host trials are not represented as Nexus receipts.

## Frozen Host baseline

- Primary Memory: `OFF`
- Tool-chat memory: `ON_FORCED / USER_NOT_CONTROLLABLE`
- Cross-session effect: `NOT INDEPENDENTLY VERIFIED`
- Custom Instructions: `ON / UNCHANGED`
- Global AGENTS: `UNCHANGED`
- `project-experience-curator`: `DISCOVERED / UNEVALUATED / UNCHANGED`
- No Skill/plugin/MCP installation, removal, permission change, or bulk toggle

This is `HOST_BASELINE_V1`. Host-native Memory does not determine behavioral success.

## Frozen packet and blind evaluator

The 18 blind packets and their SHA-256 are in `fixtures/behavioral-phase5-packets.json`; expected outputs, condition mapping, and deterministic scoring are isolated in `fixtures/behavioral-phase5-evaluator.json`, which is never supplied to the Host. Four context families each have no synthetic records, minimum relevant records, and broader safe records. They cover a nonce fact, multi-fact composition, a similar identifier, and a two-source conflict. C0 answers for hidden facts are expected to be `UNKNOWN`, not a guess. V2 freezes an interleaved order that keeps each semantic family's exposure monotonic: C0 before C1 before C2, and each presence task's P0 before P1 before P2. V1 was replaced before any formal behavioral trial.

The fixed execution order was `A17, E04, V06, F22, B29, H11, T63, D31, R41, G08, K02, J15, U07, L73, M26, S12, N58, W34`.

The separate presence pilot uses the Academy-only `Glyph Shift` capability at absent, metadata-only, and full-instruction presence, plus the same unrelated legacy task under those three conditions. P3 invocation-only is `NOT TESTED / HOST OBSERVABILITY GAP`; no invocation transition is simulated. Packets contain no condition labels, relevance tags, expected answers, or scoring criteria.

The first and only formal execution completed as `HOST_TRIALS_COMPLETED`: 18 attempted and eligible trials, 12 Context and 6 Presence, with 18 unique thread IDs, no duplicates, no tool contamination, and no pre-model failure, incomplete turn, or timeout. The 18 trial records remain in `context_trials` and `presence_trials`; a separate deterministic `formal_matrix_result` contains aggregates derived from those unchanged observations and the frozen evaluator. Historical V2/V3 incidents and liveness diagnostics remain separate and are not part of the 18-trial sample. No manual packet reruns or retries were performed.

The matrix is closed to further observation. Do not copy, paste, or manually rerun any packet, and do not invoke the Host runner again. The result records the first and only formal execution: 18 attempted and eligible trials. The frozen evaluator scores format validity, exact answers, required-field coverage, unsupported assertions, distractor adoption, and conflict handling.

## External review closure

External review accepted Phase 5 with evidence scope limited to 18/18 eligible formal real-Host trials scored by the deterministic frozen evaluator. Acceptance closes the phase; it does not mean every outcome passed or establish Academy-wide success.

- L73 is classified `EXACT_CANONICALIZATION_MISMATCH`. Its frozen output was `{"retention_days":"23 days","handoff":"MIRA-Q8","region":"LCL-6"}` against frozen expected `{"retention_days":"23","handoff":"MIRA-Q8","region":"LCL-6"}`. The source fact remained 23 days; no distractor or wrong workspace value was adopted. The frozen score remains exact incorrect, 2/3 required-fact coverage, and one unsupported assertion. This qualitative label does not change scoring.
- W34 returned `{"owner":"RPAL-632"}` for the frozen owner `KITE-309`. `RPAL-632` is the result of applying the frozen Glyph Shift full instruction to `KITE-309`; this is classified `INSTRUCTION_BLEED_OBSERVATION / PRESENCE_REGRESSION_OBSERVED` because the unrelated legacy task required returning the record value. It was observed in this real-Host pilot; no universal or causal claim is made while cross-session Memory confounding remains uncharacterized.

## Measurements and outcomes

The machine-readable results artifact records one row per trial: explicit task and packet hashes, packet bytes/chars, real Host completion flag, output hash/text, observed tool calls, Host-reported total-turn input/cached-input/output/reasoning-output token telemetry, and local wall-clock duration. Host total-turn token telemetry is observable; per-exposure token attribution, provider dollar cost, Host model identity, provider request ID, automatic retry count, and complete implicit context remain unavailable. No packet, Skill, AGENTS, or ambient token count and no billable dollar cost are inferred. Ambient input composition is `UNAVAILABLE / NOT DECOMPOSABLE`.

The runner writes raw captures only to a temporary directory outside the repository. Persisted Academy results omit the absolute Codex executable and capture-directory paths; capture filenames remain basenames only.

### Formal matrix descriptive results

These are descriptive scores from the frozen evaluator for this single ordered real-Host pilot. `CROSS_SESSION_MEMORY_CONFOUND = UNCHARACTERIZED` and `HOST_MEMORY_CONTAMINATION = UNAVAILABLE / NOT INDEPENDENTLY VERIFIED`; no condition is claimed to have caused an outcome.

| Context condition | Trials | Exact correct | Format valid | Mean required-fact coverage | Unsupported assertions | Distractor adoptions |
|---|---:|---:|---:|---:|---:|---:|
| C0_NONE | 4 | 4 | 4 | 1.000 | 0 | 0 |
| C1_MINIMAL_RELEVANT | 4 | 4 | 4 | 1.000 | 0 | 0 |
| C2_BROAD_SAFE | 4 | 3 | 4 | 0.917 | 1 | 0 |

| Context family | Trial outcomes (ID: exact / format / coverage / unsupported / distractor) |
|---|---|
| single_fact | A17: true / true / 1.000 / 0 / 0; B29: true / true / 1.000 / 0 / 0; K02: true / true / 1.000 / 0 / 0 |
| multi_fact_composition | E04: true / true / 1.000 / 0 / 0; D31: true / true / 1.000 / 0 / 0; L73: false / true / 0.667 / 1 / 0 |
| similar_identifier | F22: true / true / 1.000 / 0 / 0; G08: true / true / 1.000 / 0 / 0; M26: true / true / 1.000 / 0 / 0 |
| conflicting_sources | H11: true / true / 1.000 / 0 / 0; J15: true / true / 1.000 / 0 / 0; N58: true / true / 1.000 / 0 / 0; conflict handling correct: 3/3 |

| Presence condition | Trials | Capability task exact | Unrelated legacy task exact | Format valid | Mean coverage | Unsupported assertions | Distractor adoptions |
|---|---:|---:|---:|---:|---:|---:|---:|
| P0_ABSENT | 2 | V06 true | T63 true | 2/2 | 1.000 | 0 | 0 |
| P1_METADATA_ONLY | 2 | R41 true | U07 true | 2/2 | 1.000 | 0 | 0 |
| P2_FULL_INSTRUCTION | 2 | S12 true | W34 false | 2/2 | 0.500 | 1 | 0 |

Across all 18 trials, unsupported assertions total 2 and distractor adoptions total 0. All three conflicting-source trials handled the frozen conflict correctly. The synthetic capability-task outcomes were correct in all three presence conditions; the unrelated legacy-task outcomes were correct in P0 and P1 and incorrect in P2. These observations do not establish causal effects. P3 remains `NOT TESTED / HOST OBSERVABILITY GAP`.

Host-reported token telemetry below is min / max / mean; each Context condition has 4 trials and each Presence condition has 2. Cache-write input was 0 in every trial.

| Condition | Input | Cached input | Output | Reasoning output |
|---|---|---|---|---|
| C0 | 20504 / 20536 / 20516.25 | 7936 / 12032 / 11008 | 9 / 37 / 25 | 0 / 26 / 11.75 |
| C1 | 20524 / 20576 / 20542.75 | 7936 / 18176 / 12544 | 23 / 37 / 27.75 | 9 / 14 / 11.25 |
| C2 | 20565 / 20619 / 20584.75 | 12032 / 18176 / 15104 | 42 / 57 / 49.75 | 28 / 41 / 33 |
| P0 | 20496 / 20499 / 20497.5 | 12032 / 12032 / 12032 | 24 / 78 / 51 | 10 / 67 / 38.5 |
| P1 | 20519 / 20519 / 20519 | 12032 / 18176 / 15104 | 30 / 52 / 41 | 16 / 41 / 28.5 |
| P2 | 20550 / 20554 / 20552 | 12032 / 18176 / 15104 | 46 / 64 / 55 | 32 / 50 / 41 |

The recorded usage basis is `HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY`. Per-exposure token attribution remains `UNAVAILABLE`; ambient host input composition is `UNAVAILABLE / NOT DECOMPOSABLE`; Host model identity and provider dollar cost remain `UNAVAILABLE`. No token estimate was inferred from packet size.

The historical V2 incident contains one attempted A17 packet, zero eligible trials, and zero completed Context or Presence trials; it remains `STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT` and is excluded from the formal matrix. The internal V3 and external PowerShell diagnostics remain separately retained, non-behavioral history and are excluded from scoring. The V3 internal diagnostic's argv SHA-256 is `c00a30408ea66d1e595a4f2452c9dea3e52ed6e9fb5cfad33a728045fe83c8da`; it failed before thread start. The external liveness diagnostic completed successfully as recorded above. None of the historical diagnostics changes the 18 formal trial records.

## Skill certification preflight

No real Skill was selected: the sanitized Phase 1 inventory establishes 136 discovered Skill files but does not provide enough trusted per-skill safety detail for selecting a no-write/no-secret/no-external-write behavioral candidate. The known `project-experience-curator` instruction was excluded because its workflow may write project instruction files. It remains `DISCOVERED / UNEVALUATED`; this is not a retirement decision. No real Skill A/B was run.

## Gaps and gate state

- Exact caller-supplied packet: available and hashed per invocation.
- Complete ambient model-visible context: unavailable through the supported interface.
- Real Skill absent-vs-present/invoked control: unavailable; no real Skill evaluated.
- P3 invocation-only presence: not tested because initial absence and subsequent actual visibility cannot be attested.
- Custom Instructions off/on control: not tested; global setting remains on and unchanged.
- Host Memory contamination: `NOT INDEPENDENTLY VERIFIED`; no test secret was stored.
- Gate J1 Host total-turn token telemetry: `SUPPORTED_BY_REAL_HOST`.
- Gate J2 provider dollar cost: `UNAVAILABLE`; Host model identity: `UNAVAILABLE`.
- Per-exposure token attribution and ambient input composition: `UNAVAILABLE / NOT DECOMPOSABLE`.
- H1 synthetic capability presence behavior: `OBSERVED_IN_REAL_HOST_PILOT`. H2 real Skill capability utility: `NOT TESTED`.
- Cross-session Memory confound: `UNCHARACTERIZED` because Host Memory contamination is `NOT INDEPENDENTLY VERIFIED`.
- Nexus Core Hosted MODEL receipt: none created by the external CLI harness.

G1 explicit experiment packet delivery is `SUPPORTED_BY_FORMAL_REAL_HOST_MATRIX`. G2 full model-visible context proof is `UNAVAILABLE`; ambient context is held constant but partially observable. H1 reflects only synthetic Glyph Shift behavior in this pilot; H2 has no real Skill A/B. No real capability may enter `EVALUATED`, `SHADOW`, or `ACTIVE`. `HOST_BASELINE_V1` remains unchanged. External review set Phase 5 to `CLOSED / ACCEPTED`; acceptance is scoped to the completed matrix and does not assert an Academy-wide PASS. Capability Certification, Shadow, and Production remain `NOT STARTED`.
