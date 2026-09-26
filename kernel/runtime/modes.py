"""Persisted Runtime mode selection and deterministic capability gates."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from kernel.runtime.errors import RuntimeDenied


MODES = frozenset({"NORMAL", "SAFE", "STATELESS", "RECOVERY"})
_STATELESS_TRACE_TYPES = frozenset(
    {
        "nexus.run.created",
        "nexus.run.transitioned",
        "nexus.object.created",
        "nexus.effect.declared",
        "nexus.effect.prepared",
        "nexus.effect.authorized",
        "nexus.effect.commit_started",
        "nexus.effect.outcome_recorded",
        "nexus.effect.reconciled",
        "nexus.authority.denied",
        "nexus.trace.event_rejected",
        "nexus.runtime.mode_changed",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def require_mode_permission(
    mode: str,
    capability: str,
    *,
    effect_class: str | None = None,
    event_type: str | None = None,
) -> None:
    """Raise on an operation forbidden by the active operating mode.

    SAFE is deliberately stricter than the configured egress policy, and
    STATELESS keeps only minimum typed execution/security facts, never memory.
    RECOVERY is fail-closed for all v2 Core operations.
    """
    if mode not in MODES:
        raise RuntimeDenied("RUNTIME_MODE_INVALID")
    if capability == "mode_read":
        return
    if mode == "RECOVERY":
        raise RuntimeDenied("RUNTIME_RECOVERY_CORE_BYPASS")

    if capability == "memory_read":
        allowed = mode in {"NORMAL", "SAFE"}
    elif capability == "memory_write":
        allowed = mode == "NORMAL"
    elif capability == "trace_write":
        allowed = mode in {"NORMAL", "SAFE"} or (
            mode == "STATELESS" and event_type in _STATELESS_TRACE_TYPES
        )
    elif capability == "effect_commit":
        allowed = mode != "SAFE" or effect_class not in {
            "EXTERNAL_REVERSIBLE",
            "EXTERNAL_IRREVERSIBLE",
        }
    elif capability == "egress":
        allowed = mode != "SAFE"
    elif capability == "learning_write":
        allowed = mode == "NORMAL"
    elif capability in {"core_read", "core_write", "run_execute", "inspect"}:
        allowed = True
    else:
        raise RuntimeDenied("RUNTIME_CAPABILITY_UNKNOWN")

    if not allowed:
        raise RuntimeDenied("RUNTIME_MODE_DENIED")


class RuntimeModeService:
    """Command-ledgered instance mode changes and mode-policy evaluation."""

    def __init__(self, store, authority):
        self.store = store
        self.authority = authority

    def current(self) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT mode,updated_at,updated_by,command_id FROM runtime_mode_state WHERE singleton=1"
            ).fetchone()
        if not row or row["mode"] not in MODES:
            raise RuntimeDenied("RUNTIME_MODE_STATE_INVALID")
        return dict(row)

    def require(self, capability: str, *, effect_class: str | None = None, event_type: str | None = None) -> None:
        require_mode_permission(
            self.current()["mode"], capability, effect_class=effect_class, event_type=event_type
        )

    def set_mode(self, *, command_id: str, grant_id: str, task_id: str, mode: str, classification_assertion_ref: str) -> dict[str, Any]:
        if self.authority.store is not self.store:
            raise ValueError("Mode changes require AuthorityService and RuntimeModeService to share one ObjectStore")
        if mode not in MODES:
            raise RuntimeDenied("RUNTIME_MODE_INVALID")
        self.require("core_write")
        self.require("trace_write", event_type="nexus.runtime.mode_changed")
        operation = "set_runtime_mode"
        request = {"grant_id": grant_id, "task_id": task_id, "mode": mode, "classification_assertion_ref": classification_assertion_ref}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            return prior
        with self.store._lock:
            return self._set_mode_locked(
                command_id=command_id,
                grant_id=grant_id,
                task_id=task_id,
                mode=mode,
                classification_assertion_ref=classification_assertion_ref,
                operation=operation,
                request_hash=request_hash,
            )

    def _set_mode_locked(self, *, command_id, grant_id, task_id, mode, classification_assertion_ref, operation, request_hash):
        self.require("core_write")
        self.require("trace_write", event_type="nexus.runtime.mode_changed")
        self.authority.evaluate_authorization(
            grant_id,
            {
                "task": task_id,
                "resource": "runtime-mode:instance",
                "action": "RUNTIME_CONFIGURE",
                "audience": "nexus-runtime",
            },
            command_id + "-authorize",
        )
        with self.store._connection() as conn:
            task = conn.execute("SELECT root_run_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            run = conn.execute("SELECT task_id,parent_run_id,executor_kind,grant_id FROM runs WHERE run_id=?", (task["root_run_id"],)).fetchone() if task and task["root_run_id"] else None
        if not run or run["task_id"] != task_id or run["parent_run_id"] is not None or run["executor_kind"] != "ORCHESTRATOR":
            raise RuntimeDenied("MODE_TRACE_ROOT_RUN_REQUIRED")
        if run["grant_id"] != grant_id:
            raise RuntimeDenied("MODE_TRACE_GRANT_MUST_MATCH_ROOT_RUN")
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": task["root_run_id"], "action": "TRACE_APPEND", "audience": "nexus-runtime"},
            command_id + "-trace-authorize",
        )
        if self.current()["mode"] == "RECOVERY" and mode != "RECOVERY":
            raise RuntimeDenied("RECOVERY_EXIT_REQUIRES_VALIDATED_RESTORE")

        with self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store._replay_command(conn, command_id, operation, request_hash)
                if prior is not None:
                    conn.commit()
                    return prior
                state = conn.execute(
                    "SELECT mode FROM runtime_mode_state WHERE singleton=1"
                ).fetchone()
                if not state or state["mode"] not in MODES:
                    raise RuntimeDenied("RUNTIME_MODE_STATE_INVALID")
                previous = state["mode"]
                if previous == "RECOVERY" and mode != "RECOVERY":
                    raise RuntimeDenied("RECOVERY_EXIT_REQUIRES_VALIDATED_RESTORE")
                changed_at = _now()
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM runtime_mode_events"
                ).fetchone()[0]
                from kernel.run import TraceRuntime

                trace = TraceRuntime(self.store, self.authority)
                event_id = "evt-" + command_id
                trace_seq = conn.execute("SELECT COALESCE(MAX(seq_no),0)+1 FROM trace_events WHERE run_id=?", (task["root_run_id"],)).fetchone()[0]
                chain = self.authority.validate_delegation_chain(grant_id)
                event = trace._make_event(
                    conn,
                    event_id=event_id,
                    run_id=task["root_run_id"],
                    seq_no=trace_seq,
                    event_type="nexus.runtime.mode_changed",
                    actor_id=chain[-1]["granted_to"],
                    object_refs=[],
                    effect_refs=[],
                    policy_refs=[self.authority.policy["policy_version"]],
                    authority_refs=[grant_id],
                    classification_ref=classification_assertion_ref,
                    metadata={"previous_mode": previous, "next_mode": mode},
                )
                trace._insert_event(conn, event)
                result = {"mode": mode, "previous_mode": previous, "sequence": sequence, "trace_event_id": event_id, "run_id": task["root_run_id"], "trace_seq_no": trace_seq}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.execute(
                    "INSERT INTO runtime_mode_events(sequence,command_id,previous_mode,next_mode,grant_id,task_id,changed_at,request_hash) VALUES(?,?,?,?,?,?,?,?)",
                    (sequence, command_id, previous, mode, grant_id, task_id, changed_at, request_hash),
                )
                conn.execute(
                    "UPDATE runtime_mode_state SET mode=?,updated_at=?,updated_by=?,command_id=? WHERE singleton=1",
                    (mode, changed_at, grant_id, command_id),
                )
                conn.commit()
                self.store._mode_cache = mode
                return result
            except Exception:
                conn.rollback()
                raise

    def complete_validated_recovery(self, *, command_id: str, purge_service) -> dict[str, Any]:
        """Reopen NORMAL only after the handbook's recovery validations pass."""
        if purge_service.store is not self.store:
            raise ValueError("Recovery services must share one ObjectStore")
        operation = "complete_validated_recovery"
        request = {"expected_mode": "RECOVERY", "validation_profile": "migration-purge-integrity-fts-v1"}
        request_hash = self.store._request_hash(operation, request)
        mode = self.current()["mode"]
        if mode != "RECOVERY":
            with self.store._connection() as conn:
                prior = self.store._replay_command(conn, command_id, operation, request_hash)
            if prior is not None:
                return prior
            raise RuntimeDenied("VALIDATED_RECOVERY_REQUIRES_RECOVERY_MODE")

        with self.store._recovery_maintenance():
            with self.store._connection() as conn:
                prior = self.store._replay_command(conn, command_id, operation, request_hash)
            if prior is not None:
                return prior
            self.store._validate_recovery_database()
            purge_report = purge_service.replay_independent_journal()
            if not purge_report["normal_allowed"] or purge_report["held_refs"]:
                raise RuntimeDenied("RECOVERY_PURGE_BARRIER_OR_LEDGER_UNRESOLVED")

            with self.store._connection() as conn:
                available = conn.execute(
                    "SELECT e.object_id,e.payload_uri,e.integrity_hash FROM object_envelopes e "
                    "JOIN object_states s USING(object_id) WHERE s.payload_state='AVAILABLE' AND s.validity='VALID' AND s.lifecycle='ACTIVE'"
                ).fetchall()
                for row in available:
                    path = self.store._payload_path(row["payload_uri"])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row["integrity_hash"]:
                        raise RuntimeDenied("RECOVERY_OBJECT_INTEGRITY_VALIDATION_FAILED")

                for barrier in conn.execute("SELECT barrier_id,status FROM purge_barriers").fetchall():
                    if barrier["status"] != "RELEASED":
                        raise RuntimeDenied("RECOVERY_PURGE_BARRIER_UNRESOLVED")
                    refs = conn.execute("SELECT object_id FROM purge_barrier_refs WHERE barrier_id=?", (barrier["barrier_id"],)).fetchall()
                    for item in refs:
                        state = conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (item["object_id"],)).fetchone()
                        envelope = conn.execute("SELECT 1 FROM object_envelopes WHERE object_id=?", (item["object_id"],)).fetchone()
                        if (state and state["payload_state"] != "PURGED") or envelope:
                            raise RuntimeDenied("RECOVERY_PURGED_OBJECT_RESURRECTED")

                self._rebuild_text_index(conn, "raw_history_fts", "raw_history_rows")
                self._rebuild_text_index(conn, "admitted_memory_fts", "admitted_memory_rows")
                state = conn.execute("SELECT updated_by FROM runtime_mode_state WHERE singleton=1 AND mode='RECOVERY'").fetchone()
                prior_event = conn.execute(
                    "SELECT grant_id,task_id FROM runtime_mode_events WHERE next_mode='RECOVERY' ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                recovery_session = conn.execute("SELECT session_id FROM recovery_sessions WHERE status='OPEN' ORDER BY opened_at DESC LIMIT 1").fetchone()
                infrastructure_recovery = bool(state and state["updated_by"] == "nexus-core-recovery" and recovery_session)
                if not state or (not prior_event and not infrastructure_recovery):
                    raise RuntimeDenied("RECOVERY_ENTRY_AUDIT_MISSING")
                changed_at = _now()
                result = {"mode": "NORMAL", "previous_mode": "RECOVERY", "purge_report": purge_report, "validated_objects": len(available)}
                if infrastructure_recovery:
                    result["recovery_session_id"] = recovery_session["session_id"]
                conn.execute("BEGIN IMMEDIATE")
                self.store._record_command(conn, command_id, operation, request_hash, result)
                if not infrastructure_recovery:
                    sequence = conn.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM runtime_mode_events").fetchone()[0]
                    conn.execute(
                        "INSERT INTO runtime_mode_events(sequence,command_id,previous_mode,next_mode,grant_id,task_id,changed_at,request_hash) VALUES(?,?,'RECOVERY','NORMAL',?,?,?,?)",
                        (sequence, command_id, prior_event["grant_id"], prior_event["task_id"], changed_at, request_hash),
                    )
                else:
                    conn.execute("UPDATE recovery_sessions SET status='COMPLETED',completed_at=? WHERE session_id=? AND status='OPEN'", (changed_at, recovery_session["session_id"]))
                self.store._acknowledge_purge_journal_head(
                    conn,
                    identity=purge_report["journal_identity"],
                    sequence=purge_report["journal_sequence"],
                    record_hash=purge_report["journal_hash"],
                )
                conn.execute(
                    "UPDATE runtime_mode_state SET mode='NORMAL',updated_at=?,updated_by=?,command_id=? WHERE singleton=1 AND mode='RECOVERY'",
                    (changed_at, state["updated_by"], None if infrastructure_recovery else command_id),
                )
                conn.commit()
                self.store._mode_cache = "NORMAL"
                return result

    def _rebuild_text_index(self, conn, index_table: str, row_table: str) -> None:
        if index_table not in {"raw_history_fts", "admitted_memory_fts"} or row_table not in {"raw_history_rows", "admitted_memory_rows"}:
            raise RuntimeDenied("RECOVERY_INDEX_TARGET_INVALID")
        conn.execute(f"INSERT INTO {index_table}({index_table}) VALUES('delete-all')")
        rows = conn.execute(
            f"SELECT b.row_id,e.payload_uri,e.integrity_hash FROM {row_table} b "
            "JOIN object_envelopes e ON e.object_id=b.object_id "
            "JOIN object_states s ON s.object_id=e.object_id "
            "WHERE s.payload_state='AVAILABLE' AND s.validity='VALID' AND s.lifecycle='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM purge_barrier_refs pbr JOIN purge_barriers pb USING(barrier_id) "
            "WHERE pbr.object_id=e.object_id AND pb.status IN ('ACTIVE','PARTIAL'))"
        ).fetchall()
        for row in rows:
            path = self.store._payload_path(row["payload_uri"])
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != row["integrity_hash"]:
                raise RuntimeDenied("RECOVERY_INDEX_SOURCE_INTEGRITY_FAILED")
            try:
                body = payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            conn.execute(f"INSERT INTO {index_table}(rowid,body) VALUES(?,?)", (row["row_id"], body))
