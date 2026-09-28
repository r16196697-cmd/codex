# Nexus Utility Validation v1 — Pilot Freeze Package

**Status:** `NEXUS_STATE_INSUFFICIENT`\
**Protocol:** Utility Validation v1 preregistration, accepted at `9cc257e6a51234dbb521544b5a9bc8c75a07739d`\
**Source baseline:** `1a41db38a0c1a3b708aaa068ee9344ed4d0a75eb` (fixed; any replacement requires a stop and external review)\
**Formal trial authorization:** `NOT AUTHORIZED`\
**Formal trial count:** `0`

This document records an eligibility audit and a screened candidate pool. It is not a set of frozen executable task cards. No answers, evaluator maps, task assignments, Nexus state, or Host observations were created for this package.

## 1. Scope and research questions

The intended comparison remains A `HOST_NATIVE / BYPASS` against B `NEXUS_ACTIVE`, using the same Codex Host and equivalent copies of the frozen source baseline. It asks whether already-governed Nexus state can improve verified task outcomes and continuity at an acceptable measured total trial cost. It does not test future Knowledge Intake, Playbooks, Skill synthesis, Project Reconstruction productization, or autonomous upgrades.

Reduced confirmatory claims, if a future pilot is separately authorized, are limited to:

1. independently verified task success;
2. first-pass, retry, or inconclusive outcome;
3. controller-observed total trial wall time;
4. actual Nexus intervention operations in B;
5. Context Pack bytes, references, and integrity;
6. Skill resolution and fallback instruction bytes when genuinely triggered; and
7. objectively verified continuity or stale-state correctness.

Token savings, complete model-visible context, Host-native Skill use, provider cost, model identity, strict causal generalization, and complete Project Reconstruction remain outside confirmatory claims. Host usage, if present in an attributable event, is secondary descriptive evidence only.

## 2. Current Nexus state eligibility audit

### 2.1 Read-only inspection boundary

The operator CLI requires an explicit existing `--data-root` and states that it does not initialize a database. No data-root path was supplied for this audit and no relevant `NEXUS_*` data-root environment configuration was present. The only local Nexus database discoverable inside this workspace was under the explicitly named `nexus/data/ui-preview` area; it is ignored local data and was read through SQLite read-only/immutable access. No record bodies were exported or printed.

That preview database reports schema migration version 27 and contains zero canonical Objects/envelopes/states/relations, Tasks, Runs, Attempts, Trace events, admitted Memory, Memory candidates, raw history, Context Pack records, Skill Registry entries, Skill resolutions, Metering records, Verification results, Approvals, Classification assertions, or Grants. It is not evidence of a populated production Nexus instance. A single bootstrap principal and normal runtime/configuration rows do not constitute project knowledge.

No external production data root was named or safely discoverable from the repository configuration. Its existence and contents are therefore `UNKNOWN`; this audit does not scan the user's computer to find one.

### 2.2 Eligibility ledger (counts and source classes, not payloads)

| Candidate state/source | Provenance and shared-source status | Lifecycle / governance | Pre-experiment status | Classification |
|---|---|---|---|---|
| Local `ui-preview` data root | Local preview store; no project source objects | Schema v27; no eligible records | Existing preview state, not a production treatment dataset | `TEST_OR_FIXTURE_ONLY` |
| Nexus Task/Run/Attempt/Trace records in the inspected root | No records | None to authorize or bind | No task state present | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |
| Admitted Memory and Memory candidates in the inspected root | No records | No admitted or quarantined project claims present | No Memory treatment source present | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |
| Evidence, Artifact, and other canonical Objects in the inspected root | No records | No integrity/lineage/purge state to evaluate | No reusable project object present | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |
| Context Pack records and eligible refs in the inspected root | No records | No compiled Pack or eligible source refs | No Context treatment source present | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |
| Production Skill Registry and resolution records in the inspected root | No records | No registered/enabled Skill or resolution | No Skill treatment source present | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |
| Repository code, docs, and committed evaluation/history records at source baseline | Git provenance at the frozen commit; available to both A and B workspaces | Shared source; not a Nexus Memory/Evidence/Artifact record | Preexisting and committed before the source baseline | `PREEXISTING_ELIGIBLE` as shared source only; **not** a Nexus treatment |
| Academy fixtures and synthetic evaluation packets/results | Fixture/test provenance | Experiment-specific synthetic content; not production project state | Preexisting test/evaluation material | `TEST_OR_FIXTURE_ONLY` |
| Prior regression/pilot reports committed in the repository | Report provenance, readable from the same shared repository | Historical descriptions do not prove that their old local Objects/Runs remain in the current data root | Preexisting shared documentation | `PREEXISTING_ELIGIBLE` as shared historical source only; **not** reusable live Nexus canonical state |
| Conversation-only project history not present in the frozen repository | No source in the Host workspace or inspected canonical store | Cannot be independently retrieved by A from the shared baseline | Not part of the eligible evidence set | `PREEXISTING_BUT_NOT_SHARED_SOURCE` / exclude |
| State that would need to be created now to make B useful | Would be newly written for this study | Not naturally preexisting | Prohibited by this freeze task | `INSUFFICIENT_FOR_CONFIRMATORY_USE` |

The database audit supports no eligible preexisting Nexus treatment object. Repository history and documentation can support shared-source retrieval tasks, but treating them as Nexus canonical Memory or Evidence would misstate provenance. No Memory, Evidence, Artifact, Context source, Skill, Task, or Run was created or changed.

The versioned project record itself has a readable lifecycle at the source baseline: the Phase 5 result/closure is historical and closed/accepted; the Phase 6 incident/result is historical and closed/inconclusive; earlier Phase 6 preregistration states are superseded by the accepted V2 and final closure. Utility preregistration is accepted, while this instrumentation implementation records no formal Utility trials. These are facts in shared Git documents/results, not current canonical objects in the inspected Nexus root. Earlier Slice documents that say Context or Skills are not implemented are superseded by the later Slice 2/3 docs; they are not treated as current capability state. No private/local old persistent pilot database was found or inferred from these documents.

## 3. Candidate task-pair pool and screening

The following 12 are **candidate sketches only**, not frozen cards. Each A/B variant points at a different instance so the pair would not repeat the same hidden answer. Matching rationales are provisional. All currently fail the B-treatment eligibility gate because no preexisting eligible Nexus canonical state was found. None is promoted to the confirmatory set.

| Pair | Family | Candidate A variant | Candidate B variant | Matching rationale | Shared-source / Nexus-state screen |
|---|---|---|---|---|---|
| `UV1-C01` | Current state recovery | Reconstruct the preregistered v1 question and its execution authorization state from the accepted preregistration | Reconstruct the instrumentation slice's implemented boundary and remaining authorization state | Same short status-reconstruction task with fixed document evidence and structured fact checks | Both answers are in shared repo docs; no preexisting Nexus state to treat with |
| `UV1-C02` | Goal and next-step recovery | Identify the next governed step recorded by the accepted preregistration | Identify the next step recorded by the accepted instrumentation readiness/implementation documents | Same “where did work stop and what is authorized next?” burden | Shared docs are available to A; no Nexus Memory/Context to reuse |
| `UV1-C03` | Historical decision / evidence | Explain the recorded Phase 5 closure decision using its committed result/report references | Explain the recorded Phase 6 closure decision using its incident and closure references | Similar chronology lookup and evidence-ref requirement, different phase facts | Reports are shared repo sources; old run/object records are absent from current root |
| `UV1-C04` | Stale / superseded state | Distinguish a superseded Phase 6 preregistration statement from the final closure record | Distinguish an older inventory-completeness assumption from the accepted partial-positive adapter rule | Same stale/current adjudication task with explicit commit-time ordering | Git/docs provide the baseline; no live Nexus stale-state record exists |
| `UV1-C05` | Context governance | Locate the implementation rule that controls Context source eligibility and Run binding | Locate the implementation rule that controls Context Pack delivery and visibility claims | Similar bounded code/document investigation with exact file/field checks | Shared code/docs; no compiled eligible Context Pack exists |
| `UV1-C06` | Artifact/Evidence reuse | Trace one historical regression report's stated evidence chain | Trace one distinct committed closure/incident report's stated evidence chain | Same provenance-chain extraction and bounded reference count | Historical reports are accessible to both; canonical Objects/Evidence are absent |
| `UV1-C07` | Read-only code diagnosis | Diagnose one bounded workspace-validation edge in the utility controller and propose a minimal diff | Diagnose one bounded event-state validation edge in the observation ledger and propose a minimal diff | Similar code surface, output-only patch, external deterministic checks | Source is shared; no Nexus treatment source supports either variant; exploratory only |
| `UV1-C08` | Docs/implementation consistency | Compare the Context Pack documentation claim with its current service/API path | Compare the Skill inventory documentation claim with its current adapter/API path | Same number of claims and source files, exact discrepancy checklist | Shared source supports an objective checker; no Nexus state advantage |
| `UV1-C09` | Skill-relevant | Given a narrowly matched task intent, determine whether the frozen Nexus Registry has an eligible relevant Skill resolution | Given a different intent, determine whether the frozen Nexus Registry has an eligible relevant Skill resolution | Same metadata-only lookup task, distinct intents | Registry has zero entries/resolutions; no valid Skill treatment; exclude |
| `UV1-C10` | Skill-irrelevant control | Complete a bounded repository task whose acceptance does not require a Skill | Complete a different bounded repository task whose acceptance does not require a Skill | Similar task depth; no skill-specific knowledge required | Could be a control only if B has a real eligible intervention; none found |
| `UV1-C11` | Superseded design / current contract | Decide whether an earlier Host delivery assumption remains valid under the current transport contract | Decide whether an earlier Skill inventory assumption remains valid under the current partial inventory contract | Same historical-to-current contract reconciliation | Shared docs/code; no governed Nexus continuity record |
| `UV1-C12` | Verification / independent acceptance | Verify a bounded statement against a committed source ref and exact identity/hash | Verify a distinct bounded statement against a different source ref and exact identity/hash | Same deterministic acceptance form and comparable evidence count | Shared Git source can verify; no Nexus verification/evidence object exists in inspected root |

### Screening result

- Candidate sketches: **12 pairs**.
- Eligible confirmatory pairs: **0**.
- Frozen task cards/variants: **0**.
- Frozen answer maps/rubrics: **0**.
- Randomized assignments/seed: **not generated**.

The candidates could support a Host-only repository retrieval study, but not the preregistered A/B question about the utility of naturally preexisting Nexus state. Adding Memory, Evidence, Context sources, Skill entries, or task history now to make B different would be experiment seeding, not current-state utility measurement.

## 4. Pair, shared-source, and workload rules

If external review later supplies/identifies an eligible production state, each pair must have a pair-level contract and two different variant cards. The pair contract fixes family, factual depth, required source count, repository area, reasoning/tool-search burden, and acceptance complexity; variants have distinct task text, target fact/file/decision, task payload hash, and task-variant hash. No pair may replay the same hidden answer under A and B.

Every fact that can change PASS/FAIL must be recoverable from the common frozen repository baseline or another expressly shared source. B-only Memory/Context/Artifact/Skill may reduce rediscovery effort only when its source lineage resolves to that same shared baseline. A fact only present in Nexus is `NEXUS_ONLY_DESCRIPTIVE` and excluded from the confirmatory set. Conversation-only facts are not ground truth.

For code tasks, the Codex workspace remains read-only. The task may request diagnosis and a minimal patch/diff; an independent evaluator applies it to a disposable copy and runs frozen deterministic checks. The Host is not credited with writing the repository. If a proposed code task cannot be objectively checked this way, exclude it.

## 5. Ground truth, rubric, and privacy

No task-specific ground truth or acceptance map is frozen in this package because there are no eligible task cards. For a future freeze, the evaluator package must live outside the repository and all Git objects/branches/remotes visible to the Host. The package will be versioned and access-controlled for the independent evaluator; the public ledger/freeze note may contain only its hash and a sanitized opaque reference. It must not contain answer text, expected fact maps, hidden evidence maps, or task-specific pass keys.

The model's shared source is the frozen repository snapshot at `1a41db38a0c1a3b708aaa068ee9344ed4d0a75eb`. The task-freeze package, evaluator maps, condition assignment, and any post-baseline analysis documents must not be placed in that workspace. Since Host network/tool availability is not yet established, a future live-conformance gate must verify the approved tool/network policy. Any unapproved network, remote fetch, external search, or cross-condition access is an incident/invalidation; it is not assumed absent merely because the child CLI uses `--sandbox read-only`.

Each eventual rubric must freeze required facts, prohibited stale claims, source refs, deterministic PASS/FAIL checks, and INCONCLUSIVE conditions. Prefer exact identities, hashes, parser checks, and fixed deterministic tests. A blinded independent reviewer is a last resort and must not receive condition or Nexus metadata. A/B use the same rubric semantics. No LLM judge or post-hoc thresholds.

## 6. Frozen repository workspace and source parity

The trial workspace source baseline remains exactly `1a41db38a0c1a3b708aaa068ee9344ed4d0a75eb`; this pilot-freeze commit and any later files are outside the Host-visible snapshot. A future preparation operator must provide separate repo-external snapshots for each condition, each at the exact baseline, clean including untracked/ignored/submodule state, with the same content-derived workspace identity. Clone/snapshot construction is completed before `TRIAL_OPENED`; post-open workspace validation is included in trial wall time for both conditions. The controller validates but does not silently construct or repair a snapshot.

The source snapshot may expose the repository content and the Git history actually included in that snapshot. It must not contain the evaluator package or assignment. A remote may expose later refs if network is available; therefore no hidden answer or acceptance map may exist in any repository revision/ref. Network/tool use is governed by the future conformance policy, not presumed disabled. No source repo is used as a mutable trial workspace.

Each pair must match starting commit, source content identity, environment/config reference, and rubric identity. Task variants may differ. Host invocations are fresh and condition workspaces are independent; no output or generated state from one condition is available to the other. If that cannot be proven, exclude the pair before outcome comparison.

## 7. Nexus canonical-state snapshot strategy

There is no eligible populated production root to freeze in this audit. The `ui-preview` database is not promoted or copied into a trial snapshot. No canonical data was changed.

If a real root is later explicitly identified and authorized, snapshot preparation must happen outside trial time and without concurrent mutation: quiesce/close the one writer, take a consistent database snapshot using the existing supported storage procedure, copy the Object payload tree and required policy/governance metadata as a coherent unit, and preserve the independent purge journal/barrier semantics. Produce a deterministic manifest over sorted sanitized relative object names, byte lengths, and content hashes; bind schema/migration version, safe record counts by category, freeze timestamp/source, and snapshot manifest hash. The manifest must contain no absolute data-root path or payload text. Reopen/validate the frozen copy using existing Core integrity/recovery checks before any task freeze. Then create a separate disposable copy for each B trial so one trial cannot mutate the next trial's starting state. A never receives this Nexus snapshot. If a consistent, purge-safe snapshot cannot be made, stop rather than share a mutable root.

This is a proposed freeze procedure only; no Nexus snapshot or copy was created in this task.

## 8. Randomization and contamination controls

No assignment or random seed is produced until at least one pair has passed eligibility and independent review. At that point, freeze a cryptographic random seed and a deterministic assignment artifact held outside Host workspace; balance A-first/B-first across pairs, with each pair's A/B variants randomly assigned without answer reuse. Save assignment hash and a sanitized evaluator-side reference before seeing outcomes.

Each condition uses a fresh CLI invocation and an independent frozen repo snapshot. Nexus B uses an independent copy of the same frozen Nexus snapshot. No condition output, new canonical state, diff, or cross-session answer is transferred. Keep `CROSS_SESSION_MEMORY_CONFOUND=UNCHARACTERIZED`; balanced order reduces but does not eliminate it. No replacing, retrying, or swapping task cards after seeing a result.

## 9. Context and Skill treatment identity

For Context, the strongest current eval-controller evidence is: a governed Pack is compiled/bound to the correct Nexus Task/Run, then its exact bytes are included in the intervention envelope and submitted to the exact CLI stdin boundary. This is `EVAL_INTERVENTION_TRANSPORT` / `CONTROLLER_SUBMITTED_TO_CODEX_CLI`, not production delivery. `MODEL_VISIBLE` and `USED_IN_OUTCOME` remain `UNKNOWN`.

For Skills, distinguish `REGISTERED`, `AVAILABLE`, `SELECTED`, `INSTRUCTION_LOADED`, `CONTROLLER_SUBMITTED_TO_CODEX_CLI`, `MODEL_VISIBLE`, and `USED_IN_OUTCOME`. A Host-native match only means positive package discovery; it does not establish Host selection/use and Nexus must not inject a duplicate instruction. Current filesystem inventory is partial: a miss is `UNKNOWN`, not `UNAVAILABLE`, and cannot trigger fallback. Nexus fallback is available only from an eligible, enabled, integrity-valid governed snapshot when complete Host evidence says unavailable; its bytes may then be submitted through the eval transport. No such Skill entry exists in the inspected state.

## 10. Live CLI conformance rule (not run)

The implementation's expected invocation policy is the fixed `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -` contract with direct argv, `shell=false`, stdin input, and the validated frozen repo snapshot as subprocess `cwd`. This audit did not call Codex or run `codex --version`.

Before any formal trial authorization, separately request one `NON-SCORED LIVE CONFORMANCE` using a harmless non-task sentinel and no mutable Nexus state. The operator must capture and review the exact installed CLI version; freeze that exact version for the pilot or stop on mismatch. A pass requires the expected JSONL start/completion events, unique thread/session identity if emitted, complete parse, expected stdin/argv provenance, correct external workspace cwd, exit 0, no timeout, no unexpected tool/network use, and no Nexus receipt. Missing/unknown required event or unobservable/unsupported stdin contract is FAIL/INCONCLUSIVE; do not infer fields from stderr or model output. The conformance run is not a task card, is not scored, does not authorize trials, and must not alter the frozen Nexus snapshot. No conformance was performed here.

## 11. Metric provenance and missingness

| Metric | Future source | Status before live task freeze |
|---|---|---|
| Pair/trial/task/source/rubric identity | External observation ledger and frozen manifests | Designed; no task cards |
| Verified outcome / first pass / retry / inconclusive | Shared blinded deterministic evaluator plus ledger | Rubric not frozen |
| Full trial wall time | Eval controller monotonic timer, from trial open through acceptance | Implemented boundary; no trial |
| Host subprocess time / thread / events | CLI controller/parser, only fields actually present | Mock/static evidence only; live conformance pending |
| B Context Pack refs/hash/exact bytes | Existing Core Context Pack/Object/lineage query and intervention event | No eligible state/Pack in current root |
| B Skill resolution/load bytes | Existing Registry/ResolutionRecord/Artifact integrity query | No eligible Registry entry/record in current root |
| Controller-submitted intervention | Exact final stdin bytes/hash and invocation correlation | Eval transport only; not production delivery or MODEL_VISIBLE |
| Model-visible context and native Skill selected/loaded/used | No supported signal currently identified | `UNAVAILABLE` / `UNKNOWN` |
| Total/cached/uncached/output/reasoning tokens | Host-reported usage only, if emitted and attributable | No live evidence; absent fields remain `null + UNAVAILABLE` |
| Repeated reads/searches/reacquisition/manual restatement | No reliable shared per-task source | `UNAVAILABLE`; not zero |
| Provider/model identity and dollar cost | No reliable attributable signal/pricing basis | `UNAVAILABLE` |
| Prompt cache benefit / token savings | No valid Nexus attribution | Not claimed |

## 12. Stop, exclusion, and analysis rules

Keep the accepted preregistration's stop and exclusion rules. In particular, preserve partial facts; do not retry/replace an interrupted trial; mark ambiguous provenance, source mismatch, task/start-state mismatch, unapproved tool/network access, incomplete acceptance, or cross-condition contamination as incident/exclusion/inconclusive according to the existing ledger protocol. A failed Nexus preparation remains an assigned B fact with `NO_INTERVENTION`/inconclusive; it is not silently removed.

If eligibility is later repaired, begin with a small pilot only after independent approval: pair-level descriptive outcomes, no significance or universal causal claim, and no cost-per-success computation unless all common costs and attribution are measured on the same basis. Token telemetry is secondary. Missing values never become zero.

## 13. Future hypotheses not scored

Knowledge Intake/Capture, Procedure/Playbook, automatic Skill synthesis, Knowledge Gap Analysis, Nexus Scout, Knowledge Explorer, complete Project Reconstruction/Session Bootstrap, autonomous self-upgrade, second Host, DSH, Host portability, model-visible prompt proof, native Skill use, provider billing, and full reacquisition telemetry remain future or unavailable capabilities. None is treated as present in this freeze package.

## 14. Exact next step after independent review

The current disposition is `NEXUS_STATE_INSUFFICIENT`, not readiness for conformance or formal trial. An authorized operator must first identify the intended existing production Nexus data root and approve a read-only eligibility audit of that exact root. Do not seed it. If it has no qualifying preexisting governed state, stop and ask external review whether to design a distinct, explicitly labeled bootstrap/seeding study. If it does have qualifying state, repeat the eligibility/source-lineage audit, select at most eight candidate pairs, create external hidden evaluator materials, and then submit this package for independent task-freeze review. Only after that review may a separate non-scored live conformance be requested. Formal pilot execution still requires its own later authorization.

No Utility task, live conformance, or formal trial was run while producing this document. No formal trial was authorized by this package.
