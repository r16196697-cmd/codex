# Phase 5 Manual Fallback Runbook

Status: `FALLBACK ARTIFACT`. The default preregistered path is `PHASE5_OFFICIAL_HOST_INVOCATION_V1` through the automated Host behavioral bridge. The formal matrix has not started. Use these manual steps only if the automated path is unavailable and the external review authorizes that fallback.

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
