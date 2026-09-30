# Governed Daily Task Start

`task start` is the ordinary post-bootstrap operator path for starting one
bounded daily Task on an existing, bound Nexus instance. It creates a new
per-Task root Grant and one Root `ORCHESTRATOR` through the existing Core and
Codex Hosted Bridge APIs. It does not execute the Task.

```powershell
python -m adapters.client `
  --data-root <existing-root> `
  --policy <bound-policy> `
  --independent-purge-journal <bound-journal> `
  task start --plan <task-start-plan.json>
```

The plan is strict UTF-8 JSON. It freezes a `nexus.daily_task_start@1`
request: `command_id`, exact instance expectation, operator and runtime
principal IDs, a Grant ID/time/action/audience scope, and the complete Root
IDs, input, Task Contract, explicit budget, empty DAG, data boundary, and
classification assertions. `task_contract` uses the existing
`nexus.task_contract@1` shape. The requester must be the operator.

The operator principal must already be an ACTIVE HUMAN trust anchor for the
bound policy; the runtime principal must already be an ACTIVE SERVICE. The
instance must be NORMAL with ACTIVE participation. The new root Grant is
issued by that HUMAN to that SERVICE, has no parent, and is scoped to exactly
the planned Task. Root resource requirements are derived from the actual
Root plan; any additional resources must be listed explicitly. Wildcards,
delegation, egress, effects, runtime configuration, protected inspection, and
Skill administration are not available through this command.

On the first request, the CLI requires an interactive TTY and the operator
must type exactly `START <task_id>`. The sanitized confirmation shows IDs,
scope, boundary, and budget; it does not print the input payload or local plan
path. The request is durably bound in the existing CommandLedger before the
Grant or Task Root is created. An exact retry resumes without asking again;
a changed request under the same command identity conflicts. The Grant uses
the plan's finite expiry; there is no automatic renewal or replacement.

`authority-bootstrap` remains a one-time Stage 2 bootstrap operation. It is
not a daily Grant issuance API.

Task start only authorizes and starts the Root. It does not perform the work,
close the Task, revoke the Grant, update Current State, read or compile a
Context Pack, create Claims or Memory, or refresh continuation state.
