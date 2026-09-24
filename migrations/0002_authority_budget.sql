CREATE TABLE principals (
    principal_id TEXT PRIMARY KEY,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('HUMAN','SERVICE','MODEL','TOOL')),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','SUSPENDED','REVOKED'))
);

CREATE TABLE trust_anchors (
    anchor_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL UNIQUE REFERENCES principals(principal_id),
    policy_ref TEXT NOT NULL
);

CREATE TABLE delegation_grants (
    grant_id TEXT PRIMARY KEY,
    parent_grant_id TEXT REFERENCES delegation_grants(grant_id),
    issued_by TEXT NOT NULL REFERENCES principals(principal_id),
    granted_to TEXT NOT NULL REFERENCES principals(principal_id),
    task_scope_json TEXT NOT NULL CHECK (json_valid(task_scope_json)),
    resource_scope_json TEXT NOT NULL CHECK (json_valid(resource_scope_json)),
    action_scope_json TEXT NOT NULL CHECK (json_valid(action_scope_json)),
    audience_scope_json TEXT NOT NULL CHECK (json_valid(audience_scope_json)),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PROPOSED','ACTIVE','EXPIRED','REVOKED')),
    policy_version TEXT NOT NULL,
    credential_ref TEXT
);

CREATE TABLE authority_events (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    grant_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN ('GRANT_CREATED','GRANT_ACTIVATED','GRANT_REVOKED','PRINCIPAL_REVOKED','AUTHORIZATION_DENIED')),
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE approval_decisions (
    approval_id TEXT PRIMARY KEY,
    approver_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
    target_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    effect_id TEXT,
    payload_integrity_hash TEXT CHECK (payload_integrity_hash IS NULL OR length(payload_integrity_hash)=64),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE','DENY')),
    approved_scope_json TEXT NOT NULL CHECK (json_valid(approved_scope_json)),
    policy_version TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT,
    reason TEXT,
    request_ref TEXT
);

CREATE TABLE classification_assertions (
    assertion_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('OBJECT','RUN','TRACE_EVENT')),
    subject_ref TEXT NOT NULL,
    sensitivity_level TEXT NOT NULL CHECK (sensitivity_level IN ('PUBLIC','PERSONAL','PROJECT_PRIVATE','CONFIDENTIAL','SECRET')),
    handling_tags_json TEXT NOT NULL CHECK (json_valid(handling_tags_json)),
    policy_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES principals(principal_id),
    supersedes TEXT REFERENCES classification_assertions(assertion_id)
);
CREATE INDEX classification_subject_idx ON classification_assertions(subject_type,subject_ref);
CREATE TRIGGER classification_assertions_no_update
BEFORE UPDATE ON classification_assertions
BEGIN
    SELECT RAISE(ABORT, 'CLASSIFICATION_ASSERTION_IMMUTABLE');
END;

CREATE TABLE budget_accounts (
    account_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    amount_limit INTEGER NOT NULL CHECK (amount_limit >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    consumed INTEGER NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    unit TEXT NOT NULL CHECK (length(unit) > 0),
    model_call_limit INTEGER NOT NULL CHECK (model_call_limit >= 0),
    tool_call_limit INTEGER NOT NULL CHECK (tool_call_limit >= 0),
    child_run_limit INTEGER NOT NULL CHECK (child_run_limit >= 0),
    model_calls_reserved INTEGER NOT NULL DEFAULT 0 CHECK (model_calls_reserved >= 0),
    model_calls_consumed INTEGER NOT NULL DEFAULT 0 CHECK (model_calls_consumed >= 0),
    tool_calls_reserved INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls_reserved >= 0),
    tool_calls_consumed INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls_consumed >= 0),
    child_runs_reserved INTEGER NOT NULL DEFAULT 0 CHECK (child_runs_reserved >= 0),
    child_runs_consumed INTEGER NOT NULL DEFAULT 0 CHECK (child_runs_consumed >= 0),
    CHECK (reserved + consumed <= amount_limit),
    CHECK (model_calls_reserved + model_calls_consumed <= model_call_limit),
    CHECK (tool_calls_reserved + tool_calls_consumed <= tool_call_limit),
    CHECK (child_runs_reserved + child_runs_consumed <= child_run_limit)
);

CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES budget_accounts(account_id),
    run_id TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    model_calls INTEGER NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
    tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    child_runs INTEGER NOT NULL DEFAULT 0 CHECK (child_runs >= 0),
    state TEXT NOT NULL CHECK (state IN ('RESERVED','CONSUMED','RELEASED')),
    command_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE budget_ledger (
    ledger_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id TEXT NOT NULL REFERENCES budget_reservations(reservation_id),
    command_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('RESERVED','CONSUMED','RELEASED')),
    amount INTEGER NOT NULL CHECK (amount >= 0),
    created_at TEXT NOT NULL
);

CREATE INDEX delegation_grants_parent_idx ON delegation_grants(parent_grant_id);
CREATE INDEX budget_reservations_account_idx ON budget_reservations(account_id, state);

CREATE TRIGGER principals_status_transition_only
BEFORE UPDATE ON principals
WHEN NEW.principal_id <> OLD.principal_id
  OR NEW.principal_type <> OLD.principal_type
  OR OLD.status <> 'ACTIVE'
  OR NEW.status NOT IN ('SUSPENDED','REVOKED')
BEGIN
    SELECT RAISE(ABORT, 'INVALID_PRINCIPAL_TRANSITION');
END;

CREATE TRIGGER trust_anchors_no_update
BEFORE UPDATE ON trust_anchors
BEGIN
    SELECT RAISE(ABORT, 'TRUST_ANCHOR_IMMUTABLE');
END;
CREATE TRIGGER trust_anchors_no_delete
BEFORE DELETE ON trust_anchors
BEGIN
    SELECT RAISE(ABORT, 'TRUST_ANCHOR_IMMUTABLE');
END;

CREATE TRIGGER delegation_grants_scope_immutable
BEFORE UPDATE ON delegation_grants
WHEN NEW.grant_id <> OLD.grant_id
  OR NEW.parent_grant_id IS NOT OLD.parent_grant_id
  OR NEW.issued_by <> OLD.issued_by
  OR NEW.granted_to <> OLD.granted_to
  OR NEW.task_scope_json <> OLD.task_scope_json
  OR NEW.resource_scope_json <> OLD.resource_scope_json
  OR NEW.action_scope_json <> OLD.action_scope_json
  OR NEW.audience_scope_json <> OLD.audience_scope_json
  OR NEW.issued_at <> OLD.issued_at
  OR NEW.expires_at <> OLD.expires_at
  OR NEW.policy_version <> OLD.policy_version
  OR NEW.credential_ref IS NOT OLD.credential_ref
  OR NOT ((OLD.status='PROPOSED' AND NEW.status IN ('ACTIVE','REVOKED','EXPIRED'))
       OR (OLD.status='ACTIVE' AND NEW.status IN ('REVOKED','EXPIRED')))
BEGIN
    SELECT RAISE(ABORT, 'INVALID_GRANT_TRANSITION');
END;

CREATE TRIGGER authority_events_no_update
BEFORE UPDATE ON authority_events
BEGIN
    SELECT RAISE(ABORT, 'AUTHORITY_EVENT_IMMUTABLE');
END;
CREATE TRIGGER authority_events_no_delete
BEFORE DELETE ON authority_events
BEGIN
    SELECT RAISE(ABORT, 'AUTHORITY_EVENT_IMMUTABLE');
END;

CREATE TRIGGER approval_decisions_no_update
BEFORE UPDATE ON approval_decisions
BEGIN
    SELECT RAISE(ABORT, 'APPROVAL_IMMUTABLE');
END;
CREATE TRIGGER approval_decisions_no_delete
BEFORE DELETE ON approval_decisions
BEGIN
    SELECT RAISE(ABORT, 'APPROVAL_IMMUTABLE');
END;

CREATE TRIGGER classification_assertions_no_delete
BEFORE DELETE ON classification_assertions
BEGIN
    SELECT RAISE(ABORT, 'CLASSIFICATION_ASSERTION_IMMUTABLE');
END;

CREATE TRIGGER budget_accounts_limits_immutable
BEFORE UPDATE ON budget_accounts
WHEN NEW.account_id <> OLD.account_id
  OR NEW.task_id <> OLD.task_id
  OR NEW.amount_limit <> OLD.amount_limit
  OR NEW.unit <> OLD.unit
  OR NEW.model_call_limit <> OLD.model_call_limit
  OR NEW.tool_call_limit <> OLD.tool_call_limit
  OR NEW.child_run_limit <> OLD.child_run_limit
BEGIN
    SELECT RAISE(ABORT, 'BUDGET_ACCOUNT_LIMIT_IMMUTABLE');
END;

CREATE TRIGGER budget_reservations_transition_only
BEFORE UPDATE ON budget_reservations
WHEN NEW.reservation_id <> OLD.reservation_id
  OR NEW.account_id <> OLD.account_id
  OR NEW.run_id <> OLD.run_id
  OR NEW.amount <> OLD.amount
  OR NEW.model_calls <> OLD.model_calls
  OR NEW.tool_calls <> OLD.tool_calls
  OR NEW.child_runs <> OLD.child_runs
  OR NEW.command_id <> OLD.command_id
  OR OLD.state <> 'RESERVED'
  OR NEW.state NOT IN ('CONSUMED','RELEASED')
BEGIN
    SELECT RAISE(ABORT, 'INVALID_BUDGET_RESERVATION_TRANSITION');
END;

CREATE TRIGGER budget_ledger_no_update
BEFORE UPDATE ON budget_ledger
BEGIN
    SELECT RAISE(ABORT, 'BUDGET_LEDGER_IMMUTABLE');
END;
CREATE TRIGGER budget_ledger_no_delete
BEFORE DELETE ON budget_ledger
BEGIN
    SELECT RAISE(ABORT, 'BUDGET_LEDGER_IMMUTABLE');
END;

CREATE TRIGGER command_ledger_no_update
BEFORE UPDATE ON command_ledger
BEGIN
    SELECT RAISE(ABORT, 'COMMAND_LEDGER_IMMUTABLE');
END;
CREATE TRIGGER command_ledger_no_delete
BEFORE DELETE ON command_ledger
BEGIN
    SELECT RAISE(ABORT, 'COMMAND_LEDGER_IMMUTABLE');
END;
