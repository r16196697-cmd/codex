from __future__ import annotations

import json
from datetime import datetime, timezone

from kernel.runtime.errors import RuntimeDenied


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _expiry(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        normalized = parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeDenied("MEMORY_EXPIRY_INVALID") from exc
    if normalized <= datetime.now(timezone.utc):
        raise RuntimeDenied("MEMORY_EXPIRY_INVALID")
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


class MemoryService:
    def __init__(self, store, authority, verifier):
        self.store, self.authority, self.verifier = store, authority, verifier

    def retain_raw(self, *, command_id: str, object_id: str, run_id: str, expires_at: str | None = None) -> None:
        expires_at = _expiry(expires_at)
        run = self._run(run_id)
        metadata = self.store.get_object_metadata(object_id)
        if metadata.get("payload_state") != "AVAILABLE": raise RuntimeDenied("RAW_HISTORY_OBJECT_UNAVAILABLE")
        self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": object_id, "action": "MEMORY_RETAIN", "audience": "nexus-runtime"}, command_id + "-authorize")
        text = self.store.get_payload(object_id).decode("utf-8", errors="strict")
        request = {"object_id": object_id, "run_id": run_id, "expires_at": expires_at}
        digest = self.store._request_hash("retain_raw_history", request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, "retain_raw_history", digest) is not None:
                    conn.commit(); return
                self.store._assert_unbarred(conn, [object_id])
                self._assert_classification(conn, object_id, metadata["classification_assertion_ref"])
                self._assert_run_boundary(conn, run_id, object_id, metadata["classification_assertion_ref"])
                if expires_at and expires_at <= _now(): raise RuntimeDenied("RAW_HISTORY_EXPIRY_INVALID")
                prior = conn.execute("SELECT 1 FROM raw_history_rows WHERE object_id=?", (object_id,)).fetchone()
                if not prior:
                    cursor = conn.execute("INSERT INTO raw_history_rows(object_id,classification_assertion_ref,expires_at,created_at) VALUES(?,?,?,?)", (object_id, metadata["classification_assertion_ref"], expires_at, _now()))
                    conn.execute("INSERT INTO raw_history_fts(rowid,body) VALUES(?,?)", (cursor.lastrowid, text))
                self.store._record_command(conn, command_id, "retain_raw_history", digest, {"object_id": object_id})
                conn.commit()
            except Exception:
                conn.rollback(); raise

    def create_candidate(self, *, command_id: str, candidate_id: str, claim_ref: str, evidence_refs: list[str], owner: str, classification_assertion_ref: str, verification_ref: str, review_trigger: str, expires_at: str | None = None, conflicts: list[str] | None = None) -> dict:
        expires_at = _expiry(expires_at)
        verification = self.verifier.get(verification_ref)
        refs = sorted(set(evidence_refs))
        if verification["target_ref"] != claim_ref or sorted(set(verification["evidence_used"])) != refs:
            raise RuntimeDenied("MEMORY_VERIFICATION_BINDING_MISMATCH")
        for object_id in [claim_ref, *refs]:
            self.store.verify_object(object_id)
        conflict_refs = sorted(set(conflicts or []))
        claim_meta = self.store.get_object_metadata(claim_ref)
        if classification_assertion_ref != claim_meta.get("classification_assertion_ref"):
            raise RuntimeDenied("MEMORY_CANDIDATE_CLASSIFICATION_MISMATCH")
        run = self._run(verification["run_id"])
        if verification["verifier_kind"] == "T3_HUMAN_OR_DOMAIN":
            payload_hash = self.verifier.human_payload_hash(verification_id=verification_ref, target_ref=claim_ref, evidence_refs=refs, run_id=verification["run_id"], independence=verification["independence"])
            self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": claim_ref, "action": "VERIFY", "audience": "nexus-runtime", "effect_id": verification_ref}, f"memory-t3-revalidate-{candidate_id}", approval_id=verification["approval_ref"], payload_integrity_hash=payload_hash)
        for object_id in sorted(set([candidate_id, claim_ref, *refs])):
            self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": object_id, "action": "MEMORY_ADMIT", "audience": "nexus-runtime"}, f"memory-admit-auth-{candidate_id}-{object_id}")
        # T1 object-integrity success is not semantic proof of a memory claim.
        # The v0.1 truth policy admits only approved T2/T3 evidence with independent
        # evidence and method; everything weaker remains quarantined.
        truth_supported = verification["verdict"] == "PASS" and verification["verifier_kind"] in {"T2_AUTHORITATIVE", "T3_HUMAN_OR_DOMAIN"} and bool(verification.get("approval_ref")) and not verification["missing_evidence"] and not verification["conflicts"] and not conflict_refs
        admissible = truth_supported and verification["independence"]["evidence_independence"] == "INDEPENDENT" and verification["independence"]["method_independence"] == "INDEPENDENT"
        truth_state = "VERIFIED" if admissible else "UNKNOWN" if verification["verdict"] == "INCONCLUSIVE" or conflict_refs or verification["conflicts"] else "INFERRED"
        status = "ADMITTED" if admissible else "QUARANTINED"
        candidate = {"schema_id": "nexus.memory_candidate", "schema_version": 1, "candidate_id": candidate_id, "claim_ref": claim_ref, "evidence_refs": refs, "conflicts": conflict_refs, "truth_state": truth_state, "owner": owner, "classification_assertion_ref": classification_assertion_ref, "verification_ref": verification_ref, "status": status, "review_trigger": review_trigger}
        if expires_at: candidate["expires_at"] = expires_at
        self.store._validate("nexus.memory_candidate@1.schema.json", candidate)
        digest = self.store._request_hash("create_memory_candidate", candidate)
        meta = claim_meta
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, "create_memory_candidate", digest) is not None:
                    conn.commit(); return candidate
                self.store._assert_unbarred(conn, [claim_ref, *refs])
                self._assert_classification(conn, claim_ref, meta["classification_assertion_ref"])
                self._assert_run_boundary(conn, verification["run_id"], claim_ref, meta["classification_assertion_ref"])
                for object_id in refs:
                    class_ref = self.store.get_object_metadata(object_id)["classification_assertion_ref"]
                    self._assert_classification(conn, object_id, class_ref)
                    self._assert_run_boundary(conn, verification["run_id"], object_id, class_ref)
                conn.execute("INSERT INTO memory_candidates VALUES(?,?,?,?,?,?,?,?,?,?)", (candidate_id, claim_ref, owner, classification_assertion_ref, verification_ref, truth_state, status, json.dumps(candidate, sort_keys=True), _now(), expires_at))
                conn.executemany("INSERT INTO memory_candidate_evidence VALUES(?,?)", ((candidate_id, item) for item in refs))
                if status == "ADMITTED":
                    self._index_admitted(conn, candidate_id, claim_ref, classification_assertion_ref, expires_at)
                self.store._record_command(conn, command_id, "create_memory_candidate", digest, {"candidate_id": candidate_id, "status": status, "truth_state": truth_state})
                conn.commit(); return candidate
            except Exception:
                conn.rollback(); raise

    def search_raw(self, *, query: str, run_id: str, limit: int = 20) -> list[dict]:
        return self._search("raw_history_fts", "raw_history_rows", query, run_id, limit)

    def search_admitted(self, *, query: str, run_id: str, limit: int = 20) -> list[dict]:
        return self._search("admitted_memory_fts", "admitted_memory_rows", query, run_id, limit)

    def _search(self, fts, backing, query, run_id, limit):
        if not query.strip() or not 1 <= limit <= 100: raise RuntimeDenied("MEMORY_QUERY_INVALID")
        with self.store._lock, self.store._connection() as conn:
            run = conn.execute("SELECT task_id,grant_id,data_boundary_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run: raise RuntimeDenied("MEMORY_SEARCH_RUN_NOT_FOUND")
            self.authority.evaluate_authorization(run["grant_id"], {"task": run["task_id"], "resource": run_id, "action": "MEMORY_SEARCH", "audience": "nexus-runtime"}, f"memory-search-auth-{run_id}-{fts}-{self.store._request_hash('memory_search', {'query': query, 'limit': limit})[:12]}")
            auth = self.authority.compute_effective_authority(run["grant_id"])
            allowed_ids = sorted(auth["resource_scope"])
            if not allowed_ids: return []
            marks = ",".join("?" for _ in allowed_ids)
            boundary = json.loads(run["data_boundary_json"])
            levels = boundary["allowed_classifications"]
            level_marks = ",".join("?" for _ in levels)
            sql = f"SELECT b.object_id,b.classification_assertion_ref FROM {fts} JOIN {backing} b ON b.row_id={fts}.rowid JOIN object_states s ON s.object_id=b.object_id JOIN classification_assertions c ON c.assertion_id=b.classification_assertion_ref WHERE {fts} MATCH ? AND b.object_id IN ({marks}) AND c.sensitivity_level IN ({level_marks}) AND NOT EXISTS (SELECT 1 FROM json_each(c.handling_tags_json) tag WHERE tag.value NOT IN (SELECT value FROM json_each(?))) AND s.payload_state='AVAILABLE' AND (b.expires_at IS NULL OR b.expires_at>?) AND NOT EXISTS (SELECT 1 FROM purge_barrier_refs pbr JOIN purge_barriers pb USING(barrier_id) WHERE pbr.object_id=b.object_id AND pb.status IN ('ACTIVE','PARTIAL')) ORDER BY rank LIMIT ?"
            rows = conn.execute(sql, (query, *allowed_ids, *levels, json.dumps(boundary["handling_tags"]), _now(), limit)).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                result["body"] = self.store.get_payload(row["object_id"]).decode("utf-8", errors="strict")
                results.append(result)
        return results

    def purge_refs(self, conn, refs: list[str]) -> None:
        for object_id in refs:
            conn.execute("DELETE FROM raw_history_rows WHERE object_id=?", (object_id,))
            conn.execute("DELETE FROM admitted_memory_rows WHERE object_id=?", (object_id,))
            conn.execute("UPDATE memory_candidates SET status='PURGED' WHERE claim_ref=? AND status<>'PURGED'", (object_id,))
            conn.execute("UPDATE memory_candidates SET status='PURGED' WHERE candidate_id IN (SELECT candidate_id FROM memory_candidate_evidence WHERE evidence_object_id=?) AND status<>'PURGED'", (object_id,))
        self._rebuild_index(conn, "raw_history_fts", "raw_history_rows")
        self._rebuild_index(conn, "admitted_memory_fts", "admitted_memory_rows")

    def _index_admitted(self, conn, candidate_id, object_id, class_ref, expires_at):
        self.store._assert_unbarred(conn, [object_id])
        body = self.store.get_payload(object_id).decode("utf-8", errors="strict")
        cursor = conn.execute("INSERT INTO admitted_memory_rows(candidate_id,object_id,classification_assertion_ref,expires_at,created_at) VALUES(?,?,?,?,?)", (candidate_id, object_id, class_ref, expires_at, _now()))
        conn.execute("INSERT INTO admitted_memory_fts(rowid,body) VALUES(?,?)", (cursor.lastrowid, body))

    def _rebuild_index(self, conn, fts: str, backing: str) -> None:
        conn.execute(f"INSERT INTO {fts}({fts}) VALUES('delete-all')")
        rows = conn.execute(f"SELECT b.row_id,b.object_id FROM {backing} b JOIN object_states s ON s.object_id=b.object_id WHERE s.payload_state='AVAILABLE' AND NOT EXISTS (SELECT 1 FROM purge_barrier_refs pbr JOIN purge_barriers pb USING(barrier_id) WHERE pbr.object_id=b.object_id AND pb.status IN ('ACTIVE','PARTIAL'))").fetchall()
        for row in rows:
            try:
                self.store.verify_object(row["object_id"])
                body = self.store.get_payload(row["object_id"]).decode("utf-8", errors="strict")
            except Exception:
                # An unreadable or corrupt payload is never rebuilt into a searchable index.
                continue
            conn.execute(f"INSERT INTO {fts}(rowid,body) VALUES(?,?)", (row["row_id"], body))

    @staticmethod
    def _assert_classification(conn, object_id, assertion_id):
        row = conn.execute("SELECT 1 FROM object_envelopes e JOIN classification_assertions c ON c.assertion_id=e.classification_assertion_ref WHERE e.object_id=? AND c.assertion_id=? AND c.subject_type='OBJECT' AND c.subject_ref=?", (object_id, assertion_id, object_id)).fetchone()
        if not row: raise RuntimeDenied("MEMORY_OBJECT_CLASSIFICATION_INVALID")

    def _assert_run_boundary(self, conn, run_id, object_id, assertion_id):
        run = conn.execute("SELECT data_boundary_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        classification = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=? AND subject_type='OBJECT' AND subject_ref=?", (assertion_id, object_id)).fetchone()
        if not run or not classification:
            raise RuntimeDenied("MEMORY_RUN_BOUNDARY_OR_CLASSIFICATION_MISSING")
        boundary = json.loads(run["data_boundary_json"])
        if classification["sensitivity_level"] not in boundary["allowed_classifications"] or not set(json.loads(classification["handling_tags_json"])).issubset(set(boundary["handling_tags"])):
            raise RuntimeDenied("MEMORY_RUN_BOUNDARY_DENIED")

    def _run(self, run_id):
        with self.store._connection() as conn:
            row = conn.execute("SELECT task_id,grant_id,status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row: raise RuntimeDenied("MEMORY_RUN_NOT_FOUND")
        if row["status"] not in {"RUNNING", "VERIFYING"}: raise RuntimeDenied("MEMORY_RUN_NOT_ACTIVE")
        return row
