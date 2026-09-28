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
    estimate_status = "UNAVAILABLE" if provenance == UNAVAILABLE else "NOT_ESTIMATED"
    return {"value": value, "provenance": provenance, "estimate_status": estimate_status,
            "estimation_basis": None, "unit": unit, "basis": basis,
            "observation_source": source}


class PanelViewModel:
    """Compose narrow Core projections; the presentation layer sees no store."""

    def __init__(self, *, runtime, participation, panel_queries, memory, context_packs=None, metering=None, skills=None):
        self._runtime = runtime
        self._participation = participation
        self._panel_queries = panel_queries
        self._memory = memory
        self._context_packs = context_packs
        self._metering = metering
        self._skills = skills

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
        context_status = {"status": "NOT IMPLEMENTED", "detail": "Production Context Pack runtime is not available."}
        if self._context_packs is not None:
            try:
                context_status = self._context_packs.latest()
                refs = context_status.get("selected_refs", [])
                context_status["selected_ref_count"] = len(refs)
                context_status["selected_refs"] = refs
                context_status["provenance"] = "NEXUS_CANONICAL_GOVERNED_SOURCES"
                context_status["detail"] = "Payload bodies are hidden; model-visible exposure remains UNKNOWN."
            except Exception as exc:
                context_status = {"status": "UNAVAILABLE", "detail": _reason(exc),
                                  "host_delivery_status": "UNAVAILABLE", "model_visible_exposure": "UNKNOWN"}
        if self._metering is not None:
            try:
                value_snapshot = self._metering.snapshot()
                latest = value_snapshot["latest_record"]
                if latest:
                    for key, item in latest["metrics"].items():
                        value_metrics[key] = {
                            "value": item["value"], "provenance": item["provenance"],
                            "estimate_status": item["estimate_status"],
                            "unit": item["unit"], "basis": item["basis"],
                            "estimation_basis": item["estimation_basis"],
                            "observation_source": latest["record_source"],
                        }
                value_metrics["metering_status"] = _metric(
                    value_snapshot["status"], provenance="OBSERVED", observation_source="NEXUS_METERING_SERVICE"
                )
                value_metrics["metering_record_count"] = _metric(
                    value_snapshot["record_count"], provenance="DERIVED", unit="records",
                    basis="Total persisted metering record count.",
                )
            except Exception as exc:
                value_metrics["metering_status"] = _metric(_reason(exc), provenance="OBSERVED",
                                                             observation_source="NEXUS_METERING_SERVICE")
        skill_status = {"status": "NOT IMPLEMENTED", "detail": "Production Skill Registry is not available."}
        if self._skills is not None:
            try:
                skill_status = self._skills.snapshot()
                skill_status["detail"] = "Safe Registry metadata only; instruction bodies are hidden. Registered, selected, loaded, delivered, and used are distinct states."
                latest = skill_status.get("latest_selection") or {}
                value_metrics["skill_candidate_count"] = _metric(
                    latest.get("candidate_count"),
                    provenance="DERIVED" if latest else UNAVAILABLE,
                    unit="candidates", observation_source="NEXUS_SKILL_REGISTRY",
                )
                value_metrics["skill_instruction_byte_size"] = _metric(
                    latest.get("instruction_byte_size"),
                    provenance="DERIVED" if latest.get("instruction_byte_size") is not None else UNAVAILABLE,
                    unit="bytes", basis="Exact UTF-8 bytes in integrity-verified SKILL.md snapshot; not a token estimate.",
                    observation_source="NEXUS_SKILL_REGISTRY",
                )
                value_metrics["skill_instruction_tokens"] = _metric(
                    unit="tokens", observation_source="NEXUS_SKILL_REGISTRY",
                )
                value_metrics["skill_selection_latency"] = _metric(
                    latest.get("selection_latency_ms"),
                    provenance="OBSERVED" if latest else UNAVAILABLE,
                    unit="milliseconds", observation_source="NEXUS_SKILL_REGISTRY",
                )
            except Exception as exc:
                skill_status = {"status": "UNAVAILABLE", "detail": _reason(exc),
                                "registered_count": None, "eligible_count": None,
                                "stale_or_disabled_count": None, "registered": [], "latest_selection": None}
        return {
            "participation_mode": participation_mode,
            "participation_state": participation,
            "runtime_mode": runtime_mode,
            "overview": overview,
            "query_status": query_status,
            "tasks": core["tasks"],
            "memory": memory,
            "context_status": context_status,
            "skill_status": skill_status,
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
