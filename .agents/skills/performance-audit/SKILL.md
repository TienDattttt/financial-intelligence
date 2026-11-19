---
name: performance-audit
description: Reviews system performance, scalability, low-level resource handling and reliability based on real call paths and measured evidence or clearly reasoned complexity.
---
# Performance review

Work from workload assumptions and existing SLOs if available; never invent p95, QPS or benchmark results. Derive likely hot paths from real calls and traces.

Examine:
- unbounded pagination, batch, fanout, recursion, loops and growth in time/space complexity
- database N+1 calls, missing selective indexes, expensive scans/sorts, transactions held across I/O, missing limits, isolation contention and connection pool exhaustion
- blocking I/O and sync operations in async/event loops; CPU-bound work on request path; repeated parsing/serialization/crypto; excessive object allocation
- cache correctness, high-cardinality keys, stampede/thundering herd, invalidation, TTL and cross-tenant cache leakage
- retries/timeouts/backoff/jitter, circuit breaking, backpressure, queue lag and poison messages
- concurrency race/deadlock, file/socket/stream leaks, cleanup on error, unhandled promises, retries of non-idempotent operations
- observability sufficient to detect saturation, latency and failure rates

Evidence hierarchy: benchmark/profiler/query plan > instrumented test > statically justified cost model > hypothesis. Report a plan for targeted profiling or `EXPLAIN` in a disposable environment. Never run destructive or production queries; note that `EXPLAIN ANALYZE` may actually execute statements.
