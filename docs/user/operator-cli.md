# Nexus operator surface (Codex-hosted v0.1)

This is a local, thin operator client over Nexus Runtime APIs. It does not query or mutate SQLite itself, and it refuses to create a database implicitly. It is available after Nexus has been initialized at a reviewed data root:

```powershell
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> mode show
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> mode set SAFE --command-id <unique-id> --grant-id <grant-id> --task-id <task-id> --classification-assertion-ref <trace-event-classification>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect task <task-id> --grant-id <grant-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect effect <effect-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect approval <approval-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect object <object-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect route <route-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect purge <plan-id> --grant-id <grant-id> --task-id <task-id>
```

The grant must include the exact task, resource, action `INSPECT`, and audience `nexus-inspect`. Approval payload hashes and object integrity hashes require separate `INSPECT_PROTECTED` authorization and explicit `--show-payload-hash` or `--show-integrity-hash`. Object payload bytes are not exposed by this CLI.

`UNKNOWN` is rendered as an unresolved fact requiring authoritative reconciliation, never as a failed/retryable commit. `PARTIAL` purge is shown as incomplete and its barrier remains visible. A mode change is authorized for both `RUNTIME_CONFIGURE` and `TRACE_APPEND`, and requires an existing Task Root `ORCHESTRATOR` Run using the same Grant. The supplied immutable `TRACE_EVENT` classification assertion must identify `evt-<command-id>` and fit the Root Run data boundary. The mode audit row, CommandLedger result, and `nexus.runtime.mode_changed` TraceEvent commit atomically; mode changes without valid trace context are denied.

During recovery, ordinary Runtime reads/writes and inspect are isolated. The only mode exit is:

```powershell
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> recovery complete --command-id <unique-id> --purge-ledger <independent-ledger-path>
```

The ledger path must be outside the data root. Recovery opens NORMAL only after the migration checks, independent Purge Ledger replay, available-object SHA-256 verification, text-index rebuild, and deleted-object non-resurrection checks pass. Unresolved barriers or integrity failures leave the instance in RECOVERY.

Independent Model/Search Provider API calls and real secret storage are not exposed here; they remain `DEFERRED / NOT_CONFIGURED` for the Codex-hosted deployment.
