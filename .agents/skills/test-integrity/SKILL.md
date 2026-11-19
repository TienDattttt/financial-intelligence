---
name: test-integrity
description: Reviews tests and CI for false green status, assertion weaknesses, removed checks, contract coverage, regressions and independent verification evidence.
---
# Test and CI integrity review

1. Compare changed tests, fixtures, snapshots and CI workflows with baseline SHA. Flag weakened coverage thresholds, deleted/skip/only tests, altered assertions, suppressed errors, `|| true`, disabled lint/security jobs, skipped PR triggers and lower dependency policy.
2. Map each acceptance criterion to a test/assertion and runtime behavior. Identify happy path, boundary, negative, authorization, state transition, concurrency and rollback cases.
3. Where practical, suggest a minimal counterexample that fails before a fix and passes afterward; do not silently edit tests or fixtures.
4. Validate evidence: full command, working directory, exit code, target commit SHA, tool version, CI run URL, date, relevant environment. "Codex said tests pass" is NOT evidence.
5. Distinguish tests absent because the behavior is deferred in spec from tests missing for implemented functionality.
6. Report missing independent verification and flakiness plainly. Tests passing is necessary but not sufficient.
