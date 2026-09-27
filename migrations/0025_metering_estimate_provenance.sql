DROP TRIGGER value_metering_records_no_update;

CREATE TEMP TABLE metering_v25_rewrite AS
SELECT r.record_id,
       (SELECT json_group_object(
                   key,
                   json(CASE
                       WHEN json_extract(value, '$.provenance') = 'ESTIMATED'
                           THEN json_set(value, '$.provenance',
                               CASE r.record_source
                                   WHEN 'NEXUS_CONTEXT_COMPILER' THEN 'DERIVED'
                                   WHEN 'HOST_OBSERVED' THEN 'OBSERVED'
                                   ELSE 'HOST_DECLARED'
                               END,
                               '$.estimate_status', 'ESTIMATED')
                       ELSE json_set(value, '$.estimate_status',
                           CASE WHEN json_extract(value, '$.provenance') = 'UNAVAILABLE'
                               THEN 'UNAVAILABLE' ELSE 'NOT_ESTIMATED' END)
                   END)
               )
        FROM json_each(r.metrics_json)) AS metrics_json
FROM value_metering_records r;

UPDATE value_metering_records
SET metrics_json = (SELECT metrics_json FROM metering_v25_rewrite WHERE record_id=value_metering_records.record_id),
    metrics_sha256 = nexus_sha256((SELECT metrics_json FROM metering_v25_rewrite WHERE record_id=value_metering_records.record_id));

DROP TABLE metering_v25_rewrite;

CREATE TRIGGER value_metering_records_no_update BEFORE UPDATE ON value_metering_records
WHEN NOT (
    nexus_purge_redaction()=1 AND NEW.record_id=OLD.record_id AND NEW.task_id=OLD.task_id
    AND NEW.run_id=OLD.run_id AND NEW.record_source=OLD.record_source
    AND NEW.participation_mode=OLD.participation_mode AND NEW.metrics_json=OLD.metrics_json
    AND NEW.metrics_sha256=OLD.metrics_sha256 AND NEW.recorded_at=OLD.recorded_at
    AND OLD.context_pack_ref IS NOT NULL AND NEW.context_pack_ref='REDACTED_PURGED'
    AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.context_pack_ref AND s.payload_state='PURGED')
)
BEGIN SELECT RAISE(ABORT,'METERING_RECORD_IMMUTABLE'); END;
