# Nexus Utility MVP — Slice 1

**Status:** ALPHA. **Host portability:** deferred, not abandoned. Phase 5 remains `CLOSED / ACCEPTED`; Phase 6 remains `CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED`.

Slice 1 adds a small native companion window and a sanitized, read-only panel ViewModel. The current Codex integration is a companion window, not an embedded Codex panel. It does not patch Codex Desktop internals. No chat, model routing, cross-Host orchestration, Skill selection, or Context Pack runtime is included.

## Launch

Use an existing Nexus data root; the panel refuses to initialize a new database:

```powershell
python -m adapters.panel --data-root <existing-nexus-data-root>
```

The panel uses Python's standard-library Tkinter UI. `adapters.panel.application` composes existing Nexus services and bounded Core queries; the UI only consumes the ViewModel. This is a command-scoped Operator Panel: the Panel command itself owns the single writer for its session. It cannot attach concurrently to a different writer process; there is no persistent writer daemon or IPC endpoint. The current scope is one Nexus data root because the Core has no first-class Project object.

## Participation mode

Participation is persisted separately from Runtime safety/recovery modes (`NORMAL`, `SAFE`, `STATELESS`, `RECOVERY`). They are independent axes.

- `ACTIVE`: the existing Hosted Bridge may participate in governed Task/Run/Evidence/Artifact processing. Context selection and portable Skill fallback remain `NOT IMPLEMENTED` in this slice.
- `OBSERVE`: the Hosted Bridge does not ingest canonical Host Task/Run/output state or alter Host context. Future comparison telemetry requires an explicit instrumented path; this slice does not invent observations.
- `BYPASS`: the Hosted Bridge does not participate in Host execution or automatically ingest Task/result state. Existing persisted Nexus data remains intact; BYPASS is not purge.

Leaving `ACTIVE` prompts for confirmation. Mode changes to `OBSERVE` or `BYPASS` are refused while unfinished Tasks, in-flight Runs, pending Effects, or approval-bound pending commit state remain. The mode change does not cancel work. The scope is instance-level and may later be upgraded to a Project scope.

## Panel pages and evidence

At the Slice 1 boundary, `CONTEXT` and `SKILLS` reported `NOT IMPLEMENTED`; later Slice 2 and Slice 3 documents supersede those page states. `OVERVIEW`, `TASKS`, and `MEMORY` show bounded Core metadata. Memory payload bodies are never included. Academy static screening is not a production Skill registry.

`VALUE` presents a metering shape with `OBSERVED`, `HOST_DECLARED`, `DERIVED`, and `UNAVAILABLE` provenance. The panel reports grounded Core counts as derived snapshots. Host token/latency telemetry, Context Pack size, Skill selection/loading, duplicate/reused work, and cost/savings remain `UNAVAILABLE` unless later reported or derived from actual Nexus records. Missing telemetry is `null` plus `UNAVAILABLE`, never zero. No behavioral experiment is run by this slice.

The UI presentation is isolated under `adapters/panel`; it can later be replaced by an officially supported embedded panel, DSH panel, or desktop shell without moving participation policy into UI code.
