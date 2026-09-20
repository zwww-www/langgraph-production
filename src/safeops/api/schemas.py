from typing import Any

from pydantic import Field, RootModel

from safeops.domain.models import Model


class RunInput(Model):
    ticket_id: str = Field(min_length=1, max_length=100)


class ReplayInput(Model):
    checkpoint_id: str = Field(min_length=1, max_length=100)


class RunIdentity(Model):
    run_id: str


class Record(RootModel[dict[str, Any]]):
    """JSON inspection envelope for versioned durable records."""


class Status(Model):
    status: str
