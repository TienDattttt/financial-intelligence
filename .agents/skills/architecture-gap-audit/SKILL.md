---
name: architecture-gap-audit
description: Finds proven architecture inconsistencies and conditional design gaps across domain layering, inter-service contracts, persistence, distributed execution and future phases.
---
# Architecture audit

Start with business capabilities, stated architecture and explicit nonfunctional requirements. Build an **as-implemented** dependency/flow map by following actual imports and calls.

## Check all relevant boundaries
- UI/client -> API -> authN/authZ -> application service -> domain -> repository -> database/queue/cache -> external system -> response
- API versioning, backward compatibility, schema/DB migration expand-contract, serialized events and consumer contracts
- Dependency direction and ownership; domain logic leaked into controllers/UI/data access; duplicated conflicting validation or business rules
- Single vs distributed transaction; partial failure; outbox/event delivery; idempotency; deduplication; ordering; retries; exactly-once assumptions
- Tenant isolation and cross-tenant joins; identity and policy propagation across workers/caches
- Cache keys, invalidation and stale reads; consistency model; schema indexes and transaction isolation
- Time, time zones, money precision, units, rounding; state-machine invariants
- Security trust boundaries, key lifecycle, secrets, data classification, privacy, audit trail
- Resilience: request deadlines, connection pooling, queue backpressure, restart/replay, recovery and rollback
- Observability: logs, traces, metrics, correlation IDs, error budgets, alerts and operational runbooks when the system requires them
- Deployability: config/environment drift, feature flags, compatibility during rolling deployments, migration rollback
- Next-phase contracts: ensure the foundation *required now* exists and no impossible dependencies are introduced; don't demand speculative YAGNI features.

## For every gap
Show (a) required guarantee/spec or documented assumption, (b) actual path through code with file references, (c) counterexample/failure mode, (d) impact, (e) classification VERIFIED/CONDITIONAL/DECISION and (f) a proportionate remedy. Never turn unsupported scalability forecasts into fact.
