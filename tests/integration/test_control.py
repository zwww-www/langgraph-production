import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from safeops.api.app import create_app
from safeops.domain.models import Action, Conflict, Forbidden, NotFound, Principal, Ticket, now
from safeops.persistence.models import (
    ActionHistoryRow,
    ControlStateRow,
    OutcomeRow,
    PolicyRevisionRow,
)
from safeops.policy.control import CompileInput, ControlPlane, ReleaseInput
from safeops.risk.history import load_history
from safeops.risk.models import BusinessSLO, Observation
from safeops.risk.outcomes import observe

pytestmark = pytest.mark.integration
ADMIN = Principal(name="control.test", role="admin")
OPERATOR = Principal(name="operator.test", role="operator")


@pytest.fixture
async def compiled(runtime):
    service = ControlPlane(runtime.db)
    await service.put_slo(
        BusinessSLO(
            target_auto_resolution_rate=0,
            p95_resolution_seconds=100000,
            max_expected_loss_per_day_cents=100000000,
            max_expected_loss_per_window_cents=1000000000,
        ),
        ADMIN,
    )
    dataset = await service.generate(2000, 42, ADMIN)
    report = await service.compile(
        CompileInput(source="synthetic", dataset_id=dataset["id"]), ADMIN
    )
    candidate = await service.candidate(report["recommended"])
    body = ReleaseInput(
        note="Reviewed risk and workload",
        diff_digest=candidate["diff_digest"],
        expected_revision=candidate["base_revision"],
    )
    return service, candidate, body, report


async def test_release_permissions_idempotency_and_immutability(runtime, compiled):
    service, candidate, body, _ = compiled
    assert (await service.registry.active()).revision == "bootstrap-conservative"
    with pytest.raises(Forbidden):
        await service.release(candidate["id"], body, OPERATOR)
    with pytest.raises(Conflict):
        await service.release(
            candidate["id"], body.model_copy(update={"diff_digest": "wrong"}), ADMIN
        )
    assert not (await service.release(candidate["id"], body, ADMIN))["replay"]
    assert (await service.release(candidate["id"], body, ADMIN))["replay"]
    assert (await service.registry.active()).revision == candidate["id"]
    with pytest.raises(Conflict):
        await service.release(
            candidate["id"], body.model_copy(update={"note": "different intent"}), ADMIN
        )
    with pytest.raises(DBAPIError):
        async with runtime.db.sessions.begin() as session:
            await session.execute(
                update(PolicyRevisionRow)
                .where(PolicyRevisionRow.id == candidate["id"])
                .values(digest="tamper")
            )


async def test_stale_slo_rejected(compiled):
    service, candidate, body, _ = compiled
    await service.put_slo(BusinessSLO(), ADMIN)
    with pytest.raises(Conflict):
        await service.release(candidate["id"], body, ADMIN)


async def test_stale_history_rejected(compiled):
    service, candidate, body, _ = compiled
    await service.generate(10, 99, ADMIN)
    with pytest.raises(Conflict):
        await service.release(candidate["id"], body, ADMIN)


async def test_invalid_candidate_rejected(compiled):
    service, _, _, report = compiled
    # Change the SLO to make all candidates infeasible, compile against this exact snapshot.
    await service.put_slo(
        BusinessSLO(target_auto_resolution_rate=1, max_expected_loss_per_day_cents=0), ADMIN
    )
    from safeops.policy.queries import collection

    dataset = (await collection(service.db, "datasets"))[0]
    failed = await service.compile(
        CompileInput(source="synthetic", dataset_id=dataset["id"]), ADMIN
    )
    assert failed["recommended"] is None
    row = await service.candidate(failed["candidate_ids"][0])
    with pytest.raises(Conflict):
        await service.release(
            row["id"],
            ReleaseInput(
                note="must be rejected",
                diff_digest=row["diff_digest"],
                expected_revision=row["base_revision"],
            ),
            ADMIN,
        )


async def test_release_waits_for_checked_dispatch(compiled):
    service, candidate, body, _ = compiled
    async with service.registry.execution_guard():
        task = asyncio.create_task(service.release(candidate["id"], body, ADMIN))
        await asyncio.sleep(0.05)
        assert not task.done()
    await asyncio.wait_for(task, 5)
    assert (await service.registry.active()).revision == candidate["id"]


async def test_published_runtime_authority(compiled):
    service, candidate, body, _ = compiled
    await service.release(candidate["id"], body, ADMIN)
    ticket = Ticket(id="runtime-authority", customer_id="CUS-001", text="structured request")
    for tool, args, domain, expected in [
        ("issue_refund", {"invoice_ref": "INV-10032", "amount_cents": 200}, "billing", "ALLOW"),
        ("reset_credentials", {"account_ref": "ACC-2041"}, "account", "REQUIRE_APPROVAL"),
        ("issue_refund", {"invoice_ref": "INV-10032", "amount_cents": 100001}, "billing", "DENY"),
    ]:
        result = await service.registry.assess(Action.build(ticket, tool, args, domain))
        assert result.decision == expected and result.version == candidate["id"]


async def test_missing_active_fails_closed(runtime):
    async with runtime.db.sessions.begin() as session:
        state = await session.get(ControlStateRow, 1)
        await session.delete(state)
    with pytest.raises(Forbidden):
        await runtime.deps.policy.active()


async def test_delayed_outcome_identity_history_and_loss(runtime):
    ticket = Ticket(id=uuid4().hex, customer_id="CUS-001", text="Refund $2 INV-10032")
    await runtime.ticket(ticket)
    run_id = await runtime.enqueue(ticket.id)
    state = await runtime.process(run_id)
    await runtime.deps.approvals.decide(
        state["interrupts"][0]["token"], ADMIN, "approve", "verified"
    )
    state = await runtime.process(run_id)
    action = state["values"]["action"]["action_id"]
    assert state["values"]["postcondition"]["verification_status"] == "verified"
    async with runtime.db.sessions() as session:
        assert not list(await session.scalars(select(OutcomeRow)))
    observation = Observation(
        observation_id="o-unknown",
        run_id=run_id,
        action_id=action,
        outcome="unknown",
        observed_at=now(),
        source="human_audit",
    )
    with pytest.raises(Forbidden):
        await observe(runtime.db, observation, OPERATOR)
    with pytest.raises(NotFound):
        await observe(runtime.db, observation.model_copy(update={"action_id": "wrong"}), ADMIN)
    await observe(runtime.db, observation, ADMIN)
    assert (await observe(runtime.db, observation, ADMIN))["replay"]
    async with runtime.db.sessions() as session:
        rows = await load_history(session, None)
        assert rows[0].outcome == "unknown"
    final = observation.model_copy(
        update={
            "observation_id": "o-final",
            "outcome": "disputed",
            "realized_loss_cents": 200,
            "observed_at": now(),
        }
    )
    await observe(runtime.db, final, ADMIN)
    with pytest.raises(Conflict):
        await observe(
            runtime.db,
            final.model_copy(update={"observation_id": "o-conflict", "outcome": "incorrect"}),
            ADMIN,
        )
    async with runtime.db.sessions() as session:
        rows = await load_history(session, None)
        assert (
            len(rows) == 1 and rows[0].outcome == "disputed" and rows[0].realized_loss_cents == 200
        )
        assert len(list(await session.scalars(select(OutcomeRow)))) == 2
        history = await session.get(ActionHistoryRow, f"{run_id}:{action}")
        assert history.receipt and history.postcondition
    assert (await ControlPlane(runtime.db).metrics())["realized_loss_cents"] == 200


async def test_control_api_auth_validation_and_discovery(settings):
    app = create_app(settings, start_worker=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/control/active")).status_code == 401
            client.headers["Authorization"] = "Bearer demo-operator"
            assert (await client.post("/api/control/compile", json={})).status_code == 403
            assert (await client.put("/api/control/slo", json={})).status_code == 403
            client.headers["Authorization"] = "Bearer demo-admin"
            for path in (
                "slo",
                "active",
                "metrics",
                "candidates",
                "frontier",
                "calibrations",
                "datasets",
                "revisions",
                "events",
            ):
                assert (await client.get(f"/api/control/{path}")).status_code == 200
            assert (await client.post("/api/control/history", json={"count": 1})).status_code == 422
            assert (await client.get("/api/control/candidates/missing")).status_code == 404
            assert (await client.post("/api/control/compile", json={})).status_code == 409
