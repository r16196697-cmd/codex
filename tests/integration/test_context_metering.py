from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from jsonschema import ValidationError

from adapters.panel.application import open_panel_application
from adapters.storage import ObjectStore
from tests.support.test_store import open_test_store
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
from kernel.object.errors import CommandConflict
from kernel.runtime.panel import PanelQueryService
from kernel.skills import SkillRegistryService, compose_skill_application
from kernel.object.errors import WriterAlreadyRunning


class ContextMeteringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-context-metering-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.store = open_test_store(self.root, policy=self.policy)
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
        self.terminal_event_ref = "evt-ctx-run-terminal"
        self.pack_ids = ("ctx-pack-one", "ctx-pack-two", "ctx-pack-observe", "ctx-pack-bypass")
        all_sources = self.source_ids + (self.memory_id, "ctx-not-eligible-skill")
        resources = ["task:" + self.task_id, self.run_id, *all_sources,
                     *("object:" + object_id for object_id in all_sources), *self.pack_ids,
                     "evt-ctx-run-create", self.terminal_event_ref]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "ctx-grant",
            "issued_by": "human-root", "granted_to": "context-agent", "task_scope": [self.task_id],
            "resource_scope": resources,
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY", "INSPECT", "MEMORY_SEARCH"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "ctx-grant-create")
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": self.task_id,
                                "requester_id": "human-root", "status": "CREATED", "created_at": now.isoformat(),
                                "command_id": "ctx-task-create"})
        assertions = [self._class("ctx-run-class", "RUN", self.run_id),
                      self._class("ctx-run-event-class", "TRACE_EVENT", "evt-ctx-run-create"),
                      self._class("ctx-run-terminal-class", "TRACE_EVENT", self.terminal_event_ref)]
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
            conn.execute("DELETE FROM schema_migrations WHERE version=26")
            conn.execute("DELETE FROM schema_migrations WHERE version=27")
            conn.execute("DROP TRIGGER instance_policy_binding_no_update")
            conn.execute("DROP TRIGGER instance_policy_binding_no_delete")
            conn.execute("DROP TABLE instance_policy_binding")
            conn.execute("DELETE FROM schema_migrations WHERE version=28")
            conn.execute("PRAGMA user_version=24")
        (self.root / ".nexus-instance-init-v1.json").unlink()
        self.store.close()
        migration_dir = Path(self.temp.name) / "migration-25-only"
        migration_dir.mkdir()
        source_migrations = Path(__file__).resolve().parents[2] / "migrations"
        for migration in source_migrations.glob("*.sql"):
            if int(migration.name[:4]) <= 25:
                shutil.copy2(migration, migration_dir / migration.name)
        reopened = open_test_store(self.root, policy=self.policy, migrations_dir=migration_dir)
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
            "skills": SkillRegistryService(store=self.store, authority=self.authority,
                                            participation=self.participation),
        }
        app = open_panel_application(self.root, writer_services=writer_services)
        try:
            with self.assertRaises(WriterAlreadyRunning):
                open_test_store(self.root, policy=self.policy)
            snapshot = app.view_model.snapshot()
            context = snapshot["context_status"]
            self.assertEqual(context["status"], "PACK_COMPILED")
            self.assertEqual(context["pack_id"], self.pack_ids[0])
            self.assertEqual(context["model_visible_exposure"], "UNKNOWN")
            self.assertEqual(snapshot["skill_status"]["status"], "OBSERVED")
            self.assertEqual(snapshot["skill_status"]["registered_count"], 0)
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
        reopened = open_test_store(self.root, policy=self.policy)
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

    def _skill_service_and_package(self, *, name="tiny-helper", description="A tiny deterministic helper.", resources=None):
        package_root = Path(self.temp.name) / "skill-packages"
        package = package_root / name
        package.mkdir(parents=True, exist_ok=True)
        body = f"---\nname: {name}\ndescription: {description}\n---\n\nInstruction body for {name}.\n"
        (package / "SKILL.md").write_text(body, encoding="utf-8")
        for relative, content in (resources or {}).items():
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        service = SkillRegistryService(store=self.store, authority=self.authority,
                                      participation=self.participation,
                                      source_roots={"EXPLICIT_IMPORT": package_root})
        return service, package

    def _register_skill(self, *, service, package, namespace="test", name="tiny-helper", enable=True):
        from kernel.skills.service import _canonical, _sha256
        package_doc = service._read_package("EXPLICIT_IMPORT", package.name)
        identity = {"name": package_doc["name"], "source_scope": "EXPLICIT_IMPORT",
                    "source_namespace": namespace, "source_ref": package.name,
                    "package_manifest_sha256": package_doc["package_manifest_sha256"]}
        skill_id = "skill-" + _sha256(_canonical(identity))
        source_object_ref = "skill-src-" + _sha256(skill_id.encode("utf-8"))
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            self.authority.record_classification_assertion(
                self._class("class-" + source_object_ref, "OBJECT", source_object_ref),
                grant_id="ctx-grant", task_id=self.task_id, audience="nexus-runtime",
                command_id="classify-" + source_object_ref,
            )
            registered = service.register_package(
                source_scope="EXPLICIT_IMPORT", source_namespace=namespace, source_ref=package.name,
                task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
                classification_assertion_ref="class-" + source_object_ref,
                command_id="register-" + namespace,
            )
        self.assertEqual(registered["skill_id"], skill_id)
        if not enable:
            return skill_id, package_doc
        approval_id = "skill-approval-" + namespace
        self._create_skill_approval(service, skill_id, package_doc["package_manifest_sha256"], approval_id)
        with mock.patch.object(self.authority, "compute_effective_authority", return_value={
            "task_scope": {self.task_id}, "resource_scope": {skill_id},
            "action_scope": {"OBJECT_WRITE"}, "audience_scope": {"nexus-runtime"},
        }):
            enabled = service.review(skill_id=skill_id, next_status="ENABLED", task_id=self.task_id,
                                     run_id=self.run_id, grant_id="ctx-grant", command_id="review-" + namespace,
                                     approval_id=approval_id)
        self.assertTrue(enabled["eligible"])
        return skill_id, package_doc

    def _create_skill_approval(self, service, skill_id, revision, approval_id, *, decision="APPROVE",
                               target_skill_id=None, payload_revision=None, expires_at=None):
        target_skill_id = target_skill_id or skill_id
        request = {
            "skill_id": skill_id, "next_status": "ENABLED",
            "package_manifest_sha256": payload_revision or revision,
            "approval_id": approval_id, "task_id": self.task_id,
            "run_id": self.run_id, "grant_id": "ctx-grant",
        }
        now = datetime.now(timezone.utc)
        approval = {
            "schema_id": "nexus.approval_decision", "schema_version": 1,
            "approval_id": approval_id, "approver_principal_id": "human-root",
            "target_type": "OBJECT_WRITE", "target_ref": target_skill_id,
            "payload_integrity_hash": service._review_approval_payload_hash(request),
            "decision": decision, "approved_scope": ["OBJECT_WRITE", target_skill_id],
            "policy_version": "1", "issued_at": (now - timedelta(days=2) if expires_at else now).isoformat(),
        }
        if expires_at:
            approval["expires_at"] = expires_at
        self.authority.create_approval(approval, "create-" + approval_id)

    def _review_skill(self, service, skill_id, *, command_id, approval_id=None, next_status="ENABLED"):
        effective_authority = {
            "task_scope": {self.task_id}, "resource_scope": {skill_id},
            "action_scope": {"OBJECT_WRITE"}, "audience_scope": {"nexus-runtime"},
        }
        with mock.patch.object(self.authority, "compute_effective_authority", return_value=effective_authority):
            return service.review(skill_id=skill_id, next_status=next_status, task_id=self.task_id,
                                  run_id=self.run_id, grant_id="ctx-grant", command_id=command_id,
                                  approval_id=approval_id)

    def test_skill_package_validation_hashes_and_metadata_only_discovery(self):
        service, package = self._skill_service_and_package()
        skill_id, doc = self._register_skill(service=service, package=package)
        again = service._read_package("EXPLICIT_IMPORT", package.name)
        self.assertEqual(doc["package_manifest_sha256"], again["package_manifest_sha256"])
        self.assertEqual(doc["skill_md_sha256"], again["skill_md_sha256"])
        with mock.patch.object(self.store, "get_payload", side_effect=AssertionError("discovery loaded body")):
            projection = service.discover("tiny helper")
            self.assertEqual(projection["candidates"][0]["skill_id"], skill_id)
        self.assertNotIn("Instruction body", json.dumps(projection))

    def test_skill_enable_requires_revision_bound_human_approval(self):
        service, package = self._skill_service_and_package()
        skill_id, package_doc = self._register_skill(service=service, package=package, enable=False)
        revision = package_doc["package_manifest_sha256"]
        with self.assertRaisesRegex(RuntimeDenied, "SKILL_ENABLE_APPROVAL_REQUIRED"):
            self._review_skill(service, skill_id, command_id="enable-without-approval")
        cases = (
            ("wrong-skill", {"target_skill_id": "another-skill"}, "APPROVAL_TARGET_MISMATCH"),
            ("old-revision", {"payload_revision": "0" * 64}, "APPROVAL_PAYLOAD_HASH_MISMATCH"),
            ("expired", {"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}, "APPROVAL_EXPIRED"),
            ("denied", {"decision": "DENY"}, "APPROVAL_DENIED"),
        )
        for suffix, options, reason in cases:
            approval_id = "approval-" + suffix
            self._create_skill_approval(service, skill_id, revision, approval_id, **options)
            with self.subTest(reason=reason), self.assertRaisesRegex(AuthorizationDenied, reason):
                self._review_skill(service, skill_id, command_id="review-" + suffix, approval_id=approval_id)
        self.assertEqual(service.get(skill_id)["status"], "REGISTERED")
        self._create_skill_approval(service, skill_id, revision, "approval-valid")
        enabled = self._review_skill(service, skill_id, command_id="review-valid", approval_id="approval-valid")
        self.assertTrue(enabled["eligible"])

    def test_skill_review_command_ledger_prevents_old_enable_replay(self):
        service, package = self._skill_service_and_package()
        skill_id, package_doc = self._register_skill(service=service, package=package, namespace="replay", enable=False)
        self._create_skill_approval(service, skill_id, package_doc["package_manifest_sha256"], "approval-review-A")
        first = self._review_skill(service, skill_id, command_id="review-command-A", approval_id="approval-review-A")
        self.assertTrue(first["eligible"])
        disabled = self._review_skill(service, skill_id, command_id="review-command-B", next_status="DISABLED")
        self.assertEqual(disabled["status"], "DISABLED")
        replay = self._review_skill(service, skill_id, command_id="review-command-A", approval_id="approval-review-A")
        self.assertTrue(replay["eligible"], "replay returns original committed result")
        self.assertEqual(service.get(skill_id)["status"], "DISABLED", "historical replay must not reapply enable")
        with mock.patch.object(service, "_bound_run", side_effect=AssertionError("replay rechecked terminal Run")), \
             mock.patch.object(self.authority, "evaluate_authorization", side_effect=AssertionError("replay rechecked authority")), \
             mock.patch.object(self.authority, "validate_delegation_chain", side_effect=AssertionError("replay rechecked Approval")):
            replay_after_mutable_gates = self._review_skill(
                service, skill_id, command_id="review-command-A", approval_id="approval-review-A")
        self.assertEqual(replay_after_mutable_gates, replay)
        with self.assertRaises(CommandConflict):
            self._review_skill(service, skill_id, command_id="review-command-A", next_status="DISABLED")
        other_service, other_package = self._skill_service_and_package(name="second-helper")
        other_skill_id, _ = self._register_skill(service=other_service, package=other_package,
                                                  namespace="replay-other", enable=False)
        with self.assertRaises(CommandConflict):
            self._review_skill(other_service, other_skill_id, command_id="review-command-A",
                               next_status="ENABLED", approval_id="approval-review-A")

    def test_skill_package_directory_name_and_optional_frontmatter_compliance(self):
        service, package = self._skill_service_and_package(name="matching-helper")
        valid = "---\nname: matching-helper\ndescription: Small helper.\nlicense: MIT\ncompatibility: Python 3.12\nmetadata:\n  author: test\n  version: '1'\nallowed-tools: Read Search\n---\nbody\n"
        (package / "SKILL.md").write_text(valid, encoding="utf-8")
        self.assertEqual(service._read_package("EXPLICIT_IMPORT", package.name)["name"], package.name)
        (package / "SKILL.md").write_text(valid.replace("name: matching-helper", "name: other-helper"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeDenied, "SKILL_NAME_DIRECTORY_MISMATCH"):
            service._read_package("EXPLICIT_IMPORT", package.name)
        malformed_optional = valid.replace("compatibility: Python 3.12", "compatibility: " + "x" * 501)
        (package / "SKILL.md").write_text(malformed_optional, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeDenied, "SKILL_COMPATIBILITY_INVALID"):
            service._read_package("EXPLICIT_IMPORT", package.name)

    def test_skill_host_native_delegates_without_loading_body(self):
        service, package = self._skill_service_and_package()
        skill_id, doc = self._register_skill(service=service, package=package)
        adapter = type("Adapter", (), {"inventory": lambda _self: {
            "adapter_id": "test-host", "inventory_complete": True,
            "skills": [{"name": "tiny-helper", "availability": "AVAILABLE",
            "provenance": "HOST_DECLARED", "revision_sha256": doc["package_manifest_sha256"]}],
        }})()
        request = dict(
                query="tiny helper", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
                classification_assertion_ref="unused-for-native", command_id="native-selection",
                host_inventory=adapter,
        )
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True), \
             mock.patch.object(self.store, "get_payload", side_effect=AssertionError("native resolution loaded body")):
            result = service.resolve(**request)
        self.assertEqual(result["resolution"], "HOST_NATIVE")
        self.assertEqual(result["instruction_load_status"], "NOT_LOADED")
        self.assertEqual(result["model_visible_exposure"], "UNKNOWN")
        self.assertEqual(result["host_inventory_provenance"], "HOST_DECLARED")
        self.assertEqual(result["skill_id"], skill_id)
        for mode in ("OBSERVE", "BYPASS"):
            with mock.patch.object(self.participation, "current", return_value={"mode": mode}), \
                 mock.patch.object(service, "_bound_run", side_effect=AssertionError("replay rechecked Run")), \
                 mock.patch.object(self.authority, "evaluate_authorization", side_effect=AssertionError("replay rechecked grant")), \
                 mock.patch.object(adapter, "inventory", side_effect=AssertionError("replay resampled Host inventory")):
                replay = service.resolve(**request)
                self.assertEqual(replay["selection_id"], result["selection_id"])
                self.assertEqual(replay["resolution"], result["resolution"])
                self.assertEqual(replay, result)
        with self.assertRaises(CommandConflict):
            service.resolve(**{**request, "query": "different query"})
        for key, value in (("task_id", "other-task"), ("run_id", "other-run"),
                           ("grant_id", "other-grant"), ("classification_assertion_ref", "different-class")):
            with self.subTest(identity=key), self.assertRaises(CommandConflict):
                service.resolve(**{**request, key: value})
        self.trace.transition_run(
            command_id="ctx-run-terminal", run_id=self.run_id, expected_state="CREATED",
            next_state="CANCELLED", classification_assertion_ref="ctx-run-terminal-class",
        )
        with mock.patch.object(self.participation, "current", return_value={"mode": "BYPASS"}), \
             mock.patch.object(service, "_bound_run", side_effect=AssertionError("terminal Run was rechecked")), \
             mock.patch.object(self.authority, "evaluate_authorization", side_effect=AssertionError("revoked authority was rechecked")), \
             mock.patch.object(adapter, "inventory", side_effect=AssertionError("Host inventory was resampled")):
            self.assertEqual(service.resolve(**request), result)

    def test_codex_filesystem_inventory_is_bounded_to_roots_and_conservative_about_completeness(self):
        from kernel.skills.host import CodexAgentSkillsInventoryAdapter

        service, package = self._skill_service_and_package(name="native-helper")
        roots = {"REPO": package.parent}
        partial = CodexAgentSkillsInventoryAdapter(
            package_reader=service.inventory_package_metadata, roots=roots,
        ).inventory()
        self.assertFalse(partial["inventory_complete"])
        self.assertEqual(partial["skills"][0]["name"], "native-helper")
        self.assertEqual(partial["skills"][0]["availability"], "AVAILABLE")
        self.assertEqual(partial["skills"][0]["provenance"], "ADAPTER_DISCOVERY")
        self.assertFalse(partial["inventory_complete"])

        with self.assertRaises(TypeError):
            CodexAgentSkillsInventoryAdapter(
                package_reader=service.inventory_package_metadata, roots=roots,
                roots_are_exhaustive=True,
            )

        (package.parent / "malformed").mkdir()
        incomplete_after_bad_package = CodexAgentSkillsInventoryAdapter(
            package_reader=service.inventory_package_metadata, roots=roots,
        ).inventory()
        self.assertFalse(incomplete_after_bad_package["inventory_complete"])
        self.assertEqual([item["name"] for item in incomplete_after_bad_package["skills"]], ["native-helper"])

        from adapters.client.__main__ import _parser
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                _parser().parse_args([
                    "--data-root", str(self.root), "--codex-skill-inventory-roots-exhaustive",
                    "skill", "discover", "helper",
                ])

    def test_partial_codex_inventory_absence_stays_unknown_and_projection_separates_counts(self):
        service, package = self._skill_service_and_package()
        self._register_skill(service=service, package=package, namespace="projection")
        host_root = Path(self.temp.name) / "codex-visible-skills"
        host_root.mkdir()
        application = compose_skill_application(
            store=self.store, authority=self.authority, participation=self.participation,
            source_roots={"EXPLICIT_IMPORT": package.parent}, host_roots={"REPO": host_root},
        )

        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True), \
             mock.patch.object(self.store, "get_payload", side_effect=AssertionError("unknown inventory loaded fallback")):
            result = application.resolve(
                query="tiny helper", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
                classification_assertion_ref="unused", command_id="partial-inventory-absence",
            )
        self.assertEqual(result["resolution"], "UNSUPPORTED")
        self.assertEqual(result["reason"], "HOST_NATIVE_AVAILABILITY_UNKNOWN")
        self.assertEqual(result["instruction_load_status"], "NOT_LOADED")

        projection = application.snapshot()
        self.assertEqual(projection["registered_count"], 1)
        self.assertEqual(projection["host_inventory"], {
            "status": "PARTIAL", "discovered_count": 0, "completeness": "UNKNOWN",
        })
        self.assertNotIn(str(self.temp.name), json.dumps(projection))

    def test_skill_fallback_loads_only_integrity_bound_instruction_and_never_executes_scripts(self):
        marker = Path(self.temp.name) / "must-not-exist"
        service, package = self._skill_service_and_package(resources={
            "scripts/never_run.py": f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        })
        self._register_skill(service=service, package=package)
        # Auxiliary resources are not hydrated by the portable instruction baseline.
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            result = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                     grant_id="ctx-grant", classification_assertion_ref="unused",
                                     command_id="blocked-script-fallback",
                                     host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                                         "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                                     }})())
        self.assertEqual(result["resolution"], "UNSUPPORTED")
        self.assertEqual(result["reason"], "PACKAGE_AUXILIARY_RESOURCES_NOT_HYDRATED")
        self.assertFalse(marker.exists())

    def test_skill_portable_fallback_creates_context_compatible_artifact_and_exact_bytes(self):
        service, package = self._skill_service_and_package()
        skill_id, package_doc = self._register_skill(service=service, package=package)
        instruction = package_doc["files"]["SKILL.md"]
        from kernel.skills.service import _canonical, _sha256
        fallback_ref = "skill-fallback-" + _sha256(_canonical({
            "command_id": "fallback-selection", "task_id": self.task_id, "run_id": self.run_id,
            "skill_id": skill_id, "instruction_sha256": package_doc["skill_md_sha256"],
        }))
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            self.authority.record_classification_assertion(
                self._class("class-" + fallback_ref, "OBJECT", fallback_ref),
                grant_id="ctx-grant", task_id=self.task_id, audience="nexus-runtime",
                command_id="classify-" + fallback_ref,
            )
            result = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                     grant_id="ctx-grant", classification_assertion_ref="class-" + fallback_ref,
                                     command_id="fallback-selection",
                                     host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                                         "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                                     }})())
        self.assertEqual(result["resolution"], "NEXUS_FALLBACK")
        self.assertEqual(result["instruction_object_ref"], fallback_ref)
        self.assertEqual(result["instruction_byte_size"], len(instruction))
        fallback_document = json.loads(self.store.get_payload(fallback_ref))
        self.assertNotIn("loaded_at", fallback_document)
        self.assertEqual(fallback_document["instruction_utf8"].encode("utf-8"), instruction)
        self.assertEqual(fallback_document["instruction_sha256"], package_doc["skill_md_sha256"])
        self.assertEqual(self.store.get_lineage(fallback_ref)["sources"], ["skill-src-" + _sha256(skill_id.encode())])
        self.assertEqual(result["delivery_status"], "UNKNOWN")
        self.assertEqual(result["model_visible_exposure"], "UNKNOWN")
        with self.store._connection() as conn:
            before = (conn.execute("SELECT COUNT(*) FROM skill_resolution_records").fetchone()[0],
                      conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (fallback_ref,)).fetchone()[0])
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            replay = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                     grant_id="ctx-grant", classification_assertion_ref="class-" + fallback_ref,
                                     command_id="fallback-selection",
                                     host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                                         "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                                     }})())
            with self.assertRaises(CommandConflict):
                service.resolve(query="different query", task_id=self.task_id, run_id=self.run_id,
                                grant_id="ctx-grant", classification_assertion_ref="class-" + fallback_ref,
                                command_id="fallback-selection")
        self.assertEqual(replay["selection_id"], result["selection_id"])
        with self.store._connection() as conn:
            after = (conn.execute("SELECT COUNT(*) FROM skill_resolution_records").fetchone()[0],
                     conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (fallback_ref,)).fetchone()[0])
        self.assertEqual(after, before)

    def test_skill_fallback_artifact_commit_crash_retries_exactly(self):
        service, package = self._skill_service_and_package()
        skill_id, package_doc = self._register_skill(service=service, package=package, namespace="crash-retry")
        from kernel.skills.service import _canonical, _sha256
        command_id = "fallback-crash-retry"
        fallback_ref = "skill-fallback-" + _sha256(_canonical({
            "command_id": command_id, "task_id": self.task_id, "run_id": self.run_id,
            "skill_id": skill_id, "instruction_sha256": package_doc["skill_md_sha256"],
        }))
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            self.authority.record_classification_assertion(
                self._class("class-" + fallback_ref, "OBJECT", fallback_ref),
                grant_id="ctx-grant", task_id=self.task_id, audience="nexus-runtime",
                command_id="classify-" + fallback_ref,
            )
        inventory = type("Adapter", (), {"inventory": lambda _self: {
            "adapter_id": "test-host", "inventory_complete": True, "skills": [],
        }})()
        request = dict(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                       grant_id="ctx-grant", classification_assertion_ref="class-" + fallback_ref,
                       command_id=command_id, host_inventory=inventory)
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True), \
             mock.patch.object(service, "_record_resolution", side_effect=RuntimeError("simulated process loss")):
            with self.assertRaisesRegex(RuntimeError, "simulated process loss"):
                service.resolve(**request)
        committed_hash = hashlib.sha256(self.store.get_payload(fallback_ref)).hexdigest()
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            result = service.resolve(**request)
        self.assertEqual(result["resolution"], "NEXUS_FALLBACK")
        self.assertEqual(hashlib.sha256(self.store.get_payload(fallback_ref)).hexdigest(), committed_hash)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects WHERE object_id=?", (fallback_ref,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM skill_resolution_records WHERE command_id=?", (command_id,)).fetchone()[0], 1)

    def test_skill_purge_redacts_instruction_snapshot_and_fallback_references(self):
        from kernel.purge import PurgeService
        from kernel.skills.service import _canonical, _sha256
        service, package = self._skill_service_and_package()
        skill_id, package_doc = self._register_skill(service=service, package=package, namespace="purge-source")
        with self.store._connection() as conn:
            source_ref = conn.execute("SELECT instruction_object_ref FROM skill_registry_entries WHERE skill_id=?", (skill_id,)).fetchone()[0]
        purge = PurgeService(self.store, self.authority, self.memory,
                             independent_journal_path=self.store.independent_purge_journal_path)
        purge._purge_payloads_and_indexes([source_ref])
        projection = service.get(skill_id)
        self.assertEqual(projection["status"], "STALE")
        self.assertEqual(projection["stale_reason"], "INSTRUCTION_SNAPSHOT_PURGED")
        self.assertEqual(service.discover("tiny helper")["status"], "NO_MATCH")
        self.assertNotIn("Instruction body", json.dumps(service.snapshot()))
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            no_source = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                        grant_id="ctx-grant", classification_assertion_ref="unused",
                                        command_id="after-source-purge")
        self.assertEqual(no_source["result_status"], "NO_MATCH")
        self.assertEqual(self.store.get_object_metadata(source_ref)["payload_state"], "PURGED")

        second_service, second_package = self._skill_service_and_package(name="purge-helper")
        second_skill, second_doc = self._register_skill(service=second_service, package=second_package,
                                                        namespace="purge-fallback")
        fallback_ref = "skill-fallback-" + _sha256(_canonical({
            "command_id": "purge-fallback-selection", "task_id": self.task_id, "run_id": self.run_id,
            "skill_id": second_skill, "instruction_sha256": second_doc["skill_md_sha256"],
        }))
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            self.authority.record_classification_assertion(
                self._class("class-" + fallback_ref, "OBJECT", fallback_ref),
                grant_id="ctx-grant", task_id=self.task_id, audience="nexus-runtime",
                command_id="classify-" + fallback_ref,
            )
            loaded = second_service.resolve(
                query="purge helper", task_id=self.task_id, run_id=self.run_id, grant_id="ctx-grant",
                classification_assertion_ref="class-" + fallback_ref,
                command_id="purge-fallback-selection",
                host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                    "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                }})(),
            )
        self.assertEqual(loaded["resolution"], "NEXUS_FALLBACK")
        purge._purge_payloads_and_indexes([fallback_ref])
        latest = second_service.latest_resolution()
        self.assertIsNone(latest["instruction_object_ref"])
        self.assertEqual(latest["instruction_load_status"], "LOADED_TO_GOVERNED_ARTIFACT")
        self.assertEqual(latest["instruction_payload_availability"], "UNAVAILABLE")
        self.assertNotIn("Instruction body", json.dumps(second_service.snapshot()))
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_skill_stale_revision_and_participation_modes_fail_closed(self):
        service, package = self._skill_service_and_package()
        skill_id, _doc = self._register_skill(service=service, package=package)
        (package / "SKILL.md").write_text("---\nname: tiny-helper\ndescription: revised helper\n---\nnew revision\n", encoding="utf-8")
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            stale = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                    grant_id="ctx-grant", classification_assertion_ref="unused",
                                    command_id="stale-selection",
                                    host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                                        "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                                    }})())
        self.assertEqual(stale["result_status"], "UNSUPPORTED")
        self.assertEqual(service.get(skill_id)["status"], "STALE")
        for mode in ("OBSERVE", "BYPASS"):
            with mock.patch.object(self.participation, "current", return_value={"mode": mode}):
                result = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                         grant_id="ctx-grant", classification_assertion_ref="unused",
                                         command_id="inactive-" + mode)
            self.assertEqual(result["result_status"], "INACTIVE_MODE")
            self.assertEqual(result["instruction_load_status"], "NOT_LOADED")

    def test_skill_unknown_host_inventory_never_assumes_fallback_is_safe(self):
        service, package = self._skill_service_and_package()
        self._register_skill(service=service, package=package)
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True), \
             mock.patch.object(self.store, "get_payload", side_effect=AssertionError("unknown Host state loaded fallback")):
            result = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                     grant_id="ctx-grant", classification_assertion_ref="unused",
                                     command_id="unknown-host-selection")
        self.assertEqual(result["resolution"], "UNSUPPORTED")
        self.assertEqual(result["reason"], "HOST_NATIVE_AVAILABILITY_UNKNOWN")
        self.assertEqual(result["instruction_load_status"], "NOT_LOADED")

    def test_skill_same_name_packages_remain_distinct_and_tied_query_is_ambiguous(self):
        service, package = self._skill_service_and_package()
        first, _ = self._register_skill(service=service, package=package, namespace="source-one")
        second, _ = self._register_skill(service=service, package=package, namespace="source-two")
        self.assertNotEqual(first, second)
        discovered = service.discover("tiny helper")
        self.assertEqual(discovered["candidate_count"], 2)
        self.assertEqual({item["skill_id"] for item in discovered["candidates"]}, {first, second})
        with mock.patch.object(self.authority, "evaluate_authorization", return_value=True):
            result = service.resolve(query="tiny helper", task_id=self.task_id, run_id=self.run_id,
                                     grant_id="ctx-grant", classification_assertion_ref="unused",
                                     command_id="ambiguous-skill-selection",
                                     host_inventory=type("Adapter", (), {"inventory": lambda _self: {
                                         "adapter_id": "test-host", "inventory_complete": True, "skills": [],
                                     }})())
        self.assertEqual(result["result_status"], "AMBIGUOUS")
        self.assertIsNone(result["skill_id"])
        self.assertEqual(service.discover("term-not-present")["status"], "NO_MATCH")

    def test_skill_frontmatter_path_traversal_symlink_and_academy_scope_rejected(self):
        service, package = self._skill_service_and_package()
        (package / "SKILL.md").write_text("---\nname: first\nname: second\ndescription: duplicate\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeDenied, "SKILL_FRONTMATTER_DUPLICATE_KEY"):
            service._read_package("EXPLICIT_IMPORT", package.name)
        (package / "SKILL.md").write_text(
            "---\nname: tiny-helper\ndescription: good\nlicense: MIT\n"
            "compatibility: Python 3.12\nmetadata:\n  author: test\nallowed-tools: Read Search\n---\n",
            encoding="utf-8")
        asset = package / "assets" / "icon.png"
        asset.parent.mkdir()
        asset.write_bytes(b"\x89PNG\x00synthetic binary asset")
        package_doc = service._read_package("EXPLICIT_IMPORT", package.name)
        self.assertTrue(any(item["path"] == "SKILL.md" for item in package_doc["manifest"]))
        self.assertTrue(any(item["path"] == "assets/icon.png" for item in package_doc["manifest"]))
        (package / "SKILL.md").write_text(
            "---\nname: tiny-helper\ndescription: good\ncompatibility: " + ("x" * 501) + "\n---\n",
            encoding="utf-8")
        with self.assertRaisesRegex(RuntimeDenied, "SKILL_COMPATIBILITY_INVALID"):
            service._read_package("EXPLICIT_IMPORT", package.name)
        for bad_ref in ("../sample", f"{chr(67)}:/outside/package", ""):
            with self.assertRaises(RuntimeDenied):
                service._read_package("EXPLICIT_IMPORT", bad_ref)
        with self.assertRaises(ValueError):
            SkillRegistryService(store=self.store, authority=self.authority,
                                 participation=self.participation, source_roots={"ACADEMY": package.parent})
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("synthetic external target", encoding="utf-8")
        escape = package / "escape.txt"
        original_resolve = Path.resolve
        original_is_symlink = Path.is_symlink

        def resolve_with_escape(path, *args, **kwargs):
            return outside.resolve() if path == escape else original_resolve(path, *args, **kwargs)

        def mark_escape_symlink(path):
            return True if path == escape else original_is_symlink(path)

        entries = [
            SimpleNamespace(name="SKILL.md", path=str(package / "SKILL.md"), is_symlink=lambda: False,
                            is_dir=lambda **_kwargs: False),
            SimpleNamespace(name="escape.txt", path=str(escape), is_symlink=lambda: True,
                            is_dir=lambda **_kwargs: False),
        ]
        with mock.patch("kernel.skills.service.os.scandir", return_value=nullcontext(entries)), \
             mock.patch.object(Path, "resolve", resolve_with_escape), \
             mock.patch.object(Path, "is_symlink", mark_escape_symlink):
            with self.assertRaisesRegex(RuntimeDenied, "SKILL_SYMLINK_ESCAPES_PACKAGE_ROOT"):
                service._read_package("EXPLICIT_IMPORT", package.name)

    def test_skill_real_cli_composition_register_approve_resolve_panel_and_reopen(self):
        from adapters.client.__main__ import main as client_main
        from kernel.skills.service import _canonical, _sha256

        repo_root = Path(self.temp.name) / "repo-skills"
        codex_repo_root = Path(self.temp.name) / "codex-repo-skills"
        user_root = Path(self.temp.name) / "user-skills"
        package = user_root / "tiny-helper"
        repo_root.mkdir()
        codex_repo_root.mkdir()
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\nname: tiny-helper\ndescription: A local deterministic helper.\n---\nUse the small helper instruction.\n",
            encoding="utf-8",
        )
        roots = {"REPO": repo_root, "USER": user_root}
        application = compose_skill_application(
            store=self.store, authority=self.authority, participation=self.participation,
            source_roots=roots,
        )
        package_doc = application.registry.inventory_package_metadata("USER", "tiny-helper")
        identity = {
            "name": package_doc["name"], "source_scope": "USER", "source_namespace": "local",
            "source_ref": "tiny-helper", "package_manifest_sha256": package_doc["package_manifest_sha256"],
        }
        skill_id = "skill-" + _sha256(_canonical(identity))
        instruction_object_ref = "skill-src-" + _sha256(skill_id.encode("utf-8"))

        task_id, run_id, grant_id = "skill-product-task", "skill-product-run", "skill-product-grant"
        run_event_ref = "evt-create-skill-product-run"
        terminal_event_ref = "evt-cancel-skill-product-run"
        now = datetime.now(timezone.utc)
        resources = ["task:" + task_id, run_id, run_event_ref, terminal_event_ref, skill_id,
                     instruction_object_ref, "object:" + instruction_object_ref]
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": grant_id, "issued_by": "human-root", "granted_to": "context-agent",
            "task_scope": [task_id], "resource_scope": resources,
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "OBJECT_WRITE", "CLASSIFY", "TRACE_APPEND"],
            "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "skill-product-grant-create")
        self.trace.create_task({
            "schema_id": "nexus.task", "schema_version": 1, "task_id": task_id,
            "requester_id": "human-root", "status": "CREATED", "created_at": now.isoformat(),
            "command_id": "skill-product-task-create",
        })
        assertions = [
            self._class("skill-product-source-class", "OBJECT", instruction_object_ref),
            self._class("skill-product-run-class", "RUN", run_id),
            self._class("skill-product-event-class", "TRACE_EVENT", run_event_ref),
            self._class("skill-product-terminal-event-class", "TRACE_EVENT", terminal_event_ref),
        ]
        for assertion in assertions:
            self.authority.record_classification_assertion(
                assertion, grant_id=grant_id, task_id=task_id, audience="nexus-runtime",
                command_id="record-" + assertion["assertion_id"],
            )
        self.trace.create_run({
            "schema_id": "nexus.run", "schema_version": 1, "run_id": run_id,
            "task_id": task_id, "executor_kind": "ORCHESTRATOR", "status": "CREATED",
            "grant_id": grant_id,
            "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
            "classification_assertion_ref": "skill-product-run-class", "created_at": now.isoformat(),
        }, command_id="create-skill-product-run", event_classification_assertion_ref="skill-product-event-class")

        policy_path = Path(self.temp.name) / "operator-policy.json"
        policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        common = ["--data-root", str(self.root), "--policy", str(policy_path),
                  "--repo-skill-root", str(repo_root), "--codex-repo-skill-root", str(codex_repo_root),
                  "--user-skill-root", str(user_root)]

        class CliOutput(io.StringIO):
            def __init__(self, interactive=False):
                super().__init__()
                self.interactive = interactive

            def isatty(self):
                return self.interactive

        def invoke(parts, *, stdin=None):
            output, errors = CliOutput(interactive=stdin is not None), io.StringIO()
            input_stream = stdin if stdin is not None else io.StringIO()
            with mock.patch("sys.stdin", input_stream), mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
                code = client_main([*common, *parts])
            return code, output.getvalue(), errors.getvalue()

        self.store.close()
        code, register_output, _errors = invoke([
            "skill", "register", "--source-scope", "USER", "--source-namespace", "local",
            "--source-ref", "tiny-helper", "--task-id", task_id, "--run-id", run_id,
            "--grant-id", grant_id, "--classification-assertion-ref", "skill-product-source-class",
            "--command-id", "skill-product-register",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(register_output)["skill_id"], skill_id)

        enable_args = ["skill", "enable", skill_id, "--task-id", task_id, "--run-id", run_id,
                       "--grant-id", grant_id, "--command-id", "skill-product-enable"]
        code, _output, errors = invoke(enable_args)
        self.assertEqual(code, 2)
        self.assertIn("SKILL_OPERATOR_CONFIRMATION_REQUIRES_TTY", errors)
        approval_id = "skill-approval-" + hashlib.sha256(b"skill-product-enable").hexdigest()
        probe = open_test_store(self.root, policy=self.policy)
        try:
            self.assertIsNone(AuthorityService(probe, self.policy).get_approval_metadata(approval_id))
        finally:
            probe.close()

        class TestTtyInput(io.StringIO):
            def isatty(self):
                return True

        code, enable_output, _errors = invoke(enable_args, stdin=TestTtyInput("ENABLE\n"))
        self.assertEqual(code, 0, enable_output)
        enable_result = json.loads(enable_output[enable_output.index("{"):])
        self.assertEqual(enable_result["status"], "ENABLED")
        self.assertIn("tiny-helper", enable_output)
        self.assertIn(task_id, enable_output)

        code, resolve_output, errors = invoke([
            "skill", "resolve", "tiny helper", "--task-id", task_id, "--run-id", run_id,
            "--grant-id", grant_id, "--classification-assertion-ref", "unused-native-classification",
            "--command-id", "skill-product-resolve",
        ])
        self.assertEqual(code, 0, errors)
        resolution = json.loads(resolve_output)
        self.assertEqual(resolution["resolution"], "HOST_NATIVE")
        self.assertEqual(resolution["host_inventory_provenance"], "ADAPTER_DISCOVERY")
        self.assertEqual(resolution["instruction_load_status"], "NOT_LOADED")

        from adapters.panel import application as panel_application_module
        panel_runtime = {}
        open_panel = panel_application_module.open_panel_application

        def exercise_production_panel(view_model):
            panel_app = panel_runtime["panel"]
            self.assertFalse(panel_app._owns_store)
            self.assertIs(panel_app.store, panel_runtime["store"])
            self.assertIs(view_model._skills, panel_runtime["skills"])
            panel_projection = view_model.snapshot()["skill_status"]
            self.assertEqual(panel_projection["eligible_count"], 1)
            self.assertEqual(panel_projection["registered_count"], 1)
            self.assertEqual(panel_projection["host_inventory"], {
                "status": "PARTIAL", "discovered_count": 1, "completeness": "UNKNOWN",
            })
            self.assertEqual(panel_projection["latest_selection"]["resolution"], "HOST_NATIVE")
            self.assertNotIn("Use the small helper instruction", json.dumps(panel_projection))
            with self.assertRaises(WriterAlreadyRunning):
                open_test_store(self.root, policy=self.policy)

        def capture_production_panel(*args, **kwargs):
            panel_app = open_panel(*args, **kwargs)
            writer_services = kwargs["writer_services"]
            panel_runtime["store"] = writer_services["store"]
            panel_runtime["skills"] = writer_services["skills"]
            panel_runtime["panel"] = panel_app
            original_close = panel_app.close

            def close_panel_only():
                original_close()
                self.assertEqual(writer_services["skills"].snapshot()["eligible_count"], 1)
                with self.assertRaises(WriterAlreadyRunning):
                    open_test_store(self.root, policy=self.policy)

            panel_app.close = close_panel_only
            return panel_app

        with mock.patch.object(panel_application_module, "open_panel_application", side_effect=capture_production_panel), \
             mock.patch("adapters.panel.ui.launch_panel", side_effect=exercise_production_panel):
            code, _panel_output, panel_errors = invoke(["panel"])
        self.assertEqual(code, 0, panel_errors)

        code, disable_output, errors = invoke([
            "skill", "disable", skill_id, "--task-id", task_id, "--run-id", run_id,
            "--grant-id", grant_id, "--command-id", "skill-product-disable",
        ])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(disable_output)["status"], "DISABLED")
        code, reject_output, errors = invoke([
            "skill", "reject", skill_id, "--task-id", task_id, "--run-id", run_id,
            "--grant-id", grant_id, "--command-id", "skill-product-reject",
        ])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(reject_output)["status"], "REJECTED")
        transition_store = open_test_store(self.root, policy=self.policy)
        try:
            transition_trace = TraceRuntime(transition_store, AuthorityService(transition_store, self.policy))
            transition_trace.transition_run(
                command_id="cancel-skill-product-run", run_id=run_id, expected_state="CREATED",
                next_state="CANCELLED", classification_assertion_ref="skill-product-terminal-event-class",
            )
        finally:
            transition_store.close()
        code, replay_output, errors = invoke(enable_args)
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(replay_output)["status"], "ENABLED")
        self.assertNotIn("Type ENABLE to confirm", replay_output)

        reopened = open_test_store(self.root, policy=self.policy)
        try:
            reopened_app = compose_skill_application(
                store=reopened, authority=AuthorityService(reopened, self.policy),
                participation=ParticipationModeService(reopened), source_roots=roots,
            )
            persisted = reopened_app.snapshot()
            self.assertEqual(persisted["eligible_count"], 0)
            self.assertEqual(persisted["registered"][0]["status"], "REJECTED")
            self.assertEqual(persisted["latest_selection"]["resolution"], "HOST_NATIVE")
            self.assertNotIn(str(user_root.resolve()), json.dumps(persisted))
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
