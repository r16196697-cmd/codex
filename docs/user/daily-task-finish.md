# Governed Daily Task Finish

`task finish` closes one existing daily Root Task on an already bound instance.
It uses only the Task's original Grant and records the terminal transition
through `TraceRuntime` before revoking that Grant.

```powershell
python -m adapters.client `
  --data-root <existing-root> `
  --policy <bound-policy> `
  --independent-purge-journal <bound-journal> `
  task finish --plan <task-finish-plan.json>
```

The strict UTF-8 plan uses protocol `nexus.daily_task_finish@1` and freezes the
instance expectation, HUMAN operator, exact Task/Root Run/Grant IDs, outcome
(`SUCCEEDED`, `FAILED`, or `CANCELLED`), and event classification assertions.
The operator must be the active HUMAN trust anchor that issued the Task's
original root Grant. That Grant must already authorize `RUN_TRANSITION` and
`CLASSIFY` for the Root Run and the exact finish event refs. Finish never
expands authority or creates a replacement Grant.

On the first finish request, an interactive TTY is required and the HUMAN
operator must type exactly `FINISH <task_id> <OUTCOME>`. The confirmation shows
only lifecycle metadata and closure counts. An exact retry after durable
request binding does not ask again; a changed request under the same command ID
conflicts. A crash before request binding requires confirmation again.

`SUCCEEDED` follows `RUNNING → VERIFYING → SUCCEEDED`. `FAILED` and
`CANCELLED` transition directly from `RUNNING`. The Task status follows the
existing Core transition semantics. Finish refuses active child Runs and
unresolved Effects; it does not cancel child work or reconcile Effects. The
terminal transition commits before the exact original Grant is revoked. If
terminal completion was committed but the process stopped before revocation,
an exact retry only completes that revocation. A completed exact retry remains
readable after the Grant expires or is revoked.

This command does not perform the Task, create Tasks/Runs/Grants, renew
authority, update Current State, compile a Context Pack, or create Claims or
Memory. `authority-bootstrap` remains the one-time Stage 2 bootstrap operation,
not a daily Grant issuance path.
