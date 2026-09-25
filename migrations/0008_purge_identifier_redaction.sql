-- Purge may remove payload-derived identifiers while retaining immutable audit facts.
DROP TRIGGER approval_decisions_no_update;
CREATE TRIGGER approval_decisions_no_update
BEFORE UPDATE ON approval_decisions
WHEN NOT (
    NEW.approval_id=OLD.approval_id
    AND NEW.approver_principal_id=OLD.approver_principal_id
    AND NEW.target_type=OLD.target_type
    AND NEW.target_ref='REDACTED_PURGED'
    AND NEW.effect_id IS OLD.effect_id
    AND NEW.payload_integrity_hash IS NULL
    AND NEW.decision=OLD.decision
    AND NEW.approved_scope_json='[]'
    AND NEW.policy_version=OLD.policy_version
    AND NEW.issued_at=OLD.issued_at
    AND NEW.expires_at IS OLD.expires_at
    AND NEW.reason IS NULL
    AND NEW.request_ref IS NULL
    AND OLD.target_ref<>'REDACTED_PURGED'
    AND EXISTS (
        SELECT 1 FROM effects e JOIN object_states s ON s.object_id=e.payload_object_ref
        WHERE e.effect_id=OLD.effect_id AND s.payload_state='PURGED'
    )
)
BEGIN
    SELECT RAISE(ABORT,'APPROVAL_DECISION_IMMUTABLE');
END;

DROP TRIGGER effects_identity_immutable;
CREATE TRIGGER effects_identity_immutable BEFORE UPDATE ON effects
WHEN NOT (
    -- Ordinary monotonic Effect transition: preserve the v0.1 rules exactly.
    (NEW.effect_id=OLD.effect_id AND NEW.run_id=OLD.run_id AND NEW.tool_id=OLD.tool_id
     AND NEW.tool_descriptor_version=OLD.tool_descriptor_version AND NEW.action_type=OLD.action_type
     AND NEW.target_ref=OLD.target_ref AND NEW.payload_integrity_hash=OLD.payload_integrity_hash
     AND NEW.payload_object_ref=OLD.payload_object_ref AND NEW.idempotency_key=OLD.idempotency_key
     AND NEW.grant_id=OLD.grant_id AND NEW.approval_ref IS OLD.approval_ref
     AND NEW.budget_reservation_ref=OLD.budget_reservation_ref AND NEW.created_at=OLD.created_at
     AND (OLD.effect_outcome NOT IN ('COMMITTED','NOT_COMMITTED') OR NEW.effect_outcome=OLD.effect_outcome)
     AND ((OLD.execution_state='DECLARED' AND NEW.execution_state IN ('PREPARED','CANCELLED'))
       OR (OLD.execution_state='PREPARED' AND NEW.execution_state IN ('AUTHORIZED','CANCELLED'))
       OR (OLD.execution_state='AUTHORIZED' AND NEW.execution_state IN ('COMMITTING','CANCELLED'))
       OR (OLD.execution_state='COMMITTING' AND NEW.execution_state='FINISHED')
       OR (OLD.execution_state='FINISHED' AND NEW.execution_state='FINISHED')))
    OR
    -- The only non-transition mutation is payload-derived identifier erasure after Purge.
    (NEW.effect_id=OLD.effect_id AND NEW.run_id=OLD.run_id AND NEW.tool_id=OLD.tool_id
     AND NEW.tool_descriptor_version=OLD.tool_descriptor_version AND NEW.action_type=OLD.action_type
     AND NEW.target_ref='REDACTED_PURGED' AND NEW.payload_integrity_hash=replace(hex(zeroblob(32)),'00','00')
     AND NEW.payload_object_ref=OLD.payload_object_ref
     AND NEW.idempotency_key='REDACTED_PURGED:'||OLD.effect_id AND NEW.grant_id=OLD.grant_id
     AND NEW.approval_ref IS OLD.approval_ref AND NEW.budget_reservation_ref=OLD.budget_reservation_ref
     AND NEW.execution_state=OLD.execution_state AND NEW.effect_outcome=OLD.effect_outcome
     AND NEW.reconciliation_status=OLD.reconciliation_status AND NEW.external_receipt_ref IS NULL
     AND NEW.created_at=OLD.created_at AND NEW.updated_at=OLD.updated_at
     AND json_extract(NEW.effect_json,'$.effect_id')=OLD.effect_id
     AND json_extract(NEW.effect_json,'$.run_id')=OLD.run_id
     AND json_extract(NEW.effect_json,'$.idempotency_key')=NEW.idempotency_key
     AND json_extract(NEW.effect_json,'$.execution_state')=OLD.execution_state
     AND json_extract(NEW.effect_json,'$.effect_outcome')=OLD.effect_outcome
     AND json_extract(NEW.effect_json,'$.reconciliation_status')=OLD.reconciliation_status
     AND json_extract(NEW.effect_json,'$.target_ref')='REDACTED_PURGED'
     AND json_extract(NEW.effect_json,'$.payload_integrity_hash')='0000000000000000000000000000000000000000000000000000000000000000'
     AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;

DROP TRIGGER trace_events_no_update;
CREATE TRIGGER trace_events_no_update
BEFORE UPDATE ON trace_events
WHEN NOT (
    NEW.event_id=OLD.event_id AND NEW.run_id=OLD.run_id AND NEW.seq_no=OLD.seq_no
    AND NEW.event_type=OLD.event_type AND NEW.occurred_at=OLD.occurred_at AND NEW.actor_id=OLD.actor_id
    AND json_extract(NEW.event_json,'$.event_id')=json_extract(OLD.event_json,'$.event_id')
    AND json_extract(NEW.event_json,'$.run_id')=json_extract(OLD.event_json,'$.run_id')
    AND json_extract(NEW.event_json,'$.seq_no')=json_extract(OLD.event_json,'$.seq_no')
    AND json_extract(NEW.event_json,'$.event_type')=json_extract(OLD.event_json,'$.event_type')
    AND json_extract(NEW.event_json,'$.occurred_at')=json_extract(OLD.event_json,'$.occurred_at')
    AND json_extract(NEW.event_json,'$.actor_id')=json_extract(OLD.event_json,'$.actor_id')
    AND json_type(NEW.event_json,'$.typed_metadata')='object'
    AND json_extract(NEW.event_json,'$.typed_metadata')=json('{}')
    AND (
        EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state='PURGED')
        OR EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value JOIN object_states s ON s.object_id=e.payload_object_ref WHERE s.payload_state='PURGED')
    )
    AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state<>'PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') n WHERE n.value=r.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value JOIN object_states s ON s.object_id=e.payload_object_ref WHERE s.payload_state<>'PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') n WHERE n.value=r.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') n WHERE n.value<>'REDACTED_PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') o WHERE o.value=n.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') n WHERE n.value<>'REDACTED_PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') o WHERE o.value=n.value))
    AND (EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') WHERE value='REDACTED_PURGED') OR NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state='PURGED'))
    AND (EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') WHERE value='REDACTED_PURGED') OR NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value JOIN object_states s ON s.object_id=e.payload_object_ref WHERE s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'TRACE_EVENT_IMMUTABLE'); END;

DROP TRIGGER purge_refs_no_update;
CREATE TRIGGER purge_refs_no_update BEFORE UPDATE ON purge_execution_refs
WHEN NOT (NEW.record_id=OLD.record_id AND NEW.object_id=OLD.object_id
  AND NEW.payload_uri IS NULL AND NEW.integrity_hash IS NULL
  AND OLD.payload_uri IS NOT NULL AND OLD.integrity_hash IS NOT NULL
  AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.object_id AND s.payload_state='PURGED'))
BEGIN SELECT RAISE(ABORT,'PURGE_EXECUTION_REF_IMMUTABLE'); END;
