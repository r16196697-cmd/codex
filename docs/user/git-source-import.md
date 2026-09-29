# Governed local Git source import

The `import-git-source` operator command imports one explicitly named file from
an exact commit in an existing local Git worktree. It is a local immutable
source acquisition boundary; it is not Git sync, a crawler, or knowledge
admission.

The importer reads the commit, tree entry, and blob from Git's local object
database. It does not read working-tree bytes, fetch missing objects, access a
remote, switch branches, or modify the repository. A missing local object fails
closed. The repository path is process-local and is never saved in Nexus or
printed by the command.

## Operator command

Use an already initialized, policy-bound Nexus data root and an existing Task,
non-terminal Run, and Run-bound Grant. The CLI does not initialize, adopt, or
bootstrap an instance or create Task/Run authority.

```text
python -m adapters.client --data-root <existing-data-root> --policy <local-policy.json> import-git-source --repo <local-git-worktree> --plan <local-import-plan.json>
```

Pass `--independent-purge-journal <configured-journal>` before the command when
the instance uses an explicit journal path. The plan fixes the logical
`repository_id`, full commit OID, POSIX relative path, Task/Run/Grant, caller
chosen Artifact and Evidence IDs, classification assertion IDs, classification,
and command ID prefix. It must not contain the repository's absolute path.

The JSON plan has this exact shape; replace the all-zero OID with the exact full
commit OID from the local repository and pre-authorize the listed resources in
the Run-bound Grant:

```json
{
  "repository_id": "project-nexus-repo",
  "commit_oid": "0000000000000000000000000000000000000000",
  "path": "docs/design/accepted-decision.md",
  "task_id": "bootstrap-task",
  "run_id": "bootstrap-run",
  "grant_id": "bootstrap-grant",
  "artifact_object_id": "source-artifact-accepted-decision",
  "artifact_classification_assertion_id": "class-source-artifact-accepted-decision",
  "evidence_object_id": "source-evidence-accepted-decision",
  "evidence_classification_assertion_id": "class-source-evidence-accepted-decision",
  "sensitivity_level": "PUBLIC",
  "handling_tags": [],
  "command_id_prefix": "import-accepted-decision"
}
```

The importer stores the exact Git blob bytes as an Artifact. It creates a
canonical provenance Evidence envelope whose `artifact_sha256` comes from the
persisted Artifact metadata, and links Evidence `derived_from` Artifact. Both
objects are classified and written through the existing AuthorityService and
ObjectStore APIs under the target Run's exact Grant and data boundary.

Before the Artifact/Evidence multi-stage mutation, the complete import request
is deterministically committed through the existing CommandLedger. After a
partial Artifact success, the same command prefix can only exact-resume and
cannot be retargeted to another Git source.

Importing source does not establish that it is current, correct, or verified.
Imported source is not verified truth. Further work still follows the governed
path:

```text
Evidence → Verification → Memory Candidate → admission
```

The source importer does not admit Memory, compile Context, register Skills, or
create Task/Run records.
