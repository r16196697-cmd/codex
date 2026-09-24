# Nexus v2 Implementation Status

Nexus version: `v0.1-development`  
Current implementation step: Step 7 — PASS
Environment: Windows build `10.0.22631.0`; Python `3.11.0`; SQLite `3.38.4` + FTS5; Git `2.40.0.windows.1`  
Git commit / branch: `0fbc795943c166894d31ca8beca9456f05028729` / `nexus-v2-runtime`
Schema version: `nexus.* @1`; SQLite migration version `6` (isolated tests)
Policy version: `1` (fail-closed default policy)  
Database version: `6` exercised only in isolated integration databases; persistent runtime database not initialized

Step 0: PASS  
Step 1: PASS  
Step 2: PASS
Step 3: PASS
Step 4: PASS
Step 5: PASS
Step 6: PASS
Step 7: PASS
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
- Step 5: 41 contract/integration tests pass, including deterministic DAG routing/scheduling for ORCHESTRATOR, MODEL and TOOL identities; E0/E1/E2 quality floors, locality/provider/classification/context/budget hard gates; exact child Manifest binding before READY; TaskContract/LogicalRef and schema-bound inputs; authority-descended Run grants; atomic budget reservations; scheduler command replay; dependency gating; Trace-backed Subtask replay; and rejection/no persistence of an unreviewed external-write descriptor.
- Step 5 static checks: all 30 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass. Targeted Step 5 + Trace suite: 9 passed.
- Environment note corrected to match the observed source layout (`kernel/`, `adapters/`, `schemas/`, `migrations/`, `tests/`); no source was moved.
- Step 6: 43 contract/integration tests pass. Fake dispatcher response loss becomes UNKNOWN, same-command retry does not redispatch, the configured authoritative fake channel resolves using evidence, and budget settlement is bounded/idempotent. A separate compensation Effect and COMPENSATES relation commits without changing the original COMMITTED fact. Payload-hash-mismatched ApprovalDecision and revoked parent Grant both prevent dispatch.
- Step 6 static checks: all 30 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check`, and secret-pattern scan pass.
- Step 7: 47 contract/integration tests pass. T1 SHA-256 integrity results are not treated as semantic truth and remain quarantined; T3 human/domain verification requires an ApprovalDecision bound to exact target/evidence integrity hashes. Only independent evidence/method plus approval enters Admitted Memory. Raw History and Admitted Memory use separate FTS5 indexes; SQLite backing rows keep refs/classification/expiry only, while indexed result text is loaded from the immutable filesystem object. Run action/resource, data classification/handling tags, retention expiry, and purge barriers gate search and indexing.
- Step 7 Purge: plan hash binds a persisted plan and ApprovalDecision; external append-only hash-chained journal is outside the data root. RunManifest input refs are persisted as immutable input bindings and derived-from lineage; new bindings to protected inputs fail, and a previously-created but unstarted Run cannot enter READY while its input is barriered or PURGED. An active Run (even if a quiescence callback falsely reports success) or UNKNOWN Effect leaves Purge PARTIAL and barrier active. A positive barrier/quiescence/purge path deletes the payload, redacts envelope/hash/path and removes both index entries. Old-backup restore first reinstalls the barrier, replays all independent ledger events, deletes restored payloads, clears/rebuilds the contentless FTS indexes, and does not expose purged content. Restoring a PARTIAL journal state retains the barrier and does not falsely delete payloads.
- Step 7 static checks: all 33 JSON Schema documents and default policy validate; `pip check`, `compileall`, `git diff --check` pass. Test DBs are disposable; no persistent database, external provider, real history, secret, or network was used.

Tests failed:
- Step 1 first contract run had 4 errors because the initial cross-file schema references attempted network retrieval and `allOf` rejected common fields. Replaced with local self-contained references and `unevaluatedProperties`; final 11-test suite passes.
- Step 2 first integration runs exposed a hash-profile FK mismatch, open test handles, migration trigger parsing, and a child-process test harness issue; these were corrected. An early Step 2 test also expected an orphan payload for a missing lineage source; after enforcing validation before payload write, the test was updated to assert no payload or metadata is persisted.
- Step 3 first runs exposed a policy-schema directory assumption and a stale test expectation for a single migration; both were corrected. A classification egress test initially supplied an assertion ID rather than an object ID; the API was tightened to derive classification only from persisted object envelopes. A final immutable-ledger test initially queried the losing concurrent reservation ID; it now selects the actual winning ledger command. The final 32-test suite passes.
- Step 4 initial tests caught event creation ordering, schema-version expectation, high-classification fixture wiring, and the need to rollback Run state if Trace append fails; fixes are covered by the final 38-test suite.
- Step 5 development tests exposed composite DAG-edge identity, scheduling preflight ordering, child Manifest/schema constraints, and projection recovery details; these were corrected and the final 41-test suite passes.
- Step 6 development tests exposed the Step 5 read-only Descriptor scheduling boundary, migration-version expectations, commit-in-progress DB constraints and fixture identity bindings; these were corrected and the final 43-test suite passes.
- Step 7 development tests caught a PurgePlan insert placeholder/FK ordering error, invalid connection-context handling, duplicate command-ledger writes on PARTIAL/COMPLETED, a dynamic closure-query parameter mismatch, and SQLite 3.38's lack of the newer FTS contentless-delete option. Fixes now use validated transaction paths, barrier-aware RunManifest input guards, and contentless FTS with safe purge-time rebuild; all cases are covered by the final 47-test suite.

Open blockers:
- Exact Credential Broker / OS key-store policy is unknown; required before Step 8 real model/provider connection.
- Step 7 checkpoint commit is pending; branch `nexus-v2-runtime` already exists and prior stage checkpoints are committed.

Known UNKNOWN Effects: None; Nexus runtime/data not initialized.  
Pending Purge: None.  
Pending migration: `0006_memory_purge.sql` is test-applied only; there is no persistent runtime database to migrate.
Rollback point: Step 6 checkpoint `0fbc795943c166894d31ca8beca9456f05028729`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.
Last verified state: 2026-09-25 Step 7 full suite (47 tests), static checks, synthetic T3 approval, PARTIAL barrier, purge and old-backup recovery; branch `nexus-v2-runtime`; no persistent Nexus runtime database initialized.
Next allowed action: commit Step 7 checkpoint, then begin only Step 8 after recording its Step Card.

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

Files changed:
- `kernel/verification/`, `kernel/memory/`, `kernel/purge/`, `migrations/0006_memory_purge.sql`, four Step 7 schemas, migration version expectation and integration tests, and this status file.

Known limitations:
- T2 authoritative external verifier, automated/provider reconciliation, real search/model/tool adapters, client modes and the complete T1–T12 acceptance suite are not implemented yet. The runtime remains DEVELOPMENT; no persistent database is initialized.
- SQLite 3.38 lacks `contentless_delete`; purge therefore clears and safely rebuilds contentless indexes from available, verified objects while the barrier is active.

Rollback point:
- Step 6 commit `0fbc795943c166894d31ca8beca9456f05028729`; Step 0 snapshot `<LOCAL_PATH_REDACTED>`.

Next:
STEP 8 — one real Model Adapter, one reviewed read-only Tool, and one Search Provider (subject to Credential Broker blocker).
