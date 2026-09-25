-- run_manifest_inputs is a rebuildable scheduling/index projection, not the
-- authoritative RunManifest or Trace fact. Permit purge-only removal once
-- either referenced object is tombstoned; all other deletes stay forbidden.
DROP TRIGGER run_manifest_inputs_no_delete;
CREATE TRIGGER run_manifest_inputs_no_delete
BEFORE DELETE ON run_manifest_inputs
WHEN NOT (
    EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.input_object_id AND s.payload_state='PURGED')
    OR EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=OLD.manifest_object_id AND s.payload_state='PURGED')
)
BEGIN SELECT RAISE(ABORT,'RUN_MANIFEST_INPUT_IMMUTABLE'); END;
