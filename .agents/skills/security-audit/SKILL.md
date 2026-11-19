---
name: security-audit
description: Performs secure code review using threat modeling, OWASP secure review principles, trust-boundary tracing and high-confidence exploit preconditions.
---
# Security review

Use OWASP Secure Code Review and relevant ASVS controls as checklists, not as evidence a vulnerability exists. Identify assets, threat actors, entry points, trust boundaries and sinks. Trace user-controlled data to privileged actions.

Check: broken object-level auth/IDOR, tenant isolation, role checks at every write/read path; session/token expiry and revocation; CSRF for cookie-auth; injection in SQL/commands/templates; unsafe deserialization; SSRF; XSS; file upload and traversal; rate limiting and resource exhaustion; webhook validation/replay; secrets/logging; encryption/key rotation; dependency and CI supply-chain risks; defaults and feature flags; sensitive responses and caches.

For each finding require an actual reachable input->sink/privileged-operation trace, realistic preconditions, exact file/line, business impact, and a non-destructive validation recipe. Avoid running live exploits; use isolated local tests only after explicit authorization. Downgrade uncertain threats to conditional risks or questions.
