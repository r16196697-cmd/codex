from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from kernel.budget.errors import BudgetExceeded
from kernel.object.errors import CommandConflict
from adapters.storage import ObjectStore


_LIMIT_FIELDS = ("amount", "model_calls", "tool_calls", "child_runs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class BudgetService:
    def __init__(self, store: ObjectStore):
        self.store = store

    def create_account(self, *, command_id: str, account_id: str, task_id: str, amount_limit: int, unit: str, model_call_limit: int, tool_call_limit: int, child_run_limit: int) -> None:
        self.store._require_mode("core_write")
        limits = {"amount": amount_limit, "model_calls": model_call_limit, "tool_calls": tool_call_limit, "child_runs": child_run_limit}
        if not account_id or not task_id or not unit or any(type(value) is not int or value < 0 for value in limits.values()):
            raise ValueError("invalid budget account limit")
        operation = "create_budget_account"
        request: dict[str, Any] = {"account_id": account_id, "task_id": task_id, "unit": unit, **limits}
        request_hash = self.store._request_hash(operation, request)
        account_record = {"schema_id": "nexus.budget_account", "schema_version": 1, "account_id": account_id, "task_id": task_id, "limit": amount_limit, "reserved": 0, "consumed": 0, "unit": unit, "model_call_limit": model_call_limit, "tool_call_limit": tool_call_limit, "child_run_limit": child_run_limit}
        self.store._validate("nexus.budget_account@1.schema.json", account_record)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                conn.execute("INSERT INTO budget_accounts(account_id,task_id,amount_limit,unit,model_call_limit,tool_call_limit,child_run_limit) VALUES(?,?,?,?,?,?,?)", (account_id, task_id, amount_limit, unit, model_call_limit, tool_call_limit, child_run_limit))
                result = {"account_id": account_id, "task_id": task_id}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def reserve(self, *, command_id: str, account_id: str, task_id: str, run_id: str, amount: int, model_calls: int = 0, tool_calls: int = 0, child_runs: int = 0) -> str:
        self.store._require_mode("core_write")
        values = {"amount": amount, "model_calls": model_calls, "tool_calls": tool_calls, "child_runs": child_runs}
        if not account_id or not task_id or not run_id or any(type(value) is not int or value < 0 for value in values.values()):
            raise ValueError("invalid reservation")
        operation = "reserve_budget"
        request = {"account_id": account_id, "task_id": task_id, "run_id": run_id, **values}
        request_hash = self.store._request_hash(operation, request)
        legacy_request_hash = self.store._request_hash(operation, {"account_id": account_id, "run_id": run_id, **values})
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                account = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (account_id,)).fetchone()
                if not account:
                    raise BudgetExceeded("BUDGET_ACCOUNT_NOT_FOUND")
                if account["task_id"] != task_id:
                    raise BudgetExceeded("BUDGET_TASK_MISMATCH")
                run = conn.execute("SELECT task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if run is not None and run["task_id"] != task_id:
                    raise BudgetExceeded("BUDGET_TASK_MISMATCH")
                prior = conn.execute("SELECT operation,request_hash,result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()
                if prior:
                    if prior["operation"] != operation or prior["request_hash"] not in {request_hash, legacy_request_hash}:
                        raise CommandConflict("COMMAND_CONFLICT")
                    replay = json.loads(prior["result_json"])
                    reservation = conn.execute("SELECT account_id,run_id,amount,model_calls,tool_calls,child_runs FROM budget_reservations WHERE reservation_id=?", (replay.get("reservation_id"),)).fetchone()
                    if not reservation or reservation["account_id"] != account_id or reservation["run_id"] != run_id or any(reservation[key] != values[key] for key in values):
                        raise CommandConflict("COMMAND_CONFLICT")
                    conn.commit()
                    return replay["reservation_id"]
                checks = (("amount", "reserved", "consumed", "amount_limit"), ("model_calls", "model_calls_reserved", "model_calls_consumed", "model_call_limit"), ("tool_calls", "tool_calls_reserved", "tool_calls_consumed", "tool_call_limit"), ("child_runs", "child_runs_reserved", "child_runs_consumed", "child_run_limit"))
                for requested, reserved, consumed, limit in checks:
                    if account[reserved] + account[consumed] + values[requested] > account[limit]:
                        raise BudgetExceeded("BUDGET_EXCEEDED")
                reservation_id = "bres_" + uuid.uuid4().hex
                now = _now()
                reservation = {"schema_id": "nexus.budget_reservation", "schema_version": 1, "reservation_id": reservation_id, "account_id": account_id, "run_id": run_id, "amount": amount, "unit": account["unit"], "model_calls": model_calls, "tool_calls": tool_calls, "child_runs": child_runs, "state": "RESERVED", "command_id": command_id}
                self.store._validate("nexus.budget_reservation@1.schema.json", reservation)
                conn.execute("INSERT INTO budget_reservations(reservation_id,account_id,run_id,amount,model_calls,tool_calls,child_runs,state,command_id,created_at) VALUES(?,?,?,?,?,?,?,'RESERVED',?,?)", (reservation_id, account_id, run_id, amount, model_calls, tool_calls, child_runs, command_id, now))
                conn.execute("UPDATE budget_accounts SET reserved=reserved+?,model_calls_reserved=model_calls_reserved+?,tool_calls_reserved=tool_calls_reserved+?,child_runs_reserved=child_runs_reserved+? WHERE account_id=?", (amount, model_calls, tool_calls, child_runs, account_id))
                self._append_ledger(conn, reservation_id, command_id, "RESERVED", amount, now)
                self.store._record_command(conn, command_id, operation, request_hash, {"reservation_id": reservation_id, "state": "RESERVED"})
                conn.commit()
                return reservation_id
            except Exception:
                conn.rollback()
                raise

    def settle(self, *, command_id: str, reservation_id: str, actual_amount: int) -> None:
        self.store._require_mode("core_write")
        self._finish(command_id=command_id, reservation_id=reservation_id, actual_amount=actual_amount, action="CONSUMED")

    def release(self, *, command_id: str, reservation_id: str) -> None:
        self.store._require_mode("core_write")
        self._finish(command_id=command_id, reservation_id=reservation_id, actual_amount=0, action="RELEASED")

    def _finish(self, *, command_id: str, reservation_id: str, actual_amount: int, action: str) -> None:
        if type(actual_amount) is not int or actual_amount < 0:
            raise ValueError("actual_amount must be a nonnegative integer")
        operation = "finish_budget_reservation"
        request = {"reservation_id": reservation_id, "action": action, "actual_amount": actual_amount}
        request_hash = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return
                row = conn.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if not row or row["state"] != "RESERVED":
                    raise BudgetExceeded("RESERVATION_NOT_ACTIVE")
                if actual_amount > row["amount"] or (action == "RELEASED" and actual_amount != 0):
                    raise BudgetExceeded("SETTLEMENT_EXCEEDS_RESERVATION")
                account_id = row["account_id"]
                new_state = "CONSUMED" if action == "CONSUMED" else "RELEASED"
                conn.execute("UPDATE budget_reservations SET state=? WHERE reservation_id=? AND state='RESERVED'", (new_state, reservation_id))
                conn.execute("UPDATE budget_accounts SET reserved=reserved-?,consumed=consumed+?,model_calls_reserved=model_calls_reserved-?,model_calls_consumed=model_calls_consumed+?,tool_calls_reserved=tool_calls_reserved-?,tool_calls_consumed=tool_calls_consumed+?,child_runs_reserved=child_runs_reserved-?,child_runs_consumed=child_runs_consumed+? WHERE account_id=?", (row["amount"], actual_amount, row["model_calls"], row["model_calls"] if action == "CONSUMED" else 0, row["tool_calls"], row["tool_calls"] if action == "CONSUMED" else 0, row["child_runs"], row["child_runs"] if action == "CONSUMED" else 0, account_id))
                now = _now()
                self._append_ledger(conn, reservation_id, command_id, action, actual_amount, now)
                self.store._record_command(conn, command_id, operation, request_hash, {"reservation_id": reservation_id, "state": new_state, "consumed": actual_amount})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _append_ledger(self, conn, reservation_id: str, command_id: str, action: str, amount: int, created_at: str) -> None:
        ledger_seq = conn.execute("SELECT COALESCE(MAX(ledger_seq),0)+1 FROM budget_ledger").fetchone()[0]
        entry = {"schema_id": "nexus.budget_ledger", "schema_version": 1, "ledger_seq": ledger_seq, "reservation_id": reservation_id, "command_id": command_id, "action": action, "amount": amount, "created_at": created_at}
        self.store._validate("nexus.budget_ledger@1.schema.json", entry)
        conn.execute("INSERT INTO budget_ledger(ledger_seq,reservation_id,command_id,action,amount,created_at) VALUES(?,?,?,?,?,?)", (ledger_seq, reservation_id, command_id, action, amount, created_at))
