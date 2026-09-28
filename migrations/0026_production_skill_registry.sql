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
    instruction_object_ref TEXT NOT NULL REFERENCES object_envelopes(object_id),
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
CREATE INDEX skill_registry_name_idx ON skill_registry_entries(skill_name,status,skill_id);
CREATE INDEX skill_registry_revision_idx ON skill_registry_entries(source_scope,source_namespace,source_ref,skill_name);

CREATE TRIGGER skill_registry_identity_guard BEFORE UPDATE ON skill_registry_entries
WHEN NEW.skill_id<>OLD.skill_id OR NEW.skill_name<>OLD.skill_name OR NEW.description<>OLD.description
 OR NEW.source_scope<>OLD.source_scope OR NEW.source_namespace<>OLD.source_namespace OR NEW.source_ref<>OLD.source_ref
 OR NEW.skill_md_sha256<>OLD.skill_md_sha256 OR NEW.package_manifest_sha256<>OLD.package_manifest_sha256
 OR NEW.package_manifest_json<>OLD.package_manifest_json OR NEW.fallback_blockers_json<>OLD.fallback_blockers_json
 OR NEW.instruction_object_ref<>OLD.instruction_object_ref OR NEW.registered_task_id<>OLD.registered_task_id
 OR NEW.registered_run_id<>OLD.registered_run_id OR NEW.registered_by<>OLD.registered_by
 OR NEW.registered_at<>OLD.registered_at
 OR NOT ((OLD.status='REGISTERED' AND NEW.status IN ('ENABLED','DISABLED','REJECTED','STALE'))
      OR (OLD.status='ENABLED' AND NEW.status IN ('DISABLED','REJECTED','STALE'))
      OR (OLD.status='DISABLED' AND NEW.status IN ('ENABLED','REJECTED','STALE'))
      OR (OLD.status='REJECTED' AND NEW.status='STALE'))
 OR (NEW.status='STALE' AND (NEW.review_command_id IS NOT OLD.review_command_id
      OR NEW.reviewed_by IS NOT OLD.reviewed_by OR NEW.reviewed_at IS NOT OLD.reviewed_at))
 OR (NEW.status<>'STALE' AND (NEW.review_command_id IS NULL OR NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL))
BEGIN SELECT RAISE(ABORT,'SKILL_REGISTRY_IDENTITY_OR_STATUS_INVALID'); END;

CREATE TRIGGER skill_registry_no_delete BEFORE DELETE ON skill_registry_entries
BEGIN SELECT RAISE(ABORT,'SKILL_REGISTRY_RECORD_IMMUTABLE'); END;

CREATE TABLE skill_resolution_records (
    selection_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    query_sha256 TEXT NOT NULL CHECK(length(query_sha256)=64),
    candidate_count INTEGER NOT NULL CHECK(candidate_count>=0),
    selection_basis_json TEXT NOT NULL CHECK(json_valid(selection_basis_json)),
    result_status TEXT NOT NULL CHECK(result_status IN ('RESOLVED','NO_MATCH','AMBIGUOUS','UNSUPPORTED','INACTIVE_MODE')),
    resolution TEXT CHECK(resolution IN ('HOST_NATIVE','NEXUS_FALLBACK','UNSUPPORTED')),
    skill_id TEXT REFERENCES skill_registry_entries(skill_id),
    host_inventory_provenance TEXT CHECK(host_inventory_provenance IN ('ADAPTER_DISCOVERY','HOST_DECLARED')),
    host_native_availability TEXT CHECK(host_native_availability IN ('AVAILABLE','UNAVAILABLE','UNKNOWN')),
    instruction_object_ref TEXT REFERENCES object_envelopes(object_id),
    instruction_sha256 TEXT CHECK(instruction_sha256 IS NULL OR length(instruction_sha256)=64),
    instruction_byte_size INTEGER CHECK(instruction_byte_size IS NULL OR instruction_byte_size>=0),
    instruction_load_status TEXT NOT NULL CHECK(instruction_load_status IN ('NOT_LOADED','LOADED_TO_GOVERNED_ARTIFACT','UNAVAILABLE')),
    delivery_status TEXT NOT NULL CHECK(delivery_status IN ('UNKNOWN','HOST_DECLARED_DELIVERY','OBSERVED_DELIVERY')),
    model_visible_exposure TEXT NOT NULL CHECK(model_visible_exposure IN ('UNKNOWN','UNAVAILABLE','OBSERVED')),
    selection_latency_ms REAL NOT NULL CHECK(selection_latency_ms>=0),
    created_at TEXT NOT NULL
);
CREATE INDEX skill_resolution_latest_idx ON skill_resolution_records(created_at DESC,selection_id DESC);
CREATE INDEX skill_resolution_run_idx ON skill_resolution_records(task_id,run_id,created_at DESC);

CREATE TRIGGER skill_resolution_records_no_update BEFORE UPDATE ON skill_resolution_records
BEGIN SELECT RAISE(ABORT,'SKILL_RESOLUTION_RECORD_IMMUTABLE'); END;
CREATE TRIGGER skill_resolution_records_no_delete BEFORE DELETE ON skill_resolution_records
BEGIN SELECT RAISE(ABORT,'SKILL_RESOLUTION_RECORD_IMMUTABLE'); END;
