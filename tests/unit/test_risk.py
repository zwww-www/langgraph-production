from datetime import timedelta

import pytest
from pydantic import ValidationError

from safeops.domain.models import Action, Ticket
from safeops.policy.compiler import validate
from safeops.policy.engine import PolicyEngine
from safeops.policy.registry import bootstrap_definition
from safeops.policy.simulator import replay, transitions
from safeops.risk.calibration import calibrate, wilson_upper
from safeops.risk.features import extract
from safeops.risk.history import synthetic_history
from safeops.risk.models import BusinessSLO
from safeops.risk.optimizer import optimize
from safeops.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "bad,n", [(0, 0), (0, 1), (0, 3), (1, 3), (0, 10000), (100, 10000), (10000, 10000)]
)
def test_wilson_conservative(bad, n):
    bound = wilson_upper(bad, n)
    assert 0 < bound <= 1
    if n and bad < n:
        assert bound > bad / n
    if not n:
        assert bound == 1


def test_uncertainty_shrinks():
    assert wilson_upper(0, 3) > 0.4
    assert wilson_upper(0, 3) > wilson_upper(0, 30) > wilson_upper(0, 3000) > 0


@pytest.mark.parametrize("bad,n", [(-1, 2), (3, 2), (0, -1)])
def test_invalid_counts(bad, n):
    with pytest.raises(ValueError):
        wilson_upper(bad, n)


@pytest.mark.parametrize(
    "amount,bucket", [(1, 0), (1000, 0), (1001, 1), (2000, 1), (2001, 2), (100001, 6)]
)
def test_features_boundaries(amount, bucket):
    action = Action.build(
        Ticket(id="t", customer_id="CUS-001", text="x"),
        "issue_refund",
        {"invoice_ref": "INV-10032", "amount_cents": amount},
        "billing",
    )
    feature = extract(action)
    assert feature.amount_bucket == bucket and feature.exposure_cents == amount
    assert "customer_tier" in feature.unavailable
    assert extract(action) == feature
    with pytest.raises(ValidationError):
        extract(action.model_copy(update={"args": {"amount_cents": amount}}))


def test_unknown_and_future_outcomes_excluded():
    rows = synthetic_history(100, 42)
    future = max(r.arrived_at for r in rows) + timedelta(days=10)
    rows = [r.model_copy(update={"outcome": "correct", "observed_at": future}) for r in rows]
    c, _ = calibrate(rows, "synthetic")
    assert c.known_count == 0
    assert all(p.sample_count == 0 and p.risk_upper_bound == 1 for p in c.profiles.values())


@pytest.fixture(scope="module")
def compiled():
    rows = synthetic_history(2000, 42)
    c, h = calibrate(rows, "synthetic")
    base = bootstrap_definition()
    slo = BusinessSLO(
        target_auto_resolution_rate=0,
        p95_resolution_seconds=100000,
        max_expected_loss_per_day_cents=100000000,
        max_expected_loss_per_window_cents=1000000000,
    )
    return base, slo, c, h, optimize(base, slo, c, h)


def test_optimizer_deterministic_and_multiple(compiled):
    base, slo, c, h, candidates = compiled
    assert candidates == optimize(base, slo, c, h)
    assert sum(x.feasible for x in candidates) > 1
    assert any(x.frontier for x in candidates)
    assert all(not x.frontier or x.feasible for x in candidates)


def test_budget_capacity_sla_infeasible(compiled):
    base, slo, c, h, _ = compiled
    capacity = slo.capacities[0].model_copy(update={"reviewers": 0})
    constrained = slo.model_copy(
        update={
            "max_expected_loss_per_day_cents": 0,
            "p95_resolution_seconds": 1,
            "capacities": (capacity, *slo.capacities[1:]),
            "target_auto_resolution_rate": 1.0,
        }
    )
    candidates = optimize(base, constrained, c, h)
    assert not any(x.feasible for x in candidates)
    reasons = {e for x in candidates for e in x.errors}
    assert {
        "daily risk budget exceeded",
        "review capacity exceeded",
        "resolution SLA exceeded",
    } <= reasons


def test_hard_constraints_and_monotonicity(compiled):
    base, slo, c, h, candidates = compiled
    p = candidates[-1].policy
    assert "risk monotonicity violation" in validate(
        p.model_copy(update={"auto_limits": (0, 5000, 10000)}), slo
    )
    assert "hard deny boundary relaxed" in validate(
        p.model_copy(update={"slo": slo.model_copy(update={"hard_deny_amount_cents": 200000})}), slo
    )
    assert "mandatory approval constraint relaxed" in validate(
        p.model_copy(update={"slo": slo.model_copy(update={"mandatory_approval_tools": ()})}), slo
    )
    for row in h:
        if row.features.exposure_cents > slo.hard_deny_amount_cents:
            assert PolicyEngine(p, ToolRegistry()).assess(row.action).decision == "DENY"


def test_replay_pure_and_transition_counts(compiled, monkeypatch):
    from safeops.llm.offline import OfflineProvider

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not call LLM")

    monkeypatch.setattr(OfflineProvider, "complete", forbidden)
    base, _, _, h, candidates = compiled
    diff = replay(base, candidates[-1].policy, h)
    assert sum(diff.transitions.values()) == len(h)
    assert diff.changed_count == len(h) - diff.transitions["unchanged"]
    assert diff.transitions["REVIEW->AUTO"] > 0
    assert transitions(["DENY", "REVIEW", "AUTO"], ["AUTO", "AUTO", "AUTO"]) == {
        "AUTO->REVIEW": 0,
        "AUTO->DENY": 0,
        "REVIEW->AUTO": 1,
        "REVIEW->DENY": 0,
        "DENY->AUTO": 1,
        "DENY->REVIEW": 0,
        "unchanged": 1,
    }


def test_synthetic_reproducible():
    assert synthetic_history(200, 7) == synthetic_history(200, 7)
    assert synthetic_history(200, 7) != synthetic_history(200, 8)


def test_amount_must_not_become_easier_after_sparse_segment(compiled):
    base, slo, calibration, _, _ = compiled
    profile = next(iter(calibration.profiles.values())).model_copy(
        update={
            "segment": "issue_refund:1",
            "sample_count": 10000,
            "unknown_count": 0,
            "risk_upper_bound": 0.01,
        }
    )
    cal = calibration.model_copy(update={"profiles": {"issue_refund:1": profile}})
    policy = base.model_copy(
        update={"slo": slo, "calibration": cal, "auto_limits": (100000, 100000, 100000)}
    )
    assert "amount monotonicity violation" in validate(policy, slo)
