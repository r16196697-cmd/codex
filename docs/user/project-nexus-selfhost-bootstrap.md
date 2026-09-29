# Project Nexus Self-Hosting Stage 3

`project-nexus-selfhost` is a Project Nexus-specific application composition over existing Nexus Core services. It is not a generic workflow framework.

The command requires all of the following before it can run:

- an existing Stage 1 initialized and policy-bound instance;
- completed Stage 2 authority bootstrap with the exact scoped HUMAN-issued Work Grant and control Grant expected by the plan;
- an operator-reviewed, frozen `project-nexus-selfhost-bootstrap-v2` manifest;
- a local Git repository whose current HEAD matches the accepted execution commit and whose required Git objects are already available locally.

It mutates an **existing bound root**. It does not initialize a database, adopt a policy, create Stage 2 authority, fetch Git data, or create a second runtime. The local repository, policy, data-root, journal, and plan paths are process-local arguments and are not persisted in the Stage 3 semantic request or returned output.

Example command shape:

```text
python -m adapters.client --data-root <existing-bound-root> --policy <bound-local-policy> --independent-purge-journal <bound-local-journal> project-nexus-selfhost --repo <frozen-local-repository> --plan <reviewed-stage3-manifest.json>
```

The manifest freezes all logical identities, timestamps, source commits/paths/blob IDs, classifications, object refs, verifications, candidates, Context Pack refs, and command IDs. The application validates the exact local Git state, commits a master request binding before its first business mutation, and resumes only the same semantic request. Changed content or plan under the same command identity fails closed.

The normal path uses existing Authority, Hosted Bridge, Git source import, Verification, Memory, and Context Pack services. It imports eight sources, writes current-state and Claim objects, creates T1 VerificationResults and quarantined `INFERRED` Memory Candidates, compiles an explicit-ref Context Pack with no Memory query, then closes the Root Run and revokes the Work Grant. It does not admit Memory or assert that the model saw the Context Pack.

`STAGE3_CANONICAL_COMPLETE` means that this bounded canonical operation completed. It is not `BOOTSTRAP_COMPLETE`: that later operational claim also requires a coherent backup and a successful disposable restore proof.

The implementation exists; this implementation slice does **not** execute the Project Nexus production bootstrap. Do not run the example against a production root without separate authorization and independent review. No Host behavioral trial, Utility trial, or Academy run is part of this command.
