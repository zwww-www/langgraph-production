from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from safeops.domain.models import (
    Action,
    Conflict,
    Forbidden,
    Model,
    NotFound,
    Principal,
    Ticket,
    digest,
    now,
)
from safeops.observability.redaction import redact_text
from safeops.persistence.database import Database
from safeops.persistence.models import ApprovalRow, EventRow
from safeops.policy.models import PolicyDecision


class ApprovalRequest(Model):
    token: str
    thread_id: str
    ticket_id: str
    customer_id: str
    action_id: str | None
    state_digest: str
    action: Action | None
    policy: PolicyDecision
    requested_role: str
    policy_version: str


class DecisionInput(Model):
    note: str = Field(default="", max_length=2000)


def request_for(
    run_id: str, ticket: Ticket, action: Action | None, policy: PolicyDecision
) -> ApprovalRequest:
    binding = {
        "thread_id": run_id,
        "ticket": ticket.model_dump(mode="json"),
        "action": action.model_dump(mode="json") if action else None,
        "policy": policy.model_dump(mode="json"),
    }
    state_digest = digest(binding)
    role = policy.requirement.role if policy.requirement else "operator"
    return ApprovalRequest(
        token=digest({"approval": state_digest}),
        thread_id=run_id,
        ticket_id=ticket.id,
        customer_id=ticket.customer_id,
        action_id=action.action_id if action else None,
        state_digest=state_digest,
        action=action,
        policy=policy,
        requested_role=role,
        policy_version=policy.version,
    )


class Approvals:
    def __init__(self, db: Database):
        self.db = db

    async def request(self, request: ApprovalRequest) -> None:
        async with self.db.sessions.begin() as session:
            await session.execute(
                insert(ApprovalRow)
                .values(
                    token=request.token,
                    run_id=request.thread_id,
                    thread_id=request.thread_id,
                    action_id=request.action_id,
                    request=request.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
            )

    async def decide(
        self, token: str, principal: Principal, decision: Literal["approve", "reject"], note: str
    ) -> dict[str, Any]:
        async with self.db.sessions.begin() as session:
            row = await session.scalar(
                select(ApprovalRow).where(ApprovalRow.token == token).with_for_update()
            )
            if row is None:
                raise NotFound("approval not found")
            request = ApprovalRequest.model_validate(row.request)
            if principal.role not in (request.requested_role, "admin"):
                raise Forbidden("role cannot decide this approval")
            fingerprint = digest(
                {
                    "token": token,
                    "principal": principal.model_dump(),
                    "decision": decision,
                    "note": note,
                }
            )
            replay = row.fingerprint is not None
            if replay and row.fingerprint != fingerprint:
                raise Conflict("conflicting approval replay")
            if not replay:
                row.fingerprint = fingerprint
                row.approver, row.approver_role = principal.name, principal.role
                row.decision, row.note, row.decided_at = decision, redact_text(note), now()
            session.add(
                EventRow(
                    run_id=row.run_id,
                    thread_id=row.thread_id,
                    event_type="approval_decided",
                    node="human_gate",
                    payload={
                        "token": token,
                        "decision": decision,
                        "approver": principal.name,
                        "replay": replay,
                    },
                )
            )
            return {
                "token": token,
                "decision": row.decision,
                "run_id": row.run_id,
                "fingerprint": row.fingerprint,
            }

    async def verify(self, request: ApprovalRequest) -> dict[str, Any]:
        async with self.db.sessions() as session:
            row = await session.get(ApprovalRow, request.token)
            if row is None or row.request != request.model_dump(mode="json") or not row.decision:
                raise Forbidden("approval does not match current authorization state")
            if row.approver_role not in (request.requested_role, "admin"):
                raise Forbidden("approval role no longer satisfies request")
            return {
                "token": row.token,
                "decision": row.decision,
                "approver": row.approver,
                "role": row.approver_role,
                "fingerprint": row.fingerprint,
            }
