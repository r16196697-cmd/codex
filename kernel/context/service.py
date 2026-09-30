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
_ALLOWED_PACK_SOURCE_TYPES = _ALLOWED_OBJECT_TYPES | {"admitted_memory"}
_MAX_SOURCES = 50
_MAX_PACK_BYTES = 262144


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


class ContextPackService:
    def __init__(self, *, store, authority, participation, memory, metering: MeteringService | None = None):
        self.store = store
        self.authority = authority
        self.participation = participation
        self.memory = memory
        self.metering = metering or MeteringService(store, authority, participation)
        self.inspect = InspectService(store, authority)

    def read_compiled(self, pack_ref: str) -> dict:
        """Return one already compiled Context Pack after revalidating its read boundary.

        This is intentionally the only Context Pack payload read surface. It does
        not authorize execution, access arbitrary object payloads, or record
        delivery/model visibility.
        """
        self.store._require_mode("core_read")
        if not isinstance(pack_ref, str) or not pack_ref:
            raise RuntimeDenied("CONTEXT_PACK_NOT_AVAILABLE")
        with self.store._lock:
            with self.store._connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM context_pack_records WHERE pack_ref=?", (pack_ref,)
                ).fetchall()
                if len(rows) != 1 or rows[0]["state"] != "COMPILED":
                    raise RuntimeDenied("CONTEXT_PACK_NOT_AVAILABLE")
                record = dict(rows[0])
                run_rows = conn.execute(
                    "SELECT r.run_id,r.task_id,r.data_boundary_json,r.classification_assertion_ref "
                    "FROM runs r JOIN tasks t ON t.task_id=r.task_id "
                    "WHERE r.run_id=? AND r.task_id=?",
                    (record["run_id"], record["task_id"]),
                ).fetchall()
            if len(run_rows) != 1:
                raise RuntimeDenied("CONTEXT_PACK_RUN_TASK_MISMATCH")
            run = dict(run_rows[0])
            boundary = self._read_boundary(run["data_boundary_json"])

            pack_metadata = self._current_object_metadata(pack_ref, "CONTEXT_PACK_NOT_AVAILABLE")
            if (pack_metadata.get("object_type") != "artifact"
                    or pack_metadata.get("schema_id") != "nexus.object"
                    or pack_metadata.get("schema_version") != 1
                    or pack_metadata.get("created_by_run") != record["run_id"]
                    or not isinstance(pack_metadata.get("classification_assertion_ref"), str)
                    or (pack_metadata.get("payload_state"), pack_metadata.get("lifecycle"),
                        pack_metadata.get("validity")) != ("AVAILABLE", "ACTIVE", "VALID")):
                raise RuntimeDenied("CONTEXT_PACK_NOT_AVAILABLE")
            self._validate_current_classification(
                pack_metadata["classification_assertion_ref"], pack_ref, boundary,
                "CONTEXT_PACK_CLASSIFICATION_INVALID",
            )

            try:
                payload = self.store.get_payload(pack_ref)
            except Exception as exc:
                raise RuntimeDenied("CONTEXT_PACK_INTEGRITY_INVALID") from exc
            integrity_hash = hashlib.sha256(payload).hexdigest()
            if integrity_hash != pack_metadata.get("integrity_hash"):
                raise RuntimeDenied("CONTEXT_PACK_INTEGRITY_INVALID")
            try:
                document = json.loads(payload.decode("utf-8", errors="strict"), object_pairs_hook=_strict_json_object)
                self.store._validate("nexus.context_pack@1.schema.json", document)
                canonical_payload = _canonical(document)
            except Exception as exc:
                raise RuntimeDenied("CONTEXT_PACK_PAYLOAD_INVALID") from exc
            if not isinstance(document, dict) or canonical_payload != payload:
                raise RuntimeDenied("CONTEXT_PACK_PAYLOAD_INVALID")

            if (len(canonical_payload) > _MAX_PACK_BYTES
                    or document["task_id"] != record["task_id"]
                    or document["run_id"] != record["run_id"]
                    or document["content_hash"] != record["content_hash"]
                    or len(canonical_payload) != record["serialized_byte_size"]):
                raise RuntimeDenied("CONTEXT_PACK_RECORD_BINDING_MISMATCH")
            try:
                persisted_basis = json.loads(record["selection_basis_json"], object_pairs_hook=_strict_json_object)
                persisted_counts = json.loads(record["source_counts_json"], object_pairs_hook=_strict_json_object)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeDenied("CONTEXT_PACK_RECORD_BINDING_MISMATCH") from exc
            if (_canonical(document["selection_basis"]) != _canonical(persisted_basis)
                    or not self._valid_selection_basis(document["selection_basis"], len(document["entries"]))):
                raise RuntimeDenied("CONTEXT_PACK_RECORD_BINDING_MISMATCH")

            entries = document["entries"]
            refs = [entry["source_ref"] for entry in entries]
            if (len(entries) > _MAX_SOURCES or len(refs) != len(set(refs))
                    or any(entry["source_type"] not in _ALLOWED_PACK_SOURCE_TYPES for entry in entries)):
                raise RuntimeDenied("CONTEXT_PACK_ENTRY_INVALID")
            expected_order = sorted(
                entries, key=lambda item: (_SOURCE_ORDER[item["source_type"]], item["source_ref"].encode("utf-8"))
            )
            if entries != expected_order:
                raise RuntimeDenied("CONTEXT_PACK_ENTRY_ORDER_INVALID")
            source_counts = dict(sorted(Counter(entry["source_type"] for entry in entries).items()))
            if _canonical(source_counts) != _canonical(persisted_counts):
                raise RuntimeDenied("CONTEXT_PACK_RECORD_BINDING_MISMATCH")

            content_basis = {
                "task_id": document["task_id"], "run_id": document["run_id"],
                "selection_basis": document["selection_basis"], "entries": entries,
            }
            computed_content_hash = hashlib.sha256(_canonical(content_basis)).hexdigest()
            if computed_content_hash != document["content_hash"]:
                raise RuntimeDenied("CONTEXT_PACK_CONTENT_HASH_MISMATCH")

            for entry in entries:
                try:
                    embedded_bytes = entry["content"].encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise RuntimeDenied("CONTEXT_PACK_ENTRY_INTEGRITY_MISMATCH") from exc
                if hashlib.sha256(embedded_bytes).hexdigest() != entry["source_integrity_sha256"]:
                    raise RuntimeDenied("CONTEXT_PACK_ENTRY_INTEGRITY_MISMATCH")
                self._validate_current_source(entry, record["run_id"], boundary)

            return {
                "status": "CONTEXT_PACK_READ",
                "pack_id": pack_ref,
                "task_id": record["task_id"],
                "run_id": record["run_id"],
                "content_hash": document["content_hash"],
                "integrity_hash": integrity_hash,
                "serialized_byte_size": len(canonical_payload),
                "model_visible_exposure": document["model_visible_exposure"],
                "entries": entries,
            }

    def read_latest_compiled(self) -> dict:
        """Read the latest compiled pack using the existing latest ordering."""
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT pack_ref FROM context_pack_records WHERE state='COMPILED' "
                "ORDER BY compiled_at DESC,record_id DESC LIMIT 1"
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeDenied("CONTEXT_PACK_NOT_AVAILABLE")
        return self.read_compiled(rows[0]["pack_ref"])

    @staticmethod
    def _read_boundary(raw: str) -> dict:
        try:
            boundary = json.loads(raw, object_pairs_hook=_strict_json_object)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeDenied("CONTEXT_PACK_RUN_BOUNDARY_INVALID") from exc
        if (not isinstance(boundary, dict)
                or set(boundary) != {"allowed_classifications", "handling_tags"}
                or not isinstance(boundary["allowed_classifications"], list)
                or not isinstance(boundary["handling_tags"], list)
                or any(not isinstance(value, str) for value in boundary["allowed_classifications"])
                or any(not isinstance(value, str) for value in boundary["handling_tags"])
                or len(boundary["allowed_classifications"]) != len(set(boundary["allowed_classifications"]))
                or len(boundary["handling_tags"]) != len(set(boundary["handling_tags"]))):
            raise RuntimeDenied("CONTEXT_PACK_RUN_BOUNDARY_INVALID")
        return boundary

    @staticmethod
    def _valid_selection_basis(basis: dict, entry_count: int) -> bool:
        expected_keys = {
            "policy_id", "selection_rule", "ordering_rule", "source_ref_count",
            "memory_query_sha256", "memory_limit", "ambient_host_memory_read", "academy_sources_read",
        }
        if not isinstance(basis, dict) or set(basis) != expected_keys:
            return False
        query_hash = basis.get("memory_query_sha256")
        memory_limit = basis.get("memory_limit")
        return (
            basis.get("policy_id") == "NEXUS_CONTEXT_SELECTION_V1"
            and basis.get("selection_rule") == "EXPLICIT_CANONICAL_REFS_PLUS_ADMITTED_MEMORY_QUERY"
            and basis.get("ordering_rule") == "SOURCE_TYPE_FIXED_ORDER_THEN_SOURCE_REF_UTF8_BYTE_ORDER"
            and basis.get("source_ref_count") == entry_count
            and basis.get("ambient_host_memory_read") is False
            and basis.get("academy_sources_read") is False
            and (query_hash is None or isinstance(query_hash, str) and len(query_hash) == 64
                 and all(character in "0123456789abcdef" for character in query_hash))
            and ((query_hash is None and memory_limit is None)
                 or (query_hash is not None and isinstance(memory_limit, int)
                     and not isinstance(memory_limit, bool) and 1 <= memory_limit <= _MAX_SOURCES))
        )

    def _current_object_metadata(self, object_ref: str, failure_reason: str) -> dict:
        try:
            metadata = self.store.get_object_metadata(object_ref)
        except Exception as exc:
            raise RuntimeDenied(failure_reason) from exc
        if metadata.get("payload_state") == "PURGED":
            raise RuntimeDenied(failure_reason)
        return metadata

    def _validate_current_classification(
        self, assertion_ref: str, object_ref: str, boundary: dict, failure_reason: str,
    ) -> None:
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,"
                "policy_version,reason,actor_id,supersedes FROM classification_assertions WHERE assertion_id=?",
                (assertion_ref,),
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeDenied(failure_reason)
        row = dict(rows[0])
        try:
            tags = json.loads(row["handling_tags_json"], object_pairs_hook=_strict_json_object)
            assertion = {
                "schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": row["assertion_id"], "subject_type": row["subject_type"],
                "subject_ref": row["subject_ref"], "sensitivity_level": row["sensitivity_level"],
                "handling_tags": tags, "policy_version": row["policy_version"],
                "reason": row["reason"], "actor_id": row["actor_id"],
            }
            if row["supersedes"] is not None:
                assertion["supersedes"] = row["supersedes"]
            self.store._validate("nexus.classification_assertion@1.schema.json", assertion)
        except Exception as exc:
            raise RuntimeDenied(failure_reason) from exc
        if (assertion["subject_type"] != "OBJECT" or assertion["subject_ref"] != object_ref
                or not self.inspect._classification_visible(
                    {"sensitivity_level": assertion["sensitivity_level"],
                     "handling_tags_json": json.dumps(assertion["handling_tags"], ensure_ascii=False)},
                    boundary,
                )):
            raise RuntimeDenied(failure_reason)

    def _validate_current_source(self, entry: dict, run_id: str, boundary: dict) -> None:
        source_ref = entry["source_ref"]
        metadata = self._current_object_metadata(source_ref, "CONTEXT_PACK_SOURCE_UNAVAILABLE")
        expected_type = "memory" if entry["source_type"] == "admitted_memory" else entry["source_type"]
        if (metadata.get("object_type") != expected_type
                or metadata.get("schema_id") != "nexus.object"
                or metadata.get("schema_version") != 1
                or (metadata.get("payload_state"), metadata.get("lifecycle"), metadata.get("validity"))
                   != ("AVAILABLE", "ACTIVE", "VALID")
                or metadata.get("integrity_hash") != entry["source_integrity_sha256"]
                or metadata.get("classification_assertion_ref") != entry["classification_assertion_ref"]):
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_UNAVAILABLE")
        try:
            source_verified = self.store.verify_object(source_ref)
        except Exception:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_INTEGRITY_INVALID") from None
        if source_verified is not True:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_INTEGRITY_INVALID")
        self._validate_current_classification(
            entry["classification_assertion_ref"], source_ref, boundary,
            "CONTEXT_PACK_SOURCE_OUTSIDE_RUN_BOUNDARY",
        )

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
            if grant_id != run["grant_id"]:
                raise RuntimeDenied("CONTEXT_PACK_RUN_GRANT_MISMATCH")
            self.authority.evaluate_authorization(
                run["grant_id"],
                {"task": task_id, "resource": pack_object_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
                command_id + "-authorize-pack",
            )
            entries = [self._read_explicit_source(ref, task_id, run["grant_id"], run["data_boundary_json"])
                       for ref in refs]
            memory_query_sha256 = None
            if memory_query is not None:
                memory_query_sha256 = hashlib.sha256(memory_query.encode("utf-8")).hexdigest()
                matches = self.memory.search_admitted(query=memory_query, run_id=run_id, limit=memory_limit)
                for match in matches:
                    ref = match["object_id"]
                    if ref in refs:
                        raise RuntimeDenied("CONTEXT_PACK_DUPLICATE_MEMORY_SOURCE")
                    entry = self._read_explicit_source(
                        ref, task_id, run["grant_id"], run["data_boundary_json"], expected_type="memory"
                    )
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
                              run_boundary_json: str, expected_type: str | None = None) -> dict:
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
                "SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?",
                (metadata["classification_assertion_ref"],),
            ).fetchone()
        if not classification:
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_CLASSIFICATION_UNAVAILABLE")
        boundary = json.loads(run_boundary_json)
        source_tags = set(json.loads(classification["handling_tags_json"]))
        if (classification["sensitivity_level"] not in set(boundary["allowed_classifications"])
                or not source_tags.issubset(set(boundary["handling_tags"]))):
            raise RuntimeDenied("CONTEXT_PACK_SOURCE_OUTSIDE_RUN_BOUNDARY")
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
            "host_delivery_evidence": delivery,
            "actual_host_delivery": "UNKNOWN",
            "delivery_observed": False,
            "delivery_run_id": row["run_id"] if delivery != "NOT_DECLARED" else None,
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
            pack = conn.execute("SELECT task_id,run_id FROM context_pack_records WHERE pack_ref=? AND state='COMPILED'", (pack_ref,)).fetchone()
            rows = conn.execute(
                "SELECT i.manifest_object_id,e.created_by_run,s.payload_state,r.task_id,r.run_id "
                "FROM run_manifest_inputs i JOIN object_envelopes e ON e.object_id=i.manifest_object_id "
                "JOIN object_states s ON s.object_id=e.object_id "
                "JOIN runs r ON r.run_id=e.created_by_run "
                "WHERE i.input_object_id=? AND e.object_type='run_manifest' ORDER BY e.created_at DESC,e.object_id",
                (pack_ref,),
            ).fetchall()
        for row in rows:
            if (not pack or row["task_id"] != pack["task_id"] or row["run_id"] != pack["run_id"]
                    or row["payload_state"] != "AVAILABLE"):
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
