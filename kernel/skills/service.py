"""Governed registry for explicitly imported Agent Skills packages.

Discovery uses persisted name/description metadata only. Package bodies are
read for verification only during explicit registration and after a unique
Nexus fallback candidate has been selected.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from kernel.runtime.errors import RuntimeDenied
from kernel.object.errors import CommandConflict


_SCOPES = frozenset({"REPO", "USER", "EXPLICIT_IMPORT"})
_PROVENANCE = frozenset({"ADAPTER_DISCOVERY", "HOST_DECLARED"})
_MAX_FILES = 128
_MAX_FILE_BYTES = 1_048_576
_MAX_PACKAGE_BYTES = 5_242_880
_MAX_CANDIDATES = 50
_SAFE_NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SAFE_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SAFE_COMMAND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class SkillRegistryService:
    """Explicit Agent Skills registry. It never executes package resources."""

    def __init__(self, *, store, authority, participation, source_roots: dict[str, str | Path] | None = None):
        self.store = store
        self.authority = authority
        self.participation = participation
        # Absolute configured roots stay process-local and are never persisted.
        self.source_roots = {scope: Path(root).expanduser().resolve() for scope, root in (source_roots or {}).items()}
        if set(self.source_roots) - _SCOPES:
            raise ValueError("SKILL_SOURCE_SCOPE_INVALID")

    def register_package(
        self, *, source_scope: str, source_namespace: str, source_ref: str,
        task_id: str, run_id: str, grant_id: str, classification_assertion_ref: str,
        command_id: str,
    ) -> dict:
        """Register one explicitly named package; registrations start ineligible."""
        if source_scope not in _SCOPES or not _SAFE_NAMESPACE.fullmatch(source_namespace):
            raise RuntimeDenied("SKILL_SOURCE_DESCRIPTOR_INVALID")
        if not isinstance(command_id, str) or not _SAFE_COMMAND_ID.fullmatch(command_id) or not classification_assertion_ref:
            raise RuntimeDenied("SKILL_REGISTRATION_IDENTITY_INVALID")
        run = self._bound_run(task_id, run_id, grant_id)
        package = self._read_package(source_scope, source_ref)
        identity = {
            "name": package["name"], "source_scope": source_scope,
            "source_namespace": source_namespace, "source_ref": package["source_ref"],
            "package_manifest_sha256": package["package_manifest_sha256"],
        }
        skill_id = "skill-" + _sha256(_canonical(identity))
        instruction_object_ref = "skill-src-" + _sha256(skill_id.encode("utf-8"))
        self.authority.evaluate_authorization(
            run["grant_id"], {"task": task_id, "resource": instruction_object_ref,
                              "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id + "-authorize-snapshot",
        )
        self.authority.evaluate_authorization(
            run["grant_id"], {"task": task_id, "resource": skill_id,
                              "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id + "-authorize-registry",
        )
        with self.store._connection() as conn:
            existing = conn.execute("SELECT * FROM skill_registry_entries WHERE skill_id=?", (skill_id,)).fetchone()
        if existing:
            return self._public_entry(dict(existing), source_available=True)

        chain = self.authority.validate_delegation_chain(run["grant_id"])
        registered_by = chain[-1]["granted_to"]
        self.store.put_object(
            command_id=command_id + "-snapshot", object_id=instruction_object_ref,
            payload=_canonical({
                "schema_id": "nexus.skill_instruction_snapshot", "schema_version": 1,
                "skill_id": skill_id, "package_revision": package["package_manifest_sha256"],
                "instruction_sha256": package["skill_md_sha256"],
                "instruction_utf8": package["files"]["SKILL.md"].decode("utf-8"),
            }), object_type="skill", created_by_run=run_id,
            classification_assertion_ref=classification_assertion_ref,
        )
        registered_at = _now()
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                old_rows = conn.execute(
                    "SELECT skill_id,status FROM skill_registry_entries WHERE source_scope=? "
                    "AND source_namespace=? AND source_ref=? AND skill_name=? AND package_manifest_sha256<>?",
                    (source_scope, source_namespace, package["source_ref"], package["name"], package["package_manifest_sha256"]),
                ).fetchall()
                conn.execute(
                    "INSERT INTO skill_registry_entries(skill_id,skill_name,description,source_scope,source_namespace,"
                    "source_ref,skill_md_sha256,package_manifest_sha256,package_manifest_json,fallback_blockers_json,"
                    "instruction_object_ref,registered_task_id,registered_run_id,registered_by,status,registered_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'REGISTERED',?)",
                    (skill_id, package["name"], package["description"], source_scope, source_namespace,
                     package["source_ref"], package["skill_md_sha256"], package["package_manifest_sha256"],
                     _canonical(package["manifest"]).decode("utf-8"), _canonical(package["fallback_blockers"]).decode("utf-8"),
                     instruction_object_ref, task_id, run_id, registered_by, registered_at),
                )
                for old in old_rows:
                    if old["status"] != "STALE":
                        conn.execute("UPDATE skill_registry_entries SET status='STALE' WHERE skill_id=?", (old["skill_id"],))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get(skill_id)

    def review(self, *, skill_id: str, next_status: str, task_id: str, run_id: str,
               grant_id: str, command_id: str, approval_id: str | None = None) -> dict:
        """Review one exact package revision; enabling requires a bound Human Approval."""
        if (next_status not in {"ENABLED", "DISABLED", "REJECTED"}
                or not isinstance(command_id, str) or not _SAFE_COMMAND_ID.fullmatch(command_id)):
            raise RuntimeDenied("SKILL_REVIEW_REQUEST_INVALID")
        replay = self.replay_review_command(
            skill_id=skill_id, next_status=next_status, task_id=task_id,
            run_id=run_id, grant_id=grant_id, command_id=command_id,
            approval_id=approval_id,
        )
        if replay is not None:
            return replay
        with self.store._connection() as conn:
            row = conn.execute("SELECT * FROM skill_registry_entries WHERE skill_id=?", (skill_id,)).fetchone()
        if not row:
            raise RuntimeDenied("SKILL_REGISTRATION_NOT_FOUND")
        row = dict(row)
        run = self._bound_run(task_id, run_id, grant_id)
        request = {
            "skill_id": skill_id, "next_status": next_status,
            "package_manifest_sha256": row["package_manifest_sha256"],
            "approval_id": approval_id, "task_id": task_id, "run_id": run_id,
            "grant_id": run["grant_id"],
        }
        request_hash = self.store._request_hash("review_skill", request)
        if row["status"] == "STALE":
            raise RuntimeDenied("SKILL_REVISION_STALE")
        chain = self.authority.validate_delegation_chain(run["grant_id"])
        reviewer = chain[-1]["granted_to"]
        if next_status == "ENABLED":
            if not approval_id:
                raise RuntimeDenied("SKILL_ENABLE_APPROVAL_REQUIRED")
            payload_hash = self._review_approval_payload_hash(request)
            self.authority.evaluate_authorization(
                run["grant_id"], {"task": task_id, "resource": skill_id,
                                  "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
                command_id + "-authorize-review", approval_id=approval_id,
                payload_integrity_hash=payload_hash,
            )
            with self.store._connection() as conn:
                approval = conn.execute(
                    "SELECT a.approver_principal_id,p.principal_type,p.status FROM approval_decisions a "
                    "JOIN principals p ON p.principal_id=a.approver_principal_id WHERE a.approval_id=?",
                    (approval_id,),
                ).fetchone()
            if not approval or approval["principal_type"] != "HUMAN" or approval["status"] != "ACTIVE":
                raise RuntimeDenied("SKILL_ENABLE_REQUIRES_ACTIVE_HUMAN_APPROVAL")
            reviewer = approval["approver_principal_id"]
            package = self._read_package(row["source_scope"], row["source_ref"])
            if package["package_manifest_sha256"] != row["package_manifest_sha256"]:
                self._mark_stale(skill_id)
                raise RuntimeDenied("SKILL_PACKAGE_REVISION_CHANGED")
            source_metadata = self.store.get_object_metadata(row["instruction_object_ref"])
            if source_metadata.get("payload_state") != "AVAILABLE":
                self._mark_stale(skill_id)
                raise RuntimeDenied("SKILL_INSTRUCTION_SNAPSHOT_UNAVAILABLE")
        else:
            self.authority.evaluate_authorization(
                run["grant_id"], {"task": task_id, "resource": skill_id,
                                  "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
                command_id + "-authorize-review",
            )
        reviewed_at = _now()
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = self.store._replay_command(conn, command_id, "review_skill", request_hash)
                if prior is not None:
                    conn.commit()
                    return prior
                current = conn.execute("SELECT status,package_manifest_sha256 FROM skill_registry_entries WHERE skill_id=?",
                                       (skill_id,)).fetchone()
                if not current or current["status"] != row["status"] or current["package_manifest_sha256"] != row["package_manifest_sha256"]:
                    raise RuntimeDenied("SKILL_REVIEW_STATE_CONFLICT")
                changed = conn.execute(
                    "UPDATE skill_registry_entries SET status=?,review_command_id=?,reviewed_by=?,reviewed_at=? "
                    "WHERE skill_id=? AND status=?",
                    (next_status, command_id, reviewer, reviewed_at, skill_id, row["status"]),
                ).rowcount
                if changed != 1:
                    raise RuntimeDenied("SKILL_REVIEW_STATE_CONFLICT")
                updated = conn.execute("SELECT e.*,s.payload_state FROM skill_registry_entries e "
                                       "LEFT JOIN object_states s ON s.object_id=e.instruction_object_ref WHERE e.skill_id=?",
                                       (skill_id,)).fetchone()
                result = self._public_entry(dict(updated), source_available=updated["payload_state"] == "AVAILABLE")
                self.store._record_command(conn, command_id, "review_skill", request_hash, result)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def replay_review_command(self, *, skill_id: str, next_status: str, task_id: str,
                              run_id: str, grant_id: str, command_id: str,
                              approval_id: str | None = None) -> dict | None:
        """Return an exact committed review before mutable Run/authority gates."""
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            ledger = conn.execute(
                "SELECT operation,result_json FROM command_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if not ledger:
                return None
            result = json.loads(ledger["result_json"])
            row = conn.execute(
                "SELECT package_manifest_sha256 FROM skill_registry_entries WHERE skill_id=?", (skill_id,)
            ).fetchone()
        revision = row["package_manifest_sha256"] if row else result.get("package_revision")
        request = {
            "skill_id": skill_id, "next_status": next_status,
            "package_manifest_sha256": revision,
            "approval_id": approval_id, "task_id": task_id, "run_id": run_id,
            "grant_id": grant_id,
        }
        request_hash = self.store._request_hash("review_skill", request)
        with self.store._connection() as conn:
            return self.store._replay_command(conn, command_id, "review_skill", request_hash)

    @staticmethod
    def _review_approval_payload_hash(request: dict) -> str:
        return _sha256(_canonical({"operation": "review_skill", **request}))

    @classmethod
    def review_approval_payload_hash(cls, request: dict) -> str:
        """Canonical payload commitment shared by the operator application boundary."""
        return cls._review_approval_payload_hash(request)

    def prepare_review(self, *, skill_id: str, task_id: str, run_id: str, grant_id: str) -> dict:
        """Return safe revision metadata after validating a new review's Run binding."""
        run = self._bound_run(task_id, run_id, grant_id)
        return {"entry": self.get(skill_id), "governing_grant_id": run["grant_id"]}

    def discover(self, query: str, *, limit: int = 20) -> dict:
        """Search only safe persisted metadata; never read Skill instruction bodies."""
        self.store._require_mode("core_read")
        if not isinstance(query, str) or not query.strip() or len(query) > 1024 or not 1 <= limit <= _MAX_CANDIDATES:
            raise RuntimeDenied("SKILL_QUERY_INVALID")
        ranked = self._rank(query)
        rows = ranked[:limit]
        return {"status": "MATCHES" if rows else "NO_MATCH", "candidate_count": len(ranked),
                "candidates": [{**self._public_entry(row, source_available=True), "match_score": score}
                               for score, row in rows],
                "selection_basis": "UNIQUE_QUERY_TERM_OVERLAP_OVER_NAME_AND_DESCRIPTION; SCORE_DESC_SKILL_ID_ASC"}

    def resolve(self, *, query: str, task_id: str, run_id: str, grant_id: str,
                classification_assertion_ref: str, command_id: str, host_inventory=None) -> dict:
        """Resolve one candidate in ACTIVE mode, delegating native activation to Host."""
        self.store._require_mode("core_read")
        started = time.perf_counter_ns()
        if (not isinstance(query, str) or not query.strip() or len(query) > 1024
                or not isinstance(command_id, str) or not _SAFE_COMMAND_ID.fullmatch(command_id)
                or not isinstance(classification_assertion_ref, str) or not classification_assertion_ref):
            raise RuntimeDenied("SKILL_QUERY_INVALID")
        query_sha256 = _sha256(query.encode("utf-8"))
        classification_sha256 = _sha256(classification_assertion_ref.encode("utf-8"))
        prior = self._resolution_replay(
            command_id=command_id, task_id=task_id, run_id=run_id, grant_id=grant_id,
            query_sha256=query_sha256, classification_sha256=classification_sha256,
        )
        if prior is not None:
            return prior
        if self.participation.current()["mode"] != "ACTIVE":
            return {"result_status": "INACTIVE_MODE", "resolution": None,
                    "reason": "SKILL_RESOLUTION_REQUIRES_ACTIVE_PARTICIPATION",
                    "instruction_load_status": "NOT_LOADED", "delivery_status": "UNKNOWN",
                    "model_visible_exposure": "UNKNOWN"}
        run = self._bound_run(task_id, run_id, grant_id)
        self.authority.evaluate_authorization(
            run["grant_id"], {"task": task_id, "resource": run_id,
                              "action": "TRACE_APPEND", "audience": "nexus-runtime"},
            command_id + "-authorize-resolution",
        )
        inventory, inventory_sha256 = self._host_inventory_snapshot(host_inventory)
        ranked = self._rank(query)
        basis = {"selection_policy": "SKILL_METADATA_TERM_OVERLAP_V1",
                 "tie_break": "SCORE_DESC_SKILL_ID_ASC; TOP_SCORE_TIE_RETURNS_AMBIGUOUS",
                 "metadata_fields": ["name", "description"], "instruction_bodies_read_for_search": False,
                 "candidate_limit": _MAX_CANDIDATES,
                 "classification_assertion_sha256": classification_sha256,
                 "authority_grant_id": run["grant_id"],
                 "host_inventory_sha256": inventory_sha256}
        if len(ranked) > _MAX_CANDIDATES:
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="SKILL_CANDIDATE_LIMIT_EXCEEDED", started=started)
        if not ranked:
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=0, basis=basis, result_status="NO_MATCH",
                resolution=None, reason="NO_MATCH", started=started)
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="AMBIGUOUS",
                resolution=None, reason="TOP_CANDIDATES_TIED", started=started,
                candidates=[self._public_entry(row, source_available=True) for _score, row in ranked[:_MAX_CANDIDATES]])
        score, row = ranked[0]
        native, inventory_provenance = self._native_available(inventory, row["skill_name"], row["package_manifest_sha256"])
        if native == "INVALID":
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="HOST_SKILL_INVENTORY_INVALID", started=started,
                skill_id=row["skill_id"])
        if native == "UNKNOWN":
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="HOST_NATIVE_AVAILABILITY_UNKNOWN", started=started,
                skill_id=row["skill_id"], host_availability="UNKNOWN",
                host_inventory_provenance=inventory_provenance)
        if native == "AVAILABLE":
            basis["host_inventory_provenance"] = inventory_provenance
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="RESOLVED",
                resolution="HOST_NATIVE", reason="HOST_NATIVE_SKILL_AVAILABLE", started=started,
                skill_id=row["skill_id"], host_availability="AVAILABLE",
                host_inventory_provenance=inventory_provenance, instruction_load_status="NOT_LOADED")

        blockers = json.loads(row["fallback_blockers_json"])
        if blockers:
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason=blockers[0], started=started,
                skill_id=row["skill_id"], host_availability="UNAVAILABLE")
        try:
            package = self._read_package(row["source_scope"], row["source_ref"])
        except (OSError, RuntimeDenied, ValueError):
            self._mark_stale(row["skill_id"])
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="SKILL_SOURCE_UNAVAILABLE_OR_STALE", started=started,
                skill_id=row["skill_id"], host_availability="UNAVAILABLE")
        if package["package_manifest_sha256"] != row["package_manifest_sha256"]:
            self._mark_stale(row["skill_id"])
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="SKILL_PACKAGE_REVISION_CHANGED", started=started,
                skill_id=row["skill_id"], host_availability="UNAVAILABLE")
        source_metadata = self.store.get_object_metadata(row["instruction_object_ref"])
        if source_metadata.get("payload_state") != "AVAILABLE":
            self._mark_stale(row["skill_id"])
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="SKILL_INSTRUCTION_SNAPSHOT_PURGED", started=started,
                skill_id=row["skill_id"], host_availability="UNAVAILABLE")
        try:
            snapshot = json.loads(self.store.get_payload(row["instruction_object_ref"]).decode("utf-8"))
            instruction = snapshot["instruction_utf8"].encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            snapshot, instruction = {}, b""
        if (snapshot.get("schema_id") != "nexus.skill_instruction_snapshot"
                or snapshot.get("skill_id") != row["skill_id"]
                or snapshot.get("package_revision") != row["package_manifest_sha256"]
                or snapshot.get("instruction_sha256") != row["skill_md_sha256"]
                or _sha256(instruction) != row["skill_md_sha256"]
                or instruction != package["files"]["SKILL.md"]):
            self._mark_stale(row["skill_id"])
            return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
                query=query, candidate_count=len(ranked), basis=basis, result_status="UNSUPPORTED",
                resolution="UNSUPPORTED", reason="SKILL_INSTRUCTION_INTEGRITY_MISMATCH", started=started,
                skill_id=row["skill_id"], host_availability="UNAVAILABLE")
        fallback_ref = "skill-fallback-" + _sha256(_canonical({
            "command_id": command_id, "task_id": task_id, "run_id": run_id,
            "skill_id": row["skill_id"], "instruction_sha256": row["skill_md_sha256"],
        }))
        self.authority.evaluate_authorization(
            run["grant_id"], {"task": task_id, "resource": fallback_ref,
                              "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id + "-authorize-fallback-artifact",
        )
        self.store.put_object(
            command_id=command_id + "-fallback-artifact", object_id=fallback_ref,
            payload=_canonical({
                "schema_id": "nexus.skill_instruction_artifact", "schema_version": 1,
                "skill_id": row["skill_id"], "package_revision": row["package_manifest_sha256"],
                "instruction_sha256": row["skill_md_sha256"], "source_scope": row["source_scope"],
                "source_namespace": row["source_namespace"],
                "task_id": task_id, "run_id": run_id,
                "instruction_utf8": instruction.decode("utf-8"),
            }), object_type="artifact", created_by_run=run_id,
            classification_assertion_ref=classification_assertion_ref,
            derived_from=[row["instruction_object_ref"]],
        )
        basis["fallback"] = "INTEGRITY_VERIFIED_SKILL_MD_ONLY; CONTEXT_PACK_ARTIFACT_CAN_CONSUME_REF"
        return self._record_resolution(command_id=command_id, task_id=task_id, run_id=run_id,
            query=query, candidate_count=len(ranked), basis=basis, result_status="RESOLVED",
            resolution="NEXUS_FALLBACK", reason="PORTABLE_SKILL_MD_SNAPSHOT_CREATED",
            started=started, skill_id=row["skill_id"], host_availability="UNAVAILABLE",
            instruction_load_status="LOADED_TO_GOVERNED_ARTIFACT", instruction_object_ref=fallback_ref,
            instruction_sha256=row["skill_md_sha256"], instruction_byte_size=len(instruction))

    def _resolution_replay(self, *, command_id: str, task_id: str, run_id: str,
                           grant_id: str, query_sha256: str, classification_sha256: str) -> dict | None:
        with self.store._connection() as conn:
            prior = conn.execute(
                "SELECT r.*,s.payload_state AS instruction_payload_state FROM skill_resolution_records r "
                "LEFT JOIN object_states s ON s.object_id=r.instruction_object_ref WHERE r.command_id=?",
                (command_id,),
            ).fetchone()
        if not prior:
            return None
        prior = dict(prior)
        basis = json.loads(prior["selection_basis_json"])
        if (prior["task_id"] != task_id or prior["run_id"] != run_id
                or prior["query_sha256"] != query_sha256
                or basis.get("classification_assertion_sha256") != classification_sha256
                or basis.get("authority_grant_id") != grant_id):
            raise CommandConflict("COMMAND_CONFLICT")
        return self._public_resolution(prior)

    def snapshot(self) -> dict:
        """Panel-safe Registry projection; excludes every instruction body and path."""
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT e.*,s.payload_state FROM skill_registry_entries e "
                "LEFT JOIN object_states s ON s.object_id=e.instruction_object_ref "
                "ORDER BY e.skill_name,e.source_scope,e.source_namespace,e.package_manifest_sha256,e.skill_id"
            )]
            latest = conn.execute(
                "SELECT r.*,s.payload_state AS instruction_payload_state FROM skill_resolution_records r "
                "LEFT JOIN object_states s ON s.object_id=r.instruction_object_ref "
                "ORDER BY r.created_at DESC,r.selection_id DESC LIMIT 1"
            ).fetchone()
        projected = [self._public_entry(row, source_available=row.get("payload_state") == "AVAILABLE") for row in rows]
        statuses = [entry["status"] for entry in projected]
        latest_projection = self._public_resolution(dict(latest)) if latest else None
        if latest_projection and latest_projection.get("skill_id"):
            selected = next((entry for entry in projected if entry["skill_id"] == latest_projection["skill_id"]), None)
            if selected:
                latest_projection["skill_name"] = selected["name"]
                latest_projection["package_revision"] = selected["package_revision"]
        return {
            "status": "OBSERVED", "registered_count": len(projected),
            "eligible_count": sum(entry["status"] == "ENABLED" for entry in projected),
            "stale_or_disabled_count": sum(entry["status"] in {"STALE", "DISABLED", "REJECTED"} for entry in projected),
            "registered": projected, "latest_selection": latest_projection,
            "instruction_bodies_included": False,
        }

    def latest_resolution(self) -> dict | None:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT r.*,s.payload_state AS instruction_payload_state FROM skill_resolution_records r "
                "LEFT JOIN object_states s ON s.object_id=r.instruction_object_ref "
                "ORDER BY r.created_at DESC,r.selection_id DESC LIMIT 1"
            ).fetchone()
        return self._public_resolution(dict(row)) if row else None

    def _record_resolution(self, *, command_id, task_id, run_id, query, candidate_count, basis,
                           result_status, resolution, reason, started, skill_id=None,
                           host_availability="UNKNOWN", host_inventory_provenance=None,
                           instruction_load_status="NOT_LOADED", instruction_object_ref=None,
                           instruction_sha256=None, instruction_byte_size=None, candidates=None) -> dict:
        elapsed_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        selection_id = "skill-selection-" + _sha256(command_id.encode("utf-8"))
        created_at = _now()
        selection_basis = {**basis, "reason": reason, "top_candidates": [
            item["skill_id"] for item in (candidates or [])],
            "selected_skill_id": skill_id, "query_sha256": _sha256(query.encode("utf-8"))}
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prior = conn.execute(
                    "SELECT r.*,s.payload_state AS instruction_payload_state FROM skill_resolution_records r "
                    "LEFT JOIN object_states s ON s.object_id=r.instruction_object_ref WHERE r.command_id=?",
                    (command_id,),
                ).fetchone()
                if prior:
                    prior = dict(prior)
                    prior_basis = json.loads(prior["selection_basis_json"])
                    if (prior["task_id"] != task_id or prior["run_id"] != run_id
                            or prior["query_sha256"] != _sha256(query.encode("utf-8"))
                            or prior_basis.get("classification_assertion_sha256") != basis.get("classification_assertion_sha256")
                            or prior_basis.get("authority_grant_id") != basis.get("authority_grant_id")):
                        raise CommandConflict("COMMAND_CONFLICT")
                    conn.commit()
                    return self._public_resolution(prior)
                conn.execute(
                "INSERT INTO skill_resolution_records(selection_id,command_id,task_id,run_id,query_sha256,"
                "candidate_count,selection_basis_json,result_status,resolution,skill_id,host_inventory_provenance,"
                "host_native_availability,instruction_object_ref,instruction_sha256,instruction_byte_size,"
                "instruction_load_status,delivery_status,model_visible_exposure,selection_latency_ms,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (selection_id, command_id, task_id, run_id, _sha256(query.encode("utf-8")), candidate_count,
                 _canonical(selection_basis).decode("utf-8"), result_status, resolution, skill_id,
                 host_inventory_provenance, host_availability, instruction_object_ref, instruction_sha256,
                 instruction_byte_size, instruction_load_status, "UNKNOWN", "UNKNOWN", elapsed_ms, created_at),
                )
                saved = conn.execute(
                    "SELECT r.*,s.payload_state AS instruction_payload_state FROM skill_resolution_records r "
                    "LEFT JOIN object_states s ON s.object_id=r.instruction_object_ref WHERE r.command_id=?",
                    (command_id,),
                ).fetchone()
                conn.commit()
                return self._public_resolution(dict(saved))
            except Exception:
                conn.rollback()
                raise

    def _bound_run(self, task_id: str, run_id: str, grant_id: str) -> dict:
        with self.store._connection() as conn:
            row = conn.execute("SELECT task_id,grant_id,status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row or row["task_id"] != task_id or row["status"] in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            raise RuntimeDenied("SKILL_RUN_TASK_OR_STATE_INVALID")
        if row["grant_id"] != grant_id:
            raise RuntimeDenied("SKILL_RUN_GRANT_MISMATCH")
        self.store._require_mode("core_write")
        return dict(row)

    def _rank(self, query: str) -> list[tuple[int, dict]]:
        terms = _terms(query)
        if not terms:
            return []
        with self.store._connection() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT e.*,s.payload_state FROM skill_registry_entries e "
                "JOIN object_states s ON s.object_id=e.instruction_object_ref "
                "WHERE e.status='ENABLED' AND s.payload_state='AVAILABLE' "
                "ORDER BY e.skill_name,e.source_scope,e.source_namespace,e.package_manifest_sha256,e.skill_id"
            )]
        ranked = []
        for row in rows:
            haystack = _terms(row["skill_name"] + " " + row["description"])
            score = len(terms.intersection(haystack))
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["skill_id"]))
        return ranked

    @staticmethod
    def _host_inventory_snapshot(adapter) -> tuple[dict | None, str]:
        if adapter is None:
            return None, _sha256(b"NO_HOST_INVENTORY")
        try:
            inventory = adapter.inventory()
            digest = _sha256(_canonical(inventory))
        except Exception:
            return {"_invalid_inventory": True}, _sha256(b"INVALID_HOST_INVENTORY")
        return inventory, digest

    def _native_available(self, inventory, name: str, revision: str) -> tuple[str, str | None]:
        if inventory is None:
            return "UNKNOWN", None
        if not isinstance(inventory, dict) or set(inventory) != {"adapter_id", "inventory_complete", "skills"}:
            return "INVALID", None
        if not isinstance(inventory["adapter_id"], str) or not _SAFE_NAMESPACE.fullmatch(inventory["adapter_id"]):
            return "INVALID", None
        skills = inventory["skills"]
        if not isinstance(inventory["inventory_complete"], bool) or not isinstance(skills, list) or len(skills) > 256:
            return "INVALID", None
        matching = []
        for item in skills:
            if not isinstance(item, dict) or set(item) - {"name", "availability", "provenance", "revision_sha256"}:
                return "INVALID", None
            if (not isinstance(item.get("name"), str) or not _SAFE_SKILL_NAME.fullmatch(item["name"])
                    or item.get("availability") not in {"AVAILABLE", "UNAVAILABLE"}):
                return "INVALID", None
            if item.get("provenance") not in _PROVENANCE:
                return "INVALID", None
            if item.get("revision_sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", item["revision_sha256"]):
                return "INVALID", None
            if item["name"] == name:
                matching.append(item)
        if len(matching) > 1:
            return "INVALID", None
        if not matching:
            return ("UNAVAILABLE", None) if inventory["inventory_complete"] else ("UNKNOWN", None)
        if matching[0]["availability"] == "UNAVAILABLE":
            return "UNAVAILABLE", matching[0]["provenance"]
        host_revision = matching[0].get("revision_sha256")
        if host_revision != revision:
            return "UNKNOWN", matching[0]["provenance"]
        return "AVAILABLE", matching[0]["provenance"]

    def _read_package(self, scope: str, source_ref: str, *, root_override: str | Path | None = None) -> dict:
        if scope not in _SCOPES:
            raise RuntimeDenied("SKILL_SOURCE_SCOPE_INVALID")
        if root_override is None and scope not in self.source_roots:
            raise RuntimeDenied("SKILL_SOURCE_ROOT_UNCONFIGURED")
        if not isinstance(source_ref, str) or "\\" in source_ref or re.match(r"^[A-Za-z]:", source_ref):
            raise RuntimeDenied("SKILL_SOURCE_REF_INVALID")
        relative = PurePosixPath(source_ref)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} or ":" in part for part in relative.parts):
            raise RuntimeDenied("SKILL_SOURCE_REF_INVALID")
        try:
            configured_root = Path(root_override).expanduser() if root_override is not None else self.source_roots[scope]
            base = configured_root.resolve(strict=True)
            package_root = (base.joinpath(*relative.parts)).resolve(strict=True)
        except OSError as exc:
            raise RuntimeDenied("SKILL_PACKAGE_PATH_UNAVAILABLE") from exc
        try:
            package_root.relative_to(base)
        except ValueError as exc:
            raise RuntimeDenied("SKILL_PATH_TRAVERSAL") from exc
        if not package_root.is_dir():
            raise RuntimeDenied("SKILL_PACKAGE_NOT_DIRECTORY")
        files: dict[str, bytes] = {}
        manifest = []
        total = 0
        paths: list[Path] = []
        visited = 0
        queued = 0

        def visit(directory: Path) -> None:
            nonlocal visited, queued
            try:
                entries = []
                with os.scandir(directory) as scanner:
                    for entry in scanner:
                        queued += 1
                        if visited + queued > _MAX_FILES * 4:
                            raise RuntimeDenied("SKILL_PACKAGE_ENTRY_LIMIT_EXCEEDED")
                        entries.append(entry)
                entries.sort(key=lambda entry: entry.name.encode("utf-8", errors="strict"))
            except UnicodeError as exc:
                raise RuntimeDenied("SKILL_PACKAGE_PATH_NOT_UTF8") from exc
            for entry in entries:
                queued -= 1
                visited += 1
                path = Path(entry.path)
                if entry.is_symlink():
                    paths.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    visit(path)
                else:
                    paths.append(path)

        visit(package_root)
        paths.sort(key=lambda path: path.relative_to(package_root).as_posix().encode("utf-8", errors="strict"))
        for path in paths:
            relative_path = path.relative_to(package_root).as_posix()
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise RuntimeDenied("SKILL_PACKAGE_ENTRY_UNAVAILABLE") from exc
            try:
                resolved.relative_to(package_root)
            except ValueError as exc:
                raise RuntimeDenied("SKILL_SYMLINK_ESCAPES_PACKAGE_ROOT") from exc
            if path.is_symlink() and resolved.is_dir():
                raise RuntimeDenied("SKILL_DIRECTORY_SYMLINK_UNSUPPORTED")
            if not resolved.is_file():
                raise RuntimeDenied("SKILL_PACKAGE_SPECIAL_FILE_UNSUPPORTED")
            if len(files) >= _MAX_FILES:
                raise RuntimeDenied("SKILL_PACKAGE_FILE_LIMIT_EXCEEDED")
            size = resolved.stat().st_size
            if size > _MAX_FILE_BYTES:
                raise RuntimeDenied("SKILL_PACKAGE_FILE_SIZE_LIMIT_EXCEEDED")
            if total + size > _MAX_PACKAGE_BYTES:
                raise RuntimeDenied("SKILL_PACKAGE_SIZE_LIMIT_EXCEEDED")
            with resolved.open("rb") as stream:
                content = stream.read(_MAX_FILE_BYTES + 1)
            if len(content) > _MAX_FILE_BYTES:
                raise RuntimeDenied("SKILL_PACKAGE_FILE_SIZE_LIMIT_EXCEEDED")
            total += len(content)
            if total > _MAX_PACKAGE_BYTES:
                raise RuntimeDenied("SKILL_PACKAGE_SIZE_LIMIT_EXCEEDED")
            files[relative_path] = content
            manifest.append({"path": relative_path, "sha256": _sha256(content), "size_bytes": len(content)})
        if "SKILL.md" not in files:
            raise RuntimeDenied("SKILL_MD_REQUIRED")
        metadata = _parse_frontmatter(files["SKILL.md"])
        name = metadata["name"]
        if len(name) > 64 or not _SAFE_SKILL_NAME.fullmatch(name):
            raise RuntimeDenied("SKILL_NAME_INVALID")
        if name != package_root.name:
            raise RuntimeDenied("SKILL_NAME_DIRECTORY_MISMATCH")
        if len(metadata["description"]) > 1024:
            raise RuntimeDenied("SKILL_DESCRIPTION_TOO_LONG")
        if re.search(r"(?i)(?:[a-z]:[\\/]|/users/|/home/|/root/|\\\\[^\\]+\\)", metadata["description"]):
            raise RuntimeDenied("SKILL_DESCRIPTION_PRIVATE_PATH_UNSUPPORTED")
        manifest.sort(key=lambda item: item["path"].encode("utf-8"))
        manifest_sha = _sha256(_canonical(manifest))
        auxiliary = [item["path"] for item in manifest if item["path"] not in {"SKILL.md", "agents/openai.yaml"}]
        blockers = ["PACKAGE_AUXILIARY_RESOURCES_NOT_HYDRATED"] if auxiliary else []
        return {
            "source_ref": relative.as_posix(), "files": files, "manifest": manifest,
            "name": name, "description": metadata["description"],
            "skill_md_sha256": _sha256(files["SKILL.md"]),
            "package_manifest_sha256": manifest_sha, "fallback_blockers": blockers,
        }

    def inventory_package_metadata(self, scope: str, source_ref: str, *, root_override: str | Path | None = None) -> dict:
        """Read a configured package for filesystem inventory without returning bodies."""
        package = self._read_package(scope, source_ref, root_override=root_override)
        return {
            "name": package["name"], "manifest": package["manifest"],
            "package_manifest_sha256": package["package_manifest_sha256"],
        }

    def _mark_stale(self, skill_id: str) -> None:
        with self.store._lock, self.store._connection() as conn:
            conn.execute("UPDATE skill_registry_entries SET status='STALE' WHERE skill_id=? AND status IN ('REGISTERED','ENABLED','DISABLED','REJECTED')", (skill_id,))
            conn.commit()

    def get(self, skill_id: str) -> dict:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            row = conn.execute("SELECT e.*,s.payload_state FROM skill_registry_entries e "
                               "LEFT JOIN object_states s ON s.object_id=e.instruction_object_ref WHERE e.skill_id=?",
                               (skill_id,)).fetchone()
        if not row:
            raise RuntimeDenied("SKILL_REGISTRATION_NOT_FOUND")
        return self._public_entry(dict(row), source_available=row["payload_state"] == "AVAILABLE")

    @staticmethod
    def _public_entry(row: dict, *, source_available: bool) -> dict:
        status = row["status"]
        stale_reason = None
        if row.get("instruction_object_ref") is None:
            status, stale_reason = "STALE", "INSTRUCTION_SNAPSHOT_PURGED"
        if not source_available and status != "STALE":
            status, stale_reason = "STALE", "INSTRUCTION_SNAPSHOT_UNAVAILABLE"
        return {
            "skill_id": row["skill_id"], "name": row["skill_name"], "description": row["description"],
            "source_scope": row["source_scope"], "source_namespace": row["source_namespace"],
            "source_ref": row["source_ref"],
            "package_revision": row["package_manifest_sha256"],
            "skill_md_sha256": row["skill_md_sha256"], "package_manifest_sha256": row["package_manifest_sha256"],
            "status": status, "eligible": status == "ENABLED",
            "fallback_blockers": json.loads(row["fallback_blockers_json"]),
            "stale_reason": stale_reason,
        }

    @staticmethod
    def _public_resolution(row: dict) -> dict:
        result = {
            "selection_id": row["selection_id"], "result_status": row["result_status"],
            "resolution": row["resolution"], "reason": json.loads(row["selection_basis_json"]).get("reason"),
            "candidate_count": row["candidate_count"],
            "skill_id": row["skill_id"], "host_native_availability": row["host_native_availability"],
            "host_inventory_provenance": row["host_inventory_provenance"],
            "instruction_object_ref": row["instruction_object_ref"],
            "instruction_sha256": row["instruction_sha256"],
            "instruction_byte_size": row["instruction_byte_size"],
            "instruction_load_status": row["instruction_load_status"],
            "instruction_payload_availability": (
                "AVAILABLE" if row.get("instruction_payload_state") == "AVAILABLE"
                else "UNAVAILABLE" if row.get("instruction_object_ref") is None
                or row.get("instruction_payload_state") == "PURGED" else "UNKNOWN"
            ),
            "delivery_status": row["delivery_status"], "model_visible_exposure": row["model_visible_exposure"],
            "selection_latency_ms": row["selection_latency_ms"],
            "selection_basis": json.loads(row["selection_basis_json"]),
        }
        return result


def _parse_frontmatter(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeDenied("SKILL_MD_NOT_UTF8") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RuntimeDenied("SKILL_FRONTMATTER_REQUIRED")
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() in {"---", "..."})
    except StopIteration as exc:
        raise RuntimeDenied("SKILL_FRONTMATTER_UNTERMINATED") from exc
    yaml_text = "\n".join(lines[1:closing])
    try:
        # Alias expansion and duplicate-key last-wins behavior make a package
        # less auditable, so reject both rather than accepting ambiguous YAML.
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(yaml_text)):
            raise RuntimeDenied("SKILL_FRONTMATTER_ALIAS_UNSUPPORTED")

        class UniqueKeyLoader(yaml.SafeLoader):
            pass

        def construct_mapping(loader, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in mapping:
                    raise RuntimeDenied("SKILL_FRONTMATTER_DUPLICATE_KEY")
                mapping[key] = loader.construct_object(value_node, deep=deep)
            return mapping

        UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
        frontmatter = yaml.load(yaml_text, Loader=UniqueKeyLoader)
    except RuntimeDenied:
        raise
    except yaml.YAMLError as exc:
        raise RuntimeDenied("SKILL_FRONTMATTER_MALFORMED") from exc
    if not isinstance(frontmatter, dict):
        raise RuntimeDenied("SKILL_FRONTMATTER_INVALID")
    for field in ("name", "description"):
        if not isinstance(frontmatter.get(field), str) or not frontmatter[field].strip():
            raise RuntimeDenied("SKILL_FRONTMATTER_FIELDS_REQUIRED")
    for field in ("license",):
        if field in frontmatter and (not isinstance(frontmatter[field], str) or not frontmatter[field].strip()):
            raise RuntimeDenied("SKILL_FRONTMATTER_OPTIONAL_FIELD_INVALID")
    if "compatibility" in frontmatter:
        compatibility = frontmatter["compatibility"]
        if not isinstance(compatibility, str) or not compatibility.strip() or len(compatibility) > 500:
            raise RuntimeDenied("SKILL_COMPATIBILITY_INVALID")
    if "metadata" in frontmatter:
        metadata = frontmatter["metadata"]
        if not isinstance(metadata, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                 for key, value in metadata.items()):
            raise RuntimeDenied("SKILL_METADATA_INVALID")
    if "allowed-tools" in frontmatter:
        allowed_tools = frontmatter["allowed-tools"]
        if not isinstance(allowed_tools, str) or (allowed_tools and not allowed_tools.split()):
            raise RuntimeDenied("SKILL_ALLOWED_TOOLS_INVALID")
    return {"name": frontmatter["name"].strip(), "description": frontmatter["description"].strip()}


def _terms(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[^\W_]+", value, flags=re.UNICODE) if token}
