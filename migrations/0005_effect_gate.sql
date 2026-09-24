CREATE TABLE effects (
    effect_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    tool_id TEXT NOT NULL,
    tool_descriptor_version TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    payload_integrity_hash TEXT NOT NULL CHECK(length(payload_integrity_hash)=64),
    payload_object_ref TEXT NOT NULL REFERENCES objects(object_id),
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

CREATE TABLE effect_relations (
    from_effect_id TEXT NOT NULL REFERENCES effects(effect_id),
    relation_type TEXT NOT NULL CHECK(relation_type='COMPENSATES'),
    to_effect_id TEXT NOT NULL REFERENCES effects(effect_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY(from_effect_id,relation_type,to_effect_id),
    CHECK(from_effect_id<>to_effect_id)
);

CREATE TABLE effect_reconciliation_attempts (
    attempt_id TEXT PRIMARY KEY,
    effect_id TEXT NOT NULL REFERENCES effects(effect_id),
    attempt_no INTEGER NOT NULL CHECK(attempt_no > 0),
    channel_id TEXT NOT NULL,
    observation TEXT NOT NULL CHECK(observation IN ('COMMITTED','NOT_COMMITTED','INCONCLUSIVE')),
    authoritative_evidence_ref TEXT,
    command_id TEXT NOT NULL UNIQUE REFERENCES command_ledger(command_id),
    created_at TEXT NOT NULL,
    UNIQUE(effect_id,attempt_no)
);

CREATE TRIGGER effects_identity_immutable BEFORE UPDATE ON effects
WHEN NEW.effect_id<>OLD.effect_id OR NEW.run_id<>OLD.run_id OR NEW.tool_id<>OLD.tool_id
 OR NEW.tool_descriptor_version<>OLD.tool_descriptor_version OR NEW.action_type<>OLD.action_type
 OR NEW.target_ref<>OLD.target_ref OR NEW.payload_integrity_hash<>OLD.payload_integrity_hash
 OR NEW.payload_object_ref<>OLD.payload_object_ref OR NEW.idempotency_key<>OLD.idempotency_key
 OR NEW.grant_id<>OLD.grant_id OR NEW.approval_ref IS NOT OLD.approval_ref
 OR NEW.budget_reservation_ref<>OLD.budget_reservation_ref OR NEW.created_at<>OLD.created_at
 OR (OLD.effect_outcome IN ('COMMITTED','NOT_COMMITTED') AND NEW.effect_outcome<>OLD.effect_outcome)
 OR NOT (
      (OLD.execution_state='DECLARED' AND NEW.execution_state IN ('PREPARED','CANCELLED'))
   OR (OLD.execution_state='PREPARED' AND NEW.execution_state IN ('AUTHORIZED','CANCELLED'))
   OR (OLD.execution_state='AUTHORIZED' AND NEW.execution_state IN ('COMMITTING','CANCELLED'))
   OR (OLD.execution_state='COMMITTING' AND NEW.execution_state='FINISHED')
   OR (OLD.execution_state='FINISHED' AND NEW.execution_state='FINISHED')
 )
BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;

CREATE TRIGGER effects_no_delete BEFORE DELETE ON effects BEGIN SELECT RAISE(ABORT,'EFFECT_IMMUTABLE'); END;
CREATE TRIGGER effect_relations_no_update BEFORE UPDATE ON effect_relations BEGIN SELECT RAISE(ABORT,'EFFECT_RELATION_IMMUTABLE'); END;
CREATE TRIGGER effect_relations_no_delete BEFORE DELETE ON effect_relations BEGIN SELECT RAISE(ABORT,'EFFECT_RELATION_IMMUTABLE'); END;
CREATE TRIGGER effect_reconciliation_no_update BEFORE UPDATE ON effect_reconciliation_attempts BEGIN SELECT RAISE(ABORT,'RECONCILIATION_ATTEMPT_IMMUTABLE'); END;
CREATE TRIGGER effect_reconciliation_no_delete BEFORE DELETE ON effect_reconciliation_attempts BEGIN SELECT RAISE(ABORT,'RECONCILIATION_ATTEMPT_IMMUTABLE'); END;

CREATE INDEX effects_outcome_idx ON effects(effect_outcome,reconciliation_status);
CREATE INDEX effect_reconciliation_attempts_effect_idx ON effect_reconciliation_attempts(effect_id,attempt_no);
