# Read a compiled Context Pack

The operator CLI can return an already compiled Context Pack through an explicit read-only path:

```powershell
python -m adapters.client --data-root <existing-root> --policy <policy.json> `
  --independent-purge-journal <journal.jsonl> context read --pack-ref <pack-id>
```

Use `context read --latest` to select the most recently compiled pack. Both commands open an existing bound instance in read-only mode. They do not initialize, migrate, clean up, repair, or mutate canonical Nexus state.

The read API accepts only a `COMPILED` Context Pack. It verifies the persisted Pack object and document, its record bindings and hashes, each embedded source's current availability, integrity metadata, classification, and the original Run data boundary. A missing, purged, unavailable, invalid, or out-of-bound dependency causes the entire read to fail closed. Arbitrary Object payloads cannot be read through this surface.

Context Pack retrieval is a bounded resource read. It does not grant write, execution, delegation, Skill, or Effect authority; it does not create a Task, Run, Grant, or delivery record; and it does not prove that a model saw the content. Persisted `model_visible_exposure` remains `UNKNOWN`. The Panel continues to show sanitized metadata and references without rendering Pack content.

Reading a Context Pack can support a continuation workflow, but it is not full Project Reconstruction and does not by itself establish that “Continue Nexus” is solved.
