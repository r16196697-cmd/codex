CREATE TABLE task_dags (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    root_run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    dag_version TEXT NOT NULL,
    graph_hash TEXT NOT NULL CHECK(length(graph_hash)=64),
    node_count INTEGER NOT NULL CHECK(node_count>=0),
    command_id TEXT NOT NULL UNIQUE REFERENCES command_ledger(command_id),
    created_at TEXT NOT NULL
);

CREATE TABLE subtasks (
    subtask_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    node_index INTEGER NOT NULL CHECK(node_index>=0),
    node_json TEXT NOT NULL CHECK(json_valid(node_json)),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','READY','RUNNING','WAITING','SUCCEEDED','FAILED','CANCELLED','STALE')),
    scheduled_run_id TEXT UNIQUE REFERENCES runs(run_id),
    command_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(task_id,node_index),
    UNIQUE(task_id,subtask_id)
);

CREATE TABLE subtask_edges (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    dependency_id TEXT NOT NULL,
    dependent_id TEXT NOT NULL,
    PRIMARY KEY(task_id, dependency_id, dependent_id),
    CHECK(dependency_id <> dependent_id),
    FOREIGN KEY(task_id,dependency_id) REFERENCES subtasks(task_id,subtask_id),
    FOREIGN KEY(task_id,dependent_id) REFERENCES subtasks(task_id,subtask_id)
);

CREATE TABLE model_profiles (
    model_id TEXT NOT NULL,
    version TEXT NOT NULL,
    profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
    PRIMARY KEY(model_id, version)
);

CREATE TABLE tool_descriptors (
    tool_id TEXT NOT NULL,
    version TEXT NOT NULL,
    descriptor_json TEXT NOT NULL CHECK(json_valid(descriptor_json)),
    PRIMARY KEY(tool_id, version)
);

CREATE TABLE route_decisions (
    route_decision_id TEXT PRIMARY KEY,
    subtask_id TEXT NOT NULL REFERENCES subtasks(subtask_id),
    decision_object_id TEXT NOT NULL UNIQUE REFERENCES objects(object_id),
    decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
    command_id TEXT NOT NULL UNIQUE REFERENCES command_ledger(command_id),
    created_at TEXT NOT NULL
);

CREATE INDEX subtasks_task_status_idx ON subtasks(task_id, status);
CREATE INDEX subtask_edges_dependent_idx ON subtask_edges(task_id, dependent_id);

DROP TRIGGER runs_identity_immutable;
CREATE TRIGGER runs_identity_immutable
BEFORE UPDATE ON runs
WHEN NEW.run_id <> OLD.run_id
  OR NEW.task_id <> OLD.task_id
  OR NEW.subtask_id IS NOT OLD.subtask_id
  OR NEW.parent_run_id IS NOT OLD.parent_run_id
  OR NEW.executor_kind <> OLD.executor_kind
  OR NEW.grant_id <> OLD.grant_id
  OR (NEW.manifest_ref IS NOT OLD.manifest_ref AND NOT (OLD.status='CREATED' AND NEW.status='CREATED' AND OLD.manifest_ref IS NULL AND NEW.manifest_ref IS NOT NULL))
  OR NEW.budget_reservation_ref IS NOT OLD.budget_reservation_ref
  OR NEW.data_boundary_json <> OLD.data_boundary_json
  OR NEW.classification_assertion_ref <> OLD.classification_assertion_ref
  OR NEW.created_at <> OLD.created_at
  OR NOT ((OLD.status='CREATED' AND NEW.status IN ('CREATED','READY','CANCELLED'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','VERIFYING','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED'))
       OR (OLD.status='VERIFYING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED','WAITING')))
BEGIN
    SELECT RAISE(ABORT, 'INVALID_RUN_TRANSITION');
END;

CREATE TRIGGER runs_ready_requires_manifest
BEFORE UPDATE OF status ON runs
WHEN NEW.status='READY' AND (
    NEW.manifest_ref IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM object_envelopes e JOIN object_states s USING(object_id)
        WHERE e.object_id=NEW.manifest_ref AND e.object_type='run_manifest'
          AND s.lifecycle='ACTIVE' AND s.validity='VALID' AND s.payload_state='AVAILABLE'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'RUN_READY_REQUIRES_ACTIVE_MANIFEST');
END;

CREATE TRIGGER subtasks_identity_immutable
BEFORE UPDATE ON subtasks
WHEN NEW.subtask_id <> OLD.subtask_id OR NEW.task_id <> OLD.task_id
  OR NEW.node_index <> OLD.node_index
  OR NEW.node_json <> OLD.node_json OR NEW.command_id <> OLD.command_id
  OR (NEW.scheduled_run_id IS NOT OLD.scheduled_run_id AND NOT (OLD.status='PENDING' AND OLD.scheduled_run_id IS NULL AND NEW.scheduled_run_id IS NOT NULL AND NEW.status IN ('PENDING','READY')))
  OR NEW.created_at <> OLD.created_at
  OR NOT (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('READY','CANCELLED','STALE'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED','STALE'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED')))
BEGIN
    SELECT RAISE(ABORT, 'INVALID_SUBTASK_TRANSITION');
END;

CREATE TRIGGER subtask_edges_no_update BEFORE UPDATE ON subtask_edges BEGIN SELECT RAISE(ABORT, 'SUBTASK_EDGE_IMMUTABLE'); END;
CREATE TRIGGER subtask_edges_no_delete BEFORE DELETE ON subtask_edges BEGIN SELECT RAISE(ABORT, 'SUBTASK_EDGE_IMMUTABLE'); END;
CREATE TRIGGER model_profiles_no_update BEFORE UPDATE ON model_profiles BEGIN SELECT RAISE(ABORT, 'MODEL_PROFILE_IMMUTABLE'); END;
CREATE TRIGGER model_profiles_no_delete BEFORE DELETE ON model_profiles BEGIN SELECT RAISE(ABORT, 'MODEL_PROFILE_IMMUTABLE'); END;
CREATE TRIGGER tool_descriptors_no_update BEFORE UPDATE ON tool_descriptors BEGIN SELECT RAISE(ABORT, 'TOOL_DESCRIPTOR_IMMUTABLE'); END;
CREATE TRIGGER tool_descriptors_no_delete BEFORE DELETE ON tool_descriptors BEGIN SELECT RAISE(ABORT, 'TOOL_DESCRIPTOR_IMMUTABLE'); END;
CREATE TRIGGER route_decisions_no_update BEFORE UPDATE ON route_decisions BEGIN SELECT RAISE(ABORT, 'ROUTE_DECISION_IMMUTABLE'); END;
CREATE TRIGGER route_decisions_no_delete BEFORE DELETE ON route_decisions BEGIN SELECT RAISE(ABORT, 'ROUTE_DECISION_IMMUTABLE'); END;
CREATE TRIGGER task_dags_no_update BEFORE UPDATE ON task_dags BEGIN SELECT RAISE(ABORT, 'TASK_DAG_IMMUTABLE'); END;
CREATE TRIGGER task_dags_no_delete BEFORE DELETE ON task_dags BEGIN SELECT RAISE(ABORT, 'TASK_DAG_IMMUTABLE'); END;
CREATE TRIGGER subtasks_no_insert_after_dag BEFORE INSERT ON subtasks
WHEN EXISTS (SELECT 1 FROM task_dags WHERE task_id=NEW.task_id)
BEGIN SELECT RAISE(ABORT, 'TASK_DAG_IMMUTABLE'); END;
CREATE TRIGGER subtask_edges_no_insert_after_dag BEFORE INSERT ON subtask_edges
WHEN EXISTS (SELECT 1 FROM task_dags WHERE task_id=NEW.task_id)
BEGIN SELECT RAISE(ABORT, 'TASK_DAG_IMMUTABLE'); END;
