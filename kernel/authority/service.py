from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from adapters.storage import ObjectStore

KERNEL_RECOVERY_PRINCIPAL_ID = "nexus-core-recovery"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _json_list(value: str) -> list[str]:
    result = json.loads(value)
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise InvalidDelegation("INVALID_SCOPE")
    return result


class AuthorityService:
    """Persisted grant chain and approval checks. Unknown data always denies."""

    def __init__(self, store: ObjectStore, policy: dict[str, Any], max_delegation_depth: int = 8):
        if max_delegation_depth < 1:
            raise ValueError("max_delegation_depth must be positive")
        self.store = store
        self.policy = json.loads(json.dumps(policy))
        self.max_delegation_depth = max_delegation_depth
        policy_schema_path = self.store.schema_dir.parent / "policies" / "nexus.policy@1.schema.json"
        policy_schema = json.loads(policy_schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(policy_schema)
        Draft202012Validator(policy_schema, format_checker=self.store._format_checker).validate(self.policy)

    def register_principal(self, principal: dict[str, Any], command_id: str) -> None:
        self.store._require_mode("core_write")
        self.store._validate("nexus.principal@1.schema.json", principal)
        operation = "register_principal"
        request_hash = self.store._request_hash(operation, principal)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                conn.execute("INSERT INTO principals(principal_id,principal_type,status) VALUES(?,?,?)", (principal["principal_id"], principal["principal_type"], principal["status"]))
                self.store._record_command(conn, command_id, operation, request_hash, {"principal_id": principal["principal_id"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def register_trust_anchor(self, anchor: dict[str, Any], command_id: str) -> None:
        self.store._require_mode("core_write")
        self.store._validate("nexus.trust_anchor@1.schema.json", anchor)
        if anchor["principal_id"] == KERNEL_RECOVERY_PRINCIPAL_ID:
            raise InvalidDelegation("KERNEL_RECOVERY_IDENTITY_RESERVED")
        if anchor["principal_id"] not in self.policy["trust_anchors"]:
            raise InvalidDelegation("TRUST_ANCHOR_NOT_CONFIGURED_BY_POLICY")
        if anchor["policy_ref"] != self.policy["policy_version"]:
            raise InvalidDelegation("TRUST_ANCHOR_POLICY_VERSION_MISMATCH")
        if anchor["policy_ref"] != self.policy["policy_version"]:
            raise InvalidDelegation("TRUST_ANCHOR_POLICY_VERSION_MISMATCH")
        operation = "register_trust_anchor"
        request_hash = self.store._request_hash(operation, anchor)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                principal = conn.execute("SELECT status FROM principals WHERE principal_id=?", (anchor["principal_id"],)).fetchone()
                if not principal or principal["status"] != "ACTIVE":
                    raise InvalidDelegation("TRUST_ANCHOR_PRINCIPAL_INACTIVE")
                conn.execute("INSERT INTO trust_anchors(anchor_id,principal_id,policy_ref) VALUES(?,?,?)", (anchor["anchor_id"], anchor["principal_id"], anchor["policy_ref"]))
                self.store._record_command(conn, command_id, operation, request_hash, {"anchor_id": anchor["anchor_id"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_grant(self, grant: dict[str, Any], command_id: str) -> None:
        self.store._require_mode("core_write")
        self.store._validate("nexus.delegation_grant@1.schema.json", grant)
        self._validate_grant_shape(grant)
        if KERNEL_RECOVERY_PRINCIPAL_ID in {grant["issued_by"], grant["granted_to"]}:
            raise InvalidDelegation("KERNEL_RECOVERY_IDENTITY_RESERVED")
        operation = "create_grant"
        request_hash = self.store._request_hash(operation, grant)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                for principal_id in (grant["issued_by"], grant["granted_to"]):
                    row = conn.execute("SELECT status FROM principals WHERE principal_id=?", (principal_id,)).fetchone()
                    if not row or row["status"] != "ACTIVE":
                        raise InvalidDelegation("PRINCIPAL_NOT_ACTIVE")
                parent_id = grant.get("parent_grant_id")
                if parent_id:
                    parent = conn.execute("SELECT * FROM delegation_grants WHERE grant_id=?", (parent_id,)).fetchone()
                    if not parent or parent["granted_to"] != grant["issued_by"] or parent["status"] != "ACTIVE":
                        raise InvalidDelegation("PARENT_GRANT_INVALID")
                    scope_columns = {"task_scope": "task_scope_json", "resource_scope": "resource_scope_json", "action_scope": "action_scope_json", "audience_scope": "audience_scope_json"}
                    if "DELEGATE" not in _json_list(parent["action_scope_json"]):
                        raise InvalidDelegation("PARENT_CANNOT_DELEGATE")
                    for field, column in scope_columns.items():
                        if not set(grant[field]).issubset(set(_json_list(parent[column]))):
                            raise InvalidDelegation("DELEGATION_SCOPE_EXPANSION")
                else:
                    anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (grant["issued_by"],)).fetchone()
                    if not anchor or anchor["policy_ref"] != self.policy["policy_version"] or grant["issued_by"] not in self.policy["trust_anchors"]:
                        raise InvalidDelegation("UNTRUSTED_ROOT")
                self._insert_grant(conn, grant)
                self._authority_event(conn, command_id, grant["grant_id"], "GRANT_CREATED", "GRANT_CREATED")
                self.store._record_command(conn, command_id, operation, request_hash, {"grant_id": grant["grant_id"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _validate_grant_shape(self, grant: dict[str, Any]) -> None:
        if grant["policy_version"] != self.policy["policy_version"]:
            raise InvalidDelegation("GRANT_POLICY_VERSION_MISMATCH")
        try:
            if _parse_time(grant["issued_at"]) >= _parse_time(grant["expires_at"]):
                raise InvalidDelegation("INVALID_GRANT_INTERVAL")
        except (ValueError, TypeError) as exc:
            raise InvalidDelegation("INVALID_GRANT_TIME") from exc
        if grant["status"] not in {"PROPOSED", "ACTIVE"}:
            raise InvalidDelegation("CANNOT_CREATE_TERMINAL_GRANT")

    @staticmethod
    def _insert_grant(conn, grant: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO delegation_grants(grant_id,parent_grant_id,issued_by,granted_to,task_scope_json,resource_scope_json,action_scope_json,audience_scope_json,issued_at,expires_at,status,policy_version,credential_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (grant["grant_id"], grant.get("parent_grant_id"), grant["issued_by"], grant["granted_to"], json.dumps(grant["task_scope"], sort_keys=True), json.dumps(grant["resource_scope"], sort_keys=True), json.dumps(grant["action_scope"], sort_keys=True), json.dumps(grant["audience_scope"], sort_keys=True), grant["issued_at"], grant["expires_at"], grant["status"], grant["policy_version"], grant.get("credential_ref")),
        )

    @staticmethod
    def _authority_event(conn, command_id: str, grant_id: str | None, event_type: str, reason_code: str) -> None:
        conn.execute("INSERT INTO authority_events(command_id,grant_id,event_type,reason_code,created_at) VALUES(?,?,?,?,?)", (command_id, grant_id, event_type, reason_code, _now().isoformat().replace("+00:00", "Z")))

    def activate_grant(self, grant_id: str, command_id: str) -> None:
        self.store._require_mode("core_write")
        self._transition_grant(grant_id, command_id, "PROPOSED", "ACTIVE", "GRANT_ACTIVATED")

    def revoke_grant(self, grant_id: str, command_id: str) -> None:
        self.store._require_mode("core_write")
        self._transition_grant(grant_id, command_id, "ACTIVE", "REVOKED", "GRANT_REVOKED")

    def revoke_principal(self, principal_id: str, command_id: str) -> None:
        self.store._require_mode("core_write")
        operation = "revoke_principal"
        request = {"principal_id": principal_id, "target_status": "REVOKED"}
        request_hash = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                row = conn.execute("SELECT status FROM principals WHERE principal_id=?", (principal_id,)).fetchone()
                if not row or row["status"] != "ACTIVE":
                    raise InvalidDelegation("INVALID_PRINCIPAL_TRANSITION")
                conn.execute("UPDATE principals SET status='REVOKED' WHERE principal_id=? AND status='ACTIVE'", (principal_id,))
                self._authority_event(conn, command_id, None, "PRINCIPAL_REVOKED", "PRINCIPAL_REVOKED")
                self.store._record_command(conn, command_id, operation, request_hash, {"principal_id": principal_id, "status": "REVOKED"})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _transition_grant(self, grant_id: str, command_id: str, expected: str, target: str, event: str) -> None:
        self.store._require_mode("core_write")
        operation = "transition_grant"
        request = {"grant_id": grant_id, "expected": expected, "target": target}
        request_hash = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                replay = self.store._replay_command(conn, command_id, operation, request_hash)
                if replay is not None:
                    conn.commit()
                    return
                row = conn.execute("SELECT status FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()
                if not row or row["status"] != expected:
                    raise InvalidDelegation("INVALID_GRANT_TRANSITION")
                conn.execute("UPDATE delegation_grants SET status=? WHERE grant_id=? AND status=?", (target, grant_id, expected))
                self._authority_event(conn, command_id, grant_id, event, event)
                self.store._record_command(conn, command_id, operation, request_hash, {"grant_id": grant_id, "status": target})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def validate_delegation_chain(self, grant_id: str) -> list[dict[str, Any]]:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            chain: list[dict[str, Any]] = []
            current_id: str | None = grant_id
            visited: set[str] = set()
            while current_id:
                if current_id in visited or len(chain) >= self.max_delegation_depth:
                    raise InvalidDelegation("DELEGATION_CYCLE_OR_DEPTH_EXCEEDED")
                visited.add(current_id)
                row = conn.execute("SELECT * FROM delegation_grants WHERE grant_id=?", (current_id,)).fetchone()
                if not row:
                    raise InvalidDelegation("GRANT_NOT_FOUND")
                grant = dict(row)
                if KERNEL_RECOVERY_PRINCIPAL_ID in {grant["issued_by"], grant["granted_to"]}:
                    raise InvalidDelegation("KERNEL_RECOVERY_IDENTITY_RESERVED")
                if grant["status"] != "ACTIVE":
                    raise InvalidDelegation("GRANT_NOT_ACTIVE")
                if grant["policy_version"] != self.policy["policy_version"]:
                    raise InvalidDelegation("GRANT_POLICY_VERSION_MISMATCH")
                now = _now()
                try:
                    issued, expires = _parse_time(grant["issued_at"]), _parse_time(grant["expires_at"])
                except (ValueError, TypeError) as exc:
                    raise InvalidDelegation("INVALID_GRANT_TIME") from exc
                if issued > now or expires <= now:
                    raise InvalidDelegation("GRANT_NOT_CURRENTLY_VALID")
                for principal_id in (grant["issued_by"], grant["granted_to"]):
                    principal = conn.execute("SELECT status FROM principals WHERE principal_id=?", (principal_id,)).fetchone()
                    if not principal or principal["status"] != "ACTIVE":
                        raise InvalidDelegation("PRINCIPAL_NOT_ACTIVE")
                for key in ("task_scope", "resource_scope", "action_scope", "audience_scope"):
                    grant[key] = _json_list(grant[f"{key}_json"])
                    del grant[f"{key}_json"]
                chain.append(grant)
                parent_id = grant["parent_grant_id"]
                if parent_id:
                    parent = conn.execute("SELECT granted_to FROM delegation_grants WHERE grant_id=?", (parent_id,)).fetchone()
                    if not parent or parent["granted_to"] != grant["issued_by"]:
                        raise InvalidDelegation("GRANT_CHAIN_DISCONTINUITY")
                else:
                    anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (grant["issued_by"],)).fetchone()
                    if not anchor or anchor["policy_ref"] != self.policy["policy_version"] or grant["issued_by"] not in self.policy["trust_anchors"]:
                        raise InvalidDelegation("UNTRUSTED_ROOT")
                current_id = parent_id
            chain.reverse()
            self._assert_chain_containment(chain)
            return chain

    @staticmethod
    def _assert_chain_containment(chain: list[dict[str, Any]]) -> None:
        keys = ("task_scope", "resource_scope", "action_scope", "audience_scope")
        for parent, child in zip(chain, chain[1:]):
            for key in keys:
                if not set(child[key]).issubset(set(parent[key])):
                    raise InvalidDelegation("DELEGATION_SCOPE_EXPANSION")

    def compute_effective_authority(self, grant_id: str) -> dict[str, set[str]]:
        self.store._require_mode("core_read")
        chain = self.validate_delegation_chain(grant_id)
        return {key: set(chain[-1][key]) for key in ("task_scope", "resource_scope", "action_scope", "audience_scope")}

    def evaluate_authorization(self, grant_id: str, request: dict[str, str], command_id: str, approval_id: str | None = None, payload_integrity_hash: str | None = None) -> bool:
        self.store._require_mode("core_read")
        try:
            effective = self.compute_effective_authority(grant_id)
            required = {"task": "task_scope", "resource": "resource_scope", "action": "action_scope", "audience": "audience_scope"}
            for field, scope in required.items():
                if not isinstance(request.get(field), str) or request[field] not in effective[scope]:
                    raise AuthorizationDenied("SCOPE_DENIED")
            if approval_id:
                self._validate_approval(approval_id, grant_id, request, payload_integrity_hash)
            return True
        except (InvalidDelegation, ApprovalDenied, AuthorizationDenied) as exc:
            self._record_denial(grant_id, command_id, str(exc) or type(exc).__name__)
            raise AuthorizationDenied(str(exc)) from exc

    def _validate_approval(self, approval_id: str, grant_id: str, request: dict[str, str], payload_hash: str | None) -> None:
        self.store._require_mode("core_read")
        with self.store._connection() as conn:
            approval = conn.execute("SELECT * FROM approval_decisions WHERE approval_id=?", (approval_id,)).fetchone()
            grant = conn.execute("SELECT granted_to FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()
        if not approval or not grant:
            raise ApprovalDenied("APPROVAL_NOT_FOUND")
        chain = self.validate_delegation_chain(grant_id)
        chain_principals = {principal_id for item in chain for principal_id in (item["issued_by"], item["granted_to"])}
        if approval["approver_principal_id"] not in chain_principals:
            raise ApprovalDenied("APPROVER_OUTSIDE_AUTHORITY_CHAIN")
        if approval["decision"] != "APPROVE":
            raise ApprovalDenied("APPROVAL_DENIED")
        if approval["policy_version"] != self.policy["policy_version"]:
            raise ApprovalDenied("APPROVAL_POLICY_VERSION_MISMATCH")
        if approval["expires_at"] and _parse_time(approval["expires_at"]) <= _now():
            raise ApprovalDenied("APPROVAL_EXPIRED")
        if approval["target_ref"] != request["resource"] or approval["target_type"] != request["action"]:
            raise ApprovalDenied("APPROVAL_TARGET_MISMATCH")
        if approval["effect_id"] != request.get("effect_id"):
            raise ApprovalDenied("APPROVAL_EFFECT_MISMATCH")
        if approval["payload_integrity_hash"] != payload_hash:
            raise ApprovalDenied("APPROVAL_PAYLOAD_HASH_MISMATCH")
        if not {request["action"], request["resource"]}.issubset(set(json.loads(approval["approved_scope_json"]))):
            raise ApprovalDenied("APPROVAL_SCOPE_MISMATCH")

    def create_approval(self, approval: dict[str, Any], command_id: str) -> None:
        self.store._require_mode("core_write")
        self.store._validate("nexus.approval_decision@1.schema.json", approval)
        if approval["policy_version"] != self.policy["policy_version"]:
            raise ApprovalDenied("APPROVAL_POLICY_VERSION_MISMATCH")
        if _parse_time(approval["issued_at"]) > _now():
            raise ApprovalDenied("APPROVAL_NOT_YET_VALID")
        if "expires_at" in approval and _parse_time(approval["expires_at"]) <= _parse_time(approval["issued_at"]):
            raise ApprovalDenied("INVALID_APPROVAL_INTERVAL")
        operation = "create_approval"
        request_hash = self.store._request_hash(operation, approval)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                approver = conn.execute("SELECT status,principal_type FROM principals WHERE principal_id=?", (approval["approver_principal_id"],)).fetchone()
                if not approver or approver["status"] != "ACTIVE" or approver["principal_type"] != "HUMAN":
                    raise ApprovalDenied("APPROVER_MUST_BE_ACTIVE_HUMAN")
                conn.execute("INSERT INTO approval_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (approval["approval_id"], approval["approver_principal_id"], approval["target_type"], approval["target_ref"], approval.get("effect_id"), approval.get("payload_integrity_hash"), approval["decision"], json.dumps(approval["approved_scope"], sort_keys=True), approval["policy_version"], approval["issued_at"], approval.get("expires_at"), approval.get("reason"), approval.get("request_ref")))
                self.store._record_command(conn, command_id, operation, request_hash, {"approval_id": approval["approval_id"], "decision": approval["decision"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _record_denial(self, grant_id: str, command_id: str, reason_code: str) -> None:
        operation = "authorization_denied"
        request = {"grant_id": grant_id, "reason_code": reason_code}
        request_hash = self.store._request_hash(operation, request)
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is None:
                    grant_exists = conn.execute("SELECT 1 FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()
                    self._authority_event(conn, command_id, grant_id if grant_exists else None, "AUTHORIZATION_DENIED", reason_code[:128])
                    self.store._record_command(conn, command_id, operation, request_hash, {"decision": "DENY", "reason_code": reason_code[:128]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def record_classification_assertion(self, assertion: dict[str, Any], *, grant_id: str, task_id: str, audience: str, command_id: str, approval_id: str | None = None) -> None:
        self.store._require_mode("core_write")
        self.store._validate("nexus.classification_assertion@1.schema.json", assertion)
        if assertion["policy_version"] != self.policy["policy_version"]:
            raise AuthorizationDenied("CLASSIFICATION_POLICY_VERSION_MISMATCH")
        if assertion["sensitivity_level"] not in self.policy["classification"]["sensitivity_rank"]:
            raise AuthorizationDenied("CLASSIFICATION_LEVEL_UNRANKED")
        chain = self.validate_delegation_chain(grant_id)
        if chain[-1]["granted_to"] != assertion["actor_id"]:
            raise AuthorizationDenied("CLASSIFICATION_ACTOR_MISMATCH")
        with self.store._connection() as conn:
            leaves = conn.execute(
                "SELECT c.* FROM classification_assertions c WHERE c.subject_type=? AND c.subject_ref=? "
                "AND NOT EXISTS (SELECT 1 FROM classification_assertions n WHERE n.supersedes=c.assertion_id)",
                (assertion["subject_type"], assertion["subject_ref"]),
            ).fetchall()
            prior = None
            if leaves:
                if len(leaves) != 1 or assertion.get("supersedes") != leaves[0]["assertion_id"]:
                    raise AuthorizationDenied("CLASSIFICATION_SUPERSEDES_REQUIRED")
                prior = leaves[0]
            elif assertion.get("supersedes"):
                raise AuthorizationDenied("CLASSIFICATION_SUPERSEDES_MISMATCH")
        lowering = False
        if prior:
            rank = self.policy["classification"]["sensitivity_rank"]
            old_tags = set(json.loads(prior["handling_tags_json"]))
            lowering = rank[assertion["sensitivity_level"]] < rank[prior["sensitivity_level"]] or not old_tags.issubset(set(assertion["handling_tags"]))
        action = "CLASSIFICATION_LOWER" if lowering else "CLASSIFY"
        if lowering and not approval_id:
            raise ApprovalDenied("CLASSIFICATION_LOWER_REQUIRES_APPROVAL")
        request = {"task": task_id, "resource": assertion["subject_ref"], "action": action, "audience": audience}
        self.evaluate_authorization(grant_id, request, command_id + "-authorize", approval_id)
        operation = "record_classification_assertion"
        request_hash = self.store._request_hash(operation, {"assertion": assertion, "grant_id": grant_id})
        with self.store._lock, self.store._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if self.store._replay_command(conn, command_id, operation, request_hash) is not None:
                    conn.commit()
                    return
                conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?,?,?)", (assertion["assertion_id"], assertion["subject_type"], assertion["subject_ref"], assertion["sensitivity_level"], json.dumps(assertion["handling_tags"], sort_keys=True), assertion["policy_version"], assertion["reason"], assertion["actor_id"], assertion.get("supersedes")))
                self.store._record_command(conn, command_id, operation, request_hash, {"assertion_id": assertion["assertion_id"]})
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def effective_classification(self, assertion_ids: list[str]) -> tuple[str, set[str]] | None:
        self.store._require_mode("core_read")
        if not assertion_ids:
            return None
        with self.store._connection() as conn:
            rows = []
            for assertion_id in sorted(set(assertion_ids)):
                row = conn.execute("SELECT sensitivity_level,handling_tags_json FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
                if not row:
                    return None
                rows.append(row)
        ranks = self.policy["classification"]["sensitivity_rank"]
        if any(row["sensitivity_level"] not in ranks for row in rows):
            return None
        level = max((row["sensitivity_level"] for row in rows), key=lambda item: ranks[item])
        tags = set().union(*(set(json.loads(row["handling_tags_json"])) for row in rows))
        return level, tags

    def evaluate_egress(self, destination: str, object_ids: list[str]) -> bool:
        self.store._require_mode("egress")
        if not object_ids:
            return False
        assertion_ids: list[str] = []
        with self.store._connection() as conn:
            for object_id in sorted(set(object_ids)):
                row = conn.execute("SELECT classification_assertion_ref FROM object_envelopes WHERE object_id=?", (object_id,)).fetchone()
                if not row:
                    return False
                assertion_ids.append(row["classification_assertion_ref"])
        effective = self.effective_classification(assertion_ids)
        if not effective:
            return False
        sensitivity_level, handling_tags = effective
        classification = self.policy["classification"]
        if sensitivity_level not in classification["sensitivity_rank"]:
            return False
        rule = self.policy["egress"]["allowed_destinations"].get(destination)
        if rule is None:
            return False
        if set(handling_tags).intersection(classification["restrictive_tags"]):
            return False
        maximum = classification["sensitivity_rank"].get(rule.get("max_sensitivity"))
        actual = classification["sensitivity_rank"][sensitivity_level]
        return maximum is not None and actual <= maximum and set(handling_tags).issubset(set(rule.get("accepted_tags", [])))

    def authorize_egress(self, *, grant_id: str, task_id: str, destination: str, audience: str, object_ids: list[str], command_id: str) -> bool:
        self.store._require_mode("egress")
        request = {"task": task_id, "resource": destination, "action": "EGRESS", "audience": audience}
        self.evaluate_authorization(grant_id, request, command_id)
        if not self.evaluate_egress(destination, object_ids):
            self._record_denial(grant_id, command_id + "-egress-denied", "EGRESS_POLICY_DENIED")
            raise AuthorizationDenied("EGRESS_POLICY_DENIED")
        return True
