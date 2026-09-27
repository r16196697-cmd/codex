# Phase 5 — Controlled Host Behavioral Bridge + Certification Preflight

- Status: `IN PROGRESS / EXTERNAL CONTROLLER AUTHORIZED — EXTERNAL REVIEW PENDING`
- Core Independent Audit: `CLOSED / PASS`
- Capability Certification: `NOT STARTED`
- Shadow: `NOT STARTED`
- Production qualification: `NOT STARTED`

## Host execution preflight

The frozen official child invocation remains `PHASE5_OFFICIAL_HOST_INVOCATION_V1`: `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -`, stdin only, no resume, a fresh thread, and a fresh empty temporary cwd outside the repository with no evaluator or ground-truth files. `--ask-for-approval` is not part of the invocation. V2's first A17 attempt is retained as an execution incident, but its formal behavioral evidence is invalid because the committed V2 runner blob does not contain the reported argument and exact per-trial argv was not independently persisted. V2 remains `STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT`, with one attempted packet and zero eligible trials. V3 centralizes argv construction and persists sanitized argv/hash before process outcome. The Codex Agent nested execution path remains `BLOCKED_BY_HOST_STATE_ACCESS`.

An external operator's ordinary Windows PowerShell diagnostic using Codex CLI `0.158.0-alpha.2.1`, the frozen child argument semantics, a fresh empty cwd outside the repository, and stdin `Return exactly: P5_OUTER_HOST_OK` exited 0; `thread.started` and `turn.completed` were observed, the output was `P5_OUTER_HOST_OK`, and tool-call count was 0. Thread ID: `01a0e1ed-4206-7671-98ba-431aac9ef614`. Host-reported total-turn telemetry was input 20481, cached input 7936, cache-write input 0, output 10, reasoning output 0. It is `NON_BEHAVIORAL_EXTERNAL_HOST_DIAGNOSTIC`, not a packet trial, and created no MODEL receipt. `NESTED_OUTER_SANDBOX_CONFOUND = SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC`; this is not proof of a sole root cause because parent-process environments differ. The formal controller is `EXTERNAL_WINDOWS_POWERSHELL`; formal execution is authorized for the frozen matrix only, but it has not started. The first eligible formal matrix execution attempt must come from ordinary Windows PowerShell, not the Codex Agent shell. The external controller is only the outer controller; child `--sandbox read-only` remains in force. Exposure-component attribution, provider dollar cost, and Host model identity remain unavailable.

The harness hashes and records the exact user packet sent on stdin, its UTF-8 byte and character sizes, and the observed final Host message. This supports exact **explicit packet** provenance. The complete ambient system/custom-instruction/skill context is not attested or fully enumerable by this CLI interface; no claim is made that the packet hash represents every token visible to the model. Ambient Host settings were not changed.

Any later manual result is an Academy external-trial record. This harness does not invoke `CodexHostedBridge` and does not create a Core MODEL Run or Hosted MODEL receipt. Therefore `nexus_model_receipt_count` remains zero; no unexecuted or external trial is disguised as a Nexus receipt.

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

The V2 fixed execution order is `A17, E04, V06, F22, B29, H11, T63, D31, R41, G08, K02, J15, U07, L73, M26, S12, N58, W34`.

The separate presence pilot uses the Academy-only `Glyph Shift` capability at absent, metadata-only, and full-instruction presence, plus the same unrelated legacy task under those three conditions. P3 invocation-only is `NOT TESTED / HOST OBSERVABILITY GAP`; no invocation transition is simulated. Packets contain no condition labels, relevance tags, expected answers, or scoring criteria.

The next formal matrix has not started; eligible formal behavioral trial count remains zero. Use the external PowerShell controller runbook for the authorized frozen matrix. Manual packet-by-packet execution remains a fallback artifact and is not the default path. If a separately approved manual fallback is used, use one brand-new Codex chat per packet and paste exactly one `packet_text` as the first message; do not continue a chat, add commentary, or look at the evaluator until all answers are captured. To copy a packet verbatim in PowerShell:

```powershell
$p = Get-Content eval/academy/fixtures/behavioral-phase5-packets.json -Raw | ConvertFrom-Json
$p.context_packets | Where-Object trial_id -eq 'A17' | ForEach-Object packet_text | Set-Clipboard
```

Repeat for each ID in the packet fixture, opening a new chat each time. Save each Host's final message verbatim with its trial ID and return that mapping for deterministic scoring. Do not create a Hosted MODEL receipt from those manual transcripts in this phase. The runner refuses an unfrozen fixture or any result file that already contains executions. It performs one fresh ephemeral CLI process per packet and never retries a failed trial. Raw tool/error logs are not saved. The deterministic evaluator checks JSON shape, exact answers, required-field coverage, unsupported values, and exact distractor adoption.

## Measurements and outcomes

The machine-readable results artifact records one row per trial: explicit task and packet hashes, packet bytes/chars, real Host completion flag, output hash/text, observed tool calls, Host-reported total-turn input/cached-input/output/reasoning-output token telemetry, and local wall-clock duration. Host total-turn token telemetry is observable; per-exposure token attribution, provider dollar cost, Host model identity, provider request ID, automatic retry count, and complete implicit context remain unavailable. No packet, Skill, AGENTS, or ambient token count and no billable dollar cost are inferred. Ambient input composition is `UNAVAILABLE / NOT DECOMPOSABLE`.

The runner writes raw captures only to a temporary directory outside the repository. Persisted Academy results omit the absolute Codex executable and capture-directory paths; capture filenames remain basenames only.

The V2 incident contains one attempted packet (A17), zero eligible formal trials, zero completed Context trials, and zero completed Presence trials. Its matrix status is `STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT`; behavioral inference is disallowed. Host Memory contamination remains `NOT INDEPENDENTLY VERIFIED`, so `CROSS_SESSION_MEMORY_CONFOUND` is `UNCHARACTERIZED`. No condition or family aggregate is calculated from this incident. The V3 diagnostic is non-behavioral and does not alter these results. Its recorded argv SHA-256 is `c00a30408ea66d1e595a4f2452c9dea3e52ed6e9fb5cfad33a728045fe83c8da`; exit code is 1, no thread or turn completed, no agent output was observed, and Host usage is unavailable.

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
- H1 synthetic capability presence behavior: `NOT OBSERVED`. H2 real Skill capability utility: `NOT TESTED`.
- Cross-session Memory confound: `UNCHARACTERIZED` because Host Memory contamination is `NOT INDEPENDENTLY VERIFIED`.
- Nexus Core Hosted MODEL receipt: none created by the external CLI harness.

G1 explicit experiment packet delivery is `SUPPORTED_BY_REAL_HOST_PILOT`. G2 full model-visible context proof is `UNAVAILABLE`; ambient context is held constant but partially observable. Gate H has no completed behavioral trial and no real Skill A/B. No capability may enter `EVALUATED`, `SHADOW`, or `ACTIVE`. `HOST_BASELINE_V1` remains unchanged.
