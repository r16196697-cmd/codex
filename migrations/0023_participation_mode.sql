CREATE TABLE participation_mode_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    mode TEXT NOT NULL CHECK(mode IN ('ACTIVE','OBSERVE','BYPASS')),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    command_id TEXT
);

INSERT INTO participation_mode_state(singleton,mode,updated_at,updated_by,command_id)
VALUES(1,'ACTIVE','1970-01-01T00:00:00Z','nexus-bootstrap',NULL);

CREATE TABLE participation_mode_events (
    sequence INTEGER PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    previous_mode TEXT NOT NULL CHECK(previous_mode IN ('ACTIVE','OBSERVE','BYPASS')),
    next_mode TEXT NOT NULL CHECK(next_mode IN ('ACTIVE','OBSERVE','BYPASS')),
    scope TEXT NOT NULL CHECK(scope='INSTANCE'),
    changed_at TEXT NOT NULL
);

CREATE TRIGGER participation_mode_events_no_update
BEFORE UPDATE ON participation_mode_events
BEGIN
    SELECT RAISE(ABORT, 'PARTICIPATION_MODE_EVENTS_IMMUTABLE');
END;

CREATE TRIGGER participation_mode_events_no_delete
BEFORE DELETE ON participation_mode_events
BEGIN
    SELECT RAISE(ABORT, 'PARTICIPATION_MODE_EVENTS_IMMUTABLE');
END;
