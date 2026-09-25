ALTER TABLE subtasks ADD COLUMN final_attempt_id TEXT REFERENCES subtask_attempts(attempt_id);
ALTER TABLE subtasks ADD COLUMN final_outcome TEXT CHECK(final_outcome IS NULL OR final_outcome IN ('SUCCEEDED','FAILED','CANCELLED','INCONCLUSIVE','POLICY_DENIED','BUDGET_DENIED'));
ALTER TABLE subtasks ADD COLUMN finalized_at TEXT;

CREATE TABLE subtask_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no > 0),
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    route_decision_ref TEXT REFERENCES objects(object_id),
    requested_capability TEXT NOT NULL,
    attempt_reason TEXT NOT NULL,
    predecessor_attempt_id TEXT REFERENCES subtask_attempts(attempt_id),
    outcome TEXT NOT NULL CHECK(outcome IN ('CREATED','READY','RUNNING','SUCCEEDED','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED','INCONCLUSIVE')),
    command_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(subtask_id, attempt_no),
    FOREIGN KEY(task_id, subtask_id) REFERENCES subtasks(task_id, subtask_id)
);

INSERT INTO subtask_attempts(attempt_id,task_id,subtask_id,attempt_no,run_id,route_decision_ref,requested_capability,attempt_reason,predecessor_attempt_id,outcome,command_id,created_at)
SELECT s.subtask_id || ':attempt:1', s.task_id, s.subtask_id, 1, s.scheduled_run_id,
       (SELECT r.decision_object_id FROM route_decisions r WHERE r.subtask_id=s.subtask_id ORDER BY r.created_at LIMIT 1),
       COALESCE((SELECT json_extract(r.decision_json,'$.selected_model_class') FROM route_decisions r WHERE r.subtask_id=s.subtask_id ORDER BY r.created_at LIMIT 1), json_extract(s.node_json,'$.requested_executor')),
       'LEGACY_SINGLE_RUN_BACKFILL', NULL,
       CASE run.status WHEN 'CREATED' THEN 'CREATED' WHEN 'READY' THEN 'READY' WHEN 'RUNNING' THEN 'RUNNING' WHEN 'SUCCEEDED' THEN 'SUCCEEDED' WHEN 'FAILED' THEN 'FAILED' WHEN 'CANCELLED' THEN 'CANCELLED' WHEN 'WAITING' THEN 'RUNNING' WHEN 'VERIFYING' THEN 'RUNNING' ELSE 'INCONCLUSIVE' END,
       'migration-v11-backfill-' || s.subtask_id, run.created_at
FROM subtasks s JOIN runs run ON run.run_id=s.scheduled_run_id;

UPDATE subtasks SET final_attempt_id=subtask_id || ':attempt:1',
    final_outcome=CASE status WHEN 'SUCCEEDED' THEN 'SUCCEEDED' WHEN 'FAILED' THEN 'FAILED' WHEN 'CANCELLED' THEN 'CANCELLED' ELSE NULL END,
    finalized_at=CASE WHEN status IN ('SUCCEEDED','FAILED','CANCELLED') THEN created_at ELSE NULL END
WHERE scheduled_run_id IS NOT NULL AND status IN ('SUCCEEDED','FAILED','CANCELLED');

CREATE INDEX subtask_attempts_order_idx ON subtask_attempts(task_id,subtask_id,attempt_no);
CREATE INDEX subtask_attempts_run_idx ON subtask_attempts(run_id);

DROP TRIGGER subtasks_identity_immutable;
CREATE TRIGGER subtasks_identity_immutable
BEFORE UPDATE ON subtasks
WHEN NEW.subtask_id <> OLD.subtask_id OR NEW.task_id <> OLD.task_id
  OR NEW.node_index <> OLD.node_index OR NEW.node_json <> OLD.node_json OR NEW.command_id <> OLD.command_id
  OR (NEW.scheduled_run_id IS NOT OLD.scheduled_run_id AND NOT (OLD.status='PENDING' AND OLD.scheduled_run_id IS NULL AND NEW.scheduled_run_id IS NOT NULL AND NEW.status IN ('PENDING','READY')))
  OR NEW.created_at <> OLD.created_at
  OR (OLD.final_attempt_id IS NOT NULL AND (NEW.final_attempt_id IS NOT OLD.final_attempt_id OR NEW.final_outcome IS NOT OLD.final_outcome OR NEW.finalized_at IS NOT OLD.finalized_at))
  OR ((NEW.final_attempt_id IS NULL) <> (NEW.final_outcome IS NULL))
  OR ((NEW.final_attempt_id IS NULL) <> (NEW.finalized_at IS NULL))
  OR NOT (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('READY','CANCELLED','STALE'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED','STALE'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('READY','RUNNING','FAILED','CANCELLED')))
BEGIN SELECT RAISE(ABORT, 'INVALID_SUBTASK_TRANSITION'); END;

CREATE TRIGGER subtask_attempts_identity_immutable
BEFORE UPDATE ON subtask_attempts
WHEN NEW.attempt_id <> OLD.attempt_id OR NEW.task_id <> OLD.task_id OR NEW.subtask_id <> OLD.subtask_id
  OR NEW.attempt_no <> OLD.attempt_no OR NEW.run_id <> OLD.run_id
  OR NEW.route_decision_ref IS NOT OLD.route_decision_ref OR NEW.requested_capability <> OLD.requested_capability
  OR NEW.attempt_reason <> OLD.attempt_reason OR NEW.predecessor_attempt_id IS NOT OLD.predecessor_attempt_id
  OR NEW.command_id <> OLD.command_id OR NEW.created_at <> OLD.created_at
  OR NOT (OLD.outcome=NEW.outcome OR (OLD.outcome='CREATED' AND NEW.outcome IN ('READY','RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='READY' AND NEW.outcome IN ('RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='RUNNING' AND NEW.outcome IN ('SUCCEEDED','FAILED','CANCELLED','INCONCLUSIVE')))
BEGIN SELECT RAISE(ABORT, 'INVALID_SUBTASK_ATTEMPT_TRANSITION'); END;
CREATE TRIGGER subtask_attempts_no_delete BEFORE DELETE ON subtask_attempts BEGIN SELECT RAISE(ABORT, 'SUBTASK_ATTEMPT_IMMUTABLE'); END;
