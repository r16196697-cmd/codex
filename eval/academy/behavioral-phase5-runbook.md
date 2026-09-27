# Phase 5 External Host Controller Runbook

Status: `EXTERNAL WINDOWS POWERSHELL CONTROLLER AUTHORIZED — EXTERNAL REVIEW PENDING`. Run the frozen matrix only from an ordinary Windows PowerShell session, never from a Codex Agent/integrated shell. The authorization covers the frozen matrix only; it does not authorize Capability Certification, Shadow, or Production. V2's pre-model incident and the V3 internal diagnostic remain preserved and neither is an eligible behavioral trial. Manual packet execution remains a fallback artifact, not the default path.

## External controller diagnostic

An operator ran a non-behavioral diagnostic from ordinary Windows PowerShell with Codex CLI `0.158.0-alpha.2.1`, the frozen child argument semantics, a fresh empty temporary cwd outside the repository, and stdin `Return exactly: P5_OUTER_HOST_OK`. It exited 0; `thread.started` and `turn.completed` were observed; output was `P5_OUTER_HOST_OK`; tool-call count was zero. Thread ID: `01a0e1ed-4206-7671-98ba-431aac9ef614`. Host-reported total-turn telemetry: input 20481, cached input 7936, cache-write input 0, output 10, reasoning output 0. It is not a matrix trial and created no Nexus MODEL receipt. Exposure-component attribution and provider dollar cost remain unavailable; model identity remains unavailable.

`NESTED_OUTER_SANDBOX_CONFOUND = SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC`; this does not prove a sole root cause because the parent-process environments differ. `CODEX_AGENT_NESTED_EXECUTION_PATH = BLOCKED_BY_HOST_STATE_ACCESS`; `EXTERNAL_WINDOWS_POWERSHELL_EXECUTION_PATH = SUPPORTED`.

## Authorized formal matrix procedure

The first eligible formal matrix execution attempt must be launched from ordinary Windows PowerShell outside Codex Agent execution:

1. Open ordinary Windows PowerShell, not a Codex integrated/Agent shell.
2. Use the current local `codex.exe`. If needed, add its containing directory to `PATH` for the current PowerShell process only (`$env:PATH`); do not persistently modify the user's PATH and do not commit the local executable path.
3. Change directory to the `nexus-academy-bootstrap` worktree.
4. Confirm `git rev-parse HEAD` equals the exact authorization commit SHA provided with the external review authorization, and confirm `git status --short` is empty.
5. Run the committed harness exactly once:

```powershell
python scripts/eval/run_behavioral_phase5.py --execute-host --timeout 180
```

Do not manually invoke `codex exec`, resume a thread, retry a failed trial, or rerun the matrix. The external PowerShell is only the outer controller. Each child invocation still uses the frozen `PHASE5_OFFICIAL_HOST_INVOCATION_V1`, `--sandbox read-only`, `--ephemeral`, a fresh empty cwd outside the repository, stdin packet only, and no resume. This removes the Codex Agent outer-sandbox confound; it does not remove the child Host read-only sandbox. The runner's fail-closed stop conditions remain in force.

## Safety and freeze

- Use the current Codex Host with the existing configuration. Do not change Memory, Custom Instructions, AGENTS, Skills, plugins, MCP, or permissions.
- Use synthetic packets only. Do not add private or project information.
- Do not use the same conversation for multiple trials. Each packet is the first message in a brand-new Codex conversation.
- Do not show the model the evaluator file or discuss conditions, expected answers, or scoring.
- Do not approve or perform tool/file/network actions. These packets require only a text response.
- The returned response must be preserved verbatim with its trial ID. No Nexus MODEL Run/receipt is created by this manual collection step.

## Trial procedure

1. Start a new Codex chat; do not resume or fork an existing thread.
2. Copy exactly one `packet_text` from `fixtures/behavioral-phase5-packets.json` and paste it as the first user message.
3. Record the trial ID from the first line and the assistant's final response verbatim. Do not ask a follow-up in that chat.
4. Repeat in a separate new chat for every packet ID below.
5. Return the captured mapping in this form; do not evaluate it yourself:

```json
{
  "A17": "<verbatim final response>",
  "E04": "<verbatim final response>"
}
```

If the UI exposes a reliable tool-call indicator, note the count separately. If it does not, report `UNAVAILABLE`; do not infer zero from the answer text. Provider, served model identity, request ID, and cost remain `UNAVAILABLE` unless directly shown by the Host.

## Copy a packet in PowerShell

Run from the Academy worktree. Replace the ID with one from the list. This decodes the JSON string exactly and places only that one prompt on the clipboard.

```powershell
$packets = Get-Content eval/academy/fixtures/behavioral-phase5-packets.json -Raw | ConvertFrom-Json
$packets.context_packets | Where-Object trial_id -eq 'A17' | ForEach-Object packet_text | Set-Clipboard
```

Then paste into a new Codex chat. Do not use a single conversation to run multiple IDs.

## Blind trial IDs

There are 18 separate packets. The condition mapping and expected outputs are deliberately kept in `fixtures/behavioral-phase5-evaluator.json`; avoid opening it until every response has been captured. This manual procedure is not the current default execution path.

```text
A17  E04  F22  H11
B29  D31  G08  J15
K02  L73  M26  N58
V06  R41  S12
T63  U07  W34
```

The first 12 are context packets. The final six are the synthetic capability-presence and unrelated-task probes. P3 invocation-only is intentionally absent because no supported observation proves that instructions were absent initially and then actually became model-visible on invocation.

After outputs are returned, deterministic scoring may be performed against the separate frozen evaluator. Until then, C0/C1/C2 and P0/P1/P2 real execution counts remain zero, exact Host exposure remains unobserved, no real Skill has been evaluated, and no Capability Certification / Shadow status may advance.
