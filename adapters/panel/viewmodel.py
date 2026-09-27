"""Sanitized application-facing view model for the Nexus companion panel."""

from __future__ import annotations

from uuid import uuid4


UNAVAILABLE = "UNAVAILABLE"
METRIC_PROVENANCE = frozenset({"OBSERVED", "HOST_DECLARED", "DERIVED", UNAVAILABLE})


def _metric(value=None, *, provenance=UNAVAILABLE, unit=None, basis=None, observation_source=None):
    if provenance not in METRIC_PROVENANCE:
        raise ValueError("PANEL_METRIC_PROVENANCE_INVALID")
    source = observation_source
    if source is None:
        source = "NEXUS_CORE" if provenance == "DERIVED" else "HOST" if provenance == "HOST_DECLARED" else None
    return {"value": value, "provenance": provenance, "unit": unit, "basis": basis,
            "observation_source": source}


class PanelViewModel:
    """Compose narrow Core projections; the presentation layer sees no store."""

    def __init__(self, *, runtime, participation, panel_queries, memory):
        self._runtime = runtime
        self._participation = participation
        self._panel_queries = panel_queries
        self._memory = memory

    def snapshot(self) -> dict:
        try:
            participation = self._participation.current()
            participation_mode = participation["mode"]
        except Exception as exc:
            participation = {"mode": "UNKNOWN", "scope": "INSTANCE", "status": _reason(exc)}
            participation_mode = "UNKNOWN"
        try:
            runtime_mode = self._runtime.current_mode()["mode"]
        except Exception as exc:
            runtime_mode = "UNKNOWN"

        try:
            core = self._panel_queries.snapshot()
            query_status = "OBSERVED"
        except Exception as exc:
            query_status = _reason(exc)
            core = {
                "overview": {"task_count": None, "recorded_run_count": None, "unfinished_task_count": None,
                             "blocked_task_count": None, "active_run_count": None, "pending_effect_count": None},
                "recent_runs": [], "tasks": [],
            }
        try:
            memory = self._memory.status_summary()
            memory["status"] = "OBSERVED"
        except Exception as exc:
            memory = {"raw_history_count": None, "admitted_count": None, "candidate_states": [],
                      "payloads_included": False, "status": "UNAVAILABLE", "reason": _reason(exc)}
        overview = {
            **core["overview"],
            "recent_runs": core["recent_runs"],
            "status": query_status,
            "participation_scope": participation.get("scope", "INSTANCE"),
            "scope_note": "Current Nexus data root; no first-class Project object exists.",
        }
        value_metrics = {
            "core_recorded_run_count": _metric(
                core["overview"].get("recorded_run_count"),
                provenance="DERIVED" if core["overview"].get("recorded_run_count") is not None else UNAVAILABLE,
                unit="runs",
                basis="Total recorded Run count from Nexus Core records; not a value/savings claim.",
            ),
            "observation_source": _metric("NEXUS_CORE_PANEL_SNAPSHOT", provenance="OBSERVED"),
            "nexus_influenced_execution": _metric(),
            "input_tokens": _metric(unit="tokens", observation_source="HOST_REPORTED_TELEMETRY"),
            "cached_input_tokens": _metric(unit="tokens", observation_source="HOST_REPORTED_TELEMETRY"),
            "output_tokens": _metric(unit="tokens", observation_source="HOST_REPORTED_TELEMETRY"),
            "reasoning_tokens": _metric(unit="tokens", observation_source="HOST_REPORTED_TELEMETRY"),
            "latency": _metric(unit="milliseconds", observation_source="HOST_REPORTED_TELEMETRY"),
            "context_pack_size": _metric(unit="bytes", observation_source="NEXUS_CONTEXT_RUNTIME"),
            "skill_instructions_selected": _metric(observation_source="NEXUS_SKILL_SELECTION"),
            "skill_instructions_loaded": _metric(observation_source="HOST_SKILL_OBSERVABILITY"),
            "duplicate_work_reused_count": _metric(unit="runs", observation_source="NEXUS_CORE_RECORDS"),
            "duplicate_work_avoided_count": _metric(unit="runs", observation_source="NEXUS_CORE_RECORDS"),
            "estimated_cost_or_savings": _metric(unit="provider currency", observation_source="PROVIDER_BILLING"),
        }
        return {
            "participation_mode": participation_mode,
            "participation_state": participation,
            "runtime_mode": runtime_mode,
            "overview": overview,
            "query_status": query_status,
            "tasks": core["tasks"],
            "memory": memory,
            "context_status": {"status": "NOT IMPLEMENTED", "detail": "Production Context Pack runtime is not available."},
            "skill_status": {"status": "NOT IMPLEMENTED", "detail": "Production Skill Registry/selection is not available."},
            "value_metrics": value_metrics,
            "metric_provenance_values": sorted(METRIC_PROVENANCE),
        }

    def set_participation_mode(self, mode: str, *, expected_mode: str | None = None) -> dict:
        current = expected_mode or self._participation.current()["mode"]
        return self._participation.set_mode(
            mode=mode,
            expected_mode=current,
            command_id="panel-mode-" + uuid4().hex,
            updated_by="local-operator-panel",
        )


def _reason(exc: Exception) -> str:
    value = exc.args[0] if getattr(exc, "args", None) else type(exc).__name__
    return value if isinstance(value, str) and value.isupper() and len(value) <= 96 else type(exc).__name__
