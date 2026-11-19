---
name: evidence-gate
description: Triage and re-review reported code defects after fixes, validate new commit and CI evidence, and produce a conservative final acceptance recommendation.
---
# Re-verification and gate

Inputs: previous findings and reviewed HEAD_SHA, new HEAD_SHA, fix rationale, exact new CI results, current AC spec.

1. Diff OLD_HEAD_SHA..NEW_HEAD_SHA. For each prior finding mark FIXED_WITH_EVIDENCE, NOT_FIXED, PARTIALLY_FIXED, NOT_VERIFIED or FALSE_POSITIVE, citing new code/tests.
2. Independently review *all new changes* for regressions and unintended scope expansion.
3. Confirm CI results run against exact NEW_HEAD_SHA; verify testing/CI controls were not weakened.
4. Re-evaluate original AC matrix and architecture risk register.
5. Gate logic: BLOCK if confirmed open P0/P1, mandatory CI failed, or unauthorized change that breaks contract. CONDITIONAL for accepted P2 or unverified medium-impact risk with documented owner. INSUFFICIENT_EVIDENCE for critical missing tests/specs/CI. PASS only if all required checks satisfied and critical findings closed. Human/code-owner decides actual approval.
6. Return final gate with provenance and a concise unresolved-risk table; no changes or pushes.
