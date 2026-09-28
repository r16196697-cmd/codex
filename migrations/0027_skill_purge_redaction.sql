-- Permit existing Purge semantics to redact Skill Object references while
-- retaining the non-sensitive Registry and resolution history.
DROP TRIGGER skill_registry_identity_guard;
DROP TRIGGER skill_registry_no_delete;
DROP TRIGGER skill_resolution_records_no_update;

ALTER TABLE skill_registry_entries RENAME TO skill_registry_entries_v26;
CREATE TABLE skill_registry_entries (
    skill_id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_scope TEXT NOT NULL CHECK(source_scope IN ('REPO','USER','EXPLICIT_IMPORT')),
    source_namespace TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    skill_md_sha256 TEXT NOT NULL CHECK(length(skill_md_sha256)=64),
    package_manifest_sha256 TEXT NOT NULL CHECK(length(package_manifest_sha256)=64),
    package_manifest_json TEXT NOT NULL CHECK(json_valid(package_manifest_json)),
    fallback_blockers_json TEXT NOT NULL CHECK(json_valid(fallback_blockers_json)),
    instruction_object_ref TEXT REFERENCES object_envelopes(object_id),
    registered_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    registered_run_id TEXT NOT NULL REFERENCES runs(run_id),
    registered_by TEXT NOT NULL REFERENCES principals(principal_id),
    status TEXT NOT NULL CHECK(status IN ('REGISTERED','ENABLED','DISABLED','REJECTED','STALE')),
    review_command_id TEXT UNIQUE,
    reviewed_by TEXT REFERENCES principals(principal_id),
    reviewed_at TEXT,
    registered_at TEXT NOT NULL,
    UNIQUE(source_scope,source_namespace,source_ref,package_manifest_sha256)
);
INSERT INTO skill_registry_entries SELECT * FROM skill_registry_entries_v26;
DROP TABLE skill_registry_entries_v26;

CREATE INDEX skill_registry_name_idx ON skill_registry_entries(skill_name,status,skill_id);
CREATE INDEX skill_registry_revision_idx ON skill_registry_entries(source_scope,source_namespace,source_ref,skill_name);

CREATE TRIGGER skill_registry_identity_guard BEFORE UPDATE ON skill_registry_entries
WHEN NOT (
    (NEW.skill_id=OLD.skill_id AND NEW.skill_name=OLD.skill_name AND NEW.description=OLD.description
     AND NEW.source_scope=OLD.source_scope AND NEW.source_namespace=OLD.source_namespace AND NEW.source_ref=OLD.source_ref
     AND NEW.skill_md_sha256=OLD.skill_md_sha256 AND NEW.package_manifest_sha256=OLD.package_manifest_sha256
     AND NEW.package_manifest_json=OLD.package_manifest_json AND NEW.fallback_blockers_json=OLD.fallback_blockers_json
     AND NEW.instruction_object_ref IS OLD.instruction_object_ref
     AND NEW.registered_task_id=OLD.registered_task_id AND NEW.registered_run_id=OLD.registered_run_id
     AND NEW.registered_by=OLD.registered_by AND NEW.registered_at=OLD.registered_at
     AND ((OLD.status='REGISTERED' AND NEW.status IN ('ENABLED','DISABLED','REJECTED','STALE'))
       OR (OLD.status='ENABLED' AND NEW.status IN ('DISABLED','REJECTED','STALE'))
       OR (OLD.status='DISABLED' AND NEW.status IN ('ENABLED','REJECTED','STALE'))
       OR (OLD.status='REJECTED' AND NEW.status='STALE'))
     AND (NEW.status='STALE' AND NEW.review_command_id IS OLD.review_command_id
       AND NEW.reviewed_by IS OLD.reviewed_by AND NEW.reviewed_at IS OLD.reviewed_at
       OR NEW.status<>'STALE' AND NEW.review_command_id IS NOT NULL
       AND NEW.reviewed_by IS NOT NULL AND NEW.reviewed_at IS NOT NULL))
    OR (nexus_purge_redaction()=1 AND NEW.skill_id=OLD.skill_id AND NEW.skill_name=OLD.skill_name
     AND NEW.description=OLD.description AND NEW.source_scope=OLD.source_scope
     AND NEW.source_namespace=OLD.source_namespace AND NEW.source_ref=OLD.source_ref
     AND NEW.skill_md_sha256=OLD.skill_md_sha256 AND NEW.package_manifest_sha256=OLD.package_manifest_sha256
     AND NEW.package_manifest_json=OLD.package_manifest_json AND NEW.fallback_blockers_json=OLD.fallback_blockers_json
     AND NEW.instruction_object_ref IS NULL AND NEW.registered_task_id=OLD.registered_task_id
     AND NEW.registered_run_id=OLD.registered_run_id AND NEW.registered_by=OLD.registered_by
     AND NEW.status='STALE' AND NEW.review_command_id IS OLD.review_command_id
     AND NEW.reviewed_by IS OLD.reviewed_by AND NEW.reviewed_at IS OLD.reviewed_at
     AND NEW.registered_at=OLD.registered_at
     AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.instruction_object_ref AND s.payload_state='PURGED'))
)
BEGIN SELECT RAISE(ABORT,'SKILL_REGISTRY_IDENTITY_OR_STATUS_INVALID'); END;

CREATE TRIGGER skill_registry_no_delete BEFORE DELETE ON skill_registry_entries
BEGIN SELECT RAISE(ABORT,'SKILL_REGISTRY_RECORD_IMMUTABLE'); END;

CREATE TRIGGER skill_resolution_records_no_update BEFORE UPDATE ON skill_resolution_records
WHEN NOT (
    nexus_purge_redaction()=1 AND NEW.selection_id=OLD.selection_id AND NEW.command_id=OLD.command_id
    AND NEW.task_id=OLD.task_id AND NEW.run_id=OLD.run_id AND NEW.query_sha256=OLD.query_sha256
    AND NEW.candidate_count=OLD.candidate_count AND NEW.selection_basis_json=OLD.selection_basis_json
    AND NEW.result_status=OLD.result_status AND NEW.resolution IS OLD.resolution
    AND NEW.skill_id IS OLD.skill_id AND NEW.host_inventory_provenance IS OLD.host_inventory_provenance
    AND NEW.host_native_availability IS OLD.host_native_availability AND NEW.instruction_object_ref IS NULL
    AND NEW.instruction_sha256 IS OLD.instruction_sha256 AND NEW.instruction_byte_size IS OLD.instruction_byte_size
    AND NEW.instruction_load_status=OLD.instruction_load_status AND NEW.delivery_status=OLD.delivery_status
    AND NEW.model_visible_exposure=OLD.model_visible_exposure
    AND NEW.selection_latency_ms=OLD.selection_latency_ms AND NEW.created_at=OLD.created_at
    AND EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.instruction_object_ref AND s.payload_state='PURGED')
)
BEGIN SELECT RAISE(ABORT,'SKILL_RESOLUTION_RECORD_IMMUTABLE'); END;
