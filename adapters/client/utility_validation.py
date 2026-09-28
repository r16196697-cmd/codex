"""Thin eval-only boundary preparing governed Nexus state for CLI input.

The payload prepared here is not yet received by a Host. Only after the
controller returns from the CLI subprocess API can the ledger say
CONTROLLER_SUBMITTED_TO_CODEX_CLI; neither state proves model visibility. This
composes existing Core artifacts, never reads SQLite directly, and never
executes Skill package code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from kernel.runtime.errors import RuntimeDenied
from scripts.eval.utility_validation_observation import TrialSession, canonical_json, sha256_bytes


COMPOSITION_VERSION = "UTILITY_INTERVENTION_ENVELOPE_V1"


@dataclass(frozen=True)
class PreparedUtilityIntervention:
    payload: bytes
    controller_invocation_id: str
    context_pack_ref: str
    context_pack_sha256: str
    context_pack_byte_size: int
    task_payload_sha256: str
    skill_payload_sha256: str | None
    skill_resolution_ref: str | None
    skill_resolution_status: str | None
    skill_resolution: str | None
    skill_candidate_count: int | None
    selected_skill_ref: str | None
    host_skill_availability: str | None
    host_inventory_provenance: str | None
    skill_instruction_load_status: str | None
    skill_instruction_byte_size: int | None
    skill_selection_latency_ms: float | None
    skill_instruction_object_ref: str | None
    preparation_status: str = "PREPARED_NOT_SUBMITTED"
    boundary: str = "CODEX_CLI_INPUT"


def _canonical_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class UtilityInterventionComposer:
    """Prepare B from existing Context Pack and Skill application services."""

    def __init__(self, *, context_packs, skill_application):
        self.context_packs = context_packs
        self.skill_application = skill_application
        self.store = context_packs.store

    def prepare_b(self, session: TrialSession, **request) -> PreparedUtilityIntervention | None:
        """Prepare B, recording a fail-closed inconclusive fact on preparation errors."""
        if session.condition != "NEXUS_ACTIVE":
            raise RuntimeDenied("UTILITY_INTERVENTION_REQUIRES_NEXUS_ACTIVE")
        task_input = request.get("task_input")
        controller_invocation_id = request.get("controller_invocation_id")
        if not isinstance(task_input, bytes) or not isinstance(controller_invocation_id, str):
            raise RuntimeDenied("UTILITY_INTERVENTION_REQUEST_INVALID")
        resolved_refs = {
            key: request.get(key)
            for key in ("task_id", "run_id")
        }
        prepare_callback = request.get("prepare_nexus_work")
        if prepare_callback is not None:
            def tracked_prepare():
                result = prepare_callback()
                if isinstance(result, dict):
                    resolved_refs.update({key: result.get(key) for key in ("task_id", "run_id")})
                return result
            request["prepare_nexus_work"] = tracked_prepare
        try:
            return self._prepare_b(session, **request)
        except Exception:
            # Preserve the opened trial and elapsed time without serializing an
            # exception string that could contain local or governed data.
            events = session.ledger.trial_events(session.study_id, session.trial_id)
            if events and events[-1]["event_type"] not in {
                "TRIAL_COMPLETED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED",
            }:
                already_prepared = any(
                    event["event_type"] == "INTERVENTION_PREPARED"
                    and event["payload"].get("controller_invocation_id") == controller_invocation_id
                    for event in events
                )
                if not already_prepared:
                    opened = events[0]["payload"]
                    task_id = opened["nexus_task_id"] or resolved_refs.get("task_id")
                    run_id = opened["nexus_run_id"] or resolved_refs.get("run_id")
                    if (task_id is None) != (run_id is None):
                        task_id = run_id = None
                    session.prepare_intervention(
                        controller_invocation_id=controller_invocation_id,
                        preparation_status="NONE_INCOMPLETE", boundary="CODEX_CLI_INPUT",
                        composition_version=COMPOSITION_VERSION,
                        task_payload_sha256=hashlib.sha256(task_input).hexdigest(),
                        final_cli_input_sha256=None, final_cli_input_bytes=0,
                        context_payload_sha256=None, context_pack_refs=[], context_pack_byte_size=None,
                        skill_payload_sha256=None, skill_resolution_ref=None,
                        skill_resolution_status=None, skill_resolution=None,
                        skill_candidate_count=None, selected_skill_ref=None,
                        host_skill_availability=None, host_inventory_provenance=None,
                        skill_instruction_load_status=None, skill_instruction_byte_size=None,
                        skill_selection_latency_ms=None,
                        skill_instruction_object_ref=None,
                        nexus_task_id=task_id, nexus_run_id=run_id,
                        provenance={"intervention_payload": "UNAVAILABLE", "preparation": "UNAVAILABLE"},
                    )
                session.inconclusive("B_INTERVENTION_NOT_SUBMITTED")
            return None

    def _prepare_b(self, session: TrialSession, *, task_input: bytes,
                   task_id: str | None = None, run_id: str | None = None,
                   grant_id: str | None = None,
                   classification_assertion_ref: str | None = None,
                   prepare_nexus_work: Callable[[], dict] | None = None,
                   context_pack_request: dict | None = None,
                  skill_query: str | None,
                  skill_resolution_command_id: str,
                  context_command_id: str,
                  controller_invocation_id: str) -> PreparedUtilityIntervention | None:
        if session.condition != "NEXUS_ACTIVE":
            raise RuntimeDenied("UTILITY_INTERVENTION_REQUIRES_NEXUS_ACTIVE")
        if not isinstance(task_input, bytes):
            raise TypeError("task_input must be exact UTF-8 bytes")
        if any(value is None for value in (task_id, run_id, grant_id, classification_assertion_ref)):
            if any(value is not None for value in (task_id, run_id, grant_id, classification_assertion_ref)) or prepare_nexus_work is None:
                raise RuntimeDenied("UTILITY_NEXUS_TASK_RUN_PREPARATION_INCOMPLETE")
            prepared_work = prepare_nexus_work()
            required_work = {"task_id", "run_id", "grant_id", "classification_assertion_ref"}
            if not isinstance(prepared_work, dict) or set(prepared_work) != required_work:
                raise RuntimeDenied("UTILITY_NEXUS_TASK_RUN_PREPARATION_INVALID")
            task_id = prepared_work["task_id"]
            run_id = prepared_work["run_id"]
            grant_id = prepared_work["grant_id"]
            classification_assertion_ref = prepared_work["classification_assertion_ref"]
        opened_payload = session.ledger.trial_events(session.study_id, session.trial_id)[0]["payload"]
        if (opened_payload["nexus_task_id"] is not None and opened_payload["nexus_task_id"] != task_id
                or opened_payload["nexus_run_id"] is not None and opened_payload["nexus_run_id"] != run_id):
            raise RuntimeDenied("UTILITY_NEXUS_TASK_RUN_BINDING_MISMATCH")
        try:
            task_text = task_input.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeDenied("UTILITY_TASK_INPUT_NOT_UTF8") from exc
        resolution = None
        fallback_ref = None
        skill_hash = None
        if skill_query is not None:
            resolution = self.skill_application.resolve(
                query=skill_query, task_id=task_id, run_id=run_id, grant_id=grant_id,
                classification_assertion_ref=classification_assertion_ref,
                command_id=skill_resolution_command_id,
            )
            if resolution.get("resolution") == "NEXUS_FALLBACK":
                fallback_ref = resolution.get("instruction_object_ref")
                if not isinstance(fallback_ref, str):
                    raise RuntimeDenied("UTILITY_FALLBACK_ARTIFACT_REF_MISSING")
                skill_hash = resolution.get("instruction_sha256")
            elif resolution.get("resolution") == "HOST_NATIVE":
                # Inventory availability is not activation. Never load the
                # Nexus copy or inject a duplicate native instruction.
                pass

        request = dict(context_pack_request or {})
        allowed_request_keys = {"pack_object_id", "classification_assertion_ref", "source_refs", "memory_query", "memory_limit"}
        if set(request) - allowed_request_keys:
            raise RuntimeDenied("UTILITY_CONTEXT_REQUEST_FIELDS_INVALID")
        if fallback_ref:
            request.setdefault("source_refs", [])
            if fallback_ref not in request["source_refs"]:
                request["source_refs"] = [*request["source_refs"], fallback_ref]
        pack_status = None
        pack_payload = None
        if request:
            required = {"pack_object_id"}
            if not required.issubset(request):
                raise RuntimeDenied("UTILITY_CONTEXT_REQUEST_INCOMPLETE")
            pack_status = self.context_packs.compile(
                task_id=task_id, run_id=run_id, grant_id=grant_id,
                pack_object_id=request["pack_object_id"],
                classification_assertion_ref=request.get("classification_assertion_ref", classification_assertion_ref),
                command_id=context_command_id,
                source_refs=request.get("source_refs", ()),
                memory_query=request.get("memory_query"),
                memory_limit=request.get("memory_limit", 20),
            )
            if pack_status.get("task_id") != task_id or pack_status.get("run_id") != run_id:
                raise RuntimeDenied("UTILITY_CONTEXT_PACK_RUN_BINDING_MISMATCH")
            self.store.verify_object(pack_status["pack_id"])
            metadata = self.store.get_object_metadata(pack_status["pack_id"])
            if metadata.get("payload_state") != "AVAILABLE" or metadata.get("object_type") != "artifact":
                raise RuntimeDenied("UTILITY_CONTEXT_PACK_UNAVAILABLE")
            pack_payload = self.store.get_payload(pack_status["pack_id"])
            if (len(pack_payload) != pack_status.get("serialized_byte_size")
                    or _canonical_hash(pack_payload) != pack_status.get("integrity_hash")):
                raise RuntimeDenied("UTILITY_CONTEXT_PACK_INTEGRITY_MISMATCH")
            try:
                pack_document = json.loads(pack_payload.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeDenied("UTILITY_CONTEXT_PACK_INVALID") from exc
            if (pack_document.get("schema_id") != "nexus.context_pack"
                    or pack_document.get("task_id") != task_id or pack_document.get("run_id") != run_id):
                raise RuntimeDenied("UTILITY_CONTEXT_PACK_RUN_BINDING_MISMATCH")
            if fallback_ref:
                skill_entry = next((entry for entry in pack_document.get("entries", [])
                                    if entry.get("source_ref") == fallback_ref), None)
                if not skill_entry or not isinstance(skill_entry.get("content"), str):
                    raise RuntimeDenied("UTILITY_FALLBACK_NOT_BOUND_IN_CONTEXT_PACK")
                try:
                    artifact = json.loads(skill_entry["content"])
                    instruction = artifact["instruction_utf8"].encode("utf-8")
                except (json.JSONDecodeError, KeyError, TypeError, UnicodeEncodeError) as exc:
                    raise RuntimeDenied("UTILITY_FALLBACK_ARTIFACT_INVALID") from exc
                if (artifact.get("schema_id") != "nexus.skill_instruction_artifact"
                        or artifact.get("task_id") != task_id or artifact.get("run_id") != run_id
                        or artifact.get("skill_id") != resolution.get("skill_id")
                        or artifact.get("instruction_sha256") != skill_hash
                        or _canonical_hash(instruction) != skill_hash
                        or len(instruction) != resolution.get("instruction_byte_size")):
                    raise RuntimeDenied("UTILITY_FALLBACK_ARTIFACT_INTEGRITY_MISMATCH")
                skill_hash = _canonical_hash(instruction)
        if pack_payload is None or not pack_status.get("selected_refs"):
            session.prepare_intervention(
                controller_invocation_id=controller_invocation_id,
                preparation_status="NONE_INCOMPLETE", boundary="CODEX_CLI_INPUT",
                composition_version=COMPOSITION_VERSION,
                task_payload_sha256=_canonical_hash(task_input),
                final_cli_input_sha256=None, final_cli_input_bytes=0,
                context_payload_sha256=None, context_pack_refs=[], context_pack_byte_size=None,
                skill_payload_sha256=None, skill_resolution_ref=resolution.get("selection_id") if resolution else None,
                skill_resolution_status=resolution.get("result_status") if resolution else None,
                skill_resolution=resolution.get("resolution") if resolution else None,
                skill_candidate_count=resolution.get("candidate_count") if resolution else None,
                selected_skill_ref=resolution.get("skill_id") if resolution else None,
                host_skill_availability=resolution.get("host_native_availability") if resolution else None,
                host_inventory_provenance=resolution.get("host_inventory_provenance") if resolution else None,
                skill_instruction_load_status=resolution.get("instruction_load_status") if resolution else None,
                skill_instruction_byte_size=resolution.get("instruction_byte_size") if resolution else None,
                skill_selection_latency_ms=resolution.get("selection_latency_ms") if resolution else None,
                skill_instruction_object_ref=fallback_ref, nexus_task_id=task_id, nexus_run_id=run_id,
                provenance={"intervention_payload": "UNAVAILABLE", "nexus_resolution": "DERIVED" if resolution else "UNAVAILABLE"},
            )
            session.inconclusive("B_INTERVENTION_NOT_SUBMITTED")
            return None

        # Canonical JSON is the frozen envelope representation. The task text
        # remains byte-identical as a value; the only added fields are actual
        # governed Nexus payloads. No experiment-purpose text is added.
        envelope = {
            "schema_id": "nexus.utility_intervention_envelope",
            "schema_version": 1,
            "task_input_utf8": task_text,
            "context_pack": json.loads(pack_payload.decode("utf-8")),
        }
        final_payload = canonical_json(envelope)
        context_hash = _canonical_hash(pack_payload)
        prepared = PreparedUtilityIntervention(
            payload=final_payload,
            controller_invocation_id=controller_invocation_id,
            context_pack_ref=pack_status["pack_id"],
            context_pack_sha256=context_hash,
            context_pack_byte_size=len(pack_payload),
            task_payload_sha256=_canonical_hash(task_input),
            skill_payload_sha256=skill_hash,
            skill_resolution_ref=resolution.get("selection_id") if resolution else None,
            skill_resolution_status=resolution.get("result_status") if resolution else None,
            skill_resolution=resolution.get("resolution") if resolution else None,
            skill_candidate_count=resolution.get("candidate_count") if resolution else None,
            selected_skill_ref=resolution.get("skill_id") if resolution else None,
            host_skill_availability=resolution.get("host_native_availability") if resolution else None,
            host_inventory_provenance=resolution.get("host_inventory_provenance") if resolution else None,
            skill_instruction_load_status=resolution.get("instruction_load_status") if resolution else None,
            skill_instruction_byte_size=resolution.get("instruction_byte_size") if resolution else None,
            skill_selection_latency_ms=resolution.get("selection_latency_ms") if resolution else None,
            skill_instruction_object_ref=fallback_ref,
        )
        session.prepare_intervention(
            controller_invocation_id=controller_invocation_id,
            preparation_status=prepared.preparation_status,
            boundary=prepared.boundary,
            composition_version=COMPOSITION_VERSION,
            task_payload_sha256=prepared.task_payload_sha256,
            final_cli_input_sha256=_canonical_hash(prepared.payload),
            final_cli_input_bytes=len(prepared.payload),
            context_payload_sha256=prepared.context_pack_sha256,
            context_pack_refs=[prepared.context_pack_ref],
            context_pack_byte_size=prepared.context_pack_byte_size,
            skill_payload_sha256=prepared.skill_payload_sha256,
            skill_resolution_ref=prepared.skill_resolution_ref,
            skill_resolution_status=prepared.skill_resolution_status,
            skill_resolution=prepared.skill_resolution,
            skill_candidate_count=prepared.skill_candidate_count,
            selected_skill_ref=prepared.selected_skill_ref,
            host_skill_availability=prepared.host_skill_availability,
            host_inventory_provenance=prepared.host_inventory_provenance,
            skill_instruction_load_status=prepared.skill_instruction_load_status,
            skill_instruction_byte_size=prepared.skill_instruction_byte_size,
            skill_selection_latency_ms=prepared.skill_selection_latency_ms,
            skill_instruction_object_ref=prepared.skill_instruction_object_ref,
            nexus_task_id=task_id,
            nexus_run_id=run_id,
            provenance={
                "intervention_payload": "DERIVED",
                "context_pack_integrity": "OBSERVED",
                "model_visible_exposure": "UNAVAILABLE",
                "skill_payload": "OBSERVED" if skill_hash else "UNAVAILABLE",
            },
        )
        return prepared
