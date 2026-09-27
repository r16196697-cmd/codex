# Phase 5 External Host Controller Runbook

Status: `FORMAL MATRIX COMPLETE — EXTERNAL REVIEW PENDING`. The first and only 18-trial matrix has completed. **Do not run the command below again, retry any packet, or manually rerun packets.** This runbook preserves controller/procedure provenance only. Capability Certification, Shadow, and Production remain unauthorized. V2's pre-model incident and the V3 internal diagnostic remain preserved and are not eligible trials.

## External controller diagnostic

An operator ran a non-behavioral diagnostic from ordinary Windows PowerShell with Codex CLI `0.158.0-alpha.2.1`, the frozen child argument semantics, a fresh empty temporary cwd outside the repository, and stdin `Return exactly: P5_OUTER_HOST_OK`. It exited 0; `thread.started` and `turn.completed` were observed; output was `P5_OUTER_HOST_OK`; tool-call count was zero. Thread ID: `01a0e1ed-4206-7671-98ba-431aac9ef614`. Host-reported total-turn telemetry: input 20481, cached input 7936, cache-write input 0, output 10, reasoning output 0. It is not a matrix trial and created no Nexus MODEL receipt. Exposure-component attribution and provider dollar cost remain unavailable; model identity remains unavailable.

`NESTED_OUTER_SANDBOX_CONFOUND = SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC`; this does not prove a sole root cause because the parent-process environments differ. `CODEX_AGENT_NESTED_EXECUTION_PATH = BLOCKED_BY_HOST_STATE_ACCESS`; `EXTERNAL_WINDOWS_POWERSHELL_EXECUTION_PATH = SUPPORTED`.

## Recorded formal matrix procedure — already completed; do not repeat

The external operator launched the first and only eligible matrix from ordinary Windows PowerShell outside Codex Agent execution. These are retained execution details, not instructions to execute again:

The controller used ordinary Windows PowerShell, the current local Codex CLI, and the `nexus-academy-bootstrap` worktree after confirming the authorized HEAD and a clean working tree. Any PATH adjustment applied only to that PowerShell process. The command recorded for that completed execution was:

```powershell
python scripts/eval/run_behavioral_phase5.py --execute-host --timeout 180
```

No follow-up execution is permitted. The external PowerShell was only the outer controller. Each child invocation used the frozen `PHASE5_OFFICIAL_HOST_INVOCATION_V1`, `--sandbox read-only`, `--ephemeral`, a fresh empty cwd outside the repository, stdin packet only, and no resume. This removed the Codex Agent outer-sandbox confound; it did not remove the child Host read-only sandbox. Historical fail-closed stop conditions remain part of the recorded protocol.

## Closed execution boundary

There is no manual fallback for this completed matrix. Do not copy or submit any packet again, start another Host chat, resume a thread, retry an ID, or invoke the runner. Keep the original 18 trial records and their raw private captures under the existing retention controls; all future work is deterministic analysis, documentation, tests, and external review only.
