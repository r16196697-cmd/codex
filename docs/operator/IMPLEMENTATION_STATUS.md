# Nexus v2 Implementation Status

## T10 RUNTIME CORRECTION — MULTI-ATTEMPT DAG EXECUTION

Goal:
Allow one logical DAG Node to persist and replay ordered 0..N Run attempts, with an explicit single final outcome and coordinator continuation; keep current Codex Host model identity unavailable.

Inputs:
Checkpoint `a0e8056b1e4e6a4be373f594aa44a79c33ca55f2`; T1–T9 PASS baseline; current T10 regression report; handbook §10 T10 and §5 contracts.

Allowed files:
`kernel/runtime/`, `kernel/run/`, `adapters/client/hosted.py`, authorized inspect projections, additive migration 0011, RouteDecision schema version 2, focused runtime/hosted/migration tests and acceptance records. Same-directory timestamped, SHA-256-verified backups before editing existing files. Persistent root migration only after a verified v10 snapshot.

Forbidden files:
Foundation Contract semantic changes; model/provider identity fabrication; new Agent/Planner/runtime; independent providers; policy/grant broadening; edits to prior migrations; persistent real/private data.

Expected outputs:
Immutable ordered attempt relations; per-attempt RouteDecision/capability/reason/outcome/predecessor; exact command replay; independent budget reservation per Run; one explicit node outcome; replay/inspect and downstream coordinator continuation; truthful Host-declared decision with unavailable backend identity.

Exact tests:
`test_escalation_attempts_replay_and_coordinator_continue_downstream`; `test_escalation_failure_finalizes_once_as_inconclusive`; Hosted MODEL Manifest/RouteDecision/replay/inspect integration; v6→v11 migration rehearsal; full suite; `compileall`, `pip check`, `git diff --check`, secret scan, persistent-root integrity/state audit.

PASS criteria:
Multiple immutable Run attempts attach to one Node; each attempt’s RouteDecision, requested tier, reason and outcome replay accurately; no duplicate attempt/debit on retry; only one final outcome; coordinator may continue only after finalization; no real Host tier or backend identity is inferred.

FAIL handling:
Keep the release DEVELOPMENT if any attempt can be overwritten, duplicated, unbudgeted, replayed ambiguously, or if Host truth would need to be fabricated. Do not alter frozen Contract semantics to fit an implementation shortcut.

Rollback point:
Git source checkpoint `a0e8056b1e4e6a4be373f594aa44a79c33ca55f2`; verified pre-v11 schema-v10 snapshot `nexus/backups/codex-hosted-attached-20260925-173434-pre-v11-attempts` (SQLite integrity `ok`; all 16 payload hashes matched before migration).

Do NOT:
- Treat deterministic Fake/Sandbox E1→E2 semantics as live Codex multi-tier model switching.
- Replace existing `scheduled_run_id` values or erase historical Runs.
- Consume one reservation more than once or reuse a reservation across attempts.

## IMPLEMENTATION STEP 10 CONTINUATION — CLOSE RECORDED ACCEPTANCE GAPS

Goal:
Close only the already-recorded Hosted T1/T2/T5/T9/T10/T11/T12 gaps against manual §10, without redesigning Contracts or standalone capabilities.

Inputs:
Source checkpoint `1542cb08579ed1281630ade4b7f6c7094c90ad19`; current Hosted E2E; manual §10; acceptance matrix in `eval/regression/2026-09-25-persistent-host-pilot.md`.

Allowed files:
Focused existing contract/integration/recovery/security tests and fixtures; minimal existing service/schema compatibility fixes only where a formal test proves a gap; acceptance report and this status file. Same-directory verified timestamp backup before each existing-file edit. All failure injection uses isolated temporary roots; persistent root may only receive synthetic approved test records.

Forbidden files:
Five Foundation Contract semantics, policy weakening, grant widening, real user data, real external writes, standalone Provider/Search/Credential Broker, broad framework or architecture additions. Any persistent schema update requires a verified pre-update snapshot.

Expected outputs:
Evidence mapped one-to-one to formal observable T1/T2/T5/T9/T10/T11/T12 conditions; exact remaining blockers called out rather than converted to PASS.

Exact tests:
Focused test modules for each affected T; full `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`; migration/schema checks; `compileall`, `pip check`, `git diff --check`, secret scan; isolated old-snapshot/Purge-ledger/RECOVERY drill.

PASS criteria:
Manual conditions are actually observed; command retry and restore cases are idempotent; Purged content and policy-governed identifiers do not reappear; Recovery cannot return NORMAL until checks finish; unsupported Host telemetry remains unavailable.

FAIL handling:
Stop only the affected item if it requires changing frozen semantics, unsafe identifier deletion, or broader authority. Preserve audited facts and report exact blocker.

Rollback point:
Source commit `1542cb08579ed1281630ade4b7f6c7094c90ad19` (reviewed T2/T5/T9 baseline checkpoint); pre-v8 persistent-root backup `nexus/backups/codex-hosted-attached-20260925-152305-pre-v8-redaction` (schema 7, integrity `ok`).

Do NOT:
- Add tests unrelated to the named unmet conditions.
- Change T3/T4/T6/T7/T8 unless a change makes their regressions necessary.
- Call a provider-specific future capability a Hosted blocker or vice versa.
- Mark any state PASS without observable evidence.

## IMPLEMENTATION STEP 8 CONTINUATION — CODEX HOST BRIDGE

Goal:
Connect actual Codex-host-declared MODEL work and actual read-only TOOL work to existing Nexus Task/Run/Object/Verification/Trace lifecycle through a reusable thin adapter, without provider telemetry claims, an Agent loop, or Contract changes.

Inputs:
Step 1–7 Core services; persistent non-sensitive pilot root; approved read-only tool boundary; current attached Codex session. Independent Provider, Search API and Credential Broker remain deferred.

Allowed files:
`adapters/client/hosted.py`; additive attached-host RunManifest schema; narrowly required version-dispatch in `adapters/storage/sqlite_store.py`, `kernel/runtime/service.py`, and `kernel/run/service.py`; new focused tests and acceptance records; this status file. Back up every existing file in the same directory before edit. Persistent test records only in the ignored Hosted data root.

Forbidden files:
Foundation Contract semantics; existing v1 schema semantics and applied migrations; direct database writes from Host Adapter; Grant widening; global/business AGENTS or Skills; secrets and user data; model/provider/backend IDs not surfaced by Host; external write tools.

Expected outputs:
Version-compatible explicit Host-declared MODEL receipt (`CODEX_HOST_DECLARED`, backend identity unavailable), real TOOL Child Run for an actual reviewed read-only operation, governed Artifact/Evidence and Verification/Trace, close/reopen persistence and authorized inspect. Adapter delegates to Core APIs and adds no planning/model execution loop.

Exact tests:
Focused Hosted Bridge contract/integration test; actual synthetic persistent Task E2E through close/reopen/inspect; actual read-only file action; search/Evidence only if the current session search surface returns a real source URL; full unit/integration suite; schema/default-policy validation; `pip check`, `compileall`, `git diff --check`, secret scan.

PASS criteria:
The underlying backend identity remains unavailable rather than guessed; child Grant ancestry/data boundary/budget/Trace admission remain enforced; real Task, ORCHESTRATOR, MODEL and TOOL records plus Artifact/Verification are inspectable after reopen. Anything not proven stays PARTIAL/DEFERRED.

FAIL handling:
Stop only the unsupported adapter slice; preserve v1 semantics and all existing history; do not broaden authority or mark hosted execution as Provider telemetry.

Rollback point:
Commit `497a991` plus verified same-directory file backups created at `20260925-133821-673`; persistent synthetic root snapshot `nexus/backups/codex-hosted-attached-20260925-132121`.

Do NOT:
- Add a sixth Foundation Contract or alter five-contract semantics.
- Add Planner, Reasoning Loop, independent Model Provider, credentials or Search Provider.
- Use direct SQLite writes in the adapter or convert a denied action into success.
- Claim release acceptance before T1–T12 evidence is assessed.

## 2026-09-25 Hosted Bridge execution notes

- The abandoned synthetic Root Run `hosted-pilot-root-20260925` was safely cancelled using its existing Grant and the normal `TraceRuntime.transition_run` path. A new PUBLIC ClassificationAssertion was written for the exact pre-authorized cancellation Trace event; the Grant was not changed. Command `hosted-root-verifying-hosted-pilot-task-20260925` produced `evt-hosted-root-verifying-hosted-pilot-task-20260925`, seq 4, status `CANCELLED`; replay returned 4 events and `CANCELLED`. The earlier exact-resource denial remains in audit history.
- New additive `nexus.run_manifest@2` expresses only Codex-host-declared MODEL Runs with `model_identity_status=UNAVAILABLE`; v1 remains unchanged. Provider/model ID/token/request telemetry are not present.

Nexus version: `v0.1 — Codex-hosted / Attached`
Current implementation step: Step 10 — PASS for Codex-hosted applicable scope; `DEPLOYED` after final release gates below
Environment: Windows build `10.0.22631.0`; Python `3.11.0`; SQLite `3.38.4` + FTS5; Git `2.40.0.windows.1`  
Implementation checkpoint / branch: T10 Runtime code checkpoint `e42b0340a2828cdd868414887d3bc2b8c9770fe8` / `nexus-v2-runtime`; final status reconciliation is recorded in the closeout update.
Schema version: `nexus.* @1`; SQLite migration version `11` (tests and persistent Hosted root)
Policy version: `1` (fail-closed default policy)  
Database version: `11` in isolated integration tests and the persistent synthetic pilot

Step 0: PASS  
Step 1: PASS  
Step 2: PASS
Step 3: PASS
Step 4: PASS
Step 5: PASS (corrective four-mode Runtime gates, authorized inspect, and mode-to-Trace transaction/replay; 69-test regression passed)
Step 6: PASS
Step 7: PASS
Step 8: PASS for Codex-hosted integration (reusable Host Bridge, declared MODEL Child Run, actual read-only TOOL Child Run, Artifact/Evidence/Verification/Trace and persistent reopen/inspect verified); standalone Provider/Broker cases DEFERRED / NOT_CONFIGURED
Step 9: PASS for Codex-hosted operations (four enforced modes, controlled inspect, CLI mode/inspect and persistent deployment-root reopen verified)
Step 10: PASS for Codex-hosted applicable scope (final regression: 82 passed, 1 optional search-receipt test skipped; T1–T12 PASS for Hosted-required conditions. Live Codex E1/E2 backend switching unavailable and not claimed.)

Tests passed:
- T10 multi-attempt correction: E1 failed → E2 succeeded → dependent DAG node scheduled; exact same-command retry yielded no third attempt; two Runs had two distinct budget reservations. Separate E1/E2 failed path finalized exactly once as `INCONCLUSIVE` with a root coordinator Trace event; replay agreed and further scheduling was denied.
- Hosted RouteDecision v2: MODEL RunManifest references its RouteDecision object; `execution_source=CODEX_HOST_DECLARED`, model identity `UNAVAILABLE`; TOOL attempts also carry governed RouteDecision records. Authorized task inspect exposes ordered attempts only inside classification boundary.
- Migration `0011_subtask_attempts.sql`: schema-v10 live root was snapshotted/verified first, then upgraded to schema 11; old `scheduled_run_id` values remain untouched and are backfilled as legacy attempt 1. Existing historical missing RouteDecision refs remain null.
- Final relevant suite: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **82 passed, 1 optional search receipt test skipped** in 40.975s. Skipped test requests an operator-supplied observed query/URL/excerpt; existing T11 observed Search/Evidence receipt remains its evidence.
- Persistent root reopened at schema 11: mode `NORMAL`, `PRAGMA integrity_check=ok`, 2 legacy attempts backfilled, all 16 payloads verified, both old child Run traces replay `SUCCEEDED`; no persistent synthetic Task/Run was added during this T10 correction.
- Host limitation: `live multi-tier model switching unavailable in current Codex host`; Host-declared requested capability remains `UNSPECIFIED` unless provided; no backend/model identity inferred.
- 2026-09-25 earlier Step 10 round (superseded by the final T10 closeout below): `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 80 passed, 1 optional environment-gated receipt test skipped; live search was separately bracketed by a Host-declared TOOL Run before and after the actual Codex Search action. T11 security/insufficiency and T12 persistent-root clone recovery focused tests passed.
- Persistent root snapshots before v9 (schema 8) and v10 (schema 9) were verified with integrity `ok`, 3 Tasks, 0 Purge rows. The root was subsequently upgraded/reopened at schema 11 after a verified pre-v11 snapshot; final read-only inspection follows below.
- Final T matrix: T1–T12 PASS for Codex-hosted required conditions (see acceptance evidence at `eval/regression/2026-09-25-persistent-host-pilot.md`). Independent Model/Search Provider, Credential Broker, multi-provider routing and standalone model execution remain `DEFERRED / NOT_CONFIGURED`.

Tests failed / incomplete (current release run):
- The Hosted fixture's former `COMMAND_CONFLICT` was a fixture determinism defect, not a Core defect. Persistent rerun now detects a terminal existing E2E Task read-only and verifies Run replay/Verifications without reconstructing timestamped Grants; a separate isolated test proves fresh Task + command IDs and exact same-command retry.
- T2 now covers post-reopen create Task, create Root Run, committed transition, MODEL output and TOOL output retries without duplicates, plus payload rename/metadata-commit and state/Trace atomic-failure injections.
- T9 now performs guarded target/hash/scope/idempotency/receipt/Trace metadata/purge-record path redaction while retaining Approval decision and Effect outcome/axes; the isolated old-snapshot replay repeats these checks.
- No current acceptance failures. One optional search-receipt test is skipped because this run does not supply an observed query/URL/excerpt; previously captured Hosted Search evidence is recorded under T11. Live Codex multi-tier model switching remains unavailable and is not represented as a Runtime or Hosted PASS claim.
- T11 Hosted observable scope PASS: a live isolated Host-declared SEARCH TOOL Run began before the actual search, then bound query/source URL/retrieval receipt to Evidence, deterministic Verification and Trace; security tests keep injected/conflicting evidence QUARANTINED/UNKNOWN and missing evidence INCONCLUSIVE. No search provider telemetry is claimed.
- T12 PASS: the persistent-root clone test now combines synthetic Purge, old-snapshot restore, RECOVERY inspect/Memory denial, Purge Ledger replay, integrity/schema and payload/index non-resurrection, SAFE/STATELESS gates, return to NORMAL, close/reopen and post-reopen checks.
- No acceptance failure has been reclassified as DEFERRED except Standalone-only Provider/Search/Credential Broker cases.

Current release state:
- `Nexus v0.1 — Codex-hosted / Attached`: DEPLOYED for Hosted-required acceptance scope.
- `Standalone Nexus`: NOT IMPLEMENTED / DEFERRED.
- 2026-09-25 corrective mode/inspect + client run: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 65 passed; `compileall`, `pip check`, `git diff --check`, CLI `--help` passed. The only `git diff --check` output was Git's LF→CRLF advisory, not whitespace errors.
- 2026-09-25 follow-up revalidation: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 65 passed in 19.736s; `compileall`, `pip check`, `git diff --check`, and CLI `--help` passed. No tracked source edits were made during this audit. Step 9's mode/inspect gates remain regression-PASS; this is not formal T1–T12 acceptance.
- 2026-09-25 mode-to-Trace correction: focused Runtime/Trace/replay/deterministic-runtime/client regression — 32 passed; full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **68 passed** in 21.325s. `compileall`, `pip check`, CLI `--help` and `mode set --help`, and `git diff --check` passed. These checks establish only tested Core behavior, not a live persistent-client deployment or full T1–T12 acceptance.
- 2026-09-25 configured CLI end-to-end: a disposable policy-configured instance completed authorized CLI `mode set SAFE`, persisted the matching TraceEvent, and reopened with mode/event intact. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.392s; `compileall`, `pip check`, CLI help, and `git diff --check` passed. No persistent deployment root was created.
- 2026-09-25 CLI inspect integration: a disposable policy-configured instance used a separate exact `INSPECT` Grant to retrieve the Task/Root-Run projection after the authorized mode-set path; the projection exposed no payload field. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.967s. This is isolated client integration evidence, not deployment-root or live Codex Host acceptance.
- 2026-09-25 full recovery regression: `test_old_snapshot_recovery_replays_purge_ledger_before_normal` created a pre-purge snapshot, entered RECOVERY through an authorized mode command on the isolated snapshot, completed purge in the source instance, restored the snapshot, blocked Core access during RECOVERY, replayed the independent Purge Ledger, rebuilt FTS, verified payload/search unavailability, and only then returned to NORMAL. Full suite — **70 passed** in 23.294s; `compileall`, `pip check`, and `git diff --check` passed. Temporary synthetic data only.
- 2026-09-25 T2/T5/T9 hardening: simulated object metadata commit failure after payload rename and verified reopen deletes the orphan; replayed a synthetic v6 database snapshot through migration v7; simulated crash after independent Purge Ledger barrier append but before SQLite barrier commit and verified replay reinstalls a persistent PARTIAL hold, denies Memory derivation and remains PARTIAL after reopen. Focused tests passed; full suite **73 passed** in 22.942s; `compileall`, `pip check`, `git diff --check` passed.
- 2026-09-25 Purge delete crash/retry: an injected SQLite failure after filesystem unlink rolled back metadata while retaining ACTIVE barrier/RUNNING record; after reopen, the same `command_id` completed deletion idempotently, verified indexes/content unavailable, and only then released the barrier. Full suite **74 passed** in 21.779s; `compileall`, `pip check`, `git diff --check` passed.
- 2026-09-25 persistent pilot: created the previously absent ignored root `nexus/data/codex-hosted-attached/` with a secret-free policy and one local pilot trust anchor; SQLite migration v7, FTS5 and integrity checks passed. A real local read-only probe input/result were stored as NexusObjects under an authorized Task and ORCHESTRATOR Root Run; deterministic T1 SHA-256 Verification returned PASS. After close/reopen, CLI mode and authorized inspect showed SUCCEEDED; Core Trace replay returned 6 events; Verification remained PASS. A SQLite backup-API snapshot plus payload-directory copy restored to an isolated sibling path; restored Task/Run/Trace/Verification/mode and output hash validated. Snapshot: `nexus/backups/codex-hosted-attached-20260925-132121`; restore: `nexus/backups/codex-hosted-attached-20260925-132121-restore`.
- 2026-09-25 pilot negative result: the first Task's immutable Grant correctly denied a Trace classification assertion whose event resource had been omitted from the Grant. It temporarily left `hosted-pilot-root-20260925` RUNNING; later it was cancelled through the existing Grant and normal Trace transition. The denial remains audited; no scope expansion or direct DB cleanup occurred.
- 2026-09-25 post-pilot regression: full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **74 passed** in 24.655s; `compileall`, `pip check`, and `git diff --check` passed. This does not substitute for the formal T1–T12 Hosted Acceptance Suite.
- Four modes: authorized/idempotent/persistent switches; SAFE blocks external reversible Effect commit, memory writes and learning/profile/descriptor changes; STATELESS blocks Memory service reads/writes, disallowed Trace classes and the SQLite Memory table/index access path while preserving allowlisted traces; Recovery blocks Core/inspect, skips startup migration/cleanup, rejects ordinary exit, and exits only after SQLite integrity/migration, Purge Ledger, object SHA-256, index and deletion checks. Tests confirm pending Purge barriers and corrupted payloads keep RECOVERY isolation.
- Inspect: valid Task projection succeeds under exact `INSPECT` task/resource/audience scope; unauthorized Grant and RECOVERY calls deny; Approval hash is redacted unless a distinct `INSPECT_PROTECTED` scope is present; real synthetic Effect UNKNOWN and Purge PARTIAL facts pass through Core inspect APIs and remain UNKNOWN/PARTIAL in the client presentation.
- CLI: mode set reads an optional schema-validated policy JSON, passes the disposable-instance authorized mode/Trace/reopen integration test, and refuses absent data roots without creating `nexus.sqlite`; client facade contains no raw SQLite query code.
- Rollback point before corrective implementation: Step 7 commit `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`. Modified pre-existing files received same-directory timestamp backups; status backup `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-013510` SHA-256 verified.
- Deployment shape remains **Nexus v0.1 — Codex-hosted / Attached**. Independent Model/Search Provider adapters and actual Credential Broker secret resolution remain `DEFERRED / NOT_CONFIGURED`, not PASS and not Core failures.
- A persistent, Git-ignored synthetic pilot now exists at `nexus/data/codex-hosted-attached/`; it contains no real user history, credentials, or provider data. The early failed-scope probe was safely cancelled through its existing Grant and normal Trace lifecycle; the denial remains recorded. A successful Hosted E2E and isolated snapshot/restore were exercised.
- Mode Trace binding is now implemented without changing the Trace schema or Foundation Contracts: an authorized mode command requires the Task's actual ORCHESTRATOR Root Run, the same Grant on that Run, `RUNTIME_CONFIGURE` plus `TRACE_APPEND`, and a pre-authorized event-specific classification assertion. The mode event, mode audit row, CommandLedger result, and mode state commit atomically; Root Run replay accepts the typed mode fact without changing Run state. Missing Run, authority, or valid classification fails closed.
- Hosted Bridge evidence: `adapters/client/hosted.py` creates governed Task/ORCHESTRATOR/MODEL/TOOL lifecycle records using Core APIs; the persisted E2E records host-declared execution only (`CODEX_HOST_DECLARED`; backend model/provider/request/token fields unavailable). It performs a real contained read-only file probe, stores Objects/Artifact/Evidence, verifies integrity and supports authorized inspect/replay after close/reopen. Hosted Search evidence is ingested only when an actual source URL is returned; the search action is not represented as a separate TOOL Run, so T11 remains PARTIAL.
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
- Historical Step 8 stop-gate revalidation: before the Hosted Bridge existed, the committed Steps 1–7 baseline had 47 tests pass. This is superseded by the Hosted Bridge evidence and 75-test run recorded above; standalone Provider-specific cases remain deferred.
- Operator disposition: current deployment is **Nexus v0.1 — Codex-hosted / Attached**. Codex is the current AI Executor/Host; Nexus owns governed local state. Independent Model/Search Provider adapters, paid API credentials, and the Credential Broker's real-secret path are `DEFERRED / NOT_CONFIGURED`, not PASS and not a Core failure.
- Hosted Bridge schema gap is resolved additively by `nexus.run_manifest@2`; existing v1 remains unchanged. The Bridge records the Host declaration and backend identity as unavailable; no Provider telemetry is inferred.
- Step 9/10 continuation audit: 47 existing contract/integration tests reran and passed; `pip check`, `compileall`, 33 schema + default-policy validation passed. `git diff --check` initially caught trailing whitespace in the newly edited status line; this line is removed in this update. This suite is not the manual's recorded T1–T12 Acceptance Suite.

Tests passed:
- 2026-09-25 Codex Hosted Bridge: full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **75 passed** in 26.828s (latest rerun; prior identical run 28.554s). `compileall`, `pip check`, `git diff --check`, and JSON schema validation passed. Persistent E2E performed actual local read-only TOOL work, host-declared MODEL receipt, Artifact/Evidence/T1 integrity Verification, Trace replay and authorized inspect after close/reopen; separate-process CLI inspect confirmed the persisted state. Post-E2E isolated snapshot restore preserved Task, all three Runs, Verifications, and NORMAL mode. This does not close the Purge-ledger restore case or all formal T1–T12 criteria.

Tests failed:
- Step 1 first contract run had 4 errors because the initial cross-file schema references attempted network retrieval and `allOf` rejected common fields. Replaced with local self-contained references and `unevaluatedProperties`; final 11-test suite passes.
- Step 2 first integration runs exposed a hash-profile FK mismatch, open test handles, migration trigger parsing, and a child-process test harness issue; these were corrected. An early Step 2 test also expected an orphan payload for a missing lineage source; after enforcing validation before payload write, the test was updated to assert no payload or metadata is persisted.
- Step 3 first runs exposed a policy-schema directory assumption and a stale test expectation for a single migration; both were corrected. A classification egress test initially supplied an assertion ID rather than an object ID; the API was tightened to derive classification only from persisted object envelopes. A final immutable-ledger test initially queried the losing concurrent reservation ID; it now selects the actual winning ledger command. The final 32-test suite passes.
- Step 4 initial tests caught event creation ordering, schema-version expectation, high-classification fixture wiring, and the need to rollback Run state if Trace append fails; fixes are covered by the final 38-test suite.
- Step 5 development tests exposed composite DAG-edge identity, scheduling preflight ordering, child Manifest/schema constraints, and projection recovery details; these were corrected and the final 41-test suite passes.
- Step 6 development tests exposed the Step 5 read-only Descriptor scheduling boundary, migration-version expectations, commit-in-progress DB constraints and fixture identity bindings; these were corrected and the final 43-test suite passes.
- Step 7 development tests caught a PurgePlan insert placeholder/FK ordering error, invalid connection-context handling, duplicate command-ledger writes on PARTIAL/COMPLETED, a dynamic closure-query parameter mismatch, and SQLite 3.38's lack of the newer FTS contentless-delete option. Fixes now use validated transaction paths, barrier-aware RunManifest input guards, and contentless FTS with safe purge-time rebuild; all cases are covered by the final 47-test suite.

Open blockers:
- Independent real Model/Search Providers, real Provider Credential Broker use, multi-provider routing and standalone model execution are intentionally `DEFERRED / NOT_CONFIGURED`. They are not Core failures. No real `auth.json` / `.env` value was read or used as a Nexus credential.
- Step 8 is **PASS for current Codex-hosted attachment scope**. It does not claim standalone Provider telemetry. Exact backend identity, request IDs, and token telemetry remain unavailable.
- Step 9 is **PASS for Codex-hosted operations**: enforced mode gates, controlled inspect, CLI use, persistent Task inspection and reopen were verified.
- Step 10 is **PASS for Codex-hosted applicable scope**. Runtime E1→E2 escalation, fallback/finalization, replay, per-Run budget isolation and coordinator continuation are deterministically exercised; Hosted-declared MODEL lifecycle and honest unavailable identity are verified without claiming live model-tier switching. T1–T12 evidence is in `eval/regression/2026-09-25-persistent-host-pilot.md`.
- The persistent pilot's previously RUNNING synthetic probe (`hosted-pilot-root-20260925`) is now CANCELLED through its existing Grant and authorized Trace transition. The prior exact-resource authorization denial remains in audit history; no Grant was widened and no database row was directly edited.

Known UNKNOWN Effects: None in the persistent pilot.
Pending Purge: None; no persistent pilot object has been Purged.
Pending migration: none; persistent pilot schema migration version is 11.
Rollback point: Step 7 checkpoint `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; verified external Step 0 snapshot (local path omitted for privacy).
Last verified state: full suite **82 passed, 1 optional search-receipt test skipped** in 40.975s; `compileall`, `pip check`, `git diff --check`, and tracked-change secret scan passed. Persistent root is schema 11, SQLite integrity `ok`, mode `NORMAL`, with 16/16 object payload hashes verified and no new persistent test Task/Run. T1–T12 Hosted-required conditions are PASS per the regression report. Live Codex multi-tier switching is unavailable and not claimed. Current T10 Runtime implementation checkpoint is `e42b0340a2828cdd868414887d3bc2b8c9770fe8`.
Next allowed action: final independent review and commit of this acceptance closeout; retain `Standalone Nexus: NOT IMPLEMENTED / DEFERRED`.

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
Step 2 checkpoint `b09251f1318cc23b66643887506dbccc524bafb8`; verified external Step 0 snapshot (local path omitted for privacy).

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
Step 3 checkpoint `59f077179c5f895c8dc3e28b5ac292ce3eeb775b`; verified external Step 0 snapshot (local path omitted for privacy).

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
Step 5 checkpoint `ac208477d70d623ebb7deb44b0dd55dc2aadfdc2`; verified external Step 0 snapshot (local path omitted for privacy).

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
Step 7 commit `09550fb7cb6db1bb3d9b2defccdbf09567f9e425`; verified external Step 0 snapshot (local path omitted for privacy). The status-file pre-edit backup was hash-verified before the original edit; its machine-local path is omitted.

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
- Step 6 commit `0fbc795943c166894d31ca8beca9456f05028729`; verified external Step 0 snapshot (local path omitted for privacy).

Next:
STEP 9 — only after the documented Runtime mode and controlled inspect API prerequisites are resolved.

## IMPLEMENTATION STEP 8 CONTINUATION — HOSTED BRIDGE + PERSISTENT PILOT

Goal:
Establish the smallest truthful Codex-hosted ingress and an isolated, persistent, non-sensitive pilot instance without changing Foundation Contract semantics or fabricating underlying model/provider metadata.

Inputs:
Steps 1–7 and corrective Step 9 Runtime/inspect prerequisites are committed; Codex can invoke local workspace commands; current exact underlying model ID and provider metadata are not exposed through an authoritative host interface.

Allowed files:
`adapters/client/`, focused Step 8/10 tests and regression/status docs. Additive schema/migration work is allowed only if it preserves existing stored semantics and can truthfully encode attached-host identity; otherwise stop that exact integration slice and preserve the current schemas. New isolated, ignored `nexus/data/`, `nexus/backups/`, and `nexus/purge-ledger/` contents may be created after confirming absent targets and inherited ACL. Existing source/data requires a same-directory timestamped hash-verified backup before editing.

Forbidden files:
Five Foundation Contract semantics; prior migrations or existing persistent records; global/business AGENTS, Skills, Codex config/credentials; real user data/secrets; network provider calls, external writes, model ID guesses, fabricated evaluation results; client direct SQLite writes.

Expected outputs:
A bounded Host receipt path using controlled Core APIs (not a second agent/runtime), and an isolated persistent pilot root with an inspectable restart/recovery path. Only authoritative/observable host facts may be persisted; if MODEL Run invariants cannot be satisfied without false metadata, record the precise compatibility blocker rather than forcing a value.

Exact tests:
Targeted Host receipt integration including persisted Task/Root Run, MODEL/TOOL boundaries, objects, deterministic verification, Trace and authorized inspect where the schemas permit; real local read-only TOOL behavior; persistent reopen/inspect/backup/restore. Then full `unittest discover`, schema/default-policy checks, `compileall`, `pip check`, `git diff --check`, and secret scan.

PASS criteria:
The current Codex-hosted execution and tool work produce authentic governed runtime records across restart, or each unsupported part is explicitly shown to be technically unrepresentable under current frozen semantics without faking facts. Independent Provider/Broker cases remain DEFERRED.

FAIL handling:
Fail closed at the narrow unsupported interface; do not weaken frozen semantics, invent model metadata, or mark Hosted bridge complete. Keep pilot data isolated and runtime DEVELOPMENT.

Rollback point:
Commit `497a991`; pre-edit hash-verified status backup `docs/operator/IMPLEMENTATION_STATUS.md.bak-20260925-131205`. Pilot root did not exist at start.

Do NOT:
- Treat a missing Nexus-native tool as an integration blocker when local command ingress is possible.
- Convert the Codex host/session identity into an invented underlying model identity.
- Store or inspect real history, credentials, or user payloads in the pilot.

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
