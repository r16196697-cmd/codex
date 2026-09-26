"""Run the synthetic Nexus Bootstrap Academy Phase 0 fixture via public APIs.

This runner records Host-declared outputs through the existing Hosted Bridge. The
bridge does not invoke or isolate a model; results therefore measure Core-side
retrieval, eligibility, manifest exposure declarations and durable lifecycle,
not a controlled provider/model A/B experiment.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args()
    root = args.data_root.resolve()
    journal = args.journal.resolve()
    repo = args.repo_root.resolve()
    if (root / "nexus.sqlite").exists() or any(root.iterdir()):
        raise SystemExit("Refusing to initialize a non-empty Academy data root")
    if journal.exists():
        raise SystemExit("Refusing to overwrite an existing independent journal")

    default_policy = json.loads((repo / "policies/default-policy.json").read_text(encoding="utf-8"))
    policy = {**default_policy, "trust_anchors": ["academy-human-root"]}
    root.mkdir(parents=True, exist_ok=True)
    (root / "policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    source_text = (
        "Synthetic local file for Nexus Bootstrap Academy Phase 0.\n"
        "The demo code word is cedar. The demo retention window is seven days.\n"
        "The sample route is local-only. The archive format is JSON Lines.\n"
    )
    source_path = root / "nexus-academy-source.txt"
    source_path.write_text(source_text, encoding="utf-8")

    task_id = "academy-context-phase0"
    root_run_id = "academy-root-phase0"
    input_id = "academy-task-input-phase0"
    contract_id = "academy-task-contract-phase0"
    root_manifest_id = "academy-root-manifest-phase0"
    corpus = [
        ("academy-codeword", "The Academy demo code word is cedar.", True),
        ("demo-retention", "The demo retention window is seven days.", True),
        ("demo-route", "The sample route is local-only.", True),
        ("archive-format", "The demo archive format is JSON Lines.", True),
        ("review-owner", "The synthetic review owner is Mira.", True),
        ("production-codeword", "The production demo code word is maple.", True),
        ("quarantined-old-codeword", "An unverified old note says the Academy code word is amber.", False),
        ("quarantined-conflict", "An unverified note conflicts and says the current code word is birch.", False),
    ]
    for i in range(20):
        corpus.append((f"unrelated-{i:02d}", f"Synthetic unrelated public note {i:02d}: topic {chr(97 + i % 26)}.", False))
    evidence_for = {
        "academy-codeword": "Evidence confirms the Academy demo code word is cedar.",
        "demo-retention": "Evidence confirms the demo retention window is seven days.",
        "demo-route": "Evidence confirms the sample route is local-only.",
        "archive-format": "Evidence confirms the demo archive format is JSON Lines.",
        "review-owner": "Evidence confirms the synthetic review owner is Mira.",
        "production-codeword": "Evidence confirms the production demo code word is maple.",
        "quarantined-old-codeword": "Unverified source excerpt: old Academy code word amber.",
        "quarantined-conflict": "Unverified conflicting source excerpt: current code word birch.",
    }
    objects = {}
    for slug, body, admitted in corpus:
        claim_id = "academy-memory-" + slug
        evidence_id = "academy-evidence-" + slug
        objects[slug] = {"claim": claim_id, "evidence": evidence_id, "body": body, "admitted_fixture": admitted}
    capability_metadata_id = "academy-capability-read-metadata"
    capability_instruction_id = "academy-capability-read-instruction"

    node_ids = ["academy-node-c0", "academy-node-c1", "academy-node-c2", "academy-node-p0", "academy-node-p1", "academy-node-p2"]
    run_ids = ["academy-run-c0", "academy-run-c1", "academy-run-c2", "academy-run-p0", "academy-run-p1", "academy-run-p2"]
    manifest_ids = ["academy-manifest-c0", "academy-manifest-c1", "academy-manifest-c2", "academy-manifest-p0", "academy-manifest-p1", "academy-manifest-p2"]
    artifact_ids = ["academy-output-c0", "academy-output-c1", "academy-output-c2", "academy-output-p0", "academy-output-p1", "academy-output-p2"]
    candidate_ids = ["academy-candidate-" + slug for slug in objects]
    all_object_ids = [input_id, contract_id, root_manifest_id, capability_metadata_id, capability_instruction_id]
    for item in objects.values():
        all_object_ids.extend([item["claim"], item["evidence"]])
    route_ids = ["route-" + run_id for run_id in run_ids]
    root_resources = set(["task:" + task_id, "runtime-mode:instance", task_id, root_run_id, input_id, contract_id, root_manifest_id, *node_ids, *all_object_ids, *candidate_ids, *run_ids, *manifest_ids, *artifact_ids, *route_ids])
    root_commands = ["academy-root-create", "academy-root-ready", "academy-root-running", "academy-root-verifying", "academy-root-succeeded", "academy-trace-input"]
    for command in root_commands:
        root_resources.add("evt-" + command)
    condition_keys = ("C0_NONE","C1_MINIMAL_RELEVANT","C2_BROAD_SAFE","P0_ABSENT","P1_METADATA","P2_FULL_INSTRUCTION")
    for condition_key in condition_keys:
        key = condition_key.lower().replace("_", "-")
        prefix = "academy-child-" + key
        output_prefix = "academy-output-" + key
        for event_id in ("evt-"+prefix+"-create-run", "evt-"+prefix+"-ready", "evt-"+prefix+"-running", "evt-"+prefix+"-setup-cancel",
                         "evt-"+output_prefix+"-trace-object", "evt-"+output_prefix+"-verifying", "evt-"+output_prefix+"-terminal"):
            root_resources.add(event_id)
    # Root Grant needs every possible child resource to remain a strict superset.
    root_actions = ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "VERIFY", "MEMORY_RETAIN", "MEMORY_SEARCH", "MEMORY_ADMIT", "INSPECT", "DELEGATE", "TOOL_READ"]

    store = ObjectStore(root, policy=policy, independent_purge_journal_path=journal)
    try:
        authority = AuthorityService(store, policy)
        budget = BudgetService(store)
        trace = TraceRuntime(store, authority)
        runtime = DeterministicRuntime(store, authority, budget, trace)
        verifier = VerificationService(store, authority)
        memory = MemoryService(store, authority, verifier)
        bridge = CodexHostedBridge(store=store, authority=authority, budget=budget, trace=trace, runtime=runtime, verifier=verifier)
        authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": "academy-human-root", "principal_type": "HUMAN", "status": "ACTIVE"}, "academy-principal-human")
        authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": "academy-host", "principal_type": "SERVICE", "status": "ACTIVE"}, "academy-principal-host")
        authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": "academy-model", "principal_type": "MODEL", "status": "ACTIVE"}, "academy-principal-model")
        authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": "academy-inspector", "principal_type": "HUMAN", "status": "ACTIVE"}, "academy-principal-inspector")
        authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "academy-anchor", "principal_id": "academy-human-root", "policy_ref": policy["policy_version"]}, "academy-anchor-register")
        expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"academy-inspect-grant","issued_by":"academy-human-root","granted_to":"academy-inspector",
            "task_scope":[task_id],"resource_scope":["task:"+task_id,"object:academy-output-c1","route:route-academy-run-c1"],"action_scope":["INSPECT"],
            "audience_scope":["nexus-inspect"],"issued_at":now(),"expires_at":expiry,"status":"ACTIVE","policy_version":policy["policy_version"]},"academy-inspect-grant-create")
        authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "academy-root-grant",
            "issued_by": "academy-human-root", "granted_to": "academy-host", "task_scope": [task_id],
            "resource_scope": sorted(root_resources), "action_scope": root_actions,
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now(), "expires_at": expiry,
            "status": "ACTIVE", "policy_version": policy["policy_version"],
        }, "academy-root-grant-create")
        boundary = {"allowed_classifications": ["PUBLIC"], "handling_tags": []}
        created = datetime.now(timezone.utc).isoformat()
        nodes = []
        for node_id in node_ids:
            nodes.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": node_id, "task_id": task_id,
                "input_object_refs": [input_id], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json",
                "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW", "validation_method": "SCHEMA",
                "budget_amount": 1, "requested_executor": "MODEL", "required_modalities": ["text"], "created_at": created})
        contract = {"schema_id": "nexus.task_contract", "schema_version": 1, "goal": "Measure synthetic context exposure without auto-selection or policy changes.",
            "constraints": ["PUBLIC only", "No external writes", "No model/provider identity inference"],
            "success_criteria": ["Persist exact exposure refs", "Keep quarantined memory out of grounding", "Record verification and provenance"],
            "risk_class": "LOW", "routing_constraints": {"allowed_providers": ["codex-host"], "forbidden_providers": [], "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]},
            "routing_preferences": {"optimize_for": "BALANCED"}, "created_at": created}
        root_classes = {
            "root_run": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-root-run","subject_type":"RUN","subject_ref":root_run_id,"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Phase 0","actor_id":"academy-host"},
            "root_created_event": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-root-create-event","subject_type":"TRACE_EVENT","subject_ref":"evt-academy-root-create","sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Phase 0","actor_id":"academy-host"},
            "input_object": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-input","subject_type":"OBJECT","subject_ref":input_id,"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic task prompt","actor_id":"academy-host"},
            "input_event": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-input-event","subject_type":"TRACE_EVENT","subject_ref":"evt-academy-trace-input","sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic input provenance","actor_id":"academy-host"},
            "task_contract": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-contract","subject_type":"OBJECT","subject_ref":contract_id,"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic contract","actor_id":"academy-host"},
            "root_manifest": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-root-manifest","subject_type":"OBJECT","subject_ref":root_manifest_id,"sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic manifest","actor_id":"academy-host"},
            "root_ready_event": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-root-ready","subject_type":"TRACE_EVENT","subject_ref":"evt-academy-root-ready","sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic root ready","actor_id":"academy-host"},
            "root_running_event": {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":"academy-class-root-running","subject_type":"TRACE_EVENT","subject_ref":"evt-academy-root-running","sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic root running","actor_id":"academy-host"},
        }
        bridge.create_task_root(command_id="academy", task_id=task_id, requester_id="academy-human-root", grant_id="academy-root-grant", root_run_id=root_run_id,
            budget_account_id="academy-budget", budget_limits={"amount_limit":100,"unit":"synthetic-units","model_call_limit":10,"tool_call_limit":4,"child_run_limit":10},
            input_object_id=input_id, input_payload=b"Phase 0 synthetic tasks: arithmetic without memory; retrieve one fact; combine facts; distinguish a similar distractor; handle quarantined conflict safely.",
            task_contract=contract, contract_object_id=contract_id, dag_nodes=nodes, root_manifest_object_id=root_manifest_id,
            data_boundary=boundary, classifications=root_classes)
        trace.append_trace_event(command_id="academy-trace-input", run_id=root_run_id, event_type="nexus.object.created", classification_assertion_ref="academy-class-input-event", typed_metadata={"object_type":"user_input"}, object_refs=[input_id])

        def classify_object(object_id: str, assertion_id: str, reason: str):
            authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":"OBJECT","subject_ref":object_id,
                "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":reason,"actor_id":"academy-host"}, grant_id="academy-root-grant", task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)

        # Persist the synthetic knowledge corpus through ObjectStore and Memory APIs only.
        admitted_ids = []
        quarantine_ids = []
        for slug, item in objects.items():
            for kind, body in (("claim", item["body"]), ("evidence", evidence_for.get(slug, f"Synthetic supporting note for unrelated topic {slug}."))):
                object_id = item[kind]
                class_id = "academy-class-" + object_id
                classify_object(object_id, class_id, "synthetic PUBLIC corpus item")
                store.put_object(command_id="put-"+object_id, object_id=object_id, payload=body.encode("utf-8"), object_type="artifact", created_by_run=root_run_id, classification_assertion_ref=class_id)
                memory.retain_raw(command_id="retain-"+object_id, object_id=object_id, run_id=root_run_id, expires_at=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat())
            if item["admitted_fixture"]:
                verification_id = "academy-t3-" + slug
                axes = {"generator_independence":"NOT_APPLICABLE","evidence_independence":"INDEPENDENT","method_independence":"INDEPENDENT"}
                payload_hash = verifier.human_payload_hash(verification_id=verification_id, target_ref=item["claim"], evidence_refs=[item["evidence"]], run_id=root_run_id, independence=axes)
                approval_id = "academy-approval-" + slug
                authority.create_approval({"schema_id":"nexus.approval_decision","schema_version":1,"approval_id":approval_id,"approver_principal_id":"academy-human-root","target_type":"VERIFY","target_ref":item["claim"],"effect_id":verification_id,
                    "payload_integrity_hash":payload_hash,"decision":"APPROVE","approved_scope":["VERIFY",item["claim"]],"policy_version":"1","issued_at":now()}, "create-"+approval_id)
                human = verifier.record_human_verification(verification_id=verification_id, target_ref=item["claim"], evidence_refs=[item["evidence"]], run_id=root_run_id, approval_id=approval_id, attester_principal_id="academy-human-root", independence=axes)
                candidate = memory.create_candidate(command_id="candidate-command-"+slug, candidate_id="academy-candidate-"+slug, claim_ref=item["claim"], evidence_refs=[item["evidence"]], owner="academy-host",
                    classification_assertion_ref="academy-class-"+item["claim"], verification_ref=human["verification_id"], review_trigger="synthetic Phase 0 fixture review")
                if candidate["status"] != "ADMITTED":
                    raise RuntimeError("Expected T3-backed synthetic memory to be ADMITTED")
                admitted_ids.append(item["claim"])
            elif slug.startswith("quarantined-"):
                quarantine_ids.append(item["claim"])
        # Two contradictory/unverified claims remain quarantined and are never exposed.
        for slug in ("quarantined-old-codeword", "quarantined-conflict"):
            item = objects[slug]
            verification_id = "academy-t1-" + slug
            t1 = verifier.verify_object_integrity(verification_id=verification_id, target_ref=item["claim"], evidence_refs=[item["evidence"]], run_id=root_run_id)
            candidate = memory.create_candidate(command_id="candidate-command-"+slug, candidate_id="academy-candidate-"+slug, claim_ref=item["claim"], evidence_refs=[item["evidence"]], owner="academy-host",
                classification_assertion_ref="academy-class-"+item["claim"], verification_ref=t1["verification_id"], review_trigger="unverified/conflicting synthetic note",
                conflicts=[objects["academy-codeword"]["evidence"]] if slug == "quarantined-conflict" else [])
            if candidate["status"] != "QUARANTINED":
                raise RuntimeError("Unapproved synthetic candidate escaped quarantine")

        for object_id, assertion_id, payload in (
            (capability_metadata_id,"academy-class-capability-metadata","Capability metadata: bounded read-only access to a local file under a reviewed root."),
            (capability_instruction_id,"academy-class-capability-instruction","Full instruction: validate the approved read-only descriptor, keep the relative path under the reviewed root, read no more than 1 MiB, do not write or use network, return the exact bytes and digest."),
        ):
            classify_object(object_id, assertion_id, "synthetic PUBLIC capability-presence probe")
            store.put_object(command_id="put-"+object_id, object_id=object_id, payload=payload.encode(), object_type="artifact", created_by_run=root_run_id, classification_assertion_ref=assertion_id)

        query_terms = ["academy code word", "retention", "sample route", "production code word"]
        raw_hits, admitted_hits = {}, {}
        for term in query_terms:
            raw_hits[term] = memory.search_raw(query=term, run_id=root_run_id, limit=100)
            admitted_hits[term] = memory.search_admitted(query=term, run_id=root_run_id, limit=100)
        c1_ids = sorted({row["object_id"] for term in query_terms for row in admitted_hits[term] if row["object_id"] in {objects[s]["claim"] for s in ("academy-codeword","demo-retention","demo-route","production-codeword")}})
        broad_terms = ["academy", "retention", "route", "JSON", "Mira", "production"]
        broad_raw_hits = {term: memory.search_raw(query=term, run_id=root_run_id, limit=100) for term in broad_terms}
        broad_admitted_hits = {term: memory.search_admitted(query=term, run_id=root_run_id, limit=100) for term in broad_terms}
        c2_ids = sorted({row["object_id"] for values in broad_admitted_hits.values() for row in values})
        if not set(c1_ids).issuperset({objects["academy-codeword"]["claim"], objects["demo-retention"]["claim"], objects["demo-route"]["claim"], objects["production-codeword"]["claim"]}):
            # Preserve the API search result as evidence while rejecting a silently incomplete minimal pack.
            expected_terms = {"academy-codeword":"academy code word", "demo-retention":"demo retention", "demo-route":"sample route", "production-codeword":"production code word"}
            for slug, term in expected_terms.items():
                hits = memory.search_admitted(query=term, run_id=root_run_id, limit=100)
                if any(row["object_id"] == objects[slug]["claim"] for row in hits):
                    c1_ids.append(objects[slug]["claim"])
            c1_ids = sorted(set(c1_ids))
        context_payloads = {object_id: store.get_payload(object_id).decode("utf-8") for object_id in sorted(set(c2_ids + [capability_metadata_id, capability_instruction_id]))}
        c0_ids = []
        conditions = {
            "C0_NONE": {"refs": c0_ids, "retrieved": 0, "eligible": 0},
            "C1_MINIMAL_RELEVANT": {"refs": c1_ids, "retrieved": len({row["object_id"] for values in raw_hits.values() for row in values}), "eligible": len(set(c1_ids))},
            "C2_BROAD_SAFE": {"refs": c2_ids, "retrieved": len({row["object_id"] for values in broad_raw_hits.values() for row in values}), "eligible": len(set(c2_ids))},
        }
        if set(quarantine_ids) & set(c1_ids + c2_ids):
            raise RuntimeError("A QUARANTINED candidate was selected for exposure")

        answer_pack = {
            "tasks": [
                {"id":"T0_NO_HISTORY","prompt":"What is 2 + 2?","expected":"4","C0":"4","C1":"4","C2":"4"},
                {"id":"T1_ONE_FACT","prompt":"What is the Academy demo code word?","expected":"cedar","C0":"UNKNOWN without history","C1":"cedar","C2":"cedar"},
                {"id":"T2_COMPOSITION","prompt":"Give the demo retention window and sample route mode.","expected":"seven days; local-only","C0":"UNKNOWN without history","C1":"seven days; local-only","C2":"seven days; local-only"},
                {"id":"T3_SIMILAR_DISTRACTOR","prompt":"Distinguish Academy from production code words.","expected":"Academy cedar; production maple","C0":"UNKNOWN without history","C1":"Academy cedar; production maple","C2":"Academy cedar; production maple"},
                {"id":"T4_QUARANTINED_CONFLICT","prompt":"What does admitted memory say is the Academy code word? Do not ground on quarantined notes.","expected":"cedar; quarantined contradiction excluded","C0":"UNKNOWN without history","C1":"cedar; quarantined contradiction excluded","C2":"cedar; quarantined contradiction excluded"},
            ]
        }
        fixture_answer_rubric = {}
        for condition_key in ("C0_NONE", "C1_MINIMAL_RELEVANT", "C2_BROAD_SAFE"):
            condition_answers = {task["id"]: task[condition_key[:2]] for task in answer_pack["tasks"]}
            fixture_answer_rubric[condition_key] = {"fixture_matches": sum(condition_answers[task["id"]] == task["expected"] for task in answer_pack["tasks"]),
                "task_count": len(answer_pack["tasks"]), "semantic_verification_source": "synthetic fixture strings only; NOT measured model success; Nexus T1 only verifies artifact integrity"}
        capability_metadata = "Capability metadata: bounded read-only local file access."
        capability_instruction = "Full instruction: use an approved descriptor; resolve under reviewed root; read <=1 MiB; no writes/network; return bytes and digest."
        presence_conditions = {
            "P0_ABSENT": [],
            "P1_METADATA": [capability_metadata_id],
            "P2_FULL_INSTRUCTION": [capability_instruction_id],
        }
        conditions.update({key: {"refs": refs, "retrieved": len(refs), "eligible": len(refs)} for key, refs in presence_conditions.items()})
        conditions["P3_INVOCATION"] = {"refs": [], "retrieved": 0, "eligible": 0, "execution_status": "NOT_RUN_API_GAP"}
        outputs = {}
        # Emit one existing Hosted MODEL run per exposure condition. This records exactly the declared context refs.
        for key in condition_keys:
            index = condition_keys.index(key)
            run_id, manifest_id, artifact_id, subtask_id = run_ids[index], manifest_ids[index], artifact_ids[index], node_ids[index]
            context_refs = conditions[key]["refs"]
            child_grant_id = "academy-child-grant-" + key.lower().replace("_", "-")
            child_resources = [run_id, manifest_id, artifact_id, "route-"+run_id, input_id, contract_id, *context_refs]
            command = "academy-child-" + key.lower().replace("_", "-")
            output_command = "academy-output-"+key.lower().replace("_","-")
            for event_id in ("evt-"+command+"-create-run", "evt-"+command+"-ready", "evt-"+command+"-running", "evt-"+command+"-setup-cancel",
                             "evt-"+output_command+"-trace-object", "evt-"+output_command+"-verifying", "evt-"+output_command+"-terminal"):
                child_resources.append(event_id)
            authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":child_grant_id,"parent_grant_id":"academy-root-grant",
                "issued_by":"academy-host","granted_to":"academy-model","task_scope":[task_id],"resource_scope":sorted(set(child_resources)),
                "action_scope":["RUN_CREATE","RUN_TRANSITION","TRACE_APPEND","OBJECT_WRITE","CLASSIFY","VERIFY"],"audience_scope":["nexus-runtime"],"issued_at":now(),"expires_at":expiry,"status":"ACTIVE","policy_version":"1"}, "create-"+child_grant_id)
            run_class, manifest_class = "academy-class-"+run_id, "academy-class-"+manifest_id
            for assertion_id, stype, sref in ((run_class,"RUN",run_id),(manifest_class,"OBJECT",manifest_id)):
                authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":stype,"subject_ref":sref,
                    "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Academy condition","actor_id":"academy-model"}, grant_id=child_grant_id, task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)
            event_classes = {}
            for name, suffix in (("create","create-run"),("ready","ready"),("running","running"),("cancelled","setup-cancel")):
                event_id = "evt-"+command+"-"+suffix
                assertion_id = "academy-class-"+key.lower().replace("_","-")+"-"+name
                authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":"TRACE_EVENT","subject_ref":event_id,
                    "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Academy run transition","actor_id":"academy-model"}, grant_id=child_grant_id, task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)
                event_classes[name]=assertion_id
            common={"runtime_version":"0.1","policy_version":"1","schema_versions":{"nexus.run_manifest":2,"nexus.task_contract":1},"input_object_refs":[input_id,contract_id,*context_refs],
                "authority_grant_ref":child_grant_id,"data_boundary":boundary,"classification_assertion_ref":run_class}
            manifest=bridge.model_manifest(common=common,context_object_refs=context_refs)
            run={"schema_id":"nexus.run","schema_version":1,"run_id":run_id,"task_id":task_id,"subtask_id":subtask_id,"parent_run_id":root_run_id,"executor_kind":"MODEL","status":"CREATED","grant_id":child_grant_id,"data_boundary":boundary,"classification_assertion_ref":run_class,"created_at":now()}
            started=bridge.create_child_run(command_id=command,run=run,manifest=manifest,account_id="academy-budget",estimated_units=1,manifest_object_id=manifest_id,
                manifest_classification_assertion_ref=manifest_class,event_classification_assertion_refs=event_classes,parent_grant_id="academy-root-grant",requested_capability="UNSPECIFIED",attempt_reason="ACADEMY_PHASE0")
            if started["status"] != "RUNNING":
                raise RuntimeError("Hosted child did not start")
            if key.startswith("C"):
                by_task = {task["id"]: task[key[:2]] for task in answer_pack["tasks"]}
                output_text = json.dumps({"condition":key,"payload_kind":"SYNTHETIC_FIXTURE_NOT_MODEL_INVOCATION","task_answers":by_task}, ensure_ascii=False, sort_keys=True)
            else:
                output_text = f"SYNTHETIC_FIXTURE_NOT_MODEL_INVOCATION {key}: summarize the provided text in one sentence; no external action is requested."
            output_class, output_event_class = "academy-class-"+artifact_id, "academy-class-evt-"+key.lower().replace("_","-")+"-output"
            for assertion_id, stype, sref in ((output_class,"OBJECT",artifact_id),(output_event_class,"TRACE_EVENT","evt-"+output_command+"-trace-object")):
                authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":stype,"subject_ref":sref,
                    "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic host-declared output","actor_id":"academy-model"}, grant_id=child_grant_id, task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)
            recorded=bridge.record_output(command_id=output_command,task_id=task_id,run_id=run_id,grant_id=child_grant_id,artifact_id=artifact_id,
                payload=output_text.encode(),classification_assertion_ref=output_class,event_classification_assertion_ref=output_event_class,verifier_id="academy-verify-"+key.lower().replace("_","-"))
            close_classes={}
            for name, suffix in (("verifying","close-verifying"),("terminal","close-terminal")):
                event_id="evt-"+output_command+"-"+("verifying" if name == "verifying" else "terminal")
                assertion_id="academy-class-"+key.lower().replace("_","-")+"-"+name
                authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":"TRACE_EVENT","subject_ref":event_id,
                    "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Academy run terminal","actor_id":"academy-model"}, grant_id=child_grant_id, task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)
                close_classes[name]=assertion_id
            bridge.close_child(command_id=output_command,run_id=run_id,classification_assertion_refs=close_classes,succeeded=True,reservation_id=started["reservation_ref"],actual_units=1)
            outputs[key]={"run_id":run_id,"manifest_id":manifest_id,"artifact_id":artifact_id,"verification_id":recorded["verification"]["verification_id"],"verification_verdict":recorded["verification"]["verdict"],"context_object_refs":context_refs,"output_chars":len(output_text),"output_bytes":len(output_text.encode()),"payload_origin":"SYNTHETIC_FIXTURE_NOT_MODEL_INVOCATION","model_identity_status":manifest["model_identity_status"],"execution_source":manifest["execution_source"]}

        # Close the root through its ordinary Trace transition API.
        for name, suffix, expected, target in (("verifying","verifying","RUNNING","VERIFYING"),("terminal","succeeded","VERIFYING","SUCCEEDED")):
            event_id="evt-academy-root-"+suffix
            assertion_id="academy-class-root-"+name
            authority.record_classification_assertion({"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,"subject_type":"TRACE_EVENT","subject_ref":event_id,
                "sensitivity_level":"PUBLIC","handling_tags":[],"policy_version":"1","reason":"synthetic Academy root terminal","actor_id":"academy-host"}, grant_id="academy-root-grant", task_id=task_id, audience="nexus-runtime", command_id="classify-"+assertion_id)
            trace.transition_run(command_id="academy-root-"+suffix,run_id=root_run_id,expected_state=expected,next_state=target,classification_assertion_ref=assertion_id)

        # Exact replay of a safe committed object creation; changed payload must conflict.
        replay_before = {}
        with store._connection() as conn:
            replay_before={"objects":conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],"runs":conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],"reservations":conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0],"trace_events":conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]}
        exact_replay=store.put_object(command_id="put-"+objects["academy-codeword"]["claim"],object_id=objects["academy-codeword"]["claim"],payload=objects["academy-codeword"]["body"].encode(),object_type="artifact",created_by_run=root_run_id,classification_assertion_ref="academy-class-"+objects["academy-codeword"]["claim"])
        conflict_code=None
        try:
            store.put_object(command_id="put-"+objects["academy-codeword"]["claim"],object_id=objects["academy-codeword"]["claim"],payload=b"changed synthetic payload",object_type="artifact",created_by_run=root_run_id,classification_assertion_ref="academy-class-"+objects["academy-codeword"]["claim"])
        except Exception as exc:
            conflict_code=str(getattr(exc,"args",[type(exc).__name__])[0])
        with store._connection() as conn:
            replay_after={"objects":conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],"runs":conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],"reservations":conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0],"trace_events":conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]}
            trace_count=conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]
            root_status=conn.execute("SELECT status FROM runs WHERE run_id=?",(root_run_id,)).fetchone()[0]
            child_statuses={row["run_id"]:row["status"] for row in conn.execute("SELECT run_id,status FROM runs WHERE parent_run_id=?",(root_run_id,))}
            task_status=conn.execute("SELECT status FROM tasks WHERE task_id=?",(task_id,)).fetchone()[0]
            candidate_statuses={row["candidate_id"]:row["status"] for row in conn.execute("SELECT candidate_id,status FROM memory_candidates")}
            reservation_count=conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0]
        journal_head=store.independent_purge_journal.verified_head()
        result={
            "schema_version":22,"task_id":task_id,"root_run_id":root_run_id,"task_status":task_status,"root_run_status":root_status,"child_runs":child_statuses,
            "corpus_object_count":len(objects)*2,"raw_retained_object_count":len(objects)*2,"admitted_object_ids":sorted(admitted_ids),
            "quarantined_object_ids":sorted(quarantine_ids),"candidate_statuses":candidate_statuses,
            "retrieval":{"queries":query_terms,"raw_candidate_counts":{q:len(v) for q,v in raw_hits.items()},"admitted_eligible_counts":{q:len(v) for q,v in admitted_hits.items()},
                "broad_queries":broad_terms,"broad_raw_candidate_counts":{q:len(v) for q,v in broad_raw_hits.items()},"broad_admitted_eligible_counts":{q:len(v) for q,v in broad_admitted_hits.items()},
                "quarantined_ids_excluded":not bool(set(quarantine_ids)&{r["object_id"] for values in [*admitted_hits.values(),*broad_admitted_hits.values()] for r in values})},
            "conditions":{key:{"retrieved_candidate_count":v["retrieved"],"eligible_candidate_count":v["eligible"],"exposed_object_count":len(v["refs"]),"exposed_object_refs":v["refs"],
                "serialized_chars":len(json.dumps([{"object_ref":ref,"payload":context_payloads.get(ref,"")} for ref in v["refs"]],ensure_ascii=False,sort_keys=True,separators=(",",":"))),
                "serialized_bytes":len(json.dumps([{"object_ref":ref,"payload":context_payloads.get(ref,"")} for ref in v["refs"]],ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")),
                **({"execution_status":v["execution_status"]} if "execution_status" in v else {})} for key,v in conditions.items()},
            "hosted_outputs":outputs,"benchmark_tasks":answer_pack["tasks"],"fixture_answer_rubric":fixture_answer_rubric,"model_task_success":"UNAVAILABLE — current Hosted Bridge does not invoke/isolate a model","tool_calls":0,"retry_count":1,"replay_conflict_count":1,"trace_event_count":trace_count,"run_count":len(child_statuses)+1,"reservation_count":reservation_count,
            "replay":{"before":replay_before,"after":replay_after,"exact_result":exact_replay,"changed_payload_conflict":conflict_code},
            "host_telemetry":{"input_tokens":"UNAVAILABLE","output_tokens":"UNAVAILABLE","provider_cost":"UNAVAILABLE","wall_clock":"UNAVAILABLE"},
            "journal":{"path":"DISPOSABLE_EXTERNAL_PATH_NOT_COMMITTED","identity":store.independent_purge_journal.identity,"head_sequence":journal_head[0],"head_hash":journal_head[1]},
            "policy":{"base":"repository default fail-closed policy","delta":{"trust_anchors":["academy-human-root"]},"learning":{"auto_promote":policy["learning"]["auto_promote"],"learned_router":policy["learning"]["learned_router"]}},
            "inspect_grant_id":"academy-inspect-grant",
            "presence_probe":{"P0_ABSENT":[],"P1_METADATA":[capability_metadata_id],"P2_FULL_INSTRUCTION":[capability_instruction_id],"P3_INVOCATION":"NOT RUN: v0.1 has no late context-pack/exposure event; see Academy gap"},
            "execution_scope":"Outputs are synthetic fixture payloads passed through Hosted Bridge, not genuine MODEL invocation results. Hosted Bridge records host-declared outputs and context refs; it does not invoke/isolate a model. This fixture proves Core-side retrieval/eligibility and durable manifest declarations, not model behavior.",
        }
        args.results.parent.mkdir(parents=True,exist_ok=True)
        args.results.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print(json.dumps({"status":"COMPLETED","task_id":task_id,"root_run_id":root_run_id,"task_status":task_status,"trace_events":trace_count,"child_runs":len(child_statuses),"objects":len(all_object_ids)-5,"result_path":str(args.results)},ensure_ascii=False))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
