# 2026-09-25 Persistent Codex-hosted Nexus Pilot

Status: DEVELOPMENT; Step 8 is PASS for Codex-hosted attachment scope and Step 9 is PASS; Step 10 and Hosted release acceptance remain PARTIAL.

Environment: Windows 10.0.22631.0; Python 3.11.0; SQLite 3.38.4 with FTS5; Git 2.40.0.windows.1.

## Persistent instance

- Data root: `nexus/data/codex-hosted-attached/` (Git-ignored; created only after confirming absent).
- Policy: local, secret-free, fail-closed policy with a narrowly named pilot trust anchor.
- Migration version: 7; SQLite integrity check: `ok`; initial mode: `NORMAL`.
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
- The current web search surface returned the official SQLite FTS5 URL, and its URL/excerpt provenance was stored and verified as an Evidence Object. Search was invoked before this Task and the current client does not expose a Codex-native search telemetry receipt to the adapter; consequently there is no distinct Search TOOL Child Run. T11 stays PARTIAL rather than claiming a fully instrumented search execution chain.
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
| T2 | PARTIAL | `test_crash_after_atomic_payload_rename_before_db_commit_cleans_orphan_on_reopen`, `test_state_and_trace_rollback_together_when_event_insert_fails`, `test_transition_retry_returns_recorded_result_before_stale_state_check`, and the Hosted Bridge test's MODEL **and TOOL** output retries after store reopen exercise payload rename/metadata failure and lost output/transition responses without duplicate Objects, Trace, Verification or state transitions. Fake Effect UNKNOWN/no-redispatch also passes. Still missing a full per-mutation-command response-loss matrix (including create Task/Run and all command boundaries). An attempted persistent-fixture rerun failed in setup with `COMMAND_CONFLICT` because it reused a command ID with a newly timestamped Grant; no database mutation occurred. |
| T3 | PASS (Core fake/sandbox only) | Existing isolated UNKNOWN/no-retry, authoritative fake reconciliation and independent compensation regression; no real external write. |
| T4 | PASS (Core) | Existing authority-chain trust/scope/expiry/revocation tests; pilot also uses narrow Grants. |
| T5 | PASS (v0.1 schema scope) | `tests/integration/test_object_store.py::test_v6_snapshot_replays_v7_runtime_mode_migration` rehearses v6→v7 deterministically; `test_unknown_schema_version_is_rejected_as_unsupported` asserts `SCHEMA_UNSUPPORTED`. The Hosted fixture reads ORCHESTRATOR v1, attached MODEL v2, and TOOL v1 manifests; Run/Task Trace replay after reopen is covered by `test_reopen_replay_reconstructs_run_projection` and the Hosted Bridge test. Existing schema versions/records are not rewritten. |
| T6 | PASS (Core tested scope) | Existing SHA-256/tamper/classification/lineage tests pass; OTel egress remains disabled and not an active integration. |
| T7 | PASS (Core tested scope) | Existing bound-approval/revocation/hash-race regressions pass; no live external Effect. |
| T8 | PASS (Core tested scope) | Existing CAS and atomic Task-budget concurrency regressions pass. |
| T9 | PARTIAL | `test_purge_backup_restore_replays_external_ledger_without_resurrection` now verifies payload, RunManifest payload, raw/admitted FTS and public inspect projections stay unavailable after Purge and old-snapshot journal replay. It intentionally confirms immutable Approval/Effect rows still retain the original target/hash in SQLite; inspect masking alone does not satisfy §7.1. Complete on-disk redaction of governed identifiers across Approval, Effect, Trace metadata and other derived records (while preserving minimum audit facts) plus their restore verification is still unmet. |
| T10 | PARTIAL | Hosted test verifies real MODEL and TOOL child records, executor-specific manifests, Run replay, boundary/classification inheritance, and a reviewed read-only descriptor. `tests/integration/test_deterministic_runtime.py::test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass` exercises deterministic Tool/E0/E1/E2 choices and locality hard constraint. Formal E2 escalation and return/fallback behavior are not implemented/evidenced; the test matrix only chooses by initial route. |
| T11 | PARTIAL | The actual Codex web-search result URL (`https://www.sqlite.org/fts5.html`) is persisted as Evidence with provenance, object-integrity Verification and Trace reference; no Search API was purchased. The Host interface cannot capture this client's Search action as a distinct TOOL Run with query/time receipt, and injection-resistance plus conflicting-evidence→INCONCLUSIVE and independence-axis cases lack end-to-end acceptance evidence. Independent Search Provider/API remains `DEFERRED / NOT_CONFIGURED`; that deferral is not counted as Hosted PASS. |
| T12 | PARTIAL | `tests/integration/test_runtime_modes.py` exercises the four-mode matrix, bypass denial, reopen persistence and validated Recovery exit; `test_old_snapshot_recovery_replays_purge_ledger_before_normal` restores a pre-Purge snapshot, replays the independent journal/barrier, verifies unavailable payload/index, and exits RECOVERY only after validation. The persistent root currently reopens `NORMAL` with `PRAGMA integrity_check=ok`, but has no Purge history; no single persisted-root clone drill yet combines all modes and historical Purge restoration. |

## Regression commands

- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 74 passed in 24.655s.
- Latest rerun after Host Bridge integration: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **75 passed in 26.828s** (prior identical run: 28.554s).
- Step 10 continuation focused Core regression across ObjectStore, Trace, Hosted Bridge, Purge, Runtime Modes and Deterministic Runtime — 51 passed; latest full regression after T1 manifest/boundary, T2 model/tool lost-response retries, and T9 RunManifest recovery assertions — **76 passed in 28.286s**.
- Persistent Host Bridge fixture replay attempt — FAIL at setup with `COMMAND_CONFLICT`: fixed test command IDs are combined with fresh timestamps in Grant requests. The transaction was rejected before data writes; the persistent root remains schema 7, integrity `ok`, mode `NORMAL`, 3 Tasks / 5 Runs / 0 Purge Ledger rows. This is a repeatability limitation in the test fixture, not evidence that the existing persisted E2E was lost.
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `git diff --check` — passed after removing Markdown trailing whitespace (Git may print its line-ending advisory).
- Independent Model Provider/API, Search Provider/API, real Provider Credential Broker use, multi-provider routing and standalone model execution remain `DEFERRED / NOT_CONFIGURED` by operator decision. None of the Hosted gaps above are counted as deferred.
