import json
import logging
from datetime import datetime
from typing import Any

from safeops.domain.models import Model
from safeops.observability.redaction import redact
from safeops.persistence.database import Database
from safeops.persistence.models import EventRow, RunRow


class AgentEvent(Model):
    id: int
    run_id: str
    thread_id: str
    event_type: str
    node: str
    timestamp: datetime
    payload: dict[str, Any]


class Events:
    def __init__(self, db: Database):
        self.db = db

    async def emit(
        self,
        run_id: str,
        event_type: str,
        node: str,
        payload: dict[str, Any] | None = None,
        ticket_id: str = "",
    ) -> None:
        safe = redact(payload or {})
        async with self.db.sessions.begin() as session:
            if not ticket_id:
                run = await session.get(RunRow, run_id)
                if run is not None:
                    ticket_id = run.ticket_id
            session.add(
                EventRow(
                    run_id=run_id, thread_id=run_id, event_type=event_type, node=node, payload=safe
                )
            )
        logging.getLogger("safeops").info(
            json.dumps(
                {
                    "run_id": run_id,
                    "thread_id": run_id,
                    "ticket_id": ticket_id,
                    "event_type": event_type,
                    "node": node,
                    "action_id": safe.get("action_id"),
                    "payload": safe,
                },
                default=str,
            )
        )
