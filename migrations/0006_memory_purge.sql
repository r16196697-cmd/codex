CREATE TABLE verification_results (
    verification_id TEXT PRIMARY KEY,
    target_ref TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('PASS','FAIL','INCONCLUSIVE')),
    verifier_kind TEXT NOT NULL CHECK(verifier_kind IN ('T1_DETERMINISTIC','T2_AUTHORITATIVE','T3_HUMAN_OR_DOMAIN','T4_EVIDENCE_CONSTRAINED_MODEL','T5_SELF_CHECK')),
    evidence_used_json TEXT NOT NULL CHECK(json_valid(evidence_used_json)),
    independence_json TEXT NOT NULL CHECK(json_valid(independence_json)),
    result_json TEXT NOT NULL CHECK(json_valid(result_json)),
    attester_principal_id TEXT REFERENCES principals(principal_id),
    approval_ref TEXT REFERENCES approval_decisions(approval_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL
);
CREATE TABLE memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    claim_ref TEXT NOT NULL REFERENCES objects(object_id),
    owner TEXT NOT NULL,
    classification_assertion_ref TEXT NOT NULL REFERENCES classification_assertions(assertion_id),
    verification_ref TEXT NOT NULL REFERENCES verification_results(verification_id),
    truth_state TEXT NOT NULL CHECK(truth_state IN ('VERIFIED','SUPPORTED','INFERRED','HYPOTHESIS','UNKNOWN')),
    status TEXT NOT NULL CHECK(status IN ('PENDING','ADMITTED','QUARANTINED','REJECTED','PURGED')),
    metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE memory_candidate_evidence (
    candidate_id TEXT NOT NULL REFERENCES memory_candidates(candidate_id),
    evidence_object_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY(candidate_id,evidence_object_id)
);
CREATE TABLE raw_history_rows (
    row_id INTEGER PRIMARY KEY,
    object_id TEXT NOT NULL UNIQUE REFERENCES objects(object_id),
    classification_assertion_ref TEXT NOT NULL REFERENCES classification_assertions(assertion_id),
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE raw_history_fts USING fts5(body, content='', tokenize='unicode61');
CREATE TABLE admitted_memory_rows (
    row_id INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES memory_candidates(candidate_id),
    object_id TEXT NOT NULL REFERENCES objects(object_id),
    classification_assertion_ref TEXT NOT NULL REFERENCES classification_assertions(assertion_id),
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE admitted_memory_fts USING fts5(body, content='', tokenize='unicode61');
CREATE TABLE purge_plan_records (
    plan_id TEXT PRIMARY KEY,
    plan_hash TEXT NOT NULL CHECK(length(plan_hash)=64),
    lineage_revision INTEGER NOT NULL,
    plan_json TEXT NOT NULL CHECK(json_valid(plan_json)),
    command_id TEXT NOT NULL UNIQUE REFERENCES command_ledger(command_id),
    created_at TEXT NOT NULL
);
CREATE TABLE purge_execution_records (
    record_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES purge_plan_records(plan_id),
    barrier_id TEXT NOT NULL REFERENCES purge_barriers(barrier_id),
    status TEXT NOT NULL CHECK(status IN ('PLANNED','RUNNING','COMPLETED','PARTIAL','FAILED')),
    unresolved_json TEXT NOT NULL CHECK(json_valid(unresolved_json)),
    record_json TEXT NOT NULL CHECK(json_valid(record_json)),
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE purge_execution_refs (
    record_id TEXT NOT NULL REFERENCES purge_execution_records(record_id),
    object_id TEXT NOT NULL,
    payload_uri TEXT,
    integrity_hash TEXT,
    PRIMARY KEY(record_id,object_id)
);
CREATE TABLE run_manifest_inputs (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    manifest_object_id TEXT NOT NULL REFERENCES objects(object_id),
    input_object_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY(run_id,manifest_object_id,input_object_id)
);
CREATE INDEX run_manifest_inputs_object_idx ON run_manifest_inputs(input_object_id,run_id);
CREATE TRIGGER verification_results_no_update BEFORE UPDATE ON verification_results BEGIN SELECT RAISE(ABORT,'VERIFICATION_RESULT_IMMUTABLE'); END;
CREATE TRIGGER verification_results_no_delete BEFORE DELETE ON verification_results BEGIN SELECT RAISE(ABORT,'VERIFICATION_RESULT_IMMUTABLE'); END;
CREATE TRIGGER memory_candidate_identity_immutable BEFORE UPDATE ON memory_candidates WHEN NEW.candidate_id<>OLD.candidate_id OR NEW.claim_ref<>OLD.claim_ref OR NEW.owner<>OLD.owner OR NEW.classification_assertion_ref<>OLD.classification_assertion_ref OR NEW.verification_ref<>OLD.verification_ref OR NEW.truth_state<>OLD.truth_state OR NEW.metadata_json<>OLD.metadata_json OR NEW.created_at<>OLD.created_at OR NOT ((OLD.status='PENDING' AND NEW.status IN ('ADMITTED','QUARANTINED','REJECTED','PURGED')) OR (OLD.status IN ('ADMITTED','QUARANTINED','REJECTED') AND NEW.status='PURGED')) BEGIN SELECT RAISE(ABORT,'INVALID_MEMORY_CANDIDATE_TRANSITION'); END;
CREATE TRIGGER memory_candidates_no_delete BEFORE DELETE ON memory_candidates BEGIN SELECT RAISE(ABORT,'MEMORY_CANDIDATE_IMMUTABLE'); END;
CREATE TRIGGER memory_evidence_no_update BEFORE UPDATE ON memory_candidate_evidence BEGIN SELECT RAISE(ABORT,'MEMORY_EVIDENCE_IMMUTABLE'); END;
CREATE TRIGGER memory_evidence_no_delete BEFORE DELETE ON memory_candidate_evidence BEGIN SELECT RAISE(ABORT,'MEMORY_EVIDENCE_IMMUTABLE'); END;
CREATE TRIGGER purge_plans_no_update BEFORE UPDATE ON purge_plan_records BEGIN SELECT RAISE(ABORT,'PURGE_PLAN_IMMUTABLE'); END;
CREATE TRIGGER purge_plans_no_delete BEFORE DELETE ON purge_plan_records BEGIN SELECT RAISE(ABORT,'PURGE_PLAN_IMMUTABLE'); END;
CREATE TRIGGER purge_records_transition_only BEFORE UPDATE ON purge_execution_records WHEN NEW.record_id<>OLD.record_id OR NEW.plan_id<>OLD.plan_id OR NEW.barrier_id<>OLD.barrier_id OR NEW.started_at<>OLD.started_at OR NOT ((OLD.status='PLANNED' AND NEW.status IN ('RUNNING','FAILED')) OR (OLD.status='RUNNING' AND NEW.status IN ('COMPLETED','PARTIAL','FAILED')) OR (OLD.status='PARTIAL' AND NEW.status IN ('RUNNING','COMPLETED','PARTIAL','FAILED'))) BEGIN SELECT RAISE(ABORT,'INVALID_PURGE_RECORD_TRANSITION'); END;
CREATE TRIGGER purge_refs_no_update BEFORE UPDATE ON purge_execution_refs BEGIN SELECT RAISE(ABORT,'PURGE_EXECUTION_REF_IMMUTABLE'); END;
CREATE TRIGGER purge_refs_no_delete BEFORE DELETE ON purge_execution_refs BEGIN SELECT RAISE(ABORT,'PURGE_EXECUTION_REF_IMMUTABLE'); END;
CREATE TRIGGER run_manifest_input_admission BEFORE INSERT ON run_manifest_inputs
WHEN NOT EXISTS (SELECT 1 FROM object_states s WHERE s.object_id=NEW.input_object_id AND s.payload_state='AVAILABLE' AND s.validity='VALID' AND s.lifecycle='ACTIVE')
  OR EXISTS (SELECT 1 FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE r.object_id=NEW.input_object_id AND b.status IN ('ACTIVE','PARTIAL'))
BEGIN SELECT RAISE(ABORT,'RUN_MANIFEST_INPUT_PURGED_OR_BARRIERED'); END;
CREATE TRIGGER run_manifest_inputs_no_update BEFORE UPDATE ON run_manifest_inputs BEGIN SELECT RAISE(ABORT,'RUN_MANIFEST_INPUT_IMMUTABLE'); END;
CREATE TRIGGER run_manifest_inputs_no_delete BEFORE DELETE ON run_manifest_inputs BEGIN SELECT RAISE(ABORT,'RUN_MANIFEST_INPUT_IMMUTABLE'); END;
CREATE TRIGGER run_cannot_enter_active_with_protected_input BEFORE UPDATE OF status ON runs
WHEN NEW.status IN ('READY','RUNNING','WAITING','VERIFYING') AND EXISTS (
    SELECT 1 FROM run_manifest_inputs i LEFT JOIN object_states s ON s.object_id=i.input_object_id
    LEFT JOIN purge_barrier_refs r ON r.object_id=i.input_object_id
    LEFT JOIN purge_barriers b ON b.barrier_id=r.barrier_id
    WHERE i.run_id=NEW.run_id AND (s.payload_state<>'AVAILABLE' OR s.validity<>'VALID' OR s.lifecycle<>'ACTIVE' OR b.status IN ('ACTIVE','PARTIAL'))
)
BEGIN SELECT RAISE(ABORT,'RUN_INPUT_PURGED_OR_PURGE_BARRIER_ACTIVE'); END;
