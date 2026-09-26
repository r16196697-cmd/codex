from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from kernel.purge.journal import IndependentPurgeJournal
from kernel.object.errors import CommandConflict
from kernel.object_refs import known_object_refs, redact_governed_object_values, resolve_governed_object_resource
from kernel.runtime.errors import RuntimeDenied


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PurgeService:
    def __init__(self, store, authority, memory, *, independent_journal_path: str | Path):
        self.store, self.authority, self.memory = store, authority, memory
        self.journal = IndependentPurgeJournal(independent_journal_path, store.data_root)
        if self.journal.path != store.independent_purge_journal_path:
            raise ValueError("PurgeService journal must match the ObjectStore startup journal configuration")

    def plan(self, *, command_id: str, plan_id: str, task_id: str, target_refs: list[str]) -> dict:
        self.store._require_mode("core_write")
        if not command_id or not plan_id or not task_id:
            raise RuntimeDenied("PURGE_TASK_REQUIRED")
        targets = sorted(set(target_refs))
        if not targets:
            raise RuntimeDenied("PURGE_TARGETS_REQUIRED")
        operation = "create_purge_plan"
        stable_request = {"plan_id": plan_id, "task_id": task_id, "target_refs": targets}
        digest = self.store._request_hash(operation, stable_request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                command = conn.execute("SELECT operation,result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()
                if command:
                    if command["operation"] != operation:
                        raise CommandConflict("COMMAND_CONFLICT")
                    result = json.loads(command["result_json"])
                    persisted = conn.execute("SELECT plan_json,command_id FROM purge_plan_records WHERE plan_id=?", (result.get("plan_id"),)).fetchone()
                    if not persisted or persisted["command_id"] != command_id:
                        raise CommandConflict("COMMAND_CONFLICT")
                    original = json.loads(persisted["plan_json"])
                    original_request = {"plan_id": original.get("plan_id"), "task_id": original.get("task_id"), "target_refs": original.get("target_refs")}
                    if original_request != stable_request:
                        raise CommandConflict("COMMAND_CONFLICT")
                    conn.commit()
                    return original
                existing = {row[0] for row in conn.execute(
                    "SELECT object_id FROM objects WHERE object_id IN (" + ",".join("?" for _ in targets) + ")", targets
                )}
                if existing != set(targets):
                    raise RuntimeDenied("PURGE_TARGET_NOT_FOUND")
                closure = self._closure(targets, conn)
                self._assert_refs_belong_to_task(task_id, closure, conn)
                body = {"schema_id": "nexus.purge_plan", "schema_version": 2, "plan_id": plan_id, "task_id": task_id, "target_refs": targets, "descendant_refs": sorted(set(closure) - set(targets)), "affected_indexes": ["raw_history", "admitted_memory"], "planned_actions": ["QUIESCE_RUNS", "RECONCILE_EFFECTS", "DELETE_PAYLOADS", "DELETE_INDEX_ROWS", "REDACT_DERIVED_METADATA", "VERIFY_UNAVAILABLE"], "lineage_revision": self._lineage_revision(conn), "created_at": _now(), "policy_version": self.authority.policy["policy_version"]}
                body["plan_hash"] = hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()
                self.store._validate("nexus.purge_plan@2.schema.json", body)
                self.store._record_command(conn, command_id, operation, digest, {"plan_id": plan_id, "plan_hash": body["plan_hash"]})
                conn.execute("INSERT INTO purge_plan_records(plan_id,plan_hash,lineage_revision,plan_json,command_id,created_at,task_id) VALUES(?,?,?,?,?,?,?)", (plan_id, body["plan_hash"], body["lineage_revision"], _canon(body), command_id, _now(), task_id))
                conn.commit()
                return body
            except Exception:
                conn.rollback()
                raise

    def execute(self, *, command_id: str, record_id: str, barrier_id: str, plan: dict, grant_id: str, task_id: str, approval_id: str, quiesce_run=None) -> dict:
        self.store._require_mode("core_write")
        schema_version = plan.get("schema_version") if isinstance(plan, dict) else None
        if schema_version == 1:
            self.store._validate("nexus.purge_plan@1.schema.json", plan)
            raise RuntimeDenied("PURGE_PLAN_TASK_UNBOUND_LEGACY")
        self.store._validate("nexus.purge_plan@2.schema.json", plan)
        body = dict(plan)
        claimed_hash = body.pop("plan_hash")
        if hashlib.sha256(_canon(body).encode("utf-8")).hexdigest() != claimed_hash:
            raise RuntimeDenied("PURGE_PLAN_HASH_MISMATCH")
        if plan["task_id"] != task_id:
            raise RuntimeDenied("PURGE_PLAN_TASK_MISMATCH")

        # Revocation blocks new authority-bearing work; it does not erase a
        # mutation already committed under valid authority. Exact replay only
        # returns that immutable CommandLedger result and performs no purge.
        # Keep deterministic request checks in front of replay, but do not
        # re-evaluate mutable grants/approvals.
        operation = "execute_purge"
        request = {"record_id": record_id, "barrier_id": barrier_id, "plan_hash": claimed_hash}
        digest = self.store._request_hash(operation, request)
        protected = sorted(set(plan["target_refs"]) | set(plan["descendant_refs"]))
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, digest)
            prior_execution = conn.execute(
                "SELECT record_id,plan_id,barrier_id,status,unresolved_json,record_json "
                "FROM purge_execution_records WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if prior_execution:
                self._assert_execution_binding(conn, prior_execution, record_id, barrier_id, plan, claimed_hash, protected)
            release_projection_replay = bool(
                prior_execution
                and prior_execution["status"] == "PARTIAL"
                and json.loads(prior_execution["unresolved_json"]) == ["INDEPENDENT_JOURNAL_RELEASE_WRITE_FAILED"]
            )
            if prior is not None:
                if not prior_execution:
                    raise RuntimeDenied("PURGE_EXECUTION_BINDING_MISMATCH")
                self._assert_execution_binding(conn, prior_execution, record_id, barrier_id, plan, claimed_hash, protected)
                if not isinstance(prior, dict) or prior.get("record_id") != record_id or prior.get("status") not in {"PARTIAL", "COMPLETED"}:
                    raise RuntimeDenied("PURGE_EXECUTION_BINDING_MISMATCH")
                if not release_projection_replay:
                # Exact replay returns the persisted result without another
                # authorization, journal append, or payload/index mutation.
                    return prior

        auth_request = {"task": task_id, "resource": plan["plan_id"], "action": "PURGE_EXECUTE", "audience": "nexus-runtime", "effect_id": record_id}
        self.authority.evaluate_authorization(grant_id, auth_request, command_id + "-authorize", approval_id=approval_id, payload_integrity_hash=claimed_hash)
        with self.store._connection() as conn:
            persisted_plan = conn.execute("SELECT plan_hash,plan_json,task_id FROM purge_plan_records WHERE plan_id=?", (plan["plan_id"],)).fetchone()
        if not persisted_plan or persisted_plan["task_id"] is None:
            raise RuntimeDenied("PURGE_PLAN_TASK_UNBOUND_LEGACY")
        if persisted_plan["task_id"] != task_id:
            raise RuntimeDenied("PURGE_PLAN_TASK_MISMATCH")
        if persisted_plan["plan_hash"] != claimed_hash or json.loads(persisted_plan["plan_json"]) != plan:
            raise RuntimeDenied("PURGE_PLAN_NOT_PERSISTED_OR_MISMATCHED")
        closure = self._closure(plan["target_refs"])
        with self.store._connection() as conn:
            existing_barrier = conn.execute("SELECT status FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone()
        post_barrier = bool(existing_barrier and existing_barrier["status"] in {"ACTIVE", "PARTIAL", "RELEASED"})
        closure_set = set(closure)
        protected_set = set(protected)
        if post_barrier:
            with self.store._connection() as conn:
                purged = {row[0] for row in conn.execute("SELECT object_id FROM object_states WHERE object_id IN (" + ",".join("?" for _ in protected) + ") AND payload_state='PURGED'", protected)} if protected else set()
            closure_valid = closure_set.issubset(protected_set) and closure_set | purged == protected_set
        else:
            closure_valid = sorted(closure_set - set(plan["target_refs"])) == plan["descendant_refs"] and self._lineage_revision() == plan["lineage_revision"]
        if not closure_valid:
            raise RuntimeDenied("PURGE_PLAN_STALE")
        protected = sorted(set(plan["target_refs"]) | set(plan["descendant_refs"]))
        with self.store._connection() as conn:
            prior_execution = conn.execute(
                "SELECT record_id,plan_id,barrier_id,status,unresolved_json,record_json "
                "FROM purge_execution_records WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if prior_execution:
                self._assert_execution_binding(conn, prior_execution, record_id, barrier_id, plan, claimed_hash, protected)
        if prior_execution and prior_execution["status"] == "COMPLETED":
            return {"record_id": record_id, "status": "COMPLETED", "purged_refs": ["REDACTED_PURGED"] * len(protected)}
        if prior_execution and prior_execution["status"] == "PARTIAL":
            unresolved_prior = json.loads(prior_execution["unresolved_json"])
            release_projection_recovery = unresolved_prior == ["INDEPENDENT_JOURNAL_RELEASE_WRITE_FAILED"]
            # A durable release event is authoritative even if its SQLite
            # projection did not commit. Validate its full operation binding
            # before accepting it as recovery evidence.
            with self.store._connection() as conn:
                barrier = conn.execute("SELECT plan_id,lineage_revision,status FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone()
            if not barrier or barrier["plan_id"] != plan["plan_id"] or barrier["lineage_revision"] != plan["lineage_revision"] or barrier["status"] not in {"ACTIVE", "PARTIAL", "RELEASED"}:
                raise RuntimeDenied("PURGE_RELEASE_RETRY_BARRIER_MISMATCH")
            if release_projection_recovery:
                release_event = self._release_journal_event(barrier_id, plan["plan_id"], claimed_hash, plan["lineage_revision"], protected, task_id=task_id)
                if barrier["status"] == "RELEASED" and release_event is None:
                    raise RuntimeDenied("PURGE_RELEASE_RETRY_BARRIER_MISMATCH")
                current_closure = set(self._closure(plan["target_refs"]))
                with self.store._connection() as conn:
                    already_purged = {
                        row[0] for row in conn.execute(
                            "SELECT object_id FROM object_states WHERE payload_state='PURGED' AND object_id IN ("
                            + ",".join("?" for _ in protected) + ")", protected
                        )
                    } if protected else set()
                # Payload/index redaction removes ordinary lineage projections.
                # During RELEASE projection recovery, compare the surviving
                # closure plus tombstones to the originally bound closure.
                closure_matches = current_closure.issubset(set(protected)) and current_closure | already_purged == set(protected)
                if self._active_runs(protected) or self._unknown_effects(protected) or not closure_matches:
                    return {"record_id": record_id, "status": "PARTIAL", "unresolved_items": unresolved_prior}
                self._purge_payloads_and_indexes(protected)
                if release_event is None:
                    try:
                        self.journal.append(action="BARRIER_RELEASED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected, task_id=task_id)
                    except Exception:
                        return {"record_id": record_id, "status": "PARTIAL", "unresolved_items": unresolved_prior}
                return self._release(command_id, record_id, barrier_id, plan, protected, digest)
        if not prior_execution:
            # The SQLite hold is installed first. No destructive work starts
            # until the independent journal confirms the operation.
            self._install_barrier(command_id, record_id, barrier_id, plan, protected, digest)
            try:
                self.journal.append(action="BARRIER_INSTALLED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected, task_id=task_id)
                self._acknowledge_journal_projection(barrier_id, "ACTIVE")
            except Exception:
                return self._mark_partial_local(command_id, record_id, barrier_id, plan, protected, ["INDEPENDENT_JOURNAL_INSTALL_WRITE_FAILED"], digest)
        elif prior_execution["status"] == "PARTIAL" and json.loads(prior_execution["unresolved_json"]) == ["INDEPENDENT_JOURNAL_INSTALL_WRITE_FAILED"]:
            pass
        if prior_execution and prior_execution["status"] in {"RUNNING", "PARTIAL"}:
            installed = self._installation_journal_event(barrier_id, plan["plan_id"], claimed_hash, plan["lineage_revision"], protected, task_id)
            if installed is None:
                try:
                    self.journal.append(action="BARRIER_INSTALLED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected, task_id=task_id)
                    self._acknowledge_journal_projection(barrier_id, "ACTIVE")
                except Exception:
                    return self._mark_partial_local(command_id + "-journal-install", record_id, barrier_id, plan, protected, ["INDEPENDENT_JOURNAL_INSTALL_WRITE_FAILED"], self.store._request_hash("execute_purge_partial", request))
        active_runs = self._active_runs(protected)
        unresolved = []
        for run_id in active_runs:
            requested = quiesce_run is not None and bool(quiesce_run(run_id))
            if not requested or run_id in self._active_runs(protected):
                unresolved.append("ACTIVE_RUN:" + run_id)
        unresolved.extend("UNKNOWN_EFFECT:" + effect_id for effect_id in self._unknown_effects(protected))
        if unresolved:
            return self._mark_partial(command_id, record_id, barrier_id, plan, protected, unresolved, digest)
        if self._closure(plan["target_refs"]) != protected:
            return self._mark_partial(command_id, record_id, barrier_id, plan, protected, ["LINEAGE_CHANGED_AFTER_BARRIER"], digest)
        self._purge_payloads_and_indexes(protected)
        try:
            self.journal.append(action="BARRIER_RELEASED", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=claimed_hash, lineage_revision=plan["lineage_revision"], protected_refs=protected, task_id=task_id)
        except Exception:
            return self._mark_partial(command_id + "-journal", record_id, barrier_id, plan, protected, ["INDEPENDENT_JOURNAL_RELEASE_WRITE_FAILED"], self.store._request_hash("execute_purge_partial", request))
        return self._release(command_id, record_id, barrier_id, plan, protected, digest)

    @staticmethod
    def _assert_execution_binding(conn, execution, record_id, barrier_id, plan, plan_hash, protected_refs):
        """Fail closed before replay/resume when an execution ID is not this operation."""
        try:
            record = json.loads(execution["record_json"])
            execution_refs = sorted(row[0] for row in conn.execute(
                "SELECT object_id FROM purge_execution_refs WHERE record_id=?", (record_id,)
            ))
            barrier = conn.execute(
                "SELECT plan_id,lineage_revision,status FROM purge_barriers WHERE barrier_id=?", (barrier_id,)
            ).fetchone()
            barrier_refs = sorted(row[0] for row in conn.execute(
                "SELECT object_id FROM purge_barrier_refs WHERE barrier_id=?", (barrier_id,)
            ))
            matches = (
                execution["record_id"] == record_id
                and execution["plan_id"] == plan["plan_id"]
                and execution["barrier_id"] == barrier_id
                and record.get("schema_id") == "nexus.purge_record"
                and record.get("schema_version") == 1
                and record.get("record_id") == record_id
                and record.get("plan_id") == plan["plan_id"]
                and record.get("barrier_id") == barrier_id
                and record.get("plan_hash") == plan_hash
                and record.get("status") == execution["status"]
                and execution_refs == sorted(set(protected_refs))
                and barrier is not None
                and barrier["plan_id"] == plan["plan_id"]
                and barrier["lineage_revision"] == plan["lineage_revision"]
                and barrier_refs == sorted(set(protected_refs))
            )
        except (TypeError, ValueError, KeyError):
            matches = False
        if not matches:
            raise RuntimeDenied("PURGE_EXECUTION_BINDING_MISMATCH")

    def replay_independent_journal(self) -> dict:
        if self.store._current_runtime_mode() not in {"NORMAL", "RECOVERY"}:
            raise RuntimeDenied("PURGE_RECOVERY_REQUIRES_NORMAL_OR_RECOVERY_MODE")
        maintenance = self.store._recovery_maintenance() if self.store._current_runtime_mode() == "RECOVERY" else nullcontext()
        with maintenance:
            return self._replay_independent_journal()

    def _replay_independent_journal(self) -> dict:
        if not self.journal.path.is_file():
            raise RuntimeDenied("PURGE_JOURNAL_MISSING")
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
        with self.store._connection() as conn:
            local_holds = conn.execute("SELECT COUNT(*) FROM purge_barriers WHERE status IN ('ACTIVE','PARTIAL')").fetchone()[0]
            held_refs = conn.execute("SELECT COUNT(*) FROM purge_barrier_refs r JOIN purge_barriers b USING(barrier_id) WHERE b.status IN ('ACTIVE','PARTIAL')").fetchone()[0]
        return {
            "barriers": len(latest),
            "purged_refs_checked": applied,
            "held_refs": held_refs,
            "normal_allowed": local_holds == 0 and all(row["action"] == "BARRIER_RELEASED" for row in latest.values()),
            "journal_identity": self.journal.identity,
            "journal_sequence": len(journal),
            "journal_hash": journal[-1]["record_hash"] if journal else "0" * 64,
        }

    def _install_barrier(self, command_id, record_id, barrier_id, plan, refs, request_hash):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self._lineage_revision(conn) != plan["lineage_revision"] or self._closure(plan["target_refs"], conn) != refs:
                    raise RuntimeDenied("PURGE_PLAN_STALE_AT_BARRIER_INSTALL")
                barrier = {"schema_id":"nexus.purge_barrier","schema_version":1,"barrier_id":barrier_id,"plan_id":plan["plan_id"],"protected_refs":refs,"lineage_revision":plan["lineage_revision"],"status":"ACTIVE","active_run_refs":self._active_runs(refs, conn),"created_at":_now()}
                self.store._validate("nexus.purge_barrier@1.schema.json", barrier)
                conn.execute("INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at,task_id) VALUES(?,?,?,'ACTIVE',?,?)", (barrier_id, plan["plan_id"], plan["lineage_revision"], barrier["created_at"], plan.get("task_id")))
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
        self.journal.append(action="BARRIER_PARTIAL", barrier_id=barrier_id, plan_id=plan["plan_id"], plan_hash=plan["plan_hash"], lineage_revision=plan["lineage_revision"], protected_refs=refs, task_id=plan.get("task_id"))
        self._acknowledge_journal_projection(barrier_id, "PARTIAL")
        return result

    def _acknowledge_journal_projection(self, barrier_id, expected_status):
        sequence, record_hash = self.journal.verified_head()
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT status FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone()
                if not row or row["status"] != expected_status:
                    raise RuntimeDenied("PURGE_JOURNAL_PROJECTION_MISMATCH")
                self.store._acknowledge_purge_journal_head(
                    conn,
                    identity=self.journal.identity,
                    sequence=sequence,
                    record_hash=record_hash,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _mark_partial_local(self, command_id, record_id, barrier_id, plan, refs, unresolved, request_hash):
        """Persist a restrictive local hold when independent journal install failed."""
        if not unresolved:
            raise RuntimeDenied("PURGE_PARTIAL_REQUIRES_UNRESOLVED_ITEM")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE purge_barriers SET status='PARTIAL' WHERE barrier_id=? AND status IN ('ACTIVE','PARTIAL')", (barrier_id,))
                started_at = conn.execute("SELECT started_at FROM purge_execution_records WHERE record_id=?", (record_id,)).fetchone()[0]
                record = self._record_document(record_id, plan, barrier_id, "PARTIAL", unresolved, started_at)
                conn.execute("UPDATE purge_execution_records SET status='PARTIAL',unresolved_json=?,record_json=? WHERE record_id=?", (_canon(unresolved), _canon(record), record_id))
                self._ledger(conn, command_id + "-local-partial", barrier_id, "BARRIER_PARTIAL")
                result = {"record_id": record_id, "status": "PARTIAL", "unresolved_items": unresolved}
                self.store._record_command(conn, command_id, "execute_purge", request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def _purge_payloads_and_indexes(self, refs):
        with self.store._lock, self.store._connection() as conn:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for ref in sorted(set(refs)):
                    row = conn.execute("SELECT payload_uri FROM object_envelopes WHERE object_id=?", (ref,)).fetchone()
                    if row:
                        self.store._payload_path(row["payload_uri"]).unlink(missing_ok=True)
                    self.memory.purge_refs(conn, [ref])
                    conn.execute("UPDATE object_states SET payload_state='PURGED',validity='INVALIDATED',lifecycle='RETIRED' WHERE object_id=?", (ref,))
                    conn.execute("DELETE FROM object_envelopes WHERE object_id=?", (ref,))
                # This is a rebuildable manifest-input projection, not an
                # immutable audit record. Remove rows only after the referenced
                # input or manifest has become a Purged tombstone; Trace keeps
                # the binding event with its governed references redacted.
                purged_ids = [row[0] for row in conn.execute("SELECT object_id FROM object_states WHERE payload_state='PURGED'")]
                with self.store._allow_purge_redaction(purged_ids):
                    # Subtask JSON is a frozen graph projection, not an object
                    # envelope. Detach exact purged inputs and make the node
                    # permanently non-schedulable without rewriting DAG hash.
                    for subtask in conn.execute("SELECT subtask_id,node_json,input_state FROM subtasks WHERE input_state='AVAILABLE'").fetchall():
                        node = json.loads(subtask["node_json"])
                        inputs = node.get("input_object_refs", [])
                        kept = [ref for ref in inputs if ref not in purged_ids]
                        if len(kept) != len(inputs):
                            node["input_object_refs"] = kept
                            conn.execute("UPDATE subtasks SET node_json=?,input_state='PURGED_INPUT' WHERE subtask_id=?", (_canon(node), subtask["subtask_id"]))
                    conn.execute(
                        "DELETE FROM run_manifest_inputs WHERE input_object_id IN "
                        "(SELECT object_id FROM object_states WHERE payload_state='PURGED') "
                        "OR manifest_object_id IN (SELECT object_id FROM object_states WHERE payload_state='PURGED')"
                    )
                    self._redact_purged_identifiers(conn)
                conn.commit()
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and checkpoint[0] != 0:
                    raise RuntimeDenied("PURGE_WAL_CHECKPOINT_BUSY")
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _redact_purged_identifiers(conn):
        """Erase payload-derived identifiers while retaining minimum audit facts."""
        purged = {row[0] for row in conn.execute("SELECT object_id FROM object_states WHERE payload_state='PURGED'")}
        if not purged:
            return
        def resolve(value):
            return resolve_governed_object_resource(conn, value)

        def redact(value):
            return redact_governed_object_values(value, purged)

        def contains_purged(value):
            if isinstance(value, str):
                return value in purged
            if isinstance(value, list):
                return any(contains_purged(item) for item in value)
            if isinstance(value, dict):
                return any(contains_purged(item) for item in value.values())
            return False

        # Immutable audit records retain their non-identifying verdict/state,
        # while every exact object-ID value is replaced before any read/replay.
        for row in conn.execute("SELECT verification_id,target_ref,evidence_used_json,result_json FROM verification_results").fetchall():
            target = "REDACTED_PURGED" if row["target_ref"] in purged else row["target_ref"]
            evidence = redact(json.loads(row["evidence_used_json"]))
            result = redact(json.loads(row["result_json"]))
            if target != row["target_ref"] or _canon(evidence) != row["evidence_used_json"] or _canon(result) != row["result_json"]:
                conn.execute("UPDATE verification_results SET target_ref=?,evidence_used_json=?,result_json=? WHERE verification_id=?", (target, _canon(evidence), _canon(result), row["verification_id"]))

        # Detach memory projections before deleting their object-link rows.
        for row in conn.execute("SELECT * FROM memory_candidates").fetchall():
            metadata = json.loads(row["metadata_json"])
            if row["claim_ref"] in purged or contains_purged(metadata):
                claim_ref = None if row["claim_ref"] in purged else row["claim_ref"]
                conn.execute("UPDATE memory_candidates SET claim_ref=?,metadata_json=?,status='PURGED' WHERE candidate_id=?", (claim_ref, _canon(redact(metadata)), row["candidate_id"]))
        for object_id in sorted(purged):
            conn.execute("DELETE FROM memory_candidate_evidence WHERE evidence_object_id=?", (object_id,))

        # Classification assertions remain as non-identifying policy facts.
        for row in conn.execute("SELECT assertion_id,subject_type,subject_ref,reason FROM classification_assertions WHERE subject_type='OBJECT'").fetchall():
            subject_purged = row["subject_ref"] in purged
            reason_contains = any(ref in (row["reason"] or "") for ref in purged)
            if subject_purged:
                conn.execute("UPDATE classification_assertions SET subject_ref='REDACTED_PURGED',reason='[redacted by purge]' WHERE assertion_id=?", (row["assertion_id"],))
            elif reason_contains:
                conn.execute("UPDATE classification_assertions SET reason='[redacted by purge]' WHERE assertion_id=?", (row["assertion_id"],))

        # Rebuildable projections are detached/removed, never redirected to a
        # fake live object. Terminal runs may retain their historical state.
        for object_id in sorted(purged):
            conn.execute("DELETE FROM logical_refs WHERE current_object_id=?", (object_id,))
            conn.execute("DELETE FROM object_relations WHERE from_id=? OR to_id=?", (object_id, object_id))
            for attempt in conn.execute("SELECT attempt_id,task_id FROM subtask_attempts WHERE route_decision_ref=?", (object_id,)).fetchall():
                conn.execute("INSERT OR IGNORE INTO purge_redacted_attempt_routes(attempt_id,task_id,recorded_at) VALUES(?,?,?)", (attempt["attempt_id"], attempt["task_id"], _now()))
            conn.execute("UPDATE subtask_attempts SET route_decision_ref=NULL WHERE route_decision_ref=?", (object_id,))
            conn.execute("DELETE FROM route_decisions WHERE decision_object_id=?", (object_id,))
            conn.execute("UPDATE runs SET manifest_ref='REDACTED_PURGED' WHERE manifest_ref=? AND status IN ('CREATED','SUCCEEDED','FAILED','CANCELLED')", (object_id,))

        # Effect object targets are matched only to actual Object tombstones;
        # arbitrary external target strings are not treated as Nexus objects.
        effects = []
        for row in conn.execute("SELECT * FROM effects").fetchall():
            payload_purged = row["payload_object_ref"] in purged
            target_purged = resolve(row["target_ref"]) in purged
            if payload_purged or target_purged:
                effects.append((row, payload_purged, target_purged))
        effect_ids = {row["effect_id"] for row, _, _ in effects}
        for row, payload_purged, target_purged in effects:
            effect_json = json.loads(row["effect_json"])
            effect_json = redact(effect_json)
            effect_json["target_ref"] = "REDACTED_PURGED"
            if payload_purged:
                effect_json["payload_integrity_hash"] = "0" * 64
                effect_json["idempotency_key"] = "REDACTED_PURGED:" + row["effect_id"]
            conn.execute(
                "UPDATE effects SET target_ref='REDACTED_PURGED',payload_integrity_hash=?,payload_object_ref=?,idempotency_key=?,external_receipt_ref=NULL,effect_json=? WHERE effect_id=?",
                ("0" * 64 if payload_purged else row["payload_integrity_hash"],
                 None if payload_purged else row["payload_object_ref"],
                 "REDACTED_PURGED:" + row["effect_id"] if payload_purged else row["idempotency_key"],
                 _canon(effect_json), row["effect_id"]),
            )
        for row in conn.execute("SELECT * FROM approval_decisions").fetchall():
            scopes = json.loads(row["approved_scope_json"])
            effect_redacted = row["effect_id"] in effect_ids
            target_redacted = resolve(row["target_ref"]) in purged or effect_redacted
            scope_redacted = any(resolve(value) in purged for value in scopes)
            reason_redacted = any(ref in (row["reason"] or "") for ref in purged)
            request_redacted = resolve(row["request_ref"]) in purged
            provenance_redacted = resolve(row["target_ref"]) in purged or scope_redacted or effect_redacted
            if not (provenance_redacted or reason_redacted or request_redacted):
                continue
            owner = conn.execute("SELECT r.task_id FROM effects e JOIN runs r ON r.run_id=e.run_id WHERE e.effect_id=?", (row["effect_id"],)).fetchone() if row["effect_id"] else None
            if owner is None:
                linked_refs = {resolve(row["target_ref"])} if resolve(row["target_ref"]) in purged else set()
                linked_refs.update(ref for value in scopes if (ref := resolve(value)) in purged)
                owner_rows = []
                if linked_refs:
                    ref_marks = ",".join("?" for _ in linked_refs)
                    owner_rows = conn.execute(
                        f"SELECT DISTINCT b.task_id FROM purge_barrier_refs br JOIN purge_barriers b USING(barrier_id) WHERE br.object_id IN ({ref_marks}) AND b.task_id IS NOT NULL",
                        sorted(linked_refs),
                    ).fetchall()
                owner_task = owner_rows[0]["task_id"] if len(owner_rows) == 1 else None
            else:
                owner_task = owner["task_id"]
            if owner_task and provenance_redacted:
                conn.execute("INSERT OR IGNORE INTO purge_redacted_approval_owners(approval_id,task_id,recorded_at) VALUES(?,?,?)", (row["approval_id"], owner_task, _now()))
            narrowed_scopes = [value for value in scopes if resolve(value) not in purged]
            conn.execute(
                "UPDATE approval_decisions SET target_ref=?,effect_id=?,payload_integrity_hash=?,approved_scope_json=?,reason=?,request_ref=? WHERE approval_id=?",
                ("REDACTED_PURGED" if target_redacted else row["target_ref"],
                 None if effect_redacted else row["effect_id"],
                 None if effect_redacted else row["payload_integrity_hash"],
                 _canon(narrowed_scopes),
                 None if reason_redacted or effect_redacted else row["reason"],
                 None if request_redacted or effect_redacted else row["request_ref"],
                 row["approval_id"]),
            )
        # Narrow only exact object scopes; all other grant scope remains intact.
        for row in conn.execute("SELECT grant_id,resource_scope_json FROM delegation_grants").fetchall():
            old_scope = json.loads(row["resource_scope_json"])
            new_scope = [value for value in old_scope if not any(value in {ref, "object:" + ref} for ref in purged)]
            if len(new_scope) != len(old_scope):
                conn.execute("UPDATE delegation_grants SET resource_scope_json=? WHERE grant_id=?", (_canon(new_scope), row["grant_id"]))

        # Command results are committed facts, but their ordinary replay
        # projection must not expose exact purged identifiers.
        for row in conn.execute("SELECT command_id,result_json,result_state FROM command_ledger").fetchall():
            result = json.loads(row["result_json"])
            redacted = redact(result)
            if _canon(redacted) != row["result_json"]:
                conn.execute("UPDATE command_ledger SET result_json=?,result_state='PURGED_REDACTED' WHERE command_id=?", (_canon(redacted), row["command_id"]))

        refs = sorted(purged)
        marks = ",".join("?" for _ in refs)
        conn.execute(f"UPDATE purge_execution_refs SET payload_uri=NULL,integrity_hash=NULL WHERE object_id IN ({marks}) AND payload_uri IS NOT NULL", refs)
        for row in conn.execute("SELECT event_id,event_json FROM trace_events").fetchall():
            event = json.loads(row["event_json"])
            objects = list(event.get("object_refs", []))
            effects_in_trace = list(event.get("effect_refs", []))
            protected_objects = [item for item in objects if item in purged]
            protected_effects = [item for item in effects_in_trace if item in effect_ids]
            if not protected_objects and not protected_effects:
                continue
            event["object_refs"] = [item for item in objects if item not in purged]
            event["effect_refs"] = [item for item in effects_in_trace if item not in effect_ids]
            if protected_objects:
                event["object_refs"] = sorted(set(event["object_refs"] + ["REDACTED_PURGED"]))
            if protected_effects:
                event["effect_refs"] = sorted(set(event["effect_refs"] + ["REDACTED_PURGED"]))
            # Keep only the three non-identifying Effect state axes. Their enum
            # values preserve the historical state fact; all IDs, targets,
            # hashes, paths, receipts and free-form metadata are erased.
            state_values = {
                "execution_state": {"DECLARED", "PREPARED", "AUTHORIZED", "COMMITTING", "FINISHED", "CANCELLED"},
                "effect_outcome": {"UNDETERMINED", "COMMITTED", "NOT_COMMITTED", "UNKNOWN"},
                "reconciliation_status": {"NOT_REQUIRED", "PENDING", "RETRYING", "EXHAUSTED", "HUMAN_REQUIRED", "BLOCK_AND_ALERT", "RESOLVED"},
            }
            metadata = event.get("typed_metadata", {})
            event["typed_metadata"] = {
                key: value for key, value in metadata.items()
                if key in state_values and isinstance(value, str) and value in state_values[key]
            }
            conn.execute("UPDATE trace_events SET event_json=? WHERE event_id=?", (_canon(event), row["event_id"]))

    def _release(self, command_id, record_id, barrier_id, plan, refs, request_hash):
        journal_sequence, journal_hash = self.journal.verified_head()
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("UPDATE purge_barriers SET status='RELEASED' WHERE barrier_id=? AND status IN ('ACTIVE','PARTIAL')", (barrier_id,))
                started_at = conn.execute("SELECT started_at FROM purge_execution_records WHERE record_id=?", (record_id,)).fetchone()[0]
                completed_at = _now()
                record = self._record_document(record_id, plan, barrier_id, "COMPLETED", [], started_at, completed_at)
                conn.execute("UPDATE purge_execution_records SET status='COMPLETED',unresolved_json='[]',record_json=?,completed_at=? WHERE record_id=?", (_canon(record), completed_at, record_id))
                self._ledger(conn, command_id + "-released", barrier_id, "BARRIER_RELEASED")
                result = {"record_id":record_id,"status":"COMPLETED","purged_refs":["REDACTED_PURGED"] * len(refs)}
                self.store._record_command(conn, command_id, "execute_purge", request_hash, result)
                self.store._acknowledge_purge_journal_head(
                    conn,
                    identity=self.journal.identity,
                    sequence=journal_sequence,
                    record_hash=journal_hash,
                )
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
                    conn.execute("INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at,task_id) VALUES(?,?,?,'ACTIVE',?,?)", (barrier_id, event["plan_id"], event.get("lineage_revision", 0), _now(), event.get("task_id")))
                elif event.get("version") == 2:
                    persisted_task = conn.execute("SELECT task_id FROM purge_barriers WHERE barrier_id=?", (barrier_id,)).fetchone()[0]
                    if persisted_task not in {None, event.get("task_id")}:
                        raise RuntimeDenied("PURGE_JOURNAL_TASK_BINDING_MISMATCH")
                    conn.execute("UPDATE purge_barriers SET task_id=? WHERE barrier_id=? AND task_id IS NULL", (event.get("task_id"), barrier_id))
                existing = [ref for ref in refs if conn.execute("SELECT 1 FROM objects WHERE object_id=?", (ref,)).fetchone()]
                conn.executemany("INSERT OR IGNORE INTO purge_barrier_refs VALUES(?,?)", ((barrier_id, ref) for ref in existing))
                conn.execute("UPDATE purge_barriers SET status=? WHERE barrier_id=?", (status, barrier_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _release_journal_event(self, barrier_id, plan_id, plan_hash, lineage_revision, protected_refs, *, task_id=None):
        events = [row for row in self.journal.read() if row.get("barrier_id") == barrier_id and row.get("action") == "BARRIER_RELEASED"]
        if not events:
            return None
        if len(events) != 1:
            raise RuntimeDenied("PURGE_RELEASE_JOURNAL_DUPLICATE")
        event = events[0]
        expected = {
            "barrier_id": barrier_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "lineage_revision": lineage_revision,
            "protected_refs": sorted(set(protected_refs)),
        }
        if any(event.get(key) != value for key, value in expected.items()):
            raise RuntimeDenied("PURGE_RELEASE_JOURNAL_MISMATCH")
        if event.get("version") == 2 and event.get("task_id") != task_id:
            raise RuntimeDenied("PURGE_RELEASE_JOURNAL_MISMATCH")
        return event

    def _installation_journal_event(self, barrier_id, plan_id, plan_hash, lineage_revision, protected_refs, task_id):
        events = [row for row in self.journal.read() if row.get("barrier_id") == barrier_id and row.get("action") == "BARRIER_INSTALLED"]
        if not events:
            return None
        if len(events) != 1:
            raise RuntimeDenied("PURGE_INSTALL_JOURNAL_DUPLICATE")
        event = events[0]
        expected = {
            "barrier_id": barrier_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "lineage_revision": lineage_revision,
            "protected_refs": sorted(set(protected_refs)),
        }
        if any(event.get(key) != value for key, value in expected.items()):
            raise RuntimeDenied("PURGE_INSTALL_JOURNAL_MISMATCH")
        if event.get("version") == 2 and event.get("task_id") != task_id:
            raise RuntimeDenied("PURGE_INSTALL_JOURNAL_MISMATCH")
        return event

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
                # Resolve historical known-schema references that predate
                # lineage projection rows. This is intentionally schema-aware.
                governed = conn.execute(
                    "SELECT e.object_id,e.payload_uri FROM object_envelopes e "
                    "WHERE e.object_type IN ('task_contract','run_manifest')"
                ).fetchall()
                for envelope in governed:
                    try:
                        document = json.loads(self.store._payload_path(envelope["payload_uri"]).read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if found.intersection(known_object_refs(document)) and envelope["object_id"] not in found:
                        found.add(envelope["object_id"])
                        changed = True
                marks = ",".join("?" for _ in found)
                rows = conn.execute(f"SELECT c.claim_ref FROM memory_candidates c LEFT JOIN memory_candidate_evidence e USING(candidate_id) WHERE c.claim_ref IN ({marks}) OR e.evidence_object_id IN ({marks})", (*found, *found)).fetchall()
                extras = {row[0] for row in rows} - found
                if extras: found.update(extras); changed = True
            return sorted(found)
        finally:
            if own: conn.close()

    def _assert_refs_belong_to_task(self, task_id, refs, conn=None):
        refs = sorted(set(refs))
        if not refs:
            raise RuntimeDenied("PURGE_TARGETS_REQUIRED")
        marks = ",".join("?" for _ in refs)
        own = conn is None
        if own:
            conn = self.store._connect()
        try:
            rows = conn.execute(
                "SELECT o.object_id,r.task_id FROM objects o "
                "LEFT JOIN object_envelopes e USING(object_id) "
                "LEFT JOIN runs r ON r.run_id=e.created_by_run "
                f"WHERE o.object_id IN ({marks})",
                refs,
            ).fetchall()
        finally:
            if own:
                conn.close()
        if len(rows) != len(refs) or any(row["task_id"] is None for row in rows):
            raise RuntimeDenied("PURGE_PLAN_OBJECT_TASK_UNRESOLVED")
        if any(row["task_id"] != task_id for row in rows):
            raise RuntimeDenied("PURGE_PLAN_TASK_MISMATCH")

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
            active = "('READY','RUNNING','WAITING','VERIFYING')"
            rows = conn.execute(
                "SELECT DISTINCT r.run_id FROM runs r WHERE r.status IN " + active + " AND ("
                f"r.manifest_ref IN ({marks}) "
                f"OR EXISTS (SELECT 1 FROM run_manifest_inputs i WHERE i.run_id=r.run_id AND (i.input_object_id IN ({marks}) OR i.manifest_object_id IN ({marks}))) "
                f"OR EXISTS (SELECT 1 FROM subtask_attempts a WHERE a.run_id=r.run_id AND a.route_decision_ref IN ({marks})))",
                (*refs, *refs, *refs, *refs),
            ).fetchall()
            return sorted(row["run_id"] for row in rows)
        finally:
            if own: conn.close()

    def _unknown_effects(self, refs):
        protected = set(refs)
        if not protected:
            return []
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT effect_id,payload_object_ref,target_ref FROM effects "
                "WHERE effect_outcome='UNKNOWN' OR execution_state IN ('COMMITTING','DECLARED','PREPARED','AUTHORIZED')",
            ).fetchall()
            return sorted(
                row["effect_id"] for row in rows
                if row["payload_object_ref"] in protected
                or resolve_governed_object_resource(conn, row["target_ref"]) in protected
            )
