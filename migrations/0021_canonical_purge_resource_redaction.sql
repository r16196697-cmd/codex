-- Normalize canonical object:<id> resources in the same one-way purge path as
-- bare object IDs. Only actual ObjectState rows are governed references.

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
)
BEGIN SELECT RAISE(ABORT,'INVALID_EFFECT_TRANSITION'); END;

-- Reconcile canonical residue left by v20 before the upgraded runtime can
-- enter NORMAL. The migration runner supplies the exact tombstone set to the
-- narrowly scoped purge-redaction SQL functions for this transaction only.
UPDATE effects SET
  target_ref='REDACTED_PURGED',
  payload_object_ref=CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=effects.payload_object_ref AND s.payload_state='PURGED') THEN NULL ELSE payload_object_ref END,
  payload_integrity_hash=CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=effects.payload_object_ref AND s.payload_state='PURGED') THEN '0000000000000000000000000000000000000000000000000000000000000000' ELSE payload_integrity_hash END,
  idempotency_key=CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=effects.payload_object_ref AND s.payload_state='PURGED') THEN 'REDACTED_PURGED:'||effect_id ELSE idempotency_key END,
  external_receipt_ref=NULL,
  effect_json=nexus_redact_effect_json(effect_json,effect_id,payload_object_ref,target_ref)
WHERE EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(effects.target_ref,1,7)='object:' THEN substr(effects.target_ref,8) ELSE effects.target_ref END AND s.payload_state='PURGED')
   OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=effects.payload_object_ref AND s.payload_state='PURGED');

DROP TRIGGER approval_decisions_no_update;
CREATE TRIGGER approval_decisions_no_update BEFORE UPDATE ON approval_decisions
WHEN NOT (
  nexus_purge_redaction()=1 AND NEW.approval_id=OLD.approval_id
  AND NEW.approver_principal_id=OLD.approver_principal_id AND NEW.target_type=OLD.target_type
  AND NEW.decision=OLD.decision AND NEW.policy_version=OLD.policy_version
  AND NEW.issued_at=OLD.issued_at AND NEW.expires_at IS OLD.expires_at
  AND NEW.target_ref IS (CASE WHEN EXISTS (SELECT 1 FROM object_states s
       WHERE s.object_id=CASE WHEN substr(OLD.target_ref,1,7)='object:' THEN substr(OLD.target_ref,8) ELSE OLD.target_ref END AND s.payload_state='PURGED')
       OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED')
       THEN 'REDACTED_PURGED' ELSE OLD.target_ref END)
  AND nexus_purge_scope_json(OLD.approved_scope_json)=NEW.approved_scope_json
  AND NEW.effect_id IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL ELSE OLD.effect_id END)
  AND NEW.payload_integrity_hash IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL ELSE OLD.payload_integrity_hash END)
  AND NEW.reason IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND OLD.reason IS NOT NULL AND instr(OLD.reason,s.object_id)>0)
      THEN NULL ELSE OLD.reason END)
  AND NEW.request_ref IS (CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.request_ref,1,7)='object:' THEN substr(OLD.request_ref,8) ELSE OLD.request_ref END AND s.payload_state='PURGED')
      THEN NULL ELSE OLD.request_ref END)
  AND (NEW.target_ref IS NOT OLD.target_ref OR NEW.approved_scope_json IS NOT OLD.approved_scope_json
    OR NEW.effect_id IS NOT OLD.effect_id OR NEW.payload_integrity_hash IS NOT OLD.payload_integrity_hash
    OR NEW.reason IS NOT OLD.reason OR NEW.request_ref IS NOT OLD.request_ref)
  AND (EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.target_ref,1,7)='object:' THEN substr(OLD.target_ref,8) ELSE OLD.target_ref END AND s.payload_state='PURGED')
    OR EXISTS (SELECT 1 FROM json_each(OLD.approved_scope_json) o JOIN object_states s
      ON s.object_id=CASE WHEN substr(o.value,1,7)='object:' THEN substr(o.value,8) ELSE o.value END AND s.payload_state='PURGED')
    OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(OLD.request_ref,1,7)='object:' THEN substr(OLD.request_ref,8) ELSE OLD.request_ref END AND s.payload_state='PURGED')
    OR (OLD.reason IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND instr(OLD.reason,s.object_id)>0))
    OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=OLD.effect_id AND e.target_ref='REDACTED_PURGED'))
)
BEGIN SELECT RAISE(ABORT,'APPROVAL_DECISION_IMMUTABLE'); END;

UPDATE approval_decisions SET
  target_ref=CASE WHEN EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(approval_decisions.target_ref,1,7)='object:' THEN substr(approval_decisions.target_ref,8) ELSE approval_decisions.target_ref END AND s.payload_state='PURGED')
      OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED') THEN 'REDACTED_PURGED' ELSE target_ref END,
  approved_scope_json=nexus_purge_scope_json(approved_scope_json),
  effect_id=CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL ELSE effect_id END,
  payload_integrity_hash=CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED') THEN NULL ELSE payload_integrity_hash END,
  reason=CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND reason IS NOT NULL AND instr(reason,s.object_id)>0) THEN NULL ELSE reason END,
  request_ref=CASE WHEN EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED')
      OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(approval_decisions.request_ref,1,7)='object:' THEN substr(approval_decisions.request_ref,8) ELSE approval_decisions.request_ref END AND s.payload_state='PURGED') THEN NULL ELSE request_ref END
WHERE EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(approval_decisions.target_ref,1,7)='object:' THEN substr(approval_decisions.target_ref,8) ELSE approval_decisions.target_ref END AND s.payload_state='PURGED')
   OR EXISTS (SELECT 1 FROM json_each(approval_decisions.approved_scope_json) o JOIN object_states s
      ON s.object_id=CASE WHEN substr(o.value,1,7)='object:' THEN substr(o.value,8) ELSE o.value END AND s.payload_state='PURGED')
   OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=CASE WHEN substr(approval_decisions.request_ref,1,7)='object:' THEN substr(approval_decisions.request_ref,8) ELSE approval_decisions.request_ref END AND s.payload_state='PURGED')
   OR (approval_decisions.reason IS NOT NULL AND EXISTS (SELECT 1 FROM object_states s WHERE s.payload_state='PURGED' AND instr(approval_decisions.reason,s.object_id)>0))
   OR EXISTS (SELECT 1 FROM effects e WHERE e.effect_id=approval_decisions.effect_id AND e.target_ref='REDACTED_PURGED');
