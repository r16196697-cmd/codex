from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from kernel.purge.journal import IndependentPurgeJournal
from kernel.runtime.errors import RuntimeDenied


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PurgeService:
    def __init__(self, store, authority, memory, *, independent_journal_path: str | Path):
        self.store, self.authority, self.memory = store, authority, memory
        self.journal = IndependentPurgeJournal(independent_journal_path, store.data_root)

    def plan(self, *, command_id: str, plan_id: str, target_refs: list[str]) -> dict:
        targets = sorted(set(target_refs))
        if not targets:
            raise RuntimeDenied("PURGE_TARGETS_REQUIRED")
        with self.store._connection() as conn:
            existing = {row[0] for row in conn.execute(
                f"SELECT object_id FROM objects WHERE object_id IN ({','.join('?' for _ in targets)})", targets
            )}
        if existing != set(targets):
            raise RuntimeDenied("PURGE_TARGET_NOT_FOUND")
        closure = self._closure(targets)
        body = {"schema_id": "nexus.purge_plan", "schema_version": 1, "plan_id": plan_id, "target_refs": targets, "descendant_refs": sorted(set(closure) - set(targets)), "affected_indexes": ["raw_history", "admitted_memory"], "planned_actions": ["QUIESCE_RUNS", "RECONCILE_EFFECTS", "DELETE_PAYLOADS", "DELETE_INDEX_ROWS", "REDACT_DERIVED_METADATA", "VERIFY_UNAVAILABLE"], "lineage_revision": self._lineage_revision(), "created_at": _now(), "policy_version": self.authority.policy["policy_version"]}
        body["plan_hash"] = hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()
        self.store._validate("nexus.purge_plan@1.schema.json", body)
        digest = self.store._request_hash("create_purge_plan", body)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, "create_purge_plan", digest) is not None:
                    conn.commit()
                    return body
                self.store._record_command(conn, command_id, "create_purge_plan", digest, {"plan_id": plan_id, "plan_hash": body["plan_hash"]})
                conn.execute("INSERT INTO purge_plan_records VALUES(?,?,?,?,?,?)", (plan_id, body["plan_hash"], body["lineage_revision"], _canon(body), command_id, _now()))
                conn.commit()
                return body
            except Exception:
                conn.rollback()
                raise

    def execute(self, *, command_id: str, record_id: str, barrier_id: str, plan: dict, grant_id: str, task_id: str, approval_id: str, quiesce_run=None) -> dict:
        self.store._validate("nexus.purge_plan@1.schema.json", plan)
        body = dict(plan)
        claimed_hash = body.pop("plan_hash")
        if hashlib.sha256(_canon(body).encode("utf-8")).hexdigest() != claimed_hash:
            raise RuntimeDenied("PURGE_PLAN_HASH_MISMATCH")
        auth_request = {"task": task_id, "resource": plan["plan_id"], "action": "PURGE_EXECUTE", "audience": "nexus-runtime", "effect_id": record_id}
        self.authority.evaluate_authorization(grant_id, auth_request, command_id + "-authorize", approval_id=approval_id, payload_integrity_hash=claimed_hash)
        with self.store._connection() as conn:
            persisted_plan = conn.execute("SELECT plan_hash,plan_json FROM purge_plan_records WHERE plan_id=?", (plan["plan_id"],)).fetchone()
        if not persisted_plan or persisted_plan["plan_hash"] != claimed_hash or json.loads(persisted_plan["plan_json"]) != plan:
            raise RuntimeDenied("PURGE_PLAN_NOT_PERSISTED_OR_MISMATCHED")
        closure = self._closure(plan["target_refs"])
        if sorted(set(closure) - set(plan["target_refs"])) != plan["descendant_refs"] or self._lineage_revision() != plan["lineage_revision"]:
            raise RuntimeDenied("PURGE_PLAN_STALE")
        protected = sorted(set(plan["target_refs"]) | set(plan["descendant_refs"]))
        operation = "execute_purge"
        request = {"record_id": record_id, "barrier_id": barrier_id, "plan_hash": claimed_hash}
        digest = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, digest)
            prior_execution = conn.execute("SELECT status,unresolved_json,record_json FROM purge_execution_records WHERE record_id=?", (record_id,)).fetchone()
        if prior is not None:
            return prior
        if prior_execution and prior_execution["status"] == "COMPLETED":
            return {"record_id": record_id, "status": "COMPLETED", "purged_refs": protected}
        if prior_execution and prior_execution["status"] == "PARTIAL":
            return {"record_id": record_id, "status": "PARTIAL", "unresolved_items": json.loads(prior_execution["unresolved_json"])}
        if not prior_execution:
            # Journal first: a crash before the SQLite barrier only creates a conservative restore hold, never an unlogged purge.
            self.journal.append(action="BARRIER_INSTALLED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected)
            self._install_barrier(command_id, record_id, barrier_id, plan, protected, digest)
        active_runs = self._active_runs(protected)
        unresolved = []
        for run_id in active_runs:
            requested = quiesce_run is not None and bool(quiesce_run(run_id))
            if not requested or run_id in self._active_runs(protected):
                unresolved.append("ACTIVE_RUN:" + run_id)
        unresolved.extend("UNKNOWN_EFFECT:" + effect_id for effect_id in self._unknown_effects(protected))
        if unresolved:
            return self._mark_partial(command_id, record_id, barrier_id, plan, protected, unresolved, digest)
        if self._lineage_revision() != plan["lineage_revision"] or self._closure(plan["target_refs"]) != protected:
            return self._mark_partial(command_id, record_id, barrier_id, plan, protected, ["LINEAGE_CHANGED_AFTER_BARRIER"], digest)
        self._purge_payloads_and_indexes(protected)
        try:
            self.journal.append(action="BARRIER_RELEASED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected)
        except Exception:
            return self._mark_partial(command_id + "-journal", record_id, barrier_id, plan, protected, ["INDEPENDENT_JOURNAL_RELEASE_WRITE_FAILED"], self.store._request_hash("execute_purge_partial", request))
        return self._release(command_id, record_id, barrier_id, plan, protected, digest)

    def replay_independent_journal(self) -> dict:
        journal = self.journal.read()
        latest = {}
        for row in journal:
            if row.get("action") not in {"BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"}:
                raise RuntimeDenied("PURGE_JOURNAL_ACTION_INVALID")
            latest[row["barrier_id"]] = row
        applied = 0
        for barrier_id, row in latest.items():
            refs = row["protected_refs"]
            released = row["action"] == "BARRIER_RELEASED"
            # Re-establish an active barrier before touching restored payloads or indexes.
            self._restore_barrier_projection(barrier_id, row, refs, "ACTIVE")
            if released:
                self._purge_payloads_and_indexes(refs)
                status = "RELEASED"
                applied += len(refs)
            else:
                status = "PARTIAL"
            self._restore_barrier_projection(barrier_id, row, refs, status)
        for event in journal:
            self._replay_ledger_event(event["barrier_id"], event)
        return {"barriers": len(latest), "purged_refs_checked": applied, "held_refs": sum(len(row["protected_refs"]) for row in latest.values() if row["action"] != "BARRIER_RELEASED"), "normal_allowed": all(row["action"] == "BARRIER_RELEASED" for row in latest.values())}

    def _install_barrier(self, command_id, record_id, barrier_id, plan, refs, request_hash):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._lineage_revision(conn) != plan["lineage_revision"] or self._closure(plan["target_refs"], conn) != refs:
                    raise RuntimeDenied("PURGE_PLAN_STALE_AT_BARRIER_INSTALL")
                barrier = {"schema_id":"nexus.purge_barrier","schema_version":1,"barrier_id":barrier_id,"plan_id":plan["plan_id"],"protected_refs":refs,"lineage_revision":plan["lineage_revision"],"status":"ACTIVE","active_run_refs":self._active_runs(refs, conn),"created_at":_now()}
                self.store._validate("nexus.purge_barrier@1.schema.json", barrier)
                conn.execute("INSERT INTO purge_barriers VALUES(?,?,?,'ACTIVE',?)", (barrier_id, plan["plan_id"], plan["lineage_revision"], barrier["created_at"]))
                conn.executemany("INSERT INTO purge_barrier_refs VALUES(?,?)", ((barrier_id, ref) for ref in refs))
                started_at = _now()
                record = self._record_document(record_id, plan, barrier_id, "RUNNING", [], started_at)
                conn.execute("INSERT INTO purge_execution_records VALUES(?,?,?,'RUNNING','[]',?,?,NULL)", (record_id, plan["plan_id"], barrier_id, _canon(record), started_at))
                conn.executemany("INSERT INTO purge_execution_refs VALUES(?,?,NULL,NULL)", ((record_id, ref) for ref in refs))
                self._ledger(conn, command_id + "-barrier", barrier_id, "BARRIER_INSTALLED")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _mark_partial(self, command_id, record_id, barrier_id, plan, refs, unresolved, request_hash):
        if not unresolved:
            raise RuntimeDenied("PURGE_PARTIAL_REQUIRES_UNRESOLVED_ITEM")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE purge_barriers SET status='PARTIAL' WHERE barrier_id=? AND status IN ('ACTIVE','PARTIAL')", (barrier_id,))
                started_at = conn.execute("SELECT started_at FROM purge_execution_records WHERE record_id=?", (record_id,)).fetchone()[0]
                record = self._record_document(record_id, plan, barrier_id, "PARTIAL", unresolved, started_at)
                conn.execute("UPDATE purge_execution_records SET status='PARTIAL',unresolved_json=?,record_json=? WHERE record_id=?", (_canon(unresolved), _canon(record), record_id))
                self._ledger(conn, command_id + "-partial", barrier_id, "BARRIER_PARTIAL")
                result = {"record_id":record_id,"status":"PARTIAL","unresolved_items":unresolved}
                self.store._record_command(conn, command_id, "execute_purge", request_hash, result)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.journal.append(action="BARRIER_PARTIAL", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=plan["plan_hash"], lineage_revision=plan["lineage_revision"], protected_refs=refs)
        return result

    def _purge_payloads_and_indexes(self, refs):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for ref in sorted(set(refs)):
                    row = conn.execute("SELECT payload_uri FROM object_envelopes WHERE object_id=?", (ref,)).fetchone()
                    if row:
                        self.store._payload_path(row["payload_uri"]).unlink(missing_ok=True)
                    self.memory.purge_refs(conn, [ref])
                    conn.execute("UPDATE object_states SET payload_state='PURGED',validity='INVALIDATED',lifecycle='RETIRED' WHERE object_id=?", (ref,))
                    conn.execute("DELETE FROM object_envelopes WHERE object_id=?", (ref,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _release(self, command_id, record_id, barrier_id, plan, refs, request_hash):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE purge_barriers SET status='RELEASED' WHERE barrier_id=? AND status='ACTIVE'", (barrier_id,))
                started_at = conn.execute("SELECT started_at FROM purge_execution_records WHERE record_id=?", (record_id,)).fetchone()[0]
                completed_at = _now()
                record = self._record_document(record_id, plan, barrier_id, "COMPLETED", [], started_at, completed_at)
                conn.execute("UPDATE purge_execution_records SET status='COMPLETED',unresolved_json='[]',record_json=?,completed_at=? WHERE record_id=?", (_canon(record), completed_at, record_id))
                self._ledger(conn, command_id + "-released", barrier_id, "BARRIER_RELEASED")
                result = {"record_id":record_id,"status":"COMPLETED","purged_refs":refs}
                self.store._record_command(conn, command_id, "execute_purge", request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def _restore_barrier_projection(self, barrier_id, event, refs, status):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT status FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone()
                if not row:
                    conn.execute("INSERT INTO purge_barriers VALUES(?,?,?,'ACTIVE',?)", (barrier_id, event["plan_id"], event.get("lineage_revision", 0), _now()))
                existing = [ref for ref in refs if conn.execute("SELECT 1 FROM objects WHERE object_id=?", (ref,)).fetchone()]
                conn.executemany("INSERT OR IGNORE INTO purge_barrier_refs VALUES(?,?)", ((barrier_id, ref) for ref in existing))
                conn.execute("UPDATE purge_barriers SET status=? WHERE barrier_id=?", (status, barrier_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _ledger(self, conn, command_id, barrier_id, action):
        seq = conn.execute("SELECT COALESCE(MAX(ledger_seq),0)+1 FROM purge_ledger").fetchone()[0]
        entry = {"schema_id":"nexus.purge_ledger","schema_version":1,"ledger_seq":seq,"command_id":command_id,"barrier_id":barrier_id,"action":action,"created_at":_now()}
        self.store._validate("nexus.purge_ledger@1.schema.json", entry)
        conn.execute("INSERT INTO purge_ledger(ledger_seq,command_id,barrier_id,action,created_at) VALUES(?,?,?,?,?)", (seq,command_id,barrier_id,action,entry["created_at"]))

    def _record_document(self, record_id, plan, barrier_id, status, unresolved, started_at, completed_at=None):
        record = {"schema_id":"nexus.purge_record","schema_version":1,"record_id":record_id,"plan_id":plan["plan_id"],"plan_hash":plan["plan_hash"],"barrier_id":barrier_id,"status":status,"unresolved_items":unresolved,"started_at":started_at}
        if completed_at is not None:
            record["completed_at"] = completed_at
        self.store._validate("nexus.purge_record@1.schema.json", record)
        return record

    def _replay_ledger_event(self, barrier_id, event):
        command_id = "purge-journal-" + event["record_hash"]
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if not conn.execute("SELECT 1 FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone():
                    raise RuntimeDenied("PURGE_BARRIER_MISSING_DURING_LEDGER_REPLAY")
                if not conn.execute("SELECT 1 FROM purge_ledger WHERE command_id=?", (command_id,)).fetchone():
                    seq = conn.execute("SELECT COALESCE(MAX(ledger_seq),0)+1 FROM purge_ledger").fetchone()[0]
                    entry = {"schema_id":"nexus.purge_ledger","schema_version":1,"ledger_seq":seq,"command_id":command_id,"barrier_id":barrier_id,"action":event["action"],"created_at":_now()}
                    self.store._validate("nexus.purge_ledger@1.schema.json", entry)
                    conn.execute("INSERT INTO purge_ledger(ledger_seq,command_id,barrier_id,action,created_at) VALUES(?,?,?,?,?)", (seq, command_id, barrier_id, event["action"], entry["created_at"]))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _closure(self, targets, conn=None):
        own = conn is None
        if own: conn = self.store._connect()
        try:
            found = set(targets)
            changed = True
            while changed:
                changed = False
                marks = ",".join("?" for _ in found)
                if not marks: break
                rows = conn.execute(f"SELECT from_id FROM object_relations WHERE to_id IN ({marks}) AND relation_type IN ('derived_from','generated_from','supersedes')", tuple(found)).fetchall()
                extras = {row[0] for row in rows} - found
                if extras: found.update(extras); changed = True
                marks = ",".join("?" for _ in found)
                rows = conn.execute(f"SELECT c.claim_ref FROM memory_candidates c LEFT JOIN memory_candidate_evidence e USING(candidate_id) WHERE c.claim_ref IN ({marks}) OR e.evidence_object_id IN ({marks})", (*found, *found)).fetchall()
                extras = {row[0] for row in rows} - found
                if extras: found.update(extras); changed = True
            return sorted(found)
        finally:
            if own: conn.close()

    def _lineage_revision(self, conn=None):
        own = conn is None
        if own: conn = self.store._connect()
        try: return conn.execute("SELECT COUNT(*) FROM object_relations").fetchone()[0]
        finally:
            if own: conn.close()

    def _active_runs(self, refs, conn=None):
        own = conn is None
        if own: conn = self.store._connect()
        try:
            marks = ",".join("?" for _ in refs)
            if not marks: return []
            rows = conn.execute(f"SELECT DISTINCT r.run_id FROM runs r JOIN run_manifest_inputs i USING(run_id) WHERE r.status IN ('READY','RUNNING','WAITING','VERIFYING') AND i.input_object_id IN ({marks})", tuple(refs)).fetchall()
            return sorted(row["run_id"] for row in rows)
        finally:
            if own: conn.close()

    def _unknown_effects(self, refs):
        marks = ",".join("?" for _ in refs)
        if not marks: return []
        with self.store._connection() as conn:
            rows = conn.execute(f"SELECT effect_id FROM effects WHERE payload_object_ref IN ({marks}) AND (effect_outcome='UNKNOWN' OR execution_state='COMMITTING')", tuple(refs)).fetchall()
        return sorted(row[0] for row in rows)
