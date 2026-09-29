import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from safeops.approvals.service import request_for
from safeops.domain.models import Action, Conflict, Forbidden, Principal, Ticket
from safeops.effects.reconcile import Resolution, reconcile
from safeops.evaluation.recovery import evaluate_database, settle
from safeops.faults import CRASH_POINTS, Faults, SimulatedCrash
from safeops.graph.runtime import Runtime
from safeops.llm.base import LLMResponse
from safeops.persistence.models import (
    ApprovalRow,
    EffectRow,
    ExternalOperationRow,
)
from safeops.policy.models import PolicyDecision

pytestmark = pytest.mark.integration
ADMIN = Principal(name="test.admin", role="admin")


async def start(runtime, text="Refund $45 on INV-10032", customer="CUS-001"):
    ticket = Ticket(id=uuid4().hex, customer_id=customer, text=text)
    await runtime.ticket(ticket)
    run_id = await runtime.enqueue(ticket.id)
    return run_id, await runtime.process(run_id)


async def count(runtime, run_id):
    async with runtime.db.sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(ExternalOperationRow)
            .where(ExternalOperationRow.key.startswith(run_id + ":"))
        )


async def test_read_only_no_approval(runtime, caplog):
    caplog.set_level("INFO", logger="safeops")
    run_id, result = await start(runtime, "查一下 INV-10032")
    assert not result["interrupts"]
    assert result["values"]["receipt"]["result"]["total_cents"] == 9000
    assert result["values"]["trail"] == [
        "supervisor",
        "billing:plan",
        "policy",
        "execute",
        "verify",
        "compose",
    ]
    assert await count(runtime, run_id) == 0
    events = [json.loads(r.message) for r in caplog.records if r.name == "safeops"]
    assert events
    assert all(e["ticket_id"] == result["values"]["ticket"]["id"] for e in events)


async def test_refund_suspend_fresh_resume(settings):
    async with Runtime(settings) as first:
        run_id, result = await start(first)
        token = result["interrupts"][0]["token"]
        assert await count(first, run_id) == 0
        await first.deps.approvals.decide(token, ADMIN, "approve", "verified")
    async with Runtime(settings) as fresh:
        result = await fresh.process(run_id)
        assert result["values"]["receipt"]["result"]["refunded_cents"] == 4500
        assert await count(fresh, run_id) == 1
        await fresh.process(run_id)
        assert await count(fresh, run_id) == 1


async def test_approval_replay_authorization(runtime):
    run_id, result = await start(runtime)
    token = result["interrupts"][0]["token"]
    with pytest.raises(Forbidden):
        await runtime.deps.approvals.decide(
            token, Principal(name="low", role="operator"), "approve", "yes"
        )
    a = await runtime.deps.approvals.decide(token, ADMIN, "approve", "yes")
    assert await runtime.deps.approvals.decide(token, ADMIN, "approve", "yes") == a
    for verdict, note, who in [
        ("reject", "yes", ADMIN),
        ("approve", "changed", ADMIN),
        ("approve", "yes", Principal(name="other", role="admin")),
    ]:
        with pytest.raises(Conflict):
            await runtime.deps.approvals.decide(token, who, verdict, note)
    state = result["values"]
    request = request_for(
        "different-run",
        Ticket.model_validate(state["ticket"]),
        Action.model_validate(state["action"]),
        PolicyDecision.model_validate(state["policy"]),
    )
    with pytest.raises(Forbidden):
        await runtime.deps.approvals.verify(request)
    assert await count(runtime, run_id) == 0


async def test_reject_never_executes(runtime):
    run_id, result = await start(runtime)
    await runtime.deps.approvals.decide(
        result["interrupts"][0]["token"], ADMIN, "reject", "not eligible"
    )
    result = await runtime.process(run_id)
    assert result["values"]["outcome"] == "denied"
    assert await count(runtime, run_id) == 0


@pytest.mark.parametrize("text", ["Refund $4500000 INV-10032", "Refund $2000 INV-10032"])
async def test_unsafe_refund_never_executes(runtime, text):
    run_id, result = await start(runtime, text)
    if result["interrupts"]:
        await runtime.deps.approvals.decide(
            result["interrupts"][0]["token"], ADMIN, "approve", "handoff"
        )
        result = await runtime.process(run_id)
    assert not result["values"].get("receipt")
    assert await count(runtime, run_id) == 0


async def test_cross_customer_ownership(runtime):
    run_id, result = await start(runtime, "Refund $45 INV-10077")
    await runtime.deps.approvals.decide(
        result["interrupts"][0]["token"], ADMIN, "approve", "review"
    )
    result = await runtime.process(run_id)
    assert result["values"]["outcome"] == "refused"
    assert await count(runtime, run_id) == 0


@pytest.mark.parametrize(
    "raw",
    ["not JSON", '{"route":"unknown","confidence":0.99}', '{"route":"account","confidence":0.1}'],
)
async def test_bad_router_fails_closed(settings, raw):
    class Bad:
        async def complete(self, request):
            return LLMResponse(text=raw, model="fake")

    async with Runtime(settings, provider=Bad()) as runtime:
        run_id, result = await start(runtime)
        assert result["interrupts"]
        assert result["values"]["domain"] == "escalate"
        assert await count(runtime, run_id) == 0


@pytest.mark.parametrize("point", CRASH_POINTS)
async def test_every_crash_boundary(settings, point):
    faults = Faults(point)
    async with Runtime(settings, faults=faults) as runtime:
        ticket = Ticket(id="crash-case", customer_id="CUS-001", text="Reset credentials ACC-2041")
        await runtime.ticket(ticket)
        run_id = await runtime.enqueue(ticket.id)
        with pytest.raises(SimulatedCrash):
            await settle(runtime, run_id)
        assert faults.fired
    async with Runtime(settings) as fresh:
        result = await settle(fresh, run_id)
        status = (await fresh.get_run(run_id))["status"]
        if point in ("effect_claimed", "external_effect_executed", "effect_recorded"):
            assert status == "in_doubt"
            await fresh.process(run_id)
            assert await count(fresh, run_id) == (0 if point == "effect_claimed" else 1)
        else:
            assert status == "completed"
            assert await count(fresh, run_id) == 1
            assert result["values"]["trail"] == [
                "supervisor",
                "account:plan",
                "policy",
                "human_gate",
                "execute",
                "verify",
                "compose",
            ]


@pytest.mark.parametrize(
    "point,outcome",
    [
        ("effect_claimed", "not_performed"),
        ("external_effect_executed", "performed"),
        ("effect_recorded", "performed"),
    ],
)
async def test_reconcile_both_directions(settings, point, outcome):
    async with Runtime(settings, faults=Faults(point)) as first:
        ticket = Ticket(id="reconcile", customer_id="CUS-001", text="Reset credentials ACC-2041")
        await first.ticket(ticket)
        run_id = await first.enqueue(ticket.id)
        with pytest.raises(SimulatedCrash):
            await settle(first, run_id)
    async with Runtime(settings) as fresh:
        await fresh.process(run_id)
        async with fresh.db.sessions() as session:
            effect = await session.scalar(select(EffectRow).where(EffectRow.run_id == run_id))
            external = await session.get(ExternalOperationRow, effect.idempotency_key)
        resolution = Resolution(
            outcome=outcome,
            evidence="Checked downstream by stable key",
            result=external.result if external else None,
        )
        await reconcile(fresh.db, effect.idempotency_key, resolution, ADMIN)
        result = await fresh.process(run_id)
        assert result["values"]["receipt"]["replayed"] == (outcome == "performed")
        assert await count(fresh, run_id) == 1
        with pytest.raises(Conflict):
            await reconcile(fresh.db, effect.idempotency_key, resolution, ADMIN)


async def test_idempotent_ticket_run_and_lock(runtime):
    ticket = Ticket(id="stable", customer_id="CUS-001", text="INV-10032")
    await runtime.ticket(ticket)
    ids = await asyncio.gather(*(runtime.enqueue(ticket.id) for _ in range(5)))
    assert len(set(ids)) == 1
    with pytest.raises(Conflict):
        await runtime.ticket(ticket.model_copy(update={"text": "different"}))
    async with runtime.db.run_lock(ids[0]):
        with pytest.raises(Conflict):
            await runtime.process(ids[0])


async def test_memory_cross_thread_and_safe_replay(runtime):
    run_id, result = await start(runtime, "查一下 INV-10032")
    memory = await runtime.deps.memory.get("CUS-001")
    assert memory.preferred_language == "zh"
    assert result["values"]["ticket"]["id"] in memory.recent_ticket_ids
    run_id, result = await start(runtime)
    checkpoints = await runtime.checkpoints(run_id)
    checkpoint = next(c for c in checkpoints if c["values"].get("action"))
    replay_id = await runtime.replay(run_id, checkpoint["checkpoint_id"])
    replay = await runtime.process(replay_id)
    assert replay["values"]["receipt"]["dry_run"]
    assert await count(runtime, replay_id) == await count(runtime, run_id) == 0


async def test_database_evaluation(settings):
    report = await evaluate_database(settings)
    assert report["passed"], report


@pytest.mark.parametrize("text", ["查一下 INV-10032", "Refund $45 INV-10032"])
async def test_fork_every_populated_checkpoint(runtime, text):
    run_id, _ = await start(runtime, text)
    await settle(runtime, run_id)
    for checkpoint in await runtime.checkpoints(run_id):
        if not checkpoint["values"]:
            continue
        replay_id = await runtime.replay(run_id, checkpoint["checkpoint_id"])
        result = await runtime.process(replay_id)
        assert (await runtime.get_run(replay_id))["status"] == "completed"
        assert result["values"]["receipt"]["dry_run"]
        assert await count(runtime, replay_id) == 0
        async with runtime.db.sessions() as session:
            assert not await session.scalar(
                select(ApprovalRow).where(ApprovalRow.run_id == replay_id)
            )


async def test_sensitive_tool_gate_registry(runtime):
    from safeops.evaluation.fixtures import CASES

    for case in CASES:
        if case.policy == "REQUIRE_APPROVAL":
            run_id, result = await start(runtime, case.text)
            assert result["interrupts"], case.text
            assert await count(runtime, run_id) == 0


async def test_policy_rechecked_at_execution(runtime):
    run_id, result = await start(runtime)
    await runtime.deps.approvals.decide(result["interrupts"][0]["token"], ADMIN, "approve", "yes")
    from safeops.policy.control import CompileInput, ControlPlane, ReleaseInput
    from safeops.risk.models import BusinessSLO

    control = ControlPlane(runtime.db)
    await control.put_slo(
        BusinessSLO(
            target_auto_resolution_rate=0,
            p95_resolution_seconds=100000,
            max_expected_loss_per_day_cents=100000000,
            max_expected_loss_per_window_cents=1000000000,
        ),
        ADMIN,
    )
    dataset = await control.generate(2000, 42, ADMIN)
    report = await control.compile(
        CompileInput(source="synthetic", dataset_id=dataset["id"]), ADMIN
    )
    candidate = await control.candidate(report["recommended"])
    await control.release(
        candidate["id"],
        ReleaseInput(
            note="Reviewed changed policy",
            diff_digest=candidate["diff_digest"],
            expected_revision=candidate["base_revision"],
        ),
        ADMIN,
    )
    with pytest.raises(Forbidden):
        await runtime.process(run_id)
    assert await count(runtime, run_id) == 0
