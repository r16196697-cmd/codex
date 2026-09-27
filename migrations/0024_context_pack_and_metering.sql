CREATE TABLE context_pack_records (
    record_id TEXT PRIMARY KEY,
    pack_ref TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    serialized_byte_size INTEGER NOT NULL CHECK(serialized_byte_size>=0),
    selection_basis_json TEXT NOT NULL CHECK(json_valid(selection_basis_json)),
    source_counts_json TEXT NOT NULL CHECK(json_valid(source_counts_json)),
    state TEXT NOT NULL CHECK(state IN ('COMPILED','PURGED')),
    compiled_at TEXT NOT NULL
);
CREATE INDEX context_pack_records_latest_idx ON context_pack_records(task_id,compiled_at DESC);

CREATE TRIGGER context_pack_records_insert_guard BEFORE INSERT ON context_pack_records
WHEN NOT EXISTS (
    SELECT 1 FROM object_envelopes e JOIN object_states s USING(object_id)
    JOIN runs r ON r.run_id=e.created_by_run
    WHERE e.object_id=NEW.pack_ref AND e.object_type='artifact'
      AND s.lifecycle='ACTIVE' AND s.validity='VALID' AND s.payload_state='AVAILABLE'
      AND r.task_id=NEW.task_id AND r.run_id=NEW.run_id
)
BEGIN SELECT RAISE(ABORT,'CONTEXT_PACK_OBJECT_OR_ASSOCIATION_INVALID'); END;

CREATE TRIGGER context_pack_records_no_update BEFORE UPDATE ON context_pack_records
WHEN NOT (
    nexus_purge_redaction()=1 AND OLD.state='COMPILED' AND NEW.state='PURGED'
    AND NEW.record_id=OLD.record_id AND NEW.task_id=OLD.task_id AND NEW.run_id=OLD.run_id
    AND NEW.pack_ref='REDACTED_PURGED:'||OLD.record_id AND NEW.content_hash='0000000000000000000000000000000000000000000000000000000000000000'
    AND NEW.serialized_byte_size=0 AND NEW.selection_basis_json='{}' AND NEW.source_counts_json='{}'
    AND NEW.compiled_at=OLD.compiled_at
    AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.pack_ref AND s.payload_state='PURGED')
)
BEGIN SELECT RAISE(ABORT,'CONTEXT_PACK_RECORD_IMMUTABLE'); END;
CREATE TRIGGER context_pack_records_no_delete BEFORE DELETE ON context_pack_records
BEGIN SELECT RAISE(ABORT,'CONTEXT_PACK_RECORD_IMMUTABLE'); END;

CREATE TABLE value_metering_records (
    record_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    record_source TEXT NOT NULL CHECK(record_source IN ('NEXUS_CONTEXT_COMPILER','HOST_DECLARED','HOST_OBSERVED')),
    participation_mode TEXT NOT NULL CHECK(participation_mode IN ('ACTIVE','OBSERVE')),
    context_pack_ref TEXT,
    metrics_json TEXT NOT NULL CHECK(json_valid(metrics_json)),
    metrics_sha256 TEXT NOT NULL CHECK(length(metrics_sha256)=64),
    recorded_at TEXT NOT NULL,
    UNIQUE(record_id,task_id,run_id)
);
CREATE INDEX value_metering_records_latest_idx ON value_metering_records(task_id,recorded_at DESC);
CREATE INDEX value_metering_records_pack_idx ON value_metering_records(context_pack_ref);

CREATE TRIGGER value_metering_records_insert_guard BEFORE INSERT ON value_metering_records
WHEN NOT EXISTS (SELECT 1 FROM runs r WHERE r.run_id=NEW.run_id AND r.task_id=NEW.task_id)
 OR (NEW.record_source='NEXUS_CONTEXT_COMPILER' AND NOT EXISTS (
    SELECT 1 FROM context_pack_records p WHERE p.pack_ref=NEW.context_pack_ref
      AND p.task_id=NEW.task_id AND p.run_id=NEW.run_id AND p.state='COMPILED'))
 OR (NEW.context_pack_ref IS NOT NULL AND NEW.context_pack_ref NOT LIKE 'REDACTED_PURGED:%'
     AND NOT EXISTS (SELECT 1 FROM context_pack_records p WHERE p.pack_ref=NEW.context_pack_ref AND p.task_id=NEW.task_id))
BEGIN SELECT RAISE(ABORT,'METERING_RECORD_ASSOCIATION_INVALID'); END;

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
CREATE TRIGGER value_metering_records_no_delete BEFORE DELETE ON value_metering_records
BEGIN SELECT RAISE(ABORT,'METERING_RECORD_IMMUTABLE'); END;
