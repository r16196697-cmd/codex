from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.run import TraceRuntime
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.inspect import InspectService
from kernel.runtime.modes import RuntimeModeService


_QUALITY_FLOOR = {"ROUTINE": 0, "STANDARD": 1, "CRITICAL": 2}
_CLASS_RANK = {"E0": 0, "E1": 1, "E2": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DeterministicRuntime:
    """Persisted DAG and deterministic planning; it never invokes adapters."""

    def __init__(self, store: ObjectStore, authority: AuthorityService, budget: BudgetService, trace: TraceRuntime):
        if authority.store is not store or budget.store is not store or trace.store is not store:
            raise ValueError("Runtime services must share one ObjectStore")
        self.store = store
        self.authority = authority
        self.budget = budget
        self.trace = trace
        self.modes = RuntimeModeService(store, authority)
        self.inspector = InspectService(store, authority)

    def current_mode(self) -> dict[str, Any]:
        """Return the persisted instance mode; this is a non-mutating Core API."""
        return self.modes.current()

    def set_mode(self, *, command_id: str, grant_id: str, task_id: str, mode: str) -> dict[str, Any]:
        """Change mode through authority, policy, and CommandLedger checks."""
        return self.modes.set_mode(command_id=command_id, grant_id=grant_id, task_id=task_id, mode=mode)

    def complete_validated_recovery(self, *, command_id: str, purge_service) -> dict[str, Any]:
        return self.modes.complete_validated_recovery(command_id=command_id, purge_service=purge_service)

    def inspect_task(self, *, grant_id: str, task_id: str) -> dict[str, Any]:
        return self.inspector.task(grant_id=grant_id, task_id=task_id)

    def inspect_effect(self, *, grant_id: str, task_id: str, effect_id: str) -> dict[str, Any]:
        return self.inspector.effect(grant_id=grant_id, task_id=task_id, effect_id=effect_id)

    def inspect_approval(self, *, grant_id: str, task_id: str, approval_id: str, include_payload_hash: bool = False) -> dict[str, Any]:
        return self.inspector.approval(grant_id=grant_id, task_id=task_id, approval_id=approval_id, include_payload_hash=include_payload_hash)

    def inspect_route(self, *, grant_id: str, task_id: str, route_id: str) -> dict[str, Any]:
        return self.inspector.route(grant_id=grant_id, task_id=task_id, route_id=route_id)

    def inspect_object(self, *, grant_id: str, task_id: str, object_id: str, include_integrity_hash: bool = False) -> dict[str, Any]:
        return self.inspector.object_metadata(grant_id=grant_id, task_id=task_id, object_id=object_id, include_integrity_hash=include_integrity_hash)

    def inspect_purge(self, *, grant_id: str, task_id: str, plan_id: str) -> dict[str, Any]:
        return self.inspector.purge(grant_id=grant_id, task_id=task_id, plan_id=plan_id)

    def _authorize(self, grant_id: str, task_id: str, resource: str, action: str, command_id: str) -> None:
        self.authority.evaluate_authorization(grant_id, {"task": task_id, "resource": resource, "action": action, "audience": "nexus-runtime"}, command_id)

    def bind_task_contract(self, *, command_id: str, root_run_id: str, contract_object_id: str, classification_assertion_ref: str, contract: dict[str, Any], expected_revision: int | None = None) -> int:
        self.modes.require("core_write")
        self.store._validate("nexus.task_contract@1.schema.json", contract)
        operation = "bind_task_contract"
        request = {"root_run_id": root_run_id, "contract_object_id": contract_object_id, "classification_assertion_ref": classification_assertion_ref, "contract": contract, "expected_revision": expected_revision}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            return prior["revision"]
        with self.store._connection() as conn:
            root = conn.execute("SELECT task_id,grant_id,status,parent_run_id,executor_kind,data_boundary_json FROM runs WHERE run_id=?", (root_run_id,)).fetchone()
            task = conn.execute("SELECT requester_id FROM tasks WHERE task_id=?", (contract["task_id"],)).fetchone()
            account = conn.execute("SELECT task_id FROM budget_accounts WHERE account_id=?", (contract["budget_account_ref"],)).fetchone()
        if not root or root["parent_run_id"] is not None or root["executor_kind"] != "ORCHESTRATOR" or root["task_id"] != contract["task_id"]:
            raise RuntimeDenied("TASK_CONTRACT_ROOT_MISMATCH")
        if not task or task["requester_id"] != contract["requester_id"] or not account or account["task_id"] != contract["task_id"]:
            raise RuntimeDenied("TASK_CONTRACT_IDENTITY_OR_BUDGET_MISMATCH")
        if root["status"] != "CREATED":
            raise RuntimeDenied("TASK_CONTRACT_MUST_BIND_BEFORE_ROOT_READY")
        self._authorize(root["grant_id"], root["task_id"], root_run_id, "OBJECT_WRITE", command_id + "-authorize")
        boundary = json.loads(root["data_boundary_json"])
        for input_id in contract.get("input_object_refs", []):
            meta = self.store.get_object_metadata(input_id)
            if meta.get("payload_state") != "AVAILABLE":
                raise RuntimeDenied("TASK_CONTRACT_INPUT_UNAVAILABLE")
            with self.store._connection() as conn:
                classification = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (meta["classification_assertion_ref"],)).fetchone()
            if not classification or classification["sensitivity_level"] not in boundary["allowed_classifications"] or not set(json.loads(classification["handling_tags_json"])).issubset(set(boundary["handling_tags"])):
                raise RuntimeDenied("TASK_CONTRACT_INPUT_OUTSIDE_ROOT_BOUNDARY")
            self.store.get_payload(input_id)
        payload = _canonical(contract).encode("utf-8")
        self.store.put_object(command_id=command_id + "-object", object_id=contract_object_id, payload=payload, object_type="task_contract", created_by_run=root_run_id, classification_assertion_ref=classification_assertion_ref)
        ref_id = f"task-contract:{contract['task_id']}"
        with self.store._connection() as conn:
            ref = conn.execute("SELECT revision,current_object_id FROM logical_refs WHERE ref_id=?", (ref_id,)).fetchone()
        if not ref:
            revision = self.store.create_logical_ref(command_id=command_id + "-logical-ref", ref_id=ref_id, ref_type="task_contract", object_id=contract_object_id, updated_by_run=root_run_id)
        elif expected_revision is None and ref["current_object_id"] == contract_object_id:
            revision = ref["revision"]
        elif expected_revision is None:
            raise RuntimeDenied("TASK_CONTRACT_UPDATE_REQUIRES_EXPECTED_REVISION")
        else:
            revision = self.store.compare_and_swap_ref(command_id=command_id + "-logical-ref", ref_id=ref_id, expected_revision=expected_revision, new_object_id=contract_object_id, updated_by_run=root_run_id)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self.store._record_command(conn, command_id, operation, request_hash, {"revision": revision, "ref_id": ref_id})
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return revision

    def create_dag(self, *, command_id: str, task_id: str, root_run_id: str, nodes: list[dict[str, Any]]) -> list[str]:
        self.modes.require("core_write")
        if not isinstance(nodes, list):
            raise RuntimeDenied("DAG_MUST_BE_A_LIST")
        for node in nodes:
            self.store._validate("nexus.subtask@1.schema.json", node)
            if node["task_id"] != task_id:
                raise RuntimeDenied("SUBTASK_TASK_MISMATCH")
            for schema_name in (node["input_schema_id"], node["output_schema_id"]):
                if Path(schema_name).name != schema_name or not schema_name.endswith(".schema.json"):
                    raise RuntimeDenied("DAG_SCHEMA_REFERENCE_INVALID")
                self.store._schema(schema_name)
        ids = [node["subtask_id"] for node in nodes]
        if len(ids) != len(set(ids)):
            raise RuntimeDenied("DUPLICATE_SUBTASK_ID")
        id_set = set(ids)
        indegree = {item: 0 for item in ids}
        dependents: dict[str, list[str]] = {item: [] for item in ids}
        for node in nodes:
            for source_id in node["dependency_ids"]:
                if source_id not in id_set or source_id == node["subtask_id"]:
                    raise RuntimeDenied("DAG_DEPENDENCY_OUTSIDE_GRAPH")
                indegree[node["subtask_id"]] += 1
                dependents[source_id].append(node["subtask_id"])
            if node["requested_executor"] == "TOOL" and node.get("requires_tool_use", False):
                raise RuntimeDenied("TOOL_NODE_CANNOT_REQUIRE_MODEL_TOOL_USE")
            if node["requested_executor"] == "TOOL" and not node["input_object_refs"]:
                raise RuntimeDenied("TOOL_NODE_REQUIRES_TYPED_INPUT_OBJECT")
        ready = sorted(item for item, degree in indegree.items() if degree == 0)
        visited = 0
        while ready:
            current = ready.pop(0)
            visited += 1
            for child in sorted(dependents[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if visited != len(nodes):
            raise RuntimeDenied("DAG_CYCLE")
        operation = "create_dag"
        request = {"task_id": task_id, "root_run_id": root_run_id, "nodes": nodes}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            root = conn.execute("SELECT task_id,grant_id,parent_run_id,executor_kind,status,data_boundary_json FROM runs WHERE run_id=?", (root_run_id,)).fetchone()
            replay = self.store._replay_command(conn, command_id, operation, request_hash)
            if replay is not None:
                return replay["subtask_ids"]
        if not root or root["task_id"] != task_id or root["parent_run_id"] is not None or root["executor_kind"] != "ORCHESTRATOR" or root["status"] != "CREATED":
            raise RuntimeDenied("DAG_REQUIRES_OWNING_ROOT_RUN")
        self._authorize(root["grant_id"], task_id, root_run_id, "RUN_CREATE", command_id + "-authorize")
        boundary = json.loads(root["data_boundary_json"])
        for node in nodes:
            for object_id in node["input_object_refs"]:
                meta = self.store.get_object_metadata(object_id)
                if meta.get("payload_state") != "AVAILABLE":
                    raise RuntimeDenied("DAG_INPUT_UNAVAILABLE")
                envelope = {key: meta[key] for key in ("object_id", "object_type", "payload_uri", "hash_profile_ref", "integrity_hash", "created_by_run", "classification_assertion_ref")}
                envelope.update({"schema_id": "nexus.object", "schema_version": 1})
                if meta.get("semantic_hash") is not None:
                    envelope["semantic_hash"] = meta["semantic_hash"]
                self.store._validate(node["input_schema_id"], envelope)
                with self.store._connection() as conn:
                    c = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (meta["classification_assertion_ref"],)).fetchone()
                if not c or c["sensitivity_level"] not in boundary["allowed_classifications"] or not set(json.loads(c["handling_tags_json"])).issubset(set(boundary["handling_tags"])):
                    raise RuntimeDenied("DAG_INPUT_OUTSIDE_ROOT_BOUNDARY")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return replay["subtask_ids"]
                if conn.execute("SELECT 1 FROM task_dags WHERE task_id=?", (task_id,)).fetchone():
                    raise RuntimeDenied("DAG_VERSION_IMMUTABLE_IN_V0_1")
                for node_index, node in enumerate(nodes):
                    conn.execute("INSERT INTO subtasks(subtask_id,task_id,node_index,node_json,status,command_id,created_at) VALUES(?,?,?,?,'PENDING',?,?)", (node["subtask_id"], task_id, node_index, _canonical(node), command_id + ":" + node["subtask_id"], node["created_at"]))
                for node in nodes:
                    for dependency in node["dependency_ids"]:
                        conn.execute("INSERT INTO subtask_edges(task_id,dependency_id,dependent_id) VALUES(?,?,?)", (task_id, dependency, node["subtask_id"]))
                result = {"task_id": task_id, "subtask_ids": ids, "dag_version": "1"}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                graph_hash = hashlib.sha256(_canonical(nodes).encode("utf-8")).hexdigest()
                conn.execute("INSERT INTO task_dags(task_id,root_run_id,dag_version,graph_hash,node_count,command_id,created_at) VALUES(?,?, '1', ?,?,?,?)", (task_id, root_run_id, graph_hash, len(nodes), command_id, _now()))
                conn.commit()
                return ids
            except Exception:
                conn.rollback()
                raise

    def register_model_profile(self, *, command_id: str, grant_id: str, task_id: str, profile: dict[str, Any]) -> None:
        self.modes.require("learning_write")
        self.store._validate("nexus.model_profile@1.schema.json", profile)
        if profile["available"] and profile["local_eval_status"] != "PASSED":
            raise RuntimeDenied("AVAILABLE_PROFILE_REQUIRES_LOCAL_EVALUATION")
        operation = "register_model_profile"
        request = {"profile": profile}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                return
        self._authorize(grant_id, task_id, profile["model_id"], "RUNTIME_CONFIGURE", command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                conn.execute("INSERT INTO model_profiles(model_id,version,profile_json) VALUES(?,?,?)", (profile["model_id"], profile["version"], _canonical(profile)))
                self.store._record_command(conn, command_id, operation, request_hash, {"model_id": profile["model_id"], "version": profile["version"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def register_tool_descriptor(self, *, command_id: str, grant_id: str, task_id: str, descriptor: dict[str, Any]) -> None:
        self.modes.require("learning_write")
        self.store._validate("nexus.tool_descriptor@1.schema.json", descriptor)
        for schema_name in (descriptor["input_schema_id"], descriptor["output_schema_id"]):
            if Path(schema_name).name != schema_name or not schema_name.endswith(".schema.json"):
                raise RuntimeDenied("TOOL_SCHEMA_REFERENCE_INVALID")
            self.store._schema(schema_name)
        if descriptor["review_status"] != "APPROVED" or descriptor["effect_class"] != "READ_ONLY":
            raise RuntimeDenied("STEP5_ONLY_APPROVED_READ_ONLY_TOOLS")
        operation = "register_tool_descriptor"
        request = {"descriptor": descriptor}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                return
        self._authorize(grant_id, task_id, descriptor["tool_id"], "RUNTIME_CONFIGURE", command_id + "-authorize")
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                conn.execute("INSERT INTO tool_descriptors(tool_id,version,descriptor_json) VALUES(?,?,?)", (descriptor["tool_id"], descriptor["version"], _canonical(descriptor)))
                self.store._record_command(conn, command_id, operation, request_hash, {"tool_id": descriptor["tool_id"], "version": descriptor["version"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def bind_manifest(self, *, command_id: str, run_id: str, manifest_object_id: str, manifest_classification_assertion_ref: str, manifest: dict[str, Any]) -> None:
        self.modes.require("core_write")
        self.store._validate("nexus.run_manifest@1.schema.json", manifest)
        operation = "bind_run_manifest"
        request = {"run_id": run_id, "manifest_object_id": manifest_object_id, "manifest": manifest}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            return
        with self.store._connection() as conn:
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run or run["status"] != "CREATED" or run["manifest_ref"] is not None:
            raise RuntimeDenied("MANIFEST_CAN_BIND_ONLY_ONCE_BEFORE_READY")
        self._validate_manifest_for_run(manifest, manifest_object_id, manifest_classification_assertion_ref, run)
        self._authorize(run["grant_id"], run["task_id"], run_id, "OBJECT_WRITE", command_id + "-authorize")
        payload = _canonical(manifest).encode("utf-8")
        self.store.put_object(command_id=command_id + "-object", object_id=manifest_object_id, payload=payload, object_type="run_manifest", created_by_run=run_id, classification_assertion_ref=manifest_classification_assertion_ref)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                cursor = conn.execute("UPDATE runs SET manifest_ref=? WHERE run_id=? AND status='CREATED' AND manifest_ref IS NULL", (manifest_object_id, run_id))
                if cursor.rowcount != 1:
                    raise RuntimeDenied("MANIFEST_BIND_RACE")
                self.store._record_command(conn, command_id, operation, request_hash, {"run_id": run_id, "manifest_ref": manifest_object_id})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _validate_manifest_for_run(self, manifest: dict[str, Any], object_id: str, classification_ref: str, run) -> None:
        if manifest["executor_kind"] != run["executor_kind"] or manifest["authority_grant_ref"] != run["grant_id"]:
            raise RuntimeDenied("MANIFEST_RUN_IDENTITY_MISMATCH")
        if manifest["data_boundary"] != json.loads(run["data_boundary_json"]) or manifest["classification_assertion_ref"] != run["classification_assertion_ref"]:
            raise RuntimeDenied("MANIFEST_BOUNDARY_OR_RUN_CLASSIFICATION_MISMATCH")
        if manifest.get("budget_reservation_ref") != run["budget_reservation_ref"]:
            raise RuntimeDenied("MANIFEST_BUDGET_RESERVATION_MISMATCH")
        with self.store._connection() as conn:
            object_class_row = conn.execute("SELECT subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version FROM classification_assertions WHERE assertion_id=?", (classification_ref,)).fetchone()
            run_class_row = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (run["classification_assertion_ref"],)).fetchone()
        if not object_class_row or object_class_row["subject_type"] != "OBJECT" or object_class_row["subject_ref"] != object_id or not run_class_row:
            raise RuntimeDenied("MANIFEST_OBJECT_CLASSIFICATION_INVALID")
        ranks = self.authority.policy["classification"]["sensitivity_rank"]
        if object_class_row["policy_version"] != self.authority.policy["policy_version"] or ranks.get(object_class_row["sensitivity_level"], -1) < ranks.get(run_class_row["sensitivity_level"], 99) or not set(json.loads(run_class_row["handling_tags_json"])).issubset(set(json.loads(object_class_row["handling_tags_json"]))):
            raise RuntimeDenied("MANIFEST_OBJECT_CLASSIFICATION_DOWNGRADE")
        meta = self.store.get_object_metadata(object_id) if self._object_exists(object_id) else None
        if meta and (meta.get("object_type") != "run_manifest" or meta.get("payload_state") != "AVAILABLE"):
            raise RuntimeDenied("MANIFEST_OBJECT_INVALID")
        if run["executor_kind"] == "ORCHESTRATOR":
            if "model_id" in manifest or "provider" in manifest or "prompt_version" in manifest:
                raise RuntimeDenied("ROOT_MANIFEST_MUST_NOT_INVENT_MODEL")
            contract_id = manifest["task_contract_ref"]
            with self.store._connection() as conn:
                contract = conn.execute("SELECT current_object_id FROM logical_refs WHERE ref_id=? AND ref_type='task_contract'", (f"task-contract:{run['task_id']}",)).fetchone()
            if not contract or contract["current_object_id"] != contract_id:
                raise RuntimeDenied("ROOT_MANIFEST_TASK_CONTRACT_REF_MISMATCH")
            contract_meta = self.store.get_object_metadata(contract_id)
            if contract_meta.get("object_type") != "task_contract" or contract_meta.get("payload_state") != "AVAILABLE":
                raise RuntimeDenied("TASK_CONTRACT_OBJECT_UNAVAILABLE")
            contract_doc = json.loads(self.store.get_payload(contract_id).decode("utf-8"))
            self.store._validate("nexus.task_contract@1.schema.json", contract_doc)
            if contract_doc["task_id"] != run["task_id"]:
                raise RuntimeDenied("TASK_CONTRACT_TASK_MISMATCH")
            if manifest["input_object_refs"] != contract_doc.get("input_object_refs", []):
                raise RuntimeDenied("ROOT_MANIFEST_TASK_INPUT_REFS_MISMATCH")
            with self.store._connection() as conn:
                dag = conn.execute("SELECT root_run_id,dag_version,graph_hash,node_count FROM task_dags WHERE task_id=?", (run["task_id"],)).fetchone()
                dag_nodes = [json.loads(row[0]) for row in conn.execute("SELECT node_json FROM subtasks WHERE task_id=? ORDER BY node_index", (run["task_id"],)).fetchall()]
                dag_edges = {(row[0], row[1]) for row in conn.execute("SELECT dependency_id,dependent_id FROM subtask_edges WHERE task_id=?", (run["task_id"],)).fetchall()}
            graph_hash = hashlib.sha256(_canonical(dag_nodes).encode("utf-8")).hexdigest()
            expected_edges = {(dependency, node["subtask_id"]) for node in dag_nodes for dependency in node["dependency_ids"]}
            if not dag or dag["root_run_id"] != run["run_id"] or dag["dag_version"] != manifest["dag_version"] or manifest["scheduler_version"] != "1" or dag["node_count"] != len(dag_nodes) or dag["graph_hash"] != graph_hash or dag_edges != expected_edges:
                raise RuntimeDenied("ROOT_MANIFEST_DAG_OR_SCHEDULER_VERSION_MISMATCH")
        elif run["executor_kind"] == "MODEL":
            with self.store._connection() as conn:
                route = conn.execute("SELECT decision_json,subtask_id FROM route_decisions WHERE decision_object_id=?", (manifest["route_decision_ref"],)).fetchone()
                if not route:
                    raise RuntimeDenied("MODEL_ROUTE_DECISION_MISSING")
                decision = json.loads(route["decision_json"])
                self.store._validate("nexus.route_decision@1.schema.json", decision)
                if run["subtask_id"] != route["subtask_id"] or decision["selected_model_id"] != manifest["model_id"] or decision["selected_model_class"] != manifest["model_class"]:
                    raise RuntimeDenied("MODEL_ROUTE_DECISION_BINDING_MISMATCH")
                profile = conn.execute("SELECT profile_json FROM model_profiles WHERE model_id=? AND version=?", (manifest["model_id"], decision["selected_model_profile_version"])).fetchone()
            if not profile:
                raise RuntimeDenied("MODEL_PROFILE_NOT_REGISTERED")
            profile_doc = json.loads(profile["profile_json"])
            if not profile_doc["available"] or profile_doc["model_adapter_version"] != manifest["model_adapter_version"] or profile_doc["provider"] != manifest["provider"] or profile_doc["model_class"] != manifest["model_class"]:
                raise RuntimeDenied("MODEL_PROFILE_BINDING_MISMATCH")
        elif run["executor_kind"] == "TOOL":
            with self.store._connection() as conn:
                descriptor = conn.execute("SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? AND version=?", (manifest["tool_id"], manifest["tool_descriptor_version"])).fetchone()
            if not descriptor or json.loads(descriptor["descriptor_json"])["review_status"] != "APPROVED":
                raise RuntimeDenied("TOOL_DESCRIPTOR_NOT_APPROVED")

    def _object_exists(self, object_id: str) -> bool:
        with self.store._connection() as conn:
            return conn.execute("SELECT 1 FROM objects WHERE object_id=?", (object_id,)).fetchone() is not None

    def _contract(self, task_id: str) -> tuple[str, dict[str, Any]]:
        with self.store._connection() as conn:
            ref = conn.execute("SELECT current_object_id FROM logical_refs WHERE ref_id=? AND ref_type='task_contract'", (f"task-contract:{task_id}",)).fetchone()
        if not ref:
            raise RuntimeDenied("TASK_CONTRACT_NOT_BOUND")
        doc = json.loads(self.store.get_payload(ref["current_object_id"]).decode("utf-8"))
        self.store._validate("nexus.task_contract@1.schema.json", doc)
        return ref["current_object_id"], doc

    def _budget_snapshot(self, account_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM budget_accounts WHERE account_id=?", (account_id,)).fetchone()
        if not row:
            raise RuntimeDenied("BUDGET_ACCOUNT_NOT_FOUND")
        return {"account_id": account_id, "unit": row["unit"], "limit": row["amount_limit"], "reserved": row["reserved"], "consumed": row["consumed"], "remaining": row["amount_limit"] - row["reserved"] - row["consumed"], "model_calls_remaining": row["model_call_limit"] - row["model_calls_reserved"] - row["model_calls_consumed"], "tool_calls_remaining": row["tool_call_limit"] - row["tool_calls_reserved"] - row["tool_calls_consumed"], "child_runs_remaining": row["child_run_limit"] - row["child_runs_reserved"] - row["child_runs_consumed"]}

    def replay_subtask(self, subtask_id: str) -> dict[str, Any]:
        self.modes.require("core_read")
        with self.store._connection() as conn:
            row = conn.execute("SELECT task_id,status,scheduled_run_id,node_json FROM subtasks WHERE subtask_id=?", (subtask_id,)).fetchone()
            if not row:
                raise RuntimeDenied("SUBTASK_NOT_FOUND")
            run_id = row["scheduled_run_id"]
        if run_id is None:
            if row["status"] != "PENDING":
                raise RuntimeDenied("SUBTASK_PROJECTION_WITHOUT_RUN")
            return {"subtask_id": subtask_id, "task_id": row["task_id"], "status": "PENDING", "run_id": None}
        projection = self.trace.replay_run(run_id)
        run_to_node = {"CREATED": "PENDING", "READY": "READY", "RUNNING": "RUNNING", "WAITING": "WAITING", "VERIFYING": "RUNNING", "SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}
        expected = run_to_node[projection["status"]]
        if row["status"] != expected:
            raise RuntimeDenied("SUBTASK_TRACE_REPLAY_PROJECTION_MISMATCH")
        return {"subtask_id": subtask_id, "task_id": row["task_id"], "status": expected, "run_id": run_id, "last_seq": projection["last_seq"]}

    def _assert_classification_scope(self, assertion_id: str, subject_type: str, subject_ref: str, principal_ids: set[str], boundary: dict[str, Any], *, at_least_level: str | None = None, required_tags: set[str] | None = None) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
        if not row or row["subject_type"] != subject_type or row["subject_ref"] != subject_ref or row["policy_version"] != self.authority.policy["policy_version"] or row["actor_id"] not in principal_ids:
            raise RuntimeDenied("SCHEDULE_CLASSIFICATION_ASSERTION_INVALID")
        tags = set(json.loads(row["handling_tags_json"]))
        ranks = self.authority.policy["classification"]["sensitivity_rank"]
        if row["sensitivity_level"] not in boundary["allowed_classifications"] or not tags.issubset(set(boundary["handling_tags"])):
            raise RuntimeDenied("SCHEDULE_CLASSIFICATION_OUTSIDE_DATA_BOUNDARY")
        if at_least_level and ranks.get(row["sensitivity_level"], -1) < ranks.get(at_least_level, 99):
            raise RuntimeDenied("SCHEDULE_CLASSIFICATION_LEVEL_DOWNGRADE")
        if required_tags and not required_tags.issubset(tags):
            raise RuntimeDenied("SCHEDULE_CLASSIFICATION_TAG_DOWNGRADE")
        return dict(row)

    def _choose_model(self, node: dict[str, Any], contract: dict[str, Any], root, budget: dict[str, Any], contract_object_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        boundary = json.loads(root["data_boundary_json"])
        input_bytes = 0
        input_levels: set[str] = set()
        context_refs = [contract_object_id, *node["input_object_refs"]]
        for object_id in context_refs:
            metadata = self.store.get_object_metadata(object_id)
            if metadata.get("payload_state") != "AVAILABLE":
                raise RuntimeDenied("MODEL_INPUT_UNAVAILABLE")
            payload = self.store.get_payload(object_id)
            input_bytes += len(payload)
            with self.store._connection() as conn:
                row = conn.execute("SELECT sensitivity_level FROM classification_assertions WHERE assertion_id=?", (metadata["classification_assertion_ref"],)).fetchone()
            if not row or row["sensitivity_level"] not in boundary["allowed_classifications"]:
                raise RuntimeDenied("MODEL_INPUT_OUTSIDE_DATA_BOUNDARY")
            input_levels.add(row["sensitivity_level"])
        constraints = contract["routing_constraints"]
        preferred_floor = max(_QUALITY_FLOOR[node["quality_requirement"]], 2 if node["risk_class"] in {"HIGH", "CRITICAL"} else 0)
        profiles: list[dict[str, Any]] = []
        with self.store._connection() as conn:
            rows = conn.execute("SELECT profile_json FROM model_profiles ORDER BY model_id,version").fetchall()
        excluded: list[dict[str, str]] = []
        if not set(node["required_modalities"]).issubset(set(constraints["modalities"])):
            raise RuntimeDenied("NODE_MODALITY_OUTSIDE_TASK_HARD_CONSTRAINT")
        for row in rows:
            profile = json.loads(row["profile_json"])
            reason = None
            if not profile["available"] or profile["local_eval_status"] != "PASSED": reason = "MODEL_UNAVAILABLE_OR_UNEVALUATED"
            elif profile["model_class"] not in _CLASS_RANK: reason = "MODEL_CLASS_NOT_IN_V0_1_ROUTER"
            elif constraints["allowed_providers"] and profile["provider"] not in constraints["allowed_providers"]: reason = "PROVIDER_NOT_ALLOWED"
            elif profile["provider"] in constraints["forbidden_providers"]: reason = "PROVIDER_FORBIDDEN"
            elif constraints["locality"] == "LOCAL_ONLY" and profile["local_or_cloud"] != "LOCAL": reason = "LOCALITY_CONSTRAINT"
            elif constraints["network_required"] and profile["local_or_cloud"] != "CLOUD": reason = "NETWORK_REQUIRED"
            elif profile["local_or_cloud"] == "CLOUD" and profile["provider"] not in constraints["allowed_providers"]: reason = "CLOUD_PROVIDER_NOT_AUTHORIZED_BY_TASK_POLICY"
            elif not set(node["required_modalities"]).issubset(set(profile["modalities"])): reason = "MODALITY_UNSUPPORTED"
            elif node.get("requires_structured_output", False) and not profile["structured_output_support"]: reason = "STRUCTURED_OUTPUT_UNSUPPORTED"
            elif node.get("requires_tool_use", False) and not profile["tool_use_support"]: reason = "TOOL_USE_UNSUPPORTED"
            elif not input_levels.issubset(set(profile["allowed_classifications"])): reason = "CLASSIFICATION_NOT_ALLOWED"
            elif input_bytes > profile["context_limit"]: reason = "CONTEXT_LIMIT_CONSERVATIVE_BYTE_BOUND"
            elif profile["cost_profile"]["unit"] != budget["unit"]: reason = "BUDGET_UNIT_MISMATCH"
            elif profile["cost_profile"]["estimated_cost"] > budget["remaining"]: reason = "BUDGET_INSUFFICIENT"
            elif budget["model_calls_remaining"] < 1: reason = "MODEL_CALL_BUDGET_EXHAUSTED"
            elif budget["child_runs_remaining"] < 1: reason = "CHILD_RUN_BUDGET_EXHAUSTED"
            if reason:
                excluded.append({"model_id": profile["model_id"], "reason_code": reason})
            else:
                profiles.append(profile)
        eligible = [profile for profile in profiles if _CLASS_RANK[profile["model_class"]] >= preferred_floor]
        excluded.extend({"model_id": profile["model_id"], "reason_code": "BELOW_QUALITY_FLOOR"} for profile in profiles if _CLASS_RANK[profile["model_class"]] < preferred_floor)
        if not eligible:
            raise RuntimeDenied("NO_MODEL_MEETS_HARD_ROUTING_AND_QUALITY_CONSTRAINTS")
        optimize = contract["routing_preferences"]["optimize_for"]
        if optimize == "QUALITY":
            eligible.sort(key=lambda p: (-_CLASS_RANK[p["model_class"]], p["cost_profile"]["estimated_cost"], p["latency_profile"]["estimated_ms"], p["model_id"], p["version"]))
        elif optimize == "LATENCY":
            eligible.sort(key=lambda p: (p["latency_profile"]["estimated_ms"], -_CLASS_RANK[p["model_class"]], p["cost_profile"]["estimated_cost"], p["model_id"], p["version"]))
        elif optimize == "COST":
            eligible.sort(key=lambda p: (p["cost_profile"]["estimated_cost"], -_CLASS_RANK[p["model_class"]], p["latency_profile"]["estimated_ms"], p["model_id"], p["version"]))
        else:
            eligible.sort(key=lambda p: (-_CLASS_RANK[p["model_class"]], p["cost_profile"]["estimated_cost"], p["latency_profile"]["estimated_ms"], p["model_id"], p["version"]))
        selected = eligible[0]
        decision = {"schema_id": "nexus.route_decision", "schema_version": 1, "route_decision_id": node["subtask_id"] + ":route:1", "run_or_subtask_id": node["subtask_id"], "route_policy_version": "1", "task_class": contract["risk_class"], "risk_class": node["risk_class"], "quality_requirement": node["quality_requirement"], "user_policy_ref": "task-contract:" + contract["task_id"], "eligible_models": [{"model_id": p["model_id"], "model_class": p["model_class"], "provider": p["provider"], "version": p["version"]} for p in sorted(eligible, key=lambda p: (p["model_id"], p["version"]))], "excluded_models": sorted(item["model_id"] for item in excluded), "exclusion_reasons": sorted(excluded, key=lambda item: (item["model_id"], item["reason_code"])), "selected_model_class": selected["model_class"], "selected_model_id": selected["model_id"], "selected_model_profile_version": selected["version"], "reason_codes": ["QUALITY_FLOOR_MET", "HARD_CONSTRAINTS_MET", "DETERMINISTIC_PREFERENCE_" + optimize], "budget_snapshot": budget, "escalation_allowed": True, "created_at": node["created_at"]}
        self.store._validate("nexus.route_decision@1.schema.json", decision)
        return decision, selected

    def schedule_node(self, *, command_id: str, task_id: str, root_run_id: str, subtask_id: str, child_run_id: str, child_grant_id: str, child_classification_assertion_ref: str, event_classification_assertion_ref: str, ready_event_classification_assertion_ref: str, route_object_id: str | None = None, route_classification_assertion_ref: str | None = None, manifest_object_id: str, manifest_classification_assertion_ref: str) -> dict[str, Any]:
        self.modes.require("run_execute")
        operation = "schedule_subtask"
        request = {"task_id": task_id, "root_run_id": root_run_id, "subtask_id": subtask_id, "child_run_id": child_run_id, "child_grant_id": child_grant_id, "child_classification_assertion_ref": child_classification_assertion_ref, "event_classification_assertion_ref": event_classification_assertion_ref, "ready_event_classification_assertion_ref": ready_event_classification_assertion_ref, "route_object_id": route_object_id, "route_classification_assertion_ref": route_classification_assertion_ref, "manifest_object_id": manifest_object_id, "manifest_classification_assertion_ref": manifest_classification_assertion_ref}
        request_hash = self.store._request_hash(operation, request)
        with self.store._connection() as conn:
            prior = self.store._replay_command(conn, command_id, operation, request_hash)
        if prior is not None:
            return prior
        with self.store._connection() as conn:
            root = conn.execute("SELECT * FROM runs WHERE run_id=?", (root_run_id,)).fetchone()
            row = conn.execute("SELECT node_json,status,scheduled_run_id FROM subtasks WHERE subtask_id=? AND task_id=?", (subtask_id, task_id)).fetchone()
        if not root or root["executor_kind"] != "ORCHESTRATOR" or root["task_id"] != task_id or root["status"] != "RUNNING" or not row:
            raise RuntimeDenied("SCHEDULER_OWNERSHIP_OR_NODE_INVALID")
        node = json.loads(row["node_json"])
        if row["scheduled_run_id"]:
            with self.store._connection() as conn:
                scheduled = conn.execute("SELECT manifest_ref,budget_reservation_ref,status FROM runs WHERE run_id=?", (row["scheduled_run_id"],)).fetchone()
                route = conn.execute("SELECT decision_object_id FROM route_decisions WHERE subtask_id=?", (subtask_id,)).fetchone()
            if scheduled["status"] == "CREATED":
                self.trace.transition_run(command_id=command_id + "-ready", run_id=row["scheduled_run_id"], expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_event_classification_assertion_ref)
            elif scheduled["status"] not in {"READY", "RUNNING", "WAITING", "VERIFYING", "SUCCEEDED", "FAILED", "CANCELLED"}:
                raise RuntimeDenied("SCHEDULED_RUN_STATE_UNRECOGNIZED")
            result = {"run_id": row["scheduled_run_id"], "status": "READY", "manifest_ref": scheduled["manifest_ref"], "reservation_ref": scheduled["budget_reservation_ref"], "route_decision_ref": route["decision_object_id"] if route else None}
            with self.store._lock, self.store._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
            return result
        if row["status"] != "PENDING":
            raise RuntimeDenied("SUBTASK_NOT_SCHEDULABLE")
        with self.store._connection() as conn:
            for dependency in node["dependency_ids"]:
                dep = conn.execute("SELECT status FROM subtasks WHERE task_id=? AND subtask_id=?", (task_id, dependency)).fetchone()
                if not dep or dep["status"] != "SUCCEEDED":
                    raise RuntimeDenied("SUBTASK_DEPENDENCY_NOT_SATISFIED")
        contract_ref, contract = self._contract(task_id)
        self._authorize(root["grant_id"], task_id, root_run_id, "RUN_CREATE", command_id + "-authorize")
        child_chain = self.authority.validate_delegation_chain(child_grant_id)
        root_chain = self.authority.validate_delegation_chain(root["grant_id"])
        if len(child_chain) <= len(root_chain) or [g["grant_id"] for g in child_chain[:len(root_chain)]] != [g["grant_id"] for g in root_chain]:
            raise RuntimeDenied("CHILD_GRANT_MUST_DESCEND_FROM_ROOT_GRANT")
        child_principals = {identity for grant in child_chain for identity in (grant["issued_by"], grant["granted_to"])}
        child_boundary = json.loads(root["data_boundary_json"])
        child_class = self._assert_classification_scope(child_classification_assertion_ref, "RUN", child_run_id, child_principals, child_boundary)
        root_principals = {identity for grant in root_chain for identity in (grant["issued_by"], grant["granted_to"])}
        root_class = self._assert_classification_scope(root["classification_assertion_ref"], "RUN", root_run_id, root_principals, child_boundary)
        ranks = self.authority.policy["classification"]["sensitivity_rank"]
        if ranks[child_class["sensitivity_level"]] < ranks[root_class["sensitivity_level"]] or not set(json.loads(root_class["handling_tags_json"])).issubset(set(json.loads(child_class["handling_tags_json"]))):
            raise RuntimeDenied("CHILD_RUN_CLASSIFICATION_DOWNGRADE")
        self._assert_classification_scope(event_classification_assertion_ref, "TRACE_EVENT", "evt-" + command_id + "-create-run", child_principals, child_boundary, at_least_level=child_class["sensitivity_level"], required_tags=set(json.loads(child_class["handling_tags_json"])))
        self._assert_classification_scope(ready_event_classification_assertion_ref, "TRACE_EVENT", "evt-" + command_id + "-ready", child_principals, child_boundary, at_least_level=child_class["sensitivity_level"], required_tags=set(json.loads(child_class["handling_tags_json"])))
        self._authorize(child_grant_id, task_id, child_run_id, "RUN_CREATE", command_id + "-create-run-authorize")
        self._authorize(child_grant_id, task_id, child_run_id, "RUN_TRANSITION", command_id + "-ready-authorize")
        self._authorize(child_grant_id, task_id, child_run_id, "OBJECT_WRITE", command_id + "-bind-manifest-authorize")
        account_id = contract["budget_account_ref"]
        budget_snapshot = self._budget_snapshot(account_id)
        route_id = None
        selected_profile = None
        descriptor = None
        if node["requested_executor"] == "MODEL":
            if not route_object_id or not route_classification_assertion_ref:
                raise RuntimeDenied("ROUTE_DECISION_OBJECT_CLASSIFICATION_REQUIRED")
            self._authorize(root["grant_id"], task_id, root_run_id, "OBJECT_WRITE", command_id + "-route-object-authorize")
            self._assert_classification_scope(route_classification_assertion_ref, "OBJECT", route_object_id, root_principals, child_boundary, at_least_level=child_class["sensitivity_level"], required_tags=set(json.loads(child_class["handling_tags_json"])))
            with self.store._connection() as conn:
                previous_route = conn.execute("SELECT decision_json,decision_object_id FROM route_decisions WHERE command_id=?", (command_id + "-route-record",)).fetchone()
            if previous_route:
                decision = json.loads(previous_route["decision_json"])
                if previous_route["decision_object_id"] != route_object_id:
                    raise RuntimeDenied("ROUTE_DECISION_RETRY_ID_MISMATCH")
                profile_version = decision["selected_model_profile_version"]
                with self.store._connection() as conn:
                    profile_row = conn.execute("SELECT profile_json FROM model_profiles WHERE model_id=? AND version=?", (decision["selected_model_id"], profile_version)).fetchone()
                if not profile_row:
                    raise RuntimeDenied("ROUTE_DECISION_PROFILE_NO_LONGER_AVAILABLE")
                selected_profile = json.loads(profile_row["profile_json"])
            else:
                decision, selected_profile = self._choose_model(node, contract, root, budget_snapshot, contract_ref)
                decision = {**decision, "route_decision_id": route_object_id}
                self.store._validate("nexus.route_decision@1.schema.json", decision)
                route_payload = _canonical(decision).encode("utf-8")
                self.store.put_object(command_id=command_id + "-route-object", object_id=route_object_id, payload=route_payload, object_type="artifact", created_by_run=root_run_id, classification_assertion_ref=route_classification_assertion_ref)
            route_id = route_object_id
            if not previous_route:
                route_operation = "persist_route_decision"
                route_request = {"decision": decision, "object_id": route_object_id, "subtask_id": subtask_id}
                route_request_hash = self.store._request_hash(route_operation, route_request)
                with self.store._lock, self.store._connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        self.store._record_command(conn, command_id + "-route-record", route_operation, route_request_hash, {"route_decision_id": route_object_id})
                        conn.execute("INSERT INTO route_decisions(route_decision_id,subtask_id,decision_object_id,decision_json,command_id,created_at) VALUES(?,?,?,?,?,?)", (route_object_id, subtask_id, route_object_id, _canonical(decision), command_id + "-route-record", decision["created_at"]))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            reserve_amount = selected_profile["cost_profile"]["estimated_cost"]
        else:
            with self.store._connection() as conn:
                desc_row = conn.execute("SELECT descriptor_json FROM tool_descriptors WHERE tool_id=? ORDER BY version DESC LIMIT 1", (node["tool_id"],)).fetchone()
            if not desc_row:
                raise RuntimeDenied("TOOL_DESCRIPTOR_NOT_REGISTERED")
            descriptor = json.loads(desc_row["descriptor_json"])
            if descriptor["review_status"] != "APPROVED":
                raise RuntimeDenied("TOOL_DESCRIPTOR_NOT_REVIEWED")
            if not set(descriptor["required_authority"]).issubset(self.authority.compute_effective_authority(child_grant_id)["action_scope"]):
                raise RuntimeDenied("TOOL_REQUIRED_AUTHORITY_NOT_GRANTED")
            for input_id in node["input_object_refs"]:
                metadata = self.store.get_object_metadata(input_id)
                with self.store._connection() as conn:
                    classification = conn.execute("SELECT sensitivity_level FROM classification_assertions WHERE assertion_id=?", (metadata.get("classification_assertion_ref"),)).fetchone()
                if not classification or classification["sensitivity_level"] not in descriptor["required_classifications"]:
                    raise RuntimeDenied("TOOL_INPUT_CLASSIFICATION_NOT_DECLARED")
            if budget_snapshot["tool_calls_remaining"] < 1 or budget_snapshot["child_runs_remaining"] < 1 or node["budget_amount"] > budget_snapshot["remaining"]:
                raise RuntimeDenied("TOOL_BUDGET_UNAVAILABLE")
            reserve_amount = node["budget_amount"]
        self._assert_classification_scope(manifest_classification_assertion_ref, "OBJECT", manifest_object_id, child_principals, child_boundary, at_least_level=child_class["sensitivity_level"], required_tags=set(json.loads(child_class["handling_tags_json"])))
        reservation_id = self.budget.reserve(command_id=command_id + "-budget", account_id=account_id, run_id=child_run_id, amount=reserve_amount, model_calls=1 if selected_profile else 0, tool_calls=1 if descriptor else 0, child_runs=1)
        child_run = {"schema_id": "nexus.run", "schema_version": 1, "run_id": child_run_id, "task_id": task_id, "subtask_id": subtask_id, "parent_run_id": root_run_id, "executor_kind": node["requested_executor"], "status": "CREATED", "grant_id": child_grant_id, "budget_reservation_ref": reservation_id, "data_boundary": child_boundary, "classification_assertion_ref": child_classification_assertion_ref, "created_at": node["created_at"]}
        self.trace.create_run(child_run, command_id=command_id + "-create-run", event_classification_assertion_ref=event_classification_assertion_ref)
        child_input_refs = [contract_ref, *node["input_object_refs"]]
        common = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": node["requested_executor"], "runtime_version": "0.1", "policy_version": self.authority.policy["policy_version"], "schema_versions": {"nexus.run_manifest": 1, "nexus.task_contract": 1, "nexus.subtask": 1}, "input_object_refs": child_input_refs, "authority_grant_ref": child_grant_id, "budget_reservation_ref": reservation_id, "data_boundary": child_boundary, "classification_assertion_ref": child_classification_assertion_ref}
        if selected_profile:
            manifest = {**common, "model_id": selected_profile["model_id"], "provider": selected_profile["provider"], "model_class": selected_profile["model_class"], "model_adapter_version": selected_profile["model_adapter_version"], "prompt_version": "fake-v0", "context_object_refs": child_input_refs, "route_decision_ref": route_id}
        else:
            manifest = {**common, "tool_id": descriptor["tool_id"], "tool_descriptor_version": descriptor["version"], "tool_adapter_version": "fake-v0", "input_ref": node["input_object_refs"][0]}
        self.bind_manifest(command_id=command_id + "-bind-manifest", run_id=child_run_id, manifest_object_id=manifest_object_id, manifest_classification_assertion_ref=manifest_classification_assertion_ref, manifest=manifest)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute("UPDATE subtasks SET scheduled_run_id=? WHERE subtask_id=? AND status='PENDING' AND scheduled_run_id IS NULL", (child_run_id, subtask_id))
                if cur.rowcount != 1:
                    bound = conn.execute("SELECT scheduled_run_id FROM subtasks WHERE subtask_id=?", (subtask_id,)).fetchone()
                    if not bound or bound["scheduled_run_id"] != child_run_id:
                        raise RuntimeDenied("SUBTASK_RUN_BINDING_RACE")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.trace.transition_run(command_id=command_id + "-ready", run_id=child_run_id, expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_event_classification_assertion_ref)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = {"run_id": child_run_id, "status": "READY", "manifest_ref": manifest_object_id, "reservation_ref": reservation_id, "route_decision_ref": route_id}
                self.store._record_command(conn, command_id, operation, request_hash, result)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return result
