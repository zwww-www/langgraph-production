# Architecture audit and replacement record

The starting checkout was commit `89d9079`. The repository had no local changes before this task.
The audit covered README, packaging, all production module groups, CLI, fixtures, and the existing tests.

## Original execution

`SupportRuntime.start → classify → plan → human_gate → act → compose` was synchronous.
Checkpoints used `SqliteSaver`; approval/effect stores had both SQLite and in-memory implementations.
Six synthetic tools shared one router and registry. Tool risk was a single irreversible flag.
The CLI and evaluation driver created fresh runtimes after injected failures.

## Preserved invariants

- Schema validation before planning; invalid model output escalates.
- Canonical validated arguments define action identity.
- Deterministic tokens bind a human decision to state and action.
- Identical decision replay is valid; conflicting verdict, reviewer, or note is invalid.
- Claim before external execution; uncertain outcome cannot be retried blindly.
- Separate execution evidence and completed receipt.
- Explicit performed/not-performed reconciliation and deterministic fault injection.
- Offline provider and strict cassette misses; routing, arguments, interruption and recovery evaluation.

## Replaced and removed

The entire `src/langgraph_production` package, SQLite persistence, synchronous runtime, old CLIs,
obsolete SQLite tests, old evaluation fixture format, and outdated demonstration assets were removed.
Their originals remain recoverable in Git history, not as a second implementation in the working tree.
The MIT license was preserved.

## Design improvements

| Original concern | Final implementation |
|---|---|
| Same ticket on a new thread permits another payment | Unique live run per ticket; replay is a separate dry-run |
| One runtime shares synchronous SQLite connections | Async SQLAlchemy sessions plus official PostgreSQL saver |
| Boolean irreversible flag is authorization policy | Domain/risk metadata and deterministic versioned policy |
| Approver name supplied in decision payload | Server-owned bearer principal and required role |
| No worker ownership model | PostgreSQL transaction advisory lock per run, shared with reconciliation |
| Releasing an effect can contradict recorded execution | Reject not-performed when execution evidence exists |
| Fake handlers return values without persistent business state | Independently committed simulated downstream business records and operation keys |
| Runtime object might hide recovery defects | Destroy/reconstruct runtime, graph, engine and checkpointer in tests |
| No service or operator UI | FastAPI, durable queue, persistent events/SSE and Next.js console |

The new tests preserve behavior-level invariants, rather than keeping old module imports or
assertions about a five-node graph. Evaluation numbers are regenerated against the new fixtures;
the old 51-case classifier score is not reused as a claim about this system.
