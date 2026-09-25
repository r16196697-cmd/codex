-- Preserve only enum-constrained, non-identifying Effect state axes in a
-- TraceEvent whose governed payload or Effect reference is purged.
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
