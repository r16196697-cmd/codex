-- Bind independent-journal recovery projections to the Task without guessing
-- legacy v1 provenance, and preserve an immutable commitment to committed
-- CommandLedger results while their public projection is redacted by Purge.
ALTER TABLE purge_barriers ADD COLUMN task_id TEXT REFERENCES tasks(task_id);

DROP TRIGGER command_ledger_no_update;
ALTER TABLE command_ledger ADD COLUMN result_commitment TEXT;
UPDATE command_ledger SET result_commitment=nexus_sha256(result_json);
ALTER TABLE command_ledger ADD COLUMN result_state TEXT NOT NULL DEFAULT 'LIVE'
    CHECK(result_state IN ('LIVE','PURGED_REDACTED'));

CREATE TRIGGER command_ledger_no_update
BEFORE UPDATE ON command_ledger
WHEN NOT (
    NEW.command_id=OLD.command_id
    AND NEW.operation=OLD.operation
    AND NEW.request_hash=OLD.request_hash
    AND NEW.status=OLD.status
    AND NEW.created_at=OLD.created_at
    AND NEW.result_commitment=OLD.result_commitment
    AND OLD.result_state='LIVE'
    AND NEW.result_state='PURGED_REDACTED'
    AND NEW.result_json<>OLD.result_json
    AND nexus_purge_redaction()=1
)
BEGIN
    SELECT RAISE(ABORT,'COMMAND_LEDGER_IMMUTABLE');
END;

CREATE TRIGGER command_ledger_result_commitment_required
BEFORE INSERT ON command_ledger
WHEN NEW.result_commitment IS NULL OR length(NEW.result_commitment)<>64 OR NEW.result_state<>'LIVE'
BEGIN
    SELECT RAISE(ABORT,'COMMAND_RESULT_COMMITMENT_REQUIRED');
END;
