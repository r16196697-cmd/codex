# Project Locator and Attach

Nexus keeps portable project identity separate from a host's instance location:

- The repository manifest `.nexus/project.json` contains only `project_id`.
- The host-local registry maps that ID to an existing instance binding and its local paths.
- Every locate verifies the registered instance through the existing read-only Panel composition.

The manifest has this exact shape and is safe to commit:

```json
{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"project-example"}
```

It contains no instance ID, machine identity, or host path. The registry is not Nexus canonical state and must not be committed. Set `NEXUS_PROJECT_REGISTRY` to choose its path. Otherwise Windows uses `%LOCALAPPDATA%\Nexus\projects-v1.json`; POSIX uses `$XDG_STATE_HOME/nexus/projects-v1.json` or `~/.local/state/nexus/projects-v1.json`. Locate does not create a missing registry or parent directory.

Attach an already initialized instance from a repository:

```powershell
python -m adapters.client --data-root <existing-data-root> --policy <policy.json> --independent-purge-journal <journal.jsonl> project attach --project-root <project-root> --project-id <project-id>
```

The data root and journal must already exist. Attach verifies the bound instance in read-only mode before asking the operator to type `ATTACH <project-id>`. It never initializes an instance. An exact repeat does not prompt or rewrite either file. A conflicting manifest, host binding, path, or instance identity fails closed; there is no force-rebind option.

Locate the nearest manifest from the current directory, or from an explicit starting directory:

```powershell
python -m adapters.client project locate
python -m adapters.client project locate --project-root <starting-directory>
```

Manifest discovery walks only the starting directory's ancestors. The nearest manifest wins. Nexus does not search sibling folders, drives, or the machine for a database. A manifest without a host registry entry is reported as unattached on this host.

From a directory inside an attached project, ordinary CLI commands may omit `--data-root`, `--policy`, and `--independent-purge-journal`; the registry supplies the complete binding and Nexus verifies it on every invocation. These paths are host-local and may change when an instance is deliberately reattached through a separately reviewed workflow. Existing commands with explicit `--data-root` retain their manual/operator behavior and do not require a Project Manifest. Supplying policy or journal without a data root is rejected to prevent mixing attachments.

The registry is keyed by `project_id`, not repository path, so clones and worktrees with the same manifest resolve to the same host attachment. Project identity is portable; instance location is replaceable host configuration; the opened instance's durable binding remains authoritative. This feature does not expose arbitrary Object payloads or grant execution authority.
