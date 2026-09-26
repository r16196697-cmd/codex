# Bootstrap Academy Phase 4 — Routing Semantics Qualification

## Scope

This is synthetic deterministic routing qualification over the audited v0.1 implementation. It is not real GPT/Claude/local-model selection, model-quality evaluation, price comparison, token measurement, or provider execution. `E0_SYNTHETIC`, `E1_SYNTHETIC`, and `E2_SYNTHETIC` are fixture capability classes only. Deterministic scheduler route records are not Hosted MODEL execution receipts. Hosted provider/model identity remains `UNAVAILABLE`.

The frozen case matrix is [routing-phase4-fixture.json](fixtures/routing-phase4-fixture.json), SHA-256 `63d6be04ad6ce978d4c96dfb4b97cf83e49136beaec0cf00f4625e9da780a3b0`. The runner executes seven unique existing/new regression tests for 18 case mappings. Shared evidence tests can support multiple cases; mapped cases are not independent executions. See [routing-phase4.json](results/routing-phase4.json).

## What v0.1 represents

The deterministic RouteDecision records selected synthetic profile ID/class/version, eligible/excluded profiles, exclusion reasons, policy/risk/quality fields, and a budget snapshot. The tested TaskContract surface includes allowed/forbidden provider constraints, locality, network requirement, and modalities; Subtask includes quality/risk, requested executor, required modalities, and tool/structured-output requirements. The test fixture directly exercises quality floors, locality/provider exclusion, hard budget denial, and deterministic selection. The general network-forbidden and modality-mismatch cases were not directly exercised here. A separate capability-mismatch fallback policy is not represented as a distinct executable strategy in this fixture; no eligible profile is denied rather than approximated.

## Observations

- Routine E0 synthetic route selected without escalation in the mapped DAG integration coverage.
- Quality/risk floor is not silently downgraded when no eligible synthetic profile meets it.
- E1 failure followed by E2 creates a second Run and reservation, preserves both Attempt rows, exact-replays the second scheduling command, and reaches final `SUCCEEDED` when the second attempt succeeds.
- When both attempts fail, the existing logical-node finalization is `INCONCLUSIVE`; a third attempt is denied after finalization.
- The v0.1 scheduler requires a prior terminal failure for escalation. An `INCONCLUSIVE` first attempt followed by escalation is not representable in the current scheduler path and was not simulated as success.
- `LOCAL_ONLY` excludes a cloud-profile fixture; allowed-provider/locality exclusions are visible in RouteDecision evidence. This is not a general provider-policy qualification.
- A candidate whose synthetic cost exceeds hard budget is denied before reservation/run creation. A separate child-run-limit exhaustion scenario was not directly exercised.
- Revoking parent authority after Attempt 1 prevents creation of Attempt 2: no new child Run, route, reservation, or attempt is committed.
- Exact route command retry returns the committed result without changing route/run/reservation/Trace/ledger counts. Changing requested capability under the same command ID yields `COMMAND_CONFLICT` without changing those counts.
- Academy probes observe zero `CODEX_HOST_DECLARED` receipts for synthetic routing. No real provider/model identity, request ID, token count, latency, or dollar cost is claimed.

## Qualification boundary

The result is structural/regression-backed routing-semantics evidence only. It does not establish that a stronger model is better, that fallback improves quality, or that routing reduces real cost. No MODEL behavioral evaluation or real multi-provider execution occurred. Provider cost is `UNAVAILABLE_FOR_REAL_MODEL_ROUTING`.

Not directly tested: first-attempt `INCONCLUSIVE` then escalation, explicit distinct fallback after capability mismatch, network-forbidden exclusion, modality mismatch, exhausted child-run limit, generalized allowed/forbidden-provider matrix, and live Hosted MODEL execution. These remain `NOT REPRESENTABLE`, `NOT TESTED`, or `UNAVAILABLE` per the frozen case result; none is promoted to a supported production policy.

## Candidate status

Evidence may support a deterministic escalation/lifecycle representation candidate, bounded by the cases above. A real Routing Policy Candidate or Learning Router is not activated. `Learning Router = DEFERRED`; real multi-provider routing = `NOT IMPLEMENTED / DEFERRED`.
