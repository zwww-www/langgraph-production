# Suggested reading order

1. `README.md` — system behavior, operating commands and guarantee boundaries.
2. `src/safeops/domain/models.py` — JSON models and canonical action identity.
3. `src/safeops/config.py` — versioned policy thresholds and server-owned principals.
4. `src/safeops/tools/registry.py` — schemas, allow-list and domain metadata.
5. `src/safeops/policy/engine.py` — deterministic decisions and required reviewer roles.
6. `src/safeops/approvals/service.py` — state binding, replay fingerprint and row locking.
7. `src/safeops/effects/service.py` — atomic claim, external call, execution evidence, receipt.
8. `src/safeops/effects/reconcile.py` — uncertain outcome resolution and concurrency guard.
9. `src/safeops/graph/builder.py` — actual supervisor and compiled domain subgraphs.
10. `src/safeops/graph/subgraphs/domain.py` — bounded prompts and validated plans.
11. `src/safeops/graph/nodes/operations.py` — policy, interrupt, execution reauthorization, verification.
12. `src/safeops/graph/runtime.py` — PostgreSQL lifecycle, run identity, resume and dry-run fork.
13. `src/safeops/persistence/models.py` — application tables, uniqueness and foreign keys.
14. `src/safeops/api/app.py` — authenticated endpoints and resumable SSE.
15. `apps/web/app/page.tsx` — working operator console.
16. `src/safeops/evaluation/recovery.py` — fresh-runtime crash sweep and actual safety measurements.
17. `tests/integration/test_workflows.py` — acceptance scenarios and policy bypass prevention.
18. `tests/integration/test_effect_boundaries.py` — concurrent claims, timeouts and reconciliation races.

## Interview discussion prompts

- Why can a durable workflow still duplicate a financial side effect?
- Why canonicalize validated arguments before hashing an action?
- Why is a deterministic approval token necessary with node re-execution?
- What distinguishes an approval replay from a conflicting new decision?
- What is known in each claim/execute/record crash window?
- Why must unknown outcome be represented separately from failure?
- How do advisory locks, unique indexes and row locks solve different races?
- Where is the authorization boundary between LLM planning and deterministic code?
- What makes billing, account and subscription meaningful bounded contexts?
- Why are replay and long-term memory different from checkpoint recovery?
- How can an evaluation pass without testing anything, and how do reachability gates prevent it?
- What guarantees depend on the downstream adapter's ownership and idempotency contract?
