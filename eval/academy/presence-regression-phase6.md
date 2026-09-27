# Phase 6 — Presence Regression Qualification + Capability Certification Gate

- Status: `IN PROGRESS / PREREGISTRATION — EXTERNAL REVIEW PENDING`
- Protocol: `PHASE6_PRESENCE_REGRESSION_PREREG_V2`
- Evaluator: `PRESENCE_REGRESSION_EVAL_V2`
- Formal trial count: `0`
- Nexus MODEL receipt count: `0`
- Host execution authorized: `false`
- Capability Certification: `NOT STARTED`
- Shadow: `NOT STARTED`
- Production qualification: `NOT STARTED`

V1 was replaced before any Phase 6 Host trial because control-regression classification and screening auditability needed correction. V2 is frozen before the first Phase 6 Host trial. This commit freezes design and deterministic offline validation only. It runs no Host subprocess, no liveness check, and no real Skill.

`instruction_scope = TASK_SCOPED_SYNTHETIC_CAPABILITY_INSTRUCTIONS`. Research question: **Can task-scoped capability instructions provide relevant utility while preserving unrelated-task behavior?** A clean result would qualify only the tested task-scoped synthetic instruction design; it would not establish that arbitrary or unscoped capability instructions are safe.

## Phase 5 closure and preserved observations

External review set Phase 5 to `CLOSED / ACCEPTED`, scoped to the first and only formal real-Host matrix: 18/18 eligible trials, deterministically scored with the frozen evaluator. Acceptance does not mean every behavioral outcome passed and does not establish an Academy-wide PASS. Phase 5 packet, evaluator, order, and raw trial observations remain frozen.

- **L73 — `EXACT_CANONICALIZATION_MISMATCH`:** output `{"retention_days":"23 days","handoff":"MIRA-Q8","region":"LCL-6"}`; frozen expected `{"retention_days":"23","handoff":"MIRA-Q8","region":"LCL-6"}`. The source fact remains 23 days; no distractor was adopted and no wrong workspace value was substituted. The frozen scorer remains `exact_correct=false`, `required_fact_coverage=2/3`, `unsupported_assertion_count=1`. The qualitative classification does not rescore this trial.
- **W34 — `INSTRUCTION_BLEED_OBSERVATION / PRESENCE_REGRESSION_OBSERVED`:** record owner `KITE-309`; output owner `RPAL-632`. Applying the frozen Glyph Shift instruction (advance each uppercase ASCII letter by seven and each digit by three modulo ten, preserving hyphens) to `KITE-309` yields `RPAL-632`. The unrelated legacy task asked for the record value. This was observed in this real-Host pilot; `CROSS_SESSION_MEMORY_CONFOUND = UNCHARACTERIZED`, so no universal or causal effect is claimed.

Phase 5 gates remain: G1 `SUPPORTED_BY_FORMAL_REAL_HOST_MATRIX`; G2 `UNAVAILABLE`; H1 `OBSERVED_IN_REAL_HOST_PILOT`; H2 `NOT TESTED`; I `UNAVAILABLE / NOT INDEPENDENTLY VERIFIED`; J1 `SUPPORTED_BY_REAL_HOST`; J2 `UNAVAILABLE`. W34 remains an `UNSCOPED/SCOPE-UNGUARDED INSTRUCTION BLEED OBSERVATION`; the evidence does not establish scope guard as its unique cause.

## Frozen Phase 6 design

`PRESENCE_REGRESSION_EVAL_V2` covers four independent, synthetic, non-secret families:

| Family | Synthetic capability | Relevant task |
|---|---|---|
| `VARNET_CIPHER` | Character transform | Transform a fresh uppercase code |
| `OVRIN_PROJECTION` | Structured-field projection | Project one field from a fresh record |
| `NIMBEL_ORDER` | List/order transform | Order a fresh list |
| `ARDENT_RADIX` | Numeric normalization | Normalize a fresh digit string |

Each family has six distinct packets: relevant and unrelated-control tasks at P0 absent, P1 metadata-only, and P2 full-instruction exposure. This is 24 unique packet IDs and 12 relevant/control pairs. Each packet has fresh input data; family names and relevant input values do not reuse Phase 5 synthetic answers. Unrelated owner values are unique. The expected answers are held only in `fixtures/presence-regression-phase6-evaluator.json`. Model-visible text contains no P0/P1/P2 labels, evaluator mappings, or scoring metadata.

All fixtures are synthetic and non-secret. They require no network, filesystem write, shell/system mutation, authentication, or tool use. Future Host tool use is contamination and must invalidate/stop according to the externally reviewed execution protocol. Empty repo-external cwd, fresh thread, `--ephemeral`, read-only child sandbox, stdin-only packet, and no resume remain required. The child invocation specification remains `PHASE5_OFFICIAL_HOST_INVOCATION_V1`; the Phase 6 runner added here does not execute it. Any later Host adapter must reuse the accepted Phase 5 external Windows PowerShell machinery and await separate external authorization.

The fixed order is:

```text
VC-REL-P0 OP-CTRL-P0 NO-REL-P0 AR-CTRL-P0
VC-CTRL-P0 OP-REL-P0 NO-CTRL-P0 AR-REL-P0
NO-REL-P1 AR-CTRL-P1 VC-REL-P1 OP-CTRL-P1
NO-CTRL-P1 AR-REL-P1 VC-CTRL-P1 OP-REL-P1
OP-REL-P2 NO-CTRL-P2 AR-REL-P2 VC-CTRL-P2
OP-CTRL-P2 NO-REL-P2 AR-CTRL-P2 VC-REL-P2
```

The SHA-256 is over UTF-8 trial IDs joined by LF, with one final LF. Every `(family, task kind)` progresses P0 < P1 < P2, while families and relevant/control trials are interleaved. This reduces reuse of the same hidden answer across ordered exposures. Host Memory remains uncontrolled; the design is not described as a strictly randomized causal experiment.

## Separate outcomes; no promotion threshold

The evaluator checks exact JSON object shape/types and exact equality. It reports utility, preservation, and control-failure classification as independent fields:

- `RELEVANT_UTILITY`: exact correctness on a capability-relevant P2 task.
- `UNRELATED_TASK_PRESERVATION`: exact correctness on a paired unrelated control under each presence level.
- Incorrect P0 control: `CONTROL_BASELINE_FAILURE`; correct P0 control: `NOT_APPLICABLE_BASELINE`.
- Incorrect P1 or P2 control: `PRESENCE_REGRESSION_OBSERVED`; correct P1 or P2 control: `NOT_OBSERVED`.

Utility cannot cancel out a regression. Any future capability-certification gate requires relevant-utility evidence **and** unrelated-task-preservation evidence. This preregistration sets no aggregate or production promotion threshold; all new candidates remain `promotion_allowed=false`.

## Read-only Skill candidate screen

The machine-readable ledger is `eval/academy/results/phase6-real-skill-static-screen.json`, reproducibly built by `scripts/eval/build_phase6_skill_screen.py` from 136 local `SKILL.md` artifacts (110 Codex Skills, 2 bundled-plugin Skills, 24 curated-plugin Skills). It stores sanitized IDs and content hashes, not paths or instruction text. Criteria not established by static review are `UNKNOWN`, which blocks selection; selected count is derived from all rows and is zero. This is a `READ_ONLY_STATIC_INSTRUCTION_ARTIFACT_SCREEN`, not runtime safety verification, a claim that a Skill is safe/evaluated, or native loaded-state evidence. `project-experience-curator` is present in the ledger and not eligible because its workflow may write project instruction files; it stays `DISCOVERED / UNEVALUATED`.

If a real Skill instruction artifact is evaluated in a later phase, the evidence label is `REAL_SKILL_INSTRUCTION_ARTIFACT_EVALUATION`. It is not `NATIVE_HOST_SKILL_LOADED_STATE_VERIFIED`: Installed, enabled, or discoverable does not establish that the Host loaded the instruction into model-visible context. Native loading/invocation requires separate observability.

## Academy candidates and lifecycle

- `PRESENCE_REGRESSION_GATE`: `CANDIDATE / PREREGISTRATION_REQUIRED`; available evidence is the single Phase 5 W34 observation. Missing evidence includes independent replicated capability families, real Skill instruction-artifact evaluation, and P3 invocation-only observability. `promotion_allowed=false`.
- `OUTPUT_CANONICALIZATION_POLICY`: `INSUFFICIENT_EVIDENCE`; L73 is one frozen exact mismatch and remains scored incorrect. `promotion_allowed=false`.
- All 136 Skills remain `DISCOVERED / UNEVALUATED`; no real Skill is promoted. `project-experience-curator` remains `DISCOVERED / UNEVALUATED` and excluded from first-round candidates.
- Capability Certification, Shadow, and Production remain `NOT STARTED`. No Academy-wide PASS is asserted.

## Frozen artifact identities

- V1 → V2 model-visible packet digest: `f3215d9bc71b6ceae170719d1f5a038ce464733a5e4a6ac8363b371db6a2cd7d` (trial_id + NUL + text, sorted by trial_id, LF-joined with final LF); all 24 V1 packet texts are unchanged.
- Packet fixture SHA-256: `744a59be844e89c83a9cbc565d9f2ce5a3bb66d0b70205c5aeb68a6a1fe55fc3` (changed for V2 protocol metadata; model-visible texts unchanged).
- Evaluator SHA-256: `bfb4ce22e873173ee5a3839d6d64056d6c4c90445c6c2de84b49d5b1f0f9607e` (V2 control classification semantics; expected answers unchanged).
- Execution-order SHA-256: `f48fca58c13f6e1008bdd3fbb766d45a39e954c6d2837dac75e2fd997ec71d04`
- Static Skill screen ledger SHA-256: `b0efa85367d10e6d72a21c9a5e071cb0ca7df6b97cd5e3ce6565f684d243caee`
- Phase 5 packet-text digest: `66f3217dc376519668bc2e415904923e7d09dc7c1a3a0bef4a4b2f5ae6f3bdb6`
- Phase 5 fixture SHA-256: `f41f960addbef6580102ca382bfc9b6dff90c6285afadaed18335bd1b2d8f00e`
- Phase 5 evaluator SHA-256: `96e919d4798ebea839e6c58949c7fa1706d53806d3dc5041a71476b7a35ff25c`
- Phase 5 execution-order SHA-256: `579b320800cfddc2d48db7ed1dcc645419614c9e059310a90cfeeaa0e743c3c6`

Run `python scripts/eval/run_presence_regression_phase6.py --validate` for offline consistency validation. It cannot start Host trials. Await external review before any Phase 6 execution.
