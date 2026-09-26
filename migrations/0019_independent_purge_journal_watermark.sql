CREATE TABLE independent_purge_journal_watermark (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    journal_identity TEXT NOT NULL CHECK (length(journal_identity) = 64),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    record_hash TEXT NOT NULL CHECK (length(record_hash) = 64),
    acknowledged_at TEXT NOT NULL
);

CREATE TRIGGER independent_purge_watermark_no_delete
BEFORE DELETE ON independent_purge_journal_watermark
BEGIN
    SELECT RAISE(ABORT, 'PURGE_WATERMARK_IMMUTABLE');
END;

CREATE TRIGGER independent_purge_watermark_monotonic
BEFORE UPDATE ON independent_purge_journal_watermark
WHEN NEW.singleton != OLD.singleton
  OR NEW.journal_identity != OLD.journal_identity
  OR NEW.sequence < OLD.sequence
  OR (NEW.sequence = OLD.sequence AND NEW.record_hash != OLD.record_hash)
BEGIN
    SELECT RAISE(ABORT, 'PURGE_WATERMARK_MUST_ADVANCE_MONOTONICALLY');
END;
