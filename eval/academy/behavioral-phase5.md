# Phase 5 — Controlled Host Behavioral Bridge + Certification Preflight

- Status: `IN PROGRESS — Phase 5`
- Core Independent Audit: `CLOSED / PASS`
- Capability Certification: `NOT STARTED`
- Shadow: `NOT STARTED`
- Production qualification: `NOT STARTED`

## Host execution preflight

The installed local interface reported `codex-cli 0.158.0-alpha.2.1`. Its actual `codex exec --help` exposes non-interactive execution, stdin prompt input, `--ephemeral`, JSONL output, and final-message capture. The frozen official invocation is `PHASE5_OFFICIAL_HOST_INVOCATION_V1`: `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -`, stdin only, no resume, a fresh thread, and a fresh empty temporary cwd outside the repository with no evaluator or ground-truth files. `--ask-for-approval` is not part of the invocation. The automated Host behavioral bridge is feasible; a real Host pilot supports explicit packet delivery. The formal matrix has not started. Manual fresh-chat instructions remain only as a fallback artifact.

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

The 18 blind packets and their SHA-256 are in `fixtures/behavioral-phase5-packets.json`; expected outputs, condition mapping, and deterministic scoring are isolated in `fixtures/behavioral-phase5-evaluator.json`, which is never supplied to the Host. Four context families each have no synthetic records, minimum relevant records, and broader safe records. They cover a nonce fact, multi-fact composition, a similar identifier, and a two-source conflict. C0 answers for hidden facts are expected to be `UNKNOWN`, not a guess.

The separate presence pilot uses the Academy-only `Glyph Shift` capability at absent, metadata-only, and full-instruction presence, plus the same unrelated legacy task under those three conditions. P3 invocation-only is `NOT TESTED / HOST OBSERVABILITY GAP`; no invocation transition is simulated. Packets contain no condition labels, relevance tags, expected answers, or scoring criteria.

The automated attempt was blocked before a Host turn. Do not rerun it in this state. For manual execution, use one brand-new Codex chat per packet and paste exactly one `packet_text` as the first message; do not continue a chat, add commentary, or look at the evaluator until all answers are captured. To copy a packet verbatim in PowerShell:

```powershell
$p = Get-Content eval/academy/fixtures/behavioral-phase5-packets.json -Raw | ConvertFrom-Json
$p.context_packets | Where-Object trial_id -eq 'A17' | ForEach-Object packet_text | Set-Clipboard
```

Repeat for each ID in the packet fixture, opening a new chat each time. Save each Host's final message verbatim with its trial ID and return that mapping for deterministic scoring. Do not create a Hosted MODEL receipt from those manual transcripts in this phase. The runner refuses an unfrozen fixture or any result file that already contains executions. It performs one fresh ephemeral CLI process per packet and never retries a failed trial. Raw tool/error logs are not saved. The deterministic evaluator checks JSON shape, exact answers, required-field coverage, unsupported values, and exact distractor adoption.

## Measurements and outcomes

The machine-readable results artifact records one row per trial: explicit task and packet hashes, packet bytes/chars, real Host completion flag, output hash/text, observed tool calls, CLI-reported token usage if present, and local wall-clock duration. Provider cost, provider identity, underlying served model identity, request ID, automatic retry count, and complete implicit context are `UNAVAILABLE` unless the Host exposes them directly. No character-to-token estimate is used.

The result records the historical pre-model CLI failure and pilot diagnostics separately; neither is a formal trial. The formal trial count remains zero. A prepared fixture alone is not behavioral evidence. No aggregate score is calculated. No skill utility, model quality gain, cost saving, or Shadow readiness is inferred from a small pilot.

## Skill certification preflight

No real Skill was selected: the sanitized Phase 1 inventory establishes 136 discovered Skill files but does not provide enough trusted per-skill safety detail for selecting a no-write/no-secret/no-external-write behavioral candidate. The known `project-experience-curator` instruction was excluded because its workflow may write project instruction files. It remains `DISCOVERED / UNEVALUATED`; this is not a retirement decision. No real Skill A/B was run.

## Gaps and gate state

- Exact caller-supplied packet: available and hashed per invocation.
- Complete ambient model-visible context: unavailable through the supported interface.
- Real Skill absent-vs-present/invoked control: unavailable; no real Skill evaluated.
- P3 invocation-only presence: not tested because initial absence and subsequent actual visibility cannot be attested.
- Custom Instructions off/on control: not tested; global setting remains on and unchanged.
- Host Memory contamination: `NOT INDEPENDENTLY VERIFIED`; no test secret was stored.
- Tokens: only CLI-reported usage may be recorded; provider cost and served model/provider identity remain unavailable.
- Nexus Core Hosted MODEL receipt: none created by the external CLI harness.

G1 explicit experiment packet delivery is `SUPPORTED_BY_REAL_HOST_PILOT`. G2 full model-visible context proof is `UNAVAILABLE`; ambient context is held constant but partially observable. Gate H has no completed pilot and no real Skill A/B. No capability may enter `EVALUATED`, `SHADOW`, or `ACTIVE`. `HOST_BASELINE_V1` remains unchanged.
