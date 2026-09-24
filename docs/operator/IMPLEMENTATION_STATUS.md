# Nexus v2 Implementation Status

Nexus version: `v0.1-development`  
Current implementation step: Step 4 — PASS
Environment: Windows build `10.0.22631.0`; Python `3.11.0`; SQLite `3.38.4` + FTS5; Git `2.40.0.windows.1`  
Git commit / branch: `59f077179c5f895c8dc3e28b5ac292ce3eeb775b` / `nexus-v2-runtime`
Schema version: `nexus.* @1`; SQLite migration version `3` (isolated tests)
Policy version: `1` (fail-closed default policy)  
Database version: `3` exercised only in isolated integration databases; persistent runtime database not initialized

Step 0: PASS  
Step 1: PASS  
Step 2: PASS
Step 3: PASS
Step 4: PASS
Step 5: NOT_STARTED  
Step 6: NOT_STARTED  
Step 7: NOT_STARTED  
Step 8: NOT_STARTED  
Step 9: NOT_STARTED  
Step 10: NOT_STARTED

Tests passed:
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

Tests failed:
- Step 1 first contract run had 4 errors because the initial cross-file schema references attempted network retrieval and `allOf` rejected common fields. Replaced with local self-contained references and `unevaluatedProperties`; final 11-test suite passes.
- Step 2 first integration runs exposed a hash-profile FK mismatch, open test handles, migration trigger parsing, and a child-process test harness issue; these were corrected. An early Step 2 test also expected an orphan payload for a missing lineage source; after enforcing validation before payload write, the test was updated to assert no payload or metadata is persisted.
- Step 3 first runs exposed a policy-schema directory assumption and a stale test expectation for a single migration; both were corrected. A classification egress test initially supplied an assertion ID rather than an object ID; the API was tightened to derive classification only from persisted object envelopes. A final immutable-ledger test initially queried the losing concurrent reservation ID; it now selects the actual winning ledger command. The final 32-test suite passes.
- Step 4 initial tests caught event creation ordering, schema-version expectation, high-classification fixture wiring, and the need to rollback Run state if Trace append fails; fixes are covered by the final 38-test suite.

Open blockers:
- Exact Credential Broker / OS key-store policy is unknown; required before Step 8 real model/provider connection.
- `.git` write requires controlled approval for checkpoint commits; branch creation was approved and succeeded.

Known UNKNOWN Effects: None; Nexus runtime/data not initialized.  
Pending Purge: None.  
Pending migration: None.  
Rollback point: clean base commit `ac61316178d7e145ed420de2fbb9ee0273067263`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.  
Last verified state: 2026-09-24 Step 4 suite (38 tests); branch `nexus-v2-runtime`; no persistent Nexus runtime data initialized.
Next allowed action: checkpoint Step 4, then begin Step 5 only after using its Step Card below.

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

## IMPLEMENTATION STEP 5 — NOT_STARTED

Goal:
Build the deterministic Task/Root scheduler and acyclic subtask DAG, executor-specific RunManifest binding, and simple fail-closed node-level routing without executing any real Model or Tool.

Inputs:
Committed Steps 1–4 schemas, object/classification store, authority/budget services, Task/Run/Trace replay, and disposable fixtures.

Allowed files:
`kernel/runtime/`, a new `migrations/0004_runtime.sql`, new Step 5 schemas in `schemas/`, `adapters/storage/sqlite_store.py` only for invariant enforcement, `tests/contract/`, `tests/integration/`, and this status file.

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
Step 4 checkpoint to be committed before Step 5 modifications; Step 0 snapshot recorded above.

Do NOT:
- Implement Learned Router, model SDK, real tool, SearchProvider or external writes.
- Let Root Run execute inference or tool code.
- Replace Task budget with recursive parent/child accounts.
- Bind manifests by mutating their payloads.
