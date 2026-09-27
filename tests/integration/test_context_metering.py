from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from jsonschema import ValidationError

from adapters.panel.application import open_panel_application
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.authority.errors import AuthorizationDenied
from kernel.budget import BudgetService
from kernel.context import ContextPackService
from kernel.memory.service import MemoryService
from kernel.metering import MeteringService
from kernel.metering.service import metric, unavailable_metrics
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.runtime.errors import RuntimeDenied
from kernel.object.errors import SchemaUnsupported
from kernel.runtime.panel import PanelQueryService
from kernel.object.errors import WriterAlreadyRunning


class ContextMeteringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-context-metering-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.store = ObjectStore(self.root, policy=self.policy)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.participation = ParticipationModeService(self.store)
        self.memory = MemoryService(self.store, self.authority, verifier=None)
        self.metering = MeteringService(self.store, self.authority, self.participation)
        self.context = ContextPackService(store=self.store, authority=self.authority,
                                          participation=self.participation,
                                          memory=self.memory, metering=self.metering)
        self._create_task_run_and_sources()

    def _class(self, ident, subject_type, subject_ref):
        return {"schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": ident, "subject_type": subject_type, "subject_ref": subject_ref,
                "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
                "reason": "isolated Context Pack integration test", "actor_id": "context-agent"}

    def _create_task_run_and_sources(self):
        now = datetime.now(timezone.utc)
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                                           "principal_id": "human-root", "principal_type": "HUMAN", "status": "ACTIVE"}, "ctx-human")
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                                           "principal_id": "context-agent", "principal_type": "SERVICE", "status": "ACTIVE"}, "ctx-agent")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
                                              "anchor_id": "ctx-anchor", "principal_id": "human-root", "policy_ref": "1"}, "ctx-anchor")
        self.task_id, self.run_id = "ctx-task", "ctx-run"
        self.source_ids = ("ctx-source-z", "ctx-source-a")
        self.memory_id = "ctx-admitted-memory"
        self.pack_ids = ("ctx-pack-one", "ctx-pack-two", "ctx-pack-observe", "ctx-pack-bypass")
        all_sources = self.source_ids + (self.memory_id, "ctx-not-eligible-skill")
        resources = ["task:" + self.task_id, self.run_id, *all_sources,
                     *("object:" + object_id for object_id in all_sources), *self.pack_ids,
                     "evt-ctx-run-create"]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "ctx-grant",
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [self.task_id],
            "resource_scope": resources,
            "action_scope": ["RUN_CREATE", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "INSPECT", "MEMORY_SEARCH"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-grant-create")
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": self.task_id,
                                "requester_id": "human-root", "status": "CREATED", "created_at": now.isoformat(),
                                "command_id": "ctx-task-create"})
        assertions = [self._class("ctx-run-class", "RUN", self.run_id),
                      self._class("ctx-run-event-class", "TRACE_EVENT", "evt-ctx-run-create")]
        for object_id in self.source_ids + (self.memory_id, "ctx-not-eligible-skill") + self.pack_ids:
            assertions.append(self._class("class-" + object_id, "OBJECT", object_id))
        for assertion in assertions:
            self.authority.record_classification_assertion(assertion, grant_id="ctx-grant", task_id=self.task_id,
                                                           audience="nexus-runtime", command_id="record-" + assertion["assertion_id"])
        self.trace.create_run({"schema_id": "nexus.run", "schema_version": 1, "run_id": self.run_id,
                               "task_id": self.task_id, "executor_kind": "ORCHESTRATOR", "status": "CREATED",
                               "grant_id": "ctx-grant", "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
                               "classification_assertion_ref": "ctx-run-class", "created_at": now.isoformat()},
                              command_id="ctx-run-create", event_classification_assertion_ref="ctx-run-event-class")
        for object_id, text in zip(self.source_ids, ("source z", "source a")):
            self.store.put_object(command_id="put-" + object_id, object_id=object_id, payload=text.encode(),
                                  object_type="user_input", created_by_run=self.run_id,
                                  classification_assertion_ref="class-" + object_id)
        self.store.put_object(command_id="put-admitted-memory", object_id=self.memory_id,
                              payload=b"verified memory", object_type="memory", created_by_run=self.run_id,
                              classification_assertion_ref="class-" + self.memory_id)
        self.store.put_object(command_id="put-skill", object_id="ctx-not-eligible-skill",
                              payload=b"synthetic skill marker", object_type="skill", created_by_run=self.run_id,
                              classification_assertion_ref="class-ctx-not-eligible-skill")

    def _compile(self, pack_id, command_id, refs=None, memory_query=None):
        return self.context.compile(task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
                                    pack_object_id=pack_id, classification_assertion_ref="class-" + pack_id,
                                    command_id=command_id, source_refs=self.source_ids if refs is None else refs,
                                    memory_query=memory_query)

    def test_selection_order_content_and_hash_are_deterministic_and_exact_bytes(self):
        first = self._compile(self.pack_ids[0], "compile-one", refs=list(reversed(self.source_ids)))
        second = self._compile(self.pack_ids[0], "compile-one", refs=self.source_ids)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["selected_refs"], ["ctx-source-a", "ctx-source-z"])
        first_payload = self.store.get_payload(self.pack_ids[0])
        document = json.loads(first_payload)
        self.assertEqual(first["serialized_byte_size"], len(first_payload))
        self.assertEqual(first["integrity_hash"], hashlib.sha256(first_payload).hexdigest())
        self.assertEqual([entry["source_ref"] for entry in document["entries"]], first["selected_refs"])
        self.assertEqual(first["host_delivery_status"], "NOT_DECLARED")
        self.assertEqual(first["model_visible_exposure"], "UNKNOWN")
        with self.store._connection() as conn:
            metric_record = conn.execute("SELECT metrics_json FROM value_metering_records WHERE context_pack_ref=?", (self.pack_ids[0],)).fetchone()
        self.assertEqual(json.loads(metric_record[0])["context_pack_bytes"]["value"], len(first_payload))

    def test_ineligible_and_unavailable_sources_fail_closed(self):
        with self.assertRaisesRegex(AuthorizationDenied, "SCOPE_DENIED"):
            self._compile(self.pack_ids[0], "unauthorized-source", refs=["outside-task-object"])
        skill_id = "ctx-not-eligible-skill"
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_TYPE_INELIGIBLE"):
            self._compile(self.pack_ids[0], "ineligible-source", refs=[skill_id])
        external = sqlite3.connect(self.store.database_path)
        try:
            external.execute("UPDATE object_states SET validity='INVALIDATED' WHERE object_id=?", (self.source_ids[0],))
            external.commit()
        finally:
            external.close()
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_UNAVAILABLE"):
            self._compile(self.pack_ids[0], "invalidated-source", refs=[self.source_ids[0]])
        external = sqlite3.connect(self.store.database_path)
        try:
            external.execute("UPDATE object_states SET payload_state='PURGED' WHERE object_id=?", (self.source_ids[1],))
            external.commit()
        finally:
            external.close()
        with self.assertRaises(RuntimeDenied):
            self._compile(self.pack_ids[0], "purged-source", refs=[self.source_ids[1]])

    def test_admitted_memory_only_is_queried_and_raw_history_is_never_consulted(self):
        admitted = {"object_id": self.memory_id, "body": "verified memory"}
        with mock.patch.object(self.memory, "search_admitted", return_value=[admitted]) as admitted_search, \
             mock.patch.object(self.memory, "search_raw", side_effect=AssertionError("raw history must not be queried")):
            result = self._compile(self.pack_ids[0], "admitted-only", refs=[], memory_query="verified")
        admitted_search.assert_called_once_with(query="verified", run_id=self.run_id, limit=20)
        self.assertEqual(result["selected_source_counts"], {"admitted_memory": 1})
        self.assertEqual(self.store.get_object_metadata(self.pack_ids[0])["object_type"], "artifact")

    def test_observe_and_bypass_cannot_compile_context_but_keep_state(self):
        self._compile(self.pack_ids[0], "active-pack")
        for mode, pack_id, command_id in (("OBSERVE", self.pack_ids[2], "observe-pack"),
                                          ("BYPASS", self.pack_ids[3], "bypass-pack")):
            with mock.patch.object(self.participation, "current", return_value={"mode": mode}):
                with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_REQUIRES_ACTIVE_PARTICIPATION"):
                    self._compile(pack_id, command_id)
        self.assertEqual(self.context.latest()["pack_id"], self.pack_ids[0])
        self.assertEqual(self.store.get_payload(self.pack_ids[0])[:1], b"{")

    def test_missing_and_host_declared_telemetry_preserve_provenance(self):
        empty = unavailable_metrics()
        self.assertIsNone(empty["model_visible_input_tokens"]["value"])
        self.assertEqual(empty["model_visible_input_tokens"]["provenance"], "UNAVAILABLE")
        self._compile(self.pack_ids[0], "meter-pack")
        record = self.metering.record_host_declared(
            record_id="host-meter", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
            host_usage={"input_tokens": 100, "cached_input_tokens": 25, "cache_write_input_tokens": 2,
                        "output_tokens": 4, "reasoning_output_tokens": 1},
            context_pack_ref=self.pack_ids[0],
            estimated_cost={"value": 0.01, "currency": "USD", "basis": {
                "pricing_id": "synthetic-price", "pricing_version": "1", "formula": "explicit test basis"}},
        )
        metrics = record["metrics"]
        self.assertEqual(metrics["host_total_turn_input_tokens"]["provenance"], "HOST_DECLARED")
        self.assertIsNone(metrics["model_visible_input_tokens"]["value"])
        self.assertEqual(metrics["cached_input_tokens"]["value"], 25)
        self.assertEqual(metrics["uncached_input_tokens"]["value"], 75)
        self.assertEqual(metrics["uncached_input_tokens"]["provenance"], "DERIVED")
        self.assertEqual(metrics["cache_write_input_tokens"]["value"], 2)
        self.assertEqual(metrics["reasoning_tokens"]["value"], 1)
        self.assertIsNone(metrics["token_savings"]["value"])
        self.assertEqual(metrics["token_savings"]["provenance"], "UNAVAILABLE")
        self.assertIsNone(metrics["actual_cost"]["value"])
        self.assertEqual(metrics["actual_cost"]["provenance"], "UNAVAILABLE")
        self.assertEqual(metrics["estimated_cost"]["provenance"], "HOST_DECLARED")
        self.assertEqual(metrics["estimated_cost"]["estimate_status"], "ESTIMATED")
        self.assertEqual(metrics["estimated_cost"]["estimation_basis"]["pricing_version"], "1")
        total_only = self.metering.record_host_declared(
            record_id="host-total-only", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
            host_usage={"input_tokens": 33},
        )["metrics"]
        self.assertIsNone(total_only["uncached_input_tokens"]["value"])
        self.assertEqual(total_only["uncached_input_tokens"]["provenance"], "UNAVAILABLE")
        with self.assertRaisesRegex(ValueError, "ESTIMATED_METRIC_BASIS_REQUIRED"):
            metric(1.0, "HOST_DECLARED", unit="USD", estimate_status="ESTIMATED")
        estimated = metric(1.0, "DERIVED", unit="USD", estimate_status="ESTIMATED", estimation_basis={
            "pricing_id": "test-price", "pricing_version": "1", "formula": "explicit test formula"})
        self.assertEqual(estimated["provenance"], "DERIVED")
        self.assertEqual(estimated["estimate_status"], "ESTIMATED")

    def test_metering_exact_retry_replays_and_conflicting_retry_fails_closed(self):
        kwargs = dict(record_id="idempotent-host-event", task_id=self.task_id, run_id=self.run_id,
                      grant_id="ctx-grant", host_usage={"input_tokens": 17, "cached_input_tokens": 4})
        first = self.metering.record_host_declared(**kwargs)
        with self.store._connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0]
        replay = self.metering.record_host_declared(**kwargs)
        self.assertEqual(replay, first)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0], before)
        with self.assertRaisesRegex(RuntimeDenied, "METERING_RECORD_ID_CONFLICT"):
            self.metering.record_host_declared(**{**kwargs, "host_usage": {"input_tokens": 18}})

    def test_metering_exact_retry_survives_participation_mode_change(self):
        kwargs = dict(record_id="mode-replay-host-event", task_id=self.task_id, run_id=self.run_id,
                      grant_id="ctx-grant", host_usage={"input_tokens": 17, "cached_input_tokens": 4})
        original = self.metering.record_host_declared(**kwargs)
        with mock.patch.object(ParticipationModeService, "_blockers", return_value={
            "active_tasks": 0, "active_runs": 0, "pending_effects": 0, "approval_or_commit_pending": 0,
        }):
            self.participation.set_mode(mode="OBSERVE", expected_mode="ACTIVE", command_id="mode-replay-observe")
        self.assertEqual(self.metering.record_host_declared(**kwargs), original)
        with mock.patch.object(ParticipationModeService, "_blockers", return_value={
            "active_tasks": 0, "active_runs": 0, "pending_effects": 0, "approval_or_commit_pending": 0,
        }):
            self.participation.set_mode(mode="BYPASS", expected_mode="OBSERVE", command_id="mode-replay-bypass")
        self.assertEqual(self.metering.record_host_declared(**kwargs), original)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM value_metering_records WHERE record_id=?", (kwargs["record_id"],)
            ).fetchone()[0], 1)
        with self.assertRaisesRegex(RuntimeDenied, "METERING_RECORD_ID_CONFLICT"):
            self.metering.record_host_declared(**{**kwargs, "host_usage": {"input_tokens": 18}})
        with self.assertRaisesRegex(RuntimeDenied, "BYPASS_DISALLOWS_AUTOMATIC_METERING_INGESTION"):
            self.metering.record_host_declared(**{**kwargs, "record_id": "new-bypass-event"})

    def test_metering_rejects_grants_not_bound_to_target_run_without_inserting(self):
        now = datetime.now(timezone.utc)
        grants = (
            ("meter-broad-same-task", [self.task_id],
             ["task:" + self.task_id, self.run_id, "object:extra-resource"],
             ["TRACE_APPEND", "OBJECT_WRITE", "INSPECT", "CLASSIFY", "MEMORY_SEARCH"],
             ["nexus-runtime", "nexus-inspect"]),
            ("meter-unrelated", ["different-task"], [self.run_id], ["TRACE_APPEND"], ["nexus-runtime"]),
        )
        for grant_id, tasks, resources, actions, audiences in grants:
            self.authority.create_grant({
                "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": grant_id,
                "issued_by": "human-root", "granted_to": "context-agent", "task_scope": tasks,
                "resource_scope": resources, "action_scope": actions, "audience_scope": audiences,
                "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat(),
                "status": "ACTIVE", "policy_version": "1",
            }, grant_id + "-create")
        with self.store._connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0]
        for grant_id in ("meter-broad-same-task", "meter-unrelated"):
            with self.subTest(grant_id=grant_id), self.assertRaisesRegex(RuntimeDenied, "METERING_RUN_GRANT_MISMATCH"):
                self.metering.record_host_declared(
                    record_id="must-not-be-recorded-" + grant_id, task_id=self.task_id, run_id=self.run_id,
                    grant_id=grant_id, host_usage={"input_tokens": 9},
                )
        with self.store._connection() as conn:
            after = conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0]
        self.assertEqual(after, before)

    def test_metering_v1_is_historical_v2_is_current_and_unknown_version_fails_closed(self):
        schema_dir = Path(__file__).resolve().parents[2] / "schemas"
        v1 = json.loads((schema_dir / "nexus.metering_record@1.schema.json").read_text(encoding="utf-8"))
        v2 = json.loads((schema_dir / "nexus.metering_record@2.schema.json").read_text(encoding="utf-8"))
        self.assertIn("ESTIMATED", v1["$defs"]["metric"]["properties"]["provenance"]["enum"])
        self.assertNotIn("estimate_status", v1["$defs"]["metric"]["properties"])
        self.assertEqual(v2["properties"]["schema_version"]["const"], 2)
        self.assertNotIn("ESTIMATED", v2["$defs"]["metric"]["properties"]["provenance"]["enum"])
        self.assertIn("estimate_status", v2["$defs"]["metric"]["properties"])
        legacy_document = {
            "schema_id": "nexus.metering_record", "schema_version": 1, "task_id": self.task_id,
            "run_id": self.run_id, "record_source": "HOST_DECLARED", "participation_mode": "ACTIVE",
            "context_pack_ref": None,
            "metrics": {"estimated_cost": {"value": 0.5, "provenance": "ESTIMATED", "unit": "USD",
                "basis": "legacy v1 estimate", "estimation_basis": {"pricing_id": "p", "pricing_version": "1", "formula": "f"}}},
            "metrics_sha256": "a" * 64, "recorded_at": "2026-09-28T00:00:00Z",
        }
        self.store._validate("nexus.metering_record@1.schema.json", legacy_document)
        with self.assertRaises(ValidationError):
            self.store._validate("nexus.metering_record@2.schema.json", legacy_document)
        record = self.metering.record_host_declared(
            record_id="schema-v2-host-event", task_id=self.task_id, run_id=self.run_id,
            grant_id="ctx-grant", host_usage={"input_tokens": 3},
        )
        document = {
            "schema_id": "nexus.metering_record", "schema_version": 2, "task_id": record["task_id"],
            "run_id": record["run_id"], "record_source": record["record_source"],
            "participation_mode": record["participation_mode"], "context_pack_ref": record["context_pack_ref"],
            "metrics": record["metrics"], "metrics_sha256": record["metrics_sha256"],
            "recorded_at": record["recorded_at"],
        }
        self.store._validate("nexus.metering_record@2.schema.json", document)
        with self.assertRaises(ValidationError):
            self.store._validate("nexus.metering_record@1.schema.json", document)
        with self.assertRaisesRegex(SchemaUnsupported, "SCHEMA_UNSUPPORTED"):
            self.store._validate("nexus.metering_record@3.schema.json", document)

    def test_observe_host_metering_fails_without_observation_identity(self):
        with mock.patch.object(self.participation, "current", return_value={"mode": "OBSERVE"}):
            with self.assertRaisesRegex(RuntimeDenied, "OBSERVE_METERING_UNSUPPORTED_WITHOUT_OBSERVATION_ID"):
                self.metering.record_host_declared(
                    record_id="ambiguous-observe", task_id=self.task_id, run_id=self.run_id,
                    grant_id="ctx-grant", host_usage={"input_tokens": 10},
                )

    def test_migration_25_preserves_estimated_source_provenance(self):
        old_metrics = unavailable_metrics()
        old_metrics = {key: {name: value for name, value in item.items() if name != "estimate_status"}
                       for key, item in old_metrics.items()}
        old_metrics["estimated_cost"] = {
            "value": 0.25, "provenance": "ESTIMATED", "unit": "USD", "basis": "legacy persisted pricing",
            "estimation_basis": {"pricing_id": "legacy-price", "pricing_version": "v1", "formula": "test"},
        }
        metrics_json = json.dumps(old_metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO value_metering_records(record_id,task_id,run_id,record_source,participation_mode,context_pack_ref,metrics_json,metrics_sha256,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("legacy-estimated-meter", self.task_id, self.run_id, "HOST_DECLARED", "ACTIVE", None,
                 metrics_json, hashlib.sha256(metrics_json.encode("utf-8")).hexdigest(), "2026-09-28T00:00:00Z"),
            )
            conn.execute("DELETE FROM schema_migrations WHERE version=25")
            conn.execute("PRAGMA user_version=24")
        self.store.close()
        reopened = ObjectStore(self.root, policy=self.policy)
        self.store = reopened
        self.addCleanup(reopened.close)
        with reopened._connection() as conn:
            migrated = json.loads(conn.execute(
                "SELECT metrics_json FROM value_metering_records WHERE record_id='legacy-estimated-meter'").fetchone()[0])
            self.assertEqual(migrated["estimated_cost"]["provenance"], "HOST_DECLARED")
            self.assertEqual(migrated["estimated_cost"]["estimate_status"], "ESTIMATED")
            self.assertEqual(migrated["estimated_cost"]["estimation_basis"]["pricing_id"], "legacy-price")
            self.assertEqual(migrated["model_visible_input_tokens"]["estimate_status"], "UNAVAILABLE")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 25)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        readable = MeteringService(reopened, AuthorityService(reopened, self.policy),
                                   ParticipationModeService(reopened)).snapshot()
        migrated_record = next(item for item in readable["records"] if item["record_id"] == "legacy-estimated-meter")
        self.assertEqual(migrated_record["metrics"]["estimated_cost"]["provenance"], "HOST_DECLARED")
        self.assertEqual(migrated_record["metrics"]["estimated_cost"]["estimate_status"], "ESTIMATED")

    def test_run_bound_context_rejects_mismatched_broader_caller_grant(self):
        now = datetime.now(timezone.utc)
        broad_resources = ["task:" + self.task_id, "ctx-child-run", "ctx-source-a",
                           "object:ctx-source-a", "ctx-child-pack", "object:ctx-child-pack",
                           "ctx-child-run-class", "ctx-child-event-class", "evt-ctx-child-run-create"]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "ctx-broad-grant",
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [self.task_id],
            "resource_scope": broad_resources,
            "action_scope": ["RUN_CREATE", "OBJECT_WRITE", "INSPECT", "CLASSIFY"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-broad-grant-create")
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "ctx-target-grant",
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [self.task_id],
            "resource_scope": ["ctx-child-run", "ctx-child-pack", "object:ctx-child-pack",
                               "ctx-child-run-class", "ctx-child-event-class", "evt-ctx-child-run-create"],
            "action_scope": ["RUN_CREATE", "OBJECT_WRITE", "CLASSIFY"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-target-grant-create")
        self._class_child_run("ctx-child-run-class", "RUN", "ctx-child-run", "ctx-broad-grant")
        self._class_child_run("ctx-child-event-class", "TRACE_EVENT", "evt-ctx-child-run-create", "ctx-broad-grant")
        self.budget.create_account(command_id="ctx-child-budget-create", account_id="ctx-child-budget",
                                   task_id=self.task_id, amount_limit=1, unit="calls", model_call_limit=1,
                                   tool_call_limit=0, child_run_limit=1)
        reservation = self.budget.reserve(command_id="ctx-child-reserve", account_id="ctx-child-budget",
                                          task_id=self.task_id, run_id="ctx-child-run", amount=0,
                                          model_calls=1, child_runs=1)
        self.trace.create_run({
            "schema_id": "nexus.run", "schema_version": 1, "run_id": "ctx-child-run",
            "task_id": self.task_id, "parent_run_id": self.run_id, "executor_kind": "MODEL",
            "status": "CREATED", "grant_id": "ctx-target-grant", "budget_reservation_ref": reservation,
            "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
            "classification_assertion_ref": "ctx-child-run-class", "created_at": now.isoformat(),
        }, command_id="ctx-child-run-create", event_classification_assertion_ref="ctx-child-event-class")
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_RUN_GRANT_MISMATCH"):
            self.context.compile(task_id=self.task_id, run_id="ctx-child-run", grant_id="ctx-broad-grant",
                                 pack_object_id="ctx-child-pack", classification_assertion_ref="class-ctx-child-pack",
                                 command_id="ctx-child-broad-grant", source_refs=[self.source_ids[1]])

    def _class_child_run(self, ident, subject_type, subject_ref, grant_id="ctx-grant"):
        assertion = self._class(ident, subject_type, subject_ref)
        self.authority.record_classification_assertion(assertion, grant_id=grant_id, task_id=self.task_id,
                                                       audience="nexus-runtime", command_id="record-" + ident)

    def test_target_run_boundary_rejects_source_not_allowed_by_child(self):
        now = datetime.now(timezone.utc)
        task_id, root_id, child_id = "ctx-boundary-task", "ctx-boundary-root", "ctx-boundary-child"
        source_id, pack_id = "ctx-internal-source", "ctx-boundary-pack"
        event_root, event_child = "evt-ctx-boundary-root-create", "evt-ctx-boundary-child-create"
        root_class, child_class = "ctx-boundary-root-class", "ctx-boundary-child-class"
        root_event_class, child_event_class = "ctx-boundary-root-event", "ctx-boundary-child-event"
        internal_class = "ctx-internal-class"
        root_grant = "ctx-boundary-root-grant"
        child_grant = "ctx-boundary-child-grant"
        root_resources = ["task:" + task_id, root_id, child_id, event_root, event_child,
                          root_class, root_event_class, child_class, child_event_class,
                          source_id, "object:" + source_id, pack_id, "object:" + pack_id]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": root_grant,
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [task_id],
            "resource_scope": root_resources,
            "action_scope": ["RUN_CREATE", "OBJECT_WRITE", "INSPECT", "CLASSIFY"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-boundary-root-grant-create")
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": task_id,
                                "requester_id": "human-root", "status": "CREATED", "created_at": now.isoformat(),
                                "command_id": "ctx-boundary-task-create"})
        for assertion in (self._class(root_class, "RUN", root_id), self._class(root_event_class, "TRACE_EVENT", event_root)):
            self.authority.record_classification_assertion(assertion, grant_id=root_grant, task_id=task_id,
                                                           audience="nexus-runtime", command_id="record-" + assertion["assertion_id"])
        self.trace.create_run({"schema_id": "nexus.run", "schema_version": 1, "run_id": root_id,
                               "task_id": task_id, "executor_kind": "ORCHESTRATOR", "status": "CREATED",
                               "grant_id": root_grant,
                               "data_boundary": {"allowed_classifications": ["PUBLIC", "SECRET"], "handling_tags": []},
                               "classification_assertion_ref": root_class, "created_at": now.isoformat()},
                              command_id="ctx-boundary-root-create", event_classification_assertion_ref=root_event_class)
        internal_assertion = {**self._class(internal_class, "OBJECT", source_id), "sensitivity_level": "SECRET"}
        self.authority.record_classification_assertion(internal_assertion, grant_id=root_grant, task_id=task_id,
                                                       audience="nexus-runtime", command_id="record-internal-class")
        self.store.put_object(command_id="put-internal-source", object_id=source_id,
                              payload=b"internal source", object_type="user_input", created_by_run=root_id,
                              classification_assertion_ref=internal_class)
        child_resources = [child_id, event_child, child_class, child_event_class, source_id,
                           "object:" + source_id, pack_id, "object:" + pack_id]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": child_grant,
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [task_id],
            "resource_scope": child_resources,
            "action_scope": ["RUN_CREATE", "OBJECT_WRITE", "INSPECT"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-boundary-child-grant-create")
        for assertion in (self._class(child_class, "RUN", child_id), self._class(child_event_class, "TRACE_EVENT", event_child)):
            self.authority.record_classification_assertion(assertion, grant_id=root_grant, task_id=task_id,
                                                           audience="nexus-runtime", command_id="record-" + assertion["assertion_id"])
        self.budget.create_account(command_id="ctx-boundary-budget-create", account_id="ctx-boundary-budget",
                                   task_id=task_id, amount_limit=1, unit="calls", model_call_limit=1,
                                   tool_call_limit=0, child_run_limit=1)
        reservation = self.budget.reserve(command_id="ctx-boundary-reserve", account_id="ctx-boundary-budget",
                                          task_id=task_id, run_id=child_id, amount=0,
                                          model_calls=1, child_runs=1)
        self.trace.create_run({
            "schema_id": "nexus.run", "schema_version": 1, "run_id": child_id,
            "task_id": task_id, "parent_run_id": root_id, "executor_kind": "MODEL",
            "status": "CREATED", "grant_id": child_grant, "budget_reservation_ref": reservation,
            "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
            "classification_assertion_ref": child_class, "created_at": now.isoformat(),
        }, command_id="ctx-boundary-child-create", event_classification_assertion_ref=child_event_class)
        child_context = ContextPackService(store=self.store, authority=self.authority, participation=self.participation,
                                           memory=self.memory, metering=self.metering)
        with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_OUTSIDE_RUN_BOUNDARY"):
            child_context.compile(task_id=task_id, run_id=child_id, grant_id=child_grant,
                                  pack_object_id=pack_id, classification_assertion_ref="class-ctx-boundary-pack",
                                  command_id="ctx-child-source-boundary", source_refs=[source_id])

    def test_target_run_handling_tags_are_enforced_for_explicit_source(self):
        tagged_id = self.source_ids[0]
        tagged = {**self._class("ctx-tagged-class", "OBJECT", tagged_id),
                  "supersedes": "class-" + tagged_id,
                  "handling_tags": ["NO_EXTERNAL_EGRESS"]}
        self.authority.record_classification_assertion(tagged, grant_id="ctx-grant", task_id=self.task_id,
                                                       audience="nexus-runtime", command_id="record-tagged-class")
        metadata = self.store.get_object_metadata(tagged_id)
        metadata["classification_assertion_ref"] = "ctx-tagged-class"
        with mock.patch.object(self.context.inspect, "object_metadata", return_value={
            "object_type": "user_input", "lifecycle": "ACTIVE", "validity": "VALID", "payload_state": "AVAILABLE",
        }), mock.patch.object(self.store, "get_object_metadata", return_value=metadata):
            with self.assertRaisesRegex(RuntimeDenied, "CONTEXT_PACK_SOURCE_OUTSIDE_RUN_BOUNDARY"):
                self.context._read_explicit_source(tagged_id, self.task_id, "ctx-grant",
                                                    json.dumps({"allowed_classifications": ["PUBLIC"], "handling_tags": []}))

    def test_host_delivery_declaration_is_not_observed_and_other_run_is_not_attributed(self):
        self._compile(self.pack_ids[0], "delivery-pack", refs=[])
        manifest = json.dumps({"execution_source": "CODEX_HOST_DECLARED", "context_object_refs": [self.pack_ids[0]]})
        conn = mock.Mock()
        conn.execute.side_effect = [
            mock.Mock(fetchone=mock.Mock(return_value={"task_id": self.task_id, "run_id": self.run_id})),
            mock.Mock(fetchall=mock.Mock(return_value=[{"manifest_object_id": "other-run-manifest",
                "created_by_run": "other-run", "payload_state": "AVAILABLE", "task_id": self.task_id,
                "run_id": "other-run"}])),
        ]
        with mock.patch.object(self.store, "_connection", return_value=nullcontext(conn)), \
             mock.patch.object(self.store, "get_payload", return_value=manifest.encode("utf-8")):
            self.assertEqual(self.context._delivery_status(self.pack_ids[0]), "NOT_DECLARED")

        conn.execute.side_effect = [
            mock.Mock(fetchone=mock.Mock(return_value={"task_id": self.task_id, "run_id": self.run_id})),
            mock.Mock(fetchall=mock.Mock(return_value=[{"manifest_object_id": "bound-run-manifest",
                "created_by_run": self.run_id, "payload_state": "AVAILABLE", "task_id": self.task_id,
                "run_id": self.run_id}])),
        ]
        with mock.patch.object(self.store, "_connection", return_value=nullcontext(conn)), \
             mock.patch.object(self.store, "get_payload", return_value=manifest.encode("utf-8")):
            self.assertEqual(self.context._delivery_status(self.pack_ids[0]), "HOST_DECLARED_DELIVERY")
        status = self.context.pack_status(self.pack_ids[0])
        self.assertEqual(status["host_delivery_evidence"], "NOT_DECLARED")
        self.assertEqual(status["actual_host_delivery"], "UNKNOWN")
        self.assertFalse(status["delivery_observed"])
        with mock.patch.object(self.context, "_delivery_status", return_value="HOST_DECLARED_DELIVERY"):
            declared_status = self.context.pack_status(self.pack_ids[0])
        self.assertEqual(declared_status["host_delivery_evidence"], "HOST_DECLARED_DELIVERY")
        self.assertEqual(declared_status["actual_host_delivery"], "UNKNOWN")
        self.assertFalse(declared_status["delivery_observed"])

    def test_panel_reads_context_and_metering_services_without_payload_or_storage_handles(self):
        self._compile(self.pack_ids[0], "panel-pack")
        self.metering.record_host_declared(
            record_id="panel-host-meter", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
            host_usage={"input_tokens": 100, "cached_input_tokens": 30},
            context_pack_ref=self.pack_ids[0],
        )
        writer_services = {
            "store": self.store, "runtime": self.runtime, "participation": self.participation,
            "panel_queries": PanelQueryService(self.store), "memory": self.memory,
            "context_packs": self.context, "metering": self.metering,
        }
        app = open_panel_application(self.root, writer_services=writer_services)
        try:
            with self.assertRaises(WriterAlreadyRunning):
                ObjectStore(self.root, policy=self.policy)
            snapshot = app.view_model.snapshot()
            context = snapshot["context_status"]
            self.assertEqual(context["status"], "PACK_COMPILED")
            self.assertEqual(context["pack_id"], self.pack_ids[0])
            self.assertEqual(context["model_visible_exposure"], "UNKNOWN")
            self.assertEqual(snapshot["skill_status"]["status"], "NOT IMPLEMENTED")
            self.assertEqual(snapshot["value_metrics"]["context_pack_bytes"]["provenance"], "DERIVED")
            self.assertEqual(snapshot["value_metrics"]["host_total_turn_input_tokens"]["value"], 100)
            self.assertEqual(snapshot["value_metrics"]["host_total_turn_input_tokens"]["provenance"], "HOST_DECLARED")
            self.assertIsNone(snapshot["value_metrics"]["skill_instruction_tokens"]["value"])
            self.assertEqual(snapshot["value_metrics"]["skill_instruction_tokens"]["provenance"], "UNAVAILABLE")
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn("source z", serialized)
            self.assertNotIn(str(self.root), serialized)
            self.assertFalse(hasattr(app.view_model, "store"))
            with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_CHANGE_BLOCKED_ACTIVE_TASKS"):
                app.view_model.set_participation_mode("BYPASS", expected_mode="ACTIVE")
            app.close()
            self.assertEqual(self.participation.current()["mode"], "ACTIVE")
            ui_source = (Path(__file__).resolve().parents[2] / "adapters" / "panel" / "ui.py").read_text(encoding="utf-8")
            self.assertNotIn("sqlite3", ui_source)
            self.assertNotIn("ObjectStore", ui_source)
        finally:
            app.close()

    def test_reopen_keeps_pack_and_metering_projection(self):
        self._compile(self.pack_ids[0], "reopen-pack")
        self.store.close()
        reopened = ObjectStore(self.root, policy=self.policy)
        try:
            service = ContextPackService(store=reopened, authority=AuthorityService(reopened, self.policy),
                                         participation=ParticipationModeService(reopened),
                                         memory=MemoryService(reopened, AuthorityService(reopened, self.policy), verifier=None))
            self.assertEqual(service.latest()["pack_id"], self.pack_ids[0])
            with reopened._connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM value_metering_records").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
