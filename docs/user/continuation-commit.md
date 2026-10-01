# Governed Continuation Commit

Continuation Commit records the outcome of one real, bounded daily Task as
canonical project state. It is separate from Task Finish:

```text
Task Start -> Work -> Continuation Commit -> verify latest Context -> Task Finish
```

The operator uses a strict process-local plan with `continuation commit
--plan`. The existing instance must be bound and in NORMAL mode, participation
must be ACTIVE, the Task must be ACTIVE, its Root Run must be RUNNING, and its
original Grant must be current. The plan binds the previous Current State and
Context Pack, Git observation, HUMAN assertions, exact object IDs, and exact
pre-authorized resources. A changed state or request fails closed.

The first request requires a real interactive HUMAN confirmation:
`COMMIT CONTINUATION <task_id>`. The complete semantic request is then bound in
the existing CommandLedger before any continuation object is written. Exact
retries resume the same IDs and do not ask for the phrase again. Grants are not
created, expanded, replaced, or renewed by this command.

The commit creates an immutable Current State revision, a deterministic What
Changed delta, and a fresh Context Pack. It advances the logical Current State
reference with compare-and-swap and records an immutable `supersedes` relation;
the prior Current State remains historical. The new state records the exact
prior Context Pack ref and hashes so the prior state can be reopened through
the same explicit compiled-pack read surface. Selected stable source entries
are read only from that verified pack and copied as lineage-bound artifacts
owned by the current Task. Each wrapper retains its original source ref, type,
classification ref, integrity hash, and verified content. No arbitrary Object
payload browsing is provided.

Git branch, commit, worktree cleanliness, and the configured local
remote-tracking ref are verified from the process-local repository argument.
The remote value is a local tracking-ref expectation; this operation does not
fetch or assert a live remote state. A HUMAN confirms the accepted revision and
the objective, next step, and work summary. Task/Run lifecycle facts are
recorded separately from those HUMAN assertions. Chat text and model output do
not become canonical facts.

Context compilation uses the existing Context Pack service with explicit
sources and no Memory query. It does not admit Memory, create Skills, execute
Effects, or claim model visibility. `context read --latest` returning the pack
does not create a delivery receipt; `model_visible_exposure` remains UNKNOWN.

After a successful commit, verify the latest Context through the normal read
surface, then use the separately governed Task Finish operation. Task Finish
remains lifecycle closure only; it does not persist task outcome, update
Current State, or compile Context. Git HEAD alone does not imply accepted truth.
The Current State supersession relation preserves prior history without treating
the prior state as current.
