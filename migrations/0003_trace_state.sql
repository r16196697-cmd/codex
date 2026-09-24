CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    requester_id TEXT NOT NULL REFERENCES principals(principal_id),
    status TEXT NOT NULL CHECK (status IN ('CREATED','ACTIVE','WAITING','SUCCEEDED','FAILED','CANCELLED')),
    created_at TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE,
    root_run_id TEXT UNIQUE REFERENCES runs(run_id)
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    subtask_id TEXT,
    parent_run_id TEXT REFERENCES runs(run_id),
    executor_kind TEXT NOT NULL CHECK (executor_kind IN ('ORCHESTRATOR','MODEL','TOOL')),
    status TEXT NOT NULL CHECK (status IN ('CREATED','READY','RUNNING','WAITING','VERIFYING','SUCCEEDED','FAILED','CANCELLED')),
    grant_id TEXT NOT NULL REFERENCES delegation_grants(grant_id),
    manifest_ref TEXT,
    budget_reservation_ref TEXT,
    data_boundary_json TEXT NOT NULL CHECK (json_valid(data_boundary_json)),
    classification_assertion_ref TEXT NOT NULL REFERENCES classification_assertions(assertion_id),
    created_at TEXT NOT NULL,
    CHECK ((parent_run_id IS NULL AND executor_kind='ORCHESTRATOR') OR (parent_run_id IS NOT NULL AND executor_kind IN ('MODEL','TOOL')))
);

CREATE TABLE trace_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    seq_no INTEGER NOT NULL CHECK (seq_no >= 1),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES principals(principal_id),
    event_json TEXT NOT NULL CHECK (json_valid(event_json)),
    UNIQUE(run_id, seq_no)
);

CREATE INDEX trace_events_type_idx ON trace_events(event_type, occurred_at);
CREATE INDEX runs_task_idx ON runs(task_id, parent_run_id);

CREATE TRIGGER tasks_identity_immutable
BEFORE UPDATE ON tasks
WHEN NEW.task_id <> OLD.task_id
  OR NEW.requester_id <> OLD.requester_id
  OR NEW.created_at <> OLD.created_at
  OR NEW.command_id <> OLD.command_id
  OR (OLD.root_run_id IS NOT NULL AND NEW.root_run_id IS NOT OLD.root_run_id)
  OR NOT ((OLD.status='CREATED' AND NEW.status='ACTIVE' AND NEW.root_run_id IS NOT NULL)
       OR (OLD.status='ACTIVE' AND NEW.status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('ACTIVE','FAILED','CANCELLED')))
BEGIN
    SELECT RAISE(ABORT, 'INVALID_TASK_TRANSITION');
END;

CREATE TRIGGER runs_identity_immutable
BEFORE UPDATE ON runs
WHEN NEW.run_id <> OLD.run_id
  OR NEW.task_id <> OLD.task_id
  OR NEW.subtask_id IS NOT OLD.subtask_id
  OR NEW.parent_run_id IS NOT OLD.parent_run_id
  OR NEW.executor_kind <> OLD.executor_kind
  OR NEW.grant_id <> OLD.grant_id
  OR NEW.manifest_ref IS NOT OLD.manifest_ref
  OR NEW.budget_reservation_ref IS NOT OLD.budget_reservation_ref
  OR NEW.data_boundary_json <> OLD.data_boundary_json
  OR NEW.classification_assertion_ref <> OLD.classification_assertion_ref
  OR NEW.created_at <> OLD.created_at
  OR NOT ((OLD.status='CREATED' AND NEW.status IN ('READY','CANCELLED'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','VERIFYING','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED'))
       OR (OLD.status='VERIFYING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED','WAITING')))
BEGIN
    SELECT RAISE(ABORT, 'INVALID_RUN_TRANSITION');
END;

CREATE TRIGGER trace_events_no_update
BEFORE UPDATE ON trace_events
BEGIN
    SELECT RAISE(ABORT, 'TRACE_EVENT_IMMUTABLE');
END;
CREATE TRIGGER trace_events_no_delete
BEFORE DELETE ON trace_events
BEGIN
    SELECT RAISE(ABORT, 'TRACE_EVENT_IMMUTABLE');
END;
