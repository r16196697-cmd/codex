CREATE TABLE instance_policy_binding (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    instance_id TEXT NOT NULL CHECK (length(instance_id) > 0),
    binding_source TEXT NOT NULL CHECK (binding_source IN ('FRESH_INITIALIZE','LEGACY_OPERATOR_ADOPTION')),
    policy_version TEXT NOT NULL CHECK (length(policy_version) > 0),
    policy_sha256 TEXT NOT NULL CHECK (length(policy_sha256) = 64 AND policy_sha256 NOT GLOB '*[^0-9a-f]*'),
    journal_identity TEXT NOT NULL CHECK (length(journal_identity) = 64 AND journal_identity NOT GLOB '*[^0-9a-f]*'),
    bound_at TEXT NOT NULL,
    bootstrap_protocol_version TEXT NOT NULL CHECK (length(bootstrap_protocol_version) > 0),
    binding_command_id TEXT NOT NULL UNIQUE CHECK (length(binding_command_id) > 0),
    binding_request_sha256 TEXT NOT NULL CHECK (length(binding_request_sha256) = 64 AND binding_request_sha256 NOT GLOB '*[^0-9a-f]*')
);

CREATE TRIGGER instance_policy_binding_no_update
BEFORE UPDATE ON instance_policy_binding
BEGIN
    SELECT RAISE(ABORT, 'INSTANCE_POLICY_BINDING_IMMUTABLE');
END;

CREATE TRIGGER instance_policy_binding_no_delete
BEFORE DELETE ON instance_policy_binding
BEGIN
    SELECT RAISE(ABORT, 'INSTANCE_POLICY_BINDING_IMMUTABLE');
END;
