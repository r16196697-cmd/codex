from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone

from kernel.object.errors import IntegrityMismatch, ObjectNotFound, PurgedObject
from kernel.runtime.errors import RuntimeDenied


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class VerificationService:
    """The v0.1 verifier exposes deterministic checks; model self-evaluation is never a PASS authority."""

    def __init__(self, store, authority):
        self.store, self.authority = store, authority

    def verify_object_integrity(self, *, verification_id: str, target_ref: str, evidence_refs: list[str], run_id: str, independence: dict[str, str] | None = None) -> dict:
        self.store._require_mode("core_write")
        evidence = sorted(set(evidence_refs))
        if not evidence:
            raise RuntimeDenied("VERIFIER_REQUIRES_EVIDENCE_REFS")
        run = self._run(run_id)
        if run["status"] not in {"RUNNING", "VERIFYING"}:
            raise RuntimeDenied("VERIFIER_RUN_NOT_ACTIVE")
        grant_id = run["grant_id"]
        for object_id in sorted(set([target_ref, *evidence])):
            self.authority.evaluate_authorization(
                grant_id,
                {"task": run["task_id"], "resource": object_id, "action": "VERIFY", "audience": "nexus-runtime"},
                f"verify-auth-{verification_id}-{object_id}",
            )
        missing = []
        for object_id in sorted(set([target_ref, *evidence])):
            try:
                self.store.verify_object(object_id)
                metadata = self.store.get_object_metadata(object_id)
                self._assert_run_boundary(run, metadata["classification_assertion_ref"], object_id)
            except (IntegrityMismatch, ObjectNotFound, PurgedObject):
                missing.append(object_id)
        axes = {"generator_independence": "NOT_APPLICABLE", "evidence_independence": "UNKNOWN", "method_independence": "INDEPENDENT"}
        if independence is not None and independence != axes:
            raise RuntimeDenied("T1_INDEPENDENCE_IS_NOT_CALLER_ASSERTED")
        result = {"schema_id": "nexus.verification_result", "schema_version": 1, "verification_id": verification_id, "target_ref": target_ref, "verdict": "PASS" if not missing else "INCONCLUSIVE", "verifier_kind": "T1_DETERMINISTIC", "evidence_used": evidence, "missing_evidence": missing, "conflicts": [], "independence": axes, "rationale_summary": "SHA-256 integrity verification of target and referenced evidence objects.", "run_id": run_id, "policy_version": self.authority.policy["policy_version"]}
        self.store._validate("nexus.verification_result@1.schema.json", result)
        operation = "record_verification_result"
        request = result
        digest = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store._replay_command(conn, "verify-" + verification_id, operation, digest)
                if prior is not None:
                    conn.commit(); return result
                conn.execute("INSERT INTO verification_results(verification_id,target_ref,verdict,verifier_kind,evidence_used_json,independence_json,result_json,attester_principal_id,approval_ref,run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (verification_id, target_ref, result["verdict"], result["verifier_kind"], json.dumps(evidence), json.dumps(axes, sort_keys=True), json.dumps(result, sort_keys=True), None, None, run_id, _now()))
                self.store._record_command(conn, "verify-" + verification_id, operation, digest, {"verification_id": verification_id, "verdict": result["verdict"]})
                conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def record_human_verification(self, *, verification_id: str, target_ref: str, evidence_refs: list[str], run_id: str, approval_id: str, attester_principal_id: str, independence: dict[str, str]) -> dict:
        self.store._require_mode("core_write")
        evidence = sorted(set(evidence_refs))
        if not evidence:
            raise RuntimeDenied("VERIFIER_REQUIRES_EVIDENCE_REFS")
        if set(independence) != {"generator_independence", "evidence_independence", "method_independence"}:
            raise RuntimeDenied("VERIFIER_INDEPENDENCE_AXES_REQUIRED")
        run = self._run(run_id)
        if run["status"] not in {"RUNNING", "VERIFYING"}:
            raise RuntimeDenied("VERIFIER_RUN_NOT_ACTIVE")
        refs = sorted(set([target_ref, *evidence]))
        for object_id in refs:
            self.store.verify_object(object_id)
            metadata = self.store.get_object_metadata(object_id)
            self._assert_run_boundary(run, metadata["classification_assertion_ref"], object_id)
        payload_hash = self.human_payload_hash(verification_id=verification_id, target_ref=target_ref, evidence_refs=evidence, run_id=run_id, independence=independence)
        with self.store._connection() as conn:
            approval = conn.execute("SELECT approver_principal_id FROM approval_decisions WHERE approval_id=?", (approval_id,)).fetchone()
        if not approval or approval["approver_principal_id"] != attester_principal_id:
            raise RuntimeDenied("VERIFIER_ATTESTER_APPROVAL_MISMATCH")
        self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": target_ref, "action": "VERIFY", "audience": "nexus-runtime", "effect_id": verification_id}, "verify-human-auth-" + verification_id, approval_id=approval_id, payload_integrity_hash=payload_hash)
        for object_id in evidence:
            self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": object_id, "action": "VERIFY", "audience": "nexus-runtime"}, f"verify-human-evidence-{verification_id}-{object_id}")
        result = {"schema_id": "nexus.verification_result", "schema_version": 1, "verification_id": verification_id, "target_ref": target_ref, "verdict": "PASS", "verifier_kind": "T3_HUMAN_OR_DOMAIN", "evidence_used": evidence, "missing_evidence": [], "conflicts": [], "independence": independence, "rationale_summary": "Human/domain attestation bound to the exact target, evidence references, integrity hashes, and ApprovalDecision.", "run_id": run_id, "policy_version": self.authority.policy["policy_version"], "attester_principal_id": attester_principal_id, "approval_ref": approval_id}
        self.store._validate("nexus.verification_result@1.schema.json", result)
        operation = "record_verification_result"
        digest = self.store._request_hash(operation, result)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, "verify-" + verification_id, operation, digest) is not None:
                    conn.commit(); return result
                conn.execute("INSERT INTO verification_results(verification_id,target_ref,verdict,verifier_kind,evidence_used_json,independence_json,result_json,attester_principal_id,approval_ref,run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (verification_id, target_ref, "PASS", "T3_HUMAN_OR_DOMAIN", json.dumps(evidence), json.dumps(independence, sort_keys=True), json.dumps(result, sort_keys=True), attester_principal_id, approval_id, run_id, _now()))
                self.store._record_command(conn, "verify-" + verification_id, operation, digest, {"verification_id": verification_id, "verdict": "PASS"})
                conn.commit(); return result
            except Exception:
                conn.rollback(); raise

    def human_payload_hash(self, *, verification_id: str, target_ref: str, evidence_refs: list[str], run_id: str, independence: dict[str, str]) -> str:
        self.store._require_mode("core_read")
        refs = sorted(set([target_ref, *evidence_refs]))
        bindings = {"verification_id": verification_id, "target_ref": target_ref, "evidence_used": sorted(set(evidence_refs)), "integrity_hashes": {ref: self.store.get_object_metadata(ref)["integrity_hash"] for ref in refs}, "run_id": run_id, "verdict": "PASS", "independence": independence}
        return hashlib.sha256(json.dumps(bindings, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    def get(self, verification_id: str) -> dict:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute("SELECT result_json FROM verification_results WHERE verification_id=?", (verification_id,)).fetchone()
        if not row: raise RuntimeDenied("VERIFICATION_NOT_FOUND")
        return json.loads(row["result_json"])

    def _run(self, run_id):
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row: raise RuntimeDenied("VERIFIER_RUN_NOT_FOUND")
        return row

    def _assert_run_boundary(self, run, assertion_id, object_id):
        with self.store._connection() as conn:
            classification = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=? AND subject_type='OBJECT' AND subject_ref=?", (assertion_id, object_id)).fetchone()
        if not classification:
            raise RuntimeDenied("VERIFIER_CLASSIFICATION_MISSING")
        boundary = json.loads(run["data_boundary_json"])
        if classification["sensitivity_level"] not in boundary["allowed_classifications"] or not set(json.loads(classification["handling_tags_json"])).issubset(set(boundary["handling_tags"])):
            raise RuntimeDenied("VERIFIER_RUN_BOUNDARY_DENIED")
