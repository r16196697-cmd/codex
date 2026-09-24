# 2026-09-25 Runtime mode / inspect corrective regression

Environment: Windows 10.0.22631.0; Python 3.11.0; SQLite 3.38.4 + FTS5; branch `nexus-v2-runtime`; disposable temporary databases only; no providers, secrets, network calls, or persistent Nexus database.

Executed:

- Follow-up audit: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **65 passed** in 19.736s; `compileall`, `pip check`, `git diff --check`, and CLI `--help` passed. This reruns the mode/inspect and client regression coverage; it does not create or substitute for the manual's formal T1–T12 Acceptance Suite.
- Mode-to-Trace correction: focused Runtime/Trace/replay/deterministic-runtime/client regression — **32 passed**; full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **68 passed** in 21.325s. `compileall`, `pip check`, CLI `--help`, `mode set --help`, and `git diff --check` passed. The full suite still is not a formal T1–T12 acceptance run.
- Policy-configured CLI integration: on a disposable instance, authorized `mode set SAFE` persisted both `runtime_mode_state` and its Root-Run TraceEvent; reopen verification matched. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.392s; `compileall`, `pip check`, CLI help, and `git diff --check` passed. No deployment root was initialized.
- CLI inspect integration: on a disposable policy-configured instance, a distinct exact `INSPECT` Grant returned the Task/Root-Run projection after mode-set; no payload field was exposed. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.967s. No persistent deployment root was initialized.
- Full old-snapshot Recovery drill: a pre-purge snapshot was put into RECOVERY through an authorized Runtime mode command; after purge completed in the source instance, the snapshot was restored, Core access was denied while isolated, the independent Purge Ledger was replayed, FTS indexes rebuilt, and the purged payload/search result verified unavailable before validated return to NORMAL. Targeted case passed; full suite — **70 passed** in 23.294s. Synthetic disposable data only.
- T2/T5/T9 fault-window rerun: object payload rename followed by injected metadata-transaction abort and reopen cleanup; synthetic v6 schema snapshot replayed through migration 0007; independent purge-journal barrier append interrupted before SQLite install, then journal replay restored a PARTIAL barrier which blocked retention before and after reopen. Focused cases passed; full suite — **73 passed** in 22.942s; `compileall`, `pip check`, and `git diff --check` passed.
- Purge deletion transaction recovery: an SQLite trigger interrupted after filesystem unlink; metadata rolled back while the barrier remained ACTIVE. Reopening and retrying the same command completed the remaining deletion, maintained empty search results, then released the barrier. Targeted case passed; full suite — **74 passed** in 21.779s; static checks passed.
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **65 passed** (rerun after adding Recovery `PRAGMA integrity_check`).
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — PASS.
- `.venv\Scripts\python.exe -m pip check` — PASS, no broken requirements.
- `git diff --check` — PASS (Git emitted only the configured LF-to-CRLF advisory).
- `.venv\Scripts\python.exe -m adapters.client --help` — PASS.

This is a regression mapping, not a claim that the full manual `nexus test acceptance` suite exists or that release acceptance passed.

| Case | Result | Evidence / limit |
|---|---|---|
| T1 normal Task/Run/DAG/Manifest path | PASS (Core) | `test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass`; verification and governed memory cases also pass. No live Codex Host ingestion. |
| T2 crash/idempotency | PARTIAL | Command replay/conflict, transition replay, ambiguous Effect response-loss, and payload-rename→metadata-transaction-abort→reopen orphan cleanup pass. Not every documented DB/model/tool crash injection point was exercised. |
| T3 Effect / UNKNOWN / compensation | PASS (synthetic) | `test_unknown_effect_is_reconciled_without_retry_and_compensation_is_independent`; live inspect shows all three axes and configured fake channel; no real external write. |
| T4 Authority chain | PASS | Authority integration coverage for trust anchors, containment, expiry/revocation, scope and denial. |
| T5 schema evolution | PARTIAL | A synthetic v6 database snapshot upgrades through migration 0007 to v7 with NORMAL mode initialization and SQLite integrity intact; checksum guards and schema validation pass. No representative old production database or all historical compatibility cases were available for complete acceptance. |
| T6 integrity / classification / lineage | PASS (Core) | Tamper detection, SHA-256, classification, lineage, egress and Trace admission regression pass. No OTel destination is enabled. |
| T7 approval / revoke race | PASS (synthetic) | Existing approval hash/revocation-before-commit test passes; inspect redacts payload hash unless the separate `INSPECT_PROTECTED` authorization passes. |
| T8 CAS / budget concurrency | PASS | Existing CAS winner and atomic budget reservation tests pass. |
| T9 Purge barrier / restore | PARTIAL | Active Run + UNKNOWN Effect remains PARTIAL and is inspected/rendered without a completion claim; old-backup anti-resurrection and T12 Recovery drill pass. Fault injection covers interruption after external journal append/before SQLite barrier install and after filesystem unlink/before metadata commit; barriers persist, derived writes stay blocked, and same-command retry completes after reopen. Cross-record derived-identifier cleanup/verification across Approval, Effect, Manifest, Trace and backup copies is not yet complete. |
| T10 DAG / routing | PASS (fake profiles) | Deterministic routing, Run kinds, Manifest constraints, hard constraints and E0/E1/E2 tests pass. No independent Provider or live Host route recording. |
| T11 search / verifier | DEFERRED / NOT_APPLICABLE for Provider | Verifier/Memory truth and independence Core tests pass. Hosted search-to-Evidence/source-URL ingestion is not integrated; independent Search API is intentionally not configured. |
| T12 modes / recovery | PASS (isolated) | Four-mode Runtime/API gates, SAFE/STATELESS bypass denials, authorized Root-Run-bound mode Trace events, atomic rollback, replay and pending-barrier/corrupt-payload isolation pass. New end-to-end test restores a pre-purge snapshot already isolated in RECOVERY, replays the independent Purge Ledger, rebuilds FTS, confirms the payload and search results remain unavailable, then returns to NORMAL only after validation. Synthetic disposable root; not a persistent deployment recovery. |

Release disposition: **DEVELOPMENT**. The 74-test suite and isolated T12 recovery drill pass, along with T2/T5/T9 fault-window evidence; T2/T5/T9 remain partial and the Step 8 Codex-hosted execution bridge is not connected to a live runtime. Step 9's mode and inspect commands were tested end-to-end only on disposable data, not a deployment root. Independent Model/Search Provider and Credential Broker work remains `DEFERRED / NOT_CONFIGURED` by operator decision.
