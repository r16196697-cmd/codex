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

### 2.1 Root Generation and Policy-Binding States

The binding protocol must distinguish the root's generation instead of interpreting every missing binding the same way:

| State | Meaning | Policy-content binding | Ordinary product behavior |
|---|---|---|---|
| `FRESH_BOUND_INSTANCE` | Created by the reviewed Stage 1 intent, with its exact binding committed before success is reported. | `BOUND` to the recorded policy version/digest and journal identity digest. | Normal startup is allowed only while all bound identities match. |
| `LEGACY_UNBOUND_INSTANCE` | Database existed before the binding feature and was migrated without adopting a policy document. This does not mean compromised. | `UNVERIFIED`; no policy digest is inferred. | Read/inspect and explicit adoption are allowed; ordinary mutations are suspended. |
| `LEGACY_ADOPTED_BOUND_INSTANCE` | Legacy-origin database whose operator explicitly bound a policy at adoption time. | `BOUND_FROM_ADOPTION`; historical policy content remains unknown. | Normal startup is allowed only while the adopted policy and journal identities match. |
| `PARTIAL_FRESH_BOOTSTRAP` | A fresh-init intent exists, but schema, journal watermark, and binding have not all reached the committed state. | `PENDING` or incomplete. | Ordinary client and product services refuse the root; only exact bootstrap resume/inspection is allowed. |
| `CONFLICT_OR_RECOVERY_REQUIRED` | Input, schema, journal, binding, or persisted state disagrees or cannot be classified safely. | Untrusted/ambiguous. | Fail closed; require reviewed recovery. |

`LEGACY_UNBOUND_INSTANCE` is an honest compatibility classification, not a finding of compromise. `POLICY_CONTENT_BINDING=UNVERIFIED` means Nexus cannot prove which complete policy document governed the historical database because old Authority rows preserve only `policy_version`.

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
4. Require an explicit `INSTANCE_INITIALIZE` mode that ordinary `ObjectStore` construction cannot select implicitly. It applies the normal contiguous migrations and Purge Journal freshness handshake, then commits the binding record. The migration creates the binding schema only; it never hashes or adopts the currently loaded policy.
5. Report `INITIALIZED_UNAUTHORIZED` only after the complete binding is durably committed and re-read. Do not create Task/Run/Memory or user Authority rows. Ordinary production open is permitted only for a complete bound instance (or the restricted legacy inspect/adoption surface below).

The fresh binding contains a generated `instance_id`, policy version, canonical policy SHA-256, independent-journal identity digest, and `initialized_at`. It contains no policy text or local path. Initialization has durable intermediate steps, so it is not one database transaction; the final binding transaction is the completion point, and Stage 1 reports success only after that binding is committed and re-read. Normal startup compares all bound values and fails closed on mismatch. Same policy version with a different digest is a conflict; a different policy version is unsupported until a separately reviewed rotation design exists.

### Fresh Stage 1 Partial Failure and Resume

Before side effects, Stage 1 validates the target, configured journal, policy, and aliases. It then atomically writes a small, secret-free fresh-init intent in the target root before creating the journal or database. The intent binds a generated instance ID, stable initialization command ID, policy version/digest, journal identity digest, and protocol version; it is a resume marker, not an Authority grant or completed policy binding. It stores no absolute path.

| Crash point | Next invocation |
|---|---|
| Before the intent is durable | No initialization side effect is recognized. A new explicit intent may start only after the normal empty-root/journal preflight passes. |
| After intent, before journal creation | Verify the exact intent and supplied policy/journal identities, then resume the same initialization. |
| Journal created, before migrations complete | Verify the journal is still empty and matches the intent; resume only the contiguous migration prefix. Any history or identity mismatch is `CONFLICT_OR_RECOVERY_REQUIRED`. |
| Migrations complete, before the sequence-zero watermark | Verify the same intent, schema/checksums, empty journal, and no ordinary user rows; complete the existing watermark handshake, then continue. |
| Watermark complete, before binding commit | Verify all prior identities and the exact pending-fresh marker; commit the binding once. Do not infer completion from `nexus.sqlite` or migrations alone. |
| Binding committed, response lost | Re-read and compare the exact binding and intent; return the prior success without another mutation. |

An exact retry may resume only the same fresh-init command and immutable request. It must not delete/recreate a database or journal. Missing intent with partial files, changed inputs, unexpected rows, non-empty journal, or an unprovable crash point fails closed for review. Retain the non-sensitive intent as provenance and require it to agree with the committed binding on reopen; it is not silently removed as a repair step.

## 6. Stage 2 — AUTHORITY_BOOTSTRAP

Stage 2 is a distinct, explicitly invoked operator action after Stage 1. It composes existing AuthorityService operations; it does not insert Authority rows outside Core.

Recommended sequence:

1. Require `FRESH_BOUND_INSTANCE` or an explicitly adopted legacy binding; revalidate the exact current policy digest/version and journal identity. A legacy unbound root cannot enter Stage 2 directly.
2. Require an interactive TTY confirmation over a displayed, immutable bootstrap request.
3. Register the exact operator Principal as `HUMAN`, `ACTIVE`.
4. Register an explicitly named `SERVICE`, `ACTIVE` local bootstrap/runtime Principal for the Bridge to act as. It is not an administrator; it receives only the following task-bound Grant. If review prefers a human as the direct Run actor, that alternative must be explicit and must not silently change actor semantics.
5. Register a Trust Anchor for the HUMAN Principal only if that exact ID is already listed in the loaded policy and `policy_ref` equals its policy version.
6. Create the initial ACTIVE root Grant last, issued by that anchor Principal to the scoped SERVICE Principal, with exact pre-frozen IDs and required expiry.

The Authority APIs already enforce active Principal checks, exact policy anchor membership/version, root issuer trust, grant scope shape, required `issued_at`/`expires_at`, and command-ledger replay. The bootstrap caller must use distinct stable command IDs for each operation and must not translate conflicts into upserts.

Stage 2 is not one database transaction today: each AuthorityService operation commits its own command. Do not claim atomicity. A partial prefix remains detectable and cannot be mistaken for completion. Retrying uses the same command IDs and exact request bodies; an identity/scope mismatch is a conflict requiring operator review. Grant creation is last, so a failed prefix has no persisted task-scoped work Grant. If the Trust Anchor command has committed, that exact policy-backed anchor is nevertheless a root-of-authority by existing semantics; the instance is partial, ordinary project work must not start, and no Grant is auto-created. Resume requires an explicit operator confirmation of the same frozen plan.

## 7. Policy / Trust Anchor Model

For fresh roots, the operator supplies a local, secret-free, schema-valid production policy; Stage 1 validates and binds its version and canonical SHA-256; Stage 2 may register only the exact HUMAN principal already named in that policy. The repo default stays `trust_anchors: []` and is not changed to name the local user.

### Selected Legacy Strategy: Explicit Adoption Required

Choose **Option A — explicit legacy adoption**, not automatic compatibility writes. A root proven to predate the binding feature is classified `LEGACY_UNBOUND_INSTANCE` with `POLICY_CONTENT_BINDING=UNVERIFIED`. It is not called compromised. Ordinary mutating product paths and new Authority issuance are suspended until an operator explicitly adopts a policy binding; safe read/inspect and the adoption path remain available. This avoids silently trusting whichever policy happens to be loaded while preserving a recovery path for legacy data.

The separate `adopt-policy-binding` operator action must use an interactive local confirmation and, before committing, verify the existing migration/checksum state, Purge Journal identity/head/watermark, object integrity and purge state, and authority consistency. It displays the proposed policy version and digest. It requires every persisted Trust Anchor to be allowed by that policy; all existing Grants, classification assertions, and Approval records must have the proposed policy version and pass current policy/authority consistency checks. Ambiguous or incompatible state is rejected without changing history. Adoption does not rewrite old Authority records or claim that the exact document was used historically. It binds the instance **from adoption time onward**, recording `source=LEGACY_OPERATOR_ADOPTION` and an adoption timestamp; original `initialized_at` remains unknown if it was not recorded. Only after this transaction commits can ordinary product mutations resume under the bound policy.

The policy file path is never stored or shown in Nexus projections. The canonical digest is safe metadata. The current TrustAnchor `policy_ref` and Grant `policy_version` remain as implemented; the instance binding adds content integrity because those existing fields do not persist the document hash. No migration may populate that digest from the process's current policy.

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

Stage 1 can be recognized as `FRESH_BOUND_INSTANCE` only from a complete current migration sequence, a valid bound instance row, an intact Purge Journal watermark, and zero ordinary Principal/Trust Anchor/Grant/Task/Run rows. The reserved recovery Principal is expected. An existing database whose pre-feature generation was captured as legacy is `LEGACY_UNBOUND_INSTANCE`; its missing policy digest is not repaired by ordinary startup. A durable fresh-init intent without a complete binding is `PARTIAL_FRESH_BOOTSTRAP`. Any state that cannot be proven to match one of these generations is `CONFLICT_OR_RECOVERY_REQUIRED`. No completion marker is needed for Stage 2: completion is derived from the exact active operator Principal, policy-backed anchor, exact active unexpired root Grant and its CommandLedger results.

Derived states should be reported distinctly:

- `UNINITIALIZED`: no Nexus database and no history-bearing journal.
- `INITIALIZED_UNAUTHORIZED`: a `FRESH_BOUND_INSTANCE` has valid migrations, binding and watermark; no ordinary authority.
- `LEGACY_UNBOUND_INSTANCE`: migrated historical database; policy content is unverified, not presumed compromised; ordinary mutation is suspended pending explicit adoption.
- `LEGACY_ADOPTED_BOUND_INSTANCE`: legacy-origin root with an explicit adoption binding; its historical policy content remains unknown, while current opens enforce the adopted digest.
- `PARTIAL_FRESH_BOOTSTRAP`: a fresh-init intent exists but the complete schema/journal/binding commit is absent; ordinary product open is denied.
- `AUTHORITY_BOOTSTRAP_PARTIAL`: only an exact prefix of the planned Principal/anchor/grant commands is committed; ordinary bootstrap work remains denied.
- `AUTHORITY_BOOTSTRAP_COMPLETE`: all exact records and command results match the approved plan. Current usability is checked separately; an expired/revoked Grant does not trigger automatic re-issuance.
- `CONFLICT_OR_RECOVERY_REQUIRED`: unexpected rows, mismatched replay, altered policy, journal inconsistency, or incompatible data.

On partial Stage 2, only an explicit operator rerun with the same IDs, same request, same policy digest, and same command IDs may resume. The CommandLedger's exact replay returns prior command results without reapplying them; different request semantics conflict. Do not silently generate new command IDs to get around a conflict. If the exact prefix cannot be proven, stop for review.

### Migration and Startup Ordering

The required order is:

1. A dedicated bootstrap preflight determines whether this is an explicitly requested fresh root or a database that already existed before the binding feature. It validates target/journal aliases and the supplied policy without opening a normal writable product instance.
2. For a fresh root, atomically create the durable fresh-init intent before creating journal/database files. Ordinary startup cannot set `INSTANCE_INITIALIZE` implicitly. A crash residue that is not a complete verifiable intent is a conflict, not an empty-root retry.
3. Acquire the existing single-writer lock. Apply contiguous checksum-verified migrations. The binding migration creates only the binding/state schema; it never records the loaded policy hash. A narrowly scoped migration-coordinator hook records a generation marker (`PENDING_FRESH` for an explicit matching fresh intent, or `LEGACY_UNBOUND` only when pre-migration evidence proves the database already existed) in the same migration transaction. Both markers have a null policy digest until an explicit binding action commits. This marker records generation only; it is not a policy endorsement.
4. Run the existing independent Purge Journal startup validation and sequence-zero watermark handshake after schema migrations. Fresh initialization requires the exact empty journal bound by its intent; history or mismatch is a conflict. A legacy root must match its persisted watermark and journal identity; migration cannot bless a replacement journal.
5. Before orphan cleanup or exposing ordinary Core/application services, ObjectStore validates generation and binding. Fresh initialization commits the complete policy/journal binding only through the restricted bootstrap initialization mode. Legacy remains unverified until the explicit adoption command commits. Normal startup never creates either binding.
6. `FRESH_BOUND_INSTANCE` opens normally only when schema, policy version/digest, journal identity, and watermark all match. A same-version/different-hash policy fails closed; a different version is unsupported pending a separately reviewed rotation design. Partial or conflicting states expose only a sanitized status and the permitted bootstrap/recovery surface.

If the implementation cannot atomically preserve the pre-migration generation fact alongside the binding-schema migration, it must classify the resulting root as `CONFLICT_OR_RECOVERY_REQUIRED`; it must not guess fresh versus legacy. `force_recovery` is not an initialization mode and must not be reused for this purpose.

### Enforcement Boundary

Use both layers. ObjectStore/Core startup is the invariant boundary: it detects generation, validates bound policy/journal identities, exposes sanitized binding status, and blocks ordinary mutations for partial, conflicting, or legacy-unadopted roots before cleanup/services are exposed. A narrowly scoped inspect/adoption startup mode may expose only reads needed to validate and the binding-adoption mutation; it is distinct from `force_recovery`. The dedicated bootstrap application owns the explicit fresh binding commit and legacy adoption intent. `adapters.client`, Panel, and other compositions consume that status but cannot bypass it; the ordinary ObjectStore constructor never silently binds or adopts a policy.

## 11. Operator CLI Surface

Recommend a separate module, for example:

```text
python -m adapters.bootstrap initialize-instance --data-root <local-root> --policy <local-policy> --independent-purge-journal <local-journal>
python -m adapters.bootstrap authority-bootstrap --data-root <local-root> --policy <same-local-policy> --operator-principal-id <id> --runtime-principal-id <id> --task-id <reserved-id> --root-run-id <reserved-id> --grant-id <id> --expires-at <timestamp> --command-id-prefix <stable-id>
python -m adapters.bootstrap adopt-policy-binding --data-root <legacy-root> --policy <reviewed-local-policy> --command-id <stable-id>
```

These are design examples, not implemented commands. Keeping bootstrap separate preserves `python -m adapters.client` and Panel's existing-root-only semantics. Stage 1 should require explicit intent and report that it creates only an unauthorized instance. Stage 2 must require TTY confirmation of the full normalized authority request; legacy adoption must independently require TTY confirmation of the displayed policy version/digest and compatibility findings. No `--yes`, `--force`, environment-driven identity, or startup auto-bootstrap option is allowed. Do not print local absolute paths in persistent records or public output.

## 12. Purge Journal Semantics

Reuse `IndependentPurgeJournal` exactly; do not add another journal initializer. For a new root, the configured journal must be independent of the data root, valid, and have no historical records. The explicit bootstrap flow writes its init intent, establishes the empty journal, applies migrations, performs the existing identity/head validation and sequence-zero watermark handshake, and only then commits the fresh policy binding. On normal reopen, the same configured journal identity and current head must match the database watermark before a bound instance is exposed. A non-empty journal without a matching database watermark, a changed journal identity, or a missing/mismatched head enters recovery and blocks normal startup. A legacy adoption also validates, but never resets, its existing watermark.

The journal identity in the database is a digest, not a local path. A failed initialization must never “fix” a mismatch by deleting/replacing the external journal.

## 13. Root Safety

Before any constructor can create files, the future initializer must:

- reject a target inside the source repository, `ui-preview`, a test/Academy root, or a known backup/restore root;
- require the target not to exist or to be a strictly empty directory with no database, object payload, foreign file, or existing instance metadata; on exact resume, the only permitted entry is the verifiable fresh-init intent and its atomic-write residue is never ignored;
- resolve path aliases and symlinks and prove the final target remains outside the repository and disjoint from source/journal; ambiguous resolution fails closed;
- ensure the purge journal is outside the data root and empty of historical records; reject aliases between journal and target;
- never overwrite, migrate an incompatible existing instance as a “fresh” root, recover, or repair in place.

Do not log or persist absolute local paths. A repo-external root identity can be a generated opaque instance ID; it must not encode the path.

## 14. Stage 3 Boundary

Stage 3 remains the separately accepted **Project Nexus Self-Hosting Bootstrap Slice 1** proposal: first real Task/Root Run, carefully selected source artifacts, Evidence, limited Memory Candidates/admission, CURRENT vs SUPERSEDED representation, and one Context Pack using existing services. It is not authorized by this contract.

Pre-Nexus history must be imported as source Artifact/Evidence with explicit `IMPORTED_FROM_PRE_NEXUS_HISTORY` provenance. It must not become fabricated historical Task/Run/Trace/Verification/Effect/Approval activity. A governed local Git/document source import boundary remains the next application dependency after Authority bootstrap; it is not part of Stage 1/2.

## 15. Test Plan

**Instance initialization and generations:** external empty root succeeds only via explicit fresh intent; existing DB, nonempty root, repo-contained/symlink-escaping target, target alias, stale/nonempty journal, or incompatible policy binding is denied before overwrite; migration sequence completes; normal reopen succeeds with matching journal identity/head; sequence-zero watermark is consistent; no user Task/Run/Principal/Grant is created. A pre-feature root migrates to `LEGACY_UNBOUND_INSTANCE` with a null/unverified policy digest, and tests prove migration never copies the currently loaded policy hash. Same policy version with changed digest fails closed; changed policy version is unsupported. Fresh marker and binding agree across close/reopen.

**Stage 1 partial failure:** inject a crash after intent, journal creation, migration completion, watermark commit, and binding commit. Exact same-command/input retries resume only provable pending states; a lost response after binding returns the committed result. Changed policy/journal/command, unexpected journal history/rows, missing marker with partial files, or unclassifiable migration state fails closed; no path deletes/recreates the database. Ordinary client refuses `PARTIAL_FRESH_BOOTSTRAP` and `CONFLICT_OR_RECOVERY_REQUIRED`.

**Legacy adoption:** unbound legacy roots permit only safe inspection/adoption before binding; ordinary mutations and new Trust Anchor/root Grant issuance are denied. Adoption requires TTY confirmation, valid migration/journal/object/purge integrity, all current Trust Anchors allowed by the supplied policy, and compatible policy versions for existing Grants/classifications/Approvals. Exact adoption replay returns the existing binding; changed request conflicts; incompatible state remains unbound and unmodified. The persisted source/timestamp explicitly say `LEGACY_OPERATOR_ADOPTION` and do not rewrite old Authority rows.

**Authority bootstrap:** empty policy anchor list denies Stage 2; an arbitrary ID not listed in policy cannot become an anchor; exact policy-listed HUMAN is accepted; recovery Principal cannot be promoted/used; explicit SERVICE grantee receives only the exact task-bound Grant; expiry is required; wildcard, unrelated task/resource, egress, Effect, or unapproved actions are denied; exact command replay is idempotent; changed command semantics conflict; failure after each persisted command leaves a detectable partial state and exact retry resumes without duplicate rows.

**Privacy/reopen:** no absolute path or credential appears in persisted/public projection; only safe policy version/hash and journal identity digest are exposed; normal operator startup enforces the initialization policy binding; initialization and authority states survive close/reopen.

These tests must use a temporary external root and local test policy, but test identities/policies must never be presented as production bootstrap evidence.

## 16. Non-goals

This contract does not authorize creating a production root, changing `default-policy.json`, adding Principal/TrustAnchor/Grant rows, creating a Task/Run, importing source, writing Memory/Evidence/Artifact/Context Pack, changing Foundation Contracts, implementing a general account system, implementing policy rotation, building a source importer, or running Utility/Host/Academy experiments. For a bound instance, same-version/different-hash policy is fail-closed and a different policy version remains unsupported until a separate reviewed rotation design.

## 17. Exact Next Step

External review this bootstrap contract, especially fresh/legacy generation classification, the rule that migration never adopts the loaded policy, explicit legacy adoption compatibility checks, restricted Stage 1 resume, startup enforcement ordering, local HUMAN/TTY threat-model wording, service-grantee semantics, Cartesian Grant-scope limitation, partial Stage 2 recovery, and exact Stage 3 resource/action plan. Only after acceptance and separate implementation authorization should a small initialization/Authority bootstrap slice be implemented and tested. Bootstrap execution still requires its own explicit authorization; this document grants none.
