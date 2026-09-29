# Fresh Nexus Instance Bootstrap

The explicit bootstrap implementation is available through `python -m adapters.bootstrap`. Its presence does not mean a production bootstrap has run. No Project Nexus production data root has been created.

## Stage 1: initialize an unauthorized instance

Prepare a local, schema-valid policy file that explicitly lists exactly one intended operator HUMAN principal in `trust_anchors`. Keep `policies/default-policy.json` unchanged; its empty anchor list is deliberately fail-closed. Choose a new repo-external data root and a separate independent Purge Journal location. These paths are local inputs and are not written into Nexus records or command output.

```text
python -m adapters.bootstrap initialize-instance --data-root <local-root> --policy <local-policy> --independent-purge-journal <local-journal> --command-id <stable-command-id>
```

The command creates the schema, sequence-zero Purge Journal watermark, and immutable policy/journal binding. Its success state is `INITIALIZED_UNAUTHORIZED`. It creates no ordinary Principal, Trust Anchor, Grant, Task, Run, Memory, or Evidence.

An exact retry must use the same command ID, policy, and journal. Changed inputs conflict. Partial roots are never deleted or overwritten automatically. Normal `adapters.client` and Panel startup remain existing-root-only and do not initialize, adopt, or bootstrap authority.

## Optional explicit legacy adoption

For a pre-binding legacy database only, inspect and verify the proposed policy and journal first, then run:

```text
python -m adapters.bootstrap adopt-policy-binding --data-root <legacy-root> --policy <reviewed-local-policy> --independent-purge-journal <existing-local-journal> --command-id <stable-command-id>
```

This requires an interactive TTY confirmation. Adoption verifies migration/checksum, database, journal, purge, object, and authority compatibility before binding. It declares that the supplied full policy applies from adoption time forward; it does not assert that the exact policy document governed historical operation.

## Stage 2: bootstrap narrow operator authority

Create a local plan JSON with exactly these fields:

```text
operator_principal_id
runtime_principal_id
anchor_id
grant_id
task_id
resource_scope
action_scope
audience_scope
issued_at
expires_at
command_id_prefix
```

The HUMAN operator ID must already be listed in the bound policy. Use one exact reserved Task ID, a finite list of safe logical resource IDs, allowlisted non-effect actions, and an explicit finite expiry. Wildcards, local paths, recovery identity, delegation, egress, Effect authority, and unrelated runtime authority are rejected.

```text
python -m adapters.bootstrap authority-bootstrap --data-root <bound-root> --policy <same-local-policy> --independent-purge-journal <same-local-journal> --plan <local-plan.json>
```

The command prints a normalized request summary and requires the operator to type the requested confirmation phrase. A pipe or non-interactive invocation is denied. This records an explicit local operator act; it does not authenticate a real-world identity cryptographically. It registers HUMAN and SERVICE principals, the HUMAN Trust Anchor, and the narrow Grant through `AuthorityService`, in that order. It does not create the first Task or Run.

No `--yes`, `--force`, repair shortcut, policy rotation, or automatic re-issuance path exists. Exact retries reuse the durable intent and stable command IDs; conflicting inputs stop for review.
