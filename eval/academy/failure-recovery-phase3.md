# Bootstrap Academy Phase 3 — Failure Recovery / Operational Resilience

## Purpose and limits

This Academy evaluation exercises existing v0.1 Runtime/Core behavior only. It adds no Core code, schema, migration, authority rule, or runtime mode. The committed failure matrix is authored before evaluation; its expected durable states and allowed/denied actions are independent of test output. The runner executes named existing synthetic integration tests plus one Academy-only local fake-dispatch matrix. Those tests use isolated temporary stores under a fresh timestamped Academy scratch root. Database, payload, snapshot, and journal files remain outside Git.

Each case records a `SIMULATED_CRASH_BOUNDARY`: explicit close/reopen, injected service exception, lost response, or controlled fake executor result. This is not an OS crash, power-cut, or physical media durability test. Windows does not execute the POSIX parent-directory `fsync` path. `PHYSICAL_DURABILITY` is therefore `NOT FULLY QUALIFIED`.

## Frozen matrix and truth source

`failure-recovery-phase3-fixture.json` contains 27 fixed cases covering command replay/conflict, revocation, deterministic and UNKNOWN Effects, reconciliation, separate compensation, Hosted setup cleanup, purge barriers and replay dominance, old snapshot recovery, stale/missing/truncated/corrupt/wrong-identity journal conditions, all four Runtime Modes, payload integrity, unknown schema, and restart projection. Expected values are written in the fixture before the final evaluation run. Machine result records the exact fixture SHA-256 and the concrete test IDs used as evidence. Runner bring-up runs that failed before loading the intended test modules are harness diagnostics, not evaluation observations.

The runner explicitly puts the repository root on `sys.path` so invocation as a script resolves the same `tests.*` modules as unittest discovery. It uses the current Core's tests as the subject under evaluation rather than treating Academy's result as a new implementation. A passing evidence test means its own explicit assertions held; a missing, skipped, or failed mapped test does not count as qualification and is recorded as inconclusive/blocker. The runner refuses any existing scratch-root path, preventing accidental overwrite.

## Effect outcomes

The Academy-only effect probe uses `DeterministicRuntimeTests`' synthetic fixture and an in-process fake dispatcher/reconciliation port. It covers `COMMITTED`, deterministic `NOT_COMMITTED`, ambiguous dispatch with `UNKNOWN`, authoritative reconciliation to either terminal outcome, and `INCONCLUSIVE` reconciliation that leaves the Effect `UNKNOWN`. For every case, exact commit replay is performed and the fake dispatcher call count must remain one. No network dispatcher or real external write is configured.

## Evidence interpretation

- Command retry evidence separately checks exact replay, changed-request conflict, reopen persistence, and before/after object/command/Trace counts.
- Revocation evidence distinguishes immutable historical replay from fresh work denied by revoked authority.
- Effect compensation evidence checks that the original committed Effect remains committed and a separate related Effect records compensation.
- Purge/recovery evidence uses disposable Core fixtures and checks barriers, ordinary API denial, redacted replay, old snapshot replay, journal watermark, payload integrity, and Recovery-mode gates.
- Journal corruption/missing/truncation and unknown schema cases are expected to fail closed; the runner does not repair those inputs.
- Mode evidence is runtime enforcement for exactly `NORMAL`, `SAFE`, `STATELESS`, and `RECOVERY`, not prompt simulation.

Metrics are counts of explicit test assertions, not a statistical score. A zero duplicate/exposure metric is emitted only when all required evidence tests for that invariant pass. Recovery failure remains an acceptable terminal observation when the correct result is fail-closed or operator diagnosis.

## Observed synthetic matrix result

The final frozen fixture hash is `707952f433fe6081bf3ca823bfd7f46c95785888985e00d8bca349f1823e0541`. All 27 cases passed through 23 unique evidence tests; 0 cases were blockers or inconclusive. The cases requiring a denied action numbered 27 and all 27 evidence mappings passed. Observed counts were: duplicate fake external dispatches 0, duplicate mutations 0, purged-identifier re-exposures 0, unauthorized new mutations after revocation 0, and unexpected NORMAL entries under required recovery mismatch 0. The 3 fail-closed control-plane cases (corrupt chain, missing/truncated journal, and unsupported schema) failed closed as expected. The payload-hash mismatch was detected. No deliberate low-level SQLite page corruption was injected: it would test SQLite file-open failure rather than a supported Nexus repair/check API, and this phase does not add a repair engine.

The effect outcome matrix used only an in-process fake dispatcher; no external effect was sent. Old-snapshot recovery and journal reconciliation used simulated close/reopen boundaries, not OS power loss. `PHYSICAL_DURABILITY` remains `NOT FULLY QUALIFIED`.

## Frozen Host configuration

The Phase 2 Host settings remain unchanged: primary Memory `OFF`; tool-chat memory generation `ON_FORCED / USER_NOT_CONTROLLABLE`; its cross-session effect while primary Memory is off `NOT INDEPENDENTLY VERIFIED`; Custom Instructions and global AGENTS unchanged; `project-experience-curator` unchanged at `DISCOVERED / UNEVALUATED`. There is no model behavioral evaluation, so Host-native Memory is not part of recovery evidence.

## Current scope boundaries

Results establish only transaction/restart/replay semantics exercised by these synthetic tests on the current Windows environment. They do not establish all-platform power-loss durability, real external Effect outcomes, production recovery readiness, or user-data operational qualification. Phase 3 candidates remain candidates pending external review; Academy has not promoted them to production policy.
