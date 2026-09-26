-- One-way purge transforms for ordinary data-plane projections. The purge
-- service enters the narrow SQL capability only after ObjectState is PURGED.

-- Nullable projection links are required where replacing a real object ID
-- with a sentinel would collide with keys or create false provenance.
DROP TRIGGER memory_candidate_identity_immutable;
DROP TRIGGER memory_candidates_no_delete;
CREATE TABLE memory_candidates_v17 (
    candidate_id TEXT PRIMARY KEY,
    claim_ref TEXT REFERENCES objects(object_id),
    owner TEXT NOT NULL,
    classification_assertion_ref TEXT NOT NULL REFERENCES classification_assertions(assertion_id),
    verification_ref TEXT NOT NULL REFERENCES verification_results(verification_id),
    truth_state TEXT NOT NULL CHECK(truth_state IN ('VERIFIED','SUPPORTED','INFERRED','HYPOTHESIS','UNKNOWN')),
    status TEXT NOT NULL CHECK(status IN ('PENDING','ADMITTED','QUARANTINED','REJECTED','PURGED')),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL,
    expires_at TEXT
);
INSERT INTO memory_candidates_v17 SELECT * FROM memory_candidates;
DROP TABLE memory_candidates;
ALTER TABLE memory_candidates_v17 RENAME TO memory_candidates;

CREATE TABLE effects_v17 (
    effect_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    tool_id TEXT NOT NULL,
    tool_descriptor_version TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    payload_integrity_hash TEXT NOT NULL CHECK(length(payload_integrity_hash)=64),
    payload_object_ref TEXT REFERENCES objects(object_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    grant_id TEXT NOT NULL REFERENCES delegation_grants(grant_id),
    approval_ref TEXT REFERENCES approval_decisions(approval_id),
    budget_reservation_ref TEXT NOT NULL REFERENCES budget_reservations(reservation_id),
    execution_state TEXT NOT NULL CHECK(execution_state IN ('DECLARED','PREPARED','AUTHORIZED','COMMITTING','FINISHED','CANCELLED')),
    effect_outcome TEXT NOT NULL CHECK(effect_outcome IN ('UNDETERMINED','COMMITTED','NOT_COMMITTED','UNKNOWN')),
    reconciliation_status TEXT NOT NULL CHECK(reconciliation_status IN ('NOT_REQUIRED','PENDING','RETRYING','EXHAUSTED','HUMAN_REQUIRED','BLOCK_AND_ALERT','RESOLVED')),
    external_receipt_ref TEXT,
    effect_json TEXT NOT NULL CHECK(json_valid(effect_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((effect_outcome='UNDETERMINED' AND execution_state IN ('DECLARED','PREPARED','AUTHORIZED') AND reconciliation_status='NOT_REQUIRED')
        OR (effect_outcome='UNKNOWN' AND execution_state IN ('COMMITTING','FINISHED') AND reconciliation_status IN ('PENDING','RETRYING','EXHAUSTED','HUMAN_REQUIRED','BLOCK_AND_ALERT'))
        OR (effect_outcome='COMMITTED' AND execution_state='FINISHED' AND reconciliation_status IN ('NOT_REQUIRED','RESOLVED'))
        OR (effect_outcome='NOT_COMMITTED' AND execution_state IN ('FINISHED','CANCELLED') AND reconciliation_status IN ('NOT_REQUIRED','RESOLVED'))),
    CHECK(json_extract(effect_json,'$.effect_id')=effect_id
      AND json_extract(effect_json,'$.run_id')=run_id
      AND json_extract(effect_json,'$.idempotency_key')=idempotency_key
      AND json_extract(effect_json,'$.execution_state')=execution_state
      AND json_extract(effect_json,'$.effect_outcome')=effect_outcome
      AND json_extract(effect_json,'$.reconciliation_status')=reconciliation_status),
    CHECK (execution_state<>'CANCELLED' OR effect_outcome='NOT_COMMITTED')
);
INSERT INTO effects_v17 SELECT * FROM effects;
DROP TABLE effects;
ALTER TABLE effects_v17 RENAME TO effects;
CREATE INDEX effects_outcome_idx ON effects(effect_outcome,reconciliation_status);

DROP TRIGGER verification_results_no_update;
CREATE TRIGGER verification_results_no_update BEFORE UPDATE ON verification_results
WHEN NOT (
    nexus_purge_redaction()=1
    AND NEW.verification_id=OLD.verification_id AND NEW.verdict=OLD.verdict
    AND NEW.verifier_kind=OLD.verifier_kind AND NEW.independence_json=OLD.independence_json
    AND NEW.attester_principal_id IS OLD.attester_principal_id AND NEW.approval_ref IS OLD.approval_ref
    AND NEW.run_id=OLD.run_id AND NEW.created_at=OLD.created_at
    AND (NEW.target_ref=OLD.target_ref OR (NEW.target_ref='REDACTED_PURGED' AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')))
    AND json_valid(NEW.evidence_used_json) AND json_valid(NEW.result_json)
    AND (NEW.target_ref<>OLD.target_ref OR NEW.evidence_used_json<>OLD.evidence_used_json OR NEW.result_json<>OLD.result_json)
    AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM json_tree(OLD.evidence_used_json) o JOIN object_states s ON s.object_id=o.value WHERE s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM json_tree(OLD.result_json) o JOIN object_states s ON s.object_id=o.value WHERE s.payload_state='PURGED'))
    AND NOT EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND s.object_id=NEW.target_ref)
    AND NOT EXISTS (SELECT 1 FROM json_tree(NEW.evidence_used_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')
    AND NOT EXISTS (SELECT 1 FROM json_tree(NEW.result_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')
)
BEGIN SELECT RAISE(ABORT,'VERIFICATION_RESULT_IMMUTABLE'); END;

DROP TRIGGER run_manifest_inputs_no_delete;
CREATE TRIGGER run_manifest_inputs_no_delete BEFORE DELETE ON run_manifest_inputs
WHEN NOT (nexus_purge_redaction()=1 AND
  (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.input_object_id AND s.payload_state='PURGED')
   OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.manifest_object_id AND s.payload_state='PURGED')))
BEGIN SELECT RAISE(ABORT,'RUN_MANIFEST_INPUT_IMMUTABLE'); END;

DROP TRIGGER classification_assertions_no_update;
CREATE TRIGGER classification_assertions_no_update BEFORE UPDATE ON classification_assertions
WHEN NOT (
    nexus_purge_redaction()=1 AND OLD.subject_type='OBJECT'
    AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.subject_ref AND s.payload_state='PURGED')
    AND NEW.assertion_id=OLD.assertion_id AND NEW.subject_type=OLD.subject_type
    AND NEW.subject_ref='REDACTED_PURGED' AND NEW.sensitivity_level=OLD.sensitivity_level
    AND NEW.handling_tags_json=OLD.handling_tags_json AND NEW.policy_version=OLD.policy_version
    AND NEW.actor_id=OLD.actor_id AND NEW.supersedes IS OLD.supersedes
    AND NEW.reason='[redacted by purge]'
)
BEGIN SELECT RAISE(ABORT,'CLASSIFICATION_ASSERTION_IMMUTABLE'); END;

CREATE TRIGGER memory_candidate_identity_immutable BEFORE UPDATE ON memory_candidates
WHEN NOT (
    NEW.candidate_id=OLD.candidate_id AND NEW.owner=OLD.owner
    AND NEW.classification_assertion_ref=OLD.classification_assertion_ref
    AND NEW.verification_ref=OLD.verification_ref AND NEW.truth_state=OLD.truth_state
    AND NEW.created_at=OLD.created_at AND NEW.expires_at IS OLD.expires_at
    AND (
      (NEW.claim_ref IS OLD.claim_ref AND NEW.metadata_json=OLD.metadata_json
       AND (OLD.status=NEW.status OR (OLD.status='PENDING' AND NEW.status IN ('ADMITTED','QUARANTINED','REJECTED','PURGED')) OR (OLD.status IN ('ADMITTED','QUARANTINED','REJECTED') AND NEW.status='PURGED')))
      OR (nexus_purge_redaction()=1 AND OLD.status='PURGED' AND NEW.status='PURGED'
        AND (NEW.claim_ref IS OLD.claim_ref OR (NEW.claim_ref IS NULL AND OLD.claim_ref IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.claim_ref AND s.payload_state='PURGED')))
        AND NEW.metadata_json<>OLD.metadata_json
        AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.claim_ref AND s.payload_state='PURGED')
          OR EXISTS (SELECT 1 FROM memory_candidate_evidence e JOIN object_states s ON s.object_id=e.evidence_object_id WHERE e.candidate_id=OLD.candidate_id AND s.payload_state='PURGED'))
        AND NOT EXISTS (SELECT 1 FROM json_tree(NEW.metadata_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED'))
    )
)
BEGIN SELECT RAISE(ABORT,'INVALID_MEMORY_CANDIDATE_TRANSITION'); END;
CREATE TRIGGER memory_candidates_no_delete BEFORE DELETE ON memory_candidates BEGIN SELECT RAISE(ABORT,'MEMORY_CANDIDATE_IMMUTABLE'); END;

DROP TRIGGER memory_evidence_no_delete;
CREATE TRIGGER memory_evidence_no_delete BEFORE DELETE ON memory_candidate_evidence
WHEN NOT (nexus_purge_redaction()=1 AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.evidence_object_id AND s.payload_state='PURGED'))
BEGIN SELECT RAISE(ABORT,'MEMORY_EVIDENCE_IMMUTABLE'); END;

DROP TRIGGER purge_refs_no_update;
CREATE TRIGGER purge_refs_no_update BEFORE UPDATE ON purge_execution_refs
WHEN NOT (nexus_purge_redaction()=1 AND NEW.record_id=OLD.record_id AND NEW.object_id=OLD.object_id
  AND NEW.payload_uri IS NULL AND NEW.integrity_hash IS NULL
  AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.object_id AND s.payload_state='PURGED'))
BEGIN SELECT RAISE(ABORT,'PURGE_EXECUTION_REF_IMMUTABLE'); END;

DROP TRIGGER object_relations_no_update;
CREATE TRIGGER object_relations_no_update BEFORE UPDATE ON object_relations BEGIN SELECT RAISE(ABORT,'OBJECT_RELATION_IMMUTABLE'); END;
CREATE TRIGGER object_relations_no_delete BEFORE DELETE ON object_relations
WHEN NOT (nexus_purge_redaction()=1 AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.from_id AND s.payload_state='PURGED') OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.to_id AND s.payload_state='PURGED')))
BEGIN SELECT RAISE(ABORT,'OBJECT_RELATION_IMMUTABLE'); END;

CREATE TRIGGER logical_refs_no_delete BEFORE DELETE ON logical_refs
WHEN NOT (nexus_purge_redaction()=1 AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.current_object_id AND s.payload_state='PURGED'))
BEGIN SELECT RAISE(ABORT,'LOGICAL_REF_IMMUTABLE'); END;

DROP TRIGGER route_decisions_no_delete;
CREATE TRIGGER route_decisions_no_delete BEFORE DELETE ON route_decisions
WHEN NOT (nexus_purge_redaction()=1 AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.decision_object_id AND s.payload_state='PURGED'))
BEGIN SELECT RAISE(ABORT,'ROUTE_DECISION_IMMUTABLE'); END;

DROP TRIGGER runs_identity_immutable;
CREATE TRIGGER runs_identity_immutable BEFORE UPDATE ON runs
WHEN NOT (
    (NEW.run_id=OLD.run_id AND NEW.task_id=OLD.task_id AND NEW.subtask_id IS OLD.subtask_id
     AND NEW.parent_run_id IS OLD.parent_run_id AND NEW.executor_kind=OLD.executor_kind
     AND NEW.grant_id=OLD.grant_id AND NEW.budget_reservation_ref IS OLD.budget_reservation_ref
     AND NEW.data_boundary_json=OLD.data_boundary_json AND NEW.classification_assertion_ref=OLD.classification_assertion_ref
     AND NEW.created_at=OLD.created_at
     AND (NEW.manifest_ref IS OLD.manifest_ref OR (OLD.status='CREATED' AND NEW.status='CREATED' AND OLD.manifest_ref IS NULL AND NEW.manifest_ref IS NOT NULL))
     AND ((OLD.status='CREATED' AND NEW.status IN ('CREATED','READY','CANCELLED'))
       OR (OLD.status='READY' AND NEW.status IN ('RUNNING','CANCELLED'))
       OR (OLD.status='RUNNING' AND NEW.status IN ('WAITING','VERIFYING','FAILED','CANCELLED'))
       OR (OLD.status='WAITING' AND NEW.status IN ('RUNNING','FAILED','CANCELLED'))
       OR (OLD.status='VERIFYING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED','WAITING'))))
    OR (nexus_purge_redaction()=1 AND OLD.status IN ('CREATED','SUCCEEDED','FAILED','CANCELLED')
      AND NEW.run_id=OLD.run_id AND NEW.task_id=OLD.task_id AND NEW.subtask_id IS OLD.subtask_id
      AND NEW.parent_run_id IS OLD.parent_run_id AND NEW.executor_kind=OLD.executor_kind AND NEW.status=OLD.status
      AND NEW.grant_id=OLD.grant_id AND NEW.manifest_ref='REDACTED_PURGED'
      AND NEW.budget_reservation_ref IS OLD.budget_reservation_ref AND NEW.data_boundary_json=OLD.data_boundary_json
      AND NEW.classification_assertion_ref=OLD.classification_assertion_ref AND NEW.created_at=OLD.created_at
      AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.manifest_ref AND s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'INVALID_RUN_TRANSITION'); END;

DROP TRIGGER subtask_attempts_identity_immutable;
CREATE TRIGGER subtask_attempts_identity_immutable BEFORE UPDATE ON subtask_attempts
WHEN NOT (
    (NEW.attempt_id=OLD.attempt_id AND NEW.task_id=OLD.task_id AND NEW.subtask_id=OLD.subtask_id
     AND NEW.attempt_no=OLD.attempt_no AND NEW.run_id=OLD.run_id AND NEW.route_decision_ref IS OLD.route_decision_ref
     AND NEW.requested_capability=OLD.requested_capability AND NEW.attempt_reason=OLD.attempt_reason
     AND NEW.predecessor_attempt_id IS OLD.predecessor_attempt_id AND NEW.command_id=OLD.command_id AND NEW.created_at=OLD.created_at
     AND (OLD.outcome=NEW.outcome OR (OLD.outcome='CREATED' AND NEW.outcome IN ('READY','RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='READY' AND NEW.outcome IN ('RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
       OR (OLD.outcome='RUNNING' AND NEW.outcome IN ('SUCCEEDED','FAILED','CANCELLED','INCONCLUSIVE'))))
    OR (nexus_purge_redaction()=1 AND NEW.attempt_id=OLD.attempt_id AND NEW.task_id=OLD.task_id
      AND NEW.subtask_id=OLD.subtask_id AND NEW.attempt_no=OLD.attempt_no AND NEW.run_id=OLD.run_id
      AND NEW.route_decision_ref IS NULL AND NEW.requested_capability=OLD.requested_capability
      AND NEW.attempt_reason=OLD.attempt_reason AND NEW.predecessor_attempt_id IS OLD.predecessor_attempt_id
      AND NEW.outcome=OLD.outcome AND NEW.command_id=OLD.command_id AND NEW.created_at=OLD.created_at
      AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.route_decision_ref AND s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'INVALID_SUBTASK_ATTEMPT_TRANSITION'); END;

CREATE TRIGGER effects_identity_immutable BEFORE UPDATE ON effects
WHEN NOT (
    (NEW.effect_id=OLD.effect_id AND NEW.run_id=OLD.run_id AND NEW.tool_id=OLD.tool_id
     AND NEW.tool_descriptor_version=OLD.tool_descriptor_version AND NEW.action_type=OLD.action_type
     AND NEW.target_ref=OLD.target_ref AND NEW.payload_integrity_hash=OLD.payload_integrity_hash
     AND NEW.payload_object_ref IS OLD.payload_object_ref AND NEW.idempotency_key=OLD.idempotency_key
     AND NEW.grant_id=OLD.grant_id AND NEW.approval_ref IS OLD.approval_ref
     AND NEW.budget_reservation_ref=OLD.budget_reservation_ref AND NEW.created_at=OLD.created_at
     AND (OLD.effect_outcome NOT IN ('COMMITTED','NOT_COMMITTED') OR NEW.effect_outcome=OLD.effect_outcome)
     AND ((OLD.execution_state='DECLARED' AND NEW.execution_state IN ('PREPARED','CANCELLED'))
       OR (OLD.execution_state='PREPARED' AND NEW.execution_state IN ('AUTHORIZED','CANCELLED'))
       OR (OLD.execution_state='AUTHORIZED' AND NEW.execution_state IN ('COMMITTING','CANCELLED'))
       OR (OLD.execution_state='COMMITTING' AND NEW.execution_state='FINISHED')
       OR (OLD.execution_state='FINISHED' AND NEW.execution_state='FINISHED')))
    OR (nexus_purge_redaction()=1 AND NEW.effect_id=OLD.effect_id AND NEW.run_id=OLD.run_id
      AND NEW.tool_id=OLD.tool_id AND NEW.tool_descriptor_version=OLD.tool_descriptor_version AND NEW.action_type=OLD.action_type
      AND NEW.grant_id=OLD.grant_id AND NEW.approval_ref IS OLD.approval_ref AND NEW.budget_reservation_ref=OLD.budget_reservation_ref
      AND NEW.execution_state=OLD.execution_state AND NEW.effect_outcome=OLD.effect_outcome
      AND NEW.reconciliation_status=OLD.reconciliation_status AND NEW.created_at=OLD.created_at AND NEW.updated_at=OLD.updated_at
      AND NEW.payload_object_ref IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED') THEN NULL ELSE OLD.payload_object_ref END)
      AND NEW.target_ref IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED') OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED') THEN 'REDACTED_PURGED' ELSE OLD.target_ref END)
      AND NEW.payload_integrity_hash IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED') THEN '0000000000000000000000000000000000000000000000000000000000000000' ELSE OLD.payload_integrity_hash END)
      AND NEW.idempotency_key IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED') THEN 'REDACTED_PURGED:'||OLD.effect_id ELSE OLD.idempotency_key END)
      AND NEW.external_receipt_ref IS NULL
      AND json_valid(NEW.effect_json)
      AND NOT EXISTS (SELECT 1 FROM json_tree(NEW.effect_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')
      AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
        OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')))
)
BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;
CREATE TRIGGER effects_no_delete BEFORE DELETE ON effects BEGIN SELECT RAISE(ABORT,'EFFECT_IMMUTABLE'); END;

DROP TRIGGER approval_decisions_no_update;
CREATE TRIGGER approval_decisions_no_update BEFORE UPDATE ON approval_decisions
WHEN NOT (
    nexus_purge_redaction()=1 AND NEW.approval_id=OLD.approval_id
    AND NEW.approver_principal_id=OLD.approver_principal_id AND NEW.target_type=OLD.target_type
    AND NEW.effect_id IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o JOIN object_states s ON (s.object_id=o.value OR 'object:'||s.object_id=o.value) WHERE s.payload_state='PURGED')
      THEN NULL ELSE OLD.effect_id END)
    AND NEW.target_ref IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED') OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN 'REDACTED_PURGED' ELSE OLD.target_ref END)
    AND NEW.payload_integrity_hash IS NULL AND NEW.decision=OLD.decision
    AND (NEW.approved_scope_json<>OLD.approved_scope_json OR NEW.target_ref='REDACTED_PURGED' OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED'))
    AND json_valid(NEW.approved_scope_json)
    AND NOT EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n JOIN object_states s ON (s.object_id=n.value OR 'object:'||s.object_id=n.value) WHERE s.payload_state='PURGED')
    AND NEW.policy_version=OLD.policy_version AND NEW.issued_at=OLD.issued_at AND NEW.expires_at IS OLD.expires_at
    AND NEW.reason IS NULL AND NEW.request_ref IS NULL
    AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.target_ref AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o JOIN object_states s ON (s.object_id=o.value OR 'object:'||s.object_id=o.value) WHERE s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED'))
)
BEGIN SELECT RAISE(ABORT,'APPROVAL_DECISION_IMMUTABLE'); END;

DROP TRIGGER delegation_grants_scope_immutable;
CREATE TRIGGER delegation_grants_scope_immutable BEFORE UPDATE ON delegation_grants
WHEN NOT (
    (NEW.grant_id=OLD.grant_id AND NEW.parent_grant_id IS OLD.parent_grant_id AND NEW.issued_by=OLD.issued_by
     AND NEW.granted_to=OLD.granted_to AND NEW.task_scope_json=OLD.task_scope_json AND NEW.resource_scope_json=OLD.resource_scope_json
     AND NEW.action_scope_json=OLD.action_scope_json AND NEW.audience_scope_json=OLD.audience_scope_json
     AND NEW.issued_at=OLD.issued_at AND NEW.expires_at=OLD.expires_at AND NEW.policy_version=OLD.policy_version
     AND NEW.credential_ref IS OLD.credential_ref
     AND ((OLD.status='PROPOSED' AND NEW.status IN ('ACTIVE','REVOKED','EXPIRED')) OR (OLD.status='ACTIVE' AND NEW.status IN ('REVOKED','EXPIRED'))))
    OR (nexus_purge_redaction()=1 AND NEW.grant_id=OLD.grant_id AND NEW.parent_grant_id IS OLD.parent_grant_id
      AND NEW.issued_by=OLD.issued_by AND NEW.granted_to=OLD.granted_to AND NEW.task_scope_json=OLD.task_scope_json
      AND NEW.action_scope_json=OLD.action_scope_json AND NEW.audience_scope_json=OLD.audience_scope_json
      AND NEW.issued_at=OLD.issued_at AND NEW.expires_at=OLD.expires_at AND NEW.status=OLD.status
      AND NEW.policy_version=OLD.policy_version AND NEW.credential_ref IS OLD.credential_ref
      AND json_valid(OLD.resource_scope_json) AND json_valid(NEW.resource_scope_json)
      AND NOT EXISTS (SELECT 1 FROM json_each(NEW.resource_scope_json) n WHERE NOT EXISTS (SELECT 1 FROM json_each(OLD.resource_scope_json) o WHERE o.value=n.value))
      AND EXISTS (SELECT 1 FROM json_each(OLD.resource_scope_json) o JOIN object_states s ON (s.object_id=o.value OR 'object:'||s.object_id=o.value) WHERE s.payload_state='PURGED'
        AND NOT EXISTS (SELECT 1 FROM json_each(NEW.resource_scope_json) n WHERE n.value=o.value))
      AND NOT EXISTS (SELECT 1 FROM json_each(OLD.resource_scope_json) o WHERE NOT EXISTS (SELECT 1 FROM json_each(NEW.resource_scope_json) n WHERE n.value=o.value)
        AND NOT EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND (s.object_id=o.value OR 'object:'||s.object_id=o.value)))
    )
)
BEGIN SELECT RAISE(ABORT,'INVALID_GRANT_TRANSITION'); END;

-- Defense in depth: no ordinary persistent reference can be created after an
-- object is held or tombstoned. Service-level checks provide domain errors;
-- these constraints close raw-SQL and recovery-path omissions.
CREATE TRIGGER object_relations_purge_guard BEFORE INSERT ON object_relations
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id IN (NEW.from_id,NEW.to_id) AND s.payload_state='PURGED')
  OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id IN (NEW.from_id,NEW.to_id) AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

CREATE TRIGGER logical_refs_purge_insert_guard BEFORE INSERT ON logical_refs
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.current_object_id AND s.payload_state='PURGED')
  OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.current_object_id AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER logical_refs_purge_update_guard BEFORE UPDATE OF current_object_id ON logical_refs
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.current_object_id AND s.payload_state='PURGED')
  OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.current_object_id AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

CREATE TRIGGER classification_purge_insert_guard BEFORE INSERT ON classification_assertions
WHEN NEW.subject_type='OBJECT' AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.subject_ref AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.subject_ref AND b.status IN ('ACTIVE','PARTIAL')))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER verification_purge_insert_guard BEFORE INSERT ON verification_results
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.target_ref AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM json_tree(NEW.evidence_used_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE (r.object_id=NEW.target_ref OR r.object_id IN (SELECT value FROM json_tree(NEW.evidence_used_json))) AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER memory_candidate_purge_insert_guard BEFORE INSERT ON memory_candidates
WHEN (NEW.claim_ref IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.claim_ref AND s.payload_state='PURGED'))
 OR EXISTS (SELECT 1 FROM json_tree(NEW.metadata_json) n JOIN object_states s ON s.object_id=n.value WHERE s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER memory_evidence_purge_insert_guard BEFORE INSERT ON memory_candidate_evidence
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.evidence_object_id AND s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER route_decision_purge_insert_guard BEFORE INSERT ON route_decisions
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.decision_object_id AND s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER effect_purge_insert_guard BEFORE INSERT ON effects
WHEN (NEW.payload_object_ref IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.payload_object_ref AND s.payload_state='PURGED'))
 OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.target_ref AND s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER approval_purge_insert_guard BEFORE INSERT ON approval_decisions
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.target_ref AND s.payload_state='PURGED')
 OR EXISTS (SELECT 1 FROM json_each(NEW.approved_scope_json) n JOIN object_states s ON s.object_id=replace(n.value,'object:','') WHERE s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;
CREATE TRIGGER run_manifest_purge_insert_guard BEFORE INSERT ON run_manifest_inputs
WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id IN (NEW.manifest_object_id,NEW.input_object_id) AND s.payload_state='PURGED')
BEGIN SELECT RAISE(ABORT,'PURGED_OBJECT_REFERENCE_DENIED'); END;

-- Forced restore recovery is infrastructure activity, not a user Task/Grant.
CREATE TABLE recovery_sessions (
    session_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPEN','COMPLETED')),
    completed_at TEXT
);
CREATE TRIGGER recovery_sessions_identity_immutable BEFORE UPDATE ON recovery_sessions
WHEN NEW.session_id<>OLD.session_id OR NEW.opened_at<>OLD.opened_at OR NEW.source_mode<>OLD.source_mode
 OR NOT (OLD.status='OPEN' AND NEW.status='COMPLETED' AND NEW.completed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'RECOVERY_SESSION_IMMUTABLE'); END;
CREATE TRIGGER recovery_sessions_no_delete BEFORE DELETE ON recovery_sessions
BEGIN SELECT RAISE(ABORT,'RECOVERY_SESSION_IMMUTABLE'); END;

-- Minimal ownership provenance survives identifier erasure for authorized
-- inspection without retaining an object/Effect locator.
CREATE TABLE purge_redacted_approval_owners (
    approval_id TEXT PRIMARY KEY REFERENCES approval_decisions(approval_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    recorded_at TEXT NOT NULL
);
CREATE TRIGGER purge_redacted_approval_owners_no_update BEFORE UPDATE ON purge_redacted_approval_owners
BEGIN SELECT RAISE(ABORT,'PURGE_APPROVAL_OWNER_IMMUTABLE'); END;
CREATE TRIGGER purge_redacted_approval_owners_no_delete BEFORE DELETE ON purge_redacted_approval_owners
BEGIN SELECT RAISE(ABORT,'PURGE_APPROVAL_OWNER_IMMUTABLE'); END;

-- Preserve only the fact that an attempt's route was purged, without retaining
-- its object locator, so Inspect can render a stable redacted projection.
CREATE TABLE purge_redacted_attempt_routes (
    attempt_id TEXT PRIMARY KEY REFERENCES subtask_attempts(attempt_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    recorded_at TEXT NOT NULL
);
CREATE TRIGGER purge_redacted_attempt_routes_no_update BEFORE UPDATE ON purge_redacted_attempt_routes
BEGIN SELECT RAISE(ABORT,'PURGE_ATTEMPT_ROUTE_IMMUTABLE'); END;
CREATE TRIGGER purge_redacted_attempt_routes_no_delete BEFORE DELETE ON purge_redacted_attempt_routes
BEGIN SELECT RAISE(ABORT,'PURGE_ATTEMPT_ROUTE_IMMUTABLE'); END;

-- A recovery restore runs this migration with FK enforcement temporarily off
-- only for the two table rebuilds; this transaction guard makes violations a
-- migration failure rather than silently accepting a broken graph.
CREATE TEMP TABLE purge_v17_fk_guard(value INTEGER CHECK(value=0));
INSERT INTO purge_v17_fk_guard(value) SELECT 1 FROM pragma_foreign_key_check;
DROP TABLE purge_v17_fk_guard;

DROP TRIGGER command_ledger_no_update;
CREATE TRIGGER command_ledger_no_update BEFORE UPDATE ON command_ledger
WHEN NOT (
    NEW.command_id=OLD.command_id AND NEW.operation=OLD.operation
    AND NEW.request_hash=OLD.request_hash AND NEW.status=OLD.status
    AND NEW.created_at=OLD.created_at AND NEW.result_commitment=OLD.result_commitment
    AND OLD.result_state='LIVE' AND NEW.result_state='PURGED_REDACTED'
    AND nexus_purge_redaction()=1
    AND nexus_exact_purge_json(OLD.result_json,NEW.result_json)=1
)
BEGIN SELECT RAISE(ABORT,'COMMAND_LEDGER_IMMUTABLE'); END;
