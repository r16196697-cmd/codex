# 2026-09-25 Persistent Codex-hosted Nexus Pilot

Status: DEVELOPMENT; Step 8 is PASS for Codex-hosted attachment scope and Step 9 is PASS; Step 10 and Hosted release acceptance remain PARTIAL.

Environment: Windows 10.0.22631.0; Python 3.11.0; SQLite 3.38.4 with FTS5; Git 2.40.0.windows.1.

## Persistent instance

- Data root: `nexus/data/codex-hosted-attached/` (Git-ignored; created only after confirming absent).
- Policy: local, secret-free, fail-closed policy with a narrowly named pilot trust anchor.
- Migration version: 10 after additive Purge identifier/state-fact/manifest-index migrations; SQLite integrity check: `ok`; mode: `NORMAL`.
- Stored only synthetic `PUBLIC` acceptance input/result; no real history, credentials, provider calls, or external writes.
- Hosted E2E Task `hosted-task` completed successfully. The early probe `hosted-pilot-root-20260925` that was stranded by an exact-resource classification denial was subsequently cancelled via its existing Grant and normal Trace transition. The denial remains in audit history; no Grant was widened and no SQLite row was directly edited.

## Successful actual persistent probe

### Codex Hosted Bridge E2E

- Reusable entrypoint: `adapters/client/hosted.py`, class `CodexHostedBridge`; it delegates to existing Runtime/Object/Trace/Budget/Verification/Inspect services and does not write SQLite directly.
- Persistent root: `nexus/data/codex-hosted-attached/` (ignored, synthetic PUBLIC data only). The E2E input is `eval/regression/fixtures/nexus-hosted-e2e-input.csv`; model-result receipt is `eval/regression/fixtures/nexus-hosted-e2e-model-result.txt`.
- Task `hosted-task`; Root Run `hosted-root` (ORCHESTRATOR); MODEL Child Run `hosted-model-run` (manifest `hosted-model-manifest`); actual local read-only TOOL Child Run `hosted-tool-run` (manifest `hosted-tool-manifest`). DAG nodes: `hosted-model-node`, `hosted-tool-node`.
- MODEL receipt records `executor_kind=MODEL`, `execution_source=CODEX_HOST_DECLARED`, `host_kind=CODEX`, `model_identity_status=UNAVAILABLE`. It does not claim Codex platform instrumentation and stores no guessed provider/model/request/token fields.
- TOOL Run corresponds to an actual confined read of `eval/regression/fixtures/nexus-hosted-e2e-input.csv`; descriptor was checked as reviewed READ_ONLY/no-egress, path traversal denied, and the output includes its relative path, byte count and SHA-256. It does not simulate a tool call.
- Nexus Objects/Artifacts: `hosted-model-artifact`, `hosted-tool-artifact`; Evidence: `hosted-search-evidence`, with an actual public search source URL (`https://www.sqlite.org/fts5.html`) and retrieval/provenance receipt. Verifications `hosted-model-verification`, `hosted-search-verification`, and `hosted-tool-verification` each PASS for T1 SHA-256 object integrity only—not semantic truth. Trace references the Objects and the three child Run replays end SUCCEEDED.
- Authorized inspect and Run replay passed after closing/reopening the persistent ObjectStore. A separate process invoked `adapters.client inspect task hosted-task` against the persistent root and returned the completed Task, Root/MODEL/TOOL Runs, 3 Artifacts/Evidence and 3 integrity Verifications.
- A real snapshot used SQLite's backup API plus payload/policy copies at `nexus/backups/codex-hosted-attached-20260925-140800-post-hosted-e2e`; an isolated restore at `...-post-hosted-e2e-restore` reopened successfully and preserved the Task, all three Run replays, Verifications, and NORMAL mode. This backup has no purge history and therefore is not the T9 non-resurrection drill.
- The earlier search-receipt experiment was post-search only and is superseded for T11 lifecycle evidence by the true begin/search/finish sequence recorded in the T11 row below. Both runs were isolated; neither claims platform-native telemetry or a provider/request ID.
- Budget reservation uses synthetic `test-units` for deterministic acceptance only; it is not a real token or monetary cost measurement.

Task `hosted-pilot-task-20260925b`; Root Run `hosted-pilot-root-20260925b`; exact INSPECT Grant `hosted-pilot-root-grant-20260925b`.

The Codex-invoked local operation created a Task, ORCHESTRATOR Root Run, input NexusObject, TaskContract, Root RunManifest, output Artifact, object-created Trace reference, and deterministic SHA-256 Verification. Probe result: Python 3.11.0; SQLite 3.38.4; FTS5 available. Run replay after reopen: `SUCCEEDED`, 6 events. Authorized CLI Task inspect after reopen: `SUCCEEDED`, output Artifact and Verification `PASS`. This demonstrates a persistent Core receipt for a local probe; it does **not** demonstrate a MODEL Child Run, TOOL Child Run, or Search ingestion.

## Real snapshot-copy restore

- Snapshot: `nexus/backups/codex-hosted-attached-20260925-132121/` using SQLite's backup API plus copied object files and policy.
- Restore copy: `nexus/backups/codex-hosted-attached-20260925-132121-restore/` (isolated; source left unchanged).
- Restored checks: authorized Task inspect `SUCCEEDED`; Root Run replay `SUCCEEDED`; 6 Trace events; output raw integrity `true`; persisted Verification `PASS`; mode `NORMAL`.
- Limitation: this snapshot contains no historical Purge event, so this is not a real-instance Purge Ledger replay/non-resurrection pass. The separate synthetic recovery test remains the evidence for that path.

## Acceptance classification after this run

| Test | Status | Evidence / reason |
|---|---|---|
| T1 | PASS (Codex-hosted) | `tests/integration/test_hosted_bridge.py::HostedBridgeTests.test_codex_host_model_receipt_and_real_read_only_tool_run_survive_reopen` checks Task/ORCHESTRATOR/MODEL/actual TOOL, all three readable kind-specific manifests, no Root model/provider identity, inherited boundary/classification, Artifact/Verification/Evidence/Trace, reopen/replay/inspect and quarantined Memory Candidate. `tests/integration/test_object_store.py::test_byte_tampering_and_missing_payload_never_verify` verifies byte tampering and missing payload fail integrity. Persisted pilot Task `hosted-task` supplies the real local E2E record. |
| T2 | PASS | Payload rename/DB-commit crash and metadata-schema rejection are covered by `tests/integration/test_object_store.py`; atomic Trace/Run rollback and lost-response transition replay by `tests/integration/test_trace_state.py`; `test_transition_retry_returns_recorded_result_before_stale_state_check` verifies same-command Task/Root/transition replay after reopen without duplicate events, while `test_new_independent_task_uses_a_fresh_command_id_and_identical_replay` proves a distinct Task uses a fresh command ID and exact replay is single-write. Hosted MODEL/TOOL output retry preserves Object/Trace/Verification counts; Effect UNKNOWN is reconciled without redispatch; Purge delete crash/retry completes under the original command. Persistent Hosted fixture now recognizes its completed Task read-only instead of regenerating timestamped Grants; same-ID retry semantics remain exact and no `COMMAND_CONFLICT` occurs. |
| T3 | PASS (Core fake/sandbox only) | Existing isolated UNKNOWN/no-retry, authoritative fake reconciliation and independent compensation regression; no real external write. |
| T4 | PASS (Core) | Existing authority-chain trust/scope/expiry/revocation tests; pilot also uses narrow Grants. |
| T5 | PASS (v0.1 schema scope) | `tests/integration/test_object_store.py::test_v6_snapshot_replays_v7_v8_v9_and_v10_migrations_deterministically` rehearses the synthetic v6→v10 sequence; applied checksums, migration gap detection, SQLite integrity and `SCHEMA_UNSUPPORTED` for unknown versions pass. Hosted ORCHESTRATOR v1 / attached MODEL v2 / TOOL v1 manifests and replay are verified. Migrations 0008–0010 are additive; applied migrations 0001–0008 were not edited. |
| T6 | PASS (Core tested scope) | Existing SHA-256/tamper/classification/lineage tests pass; OTel egress remains disabled and not an active integration. |
| T7 | PASS (Core tested scope) | Existing bound-approval/revocation/hash-race regressions pass; no live external Effect. |
| T8 | PASS (Core tested scope) | Existing CAS and atomic Task-budget concurrency regressions pass. |
| T9 | PASS (v0.1 implemented/isolated) | `tests/integration/test_memory_purge.py::test_purge_backup_restore_replays_external_ledger_without_resurrection` verifies payload/RunManifest removal; Memory/FTS removal; Approval target/hash/scope erasure while retaining approver/decision/time; Effect target/hash/idempotency/receipt erasure while retaining state axes/outcome; Trace references and identifying metadata redaction; purge-record path/hash clearing; and removal of `run_manifest_inputs` rows that reference a purged input or manifest. It then restores the pre-Purge SQLite+object snapshot, replays the independent Purge Ledger/barrier and repeats the non-resurrection checks. Migration 0009 permits only unchanged enum-constrained Effect state axes in affected Trace metadata; migration 0010 permits deletion of the derived manifest-input index only after a referenced object is tombstoned. SQLite `secure_delete` and WAL truncate are exercised. |
| T10 | PARTIAL | Hosted MODEL/TOOL execution, kind-specific manifests, deterministic initial Tool/E0/E1/E2 selection, hard locality constraints, and DAG/Trace evidence pass in `test_hosted_bridge.py` and `test_deterministic_runtime.py::test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass`. The requested escalation/fallback path is still absent: `subtasks.scheduled_run_id` is single-assignment, `bind_hosted_run_to_subtask()` accepts only a still-PENDING unbound node, and RouteDecision v1 requires a selected `model_id`/profile. No truthful E1→E2 hosted attempt relationship, return-to-coordinator event, or policy/budget-denied fallback is currently executable. This is a Hosted Runtime gap, not a Standalone Provider deferral; no identity or Run was fabricated. |
| T11 | PASS (Codex-hosted observable scope) | A real Codex Host search was bracketed in an isolated fixture: `hosted-codex-search-run` was created and reached RUNNING before the actual search call; afterwards query Object `hosted-search-query`, URL `https://www.sqlite.org/fts5.html`, observed excerpt and retrieval timestamp were recorded as Evidence `hosted-search-evidence`, exact-byte Verification `hosted-search-verification` returned PASS, and Trace linked the Evidence. The TOOL Run was closed SUCCEEDED; close/reopen inspection showed Task `hosted-task` SUCCEEDED, Run SUCCEEDED, verification PASS, SQLite integrity `ok`. Receipt labels `CODEX_HOST_DECLARED`, not a provider/request ID or platform telemetry. Automated `test_untrusted_search_excerpt_is_data_and_conflicts_stay_unknown` proves injected instructions remain data, do not change Grants, a contradictory candidate stays QUARANTINED/UNKNOWN, and missing evidence returns INCONCLUSIVE; evidence independence stays UNKNOWN. Independent Search API remains `DEFERRED / NOT_CONFIGURED`. |
| T12 | PASS (isolated persistent-root clone) | `tests/integration/test_t12_persistent_clone.py::PersistentRootCloneRecoveryTests.test_persistent_root_clone_modes_and_purge_snapshot_replay` clones the real schema-10 Hosted root using SQLite backup plus object/policy copies, creates synthetic PUBLIC lineage/Memory/FTS data, saves a pre-Purge snapshot, Purges, restores the old snapshot, starts RECOVERY, denies Memory and inspect access before validation, replays the independent Purge Ledger/barrier, checks integrity/schema and unavailable payload/FTS, then exercises SAFE and STATELESS gates before returning to NORMAL. Closing/reopening the restored clone preserves NORMAL and the payload remains unavailable. The live root itself was not purged. T9's dedicated recovery test remains the detailed at-rest target/hash/path/Approval/Effect/Trace/RunManifest/index non-resurrection evidence. |

### T2 persistent mutation surface inventory

| Semantic family | Persistent entrypoints / shared record path | Contract evidence |
|---|---|---|
| Object / LogicalRef | `ObjectStore.put_object`, relation, ref create/CAS → `_replay_command` / `_record_command` | payload-rename crash/reopen, same-command object retry, CAS one-winner/idempotency |
| Trace / Run / DAG / Route | `TraceRuntime` create/append/transition; `DeterministicRuntime` bind/schedule/route → CommandLedger with state+Trace transaction boundaries | same-command transition replay after reopen; injected event insert rolls state back; Hosted Run/output receipt replay |
| Approval / Authority | principal, trust anchor, Grant, Approval, ClassificationAssertion and denial records → CommandLedger; denials preserve audit event | authority scope/revoke/expiry/bound-approval tests; same shared Core ledger |
| Effect | descriptor, Effect create/transition/commit/reconcile/compensation → CommandLedger; UNKNOWN external dispatch is reconciled and never blindly retried | lost-response/UNKNOWN and independent compensation regression |
| Memory | raw retention and candidate admission → CommandLedger; `purge_refs` is private and runs only inside the Purge transaction | candidate gating; Purge/index removal and response-loss recovery |
| Purge | plan/execute/partial/release → CommandLedger; external append-only journal has its own hash-chain/replay key | crash after journal append, delete transaction failure/retry, old snapshot replay |
| Mode / control plane | mode change and validated recovery completion → CommandLedger; mode fact, Trace and state commit together | mode event failure rolls all back; idempotent retry and close/reopen tests |
| Budget / reservations | account, reserve, settle, release → CommandLedger and atomic Task-account transaction | concurrent reservation bound and idempotent settle/release tests |

Code audit found no public Core mutation family in this list that bypasses the shared CommandLedger. The independent Purge journal is the intentional separate durable barrier authority and is itself hash-chained/idempotent on replay; `purge_refs` and identifier redaction are nested in the caller's Purge transaction, not public standalone mutation entrypoints.

## Regression commands

- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 74 passed in 24.655s.
- Latest rerun after Host Bridge integration: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **75 passed in 26.828s** (prior identical run: 28.554s).
- Step 10 focused ObjectStore/Trace/Purge regression after migration and redaction — 30 passed; focused Purge recovery/mode drill — 7 passed.
- Full regression after final T11/T12 updates: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **80 passed, 1 environment-gated standalone receipt test skipped** in 35.468s. A separate live isolated Hosted sequence explicitly began the TOOL Run before calling Codex Search and finished it only after the observed result was available; no provider telemetry was claimed.
- Persistent Hosted fixture rerun passed against the schema-10 root; completed Task/ORCHESTRATOR/MODEL/TOOL statuses, replay and Verifications inspected without regenerating Grant payloads or adding persistent test records.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `git diff --check` — passed after removing Markdown trailing whitespace (Git may print its line-ending advisory).
- Actual persistent root snapshots: pre-v9 `nexus/backups/codex-hosted-attached-20260925-155850-pre-v9` (schema 8, integrity `ok`, 3 Tasks, 0 Purge Ledger rows); pre-v10 `nexus/backups/codex-hosted-attached-20260925-162120-pre-v10` (schema 9, integrity `ok`, 3 Tasks, 0 Purge Ledger rows). The root now runs schema 10 and passed reopen/fixture replay. The T12 clone/Purge/old-snapshot drill passed without purging the live root.
- T10 remains PARTIAL because the existing Runtime has no truthful multi-attempt/escalation/fallback lifecycle; implementing it without a scoped change to current DAG/RouteDecision persistence is not a test-only fix. No second Provider or routing engine was introduced.
- T11 Hosted observable conditions PASS; independent Search Provider/API remains deferred.
- T12 combined persistent-root-clone recovery drill PASS; the live root remained unchanged.
- Independent Model Provider/API, Search Provider/API, real Provider Credential Broker use, multi-provider routing and standalone model execution remain `DEFERRED / NOT_CONFIGURED` by operator decision. None of the Hosted gaps above are counted as deferred.
