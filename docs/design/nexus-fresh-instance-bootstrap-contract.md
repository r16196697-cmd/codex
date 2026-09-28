# Nexus Fresh Instance Bootstrap Contract

**Status:** `PROPOSED / EXTERNAL REVIEW REQUIRED / NO IMPLEMENTATION AUTHORIZED`

**Scope:** architecture design only. No data root, Principal, Trust Anchor, Grant, Task, or Run was created.

## 1. Verified Current Bootstrap Gap

The fresh-instance path has a real bootstrap paradox. `ObjectStore(<new-root>)` can create the data directory, SQLite schema, apply migrations, and establish the Purge Journal watermark. The ordinary `adapters.client` entry point explicitly requires an existing `nexus.sqlite`; it does not initialize one. The existing Panel also refuses to initialize a missing database.

Migrations create Authority tables but no ordinary operator Principal, Trust Anchor, or Grant. The only Principal inserted by migrations is the reserved `nexus-core-recovery` `SERVICE`; it is an infrastructure identity, not an operator. `policies/default-policy.json` has `trust_anchors: []`. `CodexHostedBridge.create_task_root()` requires an already-valid Grant and does not create authority implicitly.

There is no official production helper today that completes these steps. The Authority APIs are reusable: `register_principal()`, `register_trust_anchor()`, and `create_grant()` apply the existing schemas, policy checks, and command-ledger semantics. `CodexHostedBridge.create_task_root()` is the existing governed first Task/Root Run path once a suitable Grant exists.

Tests build authority explicitly: they set a test policy's trust-anchor list, register HUMAN/SERVICE principals, register the configured anchor, create a scoped Grant, and then call the relevant Core/Bridge API. These are examples of API use, not evidence that test identities or test policies are valid production bootstrap defaults.

## 2. Fresh Instance State

After current migrations complete on an empty root, the persisted baseline includes the schema/migration history, the `raw-sha256` hash profile, `runtime_mode=NORMAL`, `participation_mode=ACTIVE`, the reserved recovery SERVICE Principal, and an empty independent Purge Journal acknowledged by a sequence-zero database watermark. It does not include an ordinary operator Principal, Trust Anchor, Grant, Approval, Task, Run, Memory, or user Object.

The policy is supplied to the store from configuration; it is not a normal user Authority record. A policy with no configured anchors therefore leaves the initialized instance without an ordinary root of Authority.

## 3. Bootstrap Paradox

Grant creation requires an active issuer, and a root Grant issuer must be an active, persisted Trust Anchor whose exact principal ID is allowed by the loaded policy. A Trust Anchor requires an active Principal and the current policy version. A Task requires an active requester; the Root Run additionally requires a valid Grant with `RUN_CREATE` authority and valid Run/event classification assertions. Thus database initialization alone cannot produce the first governed Task/Run.

The resolution is a reviewed, explicit sequence: policy is established first; an operator explicitly requests instance initialization; then a local operator action registers the exact policy-listed operator identity and Trust Anchor and issues a narrow, expiring bootstrap Grant through AuthorityService. Only then may the ordinary Bridge create the first Task/Root Run.

## 4. Security Invariants

- **No implicit admin:** creating a database never creates operator authority.
- **Policy first:** the initializer accepts and validates an operator-supplied local policy. Bootstrap cannot add a principal ID to `trust_anchors` or rewrite policy.
- **Explicit local operator act:** Stage 2 requires an interactive local confirmation displaying the exact principal, policy binding, task, resources, actions, audience, and expiry. No environment variable or background startup path grants authority. AuthorityService validates the existing policy/Authority rules but does not prove TTY presence; the production bootstrap application must own that boundary and ordinary CLI/Panel startup must not expose a silent grant-creation route.
- **No identity overclaim:** local TTY confirmation is an explicit local operator act, not cryptographic proof of a real-world identity.
- **No scope expansion:** the first Grant is limited to one planned Task and explicit resources/actions/audiences. No wildcard, all-action, Effect, external-egress, or general delegation authority is included by default.
- **No recovery identity substitution:** `nexus-core-recovery` is never an operator, Trust Anchor, issuer, or grantee.
- **Fail closed:** incompatible roots, changed policy, partial identity state, command conflict, or ambiguous recovery state blocks normal bootstrap progress; there is no `--force`, overwrite, or repair shortcut.

## 5. Stage 1 — INSTANCE_INITIALIZE

Stage 1 is infrastructure initialization only:

1. Require an explicit target root, an explicit local policy file, and an explicitly selected independent Purge Journal location. Keep all local paths in process/configuration only.
2. Validate policy schema and canonicalize its JSON semantics. Require the policy to explicitly list the one intended initial operator anchor; preserve the repository default policy with an empty anchor list.
3. Validate root and journal safety before constructing `ObjectStore` (see §13).
4. Invoke the normal `ObjectStore` migration/journal initialization path. Do not create Task/Run/Memory or Authority rows beyond the reserved recovery identity supplied by the existing migration.
5. Report `INITIALIZED_UNAUTHORIZED` until Stage 2 has completed. The ordinary client may open the initialized store, but no user work is authorized without a Grant.

**Policy-binding gap:** current TrustAnchor and Grant records persist only `policy_version`; the current code has no instance-level binding to the full policy-document digest. Stage 1 must not claim that version equality proves policy-content identity. The proposed minimal implementation is a schema/migration-backed singleton instance-binding record containing only an instance ID, policy version, canonical policy SHA-256, independent-journal identity digest, and initialization timestamp. It stores no policy text or local path. Normal product opens must compare the supplied policy's version and digest to this binding and fail closed on mismatch; policy changes need a separately reviewed rotation path. This metadata is an initialization protocol detail, not a new Foundation Contract.

## 6. Stage 2 — AUTHORITY_BOOTSTRAP

Stage 2 is a distinct, explicitly invoked operator action after Stage 1. It composes existing AuthorityService operations; it does not insert Authority rows outside Core.

Recommended sequence:

1. Revalidate the instance-binding record and exact policy digest/version.
2. Require an interactive TTY confirmation over a displayed, immutable bootstrap request.
3. Register the exact operator Principal as `HUMAN`, `ACTIVE`.
4. Register an explicitly named `SERVICE`, `ACTIVE` local bootstrap/runtime Principal for the Bridge to act as. It is not an administrator; it receives only the following task-bound Grant. If review prefers a human as the direct Run actor, that alternative must be explicit and must not silently change actor semantics.
5. Register a Trust Anchor for the HUMAN Principal only if that exact ID is already listed in the loaded policy and `policy_ref` equals its policy version.
6. Create the initial ACTIVE root Grant last, issued by that anchor Principal to the scoped SERVICE Principal, with exact pre-frozen IDs and required expiry.

The Authority APIs already enforce active Principal checks, exact policy anchor membership/version, root issuer trust, grant scope shape, required `issued_at`/`expires_at`, and command-ledger replay. The bootstrap caller must use distinct stable command IDs for each operation and must not translate conflicts into upserts.

Stage 2 is not one database transaction today: each AuthorityService operation commits its own command. Do not claim atomicity. A partial prefix remains detectable and cannot be mistaken for completion. Retrying uses the same command IDs and exact request bodies; an identity/scope mismatch is a conflict requiring operator review. Grant creation is last, so a failed prefix has no persisted task-scoped work Grant. If the Trust Anchor command has committed, that exact policy-backed anchor is nevertheless a root-of-authority by existing semantics; the instance is partial, ordinary project work must not start, and no Grant is auto-created. Resume requires an explicit operator confirmation of the same frozen plan.

## 7. Policy / Trust Anchor Model

Recommend option **A/B**: the operator supplies a local, secret-free, schema-valid production policy; Stage 1 validates and binds its version and canonical SHA-256; Stage 2 may register only the exact HUMAN principal already named in that policy. The repo default stays `trust_anchors: []` and is not changed to name the local user.

The policy file path is never stored or shown in Nexus projections. The canonical digest is safe metadata. The current TrustAnchor `policy_ref` and Grant `policy_version` remain as implemented; the new instance binding is the additional content-integrity check required because those existing fields do not persist the document hash.

## 8. Initial Principal

The operator Principal is `HUMAN` because that is the existing type for an approving/operator identity. Its ID is chosen explicitly by the operator and must be an exact policy allowlist member; it is not inferred from the OS account or environment. The schema represents a principal ID and type/status only. There is no identity provider or credential proof in this local v0.1 flow.

`credential_ref` is optional on Grant, not on Principal. Leave it absent/null unless a separately reviewed local credential system provides a real reference. Do not mint a fake credential reference. The persisted meaning is “this local operator action declared/used this Nexus HUMAN principal,” not “Nexus authenticated a legal person.” The scoped runtime SERVICE identity is separately and explicitly named.

## 9. Initial Grant Scope

The Grant must be derived from a frozen one-Task bootstrap request before Stage 2. The Task ID can be reserved in advance; that reservation does not assert that a Task already exists. The Run ID, input/contract/manifest IDs, classification subjects, trace event IDs, source Object IDs, evidence/claim/verification IDs, candidate IDs, and Context Pack ID must also be enumerated before signing the Grant if those operations are included.

For the existing `create_task_root()` path, the current Core checks support this minimum action set for creation and startup:

| Work | Current gate / scope to predeclare |
|---|---|
| Create Task | active `requester_id`; no Grant action check in `TraceRuntime.create_task()` |
| Create Root Run and DAG | `RUN_CREATE` on the exact Root Run ID |
| Root Run transitions | `RUN_TRANSITION` on that Root Run ID |
| User input Object | `OBJECT_WRITE` on the exact input Object ID |
| Task contract / Root Manifest | `OBJECT_WRITE` on the exact Root Run ID |
| Input Trace event | `TRACE_APPEND` on the exact Root Run ID |
| Run/Object/Trace classification assertions | `CLASSIFY` on each exact assertion subject reference |
| Budget account | explicit task-bound limits in `BudgetService`; this creation path has no Authority action check |

If the same Root Run will also perform the initial governed source/Evidence/Memory/Context work, add only the actions used by those current APIs and only the exact resources they will touch: `OBJECT_WRITE` for predeclared Object/Context Pack refs; `INSPECT` on exact `object:<id>` refs with `nexus-inspect` audience where Context source metadata is read; `VERIFY` on exact verification target/evidence refs; `MEMORY_ADMIT` on exact candidate/claim/evidence refs; and `MEMORY_SEARCH` on the Run ID only if admitted Memory search is actually part of the bootstrap. Classifications still require `CLASSIFY` on their exact subject refs. Do not grant `MEMORY_RETAIN` for raw history by default.

`task_scope` contains exactly the bootstrap Task ID. `resource_scope` contains only the exact preplanned IDs/qualified inspect refs. `action_scope` contains only the actions required by the frozen operation plan. `audience_scope` contains `nexus-runtime` and, only if metadata inspection is used, `nexus-inspect`. The current Grant representation checks these as independent sets, so the action/resource allowance is their Cartesian product rather than per-action tuples; keep the resource set very small and task-specific, disclose this residual, and fail closed if the resulting combination is broader than the operator-approved plan.

Require `status=ACTIVE`, explicit `issued_at`, and a finite operator-chosen `expires_at`; no absent/permanent expiry is acceptable. The first Grant must not include `DELEGATE`, `EGRESS`, `EFFECT_*`, Skill administration, or unrelated runtime configuration. If a future task truly requires child Run delegation, that is a separately scoped and reviewed request, not an implicit addition to this Grant.

## 10. Consistency / Replay / Partial Failure

Stage 1 can be recognized from a complete current migration sequence, a valid instance-binding row, an intact fresh/current Purge Journal watermark, and zero ordinary Principal/Trust Anchor/Grant/Task/Run rows. The reserved recovery Principal is expected. No completion marker is needed for Stage 2: completion is derived from the exact active operator Principal, policy-backed anchor, exact active unexpired root Grant and its CommandLedger results.

Derived states should be reported distinctly:

- `UNINITIALIZED`: no Nexus database and no history-bearing journal.
- `INITIALIZED_UNAUTHORIZED`: migrations/binding/watermark valid; no ordinary authority.
- `AUTHORITY_BOOTSTRAP_PARTIAL`: only an exact prefix of the planned Principal/anchor/grant commands is committed; ordinary bootstrap work remains denied.
- `AUTHORITY_BOOTSTRAP_COMPLETE`: all exact records and command results match the approved plan. Current usability is checked separately; an expired/revoked Grant does not trigger automatic re-issuance.
- `CONFLICT_OR_RECOVERY_REQUIRED`: unexpected rows, mismatched replay, altered policy, journal inconsistency, or incompatible data.

On partial Stage 2, only an explicit operator rerun with the same IDs, same request, same policy digest, and same command IDs may resume. The CommandLedger's exact replay returns prior command results without reapplying them; different request semantics conflict. Do not silently generate new command IDs to get around a conflict. If the exact prefix cannot be proven, stop for review.

## 11. Operator CLI Surface

Recommend a separate module, for example:

```text
python -m adapters.bootstrap initialize-instance --data-root <local-root> --policy <local-policy> --independent-purge-journal <local-journal>
python -m adapters.bootstrap authority-bootstrap --data-root <local-root> --policy <same-local-policy> --operator-principal-id <id> --runtime-principal-id <id> --task-id <reserved-id> --root-run-id <reserved-id> --grant-id <id> --expires-at <timestamp> --command-id-prefix <stable-id>
```

These are design examples, not implemented commands. Keeping bootstrap separate preserves `python -m adapters.client` and Panel's existing-root-only semantics. Stage 1 should require explicit intent and report that it creates only an unauthorized instance. Stage 2 must require TTY confirmation of the full normalized authority request; no `--yes`, `--force`, environment-driven identity, or startup auto-bootstrap option. Do not print local absolute paths in persistent records or public output.

## 12. Purge Journal Semantics

Reuse `IndependentPurgeJournal` exactly; do not add another journal initializer. For a new root, the configured journal must be independent of the data root, valid, and have no historical records. `ObjectStore`'s existing fresh-start path establishes an empty journal, applies migrations, then persists its identity digest and sequence-zero/head-hash watermark. On normal reopen, the same configured journal identity and current head must match the database watermark. A non-empty journal without a matching database watermark, a changed journal identity, or a missing/mismatched head enters recovery and blocks normal startup.

The journal identity in the database is a digest, not a local path. A failed initialization must never “fix” a mismatch by deleting/replacing the external journal.

## 13. Root Safety

Before any constructor can create files, the future initializer must:

- reject a target inside the source repository, `ui-preview`, a test/Academy root, or a known backup/restore root;
- require the target not to exist or to be a strictly empty directory with no database, object payload, foreign file, or existing instance metadata;
- resolve path aliases and symlinks and prove the final target remains outside the repository and disjoint from source/journal; ambiguous resolution fails closed;
- ensure the purge journal is outside the data root and empty of historical records; reject aliases between journal and target;
- never overwrite, migrate an incompatible existing instance as a “fresh” root, recover, or repair in place.

Do not log or persist absolute local paths. A repo-external root identity can be a generated opaque instance ID; it must not encode the path.

## 14. Stage 3 Boundary

Stage 3 remains the separately accepted **Project Nexus Self-Hosting Bootstrap Slice 1** proposal: first real Task/Root Run, carefully selected source artifacts, Evidence, limited Memory Candidates/admission, CURRENT vs SUPERSEDED representation, and one Context Pack using existing services. It is not authorized by this contract.

Pre-Nexus history must be imported as source Artifact/Evidence with explicit `IMPORTED_FROM_PRE_NEXUS_HISTORY` provenance. It must not become fabricated historical Task/Run/Trace/Verification/Effect/Approval activity. A governed local Git/document source import boundary remains the next application dependency after Authority bootstrap; it is not part of Stage 1/2.

## 15. Test Plan

**Instance initialization:** external empty root succeeds; existing DB, nonempty root, repo-contained/symlink-escaping target, target alias, stale/nonempty journal, or incompatible policy binding is denied before overwrite; migration sequence completes; normal reopen succeeds with matching journal identity/head; sequence-zero watermark is consistent; no user Task/Run/Principal/Grant is created.

**Authority bootstrap:** empty policy anchor list denies Stage 2; an arbitrary ID not listed in policy cannot become an anchor; exact policy-listed HUMAN is accepted; recovery Principal cannot be promoted/used; explicit SERVICE grantee receives only the exact task-bound Grant; expiry is required; wildcard, unrelated task/resource, egress, Effect, or unapproved actions are denied; exact command replay is idempotent; changed command semantics conflict; failure after each persisted command leaves a detectable partial state and exact retry resumes without duplicate rows.

**Privacy/reopen:** no absolute path or credential appears in persisted/public projection; only safe policy version/hash and journal identity digest are exposed; normal operator startup enforces the initialization policy binding; initialization and authority states survive close/reopen.

These tests must use a temporary external root and local test policy, but test identities/policies must never be presented as production bootstrap evidence.

## 16. Non-goals

This contract does not authorize creating a production root, changing `default-policy.json`, adding Principal/TrustAnchor/Grant rows, creating a Task/Run, importing source, writing Memory/Evidence/Artifact/Context Pack, changing Foundation Contracts, implementing a general account system, adding policy rotation, building a source importer, or running Utility/Host/Academy experiments.

## 17. Exact Next Step

External review this bootstrap contract, especially the proposed policy-digest instance binding, local HUMAN/TTY threat-model wording, service-grantee semantics, Cartesian Grant-scope limitation, partial Stage 2 recovery, and exact Stage 3 resource/action plan. Only after acceptance and separate implementation authorization should a small initialization/Authority bootstrap slice be implemented and tested. Bootstrap execution still requires its own explicit authorization; this document grants none.
