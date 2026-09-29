# Project Nexus — Self-Hosting Bootstrap Design

**Status:** `PROPOSED / EXTERNAL REVIEW REQUIRED / NO BOOTSTRAP EXECUTION AUTHORIZED`\
**T0:** Utility Validation v1 audit `9c859cd0cb512a5993af54912a55389604bcab1f` concluded `NEXUS_STATE_INSUFFICIENT`; formal Utility trial count remains `0` and trial execution is `NOT AUTHORIZED`.\
**Scope:** design and bounded read-only provenance resolution only. No production root was initialized and no Nexus canonical data was written.

## 1. Existing production root resolution

Result: **`NO_EVIDENCE_OF_EXISTING_PRODUCTION_ROOT`**.

The inspection was limited to repository-documented data-root conventions, the current project's tracked launcher/operator configuration, relevant process environment variable names, and the explicitly named Nexus local data location under the checkout. The operator CLI requires an existing `--data-root` and refuses to create a database implicitly. No production root was configured in the inspected project sources or relevant `NEXUS_*` environment configuration. No launcher supplied another root.

The only local Nexus database found within that bounded scope is the explicitly named `ui-preview` root. It is ignored local preview data, not evidence of production Project Nexus state. Read-only/immutable inspection found schema migration version 27 and zero Objects/envelopes/states/relations, Tasks, Runs, Attempts, Trace events, admitted Memory, Memory candidates, raw history, Context Packs, Skills/resolutions, Metering records, Verification results, Approvals, Classification assertions, or Grants. Its runtime/configuration rows do not make it project knowledge. No payload bodies were read or exported. The repository's 2026-09-24 environment audit also said no persistent Nexus root was found in its then-inspected scope; this is historical corroboration, not proof about uninspected locations.

No full-disk search, unrelated user-file scan, shell history inspection, private application-state inspection, or configuration change was performed. A production root may have existed elsewhere, but this bounded audit found no reliable evidence for one. Per instruction, the search stops here.

## 2. Self-hosting objective

Self-hosting means using Nexus's governed canonical state from now on to manage the Nexus project: its current objective and work, accepted decisions/constraints, evidence and source provenance, active and superseded state, failures, and next steps. It does not mean copying the conversation archive or all repository history into a database.

The desired later-session experience is that “continue Nexus” can retrieve a compact current state and active task, state the current branch/revision and important recent changes, preserve constraints and open questions, avoid superseded decisions, and follow evidence refs back to original Git/docs when needed. This is a product objective, not a claim that Project Reconstruction is already implemented.

## 3. Existing Core reuse and current gaps

The v0.1 scope is an operational scope over one dedicated Nexus data root; no first-class Project object is required. Reuse the existing Task/Run/Attempt and Trace services for work actually undertaken after bootstrap; ObjectStore Objects/Artifacts and `derived_from` lineage for imported sources and derived summaries; Evidence and Verification paths for source and integrity evidence; MemoryService for governed candidates/admission/search; ContextPackService for explicit eligible refs bound to a real Task/Run; Skill Registry only for explicitly governed Skills; and existing operator CLI/Panel projections for inspect and review.

Current boundaries matter:

- `MemoryService.create_candidate()` binds a claim and evidence refs to a Verification result. T1 object-integrity verification only proves bytes, not semantic truth. The current verifier offers T1 integrity and T3 human/domain attestation; no usable T2 authoritative verifier path was found for this bootstrap. Semantic Memory must not be admitted on T1 alone. Use the existing approval-bound T3 route where appropriate; otherwise leave a candidate quarantined or keep the fact only as an evidence-backed Artifact.
- `ContextPackService` requires explicit eligible sources and a real authorized Task/Run. A compiled Pack is not Host delivery or model-visible evidence.
- The object-state schema has a `revision` value (`CURRENT`/`SUPERSEDED`), but no operator-facing Core transition API for marking supersession was found. Bootstrap must not update SQLite directly. Until a reviewed API exists, keep only current claims in admitted Memory/default Packs; retain historical sources as unindexed source Artifacts for explicit historical retrieval. If the chosen workflow requires durable object-level supersession transitions, stop and request a narrow Core/application design review rather than faking it in payload text.
- The operator client operates only on an existing bound data root. The explicit `adapters.bootstrap` Stage 1/2 commands provide the reviewed initialization and authority-bootstrap path; they do not create a Project Nexus production root by themselves. Direct `ObjectStore` construction remains an implementation/test capability, not an operator shortcut.
- A local Git source-import command and a Project Nexus-specific Stage 3 application composition now exist. Source import still goes through the authorized adapter/Core boundary, existing ObjectStore and governance; it is not Git synchronization, a crawler, or a general workflow engine.

## 4. Pre-Nexus history import semantics

Past work that did not pass through Nexus is imported as source material, not reconstructed as execution history. A current bootstrap Task/Run may record the actual import and verification work performed now. It must not create past Runs, Task execution receipts, Trace events, Verification events, Effects, or Approvals.

Imported source Artifacts/Evidence must carry or resolve to an explicit provenance label such as `IMPORTED_FROM_PRE_NEXUS_HISTORY`, source commit/ref/hash, and import time. That label describes the import, not historical Nexus observation. Derived summaries retain `derived_from` links to source objects. No imported source is described as a contemporaneous Host receipt.

## 5. Source hierarchy

| Tier | Source class | Admission/use rule |
|---|---|---|
| A — authoritative implementation facts | Git commit/tree, committed code, accepted evaluation result, accepted closure document, immutable result artifact | Primary source for “what code/state/result is recorded at revision X.” Verify exact commit/ref and bytes. Integrity alone does not prove a semantic interpretation. |
| B — accepted decisions and handoffs | Independently accepted Slice closure, Phase 5 closure, Phase 6 final closure, current Utility status, accepted architecture decisions | Admit only with acceptance/revision provenance. A handoff is a curated source, not itself a Nexus canonical record. Reconcile against Tier A where possible. |
| C — historical design documents | Superseded preregistrations, rejected alternatives, old status docs, historical task plans | Preserve selectively for “why/how did this change?” queries. Never promote old statements to current truth without a later source comparison. |
| D — conversation-derived summaries | Chat recollections or summaries | Durable current Memory only when traceable to a stronger source. Uncorroborated recollection remains `INFERRED`/`UNKNOWN` or is not admitted. |

Source authority and truth state are separate. Git existence or accepted document status does not by itself prove every sentence in a derived summary.

## 6. Minimal initial canonical set

Do not import all history. After separate authorization and an approved root initializer, the proposed smallest useful set is:

1. **One bootstrap Task and its actual Root Run** for the current import/review operation. These record only work done after T1.
2. **A small source set (roughly 5–8 immutable source Artifacts)**: the current accepted Phase 5 closure, Phase 6 final closure/incident, accepted Utility v1 preregistration and Tier 1 instrumentation implementation, current Utility pilot-freeze audit, and the accepted v0.1/Slice architecture boundaries needed for ongoing implementation. Select exact refs at bootstrap time; do not copy unrelated Academy fixtures, every iteration, or all handoffs.
3. **One concise current-state Artifact** derived from those source refs. It should state the current branch/accepted revision as observed at bootstrap time, current objective, current status, blockers, next step, and active constraints, each with source refs and truth labels.
4. **A few atomic Memory Candidates (target 5–10, not a quota)** for durable high-value facts only: current project objective/status, Phase 6 closed/inconclusive and no retry, Utility v1 blocked with zero trials, attached Codex-hosted scope, current participation/governance boundaries, and immediate next step. Use existing verification/approval semantics. If evidence/independence is insufficient, keep the candidate quarantined or omit it; do not force admission.
5. **At most one real Context Pack** compiled from the current-state Artifact and already eligible/admitted refs, bound to the genuine bootstrap or next-work Task/Run. Verify its refs, exact bytes, hash, and authority. Record compile only; Host delivery and model visibility stay unknown unless a supported adapter proves them.

No historical Skill is registered as part of bootstrap. No generic “project knowledge dump” is created. Open hypotheses are either omitted from Memory or explicitly represented as `HYPOTHESIS` with a review trigger; they are not current capabilities.

## 7. Memory admission

Follow the existing sequence:

`source → Evidence/Artifact → source/integrity verification → Memory Candidate → governed admission`

Do not call a summary `VERIFIED` just because its file hash matches. Byte integrity verification is T1; it is not independent semantic corroboration. The current Memory service admits only supported verification/approval configurations; for semantic claims, use the existing human/domain T3 Approval path with exact target/evidence binding and independence recorded. If no appropriate approved verification is available, preserve the material as source evidence, leave the candidate quarantined, or omit it.

Truth labels stay distinct:

- `VERIFIED`: only after the existing verification/admission gate establishes it;
- `SUPPORTED`: evidence supports the bounded claim but it is not promoted to verified;
- `INFERRED`: interpretation derived from sources;
- `HYPOTHESIS`: future design proposition;
- `UNKNOWN`: unresolved or conflicting.

Never change the Memory truth/admission policy to get a more complete bootstrap.

## 8. Supersession and current-vs-history retrieval

Preserve historical source objects; do not delete them to hide stale state. The initial retrieval policy is deliberately conservative:

- The current-state Artifact is the default compact entry point and is regenerated only through an actual governed update, with its source refs and as-of commit.
- Only current, evidence-supported atomic claims are considered for admitted Memory and default Context Packs. Do not admit old and new contradictory claims as if both were current.
- Historical/superseded documents remain source Artifacts and are not automatically indexed as admitted Memory. Retrieve them only for an explicit historical/design-rationale question, then accompany them with the current-state source.
- The summary explicitly marks historical claims `SUPERSEDED` or `HISTORICAL` and cites the replacing source. It distinguishes `CURRENT`, `SUPERSEDED`, `HISTORICAL`, and `UNKNOWN`.
- The existing `object_state.revision` field has no identified governed mutation API. Do not set it by SQL or assume it is enforced in retrieval. If object-level supersession becomes necessary, first review a minimal application/Core API and retrieval semantics; until then, use curated current-only refs and keep old sources out of default Memory/Context.

This is a retrieval discipline over explicit refs, not a new Decision subsystem or a claim that all Core searches automatically rank current truth.

## 9. Daily self-hosted operating loop

For a real post-bootstrap task:

1. Operator records the user request as a current Task/input and creates a properly classified, authorized Root Run. Never create a Run for past work.
2. Under `ACTIVE`, compile a Context Pack only from explicit eligible refs and governed admitted Memory within the Run's grant/data boundary.
3. Codex performs the work through the existing attached path. Record only actual declared/model/tool operations supported by Hosted Bridge; do not invent a MODEL receipt or assume model visibility.
4. Persist produced outputs as Artifacts and source observations as Evidence, with exact source provenance, classification, integrity, and lineage.
5. Verify results independently using existing Verification. T1 confirms bytes; semantic claims require appropriate independent evidence and human/domain review.
6. Create narrowly scoped Memory Candidates from durable lessons, decisions, blockers, or failures. Admit only through existing truth and Authority policy; retain uncertainty and conflicts.
7. Update the current-state Artifact only when actual Task/Run evidence supports a change. Recompile a Context Pack for later work and inspect via existing operator/panel APIs.

On failure, preserve the actual failed Run/Attempt and evidence. A reusable lesson begins as a candidate; later Procedure/Skill/policy proposals go through review, sandboxed evaluation, regression, Authority, Verification, Approval, and Effect controls. Learning does not grant authority or promote itself.

`project-experience-curator` may be used as an operator-facing process for proposing project lessons where appropriate, but its presence is not evidence of execution. Academy/Evolution may later help assess proposals; it does not bypass Core governance. No Procedure layer or autonomous self-upgrade is implemented here.

## 10. Handoff projection role

Existing `Nexus_会话交接_*.md` files are Tier B/C bootstrap sources with their own date/revision and provenance. They are not canonical truth and must be reconciled against accepted Git/evaluation sources. After self-hosting, the canonical current state and active Tasks/Runs become the source for a future generated handoff projection. This design does not implement a handoff generator and does not require repeated manual upload of full historical texts.

## 11. Separation from Utility Validation v1

The v1 confirmatory pilot remains blocked: `NEXUS_STATE_INSUFFICIENT`, formal trial count `0`, trial execution `NOT AUTHORIZED`. Self-hosting is a separate prospective product dogfooding phase, not preparation that repairs the old baseline.

- **T0:** audit commit `9c859cd0cb512a5993af54912a55389604bcab1f` recorded state insufficiency.
- **T1:** begins only after external review, explicit production-root initialization authorization, and actual first canonical write. It is a new prospective start; freeze its actual date/revision at execution time.
- Any utility study using state accumulated after T1 must be a newly preregistered prospective/longitudinal or matched evaluation. It must not be inserted into v1 and described as preexisting state.

No Utility trial, Academy run, Phase 6 reopen, capability certification, Shadow, or production qualification is authorized by self-hosting.

## 12. Future utility measurement (design only)

Once natural work accumulates, a separately reviewed prospective evaluation may measure new-session resume correctness, resume time, manual restatement count, repeated repository/document search, stale-decision mistakes, Evidence/Artifact reuse, Context Pack size, Skill resolution, verified task success, failed-attempt reuse, and deployment/upgrade-experience reuse. “Does the new session need the user to re-explain what Nexus is, where it stopped, and why?” is a central continuity question.

These measures require explicit identities and observation provenance; unavailable observations remain unavailable, not zero. Any confirmatory design needs its own accepted preregistration and authorization.

## 13. Operational learning and self-improvement boundary

Future learning may follow:

`Run / deployment / upgrade → Trace + Evidence → experience candidate → Procedure/Skill/policy candidate → sandbox/evaluation → separately governed promotion`

This is a future workflow, not an implemented subsystem. Candidate generation cannot approve itself, modify authority, or activate a Skill. Any effect on execution remains subject to existing Authority, Verification, Approval, Effect, purge, and regression controls.

## 14. Production data-root policy

If a fresh root is approved, designate it operationally as **`PROJECT_NEXUS_PRODUCTION_ROOT`**. This is a logical local configuration name only; the actual local path belongs in operator-local configuration/launch arguments and must never enter this repository, Panel projection, Trace, or public handoff. Keep it outside the repository checkout and separate from `ui-preview`, test/Academy roots, backups, and evaluation ledgers.

Retain the single-writer invariant. Use the existing command-scoped Panel/operator process against the one configured root; do not add a daemon or concurrent writer. Configure the independent Purge Journal consistently with the root. Backups must be coherent sets of SQLite state, Object payload tree, and matching independent Purge Journal state; a restore must follow the existing RECOVERY/freshness/integrity/replay path, not substitute a journal or open ordinary APIs on a stale copy. Verify restore from a disposable copy before relying on it.

Only a sanitized logical root identity, backup policy, schema/migration status, and safe manifest hashes may be recorded in repository docs. No absolute path, raw payload, credential, Codex state, or private instruction enters Git.

**Initialization status:** `python -m adapters.client --data-root <root>` and the Panel remain existing-root-only. A separate `python -m adapters.bootstrap` implementation now initializes and binds an explicitly supplied instance, then separately bootstraps narrow authority. This implementation exists, but no Project Nexus production root has been created or bootstrapped.

## 15. Project Nexus Self-Hosting Bootstrap Slice 1 proposal

This is a proposed implementation sequence, not permission to run it:

1. **Resolve and authorize root creation:** external operator confirms no existing production root is being overwritten and separately authorizes using the implemented Stage 1/2 bootstrap commands. The bounded root audit found no evidence of an existing production root; it did not search the whole disk.
2. **Prepare the dedicated root and recovery boundary:** establish local-only path, policy, matching independent Purge Journal, single-writer ownership, and tested backup/restore procedure.
3. **Create one real bootstrap Task/Run:** record only the present-day bootstrap action under current Authority/Participation rules.
4. **Import a small authoritative source set:** selected accepted closure/evaluation documents and current implementation/state refs at an exact accepted Git revision. Each imported object records `IMPORTED_FROM_PRE_NEXUS_HISTORY`, source commit/ref and hashes; import does not fabricate old Runs/Traces.
5. **Create Evidence and verify:** use the narrowest existing authorized Core/application path. Exact-byte verification is T1 only. Human semantic review uses existing Approval-bound T3; do not create Memory admission from T1 alone.
6. **Create a current-state Artifact and bounded Memory Candidates:** include current objective/status/blocker/next step plus a few enduring constraints, all with `CURRENT`/historical truth distinction and source lineage. Keep unsupported candidates quarantined; do not admit hypotheses as fact.
7. **Compile one real Context Pack:** bind to a genuine active Task/Run, include only current eligible refs, inspect hash/bytes/source refs and governance through the existing operator projection.
8. **Close and inspect:** use current inspect/Panel surfaces to validate state, lineage, status, and backup identity. No Utility trial is run.

The slice should not import all historical chats, all Academy packets, all Skills, every design alternative, or every source document. The first usable state should be compact enough that a new session can resume from a current summary and follow source refs when needed.

## 16. Stage 3 application composition status

The Project Nexus-specific Stage 3 composition is implemented at `adapters.client.project_nexus_selfhost` and exposed by the existing operator client as `project-nexus-selfhost`. It requires an existing policy-bound instance, completed Stage 2 authority, and a frozen, strictly validated manifest. It is not a generic workflow engine and does not initialize a root, issue Stage 1/2 authority, or execute Git network operations.

Before child mutations, `CodexHostedBridge.create_task_root()` binds its complete semantic request and uses caller-frozen canonical `created_at`. Exact completed requests replay before mutable Authority/Participation gates; the final `READY → RUNNING` child can recover a lost top-level result after a read-only persisted projection check.

Stage 3 binds its complete semantic request in the existing CommandLedger before Work Grant creation or any business mutation. `READY-TO-CLOSE` means the Stage 3 business phase and final pre-terminal projections are complete while the root is `VERIFYING`; it does not mean the root succeeded or the application is complete. Once readiness is committed, retries cannot re-enter import, Verification, Memory, or Context work. If the terminal transition has not committed and the original Work Grant is expired or revoked, recovery stops with `STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE`; it does not renew or replace the grant. If terminal success is already committed, exact recovery can finish revocation and record the Stage 3 completion result without reopening business work.

The application result is `STAGE3_CANONICAL_COMPLETE`: canonical state is complete, the Root Run is `SUCCEEDED`, and the Work Grant is revoked. It is deliberately distinct from `BOOTSTRAP_COMPLETE`. The latter additionally requires a coherent final backup and a successful disposable restore proof; no backup framework or restore gate is executed by the Stage 3 command.

## 17. Risks and non-goals

Risks: incorrectly treating imported history as live telemetry; semantically overclaiming T1 integrity; admitting stale/conflicting Memory; lack of a supersession mutation API; local source-import policy/eligibility limits; the production root remains uncreated; backup/Purge Journal mismatch; single-writer interruption; leaking private local paths or instruction bodies; confusing eval transport with product delivery; claiming Utility improvement from data created after T0.

Non-goals: create a root or write canonical state; direct SQLite or ObjectStore bypass; new Project/Decision/Procedure/Knowledge Graph service or schema; all-history/chat ingestion; automatic Memory admission; skill registration/promotion; autonomous upgrade; daemon/IPC; full Project Reconstruction; Utility v1 trial or task seeding; Phase 6 retry/reopen; Academy/Cerification/Shadow/Production claims; Host portability/DSH/Second Host; Foundation Contract changes.

## 18. Exact next step after review

Independently review the implemented Stage 1/2 and Project Nexus-specific Stage 3 application, especially its request binding, terminal recovery, authority scope, local-source provenance, semantic Memory admission, and current-vs-superseded retrieval. Production root creation and Stage 3 execution require separate explicit authorization. Until then, do not create a production root, import sources, write canonical Project Nexus state, or call the bootstrap complete.

**Current execution state:** `NO BOOTSTRAP EXECUTION AUTHORIZED`.
