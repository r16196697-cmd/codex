"""Append-only value measurements with explicit source and provenance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from kernel.runtime.errors import RuntimeDenied


_METRIC_UNITS = {
    "model_visible_input_tokens": "tokens",
    "host_total_turn_input_tokens": "tokens",
    "cached_input_tokens": "tokens",
    "cache_write_input_tokens": "tokens",
    "uncached_input_tokens": "tokens",
    "output_tokens": "tokens",
    "reasoning_tokens": "tokens",
    "latency_ms": "milliseconds",
    "context_pack_bytes": "bytes",
    "context_pack_token_count": "tokens",
    "skill_instruction_tokens": "tokens",
    "duplicate_work_reused_count": "runs",
    "duplicate_work_avoided_count": "runs",
    "actual_cost": None,
    "estimated_cost": None,
    "token_savings": "tokens",
    "nexus_influenced_execution": "boolean",
}
_PROVENANCE = frozenset({"OBSERVED", "HOST_DECLARED", "DERIVED", "UNAVAILABLE", "ESTIMATED"})


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def unavailable_metrics() -> dict:
    return {name: {"value": None, "provenance": "UNAVAILABLE", "unit": unit,
                   "basis": None, "estimation_basis": None}
            for name, unit in _METRIC_UNITS.items()}


def metric(value, provenance, *, unit=None, basis=None, estimation_basis=None) -> dict:
    if provenance not in _PROVENANCE:
        raise ValueError("METERING_PROVENANCE_INVALID")
    if provenance == "UNAVAILABLE" and value is not None:
        raise ValueError("UNAVAILABLE_METRIC_MUST_BE_NULL")
    if provenance != "UNAVAILABLE" and value is None:
        raise ValueError("AVAILABLE_METRIC_REQUIRES_VALUE")
    if provenance == "ESTIMATED":
        if not isinstance(estimation_basis, dict) or not all(
            isinstance(estimation_basis.get(key), str) and estimation_basis[key]
            for key in ("pricing_id", "pricing_version", "formula")
        ):
            raise ValueError("ESTIMATED_METRIC_BASIS_REQUIRED")
    elif estimation_basis is not None:
        raise ValueError("NON_ESTIMATED_METRIC_HAS_ESTIMATION_BASIS")
    return {"value": value, "provenance": provenance, "unit": unit, "basis": basis,
            "estimation_basis": estimation_basis}


class MeteringService:
    """Persist compiler and Host-declared telemetry without inventing missing values."""

    def __init__(self, store, authority, participation):
        self.store = store
        self.authority = authority
        self.participation = participation

    def record_host_declared(
        self, *, record_id: str, task_id: str, run_id: str, grant_id: str,
        host_usage: dict, context_pack_ref: str | None = None,
        usage_basis: str = "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY",
        latency_ms: int | None = None, actual_cost: dict | None = None,
        estimated_cost: dict | None = None,
    ) -> dict:
        """Persist fields explicitly declared by a supported Host callback.

        `input_tokens` is total-turn usage; it is never relabeled as
        model-visible packet usage. Model-visible tokens require a separate,
        explicit `model_visible_input_tokens` value and exposure basis.
        """
        mode = self.participation.current()["mode"]
        if mode == "BYPASS":
            raise RuntimeDenied("BYPASS_DISALLOWS_AUTOMATIC_METERING_INGESTION")
        self.store._require_mode("core_write")
        if not isinstance(host_usage, dict):
            raise RuntimeDenied("HOST_USAGE_INVALID")
        self.authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": run_id, "action": "TRACE_APPEND", "audience": "nexus-runtime"},
            "metering-auth-" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:16],
        )
        with self.store._connection() as conn:
            run = conn.execute("SELECT task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run or run["task_id"] != task_id:
            raise RuntimeDenied("METERING_RUN_TASK_MISMATCH")

        metrics = unavailable_metrics()
        total_input = host_usage.get("input_tokens")
        if total_input is not None:
            _nonnegative_int(total_input, "HOST_INPUT_TOKENS_INVALID")
            metrics["host_total_turn_input_tokens"] = metric(
                total_input, "HOST_DECLARED", unit="tokens", basis=usage_basis
            )
        cached = host_usage.get("cached_input_tokens")
        if cached is not None:
            _nonnegative_int(cached, "HOST_CACHED_INPUT_TOKENS_INVALID")
            metrics["cached_input_tokens"] = metric(cached, "HOST_DECLARED", unit="tokens", basis=usage_basis)
        if total_input is not None and cached is not None:
            if cached > total_input:
                raise RuntimeDenied("HOST_TOKEN_USAGE_INCONSISTENT")
            metrics["uncached_input_tokens"] = metric(
                total_input - cached, "DERIVED", unit="tokens",
                basis="host_total_turn_input_tokens minus host_declared cached_input_tokens; same reported usage basis",
            )
        field_map = {
            "cache_write_input_tokens": "cache_write_input_tokens",
            "output_tokens": "output_tokens",
            "reasoning_output_tokens": "reasoning_tokens",
        }
        for input_name, metric_name in field_map.items():
            value = host_usage.get(input_name)
            if value is not None:
                _nonnegative_int(value, "HOST_TOKEN_USAGE_INVALID")
                metrics[metric_name] = metric(value, "HOST_DECLARED", unit="tokens", basis=usage_basis)
        visible = host_usage.get("model_visible_input_tokens")
        visible_basis = host_usage.get("model_visible_input_basis")
        if visible is not None:
            _nonnegative_int(visible, "HOST_MODEL_VISIBLE_TOKENS_INVALID")
            if not isinstance(visible_basis, str) or not visible_basis.strip():
                raise RuntimeDenied("MODEL_VISIBLE_TOKEN_BASIS_REQUIRED")
            metrics["model_visible_input_tokens"] = metric(
                visible, "HOST_DECLARED", unit="tokens", basis=visible_basis
            )
        if latency_ms is not None:
            _nonnegative_int(latency_ms, "HOST_LATENCY_INVALID")
            metrics["latency_ms"] = metric(latency_ms, "HOST_DECLARED", unit="milliseconds", basis="Host callback declaration")
        if context_pack_ref is not None:
            with self.store._connection() as conn:
                pack = conn.execute(
                    "SELECT serialized_byte_size,state,run_id FROM context_pack_records WHERE pack_ref=? AND task_id=?",
                    (context_pack_ref, task_id),
                ).fetchone()
            if not pack or pack["state"] != "COMPILED" or pack["run_id"] != run_id:
                raise RuntimeDenied("METERING_CONTEXT_PACK_UNAVAILABLE")
            metrics["context_pack_bytes"] = metric(
                pack["serialized_byte_size"], "DERIVED", unit="bytes",
                basis="Exact UTF-8 serialized size persisted by ContextPackService",
            )
        if actual_cost is not None:
            metrics["actual_cost"] = _cost_metric(actual_cost, "HOST_DECLARED")
        if estimated_cost is not None:
            metrics["estimated_cost"] = _cost_metric(estimated_cost, "ESTIMATED")
        return self._insert_record(
            record_id=record_id, task_id=task_id, run_id=run_id, source="HOST_DECLARED",
            mode=mode, context_pack_ref=context_pack_ref, metrics=metrics,
        )

    def _insert_context_compile(self, conn, *, record_id: str, task_id: str, run_id: str,
                                pack_ref: str, byte_size: int, recorded_at: str) -> dict:
        metrics = unavailable_metrics()
        metrics["context_pack_bytes"] = metric(
            byte_size, "DERIVED", unit="bytes",
            basis="Exact UTF-8 serialized artifact payload size",
        )
        return self._insert_record(
            record_id=record_id, task_id=task_id, run_id=run_id,
            source="NEXUS_CONTEXT_COMPILER", mode="ACTIVE", context_pack_ref=pack_ref,
            metrics=metrics, recorded_at=recorded_at, conn=conn,
        )

    def _insert_record(self, *, record_id: str, task_id: str, run_id: str, source: str,
                       mode: str, context_pack_ref: str | None, metrics: dict,
                       recorded_at: str | None = None, conn=None) -> dict:
        if not record_id or set(metrics) != set(_METRIC_UNITS):
            raise RuntimeDenied("METERING_RECORD_INVALID")
        self.store._validate("nexus.metering_record@1.schema.json", {
            "schema_id": "nexus.metering_record", "schema_version": 1, "task_id": task_id,
            "run_id": run_id, "record_source": source, "participation_mode": mode,
            "context_pack_ref": context_pack_ref, "metrics": metrics,
            "metrics_sha256": hashlib.sha256(_canonical(metrics)).hexdigest(),
            "recorded_at": recorded_at or _now(),
        })
        metrics_json = _canonical(metrics).decode("utf-8")
        digest = hashlib.sha256(metrics_json.encode("utf-8")).hexdigest()
        created = recorded_at or _now()
        own = conn is None
        if own:
            with self.store._lock, self.store._connection() as connection:
                return self._insert_record(record_id=record_id, task_id=task_id, run_id=run_id,
                    source=source, mode=mode, context_pack_ref=context_pack_ref,
                    metrics=metrics, recorded_at=created, conn=connection)
        prior = conn.execute("SELECT * FROM value_metering_records WHERE record_id=?", (record_id,)).fetchone()
        if prior:
            expected = (task_id, run_id, source, mode, context_pack_ref, metrics_json, digest, created)
            actual = tuple(prior[key] for key in (
                "task_id", "run_id", "record_source", "participation_mode", "context_pack_ref",
                "metrics_json", "metrics_sha256", "recorded_at"))
            if actual != expected:
                raise RuntimeDenied("METERING_RECORD_ID_CONFLICT")
            return self._public_record(dict(prior))
        conn.execute(
            "INSERT INTO value_metering_records(record_id,task_id,run_id,record_source,participation_mode,"
            "context_pack_ref,metrics_json,metrics_sha256,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (record_id, task_id, run_id, source, mode, context_pack_ref, metrics_json, digest, created),
        )
        row = conn.execute("SELECT * FROM value_metering_records WHERE record_id=?", (record_id,)).fetchone()
        return self._public_record(dict(row))

    def snapshot(self, *, limit: int = 20) -> dict:
        self.store._require_mode("core_read")
        if not 1 <= limit <= 100:
            raise ValueError("METERING_LIMIT_INVALID")
        with self.store._connection() as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0]
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM value_metering_records ORDER BY recorded_at DESC,record_id DESC LIMIT ?", (limit,)
            )]
        records = [self._public_record(row) for row in rows]
        latest = records[0] if records else None
        return {
            "status": "OBSERVED" if latest else "NOT OBSERVED",
            "record_count": total_count,
            "records_returned": len(rows),
            "latest_record": latest,
            "metrics": latest["metrics"] if latest else unavailable_metrics(),
            "records": records,
            "payloads_included": False,
        }

    @staticmethod
    def _public_record(row: dict) -> dict:
        metrics_json = row["metrics_json"]
        if hashlib.sha256(metrics_json.encode("utf-8")).hexdigest() != row["metrics_sha256"]:
            raise RuntimeDenied("METERING_RECORD_INTEGRITY_FAILED")
        metrics = json.loads(metrics_json)
        return {
            "record_id": row["record_id"], "task_id": row["task_id"], "run_id": row["run_id"],
            "record_source": row["record_source"], "participation_mode": row["participation_mode"],
            "context_pack_ref": row["context_pack_ref"], "recorded_at": row["recorded_at"],
            "metrics_sha256": row["metrics_sha256"], "metrics": metrics,
        }


def _nonnegative_int(value, error: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeDenied(error)


def _cost_metric(value: dict, provenance: str) -> dict:
    if not isinstance(value, dict) or isinstance(value.get("value"), bool) or not isinstance(value.get("value"), (int, float)) or value["value"] < 0:
        raise RuntimeDenied("COST_VALUE_INVALID")
    currency = value.get("currency")
    if not isinstance(currency, str) or not currency:
        raise RuntimeDenied("COST_CURRENCY_REQUIRED")
    basis = value.get("basis")
    if provenance == "ESTIMATED":
        required = ("pricing_id", "pricing_version", "formula")
        if not isinstance(basis, dict) or not all(isinstance(basis.get(key), str) and basis[key] for key in required):
            raise RuntimeDenied("ESTIMATED_COST_BASIS_REQUIRED")
        return metric(value["value"], "ESTIMATED", unit=currency, basis="Persisted pricing basis",
                      estimation_basis={key: basis[key] for key in required})
    return metric(value["value"], provenance, unit=currency,
                  basis=basis if isinstance(basis, str) else "Host-declared provider cost")
