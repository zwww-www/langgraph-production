from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from safeops.approvals.service import Approvals
from safeops.config import Settings
from safeops.domain.models import Conflict, EffectInDoubt, NotFound, Ticket, now
from safeops.effects.service import Effects
from safeops.events.service import Events
from safeops.faults import Faults
from safeops.graph.builder import build_graph
from safeops.graph.deps import Dependencies
from safeops.llm.base import Provider
from safeops.llm.offline import OfflineProvider
from safeops.llm.providers import CompatibleProvider
from safeops.memory.service import Memory
from safeops.observability.redaction import redact_text
from safeops.persistence.database import Database, row_dict
from safeops.persistence.models import ApprovalRow, RunRow, TicketRow
from safeops.policy.engine import PolicyEngine
from safeops.tools.adapters import bind_external
from safeops.tools.local import bind_local
from safeops.tools.registry import ToolRegistry


class Runtime:
    def __init__(
        self, settings: Settings, provider: Provider | None = None, faults: Faults | None = None
    ):
        self.settings = settings
        self.db = Database(settings)
        self.stack = AsyncExitStack()
        self.provider = provider
        self.faults = faults or Faults()
        self.graph: Any = None

    async def __aenter__(self) -> "Runtime":
        try:
            self.stack.push_async_callback(self.db.close)
            client = await self.stack.enter_async_context(httpx.AsyncClient(timeout=30))
            saver = await self.stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(self.settings.database_url)
            )
            registry = ToolRegistry()
            bind_local(self.db, registry)
            bind_external(self.settings, registry, client)
            provider = self.provider or (
                CompatibleProvider(self.settings, client)
                if self.settings.llm_provider != "offline"
                else OfflineProvider()
            )
            self.deps = Dependencies(
                self.settings,
                provider,
                registry,
                PolicyEngine(self.settings, registry),
                Approvals(self.db),
                Effects(self.db, registry, self.faults),
                Events(self.db),
                Memory(self.db),
                self.faults,
            )
            self.graph = build_graph(self.deps, saver)
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args: Any) -> None:
        await self.stack.aclose()

    @staticmethod
    def config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    async def ticket(self, ticket: Ticket) -> Ticket:
        ticket = ticket.model_copy(update={"text": redact_text(ticket.text)})
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(TicketRow).values(**ticket.model_dump()).on_conflict_do_nothing()
            )
            row = await session.get(TicketRow, ticket.id)
            assert row is not None
            if (row.customer_id, row.text) != (ticket.customer_id, ticket.text):
                raise Conflict("ticket identity already binds different content")
        return ticket

    async def enqueue(self, ticket_id: str) -> str:
        run_id = uuid4().hex
        async with self.db.sessions.begin() as session:
            if await session.get(TicketRow, ticket_id) is None:
                raise NotFound("ticket not found")
            await session.execute(
                insert(RunRow)
                .values(
                    id=run_id, thread_id=run_id, ticket_id=ticket_id, status="queued", dry_run=False
                )
                .on_conflict_do_nothing()
            )
            existing = await session.scalar(
                select(RunRow).where(RunRow.ticket_id == ticket_id, RunRow.dry_run.is_(False))
            )
            assert existing is not None
            return existing.id

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self.db.sessions() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                raise NotFound("run not found")
            return row_dict(row)

    async def queue_resume(self, run_id: str) -> None:
        async with self.db.run_lock(run_id), self.db.sessions.begin() as session:
            row = await session.get(RunRow, run_id, with_for_update=True)
            if row is None:
                raise NotFound("run not found")
            if row.status != "completed":
                row.status, row.error = "queued", None

    async def snapshot(self, run_id: str) -> dict[str, Any]:
        await self.get_run(run_id)
        snap = await self.graph.aget_state(self.config(run_id))
        return {
            "values": snap.values,
            "next": list(snap.next),
            "interrupts": [i.value for i in snap.interrupts],
        }

    async def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        await self.get_run(run_id)
        return [
            {
                "checkpoint_id": s.config["configurable"]["checkpoint_id"],
                "created_at": s.created_at,
                "next": list(s.next),
                "values": s.values,
            }
            async for s in self.graph.aget_state_history(self.config(run_id), limit=100)
        ]

    async def replay(self, run_id: str, checkpoint_id: str) -> str:
        source = await self.get_run(run_id)
        config = self.config(run_id)
        config["configurable"]["checkpoint_id"] = checkpoint_id
        snapshot = await self.graph.aget_state(config)
        if not snapshot.values:
            raise NotFound("checkpoint not found on this run")
        replay_id = uuid4().hex
        # Fork persisted planning context into a new isolated dry-run. No live resume API.
        state = dict(snapshot.values)
        state.update(
            run_id=replay_id,
            dry_run=True,
            approval=None,
            receipt=None,
            outcome="pending",
            reply="",
            trail=[],
            error=None,
        )
        async with self.db.sessions.begin() as session:
            session.add(
                RunRow(
                    id=replay_id,
                    thread_id=replay_id,
                    ticket_id=source["ticket_id"],
                    status="queued",
                    dry_run=True,
                    initial_state=state,
                )
            )
        return replay_id

    async def process(self, run_id: str) -> dict[str, Any]:
        async with self.db.run_lock(run_id):
            run = await self.get_run(run_id)
            if run["status"] == "completed":
                return await self.snapshot(run_id)
            async with self.db.sessions.begin() as session:
                row = await session.get(RunRow, run_id)
                assert row is not None
                row.status = "running"
                ticket_row = await session.get(TicketRow, row.ticket_id)
                assert ticket_row is not None
                ticket = Ticket(
                    id=ticket_row.id, customer_id=ticket_row.customer_id, text=ticket_row.text
                )
            config = self.config(run_id)
            snap = await self.graph.aget_state(config)
            input_value: Any = None
            if not snap.values:
                input_value = run["initial_state"] or {
                    "run_id": run_id,
                    "ticket": ticket.model_dump(),
                    "trail": [],
                    "dry_run": run["dry_run"],
                    "memory": (await self.deps.memory.get(ticket.customer_id)).model_dump(),
                    "audit_context": {"ticket_id": ticket.id, "customer_id": ticket.customer_id},
                }
                # A fork may precede planning. Resume only after a node whose
                # outputs exist; planned forks always re-enter current policy.
                if run["initial_state"]:
                    input_value["dry_run"] = run["dry_run"]
                    domain = input_value.get("domain")
                    anchor = None
                    if "plan" in input_value and domain in ("billing", "account", "subscription"):
                        anchor = domain
                    elif "decision" in input_value:
                        anchor = "supervisor"
                    if anchor:
                        await self.graph.aupdate_state(config, input_value, as_node=anchor)
                        input_value = None
            elif snap.interrupts:
                token = snap.interrupts[0].value["token"]
                async with self.db.sessions() as session:
                    approval = await session.get(ApprovalRow, token)
                    if approval is None or approval.decision is None:
                        await self._finish(run_id, "waiting_approval", snap.values)
                        return await self.snapshot(run_id)
                input_value = Command(resume={"token": token})
            await self.deps.events.emit(run_id, "run_started", "runtime", ticket_id=ticket.id)
            try:
                result = await self.graph.ainvoke(input_value, config, durability="sync")
                status = "waiting_approval" if result.get("__interrupt__") else "completed"
                await self._finish(run_id, status, result)
                await self.deps.events.emit(
                    run_id, "run_completed" if status == "completed" else "run_suspended", "runtime"
                )
            except EffectInDoubt as exc:
                await self._finish(run_id, "in_doubt", {}, str(exc))
                await self.deps.events.emit(
                    run_id, "effect_in_doubt", "execute", {"effect_id": str(exc)}
                )
            except Exception as exc:
                # Never persist exception text from SDKs (may contain tokens/request bodies).
                await self._finish(run_id, "failed", {}, type(exc).__name__)
                await self.deps.events.emit(
                    run_id, "run_failed", "runtime", {"error_type": type(exc).__name__}
                )
                raise
            return await self.snapshot(run_id)

    async def _finish(
        self, run_id: str, status: str, state: dict, error: str | None = None
    ) -> None:
        async with self.db.sessions.begin() as session:
            row = await session.get(RunRow, run_id)
            assert row is not None
            row.status, row.error = status, error
            if state:
                row.domain, row.risk = state.get("domain"), state.get("policy", {}).get("risk")
            row.finished_at = now() if status == "completed" else None
