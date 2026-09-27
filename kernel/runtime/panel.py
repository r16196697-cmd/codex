"""Bounded read-only projections for the local companion panel."""

from __future__ import annotations


class PanelQueryService:
    """Expose task/run metadata only; never reads governed object payloads."""

    def __init__(self, store):
        self.store = store

    def snapshot(self, *, recent_limit: int = 12) -> dict:
        if not 1 <= recent_limit <= 50:
            raise ValueError("PANEL_RECENT_LIMIT_INVALID")
        self.store._require_mode("inspect")
        with self.store._connection() as conn:
            overview = {
                "task_count": int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
                "recorded_run_count": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
                "unfinished_task_count": int(conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
                ).fetchone()[0]),
                "blocked_task_count": int(conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='WAITING'"
                ).fetchone()[0]),
                "active_run_count": int(conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE status IN ('CREATED','READY','RUNNING','WAITING','VERIFYING')"
                ).fetchone()[0]),
                "pending_effect_count": int(conn.execute(
                    "SELECT COUNT(*) FROM effects WHERE effect_outcome IN ('UNDETERMINED','UNKNOWN') "
                    "OR reconciliation_status NOT IN ('NOT_REQUIRED','RESOLVED')"
                ).fetchone()[0]),
            }
            recent_runs = [dict(row) for row in conn.execute(
                "SELECT run_id,task_id,executor_kind,status,parent_run_id,created_at "
                "FROM runs ORDER BY created_at DESC,run_id LIMIT ?", (recent_limit,)
            )]
            tasks = [dict(row) for row in conn.execute(
                "SELECT task_id,status,created_at,root_run_id FROM tasks ORDER BY created_at DESC,task_id"
            )]
            run_rows = [dict(row) for row in conn.execute(
                "SELECT run_id,task_id,executor_kind,status,parent_run_id,created_at "
                "FROM runs ORDER BY task_id,created_at,run_id"
            )]
            attempts = [dict(row) for row in conn.execute(
                "SELECT attempt_id,task_id,subtask_id,attempt_no,run_id,outcome,requested_capability "
                "FROM subtask_attempts ORDER BY task_id,subtask_id,attempt_no"
            )]
        runs_by_task: dict[str, list[dict]] = {}
        attempts_by_task: dict[str, list[dict]] = {}
        for row in run_rows:
            runs_by_task.setdefault(row["task_id"], []).append(row)
        for row in attempts:
            attempts_by_task.setdefault(row["task_id"], []).append(row)
        for task in tasks:
            task["runs"] = runs_by_task.get(task["task_id"], [])
            task["attempts"] = attempts_by_task.get(task["task_id"], [])
        return {"overview": overview, "recent_runs": recent_runs, "tasks": tasks}
