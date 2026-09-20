import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, text

from safeops.api.auth import principal
from safeops.api.schemas import Record, ReplayInput, RunIdentity, RunInput, Status
from safeops.approvals.service import DecisionInput
from safeops.config import Settings
from safeops.domain.models import Conflict, Forbidden, NotFound, Principal, SafeOpsError, Ticket
from safeops.effects.reconcile import Resolution, reconcile
from safeops.events.service import AgentEvent
from safeops.graph.runtime import Runtime
from safeops.graph.worker import worker
from safeops.memory.service import CustomerMemory
from safeops.observability.redaction import redact
from safeops.persistence.database import row_dict
from safeops.persistence.models import ApprovalRow, EffectRow, EventRow, RunRow

User = Annotated[Principal, Depends(principal)]


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=config.log_level, format="%(message)s")
        async with Runtime(config) as runtime:
            app.state.runtime = runtime
            stop = asyncio.Event()
            task = asyncio.create_task(worker(runtime, stop)) if start_worker else None
            try:
                yield
            finally:
                stop.set()
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(title="SafeOps Agent", lifespan=lifespan)

    def rt() -> Runtime:
        return app.state.runtime

    @app.exception_handler(SafeOpsError)
    async def safe_error(request: Request, exc: SafeOpsError) -> JSONResponse:
        code = 404 if isinstance(exc, NotFound) else 403 if isinstance(exc, Forbidden) else 409
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.get("/health", response_model=Status)
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", response_model=Status)
    async def ready() -> dict:
        try:
            async with rt().db.sessions() as session:
                await session.execute(text("SELECT 1 FROM agent_runs LIMIT 1"))
                await session.execute(text("SELECT 1 FROM checkpoint_migrations LIMIT 1"))
        except Exception as exc:
            raise HTTPException(503, "database or migrations unavailable") from exc
        return {"status": "ready"}

    @app.post("/api/tickets", response_model=Ticket, status_code=201)
    async def ticket(body: Ticket, user: User) -> Ticket:
        return await rt().ticket(body)

    @app.post("/api/runs", response_model=RunIdentity, status_code=202)
    async def run(body: RunInput, user: User) -> dict:
        return {"run_id": await rt().enqueue(body.ticket_id)}

    @app.get("/api/runs", response_model=list[Record])
    async def runs(user: User, limit: int = Query(default=100, ge=1, le=200)) -> list:
        async with rt().db.sessions() as session:
            return [
                redact(row_dict(r))
                for r in await session.scalars(
                    select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
                )
            ]

    @app.get("/api/runs/{run_id}", response_model=Record)
    async def get_run(run_id: str, user: User) -> dict:
        return redact(await rt().get_run(run_id))

    @app.get("/api/runs/{run_id}/state", response_model=Record)
    async def state(run_id: str, user: User) -> dict:
        return redact(await rt().snapshot(run_id))

    @app.get("/api/runs/{run_id}/checkpoints", response_model=list[Record])
    async def checkpoints(run_id: str, user: User) -> list:
        return redact(await rt().checkpoints(run_id))

    @app.post("/api/runs/{run_id}/resume", response_model=RunIdentity, status_code=202)
    async def resume(run_id: str, user: User) -> dict:
        await rt().queue_resume(run_id)
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/replay", response_model=RunIdentity, status_code=202)
    async def replay(run_id: str, body: ReplayInput, user: User) -> dict:
        return {"run_id": await rt().replay(run_id, body.checkpoint_id)}

    @app.get("/api/runs/{run_id}/events", response_model=list[Record])
    async def events(run_id: str, user: User, after: int = Query(default=0, ge=0)) -> list:
        await rt().get_run(run_id)
        return await event_rows(run_id, after)

    async def event_rows(run_id: str, after: int) -> list[dict[str, Any]]:
        async with rt().db.sessions() as session:
            rows = await session.scalars(
                select(EventRow)
                .where(EventRow.run_id == run_id, EventRow.id > after)
                .order_by(EventRow.id)
                .limit(500)
            )
            return [
                AgentEvent.model_validate(redact(row_dict(r))).model_dump(mode="json") for r in rows
            ]

    @app.get("/api/runs/{run_id}/stream")
    async def stream(
        run_id: str,
        request: Request,
        user: User,
        last_event_id: str = Header(default="0"),
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        await rt().get_run(run_id)
        try:
            cursor = max(after, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(422, "invalid event cursor") from exc

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            while not await request.is_disconnected():
                rows = await event_rows(run_id, cursor)
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: agent_event\ndata: {json.dumps(row, default=str)}\n\n"
                run_state = await rt().get_run(run_id)
                if not rows and run_state["status"] in (
                    "completed",
                    "failed",
                    "in_doubt",
                    "waiting_approval",
                ):
                    yield "event: settled\ndata: {}\n\n"
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/approvals", response_model=list[Record])
    async def approvals(user: User, pending: bool = True) -> list:
        async with rt().db.sessions() as session:
            query = select(ApprovalRow).order_by(ApprovalRow.created_at.desc()).limit(200)
            if pending:
                query = query.where(ApprovalRow.decision.is_(None))
            return [redact(row_dict(r)) for r in await session.scalars(query)]

    @app.get("/api/approvals/{approval_id}", response_model=Record)
    async def approval(approval_id: str, user: User) -> dict:
        async with rt().db.sessions() as session:
            row = await session.get(ApprovalRow, approval_id)
            if not row:
                raise NotFound("approval not found")
            return redact(row_dict(row))

    async def decide(approval_id: str, body: DecisionInput, user: Principal, verdict: Any) -> dict:
        result = await rt().deps.approvals.decide(approval_id, user, verdict, body.note)
        # Durable decision precedes queueing; repeated requests can recover this crash window.
        try:
            await rt().queue_resume(result["run_id"])
        except Conflict:
            pass
        return result

    @app.post("/api/approvals/{approval_id}/approve", response_model=Record)
    async def approve(approval_id: str, body: DecisionInput, user: User) -> dict:
        return await decide(approval_id, body, user, "approve")

    @app.post("/api/approvals/{approval_id}/reject", response_model=Record)
    async def reject(approval_id: str, body: DecisionInput, user: User) -> dict:
        return await decide(approval_id, body, user, "reject")

    @app.get("/api/effects/in-doubt", response_model=list[Record])
    async def doubts(user: User) -> list:
        async with rt().db.sessions() as session:
            return [
                redact(row_dict(r))
                for r in await session.scalars(
                    select(EffectRow)
                    .where(EffectRow.status.in_(["pending", "in_doubt"]))
                    .limit(200)
                )
            ]

    @app.get("/api/effects/{effect_id}", response_model=Record)
    async def effect(effect_id: str, user: User) -> dict:
        async with rt().db.sessions() as session:
            row = await session.get(EffectRow, effect_id)
            if not row:
                raise NotFound("effect not found")
            return redact(row_dict(row))

    @app.post("/api/effects/{effect_id}/reconcile", response_model=RunIdentity)
    async def resolve(effect_id: str, body: Resolution, user: User) -> dict:
        run_id = await reconcile(rt().db, effect_id, body, user)
        await rt().queue_resume(run_id)
        return {"run_id": run_id}

    @app.get("/api/customers/{customer_id}/memory", response_model=CustomerMemory)
    async def memory(customer_id: str, user: User) -> CustomerMemory:
        return await rt().deps.memory.get(customer_id)

    return app
