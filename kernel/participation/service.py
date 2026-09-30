"""Persisted instance-level participation mode and fail-closed disengagement."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from functools import wraps

from kernel.runtime.errors import RuntimeDenied


class NexusParticipationMode(str, Enum):
    ACTIVE = "ACTIVE"
    OBSERVE = "OBSERVE"
    BYPASS = "BYPASS"


_IN_FLIGHT_RUN_STATES = ("CREATED", "READY", "RUNNING", "WAITING", "VERIFYING")
_IN_FLIGHT_TASK_STATES = ("CREATED", "ACTIVE", "WAITING")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ParticipationModeService:
    """Manage explicit participation state without changing Runtime safety mode."""

    def __init__(self, store):
        self.store = store

    def current(self) -> dict:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT mode,updated_at,updated_by,command_id FROM participation_mode_state WHERE singleton=1"
            ).fetchone()
        if not row or row["mode"] not in {item.value for item in NexusParticipationMode}:
            raise RuntimeDenied("PARTICIPATION_MODE_STATE_INVALID")
        return dict(row) | {"scope": "INSTANCE"}

    @staticmethod
    def _blockers(conn) -> dict[str, int]:
        marks = ",".join("?" for _ in _IN_FLIGHT_RUN_STATES)
        task_marks = ",".join("?" for _ in _IN_FLIGHT_TASK_STATES)
        active_tasks = conn.execute(
            f"SELECT COUNT(*) FROM tasks WHERE status IN ({task_marks})", _IN_FLIGHT_TASK_STATES
        ).fetchone()[0]
        active_runs = conn.execute(
            f"SELECT COUNT(*) FROM runs WHERE status IN ({marks})", _IN_FLIGHT_RUN_STATES
        ).fetchone()[0]
        pending_effects = conn.execute(
            "SELECT COUNT(*) FROM effects WHERE effect_outcome IN ('UNDETERMINED','UNKNOWN') "
            "OR reconciliation_status NOT IN ('NOT_REQUIRED','RESOLVED')"
        ).fetchone()[0]
        approval_bound_pending = conn.execute(
            "SELECT COUNT(*) FROM effects WHERE approval_ref IS NOT NULL AND ("
            "effect_outcome IN ('UNDETERMINED','UNKNOWN') OR reconciliation_status NOT IN ('NOT_REQUIRED','RESOLVED'))"
        ).fetchone()[0]
        return {
            "active_tasks": int(active_tasks),
            "active_runs": int(active_runs),
            "pending_effects": int(pending_effects),
            "approval_or_commit_pending": int(approval_bound_pending),
        }

    def set_mode(
        self,
        *,
        mode: str,
        expected_mode: str,
        command_id: str,
        updated_by: str = "local-operator",
    ) -> dict:
        if hasattr(self.store, "_assert_writable"):
            self.store._assert_writable()
        allowed = {item.value for item in NexusParticipationMode}
        if mode not in allowed:
            raise RuntimeDenied("PARTICIPATION_MODE_INVALID")
        if expected_mode not in allowed:
            raise RuntimeDenied("PARTICIPATION_MODE_EXPECTATION_INVALID")
        if not command_id or not updated_by:
            raise RuntimeDenied("PARTICIPATION_MODE_COMMAND_INVALID")

        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                state = conn.execute(
                    "SELECT mode FROM participation_mode_state WHERE singleton=1"
                ).fetchone()
                if not state or state["mode"] not in allowed:
                    raise RuntimeDenied("PARTICIPATION_MODE_STATE_INVALID")
                previous = state["mode"]
                command = conn.execute(
                    "SELECT previous_mode,next_mode,scope,changed_at FROM participation_mode_events WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                if command:
                    if command["next_mode"] != mode or command["scope"] != "INSTANCE" or previous != mode:
                        raise RuntimeDenied("PARTICIPATION_MODE_COMMAND_CONFLICT")
                    conn.commit()
                    return {"mode": mode, "previous_mode": command["previous_mode"], "changed": True,
                            "scope": "INSTANCE", "changed_at": command["changed_at"]}
                if previous != expected_mode:
                    raise RuntimeDenied("PARTICIPATION_MODE_STALE_EXPECTATION")
                if previous == mode:
                    conn.commit()
                    return {"mode": mode, "previous_mode": previous, "changed": False, "scope": "INSTANCE"}
                if mode != NexusParticipationMode.ACTIVE.value:
                    blockers = self._blockers(conn)
                    if blockers["active_tasks"]:
                        raise RuntimeDenied("PARTICIPATION_CHANGE_BLOCKED_ACTIVE_TASKS")
                    if blockers["active_runs"]:
                        raise RuntimeDenied("PARTICIPATION_CHANGE_BLOCKED_ACTIVE_RUNS")
                    if blockers["approval_or_commit_pending"]:
                        raise RuntimeDenied("PARTICIPATION_CHANGE_BLOCKED_APPROVAL_OR_COMMIT")
                    if blockers["pending_effects"]:
                        raise RuntimeDenied("PARTICIPATION_CHANGE_BLOCKED_PENDING_EFFECTS")

                changed_at = _now()
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM participation_mode_events"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO participation_mode_events(sequence,command_id,previous_mode,next_mode,scope,changed_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (sequence, command_id, previous, mode, "INSTANCE", changed_at),
                )
                conn.execute(
                    "UPDATE participation_mode_state SET mode=?,updated_at=?,updated_by=?,command_id=? WHERE singleton=1",
                    (mode, changed_at, updated_by, command_id),
                )
                conn.commit()
                return {"mode": mode, "previous_mode": previous, "changed": True,
                        "scope": "INSTANCE", "changed_at": changed_at}
            except Exception:
                conn.rollback()
                raise

    def require_active_ingestion(self) -> None:
        mode = self.current()["mode"]
        if mode != NexusParticipationMode.ACTIVE.value:
            raise RuntimeDenied("PARTICIPATION_MODE_DISALLOWS_AUTOMATIC_INGESTION")


def active_participation_required(method):
    """Serialize Host bridge operations against mode changes, fail closed."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.store._lock:
            self.participation.require_active_ingestion()
            return method(self, *args, **kwargs)
    return guarded
