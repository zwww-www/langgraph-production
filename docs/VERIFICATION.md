# Verification record — 2026-09-19

All Python commands ran in the conda prefix `.conda-safeops` (Python 3.12.14). No system Python environment was used.

## Executed checks

| Check | Observed result |
| --- | --- |
| `python -m ruff check src tests` | Passed |
| `python -m ruff format --check src tests` | 61 files formatted |
| `python -m mypy src/safeops` | Passed, 55 source files |
| `python -m pytest -q` | **77 passed**, no skips |
| `python -m alembic check` | No new upgrade operations detected |
| `python -m safeops eval --database` | Passed; measured report in `evaluation-example.json` |
| `npm run build` | Next.js production build and TypeScript passed |
| `npm audit` | Zero vulnerabilities at installation time |

Tests used an isolated PostgreSQL 17.11 database named `safeops_test` on loopback port 55432. The fixtures refuse database names without the `_test` suffix and truncate only that dedicated database. Application migrations and official LangGraph checkpoint setup were applied. Official checkpoint tables are intentionally excluded from Alembic application autogeneration.

Coverage includes read-only routing, approval suspension and fresh-runtime resume, rejection, role/token/decision replay validation, ownership, policy reauthorization, concurrent run creation and effect claims, all nine injected crash boundaries, both reconciliation outcomes, reconciliation races, structured cross-thread memory, every populated checkpoint dry-run fork, API authentication/errors, durable SSE cursors and worker execution. HTTP and MCP contracts use controlled fake transports; they do not contact a public service.

The database evaluation reached **9/9** fault points: **6 deterministic completions**, **3 expected in-doubt stops**, and **0 duplicate external operations**. Invalid/conflicting approval acceptance and missed/spurious HITL counts were zero. The 20-case offline corpus scored 1.0 for routing, macro F1 and tool accuracy. This synthetic rule-based corpus is not evidence of live-model accuracy or production reliability.

## Browser acceptance

The actual Next.js console connected with the synthetic `demo-admin` token. A Chinese $45 refund request for CUS-001 / INV-10032 suspended for finance approval. Submitting a reviewer note and approval resumed the worker, completed the run, and displayed the receipt (4500 cents refunded, 4500 cents remaining), persisted event timeline, state and checkpoint history. A fork from the pre-routing checkpoint also completed in the browser with a DRY RUN label and simulated receipt. The page layout was visually inspected. The Windows CLI server was separately exercised, catching and fixing uvicorn's event-loop override so psycopg uses the selector loop.

## Current local setup

The temporary instance used for the original verification has been removed at the user's request. The project now uses the PostgreSQL installation at `E:\PostgreSQL\17` on port 5432. All 77 tests passed again on this installation. See [LOCAL_SETUP.md](LOCAL_SETUP.md) for current paths, ports and startup commands. Earlier test results above remain the historical verification record.

## Not verified in this environment

- Docker/Compose image builds and container startup: Docker was unavailable. Dockerfiles, health checks, migration/seed dependencies and CI checks are supplied, but no successful local container run is claimed.
- Public MCP servers and real payment/account systems remain unverified. Alibaba Cloud qwen3.8-flash was subsequently verified with actual API calls; see [ALIYUN_VERIFICATION.md](ALIYUN_VERIFICATION.md).
- Multi-host load, database failover and production ingress: not simulated. This is a production-oriented reference implementation with explicit safety boundaries, not a completed production deployment.
