import asyncio

import pytest
from sqlalchemy import select

from safeops.domain.models import Action, Conflict, EffectInDoubt, Principal, Ticket
from safeops.effects.reconcile import Resolution, reconcile
from safeops.faults import Faults, SimulatedCrash
from safeops.persistence.models import EffectRow, ExternalOperationRow

pytestmark = pytest.mark.integration


async def planned(runtime):
    ticket = Ticket(id="effect", customer_id="CUS-001", text="Reset credentials ACC-2041")
    await runtime.ticket(ticket)
    run_id = await runtime.enqueue(ticket.id)
    spec = runtime.deps.registry.require("reset_credentials")
    action = Action.build(
        ticket, spec.name, spec.validate({"account_ref": "ACC-2041"}), spec.domain
    )
    return run_id, action


async def test_concurrent_claims_invoke_handler_once(runtime):
    run_id, action = await planned(runtime)
    original = runtime.deps.registry.require(action.tool).handler
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow(args, key, customer):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(args, key, customer)

    runtime.deps.registry.bind(action.tool, slow)
    first = asyncio.create_task(runtime.deps.effects.perform(run_id, "CUS-001", action))
    await entered.wait()
    try:
        with pytest.raises(EffectInDoubt):
            await runtime.deps.effects.perform(run_id, "CUS-001", action)
    finally:
        release.set()
    await first
    replay = await runtime.deps.effects.perform(run_id, "CUS-001", action)
    assert replay.replayed and calls == 1


async def test_timeout_remains_in_doubt(runtime):
    run_id, action = await planned(runtime)

    async def timeout(args, key, customer):
        raise TimeoutError("network uncertain")

    runtime.deps.registry.bind(action.tool, timeout)
    for _ in range(2):
        with pytest.raises(EffectInDoubt):
            await runtime.deps.effects.perform(run_id, "CUS-001", action)
    async with runtime.db.sessions() as session:
        row = await session.get(EffectRow, f"{run_id}:{action.action_id}")
        assert row.status == "in_doubt"
        assert row.attempt == 1


async def test_reconciliation_rejects_contradictory_evidence(runtime):
    run_id, action = await planned(runtime)
    runtime.deps.effects.faults = Faults("effect_recorded")
    with pytest.raises(SimulatedCrash):
        await runtime.deps.effects.perform(run_id, "CUS-001", action)
    with pytest.raises(Conflict, match="contradicts"):
        await reconcile(
            runtime.db,
            f"{run_id}:{action.action_id}",
            Resolution(outcome="not_performed", evidence="manual check"),
            Principal(name="admin", role="admin"),
        )


async def test_reconciliation_cannot_release_active_worker(runtime):
    run_id, action = await planned(runtime)
    runtime.deps.effects.faults = Faults("effect_claimed")
    with pytest.raises(SimulatedCrash):
        await runtime.deps.effects.perform(run_id, "CUS-001", action)
    async with runtime.db.run_lock(run_id):
        with pytest.raises(Conflict, match="active"):
            await reconcile(
                runtime.db,
                f"{run_id}:{action.action_id}",
                Resolution(outcome="not_performed", evidence="manual check"),
                Principal(name="admin", role="admin"),
            )


async def test_concurrent_approval_conflict(runtime):
    from safeops.domain.models import Ticket

    await runtime.ticket(
        Ticket(id="approval-race", customer_id="CUS-001", text="Refund $45 INV-10032")
    )
    run_id = await runtime.enqueue("approval-race")
    result = await runtime.process(run_id)
    token = result["interrupts"][0]["token"]
    admin = Principal(name="reviewer", role="admin")
    results = await asyncio.gather(
        runtime.deps.approvals.decide(token, admin, "approve", "same"),
        runtime.deps.approvals.decide(token, admin, "reject", "same"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, Conflict) for r in results) == 1
    async with runtime.db.sessions() as session:
        assert not list(await session.scalars(select(ExternalOperationRow)))
