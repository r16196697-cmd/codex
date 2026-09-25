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
| T1 | PARTIAL | Hosted persistent Task/Root/MODEL/actual TOOL/input/output Artifacts/Evidence/T1 Verification/Trace/inspect/reopen pass. The formal Memory Candidate/admission path and remaining manual normal-path observations are not demonstrated in this E2E. |
| T2 | PARTIAL | Existing 75-test regression covers payload rename/metadata failure cleanup, a Trace command response-loss/replay case, and Purge delete retry. The complete manual crash-point matrix plus Run/Task command-idempotent response-loss recovery has not all been exercised against the persistent instance. |
| T3 | PASS (Core fake/sandbox only) | Existing isolated UNKNOWN/no-retry, authoritative fake reconciliation and independent compensation regression; no real external write. |
| T4 | PASS (Core) | Existing authority-chain trust/scope/expiry/revocation tests; pilot also uses narrow Grants. |
| T5 | PARTIAL | Synthetic v6→v7 database migration passes. The old schema reader, legacy persisted object/version behavior and full legacy Run/Trace replay acceptance conditions remain open. |
| T6 | PASS (Core tested scope) | Existing SHA-256/tamper/classification/lineage tests pass; OTel egress remains disabled and not an active integration. |
| T7 | PASS (Core tested scope) | Existing bound-approval/revocation/hash-race regressions pass; no live external Effect. |
| T8 | PASS (Core tested scope) | Existing CAS and atomic Task-budget concurrency regressions pass. |
| T9 | PARTIAL | Isolated barrier/journal/delete-retry/old-snapshot recovery tests pass; Hosted E2E snapshot restore passes. Not yet proven together: purge impact/redaction for Approval, Effect, RunManifest, Trace, index/cache, derived hash/path/identifier and historical backup non-resurrection on a persistent purge ledger. |
| T10 | PARTIAL | Persistent E2E now has a truthful host-declared MODEL Run, actual read-only TOOL Run, DAG-bound manifests, Artifacts, integrity Verification and replay. The remaining formal routing/escalation/quality/effect acceptance matrix is not fully evidenced by this attached-host E2E. |
| T11 | PARTIAL | Actual public search result URL was ingested as Evidence with provenance, T1 byte-integrity Verification and Trace ref; no Search API was purchased. The search action is not separately represented as a TOOL Run, and semantic/evidence/method independence, conflict and prompt-injection acceptance checks are not fully completed. Independent Search API remains DEFERRED. |
| T12 | PARTIAL | Isolated four-mode and old-snapshot/Purge-Ledger recovery drill pass; persistent Hosted Task close/reopen, CLI inspect and non-purged backup restore pass. The four modes and historical Purge Ledger recovery/non-resurrection are not all exercised on the persistent deployment root. |

## Regression commands

- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — 74 passed in 24.655s.
- Latest rerun after Host Bridge integration: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **75 passed in 26.828s** (prior identical run: 28.554s).
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — passed.
- `.venv\Scripts\python.exe -m pip check` — no broken requirements.
- `git diff --check` — passed after removing Markdown trailing whitespace (Git may print its line-ending advisory).
- Independent Model Provider/API, Search Provider/API, real Provider Credential Broker use, multi-provider routing and standalone model execution remain `DEFERRED / NOT_CONFIGURED` by operator decision. None of the Hosted gaps above are counted as deferred.
