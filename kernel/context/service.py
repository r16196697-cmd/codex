"""Host-neutral, governed production Context Pack compilation and inspection."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from kernel.metering.service import MeteringService
from kernel.participation import NexusParticipationMode
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.inspect import InspectService


_SOURCE_ORDER = {
    "user_input": 0, "task_contract": 1, "evidence": 2, "claim": 3,
    "artifact": 4, "verification": 5, "admitted_memory": 6,
}
_ALLOWED_OBJECT_TYPES = frozenset({"user_input", "task_contract", "evidence", "claim", "artifact", "verification"})
_MAX_SOURCES = 50
_MAX_PACK_BYTES = 262144


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class ContextPackService:
    def __init__(self, *, store, authority, participation, memory, metering: MeteringService | None = None):
        self.store = store
        self.authority = authority
        self.participation = participation
        self.memory = memory
        self.metering = metering or MeteringService(store, authority, participation)
        self.inspect = InspectService(store, authority)

    def compile(
        self, *, task_id: str, run_id: str, grant_id: str, pack_object_id: str,
        classification_assertion_ref: str, command_id: str,
        source_refs: list[str] | tuple[str, ...] = (), memory_query: str | None = None,
        memory_limit: int = 20,
    ) -> dict:
        """Compile exactly the explicit refs plus matching admitted Memory.

        Raw history, Host ambient memory, Skill/Academy artifacts, and opaque
        Host state are never queried. Oversize sources fail without truncation.
        """
        if self.participation.current()["mode"] != NexusParticipationMode.ACTIVE.value:
            raise RuntimeDenied("CONTEXT_PACK_REQUIRES_ACTIVE_PARTICIPATION")
        self.store._require_mode("core_write")
        if not command_id or not pack_object_id or not classification_assertion_ref:
            raise RuntimeDenied("CONTEXT_PACK_IDENTITY_INVALID")
        refs = list(source_refs)
        if len(refs) > _MAX_SOURCES or any(not isinstance(ref, str) or not ref for ref in refs):
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_LIST_INVALID")
        if len(set(refs)) != len(refs):
            raise RuntimeDenied("CONTEXT_PACK_DUPLICATE_SOURCE_REF")
        if memory_query is not None and (not memory_query.strip() or not 1 <= memory_limit <= _MAX_SOURCES):
            raise RuntimeDenied("CONTEXT_PACK_MEMORY_QUERY_INVALID")

        with self.store._lock:
            run = self._task_run(task_id, run_id)
            self.authority.evaluate_authorization(
                grant_id,
                {"task": task_id, "resource": pack_object_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
                command_id + "-authorize-pack",
            )
            entries = [self._read_explicit_source(ref, task_id, grant_id) for ref in refs]
            memory_query_sha256 = None
            if memory_query is not None:
                memory_query_sha256 = hashlib.sha256(memory_query.encode("utf-8")).hexdigest()
                matches = self.memory.search_admitted(query=memory_query, run_id=run_id, limit=memory_limit)
                for match in matches:
                    ref = match["object_id"]
                    if ref in refs:
                        raise RuntimeDenied("CONTEXT_PACK_DUPLICATE_MEMORY_SOURCE")
                    entry = self._read_explicit_source(ref, task_id, grant_id, expected_type="memory")
                    entry["source_type"] = "admitted_memory"
                    entries.append(entry)
            entries.sort(key=lambda item: (_SOURCE_ORDER[item["source_type"]], item["source_ref"].encode("utf-8")))
            if len(entries) > _MAX_SOURCES:
                raise RuntimeDenied("CONTEXT_PACK_SOURCE_LIMIT_EXCEEDED")
            source_counts = dict(sorted(Counter(item["source_type"] for item in entries).items()))
            basis = {
                "policy_id": "NEXUS_CONTEXT_SELECTION_V1",
                "selection_rule": "EXPLICIT_CANONICAL_REFS_PLUS_ADMITTED_MEMORY_QUERY",
                "ordering_rule": "SOURCE_TYPE_FIXED_ORDER_THEN_SOURCE_REF_UTF8_BYTE_ORDER",
                "source_ref_count": len(entries),
                "memory_query_sha256": memory_query_sha256,
                "memory_limit": memory_limit if memory_query is not None else None,
                "ambient_host_memory_read": False,
                "academy_sources_read": False,
            }
            content_basis = {"task_id": task_id, "run_id": run_id, "selection_basis": basis, "entries": entries}
            content_hash = hashlib.sha256(_canonical(content_basis)).hexdigest()
            document = {
                "schema_id": "nexus.context_pack", "schema_version": 1,
                "task_id": task_id, "run_id": run_id,
                "selection_basis": basis, "entries": entries,
                "content_hash": content_hash, "model_visible_exposure": "UNKNOWN",
            }
            self.store._validate("nexus.context_pack@1.schema.json", document)
            payload = _canonical(document)
            if len(payload) > _MAX_PACK_BYTES:
                raise RuntimeDenied("CONTEXT_PACK_SIZE_LIMIT_EXCEEDED")
            integrity_hash = hashlib.sha256(payload).hexdigest()
            self.store.put_object(
                command_id=command_id + "-object", object_id=pack_object_id, payload=payload,
                object_type="artifact", created_by_run=run_id,
                classification_assertion_ref=classification_assertion_ref,
                derived_from=[item["source_ref"] for item in entries],
            )
            metadata = self.store.get_object_metadata(pack_object_id)
            if metadata.get("integrity_hash") != integrity_hash:
                raise RuntimeDenied("CONTEXT_PACK_PERSISTED_HASH_MISMATCH")
            record_id = "ctx-" + hashlib.sha256(pack_object_id.encode("utf-8")).hexdigest()
            meter_id = "meter-ctx-" + hashlib.sha256(pack_object_id.encode("utf-8")).hexdigest()
            with self.store._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    prior = conn.execute("SELECT * FROM context_pack_records WHERE pack_ref=?", (pack_object_id,)).fetchone()
                    expected = (task_id, run_id, content_hash, len(payload),
                                _canonical(basis).decode("utf-8"), _canonical(source_counts).decode("utf-8"), metadata["created_at"])
                    if prior:
                        actual = tuple(prior[key] for key in ("task_id", "run_id", "content_hash", "serialized_byte_size", "selection_basis_json", "source_counts_json", "compiled_at"))
                        if actual != expected or prior["state"] != "COMPILED":
                            raise RuntimeDenied("CONTEXT_PACK_RECORD_CONFLICT")
                    else:
                        conn.execute(
                            "INSERT INTO context_pack_records(record_id,pack_ref,task_id,run_id,content_hash,"
                            "serialized_byte_size,selection_basis_json,source_counts_json,state,compiled_at) "
                            "VALUES(?,?,?,?,?,?,?,?, 'COMPILED',?)",
                            (record_id, pack_object_id, task_id, run_id, content_hash, len(payload),
                             _canonical(basis).decode("utf-8"), _canonical(source_counts).decode("utf-8"), metadata["created_at"]),
                        )
                    self.metering._insert_context_compile(
                        conn, record_id=meter_id, task_id=task_id, run_id=run_id,
                        pack_ref=pack_object_id, byte_size=len(payload), recorded_at=metadata["created_at"],
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            return self.pack_status(pack_object_id)

    def _task_run(self, task_id: str, run_id: str):
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT r.task_id,r.status,r.grant_id,r.data_boundary_json,t.status AS task_status "
                "FROM runs r JOIN tasks t ON t.task_id=r.task_id WHERE r.run_id=?",
                (run_id,),
            ).fetchone()
        if not row or row["task_id"] != task_id:
            raise RuntimeDenied("CONTEXT_PACK_RUN_TASK_MISMATCH")
        if row["status"] in {"CANCELLED", "FAILED", "SUCCEEDED"}:
            raise RuntimeDenied("CONTEXT_PACK_RUN_NOT_ELIGIBLE")
        return dict(row)

    def _read_explicit_source(self, object_ref: str, task_id: str, grant_id: str,
                              expected_type: str | None = None) -> dict:
        visible = self.inspect.object_metadata(
            grant_id=grant_id, task_id=task_id, object_id=object_ref,
        )
        object_type = visible["object_type"]
        allowed = object_type in _ALLOWED_OBJECT_TYPES if expected_type is None else object_type == expected_type
        if not allowed:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_TYPE_INELIGIBLE")
        if (visible["lifecycle"], visible["validity"], visible["payload_state"]) != ("ACTIVE", "VALID", "AVAILABLE"):
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_UNAVAILABLE")
        metadata = self.store.get_object_metadata(object_ref)
        if metadata.get("payload_state") != "AVAILABLE" or not metadata.get("classification_assertion_ref"):
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_UNAVAILABLE")
        payload = self.store.get_payload(object_ref)
        try:
            content = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_NOT_UTF8") from exc
        try:
            payload_doc = json.loads(content)
        except json.JSONDecodeError:
            payload_doc = None
        if isinstance(payload_doc, dict) and payload_doc.get("schema_id") == "nexus.context_pack":
            raise RuntimeDenied("CONTEXT_PACK_NESTING_NOT_ALLOWED")
        with self.store._connection() as conn:
            classification = conn.execute(
                "SELECT sensitivity_level FROM classification_assertions WHERE assertion_id=?",
                (metadata["classification_assertion_ref"],),
            ).fetchone()
        if not classification:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_CLASSIFICATION_UNAVAILABLE")
        source_type = "admitted_memory" if object_type == "memory" else object_type
        return {
            "source_ref": object_ref, "source_type": source_type,
            "classification_assertion_ref": metadata["classification_assertion_ref"],
            "source_integrity_sha256": metadata["integrity_hash"], "content": content,
        }

    def pack_status(self, pack_ref: str) -> dict:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM context_pack_records WHERE pack_ref=?", (pack_ref,)).fetchone()
        if not row or row["state"] != "COMPILED":
            raise RuntimeDenied("CONTEXT_PACK_NOT_AVAILABLE")
        refs = self._selected_refs(pack_ref)
        delivery = self._delivery_status(pack_ref)
        return {
            "status": "PACK_COMPILED", "pack_id": pack_ref,
            "task_id": row["task_id"], "run_id": row["run_id"],
            "selected_source_counts": json.loads(row["source_counts_json"]),
            "selected_refs": refs,
            "selection_basis": json.loads(row["selection_basis_json"]),
            "serialized_byte_size": row["serialized_byte_size"],
            "content_hash": row["content_hash"],
            "integrity_hash": self.store.get_object_metadata(pack_ref)["integrity_hash"],
            "compiled_at": row["compiled_at"],
            "host_delivery_status": delivery,
            "host_delivery_known": delivery == "HOST_DECLARED_DELIVERY",
            "model_visible_exposure": "UNKNOWN",
            "model_visible_exposure_proven": False,
        }

    def latest(self) -> dict:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT pack_ref FROM context_pack_records WHERE state='COMPILED' "
                "ORDER BY compiled_at DESC,record_id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return {"status": "NOT COMPILED", "host_delivery_status": "UNAVAILABLE",
                    "model_visible_exposure": "UNKNOWN", "selected_source_counts": {}, "selected_refs": []}
        return self.pack_status(row["pack_ref"])

    def is_context_pack(self, pack_ref: str, *, task_id: str | None = None) -> bool:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT state FROM context_pack_records WHERE pack_ref=?" + (" AND task_id=?" if task_id else ""),
                (pack_ref, task_id) if task_id else (pack_ref,),
            ).fetchone()
        return bool(row and row["state"] == "COMPILED")

    def _selected_refs(self, pack_ref: str) -> list[str]:
        try:
            document = json.loads(self.store.get_payload(pack_ref).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeDenied("CONTEXT_PACK_PAYLOAD_INVALID") from exc
        if (not isinstance(document, dict) or document.get("schema_id") != "nexus.context_pack"
                or not isinstance(document.get("entries"), list)
                or any(not isinstance(entry, dict) or not isinstance(entry.get("source_ref"), str)
                       for entry in document["entries"])):
            raise RuntimeDenied("CONTEXT_PACK_PAYLOAD_INVALID")
        return [entry["source_ref"] for entry in document["entries"]]

    def _delivery_status(self, pack_ref: str) -> str:
        with self.store._connection() as conn:
            pack = conn.execute("SELECT task_id FROM context_pack_records WHERE pack_ref=? AND state='COMPILED'", (pack_ref,)).fetchone()
            rows = conn.execute(
                "SELECT i.manifest_object_id,e.created_by_run,s.payload_state,r.task_id "
                "FROM run_manifest_inputs i JOIN object_envelopes e ON e.object_id=i.manifest_object_id "
                "JOIN object_states s ON s.object_id=e.object_id "
                "JOIN runs r ON r.run_id=e.created_by_run "
                "WHERE i.input_object_id=? AND e.object_type='run_manifest' ORDER BY e.created_at DESC,e.object_id",
                (pack_ref,),
            ).fetchall()
        for row in rows:
            if not pack or row["task_id"] != pack["task_id"] or row["payload_state"] != "AVAILABLE":
                continue
            try:
                manifest = json.loads(self.store.get_payload(row["manifest_object_id"]).decode("utf-8"))
            except Exception:
                continue
            if pack_ref in manifest.get("context_object_refs", []):
                if manifest.get("execution_source") == "CODEX_HOST_DECLARED":
                    return "HOST_DECLARED_DELIVERY"
                return "DELIVERY_DECLARED_IN_RUN_MANIFEST"
        return "NOT_DECLARED"
