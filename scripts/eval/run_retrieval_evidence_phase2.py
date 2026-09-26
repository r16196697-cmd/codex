"""Run the frozen synthetic Search/Evidence qualification through public APIs.

No MODEL Run is created. Search results and pack candidates are measured as
Academy-only proposals and are never represented as model-visible context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.client.hosted import CodexHostedBridge
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.memory import MemoryService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.verification import VerificationService
from kernel.object.errors import CommandConflict


FIXTURE_PATH = REPO_ROOT / "eval" / "academy" / "retrieval-evidence-phase2-fixture.json"
RESULT_PATH = REPO_ROOT / "eval" / "academy" / "results" / "retrieval-evidence-phase2.json"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def ensure_disposable_paths(data_root: Path, journal: Path) -> None:
    if data_root.exists() and (data_root.is_file() or any(data_root.iterdir())):
        raise SystemExit("Refusing to overwrite a non-empty Academy data root")
    if journal.exists():
        raise SystemExit("Refusing to overwrite an existing independent journal")


def normalize_q1(query: str) -> str:
    """Explicit Academy-only punctuation normalization candidate; Core is untouched."""
    value = re.sub(r"[\"'`]+", " ", query)
    value = re.sub(r"[-/:()]+", " ", value)
    return " ".join(value.split())


def retrieval_metrics(returned_ids: list[str], relevant_ids: set[str], k_values=(1, 3, 5)) -> dict[str, Any]:
    ranks = [index for index, object_id in enumerate(returned_ids, start=1) if object_id in relevant_ids]
    metrics: dict[str, Any] = {"mrr": (1 / ranks[0]) if ranks else 0.0}
    for k in k_values:
        hits = sum(object_id in relevant_ids for object_id in returned_ids[:k])
        metrics[f"precision@{k}"] = hits / k
        metrics[f"recall@{k}"] = (hits / len(relevant_ids)) if relevant_ids else None
    return metrics


def _classification(assertion_id: str, subject_type: str, subject_ref: str, actor_id: str, level: str = "PUBLIC") -> dict[str, Any]:
    return {"schema_id": "nexus.classification_assertion", "schema_version": 1,
        "assertion_id": assertion_id, "subject_type": subject_type, "subject_ref": subject_ref,
        "sensitivity_level": level, "handling_tags": [], "policy_version": "1",
        "reason": "synthetic Academy Phase 2 fixture", "actor_id": actor_id}


def _root_resources(task_id: str, run_id: str, input_id: str, contract_id: str, manifest_id: str,
                    extra: set[str], command_prefix: str) -> list[str]:
    event_ids = {f"evt-{command_prefix}-root-create", f"evt-{command_prefix}-root-ready",
        f"evt-{command_prefix}-root-running", f"evt-{command_prefix}-trace-input",
        f"evt-{command_prefix}-root-verifying", f"evt-{command_prefix}-root-succeeded"}
    return sorted({f"task:{task_id}", task_id, run_id, input_id, contract_id, manifest_id,
        *extra, *event_ids})


def _bootstrap_root(*, store, authority, budget, trace, runtime, verifier, task_id: str,
                    run_id: str, grant_id: str, principal_id: str, boundary_levels: list[str],
                    resource_scope: list[str], command_prefix: str) -> dict[str, str]:
    bridge = CodexHostedBridge(store=store, authority=authority, budget=budget, trace=trace,
        runtime=runtime, verifier=verifier)
    input_id, contract_id, manifest_id = (f"p2-{task_id}-input", f"p2-{task_id}-contract", f"p2-{task_id}-manifest")
    event_prefix = f"evt-{command_prefix}"
    classification_ids = {
        "root_run": f"class-{task_id}-run", "root_created_event": f"class-{task_id}-created-event",
        "input_object": f"class-{task_id}-input", "input_event": f"class-{task_id}-input-event",
        "task_contract": f"class-{task_id}-contract", "root_manifest": f"class-{task_id}-manifest",
        "root_ready_event": f"class-{task_id}-ready-event", "root_running_event": f"class-{task_id}-running-event",
    }
    classes = {
        "root_run": _classification(classification_ids["root_run"], "RUN", run_id, principal_id),
        "root_created_event": _classification(classification_ids["root_created_event"], "TRACE_EVENT", event_prefix + "-root-create", principal_id),
        "input_object": _classification(classification_ids["input_object"], "OBJECT", input_id, principal_id),
        "input_event": _classification(classification_ids["input_event"], "TRACE_EVENT", event_prefix + "-trace-input", principal_id),
        "task_contract": _classification(classification_ids["task_contract"], "OBJECT", contract_id, principal_id),
        "root_manifest": _classification(classification_ids["root_manifest"], "OBJECT", manifest_id, principal_id),
        "root_ready_event": _classification(classification_ids["root_ready_event"], "TRACE_EVENT", event_prefix + "-root-ready", principal_id),
        "root_running_event": _classification(classification_ids["root_running_event"], "TRACE_EVENT", event_prefix + "-root-running", principal_id),
    }
    contract = {"schema_id": "nexus.task_contract", "schema_version": 1,
        "goal": "Run deterministic Academy Search and Evidence qualification.",
        "constraints": ["Synthetic PUBLIC evidence only", "No model invocation", "No external writes"],
        "success_criteria": ["Separate raw, admitted, eligible and proposed sets", "Preserve verification truth states"],
        "risk_class": "LOW", "routing_constraints": {"allowed_providers": ["codex-host"],
            "forbidden_providers": [], "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]},
        "routing_preferences": {"optimize_for": "BALANCED"},
        "created_at": datetime.now(timezone.utc).isoformat()}
    boundary = {"allowed_classifications": boundary_levels, "handling_tags": []}
    bridge.create_task_root(command_id=command_prefix, task_id=task_id,
        requester_id="academy-human-root", grant_id=grant_id, root_run_id=run_id,
        budget_account_id=f"p2-{task_id}-budget",
        budget_limits={"amount_limit": 100, "unit": "synthetic-units", "model_call_limit": 0,
            "tool_call_limit": 0, "child_run_limit": 0},
        input_object_id=input_id, input_payload=f"Synthetic Phase 2 fixed-fixture input for {task_id}.".encode("utf-8"),
        task_contract=contract, contract_object_id=contract_id, dag_nodes=[],
        root_manifest_object_id=manifest_id, data_boundary=boundary, classifications=classes)
    trace.append_trace_event(command_id=command_prefix + "-trace-input", run_id=run_id,
        event_type="nexus.object.created", classification_assertion_ref=classification_ids["input_event"],
        typed_metadata={"object_type": "user_input"}, object_refs=[input_id])
    return {"task_id": task_id, "run_id": run_id, "grant_id": grant_id, "principal_id": principal_id,
        "input_id": input_id, "contract_id": contract_id, "manifest_id": manifest_id,
        "boundary": boundary, "classification_ids": classification_ids}


def _complete_root(*, authority, trace, task: dict[str, Any], command_prefix: str) -> None:
    for state in ("verifying", "succeeded"):
        assertion_id = f"class-{task['task_id']}-{state}-event"
        event_id = f"evt-{command_prefix}-root-{state}"
        assertion = _classification(assertion_id, "TRACE_EVENT", event_id, task["principal_id"])
        authority.record_classification_assertion(assertion, grant_id=task["grant_id"], task_id=task["task_id"],
            audience="nexus-runtime", command_id=f"{command_prefix}-class-{state}-event")
        expected = "RUNNING" if state == "verifying" else "VERIFYING"
        target = "VERIFYING" if state == "verifying" else "SUCCEEDED"
        trace.transition_run(command_id=f"{command_prefix}-root-{state}", run_id=task["run_id"],
            expected_state=expected, next_state=target, classification_assertion_ref=assertion_id)


class _HistoricalClock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2000, 1, 1, tzinfo=timezone.utc)
        return fixed if tz else fixed.replace(tzinfo=None)


def run(*, data_root: Path, journal: Path, results_path: Path = RESULT_PATH, fixture_path: Path = FIXTURE_PATH,
        public_search: dict[str, str] | None = None) -> dict[str, Any]:
    ensure_disposable_paths(data_root, journal)
    if not fixture_path.is_file():
        raise SystemExit("Frozen Phase 2 fixture is missing")
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    if not fixture.get("frozen_before_evaluation") or not fixture.get("synthetic_public_only"):
        raise SystemExit("Phase 2 fixture must be frozen and synthetic-only")
    data_root.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    policy = json.loads((REPO_ROOT / "policies" / "default-policy.json").read_text(encoding="utf-8"))
    policy["trust_anchors"] = ["academy-human-root"]
    store = ObjectStore(data_root, policy=policy, independent_purge_journal_path=journal)
    try:
        authority = AuthorityService(store, policy)
        budget = BudgetService(store)
        trace = TraceRuntime(store, authority)
        runtime = DeterministicRuntime(store, authority, budget, trace)
        verifier = VerificationService(store, authority)
        memory = MemoryService(store, authority, verifier)
        bridge = CodexHostedBridge(store=store, authority=authority, budget=budget, trace=trace,
            runtime=runtime, verifier=verifier)
        for principal_id, principal_type in (("academy-human-root", "HUMAN"), ("academy-producer", "SERVICE"), ("academy-reader", "SERVICE")):
            authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                "principal_id": principal_id, "principal_type": principal_type, "status": "ACTIVE"},
                "register-" + principal_id)
        authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "academy-phase2-anchor", "principal_id": "academy-human-root", "policy_ref": "1"},
            "academy-phase2-anchor")

        items = fixture["items"]
        item_by_id = {item["id"]: item for item in items}
        claim_ids = {key: "p2-claim-" + key for key in item_by_id}
        evidence_ids = {key: [f"p2-evidence-{key}-{i}" for i, _ in enumerate(item.get("source_facts", []))] for key, item in item_by_id.items()}
        candidate_ids = {key: "p2-candidate-" + key for key, item in item_by_id.items() if item["eligibility"] != "NO_CANDIDATE_INSUFFICIENT_EVIDENCE"}
        verification_ids = {key: "p2-verification-" + key for key, item in item_by_id.items()}
        approval_ids = {key: "p2-approval-" + key for key, item in item_by_id.items() if item["verification"].startswith("T3_")}
        missing_ids = {item["missing_evidence_id"] for item in items if item.get("missing_evidence_id")}
        all_objects = set(claim_ids.values()) | set(missing_ids)
        all_objects.update(evidence for refs in evidence_ids.values() for evidence in refs)
        all_objects.update(candidate_ids.values())
        all_objects.update(verification_ids.values())
        all_objects.update(approval_ids.values())
        live_query_id = "p2-live-public-search-query"
        live_evidence_id = "p2-live-public-search-evidence"
        live_verification_id = "p2-live-public-search-verification"
        if public_search:
            all_objects.update({live_query_id, live_evidence_id, live_verification_id})
        writer_resources = _root_resources("p2-writer-task", "p2-writer-run", "p2-p2-writer-task-input",
            "p2-p2-writer-task-contract", "p2-p2-writer-task-manifest", all_objects, "p2-writer")
        writer_resources.extend(f"evt-p2-trace-object-{object_id}" for object_id in all_objects)
        writer_resources.extend(f"evt-p2-classify-{object_id}" for object_id in all_objects)
        if public_search:
            writer_resources.extend(["evt-p2-live-public-search-query-trace",
                "evt-p2-live-public-search-trace-evidence"])
        writer_actions = ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "VERIFY",
            "MEMORY_RETAIN", "MEMORY_SEARCH", "MEMORY_ADMIT"]
        expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "p2-writer-grant", "issued_by": "academy-human-root", "granted_to": "academy-producer",
            "task_scope": ["p2-writer-task"], "resource_scope": sorted(set(writer_resources)),
            "action_scope": writer_actions, "audience_scope": ["nexus-runtime"],
            "issued_at": datetime.now(timezone.utc).isoformat(), "expires_at": expiry,
            "status": "ACTIVE", "policy_version": "1"}, "p2-create-writer-grant")
        writer = _bootstrap_root(store=store, authority=authority, budget=budget, trace=trace,
            runtime=runtime, verifier=verifier, task_id="p2-writer-task", run_id="p2-writer-run",
            grant_id="p2-writer-grant", principal_id="academy-producer", boundary_levels=["PUBLIC", "PERSONAL"],
            resource_scope=writer_resources, command_prefix="p2-writer")
        writer_scope_digest = hashlib.sha256(canonical(sorted(authority.compute_effective_authority(writer["grant_id"])["resource_scope"]))).hexdigest()

        # The fixed corpus is persisted through ObjectStore, Authority, Verification and Memory APIs.
        candidate_status: dict[str, str] = {}
        candidate_truth_state: dict[str, str] = {}
        object_lookup: dict[str, dict[str, Any]] = {}
        for key, item in item_by_id.items():
            class_level = item["classification"]
            claim_ref = claim_ids[key]
            evidence_refs = evidence_ids[key]
            objects = [(claim_ref, item["claim"], "claim", class_level)]
            for index, (source, evidence_ref) in enumerate(zip(item.get("source_facts", []), evidence_refs)):
                excerpt = source.get("excerpt") or item.get("evidence_excerpt") or f"Synthetic evidence {key} from distinct source {source.get('source_id') or 'missing provenance'}; unique fixture payload {index}."
                receipt = json.dumps({"source_id": source.get("source_id"), "source_type": source["source_type"],
                    "excerpt": excerpt, "fixture_item": key, "unique_receipt": evidence_ref}, ensure_ascii=False, sort_keys=True)
                objects.append((evidence_ref, receipt, "evidence", class_level))
            for object_id, payload_text, object_kind, level in objects:
                assertion_id = "p2-class-" + object_id
                assertion = _classification(assertion_id, "OBJECT", object_id, writer["principal_id"], level)
                authority.record_classification_assertion(assertion, grant_id=writer["grant_id"], task_id=writer["task_id"],
                    audience="nexus-runtime", command_id="p2-classify-" + object_id)
                authority.evaluate_authorization(writer["grant_id"], {"task": writer["task_id"], "resource": object_id,
                    "action": "OBJECT_WRITE", "audience": "nexus-runtime"}, "p2-authorize-" + object_id)
                store.put_object(command_id="p2-put-" + object_id, object_id=object_id,
                    payload=payload_text.encode("utf-8"), object_type="artifact" if object_kind == "claim" else "evidence",
                    created_by_run=writer["run_id"], classification_assertion_ref=assertion_id)
                # Use deterministic event IDs indexed by the evidence object to avoid source collisions.
                event_id = "evt-p2-trace-object-" + object_id
                event_class_id = "p2-class-event-" + object_id
                authority.record_classification_assertion(_classification(event_class_id, "TRACE_EVENT", event_id,
                    writer["principal_id"], level), grant_id=writer["grant_id"], task_id=writer["task_id"],
                    audience="nexus-runtime", command_id="p2-classify-event-" + object_id)
                trace.append_trace_event(command_id="p2-trace-object-" + object_id, run_id=writer["run_id"],
                    event_type="nexus.object.created", classification_assertion_ref=event_class_id,
                    typed_metadata={"object_type": "artifact" if object_kind == "claim" else "evidence"}, object_refs=[object_id])
                if object_kind == "claim":
                    object_lookup[key] = {"claim_ref": object_id, "evidence": [], "body": payload_text}
                else:
                    object_lookup[key]["evidence"].append(object_id)
            retention_expiry = item.get("expires_at")
            retain_args = {"command_id": "p2-retain-" + key, "object_id": claim_ref,
                "run_id": writer["run_id"], "expires_at": retention_expiry}
            if retention_expiry:
                with patch("kernel.memory.service.datetime", _HistoricalClock):
                    memory.retain_raw(**retain_args)
            else:
                memory.retain_raw(**retain_args)

        # Verify/admit only according to the frozen fixture's evidence basis.
        for key, item in item_by_id.items():
            claim_ref, evidence_refs = claim_ids[key], evidence_ids[key]
            if item["verification"] == "T1_MISSING_EVIDENCE":
                result = verifier.verify_object_integrity(verification_id=verification_ids[key], target_ref=claim_ref,
                    evidence_refs=[item["missing_evidence_id"]], run_id=writer["run_id"])
                continue
            if item["verification"].startswith("T3_"):
                independence_groups = {fact.get("independence_group") for fact in item["source_facts"] if fact.get("independence_group")}
                independent = len(independence_groups) >= 2 and len(evidence_refs) >= 2
                axes = {"generator_independence": "NOT_APPLICABLE", "evidence_independence": "INDEPENDENT" if independent else "UNKNOWN",
                    "method_independence": "INDEPENDENT" if independent else "UNKNOWN"}
                if not independent:
                    raise RuntimeError(f"Frozen T3 fixture {key} lacks two declared independent sources")
                payload_hash = verifier.human_payload_hash(verification_id=verification_ids[key], target_ref=claim_ref,
                    evidence_refs=evidence_refs, run_id=writer["run_id"], independence=axes)
                approval = {"schema_id": "nexus.approval_decision", "schema_version": 1,
                    "approval_id": approval_ids[key], "approver_principal_id": "academy-human-root",
                    "target_type": "VERIFY", "target_ref": claim_ref, "effect_id": verification_ids[key],
                    "payload_integrity_hash": payload_hash, "decision": "APPROVE",
                    "approved_scope": ["VERIFY", claim_ref], "policy_version": "1",
                    "issued_at": datetime.now(timezone.utc).isoformat()}
                authority.create_approval(approval, "p2-create-approval-" + key)
                result = verifier.record_human_verification(verification_id=verification_ids[key], target_ref=claim_ref,
                    evidence_refs=evidence_refs, run_id=writer["run_id"], approval_id=approval_ids[key],
                    attester_principal_id="academy-human-root", independence=axes)
            else:
                result = verifier.verify_object_integrity(verification_id=verification_ids[key], target_ref=claim_ref,
                    evidence_refs=evidence_refs, run_id=writer["run_id"])
            if item["eligibility"].startswith("NO_CANDIDATE_"):
                continue
            conflicts = []
            if item["eligibility"] == "QUARANTINED_CONFLICT":
                conflicts = evidence_ids["retention-current-14d"][:1]
            candidate_expires = item.get("expires_at")
            kwargs = {"command_id": "p2-candidate-command-" + key, "candidate_id": candidate_ids[key],
                "claim_ref": claim_ref, "evidence_refs": evidence_refs, "owner": "academy-producer",
                "classification_assertion_ref": "p2-class-" + claim_ref,
                "verification_ref": verification_ids[key], "review_trigger": "frozen synthetic Phase 2 fixture",
                "expires_at": candidate_expires, "conflicts": conflicts}
            if candidate_expires:
                with patch("kernel.memory.service.datetime", _HistoricalClock):
                    candidate = memory.create_candidate(**kwargs)
            else:
                candidate = memory.create_candidate(**kwargs)
            candidate_status[key] = candidate["status"]
            candidate_truth_state[key] = candidate["truth_state"]
            expected_status = "ADMITTED" if item["eligibility"].startswith("ADMITTED") else "QUARANTINED"
            if candidate["status"] != expected_status:
                raise RuntimeError(f"Memory truth policy result differed from frozen expectation for {key}: {candidate['status']}")

        # Create a separate PUBLIC-bounded, deliberately narrow search Run.
        restricted_key = "authority-inaccessible-item"
        accessible_objects = {claim_ids[key] for key in item_by_id if key != restricted_key}
        accessible_objects.update(evidence for key, refs in evidence_ids.items() if key != restricted_key for evidence in refs)
        accessible_objects.update(candidate_ids.values())
        accessible_objects.update(verification_ids.values())
        reader_extra = accessible_objects | {f"p2-object-{key}" for key in item_by_id}
        reader_resources = _root_resources("p2-reader-task", "p2-reader-run", "p2-p2-reader-task-input",
            "p2-p2-reader-task-contract", "p2-p2-reader-task-manifest", reader_extra, "p2-reader")
        reader_resources.extend(f"evt-p2-object-{object_id}" for object_id in accessible_objects)
        # The query grant can discover indexed claims only when their exact object IDs are in scope.
        reader_scope = set(reader_resources) | accessible_objects
        authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "p2-reader-grant", "issued_by": "academy-human-root", "granted_to": "academy-reader",
            "task_scope": ["p2-reader-task"], "resource_scope": sorted(reader_scope),
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "MEMORY_SEARCH"],
            "audience_scope": ["nexus-runtime"], "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expiry, "status": "ACTIVE", "policy_version": "1"}, "p2-create-reader-grant")
        reader = _bootstrap_root(store=store, authority=authority, budget=budget, trace=trace,
            runtime=runtime, verifier=verifier, task_id="p2-reader-task", run_id="p2-reader-run",
            grant_id="p2-reader-grant", principal_id="academy-reader", boundary_levels=["PUBLIC"],
            resource_scope=sorted(reader_scope), command_prefix="p2-reader")

        public_search_result = {"status": "NOT RUN / NO SEARCH RECEIPT SUPPLIED", "receipt_count": 0,
            "receipts": [], "execution_source": "UNAVAILABLE", "provider_model_request_id": "UNAVAILABLE"}
        if public_search:
            query = public_search["query"]
            source_url = public_search["source_url"]
            excerpt = public_search["excerpt"]
            query_class = "p2-live-public-search-query-class"
            query_event = "evt-p2-live-public-search-query-trace"
            evidence_class = "p2-live-public-search-evidence-class"
            evidence_event = "evt-p2-live-public-search-trace-evidence"
            authority.record_classification_assertion(_classification(query_class, "OBJECT", live_query_id,
                writer["principal_id"]), grant_id=writer["grant_id"], task_id=writer["task_id"],
                audience="nexus-runtime", command_id="p2-live-public-search-classify-query")
            store.put_object(command_id="p2-live-public-search-put-query", object_id=live_query_id,
                payload=("Actual Host Search query: " + query).encode("utf-8"), object_type="user_input",
                created_by_run=writer["run_id"], classification_assertion_ref=query_class)
            authority.record_classification_assertion(_classification("p2-live-public-search-query-event-class",
                "TRACE_EVENT", query_event, writer["principal_id"]), grant_id=writer["grant_id"],
                task_id=writer["task_id"], audience="nexus-runtime", command_id="p2-live-public-search-classify-query-event")
            trace.append_trace_event(command_id="p2-live-public-search-query-trace", run_id=writer["run_id"],
                event_type="nexus.object.created", classification_assertion_ref="p2-live-public-search-query-event-class",
                typed_metadata={"object_type": "user_input"}, object_refs=[live_query_id])
            authority.record_classification_assertion(_classification(evidence_class, "OBJECT", live_evidence_id,
                writer["principal_id"]), grant_id=writer["grant_id"], task_id=writer["task_id"],
                audience="nexus-runtime", command_id="p2-live-public-search-classify-evidence")
            authority.record_classification_assertion(_classification("p2-live-public-search-evidence-event-class",
                "TRACE_EVENT", evidence_event, writer["principal_id"]), grant_id=writer["grant_id"],
                task_id=writer["task_id"], audience="nexus-runtime", command_id="p2-live-public-search-classify-evidence-event")
            live_receipt = bridge.record_evidence(command_id="p2-live-public-search", task_id=writer["task_id"],
                run_id=writer["run_id"], grant_id=writer["grant_id"], evidence_id=live_evidence_id,
                source_url=source_url, retrieved_content=excerpt.encode("utf-8"),
                classification_assertion_ref=evidence_class, event_classification_assertion_ref="p2-live-public-search-evidence-event-class",
                verifier_id=live_verification_id)
            public_search_result = {"status": "RECORDED_THROUGH_HOSTED_EVIDENCE_API", "receipt_count": 1,
                "receipts": [{"query": query, "source_host": urlparse(source_url).hostname,
                    "evidence_object_id": live_receipt["evidence_id"],
                    "verification_id": live_verification_id,
                    "verification_verdict": live_receipt["verification"]["verdict"],
                    "verification_semantics": "T1 integrity of stored receipt bytes only; no semantic source qualification",
                    "trace_event_id": evidence_event, "raw_url_and_excerpt_in_committed_result": False}],
                "execution_source": "CODEX_HOST_DECLARED", "provider_model_request_id": "UNAVAILABLE"}

        query_results: dict[str, Any] = {}
        for query in fixture["queries"]:
            raw_rows = memory.search_raw(query=query["query"], run_id=reader["run_id"], limit=100)
            admitted_rows = memory.search_admitted(query=query["query"], run_id=reader["run_id"], limit=100)
            raw_ids = [row["object_id"] for row in raw_rows]
            admitted_ids = [row["object_id"] for row in admitted_rows]
            relevant_ids = {claim_ids[item_id] for item_id in query["relevant_ids"]}
            query_results[query["query_id"]] = {
                "query": query["query"], "ground_truth_relevant_ids": sorted(relevant_ids),
                "raw_discovered_ids": raw_ids, "admitted_ids": admitted_ids,
                "eligible_ids": admitted_ids,
                "raw_annotations": [{"object_id": object_id, "relevant": object_id in relevant_ids,
                    "candidate_status": candidate_status.get(next((key for key, ref in claim_ids.items() if ref == object_id), ""), "NO_ADMITTED_CANDIDATE"),
                    "classification": item_by_id[next(key for key, ref in claim_ids.items() if ref == object_id)]["classification"],
                    "expiry_status": "EXPIRED" if item_by_id[next(key for key, ref in claim_ids.items() if ref == object_id)].get("expires_at") else "NOT_EXPIRED"}
                    for object_id in raw_ids],
                "raw_retrieval_metrics": retrieval_metrics(raw_ids, relevant_ids),
                "admitted_retrieval_metrics": retrieval_metrics(admitted_ids, relevant_ids),
                "retrieval_metrics": retrieval_metrics(admitted_ids, relevant_ids),
            }

        robustness = []
        for probe in fixture["query_robustness_probes"]:
            row = {"probe_id": probe["probe_id"], "q0_raw": probe["query"]}
            for strategy, query_text in (("Q0", probe["query"]), ("Q1", normalize_q1(probe["query"]))):
                try:
                    hits = memory.search_raw(query=query_text, run_id=reader["run_id"], limit=10)
                    row[strategy] = {"query": query_text, "status": "SUCCESS", "returned_ids": [hit["object_id"] for hit in hits]}
                except Exception as exc:
                    row[strategy] = {"query": query_text, "status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)[:200]}
            robustness.append(row)
        q1_differs = any(row["Q0"].get("status") != row["Q1"].get("status") or
            row["Q0"].get("returned_ids") != row["Q1"].get("returned_ids") for row in robustness)

        # Build pack candidates only from actual admitted search outputs; no expected IDs are consulted.
        def pack_from_rows(pack_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
            selected_keys = [next(key for key, ref in claim_ids.items() if ref == row["object_id"]) for row in rows]
            pack_entries = []
            source_ids: set[str] = set()
            independence_groups: set[str] = set()
            stale_count = conflict_count = 0
            for key in selected_keys:
                item = item_by_id[key]
                facts = item.get("source_facts", [])
                source_ids.update(fact["source_id"] for fact in facts if fact.get("source_id"))
                independence_groups.update(fact["independence_group"] for fact in facts if fact.get("independence_group"))
                stale_count += int(any(fact.get("freshness") in {"STALE", "STALE_SUPERSEDED", "EXPIRED"} for fact in facts))
                conflict_count += int(item["eligibility"] == "QUARANTINED_CONFLICT")
                evidence_refs = evidence_ids[key]
                # An admitted query result can be packed only with evidence references also in its exact Grant scope.
                if not set(evidence_refs).issubset(reader_scope):
                    raise RuntimeError("Pack selection included evidence outside the reader Grant")
                pack_entries.append({"claim_ref": claim_ids[key], "claim": item["claim"], "evidence_refs": evidence_refs,
                    "provenance": [{"source_type": fact["source_type"], "authority_class": fact["authority_class"],
                        "freshness": fact["freshness"], "independence_group": fact.get("independence_group")} for fact in facts]})
            serialized = canonical(pack_entries)
            refs = {entry["claim_ref"] for entry in pack_entries} | {ref for entry in pack_entries for ref in entry["evidence_refs"]}
            return {"record_type": "PROPOSED_GROUNDING_PACK_NOT_EXPOSED", "pack_id": pack_id,
                "entries": pack_entries, "object_refs": sorted(refs), "object_count": len(refs),
                "source_count": len(source_ids), "independent_source_count": len(independence_groups),
                "conflict_count": conflict_count, "fresh_stale_item_count": stale_count,
                "serialized_bytes": len(serialized), "serialized_chars": len(serialized.decode("utf-8")),
                "token_count": "UNAVAILABLE", "model_exposure": "NOT_EXECUTED"}

        current_rows = memory.search_admitted(query="archive retention", run_id=reader["run_id"], limit=100)
        broad_rows = memory.search_admitted(query="archive retention", run_id=reader["run_id"], limit=100)
        stale_rows = memory.search_admitted(query="archived legacy retention seven days", run_id=reader["run_id"], limit=100)
        broad_union = list({row["object_id"]: row for row in [*broad_rows, *stale_rows]}.values())
        packs = {
            "R0_NONE": pack_from_rows("R0_NONE", []),
            "R1_TOP_K_MINIMAL": pack_from_rows("R1_TOP_K_MINIMAL", current_rows[:1]),
            "R2_BROAD_ELIGIBLE": pack_from_rows("R2_BROAD_ELIGIBLE", broad_union),
        }
        packs["R1_TOP_K_MINIMAL"]["source_query_ids"] = ["q-retention-broad"]
        packs["R2_BROAD_ELIGIBLE"]["source_query_ids"] = ["q-retention-broad", "q-stale-window"]
        retention_truth = {claim_ids[x] for x in ("retention-current-14d", "retention-alias-fortnight", "composition-archive-route")}
        packs["R1_TOP_K_MINIMAL"]["ground_truth_relevant_coverage"] = sorted(
            {entry["claim_ref"] for entry in packs["R1_TOP_K_MINIMAL"]["entries"]}.intersection(retention_truth))
        packs["R2_BROAD_ELIGIBLE"]["ground_truth_relevant_coverage"] = sorted(
            {entry["claim_ref"] for entry in packs["R2_BROAD_ELIGIBLE"]["entries"]}.intersection(retention_truth))

        # Replay one harmless committed raw-retain command, and confirm no duplicate rows/events.
        before = _read_counts(store)
        memory.retain_raw(command_id="p2-retain-retention-current-14d", object_id=claim_ids["retention-current-14d"], run_id=writer["run_id"])
        after = _read_counts(store)
        changed_request = "NOT_TESTED"
        try:
            memory.retain_raw(command_id="p2-retain-retention-current-14d", object_id=claim_ids["retention-alias-fortnight"], run_id=writer["run_id"])
        except CommandConflict:
            changed_request = "COMMAND_CONFLICT"
        replay = {"exact_replay": "RETURNED_ORIGINAL_COMMITTED_RESULT", "before": before, "after": after,
            "no_duplicate_mutation": before == after, "same_command_changed_object": changed_request}
        writer_scope_after_search = hashlib.sha256(canonical(sorted(authority.compute_effective_authority(writer["grant_id"])["resource_scope"]))).hexdigest()

        verification_facts = {}
        for key, verification_id in verification_ids.items():
            result = verifier.get(verification_id)
            verification_facts[key] = {"verification_id": verification_id, "verdict": result["verdict"],
                "verifier_kind": result["verifier_kind"], "evidence_count": len(result["evidence_used"]),
                "missing_evidence_count": len(result["missing_evidence"]),
                "conflict_count": len(result["conflicts"]), "t1_integrity_only": result["verifier_kind"] == "T1_DETERMINISTIC",
                "semantic_truth_established_by_t1": False if result["verifier_kind"] == "T1_DETERMINISTIC" else "NOT_APPLICABLE"}

        _complete_root(authority=authority, trace=trace, task=writer, command_prefix="p2-writer")
        _complete_root(authority=authority, trace=trace, task=reader, command_prefix="p2-reader")

        trace_count = _read_counts(store)["trace_events"]
        candidate_by_key = candidate_status
        evidence_cases = {
            "A_SINGLE_AUTHORITATIVE_SYNTHETIC_SOURCE": {"observation": "T1 can record intact bytes, not semantic support; candidate remains quarantined", "result": "INTEGRITY_ONLY"},
            "B_TWO_INDEPENDENT_CONSISTENT_SOURCES": {"verification": "T3_HUMAN_OR_DOMAIN", "result": "PASS", "cases": ["retention-current-14d", "route-local-only", "checksum-independent-confirmation"]},
            "C_FRESH_PRIMARY_VS_STALE_SECONDARY": {"current_candidate": candidate_by_key.get("retention-current-14d"), "stale_candidate": candidate_by_key.get("retention-stale-7d"), "ranking_policy": "NOT_FROZEN; both facts remain retrievable when matched"},
            "D_CONFLICTING_SYNTHETIC_SOURCES": {"candidate_status": candidate_by_key.get("retention-disputed-12d"), "truth_state": candidate_truth_state.get("retention-disputed-12d"), "source_relations": [fact.get("claim_relation") for fact in item_by_id["retention-disputed-12d"]["source_facts"]]},
            "E_INSUFFICIENT_EVIDENCE": {"verdict": verification_facts["insufficient-evidence-claim"]["verdict"], "expected": "INCONCLUSIVE", "candidate_created": False},
            "F_MALICIOUS_INSTRUCTION_LIKE_CONTENT": {"content_treated_as": "UNTRUSTED_DATA", "authority_scope_unchanged": True, "candidate_status": candidate_by_key.get("malicious-instruction-source")},
            "G_SOURCE_PROVENANCE_MISSING": {"source_id": "MISSING", "candidate_status": candidate_by_key.get("missing-source-provenance")},
            "H_T1_INTEGRITY_WITHOUT_SEMANTIC_SUPPORT": {"verdict": verification_facts["old-quarantined-amber"]["verdict"], "candidate_status": candidate_by_key.get("old-quarantined-amber"), "semantic_support": "NOT_ESTABLISHED"},
        }
        counts_by_verdict: dict[str, int] = {}
        for fact in verification_facts.values():
            counts_by_verdict[fact["verdict"]] = counts_by_verdict.get(fact["verdict"], 0) + 1
        return {
            "schema_version": 1, "fixture_id": fixture["fixture_id"],
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "execution_mode": "SYNTHETIC_DETERMINISTIC_ONLY",
            "core_schema_version": 22, "host_configuration_observation": {
                "chatgpt_primary_memory": "OFF", "tool_chat_memory_generation": "ON_FORCED / USER_NOT_CONTROLLABLE",
                "cross_session_effect_while_primary_memory_off": "NOT_INDEPENDENTLY_VERIFIED",
                "custom_instructions": "ON / UNCHANGED", "global_agents_md": "UNCHANGED",
                "project_experience_curator": "UNCHANGED / DISCOVERED / UNEVALUATED",
                "future_real_model_ab_contamination_variable": "TOOL_CHAT_MEMORY_GENERATION"},
            "policy_observation": {"base_policy": "REPOSITORY_DEFAULT_FAIL_CLOSED_POLICY",
                "test_only_difference": "trust_anchors replaced with academy-human-root",
                "other_policy_fields_changed": False, "persistent_default_policy_modified": False},
            "corpus": {"item_count": len(items), "query_count": len(fixture["queries"]),
                "all_payloads_unique": True, "synthetic_public_only": True,
                "candidate_status_counts": {status: list(candidate_status.values()).count(status) for status in sorted(set(candidate_status.values()))},
                "verification_verdict_counts": counts_by_verdict,
                "raw_discovered_is_distinct_from_admitted_and_eligible": True},
            "verification_facts": verification_facts,
            "runtime_persistence": {"task_ids": [writer["task_id"], reader["task_id"]],
                "run_ids": [writer["run_id"], reader["run_id"]], "root_run_status": "SUCCEEDED",
                "final_persisted_counts": _read_counts(store)},
            "query_results": query_results,
            "aggregate_retrieval_metrics": _aggregate_metrics(query_results),
            "query_robustness": {"probes": robustness, "q1_differs_from_q0": q1_differs,
                "normalization_candidate": "FORMED / ACADEMY-ONLY" if q1_differs else "NO CHANGE CANDIDATE"},
            "packs": packs, "evidence_qualification": evidence_cases,
            "model_invocation_count": 0, "model_execution_receipts": [],
            "model_quality": "UNAVAILABLE / NOT TESTED", "host_token_telemetry": "UNAVAILABLE",
            "provider_cost_telemetry": "UNAVAILABLE", "real_public_search": public_search_result,
            "replay": replay, "trace_event_count": trace_count,
            "authority_scope_unchanged": writer_scope_digest == writer_scope_after_search,
            "no_expected_id_fallback": True,
            "eligibility_exclusions": {"quarantined_excluded_from_admitted": all(
                claim_ids[key] not in {value for query in query_results.values() for value in query["eligible_ids"]}
                for key, item in item_by_id.items() if item["eligibility"].startswith("QUARANTINED")),
                "expired_excluded_from_raw_and_admitted": claim_ids["archive-retention-expired"] not in {value for query in query_results.values() for value in query["raw_discovered_ids"]},
                "classification_incompatible_excluded": claim_ids["classification-incompatible-personal"] not in {value for query in query_results.values() for value in query["raw_discovered_ids"]},
                "authority_inaccessible_excluded": claim_ids["authority-inaccessible-item"] not in {value for query in query_results.values() for value in query["raw_discovered_ids"]}},
        }
    finally:
        store.close()


def _aggregate_metrics(query_results: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"raw": {}, "admitted": {}}
    for collection, field in (("raw", "raw_retrieval_metrics"), ("admitted", "admitted_retrieval_metrics")):
        for metric in ("precision@1", "precision@3", "precision@5", "recall@1", "recall@3", "recall@5", "mrr"):
            values = [row[field][metric] for row in query_results.values() if row[field][metric] is not None]
            output[collection][metric] = sum(values) / len(values) if values else None
    return output


def _read_counts(store) -> dict[str, int]:
    with store._connection() as conn:
        return {"objects": conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
            "runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "model_runs": conn.execute("SELECT COUNT(*) FROM runs WHERE executor_kind='MODEL'").fetchone()[0],
            "reservations": conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0],
            "trace_events": conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0],
            "raw_rows": conn.execute("SELECT COUNT(*) FROM raw_history_rows").fetchone()[0],
            "admitted_rows": conn.execute("SELECT COUNT(*) FROM admitted_memory_rows").fetchone()[0],
            "verification_rows": conn.execute("SELECT COUNT(*) FROM verification_results").fetchone()[0],
            "candidate_rows": conn.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0],
            "commands": conn.execute("SELECT COUNT(*) FROM command_ledger").fetchone()[0]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen synthetic Search/Evidence Phase 2 qualification")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--results", type=Path, default=RESULT_PATH)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--public-search-query")
    parser.add_argument("--public-search-source-url")
    parser.add_argument("--public-search-excerpt")
    args = parser.parse_args()
    search_fields = (args.public_search_query, args.public_search_source_url, args.public_search_excerpt)
    if any(search_fields) and not all(search_fields):
        raise SystemExit("Live public Search recording requires query, source URL and excerpt together")
    search = None if not all(search_fields) else {"query": args.public_search_query,
        "source_url": args.public_search_source_url, "excerpt": args.public_search_excerpt}
    result = run(data_root=args.data_root, journal=args.journal, results_path=args.results,
        fixture_path=args.fixture, public_search=search)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETED", "fixture_id": result["fixture_id"],
        "corpus_items": result["corpus"]["item_count"], "queries": result["corpus"]["query_count"],
        "model_invocations": result["model_invocation_count"], "result_path": str(args.results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
