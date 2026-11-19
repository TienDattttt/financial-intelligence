---
name: review-task
description: Independently reviews a Codex-completed task between pinned commits against its specification, acceptance criteria, changed files and verification evidence.
---
# Task review protocol

Inputs required: task specification, acceptance criteria, base SHA, head SHA and local repository. Optional: handoff, CI/test logs, ADRs, change-risk classification.

1. Confirm commit identity and clean worktree. Never infer a verified SHA from a branch label. If base/head cannot be established, record INSUFFICIENT_EVIDENCE.
2. Read the spec and enumerate AC IDs. Derive expected happy/negative paths from each AC **without** relying on Codex summary.
3. Read `git diff --name-status BASE HEAD`, full diff and changed tests; trace changed symbols to callers, callees, middleware, database, config, external integrations.
4. Review correctness, edge cases, state transitions, idempotency, concurrency, authz, tenant boundaries, contract changes, input validation, error handling, API backward compatibility, documentation and resource use.
5. Examine test quality and CI weakening using `test-integrity`. Check assertions genuinely fail for broken behavior; look for skipped/deleted tests and `|| true`, ignored errors, disabled scans.
6. Build an AC traceability matrix: AC -> code path -> test/evidence -> verified/partial/unverified/not applicable. Never confuse "a test exists" with "behavior demonstrated".
7. Generate at least 3 high-value adversarial scenarios for sensitive flows (not arbitrary speculative findings). Evaluate whether a real execution path supports each.
8. Prioritize evidence-backed findings: P0/P1 stop gate, P2 decide risk, P3 optional. Each finding must state introduced vs pre-existing.
9. Output review report in conversation; don't write files. Recheck exact head SHA after any investigation.
