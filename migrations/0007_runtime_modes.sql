CREATE TABLE runtime_mode_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    mode TEXT NOT NULL CHECK(mode IN ('NORMAL','SAFE','STATELESS','RECOVERY')),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    command_id TEXT REFERENCES command_ledger(command_id)
);
INSERT INTO runtime_mode_state(singleton,mode,updated_at,updated_by,command_id)
VALUES(1,'NORMAL','1970-01-01T00:00:00Z','nexus-bootstrap',NULL);

CREATE TABLE runtime_mode_events (
    sequence INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE REFERENCES command_ledger(command_id),
    previous_mode TEXT NOT NULL CHECK(previous_mode IN ('NORMAL','SAFE','STATELESS','RECOVERY')),
    next_mode TEXT NOT NULL CHECK(next_mode IN ('NORMAL','SAFE','STATELESS','RECOVERY')),
    grant_id TEXT NOT NULL REFERENCES delegation_grants(grant_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    changed_at TEXT NOT NULL,
    request_hash TEXT NOT NULL
);
CREATE TRIGGER runtime_mode_events_no_update
BEFORE UPDATE ON runtime_mode_events
BEGIN
    SELECT RAISE(ABORT, 'RUNTIME_MODE_EVENTS_IMMUTABLE');
END;
CREATE TRIGGER runtime_mode_events_no_delete
BEFORE DELETE ON runtime_mode_events
BEGIN
    SELECT RAISE(ABORT, 'RUNTIME_MODE_EVENTS_IMMUTABLE');
END;
