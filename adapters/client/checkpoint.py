"""One HUMAN authorization composing the reviewed daily continuation workflows.

Frozen plans live in an explicit host-local receipt. Only an exact canonical
CommandLedger binding proves authorization; a receipt alone never does.
No storage queries or new canonical persistence are introduced here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.client import continuation as continuation
from adapters.client import task_finish as finish
from adapters.client import task_start as start
from adapters.client.host_file_lock import path_mutation_lock
from adapters.client.presence import _read_continuity_documents
from adapters.client.project_locator import locate_project, resolve_project_registry_path, _write_atomic, _has_symlink_component
from adapters.panel.application import open_panel_application
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.context import ContextPackService
from kernel.experience import ExperienceProjectionService, LocalOperatorReadContext
from kernel.memory.service import MemoryService
from kernel.metering import MeteringService
from kernel.object.errors import CommandConflict
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.runtime.panel import PanelQueryService
from kernel.verification import VerificationService


PROTOCOL = "nexus.checkpoint@1"
OPERATION = "checkpoint_request"
_SEMANTICS = {"accepted_revision", "current_objective", "next_step", "recent_work"}


class CheckpointError(Exception):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason):
    raise CheckpointError(reason)


def _bytes(value):
    return continuation._canonical_json(value)


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def validate_proposal(value):
    if not isinstance(value, dict) or set(value) != _SEMANTICS:
        _fail("CHECKPOINT_PROPOSAL_INVALID")
    if not isinstance(value["accepted_revision"], str) or not continuation._GIT_OID.fullmatch(value["accepted_revision"]):
        _fail("CHECKPOINT_PROPOSAL_INVALID")
    for key in _SEMANTICS - {"accepted_revision"}:
        continuation._text(value[key], "CHECKPOINT_PROPOSAL_INVALID")
    return copy.deepcopy(value)


def read_checkpoint_proposal(path):
    try:
        return validate_proposal(json.loads(Path(path).read_bytes().decode("utf-8"),
            object_pairs_hook=continuation._pairs_no_duplicates,
            parse_constant=continuation._reject_constant))
    except CheckpointError:
        raise
    except Exception:
        _fail("CHECKPOINT_PROPOSAL_INVALID")


def _identity(binding, proposal, checkpoint_id):
    identity = checkpoint_id or "cp-" + _digest(proposal)[:24]
    if not start._logical_id(identity) or len(identity) > 48:
        _fail("CHECKPOINT_ID_INVALID")
    # Short internal IDs leave space for every existing nested command suffix.
    key = _digest({"instance": binding["instance_id"], "project": binding["project_id"], "id": identity})[:24]
    return identity, "cp-" + key


def _idle(store):
    overview = PanelQueryService(store).snapshot()["overview"]
    if any(overview[key] for key in ("unfinished_task_count", "active_run_count", "pending_effect_count")):
        _fail("CHECKPOINT_PROJECT_NOT_IDLE")


def compose_services(store):
    authority = AuthorityService(store, store.policy)
    budget = BudgetService(store)
    trace = TraceRuntime(store, authority)
    participation = ParticipationModeService(store)
    context = ContextPackService(store=store, authority=authority, participation=participation,
        memory=MemoryService(store, authority, verifier=None),
        metering=MeteringService(store, authority, participation))
    return dict(store=store, authority=authority, budget=budget, trace=trace,
        runtime=DeterministicRuntime(store, authority, budget, trace),
        verifier=VerificationService(store, authority), context_packs=context)


def build_checkpoint(*, store, authority, context_packs, binding, proposal, checkpoint_id=None):
    """Freeze plans from an already verified, strictly read-only application."""
    if not store.read_only:
        _fail("CHECKPOINT_READ_ONLY_PREFLIGHT_REQUIRED")
    proposal = validate_proposal(proposal)
    if binding["project_id"] != "project-nexus":
        # The accepted Continuation protocol is currently Project Nexus specific.
        _fail("CHECKPOINT_PROJECT_UNSUPPORTED")
    _idle(store)
    identity, prefix = _identity(binding, proposal, checkpoint_id)
    latest = context_packs.latest()
    pack = context_packs.read_compiled(latest.get("pack_id"))
    state, delta, refs = _read_continuity_documents(pack)
    if state is None:
        _fail("CHECKPOINT_CURRENT_STATE_UNAVAILABLE")
    pointer = continuation._read_current_ref(store)
    if pointer and pointer["current_object_id"] != refs["current_state"]:
        _fail("CONTINUATION_STATE_CONFLICT")
    # Reuse the authorized read facade and existing Task/Grant binding reader.
    experience = ExperienceProjectionService(store, read_context=LocalOperatorReadContext(),
        context_packs=context_packs).project_task(pack["task_id"])
    prior_root = next((row for row in experience["lifecycle"]["runs"]["items"]
                       if row["run_id"] == pack["run_id"]), None)
    if prior_root is None:
        _fail("CHECKPOINT_OPERATOR_BINDING_UNAVAILABLE")
    prior = finish._load_projection(store, {"task_id": pack["task_id"], "run_id": pack["run_id"],
        "grant_id": prior_root["grant_id"]})
    operator, runtime = prior["task"]["requester_id"], prior["grant"]["granted_to"]
    boundary = prior["boundary"]
    instance = {key: binding[key] for key in ("instance_id", "policy_version", "policy_sha256", "journal_identity")}
    # Stable sources are precisely the previous verified entries, excluding the
    # two changing continuity documents. No history import or extra discovery.
    stable = sorted((entry for entry in pack["entries"]
        if entry["source_ref"] not in {refs["current_state"], refs.get("what_changed")}
        and entry["source_type"] in continuation._STABLE_SOURCE_TYPES), key=lambda entry: entry["source_ref"].encode("utf-8"))
    now = datetime.now(timezone.utc)
    text_time = lambda value: value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    created = text_time(now)
    task, run, grant = (prefix + suffix for suffix in ("-task", "-run", "-grant"))
    start_command, commit_command, finish_command = (prefix + suffix for suffix in ("-start", "-commit", "-finish"))
    # Use the previous state's classification; _build_documents additionally
    # checks every stable source and prohibits any output downgrade.
    entry = continuation._pack_entry(pack, refs["current_state"])
    classification = continuation._source_classification(store, refs["current_state"],
        entry["classification_assertion_ref"], boundary)["classification"]
    levels = {classification["sensitivity_level"]}
    tags = set(classification["handling_tags"])
    for source in stable:
        info = continuation._source_classification(store, source["source_ref"],
            source["classification_assertion_ref"], boundary)["classification"]
        levels.add(info["sensitivity_level"])
        tags.update(info["handling_tags"])
    ranks = store.policy["classification"]["sensitivity_rank"]
    level = max(levels, key=lambda item: ranks[item])

    def assertion(suffix, subject_type, subject_ref):
        return {"schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": prefix + "-class-" + suffix, "subject_type": subject_type,
            "subject_ref": subject_ref, "sensitivity_level": level, "handling_tags": sorted(tags),
            "policy_version": instance["policy_version"], "reason": "HUMAN-authorized checkpoint composition.",
            "actor_id": runtime}

    root_command = start_command + ":root"
    input_id, contract_id, manifest_id = (prefix + suffix for suffix in ("-input", "-contract", "-manifest"))
    classes = {
        "root_run": assertion("run", "RUN", run),
        "root_created_event": assertion("created", "TRACE_EVENT", "evt-" + root_command + "-root-create"),
        "input_object": assertion("input", "OBJECT", input_id),
        "input_event": assertion("input-event", "TRACE_EVENT", "evt-" + root_command + "-trace-input"),
        "task_contract": assertion("contract", "OBJECT", contract_id),
        "root_manifest": assertion("manifest", "OBJECT", manifest_id),
        "root_ready_event": assertion("ready", "TRACE_EVENT", "evt-" + root_command + "-root-ready"),
        "root_running_event": assertion("running", "TRACE_EVENT", "evt-" + root_command + "-root-running"),
    }
    git_repo = binding["project_root"]
    git = {"repository_id": "project-nexus-repo", "branch": continuation._run_git(git_repo, ["branch", "--show-current"]),
        "commit_sha": proposal["accepted_revision"], "worktree_state": "CLEAN",
        "upstream_ref": continuation._run_git(git_repo, ["rev-parse", "--abbrev-ref", "@{u}"]),
        "upstream_commit_sha": continuation._run_git(git_repo, ["rev-parse", "@{u}"])}
    commit_plan = {"protocol_version": continuation.PROTOCOL_VERSION, "command_id": commit_command,
        "instance_expectation": instance, "operator_principal_id": operator, "task_id": task,
        "root_run_id": run, "grant_id": grant, "as_of": created, "git_fact": git,
        "human_assertions": {key: proposal[key] for key in _SEMANTICS - {"accepted_revision"}},
        "expected_previous_state": {"ref_id": continuation.CURRENT_STATE_REF,
            "object_id": refs["current_state"], "integrity_sha256": refs["current_state_hash"],
            "current_ref_revision": pointer["revision"] if pointer else None,
            "accepted_revision": continuation._canonical_prior_revision(state)},
        "expected_previous_context": {"pack_ref": pack["pack_id"], "content_hash": pack["content_hash"],
            "integrity_sha256": pack["integrity_hash"]},
        "outputs": {"current_state_object_id": prefix + "-state", "what_changed_object_id": prefix + "-delta",
            "context_pack_object_id": prefix + "-context", **{
                key: {"assertion_id": prefix + "-class-" + suffix, "sensitivity_level": level,
                      "handling_tags": sorted(tags)} for key, suffix in (
                    ("current_state_classification", "state"), ("what_changed_classification", "delta"),
                    ("context_pack_classification", "context"))}},
        "stable_sources": [{"source_ref": source["source_ref"], "snapshot_object_id": prefix + f"-snapshot-{index}",
            "classification_assertion_id": prefix + f"-class-snapshot-{index}"} for index, source in enumerate(stable)]}
    normalized_commit = continuation._validate_plan_shape(store, authority, commit_plan)
    resources = continuation._resource_closure(normalized_commit, refs["current_state"])
    resources.update({f"evt-{finish_command}:verifying", f"evt-{finish_command}:terminal"})
    # ObjectStore has a unique payload URI boundary. Distinct milestones may
    # have identical product semantics, so their governed input includes identity.
    start_plan = {"protocol_version": start.PROTOCOL_VERSION, "command_id": start_command,
        "instance_expectation": instance, "operator_principal_id": operator, "runtime_principal_id": runtime,
        "grant": {"grant_id": grant, "issued_at": created, "expires_at": text_time(now + timedelta(hours=2)),
            "action_scope": ["CLASSIFY", "INSPECT", "OBJECT_WRITE", "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND"],
            "audience_scope": ["nexus-inspect", "nexus-runtime"], "additional_resource_scope": sorted(resources)},
        "root": {"created_at": created, "task_id": task, "requester_id": operator, "root_run_id": run,
            "budget_account_id": prefix + "-budget", "budget_limits": {"amount_limit": 1,
                "unit": "operator-work-units", "model_call_limit": 0, "tool_call_limit": 0, "child_run_limit": 0},
            "input_object_id": input_id, "input_payload": _bytes({"checkpoint_id": identity,
                "request_key": prefix, "proposal": proposal}).decode("utf-8"),
            "task_contract": {"schema_id": "nexus.task_contract", "schema_version": 1, "task_id": task,
                "requester_id": operator, "goal": "Record one explicitly HUMAN-authorized project checkpoint.",
                "constraints": ["No implementation or external effects; exact frozen continuation only."],
                "success_criteria": ["Continuation committed and daily Task finished SUCCEEDED."],
                "risk_class": "LOW", "budget_account_ref": prefix + "-budget",
                "routing_constraints": {"allowed_providers": [], "forbidden_providers": [], "locality": "LOCAL_ONLY",
                    "network_required": False, "modalities": []}, "routing_preferences": {"optimize_for": "BALANCED"},
                "created_at": created}, "contract_object_id": contract_id, "dag_nodes": [],
            "root_manifest_object_id": manifest_id, "data_boundary": boundary, "classifications": classes}}
    finish_plan = {"protocol_version": finish.PROTOCOL_VERSION, "command_id": finish_command,
        "instance_expectation": instance, "operator_principal_id": operator, "task_id": task,
        "root_run_id": run, "grant_id": grant, "outcome": "SUCCEEDED", "classifications": {
            "verifying_event": assertion("finish-verifying", "TRACE_EVENT", f"evt-{finish_command}:verifying"),
            "terminal_event": assertion("finish-terminal", "TRACE_EVENT", f"evt-{finish_command}:terminal")}}
    envelope = {"protocol_version": PROTOCOL, "checkpoint_id": identity, "project_id": binding["project_id"],
        "command_id": prefix + ":request", "proposal": proposal,
        "start": start_plan, "continuation": commit_plan, "finish": finish_plan}
    preflight_checkpoint(store=store, authority=authority, context_packs=context_packs, envelope=envelope,
        git_repo=git_repo)
    return envelope


def _normalized(store, authority, envelope):
    if (not isinstance(envelope, dict) or set(envelope) != {"protocol_version", "checkpoint_id", "project_id",
            "command_id", "proposal", "start", "continuation", "finish"} or envelope["protocol_version"] != PROTOCOL
            or envelope["project_id"] != "project-nexus"):
        _fail("CHECKPOINT_RECEIPT_INVALID")
    validate_proposal(envelope["proposal"])
    s = start._validate_plan(store, authority, envelope["start"])
    c = continuation._validate_plan_shape(store, authority, envelope["continuation"])
    f = finish._validate_plan(store, authority, envelope["finish"])
    start._validate_binding(store, s["instance_expectation"])
    identity, prefix = _identity({"instance_id": s["instance_expectation"]["instance_id"],
        "project_id": envelope["project_id"]}, envelope["proposal"], envelope["checkpoint_id"])
    if envelope["command_id"] != prefix + ":request":
        _fail("CHECKPOINT_RECEIPT_INVALID")
    bound = start._replay(store, envelope["command_id"], OPERATION, envelope)
    if bound not in (None, {"status": "REQUEST_BOUND"}):
        _fail("CHECKPOINT_REQUEST_BINDING_INVALID")
    if any((item["instance_expectation"], item["operator_id"], item["task_id"], item["run_id"], item["grant_id"])
        != (s["instance_expectation"], s["operator_id"], s["root"]["task_id"], s["root"]["root_run_id"], s["grant"]["grant_id"])
        for item in (c, f)) or f["outcome"] != "SUCCEEDED":
        _fail("CHECKPOINT_RECEIPT_INVALID")
    # Checkpoint acceptance policy applies to frozen receipts too, including retries.
    # Compare bound facts here; completed historical replay must not reread live Git.
    if not (c["git_fact"]["commit_sha"] == c["git_fact"]["upstream_commit_sha"]
            == envelope["proposal"]["accepted_revision"]):
        _fail("CHECKPOINT_GIT_REVISION_NOT_UPSTREAM")
    if (c["human_assertions"] != {
        key: envelope["proposal"][key] for key in _SEMANTICS - {"accepted_revision"}}):
        _fail("CHECKPOINT_RECEIPT_INVALID")
    if (s["command_id"] != prefix + "-start" or c["command_id"] != prefix + "-commit"
            or f["command_id"] != prefix + "-finish"
            or s["root"]["task_id"] != prefix + "-task" or s["root"]["root_run_id"] != prefix + "-run"
            or s["grant"]["grant_id"] != prefix + "-grant"
            or s["root"]["input_payload"] != _bytes({"checkpoint_id": identity, "request_key": prefix,
                "proposal": envelope["proposal"]}).decode("utf-8")
            or s["root"]["dag_nodes"] != []
            or s["root"]["budget_limits"] != {"amount_limit": 1, "unit": "operator-work-units",
                "model_call_limit": 0, "tool_call_limit": 0, "child_run_limit": 0}
            or s["grant"]["action_scope"] != ["CLASSIFY", "INSPECT", "OBJECT_WRITE", "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND"]
            or s["grant"]["audience_scope"] != ["nexus-inspect", "nexus-runtime"]
            or s["root"]["created_at"] != c["as_of"] or s["grant"]["issued_at"] != c["as_of"]
            or s["expires"] - s["issued"] != timedelta(hours=2)):
        _fail("CHECKPOINT_RECEIPT_INVALID")
    required = continuation._resource_closure(c, c["prior"]["object_id"]) | set(f["event_ids"])
    if set(envelope["start"]["grant"]["additional_resource_scope"]) != required:
        _fail("CHECKPOINT_RECEIPT_INVALID")
    if any(item["actor_id"] != s["runtime_id"] for item in f["classifications"].values()):
        _fail("CHECKPOINT_RECEIPT_INVALID")
    commands = [envelope["command_id"], *s["command_ids"], *c["command_ids"], *f["commands"]]
    classes = [item["assertion_id"] for item in s["root"]["classifications"].values()]
    classes += [item["assertion_id"] for item in c["classes"].values()]
    classes += [item["classification_assertion_id"] for item in c["stable_sources"]]
    classes += [item["assertion_id"] for item in f["classifications"].values()]
    if len(set(commands)) != len(commands) or len(set(classes)) != len(classes):
        _fail("CHECKPOINT_ID_CONFLICT")
    return s, c, f, bound is not None


def _planned_documents(store, s, c, context_packs):
    pack, entry, doc = continuation._prior_context(context_packs, c)
    projection = {"boundary": s["root"]["data_boundary"], "runtime_principal_id": s["runtime_id"],
        "scope": s["grant"], "task": {"status": "ACTIVE"}, "run": {"status": "RUNNING"}}
    documents = continuation._build_documents(store, c, projection, entry, doc, pack)
    continuation._validate_scope(projection, c, c["prior"]["object_id"])
    return documents


def preflight_checkpoint(*, store, authority, context_packs, envelope, git_repo):
    """Pure read gates. Prospective plans use existing protocol validators."""
    s, c, f, bound = _normalized(store, authority, envelope)
    if not bound:
        _idle(store)
        start._validate_current_authority(store, authority, s, require_current_created_at=True)
        start._assert_ids_unused_or_replay(store, s, request_bound=False, grant_replay=False)
        finish._assert_command_namespace(store, f, request_bound=False)
        continuation._validate_modes(store)
        continuation._validate_git_fact(git_repo, c["git_fact"])
        continuation._check_pointer(store, c, request_bound=False)
        latest = context_packs.latest()
        if latest.get("pack_id") != c["prior_context"]["pack_ref"]:
            _fail("CONTINUATION_CONTEXT_CONFLICT")
        if c["as_of_dt"] <= continuation._timestamp(latest["compiled_at"]):
            _fail("CONTINUATION_TIMESTAMP_NOT_MONOTONIC")
        pack, entry, prior = continuation._prior_context(context_packs, c)
        if prior.get("as_of") and c["as_of_dt"] <= continuation._timestamp(prior["as_of"]):
            _fail("CONTINUATION_TIMESTAMP_NOT_MONOTONIC")
        documents = _planned_documents(store, s, c, context_packs)
        continuation._assert_resource_barriers(store, c, c["prior"]["object_id"])
        continuation._assert_ids_and_outputs(store, c, documents, request_bound=False)
        # Cross-stage object IDs and classes must also be distinct.
        first_ids = {s["root"][key] for key in ("input_object_id", "contract_object_id", "root_manifest_object_id")}
        other_ids = set(c["output_ids"].values()) | {item["snapshot_object_id"] for item in c["stable_sources"]}
        if first_ids & other_ids:
            _fail("CHECKPOINT_ID_CONFLICT")
        return {"request_bound": False, "completed": False}
    # After binding, each reviewed stage remains responsible for exact command
    # proof and current authority before any forward work. No stale-plan rewrite.
    root = start._replay(store, s["root_command_id"], "create_hosted_task_root", start._bridge_request(store, s))
    if root is None:
        _idle_or_owned_partial(store, s)
        start._validate_current_authority(store, authority, s, require_current_created_at=False)
        continuation._check_pointer(store, c, request_bound=False)
        if context_packs.latest().get("pack_id") != c["prior_context"]["pack_ref"]:
            _fail("CONTINUATION_CONTEXT_CONFLICT")
        continuation._validate_git_fact(git_repo, c["git_fact"])
        return {"request_bound": True, "completed": False}
    if (start._replay(store, s["request_command_id"], "daily_task_start_request", s["request"]) != {"status": "REQUEST_BOUND"}
            or start._replay(store, s["grant_command_id"], "create_grant", s["grant"]) is None
            or root.get("task_id") != s["root"]["task_id"] or root.get("root_run_id") != s["root"]["root_run_id"]
            or root.get("status") != "RUNNING" or root.get("executor_kind") != "ORCHESTRATOR"):
        _fail("CHECKPOINT_COMPLETION_INVALID")
    start._assert_ids_unused_or_replay(store, s, request_bound=True, grant_replay=True)
    # A complete Continuation can be replayed even while Finish is VERIFYING.
    prepared = continuation.prepare_continuation_commit(store=store, authority=authority,
        context_packs=context_packs, plan=envelope["continuation"], git_repo=git_repo)
    if not prepared["completed"]:
        return {"request_bound": True, "completed": False}
    projection = finish._load_projection(store, f)
    finish._validate_task_and_grant_identity(authority, store, f, projection, require_active=False)
    if projection["run"]["status"] != "SUCCEEDED" or projection["grant"]["status"] != "REVOKED":
        return {"request_bound": True, "completed": False}
    if not finish._ensure_exact_request(store, f):
        _fail("CHECKPOINT_COMPLETION_INVALID")
    finish._assert_command_namespace(store, f, request_bound=True)
    for stage, before, after, key in (("verifying", "RUNNING", "VERIFYING", "verifying_event"),
                                     ("terminal", "VERIFYING", "SUCCEEDED", "terminal_event")):
        if finish._transition_committed(store, f, stage, before, after, f["classifications"][key]) is None:
            _fail("CHECKPOINT_COMPLETION_INVALID")
    finish._projection_matches_terminal(store, f, projection)
    if finish._replay(store, f["command_id"] + ":revoke", "transition_grant",
        {"grant_id": f["grant_id"], "expected": "ACTIVE", "target": "REVOKED"}) != {
            "grant_id": f["grant_id"], "status": "REVOKED"}:
        _fail("CHECKPOINT_COMPLETION_INVALID")
    return {"request_bound": True, "completed": True}


def _idle_or_owned_partial(store, s):
    # A partially created root may already exist; only that Task may be open.
    snapshot = PanelQueryService(store).snapshot()
    if snapshot["overview"]["pending_effect_count"] or any(
        task["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"} and task["task_id"] != s["root"]["task_id"]
        for task in snapshot["tasks"]):
        _fail("CHECKPOINT_PROJECT_NOT_IDLE")
    if any(run["status"] in {"CREATED", "READY", "RUNNING", "WAITING", "VERIFYING"}
           and run["run_id"] != s["root"]["root_run_id"]
           for task in snapshot["tasks"] for run in task["runs"]):
        _fail("CHECKPOINT_PROJECT_NOT_IDLE")


def checkpoint_summary(envelope):
    c = envelope["continuation"]
    revision = c["expected_previous_state"]["current_ref_revision"]
    return {"project_id": envelope["project_id"], "previous_revision": revision,
        "new_revision": revision + 1 if revision else 2, **envelope["proposal"],
        "outcome": "SUCCEEDED", "authorization": "Task Start -> Continuation Commit -> Task Finish"}


def _result(envelope, replayed):
    c = envelope["continuation"]
    return {"status": "CHECKPOINT_COMPLETED", "checkpoint_id": envelope["checkpoint_id"],
        "state_revision": checkpoint_summary(envelope)["new_revision"],
        "accepted_revision": envelope["proposal"]["accepted_revision"],
        "current_state_ref": c["outputs"]["current_state_object_id"],
        "context_pack_ref": c["outputs"]["context_pack_object_id"],
        "task_outcome": "SUCCEEDED", "run_outcome": "SUCCEEDED", "replayed": replayed}


def execute_checkpoint(*, envelope, git_repo, confirmation, **services):
    """Application API; only the whole frozen request can authorize subphases."""
    store, authority, context = services["store"], services["authority"], services["context_packs"]
    bound = False
    try:
        # Know whether a failure is resumable even when a current-authority or
        # concurrency gate fails during preflight. Conflict is never resumable.
        bound = _normalized(store, authority, envelope)[3]
        gates = preflight_checkpoint(store=store, authority=authority, context_packs=context,
            envelope=envelope, git_repo=git_repo)
        bound = gates["request_bound"]
        if gates["completed"]:
            return _result(envelope, True)
        if not bound:
            frozen_digest = _digest(envelope)
            if not confirmation("CHECKPOINT " + envelope["project_id"], checkpoint_summary(envelope)):
                _fail("CHECKPOINT_CONFIRMATION_DENIED")
            if _digest(envelope) != frozen_digest:
                _fail("COMMAND_CONFLICT")
            preflight_checkpoint(store=store, authority=authority, context_packs=context,
                envelope=envelope, git_repo=git_repo)
            store.bind_command_request(command_id=envelope["command_id"], operation=OPERATION, request=envelope)
            bound = True

        def reuse_authorization(_phrase, _summary):
            # Not automatic approval: exact durable evidence of the one HUMAN
            # authorization is required at every subworkflow boundary.
            if start._replay(store, envelope["command_id"], OPERATION, envelope) != {"status": "REQUEST_BOUND"}:
                _fail("CHECKPOINT_REQUEST_BINDING_INVALID")
            return True

        start.start_daily_task(**{key: services[key] for key in (
            "store", "authority", "budget", "trace", "runtime", "verifier")},
            plan=envelope["start"], confirmation=reuse_authorization)
        continuation.commit_continuation(store=store, authority=authority, context_packs=context,
            plan=envelope["continuation"], git_repo=git_repo, confirmation=reuse_authorization)
        finish.finish_daily_task(store=store, authority=authority, trace=services["trace"],
            plan=envelope["finish"], confirmation=reuse_authorization)
        if not preflight_checkpoint(store=store, authority=authority, context_packs=context,
            envelope=envelope, git_repo=git_repo)["completed"]:
            _fail("CHECKPOINT_COMPLETION_INVALID")
        return _result(envelope, False)
    except Exception as exc:
        reason = getattr(exc, "reason_code", "COMMAND_CONFLICT" if isinstance(exc, CommandConflict) else "CHECKPOINT_EXECUTION_FAILED")
        if not isinstance(reason, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason) is None:
            reason = "CHECKPOINT_EXECUTION_FAILED"
        if reason == "COMMAND_CONFLICT":
            _fail(reason)
        if not bound:
            # The ledger commit itself may have succeeded before its response
            # was lost. Recover the authorization proof, never assume zero writes.
            try:
                bound = start._replay(store, envelope["command_id"], OPERATION, envelope) == {"status": "REQUEST_BOUND"}
            except CommandConflict:
                _fail("COMMAND_CONFLICT")
            except Exception:
                pass
        if bound:
            return {"status": "CHECKPOINT_PARTIAL_RESUMABLE", "checkpoint_id": envelope["checkpoint_id"], "reason": reason}
        raise


def checkpoint_project(*, proposal, checkpoint_id=None, start_dir=None, confirmation):
    """Host transaction: serialize, read-only preflight, confirm, persist, write."""
    proposal = validate_proposal(proposal)
    binding = locate_project(start_dir=start_dir)
    identity, prefix = _identity(binding, proposal, checkpoint_id)
    directory = resolve_project_registry_path().parent / "checkpoints-v1" / binding["instance_id"]
    receipt = directory / (prefix + ".json")
    if _has_symlink_component(directory):
        _fail("CHECKPOINT_RECEIPT_INVALID")
    # Serialize Checkpoints for the instance, including confirmation; no stale
    # pre-lock state or concurrently confirmed Checkpoint may overwrite another.
    with path_mutation_lock(directory / "mutation"):
        if _has_symlink_component(directory):
            _fail("CHECKPOINT_RECEIPT_INVALID")
        if locate_project(start_dir=start_dir) != binding:
            _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
        kwargs = {"data_root": binding["data_root"], "policy_path": binding["policy_path"],
            "independent_purge_journal_path": binding["independent_purge_journal_path"]}
        app = open_panel_application(**kwargs, read_only=True)
        try:
            if receipt.is_symlink():
                _fail("CHECKPOINT_RECEIPT_INVALID")
            if receipt.exists():
                try:
                    envelope = json.loads(receipt.read_bytes().decode("utf-8"),
                        object_pairs_hook=continuation._pairs_no_duplicates, parse_constant=continuation._reject_constant)
                except Exception:
                    _fail("CHECKPOINT_RECEIPT_INVALID")
                if not isinstance(envelope, dict):
                    _fail("CHECKPOINT_RECEIPT_INVALID")
                if envelope.get("proposal") != proposal or envelope.get("checkpoint_id") != identity:
                    _fail("COMMAND_CONFLICT")
            else:
                envelope = build_checkpoint(store=app.store, authority=AuthorityService(app.store, app.store.policy),
                    context_packs=app.context_packs, binding=binding, proposal=proposal, checkpoint_id=identity)
            bound = _normalized(app.store, AuthorityService(app.store, app.store.policy), envelope)[3]
            try:
                gates = preflight_checkpoint(store=app.store, authority=AuthorityService(app.store, app.store.policy),
                    context_packs=app.context_packs, envelope=envelope, git_repo=binding["project_root"])
            except Exception as exc:
                if bound and getattr(exc, "reason_code", None) != "COMMAND_CONFLICT" and not isinstance(exc, CommandConflict):
                    return {"status": "CHECKPOINT_PARTIAL_RESUMABLE", "checkpoint_id": identity,
                        "reason": getattr(exc, "reason_code", "CHECKPOINT_PREFLIGHT_FAILED")}
                raise
            if gates["completed"]:
                return _result(envelope, True)
            authorized_digest = _digest(envelope)
            if not gates["request_bound"]:
                if not confirmation("CHECKPOINT " + binding["project_id"], checkpoint_summary(envelope)):
                    _fail("CHECKPOINT_CONFIRMATION_DENIED")
                if _digest(envelope) != authorized_digest:
                    _fail("COMMAND_CONFLICT")
                preflight_checkpoint(store=app.store, authority=AuthorityService(app.store, app.store.policy),
                    context_packs=app.context_packs, envelope=envelope, git_repo=binding["project_root"])
                if not receipt.exists():
                    _write_atomic(receipt, _bytes(envelope))
        finally:
            app.close()
        # Writer startup is intentionally after the actual HUMAN boundary.
        if locate_project(start_dir=start_dir) != binding:
            _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
        app = open_panel_application(**kwargs, read_only=False)
        try:
            def authorized(phrase, summary):
                if _digest(envelope) != authorized_digest:
                    _fail("COMMAND_CONFLICT")
                return True
            return execute_checkpoint(envelope=envelope, git_repo=binding["project_root"],
                confirmation=authorized, **compose_services(app.store))
        finally:
            app.close()
