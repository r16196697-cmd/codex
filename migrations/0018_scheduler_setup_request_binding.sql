ALTER TABLE subtask_attempts ADD COLUMN schedule_request_hash TEXT;

DROP TRIGGER subtask_attempts_identity_immutable;
CREATE TRIGGER subtask_attempts_identity_immutable BEFORE UPDATE ON subtask_attempts
WHEN NOT (
    (NEW.attempt_id=OLD.attempt_id AND NEW.task_id=OLD.task_id AND NEW.subtask_id=OLD.subtask_id
     AND NEW.attempt_no=OLD.attempt_no AND NEW.run_id=OLD.run_id AND NEW.route_decision_ref IS OLD.route_decision_ref
     AND NEW.requested_capability=OLD.requested_capability AND NEW.attempt_reason=OLD.attempt_reason
     AND NEW.predecessor_attempt_id IS OLD.predecessor_attempt_id AND NEW.command_id=OLD.command_id
     AND NEW.schedule_request_hash IS OLD.schedule_request_hash AND NEW.created_at=OLD.created_at
     AND (OLD.outcome=NEW.outcome OR (OLD.outcome='CREATED' AND NEW.outcome IN ('READY','RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='READY' AND NEW.outcome IN ('RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='RUNNING' AND NEW.outcome IN ('SUCCEEDED','FAILED','CANCELLED','INCONCLUSIVE'))))
    OR (nexus_purge_redaction()=1 AND NEW.attempt_id=OLD.attempt_id AND NEW.task_id=OLD.task_id
      AND NEW.subtask_id=OLD.subtask_id AND NEW.attempt_no=OLD.attempt_no AND NEW.run_id=OLD.run_id
      AND NEW.route_decision_ref IS NULL AND NEW.requested_capability=OLD.requested_capability
      AND NEW.attempt_reason=OLD.attempt_reason AND NEW.predecessor_attempt_id IS OLD.predecessor_attempt_id
      AND NEW.outcome=OLD.outcome AND NEW.command_id=OLD.command_id
      AND NEW.schedule_request_hash IS OLD.schedule_request_hash AND NEW.created_at=OLD.created_at
      AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.route_decision_ref AND s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'INVALID_SUBTASK_ATTEMPT_TRANSITION'); END;
