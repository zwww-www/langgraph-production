from typing import Literal

from pydantic import Field
from sqlalchemy.dialects.postgresql import insert

from safeops.domain.models import Model, now
from safeops.persistence.database import Database
from safeops.persistence.models import MemoryRow


class CustomerMemory(Model):
    preferred_language: Literal["en", "zh"] = "en"
    customer_plan: Literal["unknown", "free", "pro", "enterprise"] = "unknown"
    concise_replies: bool = True
    # History is structured references, never arbitrary LLM-written customer secrets.
    recent_ticket_ids: list[str] = Field(default_factory=list, max_length=20)


class Memory:
    def __init__(self, db: Database):
        self.db = db

    async def get(self, customer: str) -> CustomerMemory:
        async with self.db.sessions() as session:
            row = await session.get(MemoryRow, customer)
            return CustomerMemory.model_validate(row.data) if row else CustomerMemory()

    async def remember(
        self, customer: str, ticket_id: str, language: str, plan: str | None = None
    ) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(MemoryRow)
                .values(customer_id=customer, data=CustomerMemory().model_dump())
                .on_conflict_do_nothing()
            )
            row = await session.get(MemoryRow, customer, with_for_update=True)
            assert row is not None
            data = dict(row.data)
            data["preferred_language"] = language
            data["recent_ticket_ids"] = list(
                dict.fromkeys([ticket_id] + data["recent_ticket_ids"])
            )[:20]
            if plan in ("free", "pro", "enterprise"):
                data["customer_plan"] = plan
            row.data = CustomerMemory.model_validate(data).model_dump()
            row.updated_at = now()
