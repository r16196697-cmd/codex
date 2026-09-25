# Nexus v0.1 — Codex-hosted / Attached

Release status: `DEPLOYED`
Release tag: `nexus-v0.1-hosted`
Schema/migration version: `11`

## Included

- Thin Codex Hosted Bridge over the Nexus Runtime; Codex remains the reasoning and execution host.
- Hosted-required acceptance conditions T1–T12 are recorded as PASS in the release acceptance evidence.
- Immutable ordered DAG attempts, deterministic Runtime escalation/fallback, authority and budget gates, governed Objects/Evidence/Trace, inspect, memory/purge, and recovery.
- Sanitized source, schemas, migrations, policies, tests, acceptance evidence, and operator documentation for independent code audit.

## Explicit limits

- `Standalone Nexus: NOT IMPLEMENTED / DEFERRED`.
- Independent Model/Search Providers, real Provider Credential Broker use, multi-provider routing, standalone model execution, independent GUI, and Bootstrap Academy are not part of this release.
- Current Codex does not expose a reliable interface for Nexus to choose or observe live E0/E1/E2 model backends. Runtime escalation/fallback semantics were tested with deterministic executors; no real model switch or provider identity is claimed.
- Provider IDs, model IDs, request IDs, and token/cost telemetry unavailable from the Host remain unavailable, not inferred.

## Reproducibility and audit

Acceptance details and observed evidence are in [`2026-09-25-persistent-host-pilot.md`](../../eval/regression/2026-09-25-persistent-host-pilot.md) and [`2026-09-25-mode-inspect-regression.md`](../../eval/regression/2026-09-25-mode-inspect-regression.md). Current implementation state is in [`IMPLEMENTATION_STATUS.md`](../operator/IMPLEMENTATION_STATUS.md). Runtime databases, objects, backups, private memory, traces, credentials, and machine-local snapshots are excluded from Git.
