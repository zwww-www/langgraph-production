from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from safeops.api.auth import principal
from safeops.domain.models import Model, Principal
from safeops.policy.control import CompileInput, ControlDocument, ControlPlane, ReleaseInput
from safeops.policy.models import PolicyDefinition
from safeops.policy.queries import Collection, collection
from safeops.risk.models import BusinessSLO, Observation
from safeops.risk.outcomes import observe

router = APIRouter(prefix="/api/control", tags=["Risk-budgeted control plane"])
User = Annotated[Principal, Depends(principal)]


def service(request: Request) -> ControlPlane:
    return ControlPlane(request.app.state.runtime.db)


Control = Annotated[ControlPlane, Depends(service)]


class GenerateInput(Model):
    count: int = Field(default=100000, ge=10, le=500000)
    seed: int = Field(default=42, ge=0, le=2147483647)


@router.get("/slo", response_model=ControlDocument)
async def get_slo(user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.slo())


@router.put("/slo", response_model=ControlDocument)
async def put_slo(body: BusinessSLO, user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.put_slo(body, user))


@router.get("/active", response_model=PolicyDefinition)
async def active(user: User, control: Control) -> PolicyDefinition:
    return await control.registry.active()


@router.post("/outcomes", response_model=ControlDocument)
async def outcome(body: Observation, user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await observe(control.db, body, user))


@router.post("/history", response_model=ControlDocument)
async def history(body: GenerateInput, user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.generate(body.count, body.seed, user))


@router.post("/compile", response_model=ControlDocument)
async def compile_policy(body: CompileInput, user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.compile(body, user))


@router.get("/candidates/{identity}", response_model=ControlDocument)
async def candidate(identity: str, user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.candidate(identity))


@router.get("/candidates/{identity}/diff", response_model=ControlDocument)
async def diff(identity: str, user: User, control: Control) -> ControlDocument:
    row = await control.candidate(identity)
    return ControlDocument(
        data={
            "replay": row["result"]["replay"],
            "diff_digest": row["diff_digest"],
            "base_revision": row["base_revision"],
        }
    )


@router.post("/candidates/{identity}/release", response_model=ControlDocument)
async def release(
    identity: str, body: ReleaseInput, user: User, control: Control
) -> ControlDocument:
    return ControlDocument(data=await control.release(identity, body, user))


@router.get("/metrics", response_model=ControlDocument)
async def metrics(user: User, control: Control) -> ControlDocument:
    return ControlDocument(data=await control.metrics())


@router.get("/{kind}", response_model=list[ControlDocument])
async def browse(
    kind: Collection, user: User, control: Control, calibration_id: str | None = None
) -> list[ControlDocument]:
    return [ControlDocument(data=row) for row in await collection(control.db, kind, calibration_id)]
