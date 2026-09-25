# Nexus v0.1 — Codex-hosted / Attached

**Release status: DEPLOYED**
**Standalone Nexus: NOT IMPLEMENTED / DEFERRED**

Nexus v0.1 is a local, governed state and reliability runtime attached to a Codex host. Codex remains responsible for reasoning and execution; a thin Hosted Bridge records declared work and actual tool actions through Nexus Core APIs. Nexus provides durable Task/Run state, authority and budget enforcement, objects and evidence, verification, trace, memory governance, purge, and recovery.

The Codex-hosted required conditions in Acceptance Tests T1–T12 were formally accepted for this release. See [the implementation status](docs/operator/IMPLEMENTATION_STATUS.md), [the regression/acceptance evidence](eval/regression/2026-09-25-persistent-host-pilot.md), [the mode and inspect regression](eval/regression/2026-09-25-mode-inspect-regression.md), and [operator CLI documentation](docs/user/operator-cli.md).

## Scope and limits

Implemented and released:

- Codex-hosted / Attached execution through the thin Host Bridge;
- deterministic Nexus Runtime semantics, including DAG attempts, routing decisions, escalation/fallback, authority, effects, verification, trace, purge, and recovery;
- Hosted-required T1–T12 acceptance evidence.

Not implemented or deferred:

- Standalone Nexus service or independent model execution;
- independent Model/Search Providers and real Provider Credential Broker use;
- multi-provider routing or live Codex multi-tier model switching (the current Host does not expose a reliable control/observation interface for that);
- independent Nexus GUI/application;
- Bootstrap Academy and automatic evolution/promotion.

E0/E1/E2 Runtime escalation/fallback semantics were tested with deterministic executors. This does not claim that Codex performed a real switch between identifiable model tiers. Hosted model/backend identity and unavailable provider/token telemetry remain unavailable rather than inferred.

## Source layout

- `kernel/`, `adapters/`: Runtime and Hosted Bridge;
- `schemas/`, `migrations/`, `policies/`: persisted contracts and policy;
- `tests/`: contract, integration, and recovery tests;
- `eval/regression/`: sanitized acceptance evidence and fixtures;
- `docs/operator/`, `docs/user/`: deployment state and operations guidance.

Runtime databases, objects, backups, credentials, private memory, and traces are local-only and excluded from this repository.

## Validation

Run the test suite with `python -m unittest discover -s tests -v` in the documented project environment. See operator docs for recovery and integrity checks. No real external write provider or secret is required for the Hosted acceptance suite.
