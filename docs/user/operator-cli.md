# Nexus operator surface (Host-neutral v0.1)

This is a local, thin operator client over Nexus Runtime APIs. It does not query or mutate SQLite itself, and it refuses to create a database implicitly. It is available after Nexus has been initialized at a reviewed data root:

An existing repository can be attached to an already initialized instance with the HUMAN-confirmed `project attach` command. Once attached on this host, commands may resolve the instance from the current directory without explicit path arguments. See [Project Locator and Attach](project-locator-attach.md). Explicit `--data-root` workflows below remain supported.

For read-only Project Presence, use `nexus status`, `nexus continue`, and `nexus doctor`. The optional `nexus host install codex` registration invokes the installed Nexus CLI at Codex SessionStart; it does not install repository scripts. See [Native Presence and Agent Host Integration](native-presence-host-integration.md).

项目 milestone 的治理写回可使用 `nexus checkpoint`：完整只读 preflight 后，一次 HUMAN 确认组合既有 Task Start / Continuation / Task Finish。内部计划自动冻结，原命令可安全重试。参见 [Checkpoint / Milestone](checkpoint.md)。

```powershell
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> --policy <nexus-policy.json> mode show
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> --policy <nexus-policy.json> mode set SAFE --command-id <unique-id> --grant-id <grant-id> --task-id <task-id> --classification-assertion-ref <trace-event-classification>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect task <task-id> --grant-id <grant-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect effect <effect-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect approval <approval-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect object <object-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect route <route-id> --grant-id <grant-id> --task-id <task-id>
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> inspect purge <plan-id> --grant-id <grant-id> --task-id <task-id>
```

The grant must include the exact task, resource, action `INSPECT`, and audience `nexus-inspect`. Approval payload hashes and object integrity hashes require separate `INSPECT_PROTECTED` authorization and explicit `--show-payload-hash` or `--show-integrity-hash`. Object payload bytes are not exposed by this CLI.

For observation-only cold-start inspection, the native Panel supports an explicit read-only open:

```powershell
.\.venv\Scripts\python.exe -m adapters.panel --data-root <existing-data-root> --policy <nexus-policy.json> --independent-purge-journal <journal-path> --read-only
```

Read-only Panel access is limited to an existing bound instance with a current, checkpointed SQLite database and matching independent Purge Journal. It validates binding, schema, database integrity, and journal freshness without running migrations, creating directories or lock sidecars, bootstrapping recovery, or cleaning orphan payloads. It holds the existing Nexus writer-lock boundary for the session; an active writer causes a prompt fail-closed error. Mutation APIs are denied by the store. A non-empty SQLite WAL is unsupported for this read-only path and is rejected rather than ignored. Use this mode for observation and cold-start reconstruction probes, not repair or recovery.

`--policy` is optional and accepts an existing, secret-free `nexus.policy@1` JSON policy. It is loaded read-only and validated by the Core policy schema; it is never written back. Omit it only when the instance intentionally uses the repository's default fail-closed policy. The CLI does not create or initialize the data root.

`UNKNOWN` is rendered as an unresolved fact requiring authoritative reconciliation, never as a failed/retryable commit. `PARTIAL` purge is shown as incomplete and its barrier remains visible. A mode change is authorized for both `RUNTIME_CONFIGURE` and `TRACE_APPEND`, and requires an existing Task Root `ORCHESTRATOR` Run using the same Grant. The supplied immutable `TRACE_EVENT` classification assertion must identify `evt-<command-id>` and fit the Root Run data boundary. The mode audit row, CommandLedger result, and `nexus.runtime.mode_changed` TraceEvent commit atomically; mode changes without valid trace context are denied.

Ordinary production startup performs a freshness handshake between the configured independent Purge Journal and the database's acknowledged journal watermark before migrations, orphan cleanup, or Core APIs. A stale snapshot, missing/mismatched journal, rollback, or corrupt chain is automatically held in RECOVERY. Configure the same journal for every ordinary startup with `--independent-purge-journal <path>` (or `NEXUS_INDEPENDENT_PURGE_JOURNAL`); absent either, the default is a sibling of the data root. Do not copy or substitute a different journal for a deployment.

`recovery open` remains an explicit operator entry point for known restores and damaged roots. It forces RECOVERY before migrations, journal replay, or Core APIs are made available:

```powershell
.\.venv\Scripts\python.exe -m adapters.client --data-root <restored-data-root> --policy <nexus-policy.json> recovery open --purge-ledger <independent-ledger-path>
```

The ledger path must be outside the data root. If recovery-open or journal replay fails, leave the root isolated and investigate; do not invoke ordinary Runtime APIs. `recovery open` is an explicit operator action, but ordinary startup also automatically detects stale restored snapshots when configured with the independent journal.

During recovery, ordinary Runtime reads/writes and inspect are isolated. The only mode exit is:

```powershell
.\.venv\Scripts\python.exe -m adapters.client --data-root <existing-data-root> recovery complete --command-id <unique-id> --purge-ledger <independent-ledger-path>
```

The ledger path must be outside the data root. Recovery opens NORMAL only after the migration checks, independent Purge Ledger replay, available-object SHA-256 verification, text-index rebuild, and deleted-object non-resurrection checks pass. Unresolved barriers or integrity failures leave the instance in RECOVERY.

Independent Model/Search Provider API calls and real secret storage are not exposed here; they remain `DEFERRED / NOT_CONFIGURED` for the Codex-hosted deployment.
