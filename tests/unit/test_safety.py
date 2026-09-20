import json

import pytest
from pydantic import ValidationError

from safeops.approvals.service import request_for
from safeops.config import Settings
from safeops.domain.models import Action, SupervisorDecision, Ticket, ToolRefused
from safeops.evaluation.runner import evaluate, metrics
from safeops.llm.base import LLMRequest
from safeops.llm.cassette import CassetteMiss, CassetteProvider
from safeops.llm.offline import OfflineProvider
from safeops.memory.service import CustomerMemory
from safeops.observability.redaction import redact, redact_text
from safeops.policy.engine import PolicyEngine
from safeops.tools.registry import RefundArgs, ToolRegistry

TICKET = Ticket(id="T-1", customer_id="CUS-001", text="Refund $45 INV-10032")


def action(amount=4500):
    args = RefundArgs(invoice_ref="INV-10032", amount_cents=amount).model_dump()
    return Action.build(TICKET, "issue_refund", args, "billing")


@pytest.mark.parametrize("amount", [4500, "4500", 4500.0])
def test_canonical_action(amount):
    assert action(amount).action_id == action().action_id


@pytest.mark.parametrize("amount", [True, False, 0, -5, 1000001, 1.5, "NaN"])
def test_refund_invalid(amount):
    with pytest.raises(ValidationError):
        action(amount)


@pytest.mark.parametrize(
    "raw",
    [
        "garbage",
        "{}",
        "[]",
        "null",
        '{"route":"wire","confidence":1}',
        '{"route":"billing","confidence":true}',
        '{"route":"billing","confidence":1.1}',
        '{"route":"billing","confidence":0.9,"approved":true}',
    ],
)
def test_strict_router(raw):
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate_json(raw)


@pytest.mark.parametrize(
    "field,value",
    [("thread", "other"), ("amount", 6000), ("customer", "CUS-002"), ("policy", "next")],
)
def test_approval_binding(field, value):
    config = Settings()
    engine = PolicyEngine(config, ToolRegistry())
    base = request_for("run", TICKET, action(), engine.assess(action()))
    other_action = action(value) if field == "amount" else action()
    ticket = TICKET.model_copy(update={"customer_id": value}) if field == "customer" else TICKET
    policy = engine.assess(other_action)
    if field == "policy":
        policy = policy.model_copy(update={"version": value})
    changed = request_for(value if field == "thread" else "run", ticket, other_action, policy)
    assert changed.token != base.token
    assert request_for("run", TICKET, action(), engine.assess(action())) == base


@pytest.mark.parametrize(
    "amount,expected",
    [
        (1, "ALLOW"),
        (1000, "ALLOW"),
        (1001, "REQUIRE_APPROVAL"),
        (4500, "REQUIRE_APPROVAL"),
        (100001, "DENY"),
    ],
)
def test_policy_thresholds(amount, expected):
    assert PolicyEngine(Settings(), ToolRegistry()).assess(action(amount)).decision == expected


@pytest.mark.parametrize(
    "tool", ["reset_credentials", "lock_account", "cancel_subscription", "change_plan"]
)
def test_all_sensitive_actions_require_review(tool):
    registry = ToolRegistry()
    spec = registry.require(tool)
    args = {"account_ref": "ACC-2041"}
    if tool == "change_plan":
        args["plan"] = "pro"
    planned = Action.build(TICKET, tool, spec.validate(args), spec.domain)
    assert PolicyEngine(Settings(), registry).assess(planned).decision == "REQUIRE_APPROVAL"


def test_domain_allowlist():
    with pytest.raises(ToolRefused):
        ToolRegistry().require("reset_credentials", "billing")
    with pytest.raises(ToolRefused):
        ToolRegistry().require("wire_transfer")


async def test_cassette_strict_and_record(tmp_path):
    request = LLMRequest(tag="supervisor", system="s", user="INV-10032")
    path = tmp_path / "cassette.json"
    record = CassetteProvider(path, "record", OfflineProvider())
    expected = await record.complete(request)
    for mode in ("replay", "strict_replay"):
        replay = CassetteProvider(path, mode)
        assert await replay.complete(request) == expected
        with pytest.raises(CassetteMiss):
            await replay.complete(request.model_copy(update={"user": "missing"}))


def test_redaction_and_memory_schema():
    assert redact({"api_key": "hidden", "nested": {"password": "hidden"}}) == {
        "api_key": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
    }
    assert "hidden" not in redact_text("password=hidden Bearer hidden")
    with pytest.raises(ValidationError):
        CustomerMemory.model_validate({"password": "x"})


async def test_evaluation_is_measured():
    report = await evaluate(Settings())
    assert report["passed"]
    assert report["database"] is None
    assert report["unmeasured"]
    assert report["metrics"]["full_tool_call_accuracy"] == 1
    rows = report["cases"]
    rows[1]["tool_ok"] = False
    assert metrics(rows)["full_tool_call_accuracy"] < 1


def test_config_rejects_unsafe_production_defaults():
    with pytest.raises(ValidationError):
        Settings(environment="production")
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///test")
    assert json.loads(TICKET.model_dump_json())["customer_id"] == "CUS-001"
