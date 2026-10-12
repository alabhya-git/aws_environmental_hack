import numpy as np
import pytest

from proofplate.model import Observation, fit
from proofplate.planning import DinerForecast, KitchenInputs, ServingConfig, evaluate_portion, plan, recommend

CFG = ServingConfig(scoop_g=60.0)


def enough(g, tol=6):
    return Observation("interval", g - tol, g + tol)


def test_withdrawal_formula_and_rounding():
    vals = np.array([100.0, 180.0, 181.0, 260.0])
    wts = np.full(4, 0.25)
    ev = evaluate_portion(vals, wts, s_g=180.0, r_g=30.0)
    # withdrawn: 180, 180, 210, 270 -> leftover 80, 0, 29, 10
    assert ev["expected_withdrawn_g"] == pytest.approx((180 + 180 + 210 + 270) / 4)
    assert ev["expected_leftover_g"] == pytest.approx((80 + 0 + 29 + 10) / 4)
    assert ev["expected_refill_trips"] == pytest.approx((0 + 0 + 1 + 3) / 4)


def test_zero_portion_is_never_recommended_even_with_tiny_demand():
    post = fit([enough(8, tol=4)] * 12)
    rec = recommend(post, CFG)
    assert rec.scoops >= CFG.min_scoops > 0 and rec.grams > 0
    assert all(row["scoops"] > 0 for row in rec.tradeoff)


def test_requested_portion_is_honoured_and_never_overridden():
    post = fit([enough(120)] * 10)
    rec = recommend(post, CFG, requested_scoops=5)
    assert rec.scoops == 5 and rec.basis == "requested"
    with pytest.raises(ValueError):
        recommend(post, CFG, requested_scoops=0)


def test_cold_start_uses_conventional_then_cohort_then_personal():
    assert recommend(None, CFG).basis == "conventional"
    cohort = fit([enough(200)] * 8)
    assert recommend(None, CFG, cohort=cohort).basis == "cohort"
    assert recommend(fit([enough(200)]), CFG, cohort=cohort).basis == "personal"


def test_opt_out_gets_the_conventional_portion():
    rec = recommend(fit([enough(100)] * 9), CFG, opted_out=True)
    assert rec.scoops == CFG.conventional_scoops and rec.basis == "opted_out"


def test_change_limit_applies_in_both_directions():
    post = fit([enough(420)] * 12)
    up = recommend(post, CFG, last_suggested_scoops=2.0)
    assert up.scoops == 3.0 and "CHANGE_LIMITED" in up.reasons
    down = recommend(fit([enough(70)] * 12), CFG, last_suggested_scoops=5.0)
    assert down.scoops == 4.0 and "CHANGE_LIMITED" in down.reasons


def test_without_accessible_seconds_the_portion_is_not_smaller():
    post = fit([enough(g) for g in (150, 180, 210, 240, 170, 200)])
    with_sec = recommend(post, CFG)
    without = recommend(post, ServingConfig(scoop_g=60.0, seconds_accessible=False))
    assert without.scoops >= with_sec.scoops
    assert "SECONDS_NOT_ACCESSIBLE_COVERAGE_RULE" in without.reasons


def test_refill_friction_moves_the_first_portion_up():
    post = fit([enough(g) for g in (150, 180, 210, 240, 170, 200, 260, 140)])
    cheap = recommend(post, ServingConfig(scoop_g=60.0, refill_friction_g=0.0, max_p_two_trips=1.0))
    costly = recommend(post, ServingConfig(scoop_g=60.0, refill_friction_g=120.0, max_p_two_trips=1.0))
    assert costly.scoops > cheap.scoops


# ---------------- kitchen ----------------

def _cohort(median=190.0, sigma=0.25, first=180.0):
    return DinerForecast(lambda rng, n: rng.lognormal(np.log(median), sigma, n), first)


def _inputs(**kw):
    base = dict(expected_attendance=120, scoop_g=60.0, refill_g=30.0, n_sims=1500, seed=11,
                refill_capacity_per_interval=90)
    base.update(kw)
    return KitchenInputs(**base)


def test_plan_covers_withdrawals_not_just_consumption():
    out = plan(_inputs(), [], _cohort())
    f = out["forecast"]
    assert f["withdrawals_g"]["p50"] > f["consumed_g"]["p50"]      # rounding + plate leftover
    assert out["planned_total_g"] >= f["withdrawals_g"]["p90"]
    assert out["status"] == "feasible"
    assert out["risk"]["modelled_shortage_risk"] <= out["risk"]["target_alpha"] + 1e-9


def test_plan_is_reproducible_for_a_fixed_seed():
    a, b = plan(_inputs(), [], _cohort()), plan(_inputs(), [], _cohort())
    assert a == b
    assert plan(_inputs(seed=12), [], _cohort())["forecast"] != a["forecast"]


def test_more_attendance_needs_more_food():
    small = plan(_inputs(expected_attendance=80), [], _cohort())
    large = plan(_inputs(expected_attendance=240), [], _cohort())
    assert large["planned_total_g"] > small["planned_total_g"]


def test_insufficient_stock_is_reported_not_ignored():
    out = plan(_inputs(stock_cooked_equiv_g=10000), [], _cohort())
    assert out["status"] == "infeasible"
    reason = next(r for r in out["infeasible_reasons"] if r["code"] == "STOCK")
    assert reason["detail"]["required_g"] > reason["detail"]["stock_g"]
    assert reason["detail"]["modelled_risk_with_stock"] > 0.05
    assert out["planned_total_g"] <= 10000        # the plan never pretends to have more food
    assert reason["options"]


def test_refill_capacity_shortfall_is_infeasible():
    out = plan(_inputs(refill_capacity_per_interval=1), [], _cohort(first=90.0))
    assert out["status"] == "infeasible"
    assert any(r["code"] == "REFILL_CAPACITY" for r in out["infeasible_reasons"])
    assert out["forecast"]["denied_refill_share"] > 0.02


def test_long_lead_time_pushes_cooking_before_service():
    fast = plan(_inputs(lead_intervals=1), [], _cohort())
    slow = plan(_inputs(lead_intervals=6), [], _cohort())
    assert slow["initial_cook_g"] >= fast["initial_cook_g"]
    assert slow["reserve_g"] == 0


def test_availability_is_checked_through_time_not_only_in_total():
    out = plan(_inputs(), [], _cohort())
    for row in out["forecast"]["cumulative_by_interval"]:
        assert row["available_g"] + 1e-6 >= row["p50_g"]


def test_shared_meal_effect_widens_the_reserve():
    out = plan(_inputs(meal_effect_sd=0.15, attendance_cv=0.15), [], _cohort())
    own, indep = out["sensitivity"]
    assert own["withdrawals_p95_g"] > indep["withdrawals_p95_g"]


def test_known_diners_with_larger_first_portions_change_the_forecast():
    small = [DinerForecast(lambda rng, n: rng.lognormal(np.log(120), 0.2, n), 120.0)] * 60
    big = [DinerForecast(lambda rng, n: rng.lognormal(np.log(320), 0.2, n), 300.0)] * 60
    a = plan(_inputs(expected_attendance=60, attendance_cv=0.0), small, _cohort())
    b = plan(_inputs(expected_attendance=60, attendance_cv=0.0), big, _cohort())
    assert b["forecast"]["withdrawals_g"]["p50"] > 2 * a["forecast"]["withdrawals_g"]["p50"]


def test_compute_is_bounded_for_large_requests():
    out = plan(_inputs(expected_attendance=3000, n_sims=5000, stock_cooked_equiv_g=2_000_000, batch_g=20000,
                       refill_capacity_per_interval=2000), [], _cohort())
    assert out["risk"]["n_sims"] <= 500
