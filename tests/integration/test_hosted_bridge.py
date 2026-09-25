import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jsonschema.exceptions import ValidationError

from adapters.client.hosted import CodexHostedBridge
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.verification import VerificationService


class HostedBridgeTests(unittest.TestCase):
    def setUp(self):
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        persistent_root = os.environ.get("NEXUS_HOSTED_BRIDGE_DATA_ROOT")
        if persistent_root:
            self.temp = None
            self.root = Path(__file__).resolve().parents[2]
            self.data_root = Path(persistent_root).resolve(strict=True)
            if not (self.data_root / "nexus.sqlite").is_file():
                raise RuntimeError("Hosted persistent test requires an already initialized Nexus root")
            policy = json.loads((self.data_root / "policy.json").read_text(encoding="utf-8"))
            self.operator_id = "nexus-local-pilot-operator"
        else:
            self.temp = tempfile.TemporaryDirectory(prefix="nexus-hosted-bridge-")
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
            self.data_root = self.root / "data"
            self.operator_id = "human-root"
            policy["trust_anchors"] = [self.operator_id]
        self.policy = policy
        self.store = ObjectStore(self.data_root, policy=policy)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.verifier = VerificationService(self.store, self.authority)
        self.bridge = CodexHostedBridge(store=self.store, authority=self.authority, budget=self.budget, trace=self.trace, runtime=self.runtime, verifier=self.verifier)
        if self.operator_id == "human-root":
            self.authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":"human-root","principal_type":"HUMAN","status":"ACTIVE"}, "principal-human-root")
        for principal_id, principal_type in (("host-agent", "SERVICE"), ("host-model", "MODEL"), ("host-tool", "TOOL")):
            self.authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":principal_id,"principal_type":principal_type,"status":"ACTIVE"}, "principal-" + principal_id)
        if self.operator_id == "human-root":
            self.authority.register_trust_anchor({"schema_id":"nexus.trust_anchor","schema_version":1,"anchor_id":"anchor-root","principal_id":"human-root","policy_ref":"1"}, "anchor-register")
        self.task_id = "hosted-task"
        self.root_run_id = "hosted-root"
        self.input_id = "hosted-input"
        self.contract_id = "hosted-contract"
        self.root_manifest_id = "hosted-root-manifest"
        self.model_run_id = "hosted-model-run"
        self.model_manifest_id = "hosted-model-manifest"
        self.model_artifact_id = "hosted-model-artifact"
        self.search_evidence_id = "hosted-search-evidence"
        self.tool_run_id = "hosted-tool-run"
        self.tool_manifest_id = "hosted-tool-manifest"
        self.tool_artifact_id = "hosted-tool-artifact"
        self.tool_id = "hosted-read-file"
        self.resource_scope = [
            "task:" + self.task_id, "runtime-mode:instance", self.root_run_id, self.input_id,
            self.contract_id, self.root_manifest_id, self.model_run_id, self.model_manifest_id,
            self.model_artifact_id, self.search_evidence_id, self.tool_run_id, self.tool_manifest_id, self.tool_artifact_id,
            self.tool_id,
        ]
        for command in ("hosted-e2e-root-create", "hosted-e2e-trace-input", "hosted-e2e-root-ready", "hosted-e2e-root-running", "hosted-e2e-root-verifying", "hosted-e2e-root-succeeded"):
            self.resource_scope.append("evt-" + command)
        for prefix in ("hosted-model", "hosted-tool"):
            for suffix in ("create-run", "ready", "running", "output-trace-object", "close-verifying", "close-terminal"):
                self.resource_scope.append("evt-" + prefix + "-" + suffix)
        self.resource_scope.append("evt-hosted-search-trace-evidence")
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"hosted-root-grant",
            "issued_by":self.operator_id,"granted_to":"host-agent","task_scope":[self.task_id],
            "resource_scope":self.resource_scope,
            "action_scope":["RUN_CREATE","RUN_TRANSITION","TRACE_APPEND","OBJECT_WRITE","CLASSIFY","VERIFY","INSPECT","DELEGATE","RUNTIME_CONFIGURE","TOOL_READ"],
            "audience_scope":["nexus-runtime","nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=2)).isoformat(),"status":"ACTIVE","policy_version":"1",
        }, "hosted-root-grant-create")
        self.boundary = {"allowed_classifications":["PUBLIC"],"handling_tags":[]}
        self.contract = {
            "schema_id":"nexus.task_contract","schema_version":1,"goal":"summarize the synthetic CSV and corroborate official SQLite FTS5 documentation through Codex Search",
            "constraints":[],"success_criteria":["record a host-declared model summary, source URL Evidence and a real read-only file probe"],
            "risk_class":"STANDARD","routing_constraints":{"allowed_providers":["codex-host"],"forbidden_providers":[],"locality":"LOCAL_ONLY","network_required":False,"modalities":["text"]},
            "routing_preferences":{"optimize_for":"QUALITY"},"created_at":now.isoformat(),
        }
        node = {"schema_id":"nexus.subtask","schema_version":1,"subtask_id":"hosted-model-node","task_id":self.task_id,
                "input_object_refs":[self.input_id],"input_schema_id":"nexus.object@1.schema.json","output_schema_id":"nexus.object@1.schema.json",
                "dependency_ids":[],"quality_requirement":"STANDARD","risk_class":"STANDARD","validation_method":"SCHEMA",
                "budget_amount":2,"requested_executor":"MODEL","required_modalities":["text"],"created_at":now.isoformat()}
        tool_node={"schema_id":"nexus.subtask","schema_version":1,"subtask_id":"hosted-tool-node","task_id":self.task_id,
                "input_object_refs":[self.input_id],"input_schema_id":"nexus.object@1.schema.json","output_schema_id":"nexus.object@1.schema.json",
                "dependency_ids":[],"quality_requirement":"ROUTINE","risk_class":"LOW","validation_method":"TEST",
                "budget_amount":1,"requested_executor":"TOOL","tool_id":self.tool_id,"required_modalities":["text"],"created_at":now.isoformat()}
        self.bridge.create_task_root(
            command_id="hosted-e2e",task_id=self.task_id,requester_id=self.operator_id,grant_id="hosted-root-grant",
            root_run_id=self.root_run_id,budget_account_id="hosted-budget",
            budget_limits={"amount_limit":10,"unit":"test-units","model_call_limit":2,"tool_call_limit":2,"child_run_limit":2},
            input_object_id=self.input_id,input_payload=(Path(__file__).resolve().parents[2]/"eval/regression/fixtures/nexus-hosted-e2e-input.csv").read_bytes(),task_contract=self.contract,
            contract_object_id=self.contract_id,dag_nodes=[node,tool_node],root_manifest_object_id=self.root_manifest_id,
            data_boundary=self.boundary, classifications=self._root_classes(),
        )

    def _class(self, assertion_id, subject_type, subject_ref, actor="host-agent"):
        return {"schema_id":"nexus.classification_assertion","schema_version":1,"assertion_id":assertion_id,
                "subject_type":subject_type,"subject_ref":subject_ref,"sensitivity_level":"PUBLIC","handling_tags":[],
                "policy_version":"1","reason":"synthetic hosted bridge integration","actor_id":actor}

    def _root_classes(self):
        return {
            "root_run":self._class("class-root-run","RUN",self.root_run_id),
            "root_created_event":self._class("class-root-event","TRACE_EVENT","evt-hosted-e2e-root-create"),
            "input_object":self._class("class-input","OBJECT",self.input_id),
            "input_event":self._class("class-input-event","TRACE_EVENT","evt-hosted-e2e-trace-input"),
            "task_contract":self._class("class-contract","OBJECT",self.contract_id),
            "root_manifest":self._class("class-root-manifest","OBJECT",self.root_manifest_id),
            "root_ready_event":self._class("class-root-ready","TRACE_EVENT","evt-hosted-e2e-root-ready"),
            "root_running_event":self._class("class-root-running","TRACE_EVENT","evt-hosted-e2e-root-running"),
        }

    def _child_grant(self, grant_id, principal_id, run_id, manifest_id, artifact_id, command_prefix, tool=False):
        resources = [run_id, manifest_id, artifact_id]
        if tool:
            resources.append(self.tool_id)
        else:
            resources.append(self.search_evidence_id)
        commands = [command_prefix+"-create-run", command_prefix+"-ready", command_prefix+"-running", command_prefix+"-output-trace-object", "hosted-"+command_prefix.split("-")[1]+"-close-verifying", "hosted-"+command_prefix.split("-")[1]+"-close-terminal"]
        if not tool:
            commands.append("hosted-search-trace-evidence")
        for command in commands:
            event = "evt-" + command
            resources.append(event)
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":grant_id,
            "parent_grant_id":"hosted-root-grant","issued_by":"host-agent","granted_to":principal_id,
            "task_scope":[self.task_id],"resource_scope":resources,
            "action_scope":["RUN_CREATE","RUN_TRANSITION","TRACE_APPEND","OBJECT_WRITE","CLASSIFY","VERIFY","TOOL_READ"],
            "audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),
            "status":"ACTIVE","policy_version":"1"}, "create-" + grant_id)

    def _add_class(self, assertion_id, subject_type, subject_ref, actor, grant_id="hosted-root-grant"):
        self.authority.record_classification_assertion(self._class(assertion_id,subject_type,subject_ref,actor),
            grant_id=grant_id,task_id=self.task_id,audience="nexus-runtime",command_id="record-"+assertion_id)

    def _child_event_classes(self, prefix, actor, grant_id):
        result = {}
        for key, suffix in (("create","create-run"),("ready","ready"),("running","running")):
            event_id = "evt-"+prefix+"-"+suffix
            assertion_id = "class-"+prefix+"-"+key
            self._add_class(assertion_id,"TRACE_EVENT",event_id,actor,grant_id)
            result[key]=assertion_id
        return result

    def _start_child(self, kind, run_id, manifest_id, artifact_id, grant_id, actor, command_id):
        self._child_grant(grant_id,actor,run_id,manifest_id,artifact_id,command_id,tool=kind=="TOOL")
        run_class="class-"+run_id
        manifest_class="class-"+manifest_id
        self._add_class(run_class,"RUN",run_id,actor,grant_id)
        self._add_class(manifest_class,"OBJECT",manifest_id,actor,grant_id)
        events=self._child_event_classes(command_id,actor,grant_id)
        run={"schema_id":"nexus.run","schema_version":1,"run_id":run_id,"task_id":self.task_id,
             "subtask_id":"hosted-model-node" if kind=="MODEL" else "hosted-tool-node",
             "parent_run_id":self.root_run_id,"executor_kind":kind,"status":"CREATED","grant_id":grant_id,
             "data_boundary":self.boundary,"classification_assertion_ref":run_class,"created_at":datetime.now(timezone.utc).isoformat()}
        common={"runtime_version":"0.1","policy_version":"1","schema_versions":{"nexus.run_manifest":2},
                "input_object_refs":[self.input_id,self.contract_id],"authority_grant_ref":grant_id,
                "data_boundary":self.boundary,"classification_assertion_ref":run_class}
        if kind=="MODEL":
            manifest=self.bridge.model_manifest(common=common,context_object_refs=[self.input_id])
            with self.assertRaises(ValidationError):
                self.store._validate("nexus.run_manifest@2.schema.json",{**manifest,"model_id":"unobserved-backend","provider":"unobserved-provider"})
        else:
            common={**common,"schema_versions":{"nexus.run_manifest":1}}
            manifest=self.bridge.tool_manifest(common={**common,"schema_version":1},tool_id=self.tool_id,descriptor_version="1",input_ref=self.input_id)
        return self.bridge.create_child_run(command_id=command_id,run=run,manifest=manifest,parent_grant_id="hosted-root-grant",
            account_id="hosted-budget",estimated_units=1,manifest_object_id=manifest_id,
            manifest_classification_assertion_ref=manifest_class,event_classification_assertion_refs=events)

    def test_codex_host_model_receipt_and_real_read_only_tool_run_survive_reopen(self):
        descriptor={"schema_id":"nexus.tool_descriptor","schema_version":1,"tool_id":self.tool_id,"version":"1",
            "input_schema_id":"nexus.object@1.schema.json","output_schema_id":"nexus.object@1.schema.json","effect_class":"READ_ONLY",
            "required_authority":["TOOL_READ"],"required_classifications":["PUBLIC"],"idempotency_support":True,
            "reconciliation_capability":"NOT_APPLICABLE_READ_ONLY","compensation_capability":"NOT_APPLICABLE_READ_ONLY",
            "network_egress":False,"risk_tags":[],"review_status":"APPROVED"}
        self.runtime.register_tool_descriptor(command_id="register-hosted-read-tool",grant_id="hosted-root-grant",task_id=self.task_id,descriptor=descriptor)

        model=self._start_child("MODEL",self.model_run_id,self.model_manifest_id,self.model_artifact_id,"hosted-model-grant","host-model","hosted-model")
        self.assertEqual(model["status"],"RUNNING")
        model_class="class-"+self.model_artifact_id
        model_event="evt-hosted-model-output-trace-object"
        self._add_class(model_class,"OBJECT",self.model_artifact_id,"host-model","hosted-model-grant")
        self._add_class("class-model-output-event","TRACE_EVENT",model_event,"host-model","hosted-model-grant")
        model_result=self.bridge.record_output(command_id="hosted-model-output",task_id=self.task_id,run_id=self.model_run_id,grant_id="hosted-model-grant",
            artifact_id=self.model_artifact_id,payload=(Path(__file__).resolve().parents[2]/"eval/regression/fixtures/nexus-hosted-e2e-model-result.txt").read_bytes(),classification_assertion_ref=model_class,
            event_classification_assertion_ref="class-model-output-event",verifier_id="hosted-model-verification")
        self.assertEqual(model_result["verification"]["verdict"],"PASS")
        self._add_class("class-hosted-search-evidence","OBJECT",self.search_evidence_id,"host-model","hosted-model-grant")
        self._add_class("class-hosted-search-event","TRACE_EVENT","evt-hosted-search-trace-evidence","host-model","hosted-model-grant")
        search_result=self.bridge.record_evidence(command_id="hosted-search",task_id=self.task_id,run_id=self.model_run_id,grant_id="hosted-model-grant",
            evidence_id=self.search_evidence_id,source_url="https://www.sqlite.org/fts5.html",
            retrieved_content=b"Official SQLite FTS5 documentation describes FTS5 as a virtual-table module providing full-text search. It documents MATCH queries, prefixes, phrases, NEAR queries, and boolean combinations.",
            classification_assertion_ref="class-hosted-search-evidence",event_classification_assertion_ref="class-hosted-search-event",verifier_id="hosted-search-verification")
        self.assertEqual(search_result["verification"]["verdict"],"PASS")
        self.bridge.close_child(command_id="hosted-model-close",run_id=self.model_run_id,succeeded=True,
            reservation_id=model["reservation_ref"],actual_units=0,
            classification_assertion_refs=self._terminal_classes("model","host-model"))

        tool=self._start_child("TOOL",self.tool_run_id,self.tool_manifest_id,self.tool_artifact_id,"hosted-tool-grant","host-tool","hosted-tool")
        self.assertEqual(tool["status"],"RUNNING")
        fixture_root=Path(__file__).resolve().parents[2]
        fixture_relative="eval/regression/fixtures/nexus-hosted-e2e-input.csv"
        with self.assertRaises(PermissionError):
            self.bridge.execute_read_only_file_probe(grant_id="hosted-tool-grant",task_id=self.task_id,tool_id=self.tool_id,
                descriptor={**descriptor,"effect_class":"EXTERNAL_REVERSIBLE"},allowed_root=fixture_root,relative_path=fixture_relative)
        with self.assertRaises(PermissionError):
            self.bridge.execute_read_only_file_probe(grant_id="hosted-tool-grant",task_id=self.task_id,tool_id=self.tool_id,
                descriptor=descriptor,allowed_root=fixture_root,relative_path="..\\outside.txt")
        receipt=self.bridge.execute_read_only_file_probe(grant_id="hosted-tool-grant",task_id=self.task_id,tool_id=self.tool_id,
            descriptor=descriptor,allowed_root=fixture_root,relative_path=fixture_relative)
        tool_class="class-"+self.tool_artifact_id
        tool_event="evt-hosted-tool-output-trace-object"
        self._add_class(tool_class,"OBJECT",self.tool_artifact_id,"host-tool","hosted-tool-grant")
        self._add_class("class-tool-output-event","TRACE_EVENT",tool_event,"host-tool","hosted-tool-grant")
        tool_result=self.bridge.record_output(command_id="hosted-tool-output",task_id=self.task_id,run_id=self.tool_run_id,grant_id="hosted-tool-grant",
            artifact_id=self.tool_artifact_id,payload=receipt,classification_assertion_ref=tool_class,
            event_classification_assertion_ref="class-tool-output-event",verifier_id="hosted-tool-verification")
        self.assertEqual(tool_result["verification"]["verdict"],"PASS")
        self.bridge.close_child(command_id="hosted-tool-close",run_id=self.tool_run_id,succeeded=True,
            reservation_id=tool["reservation_ref"],actual_units=0,
            classification_assertion_refs=self._terminal_classes("tool","host-tool"))

        for suffix, assertion_id in (("verifying","class-root-verifying"),("succeeded","class-root-succeeded")):
            self._add_class(assertion_id,"TRACE_EVENT","evt-hosted-e2e-root-"+suffix,"host-agent")
        self.trace.transition_run(command_id="hosted-e2e-root-verifying",run_id=self.root_run_id,expected_state="RUNNING",next_state="VERIFYING",classification_assertion_ref="class-root-verifying")
        self.trace.transition_run(command_id="hosted-e2e-root-succeeded",run_id=self.root_run_id,expected_state="VERIFYING",next_state="SUCCEEDED",classification_assertion_ref="class-root-succeeded")
        projection=self.runtime.inspect_task(grant_id="hosted-root-grant",task_id=self.task_id)
        self.assertEqual(projection["task"]["status"],"SUCCEEDED")
        self.assertEqual({item["executor_kind"] for item in projection["runs"]},{"ORCHESTRATOR","MODEL","TOOL"})
        manifest=json.loads(self.store.get_payload(self.model_manifest_id).decode("utf-8"))
        self.assertEqual((manifest["execution_source"],manifest["model_identity_status"]),("CODEX_HOST_DECLARED","UNAVAILABLE"))
        with self.store._connection() as conn:
            trace_count=conn.execute("select count(*) from trace_events where run_id in (?,?)",(self.model_run_id,self.tool_run_id)).fetchone()[0]
        self.assertGreaterEqual(trace_count,8)
        data_root=self.data_root
        policy=self.policy
        self.store.close()
        self.store=ObjectStore(data_root,policy=policy)
        self.addCleanup(self.store.close)
        self.authority=AuthorityService(self.store,policy)
        self.budget=BudgetService(self.store)
        self.trace=TraceRuntime(self.store,self.authority)
        self.runtime=DeterministicRuntime(self.store,self.authority,self.budget,self.trace)
        self.verifier=VerificationService(self.store,self.authority)
        self.bridge=CodexHostedBridge(store=self.store,authority=self.authority,budget=self.budget,trace=self.trace,runtime=self.runtime,verifier=self.verifier)
        reopened=self.runtime.inspect_task(grant_id="hosted-root-grant",task_id=self.task_id)
        self.assertEqual(reopened["task"]["status"],"SUCCEEDED")
        self.assertEqual(self.trace.replay_run(self.model_run_id)["status"],"SUCCEEDED")
        self.assertEqual(self.trace.replay_run(self.tool_run_id)["status"],"SUCCEEDED")
        self.assertEqual(self.runtime.replay_subtask("hosted-model-node")["status"],"SUCCEEDED")
        self.assertEqual(self.runtime.replay_subtask("hosted-tool-node")["status"],"SUCCEEDED")
        self.assertEqual(self.verifier.get("hosted-model-verification")["verdict"],"PASS")
        self.assertEqual(self.verifier.get("hosted-tool-verification")["verdict"],"PASS")
        self.assertEqual(self.verifier.get("hosted-search-verification")["verdict"],"PASS")
        evidence=json.loads(self.store.get_payload(self.search_evidence_id).decode("utf-8"))
        self.assertEqual(evidence["source_url"],"https://www.sqlite.org/fts5.html")

    def _terminal_classes(self, prefix, actor):
        result={}
        for key, suffix in (("verifying","verifying"),("terminal","terminal")):
            command="hosted-"+prefix+"-close-"+suffix
            assertion="class-"+prefix+"-close-"+suffix
            grant_id="hosted-model-grant" if prefix=="model" else "hosted-tool-grant"
            self._add_class(assertion,"TRACE_EVENT","evt-"+command,actor,grant_id)
            result[key]=assertion
        return result
