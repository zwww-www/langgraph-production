import asyncio
import logging

from sqlalchemy import exists, or_, select

from safeops.domain.models import Conflict
from safeops.graph.runtime import Runtime
from safeops.persistence.models import ApprovalRow, RunRow


async def worker(runtime: Runtime, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with runtime.db.sessions() as session:
                ids = list(
                    await session.scalars(
                        select(RunRow.id)
                        .where(
                            or_(
                                RunRow.status.in_(["queued", "running"]),
                                (RunRow.status == "waiting_approval")
                                & exists(
                                    select(ApprovalRow.token).where(
                                        ApprovalRow.run_id == RunRow.id,
                                        ApprovalRow.decision.is_not(None),
                                    )
                                ),
                            )
                        )
                        .order_by(RunRow.created_at)
                        .limit(20)
                    )
                )
            for run_id in ids:
                if stop.is_set():
                    break
                try:
                    await runtime.process(run_id)
                except Conflict:
                    pass
                except Exception as exc:
                    logging.getLogger("safeops").error("worker failure: %s", type(exc).__name__)
        except Exception as exc:
            logging.getLogger("safeops").error("queue unavailable: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=runtime.settings.worker_poll_seconds)
        except TimeoutError:
            pass
