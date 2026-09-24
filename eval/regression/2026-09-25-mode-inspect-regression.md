# 2026-09-25 Runtime mode / inspect corrective regression

Environment: Windows 10.0.22631.0; Python 3.11.0; SQLite 3.38.4 + FTS5; branch `nexus-v2-runtime`; disposable temporary databases only; no providers, secrets, network calls, or persistent Nexus database.

Executed:

- Follow-up audit: `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **65 passed** in 19.736s; `compileall`, `pip check`, `git diff --check`, and CLI `--help` passed. This reruns the mode/inspect and client regression coverage; it does not create or substitute for the manual's formal T1–T12 Acceptance Suite.
- Mode-to-Trace correction: focused Runtime/Trace/replay/deterministic-runtime/client regression — **32 passed**; full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **68 passed** in 21.325s. `compileall`, `pip check`, CLI `--help`, `mode set --help`, and `git diff --check` passed. The full suite still is not a formal T1–T12 acceptance run.
- Policy-configured CLI integration: on a disposable instance, authorized `mode set SAFE` persisted both `runtime_mode_state` and its Root-Run TraceEvent; reopen verification matched. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.392s; `compileall`, `pip check`, CLI help, and `git diff --check` passed. No deployment root was initialized.
- CLI inspect integration: on a disposable policy-configured instance, a distinct exact `INSPECT` Grant returned the Task/Root-Run projection after mode-set; no payload field was exposed. Full `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **69 passed** in 21.967s. No persistent deployment root was initialized.
- `.venv\Scripts\python.exe -m unittest discover -s tests -v` — **65 passed** (rerun after adding Recovery `PRAGMA integrity_check`).
- `.venv\Scripts\python.exe -m compileall -q adapters kernel tests` — PASS.
- `.venv\Scripts\python.exe -m pip check` — PASS, no broken requirements.
- `git diff --check` — PASS (Git emitted only the configured LF-to-CRLF advisory).
- `.venv\Scripts\python.exe -m adapters.client --help` — PASS.

This is a regression mapping, not a claim that the full manual `nexus test acceptance` suite exists or that release acceptance passed.

| Case | Result | Evidence / limit |
|---|---|---|
| T1 normal Task/Run/DAG/Manifest path | PASS (Core) | `test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass`; verification and governed memory cases also pass. No live Codex Host ingestion. |
| T2 crash/idempotency | PARTIAL | Command replay/conflict, transition replay and ambiguous Effect response-loss paths pass. Not every documented filesystem/DB/model/tool crash injection point was exercised. |
| T3 Effect / UNKNOWN / compensation | PASS (synthetic) | `test_unknown_effect_is_reconciled_without_retry_and_compensation_is_independent`; live inspect shows all three axes and configured fake channel; no real external write. |
| T4 Authority chain | PASS | Authority integration coverage for trust anchors, containment, expiry/revocation, scope and denial. |
| T5 schema evolution | PARTIAL | Migration checksums and schema validation pass; no old production database was available for a complete historical replay migration drill. |
| T6 integrity / classification / lineage | PASS (Core) | Tamper detection, SHA-256, classification, lineage, egress and Trace admission regression pass. No OTel destination is enabled. |
| T7 approval / revoke race | PASS (synthetic) | Existing approval hash/revocation-before-commit test passes; inspect redacts payload hash unless the separate `INSPECT_PROTECTED` authorization passes. |
| T8 CAS / budget concurrency | PASS | Existing CAS winner and atomic budget reservation tests pass. |
| T9 Purge barrier / restore | PARTIAL | PARTIAL with active Run + UNKNOWN Effect is inspected/rendered as PARTIAL; old-backup PurgeLedger anti-resurrection test passes. No full recovery-mode restore of a representative old snapshot was run. |
| T10 DAG / routing | PASS (fake profiles) | Deterministic routing, Run kinds, Manifest constraints, hard constraints and E0/E1/E2 tests pass. No independent Provider or live Host route recording. |
| T11 search / verifier | DEFERRED / NOT_APPLICABLE for Provider | Verifier/Memory truth and independence Core tests pass. Hosted search-to-Evidence/source-URL ingestion is not integrated; independent Search API is intentionally not configured. |
| T12 modes / recovery | PARTIAL | Four-mode Runtime/API gates, SAFE/STATELESS bypass denials, authorized Root-Run-bound mode Trace events, atomic rollback, replay, pending-barrier/corrupt-payload isolation, and successful isolated Recovery validation exit pass. Full old-snapshot restore→Purge replay→index rebuild→unavailability verification against a deployed data root remains unrun. |

Release disposition: **DEVELOPMENT**. The 69 passing tests do not satisfy required `T1–T9, T12` release acceptance because T2/T5/T9/T12 remain partial, and the Step 8 Codex-hosted execution bridge is not connected to a live runtime. Step 9's mode command was tested end-to-end only on disposable data, not a deployment root. Independent Model/Search Provider and Credential Broker work remains `DEFERRED / NOT_CONFIGURED` by operator decision.
