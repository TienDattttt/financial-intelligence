---
name: review-phase
description: Performs thorough architecture and system integration review of a completed phase across specifications, cumulative Git changes, subsystem boundaries and operational behavior.
---
# Phase acceptance protocol

1. Pin base and head SHAs; gather all specs of phase, ADRs, expected architecture, NFRs, test/CI reports. Declare out-of-scope.
2. Reconstruct **implemented architecture** from code, not diagrams alone: major components, entrypoints, data stores, jobs, queues, caches, deployment topology, dependency map and trust boundaries.
3. Compare promised vs actual architecture; list missing components and contradictions. Do not assume future-layer functionality must be present unless required by the current phase's contract.
4. Trace important end-to-end paths: normal flow, unauthorized flow, high load, partial failure, concurrent update, retry/duplicate, rollback/migration, tenant boundaries, feature flag off, legacy consumers.
5. Invoke or apply architecture-gap-audit, security-audit, performance-audit, test-integrity. Delegate to independent specialist agents if tools/version supports it; otherwise perform sequential independent perspectives.
6. Cover schema/API/event/config contracts, compatibility with previous phases, service ownership, transaction and consistency boundaries, data retention, operational observability and recovery.
7. Separate confirmed bugs, contract violations, architectural risks conditional on scale/deployment, decisions needing ADRs and optional optimizations.
8. Produce a phase coverage matrix, boundary map in text, top risk register with mitigation/owner, explicit unknowns and gate recommendation. Avoid claiming production scalability without load tests or credible capacity evidence.
