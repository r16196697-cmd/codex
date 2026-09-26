-- Remove external receipt references from already purge-redacted Effect JSON
-- projections. The only newly admitted update is a one-way key removal.

DROP TRIGGER effects_identity_immutable;
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
    AND NEW.tool_id=OLD.tool_id AND NEW.tool_descriptor_version=OLD.tool_descriptor_version
    AND NEW.action_type=OLD.action_type AND NEW.grant_id=OLD.grant_id
    AND NEW.approval_ref IS OLD.approval_ref AND NEW.budget_reservation_ref=OLD.budget_reservation_ref
    AND NEW.execution_state=OLD.execution_state AND NEW.effect_outcome=OLD.effect_outcome
    AND NEW.reconciliation_status=OLD.reconciliation_status AND NEW.created_at=OLD.created_at
    AND NEW.updated_at=OLD.updated_at
    AND NEW.payload_object_ref IS (CASE WHEN EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      THEN NULL ELSE OLD.payload_object_ref END)
    AND NEW.target_ref IS (CASE WHEN EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.target_ref,1,7)='object:' THEN substr(OLD.target_ref,8) ELSE OLD.target_ref END AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      THEN 'REDACTED_PURGED' ELSE OLD.target_ref END)
    AND NEW.payload_integrity_hash IS (CASE WHEN EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      THEN '0000000000000000000000000000000000000000000000000000000000000000' ELSE OLD.payload_integrity_hash END)
    AND NEW.idempotency_key IS (CASE WHEN EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      THEN 'REDACTED_PURGED:'||OLD.effect_id ELSE OLD.idempotency_key END)
    AND NEW.external_receipt_ref IS (CASE WHEN EXISTS
      (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.target_ref,1,7)='object:' THEN substr(OLD.target_ref,8) ELSE OLD.target_ref END AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      THEN NULL ELSE OLD.external_receipt_ref END)
    AND nexus_effect_purge_json(OLD.effect_json,NEW.effect_json,OLD.effect_id,OLD.payload_object_ref,OLD.target_ref)=1
    AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.payload_object_ref AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.target_ref,1,7)='object:' THEN substr(OLD.target_ref,8) ELSE OLD.target_ref END AND s.payload_state='PURGED')))
  OR (nexus_purge_redaction()=1 AND OLD.target_ref='REDACTED_PURGED'
    AND NEW.effect_id=OLD.effect_id AND NEW.run_id=OLD.run_id AND NEW.tool_id=OLD.tool_id
    AND NEW.tool_descriptor_version=OLD.tool_descriptor_version AND NEW.action_type=OLD.action_type
    AND NEW.target_ref=OLD.target_ref AND NEW.payload_integrity_hash=OLD.payload_integrity_hash
    AND NEW.payload_object_ref IS OLD.payload_object_ref AND NEW.idempotency_key=OLD.idempotency_key
    AND NEW.grant_id=OLD.grant_id AND NEW.approval_ref IS OLD.approval_ref
    AND NEW.budget_reservation_ref=OLD.budget_reservation_ref
    AND NEW.execution_state=OLD.execution_state AND NEW.effect_outcome=OLD.effect_outcome
    AND NEW.reconciliation_status=OLD.reconciliation_status AND NEW.created_at=OLD.created_at
    AND NEW.updated_at=OLD.updated_at AND NEW.external_receipt_ref IS NULL
    AND nexus_effect_receipt_cleanup_json(OLD.effect_json,NEW.effect_json)=1)
)
BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;

-- Existing v21 rows already identify the purge through their redacted target
-- projection; do not require the deleted source target to be reconstructed.
UPDATE effects SET external_receipt_ref=NULL,
  effect_json=json_remove(effect_json,'$.external_receipt_ref')
WHERE json_type(effect_json,'$.external_receipt_ref') IS NOT NULL
  AND (target_ref='REDACTED_PURGED'
    OR (payload_object_ref IS NULL
      AND payload_integrity_hash='0000000000000000000000000000000000000000000000000000000000000000'
      AND idempotency_key='REDACTED_PURGED:'||effect_id));

-- Effect outcome events must also detach from Effects whose governed target
-- was purged, not only Effects whose payload object was purged.
DROP TRIGGER trace_events_no_update;
CREATE TRIGGER trace_events_no_update BEFORE UPDATE ON trace_events
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
    AND NOT EXISTS (
        SELECT 1 FROM json_each(NEW.event_json,'$.typed_metadata') n
        WHERE n.key NOT IN ('execution_state','effect_outcome','reconciliation_status')
           OR CASE n.key
                WHEN 'execution_state' THEN n.value NOT IN ('DECLARED','PREPARED','AUTHORIZED','COMMITTING','FINISHED','CANCELLED')
                WHEN 'effect_outcome' THEN n.value NOT IN ('UNDETERMINED','COMMITTED','NOT_COMMITTED','UNKNOWN')
                WHEN 'reconciliation_status' THEN n.value NOT IN ('NOT_REQUIRED','PENDING','RETRYING','EXHAUSTED','HUMAN_REQUIRED','BLOCK_AND_ALERT','RESOLVED')
                ELSE 1
              END
           OR NOT EXISTS (
                SELECT 1 FROM json_each(OLD.event_json,'$.typed_metadata') o
                WHERE o.key=n.key AND json_quote(o.value)=json_quote(n.value)
           )
    )
    AND (
        EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state='PURGED')
        OR EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value
          WHERE e.target_ref='REDACTED_PURGED' OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=e.payload_object_ref AND s.payload_state='PURGED'))
    )
    AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state<>'PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') n WHERE n.value=r.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value
      WHERE e.target_ref<>'REDACTED_PURGED' AND NOT EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=e.payload_object_ref AND s.payload_state='PURGED')
        AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') n WHERE n.value=r.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') n WHERE n.value<>'REDACTED_PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') o WHERE o.value=n.value))
    AND NOT EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') n WHERE n.value<>'REDACTED_PURGED' AND NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') o WHERE o.value=n.value))
    AND (EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.object_refs') WHERE value='REDACTED_PURGED') OR NOT EXISTS (SELECT 1 FROM json_each(OLD.event_json,'$.object_refs') r JOIN object_states s ON s.object_id=r.value WHERE s.payload_state='PURGED'))
    AND (EXISTS (SELECT 1 FROM json_each(NEW.event_json,'$.effect_refs') WHERE value='REDACTED_PURGED') OR NOT EXISTS (
      SELECT 1 FROM json_each(OLD.event_json,'$.effect_refs') r JOIN effects e ON e.effect_id=r.value
      WHERE e.target_ref='REDACTED_PURGED' OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=e.payload_object_ref AND s.payload_state='PURGED')))
)
BEGIN SELECT RAISE(ABORT,'TRACE_EVENT_IMMUTABLE'); END;
