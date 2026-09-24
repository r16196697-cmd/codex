CREATE TABLE objects (
    object_id TEXT PRIMARY KEY
);

CREATE TABLE hash_profiles (
    profile_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL CHECK (profile_version >= 1),
    representation TEXT NOT NULL,
    hash_algorithm TEXT NOT NULL CHECK (hash_algorithm = 'SHA-256'),
    PRIMARY KEY (profile_id, profile_version)
);

CREATE TABLE object_envelopes (
    object_id TEXT PRIMARY KEY REFERENCES objects(object_id) ON DELETE CASCADE,
    object_type TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    payload_uri TEXT NOT NULL UNIQUE,
    hash_profile_ref TEXT NOT NULL,
    hash_profile_version INTEGER NOT NULL CHECK (hash_profile_version >= 1),
    integrity_hash TEXT NOT NULL CHECK (length(integrity_hash) = 64),
    semantic_hash TEXT CHECK (semantic_hash IS NULL OR length(semantic_hash) = 64),
    created_by_run TEXT NOT NULL,
    classification_assertion_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (hash_profile_ref, hash_profile_version) REFERENCES hash_profiles(profile_id, profile_version)
);

CREATE TABLE object_states (
    object_id TEXT PRIMARY KEY REFERENCES objects(object_id) ON DELETE CASCADE,
    revision TEXT CHECK (revision IS NULL OR revision IN ('CURRENT', 'SUPERSEDED')),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('ACTIVE', 'RETIRED')),
    validity TEXT NOT NULL CHECK (validity IN ('VALID', 'INVALIDATED')),
    payload_state TEXT NOT NULL CHECK (payload_state IN ('AVAILABLE', 'ARCHIVED', 'PURGED'))
);

CREATE TABLE object_relations (
    from_id TEXT NOT NULL REFERENCES objects(object_id),
    relation_type TEXT NOT NULL CHECK (relation_type IN ('derived_from', 'supports', 'contradicts', 'generated_from', 'supersedes')),
    to_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY (from_id, relation_type, to_id)
);

CREATE TRIGGER objects_no_update
BEFORE UPDATE ON objects
BEGIN
    SELECT RAISE(ABORT, 'OBJECT_IMMUTABLE');
END;

CREATE TRIGGER object_envelopes_no_update
BEFORE UPDATE ON object_envelopes
BEGIN
    SELECT RAISE(ABORT, 'OBJECT_IMMUTABLE');
END;

CREATE TRIGGER hash_profiles_no_update
BEFORE UPDATE ON hash_profiles
BEGIN
    SELECT RAISE(ABORT, 'HASH_PROFILE_IMMUTABLE');
END;

CREATE TRIGGER object_relations_no_update
BEFORE UPDATE ON object_relations
BEGIN
    SELECT RAISE(ABORT, 'OBJECT_RELATION_IMMUTABLE');
END;

CREATE INDEX object_relations_to_idx ON object_relations(to_id, relation_type);

CREATE TABLE logical_refs (
    ref_id TEXT PRIMARY KEY,
    ref_type TEXT NOT NULL,
    current_object_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at TEXT NOT NULL,
    updated_by_run TEXT NOT NULL
);

CREATE TABLE command_ledger (
    command_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    status TEXT NOT NULL CHECK (status = 'SUCCEEDED'),
    created_at TEXT NOT NULL
);

CREATE TABLE purge_barriers (
    barrier_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    lineage_revision INTEGER NOT NULL CHECK (lineage_revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'PARTIAL', 'RELEASED')),
    created_at TEXT NOT NULL
);

CREATE TABLE purge_barrier_refs (
    barrier_id TEXT NOT NULL REFERENCES purge_barriers(barrier_id) ON DELETE CASCADE,
    object_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY (barrier_id, object_id)
);

CREATE TABLE purge_ledger (
    ledger_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    barrier_id TEXT NOT NULL REFERENCES purge_barriers(barrier_id),
    action TEXT NOT NULL CHECK (action IN ('BARRIER_INSTALLED', 'BARRIER_PARTIAL', 'BARRIER_RELEASED')),
    created_at TEXT NOT NULL
);

INSERT INTO hash_profiles(profile_id, profile_version, representation, hash_algorithm)
VALUES ('raw-sha256', 1, 'RAW_BYTES', 'SHA-256');
