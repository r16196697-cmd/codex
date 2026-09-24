# Nexus v2 Implementation Status

Nexus version: `v0.1-development`  
Current implementation step: Step 8 — RUNNING (Codex-hosted attachment integration remains incomplete)
Environment: Windows build `10.0.22631.0`; Python `3.11.0`; SQLite `3.38.4` + FTS5; Git `2.40.0.windows.1`  
Implementation checkpoint / branch: `3ee8e32` / `nexus-v2-runtime` (authorized mode-to-Trace, configured CLI mode-set and INSPECT-protected Task projection integration; Step 7 base `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`)
Schema version: `nexus.* @1`; SQLite migration version `7` (isolated tests)
Policy version: `1` (fail-closed default policy)  
Database version: `7` exercised only in isolated integration databases; persistent runtime database not initialized

Step 0: PASS  
Step 1: PASS  
Step 2: PASS
Step 3: PASS
Step 4: PASS
Step 5: PASS (corrective four-mode Runtime gates, authorized inspect, and mode-to-Trace transaction/replay; 69-test regression passed)
Step 6: PASS
Step 7: PASS
Step 8: RUNNING (Codex-hosted attachment integration; standalone Provider/Broker cases DEFERRED / NOT_CONFIGURED)
Step 9: PARTIAL (mode-to-Trace, controlled inspect, and policy-configured CLI mode-set/reopen pass on disposable data; live Hosted connection and deployment-root acceptance remain incomplete)
Step 10: PARTIAL (70-test regression and an end-to-end old-snapshot → RECOVERY → PurgeLedger replay → index rebuild → verified NORMAL recovery pass on isolated data; T2/T5/T9 remain partial, so release acceptance is not met)

Tests passed:
- 2026-09-25 corrective mode/inspect + client run: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 65 passed; `compileall`, `pip check`, `git diff --check`, CLI `--help` passed. The only `git diff --check` output was Git's LF→CRLF advisory, not whitespace errors.
- 2026-09-25 follow-up revalidation: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 65 passed in 19.736s; `compileall`, `pip check`, `git diff --check`, and CLI `--help` passed. No tracked source edits were made during this audit. Step 9's mode/inspect gates remain regression-PASS; this is not formal T1–T12 acceptance.
- 2026-09-25 mode-to-Trace correction: focused Runtime/Trace/replay/deterministic-runtime/client regression — 32 passed; full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **68 passed** in 21.325s. `compileall`, `pip check`, CLI `--help` and `mode set --help`, and `git diff --check` passed. These checks establish only tested Core behavior, not a live persistent-client deployment or full T1–T12 acceptance.
- 2026-09-25 configured CLI end-to-end: a disposable policy-configured instance completed authorized CLI `mode set SAFE`, persisted the matching TraceEvent, and reopened with mode/event intact. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.392s; `compileall`, `pip check`, CLI help, and `git diff --check` passed. No persistent deployment root was created.
- 2026-09-25 CLI inspect integration: a disposable policy-configured instance used a separate exact `INSPECT` Grant to retrieve the Task/Root-Run projection after the authorized mode-set path; the projection exposed no payload field. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.967s. This is isolated client integration evidence, not deployment-root or live Codex Host acceptance.
- 2026-09-25 full recovery regression: `test_old_snapshot_recovery_replays_purge_ledger_before_normal` created a pre-purge snapshot, entered RECOVERY through an authorized mode command on the isolated snapshot, completed purge in the source instance, restored the snapshot, blocked Core access during RECOVERY, replayed the independent Purge Ledger, rebuilt FTS, verified payload/search unavailability, and only then returned to NORMAL. Full suite — **70 passed** in 23.294s; `compileall`, `pip check`, and `git diff --check` passed. Temporary synthetic data only.
- Four modes: authorized/idempotent/persistent switches; SAFE blocks external reversible Effect commit, memory writes and learning/profile/descriptor changes; STATELESS blocks Memory service reads/writes, disallowed Trace classes and the SQLite Memory table/index access path while preserving allowlisted traces; Recovery blocks Core/inspect, skips startup migration/cleanup, rejects ordinary exit, and exits only after SQLite integrity/migration, Purge Ledger, object SHA-256, index and deletion checks. Pending Purge barrier and corrupted payload tests remain in RECOVERY.
- Inspect: valid Task projection succeeds under exact `INSPECT` task/resource/audience scope; unauthorized Grant and RECOVERY calls deny; Approval hash is redacted unless a distinct `INSPECT_PROTECTED` scope is present; real synthetic Effect UNKNOWN and Purge PARTIAL facts pass through Core inspect APIs and remain UNKNOWN/PARTIAL in the client presentation.
- CLI: mode set reads an optional schema-validated policy JSON, passes the disposable-instance authorized mode/Trace/reopen integration test, and refuses absent data roots without creating `nexus.sqlite`; client facade contains no raw SQLite query code.
- Rollback point before corrective implementation: Step 7 commit `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`. Modified pre-existing files received same-directory timestamp backups; status backup `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-013510` SHA-256 verified.
- Deployment shape remains **Nexus v0.1 — Codex-hosted / Attached**. Independent Model/Search Provider adapters and actual Credential Broker secret resolution remain `DEFERRED / NOT_CONFIGURED`, not PASS and not Core failures.
- No persistent runtime database exists; all new migration, mode, Recovery, inspect and effect/purge displays were tested in isolated temporary roots only.
- Mode Trace binding is now implemented without changing the Trace schema or Foundation Contracts: an authorized mode command requires the Task's actual ORCHESTRATOR Root Run, the same Grant on that Run, `RUNTIME_CONFIGURE` plus `TRACE_APPEND`, and a pre-authorized event-specific classification assertion. The mode event, mode audit row, CommandLedger result, and mode state commit atomically; Root Run replay accepts the typed mode fact without changing Run state. Missing Run, authority, or valid classification fails closed.
- Known Step 8 / Step 10 blockers: the live Codex host is not connected to a running Nexus data root/ingestion bridge; T2, T5, and T9 still have documented partial coverage. The isolated old-snapshot→Recovery→Purge Ledger→index rebuild→NORMAL drill now passes but does not replace those gates.
- Environment audit: Python, Git, SQLite, SQLite FTS5 detected.
- Step 0 snapshot: core global AGENTS/Skill/config files hash-match; config parses; 7 SQLite backup copies pass `PRAGMA integrity_check`.
- Existing Nexus runtime/data: none found in the audited target workspace.
- Step 1: 11 contract unit tests pass; 21 JSON documents parse and all 20 schemas pass Draft 2020-12 meta-schema checks; no external `$ref`; `pip check`, Python compile, whitespace check and secret-pattern scan pass.
- Step 1 dependency: isolated `.venv` resolves the pinned `jsonschema==4.26.0` lock without modifying system Python.
- Step 2: 23 contract/integration tests pass, including raw-byte SHA-256/tamper detection, schema-before-payload rejection, lineage-cycle rejection and closure, CAS race, command idempotency/conflict, single-writer exclusion, immutable envelopes, migration checksum guard, SQLite integrity, and persistent purge-barrier blocking after reopen.
- Step 2 static checks: `pip check` reports no broken requirements; `compileall` passes. `git diff --check` initially found trailing Markdown whitespace in this status file; that whitespace was removed before checkpointing.
- Step 3: 32 contract/integration tests pass, including T4-style trust-root/chain/scope/audience/depth denials, principal/grant revoke revalidation, target/Effect/payload-bound approval, classification lowering approval, fail-closed classified egress, and concurrent atomic Task-budget reservations/idempotent settlement/release.
- Step 3 static checks: all 25 schemas and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass. No credentials/provider connection was used.
- Step 4: 38 contract/integration tests pass. Task/Root Run creation and state transitions commit atomically with append-only Trace sequence and CommandLedger; replay after reopen matches Run and Task projections; retry after simulated response loss returns the original event result; a forced Trace insert failure rolls back state and ledger together.
- Step 4 security/admission: only allowlisted event types and event-specific scalar metadata are admitted; secret-pattern, payload-like key, nested metadata, forged transition and wrong-actor attempts leave no Trace rows; high-classification objects cannot be downgraded and Trace contains refs rather than payload bytes.
- Step 4 static checks: all 25 JSON Schemas and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass.
- Step 5: 41 contract/integration tests pass, including deterministic DAG routing/scheduling for ORCHESTRATOR, MODEL and TOOL identities; E0/E1/E2 quality floors, locality/provider/classification/context/budget hard gates; exact child Manifest binding before READY; TaskContract/LogicalRef and schema-bound inputs; authority-descended Run grants; atomic budget reservations; scheduler command replay; dependency gating; Trace-backed Subtask replay; and rejection/no persistence of an unreviewed external-write descriptor.
- Step 5 static checks: all 30 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass. Targeted Step 5 + Trace suite: 9 passed.
- Environment note corrected to match the observed source layout (`kernel/`, `adapters/`, `schemas/`, `migrations/`, `tests/`); no source was moved.
- Step 6: 43 contract/integration tests pass. Fake dispatcher response loss becomes UNKNOWN, same-command retry does not redispatch, the configured authoritative fake channel resolves using evidence, and budget settlement is bounded/idempotent. A separate compensation Effect and COMPENSATES relation commits without changing the original COMMITTED fact. Payload-hash-mismatched ApprovalDecision and revoked parent Grant both prevent dispatch.
- Step 6 static checks: all 30 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass.
- Step 7: 47 contract/integration tests pass. T1 SHA-256 integrity results are not treated as semantic truth and remain quarantined; T3 human/domain verification requires an ApprovalDecision bound to exact target/evidence integrity hashes. Only independent evidence/method plus approval enters Admitted Memory. Raw History and Admitted Memory use separate FTS5 indexes; SQLite backing rows keep refs/classification/expiry only, while indexed result text is loaded from the immutable filesystem object. Run action/resource, data classification/handling tags, retention expiry, and purge barriers gate search and indexing.
- Step 7 Purge: plan hash binds a persisted plan and ApprovalDecision; external append-only hash-chained journal is outside the data root. RunManifest input refs are persisted as immutable input bindings and derived-from lineage; new bindings to protected inputs fail, and a previously-created but unstarted Run cannot enter READY while its input is barriered or PURGED. An active Run (even if a quiescence callback falsely reports success) or UNKNOWN Effect leaves Purge PARTIAL and barrier active. A positive barrier/quiescence/purge path deletes the payload, redacts envelope/hash/path and removes both index entries. Old-backup restore first reinstalls the barrier, replays all independent ledger events, deletes restored payloads, clears/rebuilds the contentless FTS indexes, and does not expose purged content. Restoring a PARTIAL journal state retains the barrier and does not falsely delete payloads.
- Step 7 static checks: all 33 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check` pass. Test DBs are disposable; no persistent database, external provider, real history, secret, or network was used.
- Step 8 stop-gate revalidation (no adapter/provider code changed): the committed Steps 1–7 baseline reran with 47 tests passed; `pip check`, `compileall`, and `git diff --check` passed. These checks do not satisfy Step 8's real-provider/T10/T11 acceptance criteria.
- Operator disposition: current deployment is **Nexus v0.1 — Codex-hosted / Attached**. Codex is the current AI Executor/Host; Nexus owns governed local state. Independent Model/Search Provider adapters, paid API credentials, and the Credential Broker's real-secret path are `DEFERRED / NOT_CONFIGURED`, not PASS and not a Core failure.
- Step 8 must still prove the Hosted bridge: verified executor/capability facts and results enter Task/Run/Artifact/Evidence/Verification/Trace without fabricating unavailable underlying model/provider identifiers. Existing v1 model schema currently requires such identifiers on MODEL manifests; this compatibility gap is under evaluation and must be resolved without weakening frozen semantics.
- Step 9/10 continuation audit: 47 existing contract/integration tests reran and passed; `pip check`, `compileall`, 33 schema + default-policy validation passed. `git diff --check` initially caught trailing whitespace in the newly edited status line; this line is removed in this update. This suite is not the manual's recorded T1–T12 Acceptance Suite.

Tests failed:
- Step 1 first contract run had 4 errors because the initial cross-file schema references attempted network retrieval and `allOf` rejected common fields. Replaced with local self-contained references and `unevaluatedProperties`; final 11-test suite passes.
- Step 2 first integration runs exposed a hash-profile FK mismatch, open test handles, migration trigger parsing, and a child-process test harness issue; these were corrected. An early Step 2 test also expected an orphan payload for a missing lineage source; after enforcing validation before payload write, the test was updated to assert no payload or metadata is persisted.
- Step 3 first runs exposed a policy-schema directory assumption and a stale test expectation for a single migration; both were corrected. A classification egress test initially supplied an assertion ID rather than an object ID; the API was tightened to derive classification only from persisted object envelopes. A final immutable-ledger test initially queried the losing concurrent reservation ID; it now selects the actual winning ledger command. The final 32-test suite passes.
- Step 4 initial tests caught event creation ordering, schema-version expectation, high-classification fixture wiring, and the need to rollback Run state if Trace append fails; fixes are covered by the final 38-test suite.
- Step 5 development tests exposed composite DAG-edge identity, scheduling preflight ordering, child Manifest/schema constraints, and projection recovery details; these were corrected and the final 41-test suite passes.
- Step 6 development tests exposed the Step 5 read-only Descriptor scheduling boundary, migration-version expectations, commit-in-progress DB constraints and fixture identity bindings; these were corrected and the final 43-test suite passes.
- Step 7 development tests caught a PurgePlan insert placeholder/FK ordering error, invalid connection-context handling, duplicate command-ledger writes on PARTIAL/COMPLETED, a dynamic closure-query parameter mismatch, and SQLite 3.38's lack of the newer FTS contentless-delete option. Fixes now use validated transaction paths, barrier-aware RunManifest input guards, and contentless FTS with safe purge-time rebuild; all cases are covered by the final 47-test suite.

Open blockers:
- Independent real Model/Search Providers and Credential Broker are intentionally deferred by operator decision. Current deployment shape is **Codex-hosted Nexus**: Codex supplies the client/model execution environment; Nexus provides local governance, state, Trace, Memory, verification and integrity. This is not a Core failure and is not Step 8 PASS.
- Step 8 hosted bridge is not implemented/connected: current Codex session tools cannot be shown to ingest a real Host execution into a running Nexus Task/Run/Artifact/Evidence/Trace here; exact underlying model identifiers must remain absent rather than guessed. This specific Hosted integration remains open; independent Provider APIs remain deferred.
- Step 9 is **PARTIAL**, not blocked on its former Runtime safety prerequisite: four-mode gates, authorized inspect APIs, mode Trace linkage/replay, client status rendering, and a policy-configured CLI mode-set/reopen flow pass on isolated data. No deployment-root instance is initialized and the live Codex Host execution/inspect chain is not integrated.
- Step 10 Kernel Acceptance is **PARTIAL**. The full isolated regression and the end-to-end old-snapshot RECOVERY/PurgeLedger/index-rebuild drill pass; T12's recovery path is now exercised, but T2/T5/T9 still have documented gaps and there is no persistent deployment root. This is not release acceptance or PRODUCTION_READY.
- Existing `auth.json` / `.env` files remain unread and are not Nexus credential sources. No persistent runtime database or real records exist. Prior Steps 0–7 checkpoints are committed on `nexus-v2-runtime`.

Known UNKNOWN Effects: None; Nexus runtime/data not initialized.  
Pending Purge: None.  
Pending migration: `0007_runtime_modes.sql` is test-applied only; there is no persistent runtime database to migrate.
Rollback point: Step 7 checkpoint `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.
Last verified state: 2026-09-25 authorized CLI Task-inspect projection committed as `3ee8e32`; old-snapshot RECOVERY/PurgeLedger/index-rebuild test and **70-test** full regression pass in the working tree (23.294s). `compileall`, `pip check`, and `git diff --check` pass. Persistent Nexus DB/config not initialized; deployment remains DEVELOPMENT.
Next allowed action: complete the Codex-host attachment bridge to the extent supported by authoritative Host facts, then run Step 9 client tests on an initialized isolated instance and execute the formal Step 10 T1–T12/recovery acceptance. Independent Providers remain deferred.

## IMPLEMENTATION STEP 2 — PASS

Goal:
Implement the SQLite schema/migrations and payload storage foundation: immutable SHA-256 objects, HashProfile, ObjectRelation cycle prevention, LogicalRef CAS, CommandLedger idempotency and persistent PurgeBarrier checks.

Inputs:
Step 1 schemas, policy, pinned JSON Schema validator and fixtures; isolated throwaway test databases and fake payloads only.

Allowed files:
`kernel/object/`, `adapters/storage/`, `migrations/`, Step 2 object-store schema files in `schemas/`, `tests/contract/`, `tests/integration/`, and this status file.

Forbidden files:
Global or business-project `AGENTS.md`; global Skills/config; user-level databases; secrets; real Trace/payload; model/search/runtime adapters; frozen contract semantics; test truth/evaluator changes.

Expected outputs:
Versioned SQLite migrations; storage API that verifies SHA-256 and atomically places payload; acyclic lineage enforcement; revision CAS; schema-validated CommandLedger replay/conflict behavior; schema-validated persistent PurgeBarrier/PurgeLedger foundation; isolated integration fixtures.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T2/T6/T8 subcases for bytes tampering, lineage cycles, CAS conflict, same-command retry/conflict and barrier blocking.

PASS criteria:
Payload tampering/missing payload never verifies or reports success; lineage cycles reject transactionally; only one CAS succeeds for the same expected revision; same command and request replays the stored result, same ID/different request is COMMAND_CONFLICT; barrier blocks all derived writes and persists through reopen; migration integrity and tests pass.

FAIL handling:
Fixed within Step 2; no dependent step may proceed until this checkpoint is committed and the tests remain green.

Rollback point:
Verified Step 1 checkpoint `679162116b3ecf8a64f8b77f53a67143636c50b8` on branch `nexus-v2-runtime`; base commit `ac61316178d7e145ed420de2fbb9ee0273067263` and Step 0 snapshot path recorded above.

Do NOT:
- Add a sixth Foundation Contract.
- Weaken immutable object, ledger idempotency, CAS or Purge barrier requirements.
- Install optional frameworks to conceal failure.
- Connect a real model, external write tool, MCP integration or production data.

## STEP 2 — IMPLEMENTATION REPORT

Implemented:
- Versioned SQLite metadata migration, SHA-256 content-addressed immutable payload storage, hash profile and lineage relations.
- LogicalRef compare-and-swap, CommandLedger idempotency/conflict handling, and persistent PurgeBarrier/PurgeLedger foundation.
- Single-writer lock, migration checksum validation, integrity verification, and startup cleanup of only unreferenced payloads in the isolated Nexus data root.
- Schema/lineage validation occurs before payload writes; failed schema or missing-source checks leave no payload or metadata.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 23 passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- `git diff --check` — passed after removing status Markdown trailing whitespace.

Evidence:
- Tests use disposable temporary directories and fake bytes; no persistent Nexus data/database was initialized.
- SQLite `PRAGMA integrity_check` returned `ok` in integration coverage.

Files changed:
- `adapters/storage/`, `kernel/object/`, `migrations/0001_initial.sql`, three storage contract schemas, `tests/contract/`, `tests/integration/`, and this status file.

Known limitations:
- Authority, trace/replay, runtime, effects, verifier/memory admission, and adapters remain unimplemented; the data root is test-only.

Rollback point:
- Step 1 commit `679162116b3ecf8a64f8b77f53a67143636c50b8`; Step 0 snapshot recorded above.

Next:
STEP 3 — Identity / Authority / Approval / Budget

## IMPLEMENTATION STEP 3 — PASS

Goal:
Implement the fail-closed authority chain, scoped ApprovalDecision checks, Credential Broker boundary interface, classification/egress authorization gate, and one-account-per-Task atomic budget reservations without connecting real secrets or external providers.

Inputs:
Step 1–2 committed schemas, fail-closed policy, SHA-256 object layer, CommandLedger, and isolated test databases.

Allowed files:
`kernel/authority/`, `kernel/budget/`, `adapters/credentials/` (interface only), `adapters/storage/sqlite_store.py` (classification-ref integrity only), a new `migrations/0002_authority_budget.sql`, new Step 3 schemas and backed-up `policies/nexus.policy@1.schema.json` (egress destination-rule schema only), `tests/contract/`, `tests/integration/` (including object-store regression updates), and this status file.

Forbidden files:
`migrations/0001_initial.sql`; previously committed Foundation Contract schemas except backed-up, compatibility-preserving corrections needed by this card; global or business-project `AGENTS.md`; global Skills/config; user databases; real credentials; real Trace/payload; real model/search/provider adapters; any external write path; and all files outside the allowed list.

Expected outputs:
`validate_delegation_chain()`, `compute_effective_authority()`, and `evaluate_authorization()` enforce configured trust anchors, principal/parent continuity, grant status/expiry/revocation, scope/resource/action/audience containment, depth bounds, and fail-closed unknowns. Approval binds approver/action/target/effect and optional payload integrity hash/scope/policy/expiry; authorization can be rechecked at commit and changed payload invalidates approval. Broker is a boundary contract only and never persists/prints credentials. Each Task has exactly one hard-limited BudgetAccount; atomic Run reservations cannot overspend under concurrency; CommandLedger protects mutating operations.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T4 (untrusted root, broken/expired/revoked/expanded chains, audience/resource/action denials, depth), T6 (classification inheritance and egress), T7 (parent revoke and payload-hash/effect-bound approval), and T8 (concurrent reservations and hard ceiling); `pip check`, `compileall`, schema meta-validation, `git diff --check`, and secret-pattern scan.

PASS criteria:
All invalid authority/approval requests fail closed and append no secret material; child effective permissions are a subset of parent permissions; revocation before commit denies while committed facts remain unchanged; payload hash change invalidates approval; concurrent reservations satisfy `reserved+consumed≤limit` and one Task cannot acquire a second account; all tests/static checks pass.

FAIL handling:
Failures were fixed within Step 3; preserve the five frozen contracts and do not proceed to Step 4 until this checkpoint is committed.

Rollback point:
Step 2 checkpoint `b09251f1318cc23b66643887506dbccc524bafb8`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.

Do NOT:
- Add a sixth Foundation Contract or broaden any grant.
- Trust an arbitrary chain root, a ToolDescriptor, model output, boolean approval, or client-side budget check.
- Retrieve real secrets, connect a real provider, or enable external writes.
- Modify the committed `0001_initial.sql` migration; create a forward migration instead.

## STEP 3 — IMPLEMENTATION REPORT

Implemented:
- Persisted Principal, TrustAnchor, DelegationGrant, ApprovalDecision and ClassificationAssertion state with immutable audit/event rows and constrained transitions.
- `validate_delegation_chain()`, `compute_effective_authority()`, and `evaluate_authorization()` fail closed on untrusted roots, broken/expired/revoked chains, policy version drift, depth overflow, and task/resource/action/audience expansion.
- Commit-time reusable authorization check binds ApprovalDecision to human approver, action, target, Effect, payload SHA-256, policy and expiry. Revocation invalidates future authorization; historical approvals and committed facts are not rewritten.
- Classification inheritance and lowering controls; outbound eligibility derives labels from persisted object references and a deny-by-default destination policy.
- Credential Broker protocol boundary only; no OS credential adapter or secret retrieval is active.
- One immutable-limit BudgetAccount per Task; transactional Run reservations, caps, settlement/release and append-only ledger with idempotent command handling.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 32 passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- JSON Schema/default-policy validation, `git diff --check`, and secret-pattern scan — passed.

Evidence:
- All behavior tests use disposable temporary databases, synthetic principals, and fake payloads. Concurrency race verified one of two over-budget reservations is denied.
- No persistent runtime database, real credentials, or network/provider connection was created.

Files changed:
- `kernel/authority/`, `kernel/budget/`, `adapters/storage/sqlite_store.py`, `migrations/0002_authority_budget.sql`, three budget schemas, the egress policy schema, and contract/integration coverage.

Known limitations:
- Credential Broker remains an interface; real credential-store selection is deferred to provider integration. Trace and external Effect commit path do not yet exist; Step 6 must call authorization revalidation.

Rollback point:
- Step 2 commit `b09251f1318cc23b66643887506dbccc524bafb8`; Step 0 snapshot recorded above.

Next:
STEP 4 — Trace / State / Replay

## IMPLEMENTATION STEP 4 — PASS

Goal:
Implement Task/Run state, append-only classified Trace admission, transactional state-transition/event/CommandLedger writes, and deterministic replay without adding models, tools, or external telemetry.

Inputs:
Committed Step 1–3 schemas and policy; SQLite migration version 2; ObjectStore classification lookup; AuthorityService; disposable test DBs only.

Allowed files:
`kernel/trace/`, `kernel/run/`, `adapters/storage/sqlite_store.py`, a new `migrations/0003_trace_state.sql`, new Step 4 schemas (existing schema changes require same-directory backup and must preserve Step 1 contracts), `tests/contract/`, `tests/integration/`, and this status file.

Forbidden files:
Migrations `0001` and `0002`; global or business-project `AGENTS.md`; global Skills/config; user databases; real credentials; real payloads/Trace; any real model, search, tool, MCP or external telemetry adapter; and any change to frozen contract semantics.

Expected outputs:
Schema-validated Task and ORCHESTRATOR/MODEL/TOOL Run state; legal transition matrix; `transition_run(command_id,expected_state,next_state)` looks up CommandLedger first and commits state, next event sequence, event and command result atomically; replay rebuilds projections from append-only Trace facts. Trace admission applies per-event metadata allowlists, rejects payload-like fields and secret patterns, enforces policy byte/depth/count limits, validates classifications and referenced object boundaries, and stores references only. OTel export port remains independent and DENY by default.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T2 response-loss/replay and illegal-transition cases; T6 classification inheritance and trace metadata rejection; recovery/reopen replay; then `pip check`, `compileall`, all-schema/default-policy validation, `git diff --check`, and secret-pattern scan.

PASS criteria:
Duplicate command with identical canonical request returns the original resulting state/event sequence before checking stale expected state; new command with stale state conflicts; no invalid/secret/payload event enters SQLite; Run/Trace classification is not downgraded; replay from empty projections reproduces persisted state and sequence; all tests/static checks pass.

FAIL handling:
Initial failures were fixed within Step 4; keep event admission independent of OTel export and do not advance until its checkpoint is committed.

Rollback point:
Step 3 checkpoint `59f077179c5f895c8dc3e28b5ac292ce3eeb775b`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.

Do NOT:
- Put full prompts, outputs, documents, tools results, secrets, or arbitrary previews in Trace.
- Call any real model or Tool; Trace replay must pass before those adapters.
- Let OTel acceptance imply permission to export.
- Add a sixth Foundation Contract or change the three executor kinds.

## STEP 4 — IMPLEMENTATION REPORT

Implemented:
- Task creation, ORCHESTRATOR/MODEL/TOOL Run identities, task/root linkage, legal Run/Task state transitions and append-only `TraceEvent` storage in migration `0003`.
- Run create/transition event and projection updates plus `CommandLedger` result commit in one SQLite transaction; command lookup precedes stale-state and authorization rechecks on replay.
- Allowlisted event-specific typed metadata, scalar/size/depth/count/secret guardrails, actor-chain checks, classification/data-boundary inheritance, unavailable/purged object rejection, and payload-free object refs.
- Deterministic Run and Task replay projections; independent OTel egress remains unimplemented and DENY by default.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 38 passed.
- Simulated Trace insert failure proved Run status, event and command result roll back together.
- Reopen/replay, stale-state retry, metadata rejection, forged transition rejection and classification downgrade rejection all passed.
- `pip check`, `compileall`, all-schema/default-policy validation, `git diff --check`, secret-pattern scan — passed.

Evidence:
- Temporary SQLite databases only; no real payload, provider, tool, telemetry or persistent runtime data.

Files changed:
- `kernel/run/`, `migrations/0003_trace_state.sql`, `tests/integration/test_trace_state.py`, plus Step 2 migration-count regression test.

Known limitations:
- DAG scheduling, RunManifest binding, ToolDescriptor/ModelProfile eligibility and richer internal event producers remain Step 5+; live provider/telemetry remains disabled.

Rollback point:
- Step 3 commit `59f077179c5f895c8dc3e28b5ac292ce3eeb775b`; Step 0 snapshot recorded above.

Next:
STEP 5 — Deterministic Runtime

## IMPLEMENTATION STEP 5 — PASS

Goal:
Build the deterministic Task/Root scheduler and acyclic subtask DAG, executor-specific RunManifest binding, and simple fail-closed node-level routing without executing any real Model or Tool.

Inputs:
Committed Steps 1–4 schemas, object/classification store, authority/budget services, Task/Run/Trace replay, and disposable fixtures.

Allowed files:
`kernel/runtime/`, a new `migrations/0004_runtime.sql`, new Step 5 schemas in `schemas/`, `schemas/nexus.task_contract@1.schema.json` for backward-compatible TaskContract input-object refs only, `kernel/run/service.py` for manifest-before-READY and replay-linked Subtask state invariants, `adapters/storage/sqlite_store.py` only for invariant enforcement, `tests/contract/`, `tests/integration/`, `docs/operator/environment.md` for correcting the audited source layout, and this status file.

Forbidden files:
Migrations `0001`–`0003`; global or business-project `AGENTS.md`; global Skills/config; user databases; secrets; real Trace/payload; real model/tool/search adapters; network egress; and changes to the five Foundation Contract semantics or `executor_kind` values.

Expected outputs:
TaskContract/Object + Task LogicalRef CAS, acyclic DAG nodes with declared input/output schema, validation method and budget; ORCHESTRATOR Root owns Task/DAG/scheduling and never calls Model/Tool; MODEL and TOOL Child Run each have correct executor-specific Manifest; Manifest is immutable, readable NexusObject and must be bound before READY. Fixed deterministic E0/E1/E2 selection policy records RouteDecision and hard constraints before budget reservation; fake executors only.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T1 (Task→Root→MODEL/TOOL child identities and Manifest conditionals), T2 (schedule/idempotent checkpoint), T8 (budget reservation before execution), T10 (DAG cycle, node-level eligibility, route recording, hard constraint and child boundary); all-schema validation, `pip check`, `compileall`, `git diff --check`, secret-pattern scan.

PASS criteria:
Root Run remains ORCHESTRATOR with no model fields; MODEL/TOOL child and Manifest schemas match exactly; cyclic/incomplete DAG rejected; no Run enters READY without immutable Manifest; only eligible models are selected under hard user/privacy/budget constraints; route and reservation records are deterministic and replayable; no executor contacts a real provider/tool.

FAIL handling:
Fix only Step 5 runtime/schema/tests; do not connect real adapters before Step 4 replay and fake execution pass.

Rollback point:
Step 4 checkpoint `1338b0080b55f0faa2183c5828e91d526c8bf8fb`; Step 0 snapshot recorded above.

Do NOT:
- Implement Learned Router, model SDK, real tool, SearchProvider or external writes.
- Let Root Run execute inference or tool code.
- Replace Task budget with recursive parent/child accounts.
- Bind manifests by mutating their payloads.

## STEP 5 — IMPLEMENTATION REPORT

Implemented:
- TaskContract NexusObject and Task-scoped LogicalRef CAS; validation of input-object integrity/classification and contract-to-Task/Budget identity.
- Immutable, cycle-free, indexed Task DAG with schema-bound typed inputs/outputs, dependency edge integrity, declared validation method, executor choice, quality/risk constraints and per-node budget bounds.
- ORCHESTRATOR Root Run owns DAG/scheduling; MODEL/TOOL Runs are descendants with executor-specific immutable manifests, budget reservations and authority chains. Run cannot enter READY without a valid manifest and all bound objects.
- Deterministic node-level E0/E1/E2 routing with hard provider/locality/network/modality/privacy/context/output-mode/budget constraints, persisted RouteDecision and no confidence-only downgrade. Only reviewed READ_ONLY ToolDescriptors are admitted in Step 5.
- Idempotent schedule command/recovery and dependency gating; Run transitions and Subtask projections update transactionally and can be checked by Trace replay.
- Corrected the Step 0 environment note about source placement without moving any files.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 41 passed.
- `.venv\Scripts\python.exe -m unittest tests.integration.test_deterministic_runtime tests.integration.test_trace_state -v` — 9 passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- All 29 schema files plus the policy schema passed Draft 2020-12 validation; default policy validates.
- `git diff --check` — passed (only Git LF→CRLF informational warnings); secret-pattern scan — no matches.

Evidence:
- All tests use isolated temporary SQLite databases and synthetic identities/objects. Migration v4 is exercised only in tests; no persistent runtime database, model/provider/tool invocation, network call, external effect, or secret access occurred.
- An external-write descriptor was rejected before registration; query confirmed no row was persisted.
- No fake executor output was generated: this step schedules/records deterministic Run manifests only. Actual fake execution/output handling belongs to later governed runtime work and is not claimed here.

Files changed:
- `kernel/runtime/`, Step 5 migration and schemas, `kernel/run/service.py`, backward-compatible TaskContract schema additions, integration fixtures/tests, `docs/operator/environment.md`, and this status file.

Known limitations:
- No execution adapter is invoked. No real model, tool, SearchProvider, or external write path is enabled. Credential Broker/key-store decision remains a required precondition before Step 8.

Rollback point:
- Step 4 commit `1338b0080b55f0faa2183c5828e91d526c8bf8fb`; Step 0 snapshot recorded above. The Step 5 checkpoint commit is the next repository operation.

Next:
STEP 6 — Effect Gate

## IMPLEMENTATION STEP 6 — PASS

Goal:
Implement governed Effect preparation, commit/UNKNOWN semantics, bounded authoritative reconciliation, and independent compensation Effects/relations; all external interactions are synthetic fake-service fixtures.

Inputs:
Committed Steps 1–5, especially ApprovalDecision/payload-bound authorization, Task budget reservations, Trace replay, immutable Object storage and reviewed ToolDescriptor contracts. Disposable SQLite DBs and fake receipts only.

Allowed files:
`kernel/effect/`; a new forward-only `migrations/0005_effect_gate.sql`; new Step 6-only reconciliation schema(s) under `schemas/` if no existing schema expresses the required persisted facts; `kernel/runtime/service.py` only to allow scheduling of approved mutating descriptors without providing any bypass around Effect Gate; `kernel/run/service.py` only to add schema-validated internal Effect Trace event types while keeping public event writes restricted; `tests/contract/`, `tests/integration/`, and this status file.

Forbidden files:
Migrations `0001`–`0004`; existing frozen Foundation Contract schemas `nexus.effect@1`, `nexus.effect_relation@1`, Approval, Grant, Run and Trace schemas; migrations already applied; global or business-project `AGENTS.md`; global Skills/config; persistent/user databases; secrets; real providers/tools/network/external writes; and any change that rewrites a committed Effect fact or conflates the three axes.

Expected outputs:
Typed Effect records with independent `execution_state`, `effect_outcome`, and `reconciliation_status`; authorization and ApprovalDecision revalidation immediately before commit; stable idempotency key and fail-closed UNKNOWN on ambiguous dispatch; authoritative reconciliation only through a configured synthetic channel with a finite attempt/deadline bound, otherwise HUMAN_REQUIRED/BLOCK_AND_ALERT; compensation creates a new Effect plus COMPENSATES relation and never mutates the original outcome; append-only facts and recoverable command replay.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T3 (UNKNOWN never auto-resubmits; authoritative reconciliation; original COMMITTED preserved after independent compensation; three axes remain independent; ToolDescriptor cannot grant permission), T7 (Approval binds payload/effect and parent revocation before commit denies), plus migration/reopen/replay recovery; all-schema/default-policy validation, `pip check`, `compileall`, `git diff --check`, secret-pattern scan.

PASS criteria:
Ambiguous fake dispatch persists UNKNOWN and retry cannot resubmit; bounded authoritative channel may reconcile only with authoritative evidence; unavailable/ambiguous channel blocks for a human; commit performs fresh Grant and Approval checks against exact payload hash/target/effect; compensation is separately authorized, approved as required, budgeted and recorded as an independent Effect/relationship; original outcome remains immutable; all tests/static checks pass without any real external call.

FAIL handling:
Fix only Step 6 and its migration/tests; do not enter Step 7 until T3/T7 behavior and recovery tests pass and this checkpoint is committed.

Rollback point:
Step 5 checkpoint `ac208477d70d623ebb7deb44b0dd55dc2aadfdc2`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.

Do NOT:
- Treat timeout as failure or automatically retry a commit.
- Use ToolDescriptor as a permission source.
- Update an original COMMITTED Effect to COMPENSATED.
- Call a real external endpoint or install an orchestration framework.

## STEP 6 — IMPLEMENTATION REPORT

Implemented:
- Forward migration `0005_effect_gate.sql` with immutable Effect identity/idempotency key, three independent state axes, constrained transitions, synchronized Effect JSON projection, immutable reconciliation attempts and COMPENSATES relations.
- Effect preparation and commit require an active TOOL Run, exact RunManifest Descriptor/version, matching immutable payload hash/classification, active Run budget reservation, and fresh scoped authority; external commit revalidates exact action, target, Effect ID, payload hash and ApprovalDecision under the SQLite write lock.
- Persisted `COMMITTING/UNKNOWN` before dispatch; ambiguous outcomes never trigger another dispatcher call. A retried in-progress command closes conservatively to UNKNOWN. Dispatcher returns settle the existing Tool Run reservation conservatively; UNKNOWN remains explicit.
- Bounded reconciliation uses only an injected channel whose identity matches the reviewed Descriptor and declares itself authoritative. Missing/untrusted channels produce HUMAN_REQUIRED; attempts are capped by policy. Reconciliation evidence is append-only and replayed command IDs do not re-query.
- Compensation is a second authorized Effect on a distinct TOOL Run with its own reservation and a `COMPENSATES` relation. Original COMMITTED outcome remains immutable.
- The scheduler and READY-manifest validation admit reviewed mutating ToolDescriptors for scheduling only; no path bypasses Effect Gate for dispatch.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 43 passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- All 29 schema files plus the policy schema passed Draft 2020-12 validation; default policy validates.
- `git diff --check` — passed (Git reports only informational LF→CRLF normalization); secret-pattern scan — no matches.

Evidence:
- Isolated temporary SQLite databases and synthetic identities/payloads only. Migration v5 is applied only to disposable tests; no persistent DB, real credentials, network, provider or external system was used.
- UNKNOWN response-loss path called the fake dispatcher once; same-command replay retained one dispatch. An authoritative fake reconciliation record resolved it. Original and compensation Effects remain independently COMMITTED; the relation and separate budget consumption were queried from SQLite.
- Approval payload mismatch and parent Grant revocation were exercised at commit time; neither invoked the fake dispatcher.

Files changed:
- `kernel/effect/`, `migrations/0005_effect_gate.sql`, internal Effect Trace allowlist in `kernel/run/service.py`, scheduling-only reviewed mutation support in `kernel/runtime/service.py`, integration tests/migration expectation, and this status file.

Known limitations:
- Dispatch/reconciliation are in-process fake ports only; no real external service was configured or contacted. A real service requires an independently reviewed authoritative reconciliation channel and a confirmed credential-store decision. Network-egress descriptors remain subject to the deny-by-default egress gate.
- This step does not claim production readiness and does not initialize a persistent Nexus runtime database.

Rollback point:
- Step 5 commit `ac208477d70d623ebb7deb44b0dd55dc2aadfdc2`; Step 0 snapshot recorded above.

Next:
STEP 7 — Verifier + Memory Admission + FTS + Purge

## IMPLEMENTATION STEP 7 — RUNNING

Goal:
Implement deterministic verification/truth metadata, governed Memory Candidate admission with quarantine and separate raw/admitted FTS indexes, plus PurgePlan/Execution barrier/recovery that cannot resurrect purged data.

Inputs:
Committed Steps 1–6, SHA-256 immutable Objects/lineage, Task classifications, Trace replay, durable PurgeBarrier/PurgeLedger foundation and Effect UNKNOWN gate. Synthetic objects and temporary SQLite data only.

Allowed files:
`kernel/verification/`, `kernel/memory/`, `kernel/purge/`; a new forward migration `migrations/0006_memory_purge.sql`; new schemas for VerificationResult, MemoryCandidate/Record, PurgePlan/PurgeRecord only where no frozen v1 schema already defines the required semantics; `adapters/storage/sqlite_store.py` only for barrier-governed payload lifecycle/FTS maintenance; Step 7 policy additions only if compatible with frozen defaults and recorded as a new policy version; `tests/contract/`, `tests/integration/`, and this status file.

Forbidden files:
Migrations `0001`–`0005`; existing frozen Effect, Approval, Grant, Object, Trace, PurgeBarrier/PurgeLedger, Run and Task contracts; global/business `AGENTS.md`; global Skills/config; user data/DB; real retained conversations; secrets; real model, search, network or external write adapters; vector DB/graph/learned memory; and any change that treats a generator model swap as independent evidence.

Expected outputs:
Deterministic VerificationResult separated from Truth State and recording generator/evidence/method independence; candidate/evidence lineage and expiry/review metadata; quarantined candidate excluded from normal grounding; retention-bound raw-history and admitted-memory FTS5 virtual tables with authorization/classification/purge filters; PurgePlan hash bound to lineage revision; atomic barrier install, active Run quiescence and in-flight Effect handling, closure verification, governed payload/index/derived-ID deletion, PurgeLedger replay and restore validation; PARTIAL retains its barrier and is never reported as complete.

Exact tests:
`.venv\Scripts\python.exe -m unittest discover -s tests -v`; targeted T1 verifier/admission (deterministic evidence and three independence axes), T6 object lineage/classification propagation, T9 purge barrier blocks derived-object and both-index writes, active Run/UNKNOWN Effect does not get falsely quiesced, PARTIAL retains barrier, crash/reopen/rebuild indexes, restore replays PurgeLedger and confirms deleted payload/search results remain unavailable; all-schema/default-policy validation, `pip check`, `compileall`, `git diff --check`, secret-pattern scan.

PASS criteria:
Only independently supported or policy-approved candidates enter normal grounding; conflicting/insufficient candidates are UNKNOWN/INCONCLUSIVE or quarantined, never auto-promoted; Purge target/descendant/index scope is stable under a revision-checked barrier; active runs and UNKNOWN Effects prevent false completion; after payload and both indexes are purged, restoring an old test backup and replaying ledger/barrier cannot make the item visible; all checks pass in disposable data only.

FAIL handling:
Fix only Step 7 and its disposable fixtures; if safe quiescence, lineage closure, identifier governance or restore replay cannot be demonstrated, keep the barrier and mark FAIL/PARTIAL, do not enter Step 8.

Rollback point:
Step 6 checkpoint `0fbc795943c166894d31ca8beca9456f05028729`; Step 0 snapshot recorded above.

Do NOT:
- Install a vector DB, graph store, learned memory or real Search Provider.
- Allow quarantine into normal grounding or purge during unresolved Effects/active Runs.
- Release a barrier while deletion/restore verification is incomplete.

## STEP 7 — IMPLEMENTATION REPORT

Implemented:
- Added deterministic SHA-256 VerificationResult and a payload-hash-bound human/domain attestation path. T1 integrity alone cannot assert semantic truth or admit memory.
- Added a separate Truth Policy gate for Memory Candidate admission; T1-only, unknown, conflicting, or insufficiently independent candidates remain QUARANTINED and excluded from normal grounding. T3 verification is re-authorized when used for admission.
- Added separately governed Raw History and Admitted Memory FTS5 indexes. FTS is contentless; SQLite backing tables contain object refs, classification, and expiry metadata rather than duplicate payload bodies. Retrieval checks run action/resource authority, classification level/tags, expiry and purge barrier.
- Added immutable Candidate/evidence records, expiry normalization, candidate status transitions, independent verifier axes, and purge deletion across payload, FTS, and sensitive object envelope metadata.
- Added revision-bound persisted PurgePlan, approval bound to exact plan hash, independent external hash-chained purge journal, immutable RunManifest input bindings/lineage, database enforcement against starting newly-created Runs on barriered or purged inputs, Run quiescence recheck, UNKNOWN Effect blocking, PARTIAL barrier retention, and old-backup PurgeLedger replay.

Tests executed:
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 47 passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- All 33 JSON schemas and the default policy validate; migration v6 applies to disposable SQLite databases with FTS5.
- `git diff --check` — passed; Step 7 recovery tests confirm both successful purge and held PARTIAL replay.

Evidence:
- Disposable synthetic SQLite/filesystem only. No persistent Nexus DB or user history initialized, no real provider/search/tool/network used, no secrets committed.
- T3 approval payload mismatch denied; T1-only and false quiescence tests remained quarantined/partial. Restore from a pre-purge test backup replays installed/released PurgeLedger events, leaves barrier RELEASED only after deleting the restored payload, and returns no raw/admitted search results.

## IMPLEMENTATION STEP 8 — RUNNING (CODEX-HOSTED / ATTACHED)

Goal:
Integrate the actual Codex Host as an attached executor: Host-produced MODEL/TOOL/Search results can be accepted into Nexus Task/Run/Artifact/Evidence/Verification/Trace with truthful provenance and existing authority/classification/effect gates. Do not recreate reasoning, planning, a model provider, or a search provider inside Nexus.

Inputs:
Committed Steps 1–7; Codex Host supplies actual execution; any reported host/model/capability identifiers must be supplied by an authoritative runtime interface and not inferred. Independent paid Model/Search Providers and real Credential Broker use are explicitly deferred until a future standalone Local Service or multi-model deployment.

Allowed files:
`adapters/client/` for the attached Codex bridge; narrowly scoped Step 8 tests under `tests/contract/`, `tests/integration/`, or `tests/recovery/`; versioned schema additions only if required to represent a host-attached execution truthfully and compatibly; and this status file. Do not modify Kernel policy ceilings or frozen contracts. Do not touch global/business `AGENTS.md`, global Skills/config, existing credentials, user data, persistent databases, or unrelated adapters.

Forbidden files:
Five Foundation Contract semantics; authorization/policy upper bounds; prior migrations or persisted history; real user payloads/secrets; existing auth/env files; any external-write adapter; client/UI/CLI; network or provider calls before a reviewed profile, egress grant and approved credential reference exist; provider selection inferred from the current Codex host or ambient credentials.

Expected outputs:
Small attached-host bridge which records host execution facts/results through controlled Core APIs; exact capability/ToolDescriptor association for a safe read-only operation; Search results with source URLs captured as Evidence; real artifact verification and trace linkage. If the existing MODEL schema cannot represent a genuinely unavailable model identifier without guessing, keep that exact ModelProfile/RouteDecision/standalone-provider subcase `DEFERRED` and do not synthesize an identifier.

Exact tests:
Run `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`; targeted attached-host result ingestion, ToolDescriptor/authority boundary, provenance/source URL, integrity/verification, Trace and classification tests; mapped T10/T11 Hosted subcases; all-schema/default-policy validation; `pip check`; `compileall`; `git diff --check`; and secret-pattern scan. Do not run a real independent Provider call or resolve secrets.

PASS criteria:
Representative Hosted execution results must be persisted as Nexus Objects/Evidence and linked to Task/Run Trace under authority and classification checks; a reviewed read-only capability must be proven not to exceed its descriptor/grant; source URLs and integrity must be verifiable; no unknown model/provider identifiers may be fabricated. Independent Provider/Broker cases remain `DEFERRED / NOT_CONFIGURED`, not PASS.

FAIL handling:
If host identity or schema fields cannot be truthfully established, record only verifiable host facts and defer the exact unsupported subcase; do not modify frozen semantics or invent IDs. Independent provider work stays deferred.

Rollback point:
Step 7 commit `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`. Status-file pre-edit backup: `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-002245` (SHA-256 matched original before editing).

Do NOT:
- Guess a Provider from Codex host configuration or inspect secret values to find one.
- Implement real model/search adapters against unselected providers.
- Install hooks or frameworks, alter Kernel contracts/policy ceilings, or add real external-write tools.
- Mark the stage PASS on fake-only tests or source inspection.

Files changed:
- `kernel/verification/`, `kernel/memory/`, `kernel/purge/`, `migrations/0006_memory_purge.sql`, four Step 7 schemas, migration version expectation and integration tests, and this status file.

Known limitations:
- T2 authoritative external verifier, automated/provider reconciliation, real search/model/tool adapters, client modes and the complete T1–T12 acceptance suite are not implemented yet. The runtime remains DEVELOPMENT; no persistent database is initialized.
- SQLite 3.38 lacks `contentless_delete`; purge therefore clears and safely rebuilds contentless indexes from available, verified objects while the barrier is active.

Rollback point:
- Step 6 commit `0fbc795943c166894d31ca8beca9456f05028729`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.

Next:
STEP 9 — only after the documented Runtime mode and controlled inspect API prerequisites are resolved.

## IMPLEMENTATION STEP 9 — FAIL (PRE-IMPLEMENTATION PREREQUISITE AUDIT)

Goal:
Deliver a client/user operation surface over controlled APIs for status, Approval, Effect UNKNOWN/reconciliation, PurgePlan/PARTIAL, and the four operating modes. The UI must not access SQLite directly; modes must be enforced by Runtime as required by the manual.

Inputs:
Committed Steps 1–7; Step 8 deferred as `DEFERRED / NOT_CONFIGURED`; existing Core services and disposable test fixtures.

Allowed files:
`adapters/client/`, `docs/user/`, and this status file, per the manual's Step 9 boundary. No direct SQLite access from the UI.

Forbidden files:
Foundation Contract changes; direct DB reads/writes from client code; provider/secret setup; external writes; modifications to Kernel/Runtime, policies, migrations, test truth, production data or global/business configuration during this Step 9 card.

Expected outputs:
Read-only status/Approval/Effect/Purge views backed by controlled Core APIs; explicit pending-action and PARTIAL rendering; Runtime-enforced NORMAL/SAFE/STATELESS/RECOVERY matrix; user documentation and T7/T9/T12 client acceptance evidence.

Exact tests:
The manual-mapped `nexus test acceptance` equivalent must verify T7 approval target/hash display and post-revocation denial, T9 PARTIAL presentation without completion claim, and T12 four-mode enforcement and recovery. Existing suite baseline: `.venv\Scripts\python.exe -m unittest discover -s tests -v` (47 passed), `pip check`, `compileall`, all-schema/default-policy validation, and `git diff --check`.

PASS criteria:
No boolean-only approval; no client direct DB access; users can see pending approvals, three-axis UNKNOWN state and PARTIAL purge; Runtime—not prompts/client presentation—enforces the mode matrix; applicable T7/T9/T12 tests pass.

FAIL handling:
Audit found no mode-state/enforcement API in `kernel/runtime/` and no authorized inspect/read APIs for Approval, Effect, PurgePlan or instance status. A client-only gate would violate the manual. No Step 9 source files were changed and Step 10 is not started because the sequential gate failed. Resolve the missing prerequisite under an explicitly authorized corrective step, preserving frozen Contracts, then rerun this card.

Rollback point:
Step 7 checkpoint `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; no Step 9 source changes exist. Status-file backups with verified SHA-256 include `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-003856` and `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-004019`.

Do NOT:
- Simulate mode security solely in CLI/client code.
- Read SQLite directly from a UI/client or expose protected Approval hash metadata without authorization.
- Advance to Step 10 or claim T1–T12 acceptance on the strength of the existing 47 tests.
- Enable independent real providers or change Foundation Contracts.

Files changed:
- Only `docs/operator/IMPLEMENTATION_STATUS.md`; no Step 9 runtime/client code was changed.

Known limitations:
- No `adapters/client/`, `docs/user/`, or `eval/regression/` directory is present. There is no CLI `nexus test acceptance` command.
- Existing tests cover significant Core contracts but do not establish Step 9 T7/T9 presentation or T12 mode/recovery acceptance. Provider-specific real adapter tests remain deferred.

Next:
## CORRECTIVE IMPLEMENTATION — RESTORE OMITTED MODE / INSPECT PREREQUISITES

Goal:
Implement the handbook's `set_mode(mode, command_id)` semantics and Runtime-enforced behavior for NORMAL/SAFE/STATELESS/RECOVERY, plus authorized read-only APIs needed by the Step 9 client. This is a corrective return to omitted Core/Runtime prerequisites; it does not alter any Foundation Contract.

Inputs:
Committed Steps 1–7; handbook §6.2, §7.2, §9 Step 9 and T7/T9/T12; disposable test data only; Step 8 provider work deferred.

Allowed files:
`kernel/runtime/`, narrowly required gates in `kernel/memory/`, `kernel/run/`, `kernel/effect/`, `kernel/purge/`, `adapters/storage/`, additive `migrations/`, and new focused tests under `tests/integration/` / `tests/recovery/`, plus this status file. Back up every existing file before editing it; additive migration only; no edits to applied migrations.

Forbidden files:
Five Foundation Contract semantics/schema compatibility; existing applied migrations; production data, secrets, provider configuration, global/business AGENTS or Skills; weakening policy, approval, classification, Trace, Effect, or Purge gates; changing old tests to make them pass.

Expected outputs:
Persisted, idempotent mode command and audit event; fail-closed Runtime gates matching the handbook's exact four-mode matrix across Memory, Trace, effects, and egress; no v2-core writes in RECOVERY; authority-scoped read-only projections for task/run, Approval, Effect (three axes), PurgePlan/PurgeRecord/barrier and instance health; protected Approval hash only returned when authorized. All clients remain service/API-only, never DB-direct.

Exact tests:
New isolated mode contract tests for the complete handbook matrix, command replay/conflict, unauthorized mode change, Memory gates, Trace minimum facts, SAFE effect restrictions, STATELESS isolation, RECOVERY bypass/no Core persistence, and mode persistence across reopen. Read APIs must be non-mutating, deny unauthorized/hash disclosure, and accurately surface UNKNOWN/PARTIAL. Then run `.venv\Scripts\python.exe -m unittest discover -s tests -v`, schema/policy validation, `pip check`, `compileall`, `git diff --check`, and secret scan. T7/T9/T12 are rerun in Step 9/10 after this correction passes.

PASS criteria:
Runtime—not UI or prompt—enforces every row in §7.2; persisted mode survives reopen; command ledger retry is idempotent; no unauthorized reads; no trace/payload or purge barrier bypass; all new and existing tests pass in disposable data.

FAIL handling:
Keep Step 5 reopened and Step 9/10 gated. Repair only the violated invariant; never ship a client-side imitation or default-open fallback.

Rollback point:
Committed Step 7 checkpoint `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; status pre-edit backup `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-004316` SHA-256 verified. No Core data exists.

Do NOT:
- Add a sixth Foundation Contract or rewrite existing v1 schemas/migrations.
- Treat client display as the security boundary.
- Connect independent providers, retrieve secrets, or enable external writes.
- Move to Step 9 until this card passes.

## CORRECTIVE SUBSTEP — MODE TRACE LINK — PASS

Goal:
Make ordinary four-mode changes produce a replay-compatible Trace fact without changing the Trace schema or any Foundation Contract.

Inputs:
Existing Task Root `ORCHESTRATOR` Run, its Grant, a pre-authorized `TRACE_EVENT` classification assertion, and the mode/inspect implementation above.

Allowed files:
`kernel/runtime/modes.py`, `kernel/runtime/service.py`, `kernel/run/service.py`, `adapters/client/`, `docs/user/operator-cli.md`, focused Runtime/Trace/client tests, and this status/regression record.

Forbidden files:
Foundation Contract semantics/schema; migrations; production/persistent data; policy defaults; direct-client SQLite access; fabricated Task/Run/classification; external Providers or Effects.

Expected outputs:
`nexus.runtime.mode_changed` admitted only by the internal mode command, under exact mode and Trace authority, Root Run classification boundary, and a single atomic mode-event/Trace/CommandLedger transaction; replay treats the mode fact as non-state-changing.

Exact tests:
Focused `.venv\Scripts\python.exe -m unittest tests.integration.test_runtime_modes tests.integration.test_trace_state tests.integration.test_deterministic_runtime tests.integration.test_operator_client -v` (32 passed); full `.venv\Scripts\python.exe -m unittest discover -s tests -v` (68 passed); `compileall`, `pip check`, CLI help, and `git diff --check`.

PASS criteria:
No root, TRACE_APPEND authority, matching Grant, or valid event classification means deny/no mode change; Trace insert failure rolls back mode and ledger; successful Trace replays without changing Run state. No schema/Contract change.

FAIL handling:
Keep mode transition denied; do not create synthetic runtime Runs or relax classification/Trace validation.

Rollback point:
Commit `4a694590f2ae61d3cf75f7610dc1d07e7869938d`; same-directory verified backups at `.bak-20260925-015113`; latest status/regression backups `.bak-20260925-020349`.

Files changed:
`kernel/runtime/modes.py`, `kernel/runtime/service.py`, `kernel/run/service.py`, `adapters/client/`, `docs/user/operator-cli.md`, three focused integration test files, this status and the regression map.

Known limitations:
Validated recovery exit remains represented by the dedicated immutable recovery/mode CommandLedger records; no ordinary Core/Trace API is opened before Recovery validation completes. The CLI and recovery flow were exercised on disposable policy-configured data only, not a deployment root. Step 10 is PARTIAL: T12 recovery passes in isolation, while T2/T5/T9 remain partial and required release acceptance is unmet.

Next:
Continue Step 8 Hosted integration assessment, then Step 9 persistent-client verification and Step 10 formal acceptance; do not claim PRODUCTION_READY.
