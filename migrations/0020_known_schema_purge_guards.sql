ALTER TABLE subtasks ADD COLUMN input_state TEXT NOT NULL DEFAULT 'AVAILABLE'
    CHECK(input_state IN ('AVAILABLE','PURGED_INPUT'));

DROP TRIGGER subtasks_identity_immutable;
CREATE TRIGGER subtasks_identity_immutable
BEFORE UPDATE ON subtasks
WHEN NOT (
    (NEW.subtask_id=OLD.subtask_id AND NEW.task_id=OLD.task_id
     AND NEW.node_index=OLD.node_index AND NEW.node_json=OLD.node_json
     AND NEW.input_state=OLD.input_state AND NEW.command_id=OLD.command_id
     AND NEW.created_at=OLD.created_at
     AND (OLD.final_attempt_id IS NULL OR
       (NEW.final_attempt_id IS OLD.final_attempt_id AND NEW.final_outcome IS OLD.final_outcome
        AND NEW.finalized_at IS OLD.finalized_at))
     AND ((NEW.final_attempt_id IS NULL)=(NEW.final_outcome IS NULL))
     AND ((NEW.final_attempt_id IS NULL)=(NEW.finalized_at IS NULL))
     AND (NEW.scheduled_run_id IS OLD.scheduled_run_id OR
       (OLD.status='PENDING' AND OLD.scheduled_run_id IS NULL AND NEW.scheduled_run_id IS NOT NULL
        AND NEW.status IN ('PENDING','READY')))
     AND (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('READY','CANCELLED','STALE'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED','STALE'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','SUCCEEDED','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('READY','RUNNING','FAILED','CANCELLED'))))
    OR (nexus_purge_redaction()=1 AND NEW.subtask_id=OLD.subtask_id AND NEW.task_id=OLD.task_id
      AND NEW.node_index=OLD.node_index AND NEW.command_id=OLD.command_id
      AND NEW.scheduled_run_id IS OLD.scheduled_run_id AND NEW.created_at=OLD.created_at
      AND NEW.status=OLD.status AND NEW.final_attempt_id IS OLD.final_attempt_id
      AND NEW.final_outcome IS OLD.final_outcome AND NEW.finalized_at IS OLD.finalized_at
      AND OLD.input_state='AVAILABLE' AND NEW.input_state='PURGED_INPUT'
      AND json_valid(OLD.node_json) AND json_valid(NEW.node_json)
      AND json_remove(OLD.node_json,'$.input_object_refs')=json_remove(NEW.node_json,'$.input_object_refs')
      AND EXISTS (SELECT 1 FROM json_each(OLD.node_json,'$.input_object_refs') o
        JOIN object_states s ON s.object_id=o.value AND s.payload_state='PURGED')
      AND NOT EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
        JOIN object_states s ON s.object_id=n.value AND s.payload_state='PURGED')
      AND NOT EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
        WHERE NOT EXISTS (SELECT 1 FROM json_each(OLD.node_json,'$.input_object_refs') o WHERE o.value=n.value)))
)
BEGIN SELECT RAISE(ABORT,'INVALID_SUBTASK_TRANSITION'); END;

CREATE TRIGGER subtask_known_ref_insert_guard BEFORE INSERT ON subtasks
WHEN EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
  JOIN object_states s ON s.object_id=n.value AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
  JOIN purge_barrier_refs r ON r.object_id=n.value JOIN purge_barriers b USING(barrier_id)
  WHERE b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER subtask_known_ref_update_guard BEFORE UPDATE OF node_json,input_state ON subtasks
WHEN NOT (nexus_purge_redaction()=1 AND NEW.input_state='PURGED_INPUT') AND (
  EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
    JOIN object_states s ON s.object_id=n.value AND s.payload_state='PURGED')
  OR EXISTS (SELECT 1 FROM json_each(NEW.node_json,'$.input_object_refs') n
    JOIN purge_barrier_refs r ON r.object_id=n.value JOIN purge_barriers b USING(barrier_id)
    WHERE b.status IN ('ACTIVE','PARTIAL')))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

CREATE TRIGGER delegation_grant_object_scope_insert_guard BEFORE INSERT ON delegation_grants
WHEN EXISTS (SELECT 1 FROM json_each(NEW.resource_scope_json) n JOIN objects o
  ON o.object_id=CASE WHEN substr(n.value,1,7)='object:' THEN substr(n.value,8) ELSE n.value END
  JOIN object_states s USING(object_id) WHERE s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM json_each(NEW.resource_scope_json) n JOIN objects o
  ON o.object_id=CASE WHEN substr(n.value,1,7)='object:' THEN substr(n.value,8) ELSE n.value END
  JOIN purge_barrier_refs r USING(object_id) JOIN purge_barriers b USING(barrier_id)
  WHERE b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

DROP TRIGGER effect_purge_insert_guard;
CREATE TRIGGER effect_purge_insert_guard BEFORE INSERT ON effects
WHEN (NEW.payload_object_ref IS NOT NULL AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.payload_object_ref AND s.payload_state='PURGED')
  OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.payload_object_ref AND b.status IN ('ACTIVE','PARTIAL'))))
 OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(NEW.target_ref,1,7)='object:' THEN substr(NEW.target_ref,8) ELSE NEW.target_ref END AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=CASE WHEN substr(NEW.target_ref,1,7)='object:' THEN substr(NEW.target_ref,8) ELSE NEW.target_ref END AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

DROP TRIGGER approval_purge_insert_guard;
CREATE TRIGGER approval_purge_insert_guard BEFORE INSERT ON approval_decisions
WHEN EXISTS (SELECT 1 FROM objects o JOIN object_states s USING(object_id)
  WHERE o.object_id=CASE WHEN substr(NEW.target_ref,1,7)='object:' THEN substr(NEW.target_ref,8) ELSE NEW.target_ref END AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM objects o JOIN purge_barrier_refs r USING(object_id) JOIN purge_barriers b USING(barrier_id)
  WHERE o.object_id=CASE WHEN substr(NEW.target_ref,1,7)='object:' THEN substr(NEW.target_ref,8) ELSE NEW.target_ref END AND b.status IN ('ACTIVE','PARTIAL'))
 OR EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n JOIN objects o
  ON o.object_id=CASE WHEN substr(n.value,1,7)='object:' THEN substr(n.value,8) ELSE n.value END
  JOIN object_states s USING(object_id) WHERE s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n JOIN objects o
  ON o.object_id=CASE WHEN substr(n.value,1,7)='object:' THEN substr(n.value,8) ELSE n.value END
  JOIN purge_barrier_refs r USING(object_id) JOIN purge_barriers b USING(barrier_id) WHERE b.status IN ('ACTIVE','PARTIAL'))
 OR EXISTS (SELECT 1 FROM objects o JOIN object_states s ON o.object_id=CASE WHEN substr(NEW.request_ref,1,7)='object:' THEN substr(NEW.request_ref,8) ELSE NEW.request_ref END AND s.object_id=o.object_id WHERE s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM objects o JOIN purge_barrier_refs r USING(object_id) JOIN purge_barriers b USING(barrier_id)
  WHERE o.object_id=CASE WHEN substr(NEW.request_ref,1,7)='object:' THEN substr(NEW.request_ref,8) ELSE NEW.request_ref END AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

DROP TRIGGER route_decision_purge_insert_guard;
CREATE TRIGGER route_decision_purge_insert_guard BEFORE INSERT ON route_decisions
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.decision_object_id AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.decision_object_id AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

CREATE TRIGGER subtask_attempt_route_insert_guard BEFORE INSERT ON subtask_attempts
WHEN NEW.route_decision_ref IS NOT NULL AND (
 EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.route_decision_ref AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.route_decision_ref AND b.status IN ('ACTIVE','PARTIAL')))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER subtask_attempt_route_update_guard BEFORE UPDATE OF route_decision_ref ON subtask_attempts
WHEN NEW.route_decision_ref IS NOT NULL AND (
 EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.route_decision_ref AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.route_decision_ref AND b.status IN ('ACTIVE','PARTIAL')))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

DROP TRIGGER run_manifest_purge_insert_guard;
CREATE TRIGGER run_manifest_purge_insert_guard BEFORE INSERT ON run_manifest_inputs
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id IN (NEW.manifest_object_id,NEW.input_object_id) AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id)
   WHERE r.object_id IN (NEW.manifest_object_id,NEW.input_object_id) AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

-- T1/Human replay binds the caller's original governed inputs independently
-- from the public result projection, which Purge may redact.
CREATE TABLE verification_request_bindings (
    verification_id TEXT PRIMARY KEY REFERENCES verification_results(verification_id),
    input_commitment TEXT NOT NULL CHECK(length(input_commitment)=64),
    created_at TEXT NOT NULL
);
CREATE TRIGGER verification_request_bindings_no_update BEFORE UPDATE ON verification_request_bindings
BEGIN SELECT RAISE(ABORT,'VERIFICATION_REQUEST_BINDING_IMMUTABLE'); END;
CREATE TRIGGER verification_request_bindings_no_delete BEFORE DELETE ON verification_request_bindings
BEGIN SELECT RAISE(ABORT,'VERIFICATION_REQUEST_BINDING_IMMUTABLE'); END;

DROP TRIGGER classification_assertions_no_update;
CREATE TRIGGER classification_assertions_no_update BEFORE UPDATE ON classification_assertions
WHEN NOT (
  nexus_purge_redaction()=1 AND NEW.assertion_id=OLD.assertion_id
  AND NEW.subject_type=OLD.subject_type AND NEW.sensitivity_level=OLD.sensitivity_level
  AND NEW.handling_tags_json=OLD.handling_tags_json AND NEW.policy_version=OLD.policy_version
  AND NEW.actor_id=OLD.actor_id AND NEW.supersedes IS OLD.supersedes
  AND ((NEW.subject_ref='REDACTED_PURGED' AND EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=OLD.subject_ref AND s.payload_state='PURGED')
      AND NEW.reason='[redacted by purge]')
    OR (NEW.subject_ref=OLD.subject_ref AND NEW.reason='[redacted by purge]'
      AND OLD.reason IS NOT NULL AND EXISTS
        (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND instr(OLD.reason,s.object_id)>0)))
)
BEGIN SELECT RAISE(ABORT,'CLASSIFICATION_ASSERTION_IMMUTABLE'); END;

DROP TRIGGER memory_candidate_identity_immutable;
CREATE TRIGGER memory_candidate_identity_immutable BEFORE UPDATE ON memory_candidates
WHEN NOT (
  NEW.candidate_id=OLD.candidate_id AND NEW.owner=OLD.owner
  AND NEW.classification_assertion_ref=OLD.classification_assertion_ref
  AND NEW.verification_ref=OLD.verification_ref AND NEW.truth_state=OLD.truth_state
  AND NEW.created_at=OLD.created_at AND NEW.expires_at IS OLD.expires_at
  AND ((NEW.claim_ref IS OLD.claim_ref AND NEW.metadata_json=OLD.metadata_json
       AND (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('ADMITTED','QUARANTINED','REJECTED','PURGED')) OR (OLD.status IN ('ADMITTED','QUARANTINED','REJECTED') AND NEW.status='PURGED')))
    OR (nexus_purge_redaction()=1 AND NEW.status='PURGED'
       AND (NEW.claim_ref IS OLD.claim_ref OR (NEW.claim_ref IS NULL AND OLD.claim_ref IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.claim_ref AND s.payload_state='PURGED')))
       AND NEW.metadata_json<>OLD.metadata_json
       AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.claim_ref AND s.payload_state='PURGED')
         OR EXISTS (SELECT 1 FROM memory_candidate_evidence e JOIN object_states s ON s.object_id=e.evidence_object_id WHERE e.candidate_id=OLD.candidate_id AND s.payload_state='PURGED')
         OR EXISTS (SELECT 1 FROM json_tree(OLD.metadata_json) o JOIN object_states s ON s.object_id=o.value WHERE s.payload_state='PURGED'))
       AND NOT EXISTS (SELECT 1 FROM json_tree(NEW.metadata_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')))
)
BEGIN SELECT RAISE(ABORT,'INVALID_MEMORY_CANDIDATE_TRANSITION'); END;

DROP TRIGGER approval_decisions_no_update;
CREATE TRIGGER approval_decisions_no_update BEFORE UPDATE ON approval_decisions
WHEN NOT (
  nexus_purge_redaction()=1 AND NEW.approval_id=OLD.approval_id
  AND NEW.approver_principal_id=OLD.approver_principal_id AND NEW.target_type=OLD.target_type
  AND NEW.decision=OLD.decision AND NEW.policy_version=OLD.policy_version
  AND NEW.issued_at=OLD.issued_at AND NEW.expires_at IS OLD.expires_at
  AND NEW.target_ref IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN 'REDACTED_PURGED' ELSE OLD.target_ref END)
  AND NEW.effect_id IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL ELSE OLD.effect_id END)
  AND NEW.payload_integrity_hash IS (CASE WHEN NEW.effect_id IS NULL AND OLD.effect_id IS NOT NULL THEN NULL ELSE OLD.payload_integrity_hash END)
  AND json_valid(NEW.approved_scope_json)
  AND NOT EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n WHERE NOT EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o WHERE o.value=n.value))
  AND NOT EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o WHERE NOT EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n WHERE n.value=o.value)
      AND NOT EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND (s.object_id=o.value OR 'object:'||s.object_id=o.value)))
  AND NOT EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n JOIN object_states s ON (s.object_id=n.value OR 'object:'||s.object_id=n.value) WHERE s.payload_state='PURGED')
  AND NEW.reason IS (CASE WHEN OLD.effect_id IS NOT NULL AND EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL
      WHEN OLD.reason IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND instr(OLD.reason,s.object_id)>0) THEN NULL ELSE OLD.reason END)
  AND NEW.request_ref IS (CASE WHEN OLD.effect_id IS NOT NULL AND EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL
      WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND (OLD.request_ref=s.object_id OR OLD.request_ref='object:'||s.object_id)) THEN NULL ELSE OLD.request_ref END)
  AND (NEW.target_ref IS NOT OLD.target_ref OR NEW.effect_id IS NOT OLD.effect_id OR NEW.payload_integrity_hash IS NOT OLD.payload_integrity_hash
       OR NEW.approved_scope_json IS NOT OLD.approved_scope_json OR NEW.reason IS NOT OLD.reason OR NEW.request_ref IS NOT OLD.request_ref)
  AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')
    OR EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o JOIN object_states s ON (s.object_id=o.value OR 'object:'||s.object_id=o.value) WHERE s.payload_state='PURGED')
    OR EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND ((OLD.reason IS NOT NULL AND instr(OLD.reason,s.object_id)>0) OR OLD.request_ref=s.object_id OR OLD.request_ref='object:'||s.object_id))
    OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED'))
)
BEGIN SELECT RAISE(ABORT,'APPROVAL_DECISION_IMMUTABLE'); END;
