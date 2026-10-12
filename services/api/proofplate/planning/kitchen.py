"""Constrained kitchen preparation plan.

Forecasts serving *withdrawals* (first portions plus refills, with portion
rounding), not just consumed demand, then checks availability chronologically
through the service window against batch size, cooking lead time, stock and
refill-station capacity. All probabilities are modelled under the stated
assumptions with fixed-seed Monte Carlo; none is a guarantee.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

PLANNER_VERSION = "kitchen-plan/1.0"
MAX_DRAWS = 1_500_000  # bounds compute for the public endpoint


@dataclass(frozen=True)
class DinerForecast:
    """One expected diner: a sampler for D and the first portion they would get."""

    sampler: Callable[[np.random.Generator, int], np.ndarray]
    first_portion_g: float


@dataclass(frozen=True)
class KitchenInputs:
    expected_attendance: int
    scoop_g: float
    refill_g: float
    attendance_cv: float = 0.10          # assumption: spread of attendance around the expectation
    meal_effect_sd: float = 0.08         # assumption: shared log-scale effect of this meal/day
    intervals: int = 6
    interval_minutes: int = 15
    arrival_profile: Sequence[float] | None = None
    batch_g: int = 5000
    lead_intervals: int = 2
    max_parallel_batches: int = 2
    stock_cooked_equiv_g: int = 60000
    refill_capacity_per_interval: int = 60
    refill_uptake: float = 0.85          # assumption: chance a diner who wants more walks back each time
    alpha: float = 0.05                  # modelled shortage-risk target
    denied_refill_tolerance: float = 0.02
    n_sims: int = 2000
    seed: int = 20261011


def _arrival_profile(inp: KitchenInputs) -> np.ndarray:
    if inp.arrival_profile is not None:
        p = np.asarray(inp.arrival_profile, float)
        if len(p) != inp.intervals or p.min() < 0 or p.sum() <= 0:
            raise ValueError("arrival_profile must have one non-negative share per interval")
    else:  # front-loaded default: most diners arrive early in the window
        p = np.linspace(1.6, 0.4, inp.intervals)
    return p / p.sum()


def _simulate(inp: KitchenInputs, known: Sequence[DinerForecast], cohort: DinerForecast, *, meal_sd: float, att_cv: float):
    S, T = inp.n_sims, inp.intervals
    rng = np.random.default_rng(inp.seed)
    K = len(known)

    att = np.maximum(1, np.rint(inp.expected_attendance * np.exp(att_cv * rng.standard_normal(S) - 0.5 * att_cv**2))).astype(int)
    n_max = int(att.max())
    U = max(n_max - K, 0)

    D = np.empty((K + U, S))
    first = np.empty(K + U)
    for k, f in enumerate(known):
        D[k] = f.sampler(rng, S)
        first[k] = f.first_portion_g
    if U:
        D[K:] = cohort.sampler(rng, U * S).reshape(U, S)
        first[K:] = cohort.first_portion_g

    # who attends: a random subset of the known roster, then cold-start diners
    present = np.zeros((K + U, S), dtype=bool)
    if K:
        rank = np.argsort(np.argsort(rng.random((K, S)), axis=0), axis=0)
        present[:K] = rank < np.minimum(att, K)[None, :]
    if U:
        present[K:] = np.arange(U)[:, None] < np.maximum(att - K, 0)[None, :]

    D = D * np.exp(meal_sd * rng.standard_normal(S) - 0.5 * meal_sd**2)[None, :]

    r = inp.refill_g
    need = np.ceil(np.maximum(D - first[:, None], 0.0) / r - 1e-9)
    if inp.refill_uptake >= 1.0:
        willing = np.full(D.shape, np.inf)
    else:
        willing = np.floor(np.log(rng.random(D.shape)) / math.log(inp.refill_uptake))
    arrive = rng.choice(T, size=D.shape, p=_arrival_profile(inp))
    trips = np.minimum(need, willing)
    trips = np.where(present, trips, 0.0)

    sim_idx = np.broadcast_to(np.arange(S)[None, :], D.shape)
    flat = lambda t_idx, mask: np.bincount((sim_idx[mask] * T + t_idx[mask]).ravel(), minlength=S * T).reshape(S, T)

    first_w = np.zeros((S, T))
    np.add.at(first_w, (sim_idx[present], arrive[present]), np.broadcast_to(first[:, None], D.shape)[present])

    # refill j is requested j intervals after arrival. Diners already seated at
    # the end are still served in the final interval; whatever is still queued
    # when that interval ends is counted as denied.
    requests = np.zeros((S, T))
    late = np.zeros(S)
    for j in range(1, 9):
        m = trips >= j
        if not m.any():
            break
        requests += flat(np.minimum(arrive + j, T - 1), m)
    capped_trips = np.minimum(trips, 8)

    # refill station queue: requests beyond capacity wait for the next interval
    served = np.zeros((S, T))
    queue = np.zeros(S)
    for t in range(T):
        queue = queue + requests[:, t]
        served[:, t] = np.minimum(queue, inp.refill_capacity_per_interval)
        queue = queue - served[:, t]
    denied = queue + late

    withdrawals = first_w + served * r
    total_requests = requests.sum(axis=1) + late
    # plate leftover and unmet demand are computed per diner before queue denial
    taken = first[:, None] + r * capped_trips
    leftover = np.where(present, np.maximum(taken - D, 0.0), 0.0).sum(axis=0)
    unmet = np.where(present, np.maximum(D - taken, 0.0), 0.0).sum(axis=0)
    consumed = np.where(present, np.minimum(D, taken), 0.0).sum(axis=0)
    return {
        "attendance": att,
        "withdrawals": withdrawals,
        "cum": np.cumsum(withdrawals, axis=1),
        "refill_requests": total_requests,
        "refill_denied": denied,
        "peak_requests": requests.max(axis=1),
        "leftover": leftover,
        "unmet": unmet,
        "consumed": consumed,
    }


def _q(x: np.ndarray, q: float) -> float:
    return float(np.quantile(x, q))


def plan(inp: KitchenInputs, known: Sequence[DinerForecast], cohort: DinerForecast) -> dict:
    if inp.expected_attendance < 1:
        raise ValueError("expected_attendance must be at least 1")
    if inp.batch_g <= 0 or inp.refill_g <= 0 or inp.scoop_g <= 0:
        raise ValueError("batch, refill and scoop sizes must be positive")
    n_sims = int(max(200, min(inp.n_sims, MAX_DRAWS // max(inp.expected_attendance, 1))))
    inp = KitchenInputs(**{**inp.__dict__, "n_sims": n_sims})

    sim = _simulate(inp, known, cohort, meal_sd=inp.meal_effect_sd, att_cv=inp.attendance_cv)
    cum, T, b, L = sim["cum"], inp.intervals, inp.batch_g, inp.lead_intervals
    total = cum[:, -1]
    S = len(total)

    # pointwise requirement curve, then a joint check below
    need_curve = np.quantile(cum, 1 - inp.alpha, axis=0)
    max_batches = inp.stock_cooked_equiv_g // b
    reasons: list[dict] = []

    def batches_ready(n_pre: int, starts: list[int]) -> np.ndarray:
        ready = np.full(T, n_pre)
        for s in starts:
            ready[min(s + L, T):] += 1
        return ready

    def schedule(n_pre: int) -> list[int]:
        """Start times for in-service batches so availability meets the curve."""
        starts: list[int] = []
        for t in range(L, T):
            while (n_pre + sum(1 for s in starts if s + L <= t)) * b < need_curve[t]:
                starts.append(t - L)
        return starts

    n_pre = int(math.ceil(need_curve[min(L, T) - 1] / b)) if L >= 1 else 1
    n_pre = max(n_pre, 1)
    starts = schedule(n_pre)

    def too_parallel(starts: list[int]) -> bool:
        return any(sum(1 for s in starts if s <= t < s + L) > inp.max_parallel_batches for t in range(T))

    def joint_risk(n_pre: int, starts: list[int]) -> float:
        avail = batches_ready(n_pre, starts) * b
        return float(np.mean((cum > avail[None, :] + 1e-9).any(axis=1)))

    # move work before service until the stove limit and the joint risk are satisfied
    guard = 0
    while (too_parallel(starts) or joint_risk(n_pre, starts) > inp.alpha) and guard < 60:
        n_pre += 1
        starts = schedule(n_pre)
        guard += 1

    n_total = n_pre + len(starts)
    stock_ok = n_total <= max_batches
    if not stock_ok:
        # show the best that current stock allows instead of quietly relaxing the target
        n_pre_cap = min(n_pre, max_batches)
        starts_cap = starts[: max(max_batches - n_pre_cap, 0)]
        risk_with_stock = joint_risk(n_pre_cap, starts_cap)
        short_g = n_total * b - max_batches * b
        reasons.append({
            "code": "STOCK",
            "message": f"Meeting the {inp.alpha:.0%} modelled shortage target needs {n_total * b / 1000:.1f} kg cooked; stock allows {max_batches * b / 1000:.1f} kg.",
            "detail": {"required_g": n_total * b, "stock_g": max_batches * b, "modelled_risk_with_stock": risk_with_stock},
            "options": [
                f"Add stock for {short_g / 1000:.1f} kg more cooked food",
                "Lower expected attendance only if the count is really lower",
                "Accept and display the higher modelled shortage risk for this service",
            ],
        })
        n_pre, starts = n_pre_cap, starts_cap

    risk = joint_risk(n_pre, starts)
    se = math.sqrt(max(risk * (1 - risk), 1e-12) / S)

    requests = sim["refill_requests"]
    denied_share = float(sim["refill_denied"].sum() / max(requests.sum(), 1.0))
    if denied_share > inp.denied_refill_tolerance:
        needed = int(math.ceil(_q(sim["peak_requests"], 0.95)))
        reasons.append({
            "code": "REFILL_CAPACITY",
            "message": f"The refill station is modelled to turn away {denied_share:.0%} of seconds requests; small first portions are only acceptable when seconds are accessible.",
            "detail": {"denied_share": denied_share, "capacity_per_interval": inp.refill_capacity_per_interval, "suggested_capacity_per_interval": needed},
            "options": [
                f"Raise refill capacity to about {needed} servings per {inp.interval_minutes} min",
                "Serve the conventional first portion for this service",
                "Lengthen the service window",
            ],
        })
    if L >= T and len(starts) == 0 and n_pre * b < _q(total, 0.999):
        reasons.append({
            "code": "LEAD_TIME",
            "message": "Cooking lead time is as long as the service window, so no batch started during service can arrive. Everything must be cooked before service.",
            "detail": {"lead_intervals": L, "intervals": T},
            "options": ["Cook the full plan before service", "Shorten lead time with smaller batches"],
        })

    # conditional reserve: a late batch that most services do not need is held
    # uncooked and started only if early withdrawals pass a threshold
    batches = [{"index": i + 1, "grams": b, "trigger": "before_service", "start_minute": None, "ready_minute": 0, "threshold_g": None} for i in range(n_pre)]
    cooked = np.full(S, float(n_pre * b))
    avail = np.tile(np.full(T, float(n_pre * b)), (S, 1))
    for i, s in enumerate(sorted(starts)):
        prev = (n_pre + i) * b
        needs = (cum[:, min(s + L, T - 1):] > prev).any(axis=1)
        trigger, threshold = "scheduled", None
        cook = np.ones(S, dtype=bool)
        if s >= 1 and needs.mean() < 0.5 and needs.any():
            threshold = float(np.quantile(cum[needs, s - 1], 0.02))
            cand = cum[:, s - 1] >= threshold
            trial = avail.copy()
            trial[cand, min(s + L, T):] += b
            if float(np.mean((cum > trial + 1e-9).any(axis=1))) <= max(inp.alpha, risk) + 1e-9:
                trigger, cook = "conditional", cand
        avail[cook, min(s + L, T):] += b
        cooked += np.where(cook, b, 0.0)
        batches.append({
            "index": n_pre + i + 1, "grams": b, "trigger": trigger,
            "start_minute": s * inp.interval_minutes, "ready_minute": (s + L) * inp.interval_minutes,
            "threshold_g": None if threshold is None else int(round(threshold)),
        })
    risk_final = float(np.mean((cum > avail + 1e-9).any(axis=1)))
    surplus = np.maximum(cooked - total, 0.0)

    # sensitivity: treating diners as independent with a known head-count understates the reserve
    indep = _simulate(inp, known, cohort, meal_sd=0.0, att_cv=0.0)["cum"][:, -1]

    feasible = not reasons or all(r["code"] == "LEAD_TIME" for r in reasons)
    return {
        "planner_version": PLANNER_VERSION,
        "status": "feasible" if feasible else "infeasible",
        "infeasible_reasons": reasons,
        "initial_cook_g": int(n_pre * b),
        "reserve_g": int(len(starts) * b),
        "planned_total_g": int((n_pre + len(starts)) * b),
        "batches": batches,
        "forecast": {
            "kind": "predicted",
            "withdrawals_g": {"p10": _q(total, 0.10), "p50": _q(total, 0.50), "p90": _q(total, 0.90), "p95": _q(total, 0.95)},
            "consumed_g": {"p10": _q(sim["consumed"], 0.10), "p50": _q(sim["consumed"], 0.50), "p90": _q(sim["consumed"], 0.90)},
            "plate_leftover_g": {"mean": float(sim["leftover"].mean()), "p90": _q(sim["leftover"], 0.90)},
            "unserved_surplus_g": {"mean": float(surplus.mean()), "p90": _q(surplus, 0.90)},
            "expected_cooked_g": float(cooked.mean()),
            "refill_requests": {"mean": float(requests.mean()), "p90": _q(requests, 0.90)},
            "denied_refill_share": denied_share,
            "modelled_unmet_demand_g": {"mean": float(sim["unmet"].mean())},
            "attendance": {"p10": _q(sim["attendance"], 0.10), "p50": _q(sim["attendance"], 0.50), "p90": _q(sim["attendance"], 0.90)},
            "cumulative_by_interval": [
                {"minute": (t + 1) * inp.interval_minutes, "p50_g": _q(cum[:, t], 0.5), "p95_g": _q(cum[:, t], 0.95), "available_g": float(np.median(avail[:, t]))}
                for t in range(T)
            ],
        },
        "risk": {
            "modelled_shortage_risk": risk_final,
            "target_alpha": inp.alpha,
            "monte_carlo_se": se,
            "near_boundary": abs(risk_final - inp.alpha) < 2 * se,
            "n_sims": S,
            "seed": inp.seed,
        },
        "sensitivity": [
            {"label": "This plan's assumptions", "withdrawals_p95_g": _q(total, 0.95)},
            {"label": "If diners were independent and head-count exact", "withdrawals_p95_g": _q(indep, 0.95)},
        ],
        "assumptions": {
            "attendance_cv": inp.attendance_cv,
            "meal_effect_sd": inp.meal_effect_sd,
            "refill_uptake": inp.refill_uptake,
            "arrival_profile": [float(x) for x in _arrival_profile(inp)],
            "known_diners": len(known),
            "cold_start_diners_expected": max(inp.expected_attendance - len(known), 0),
            "lead_minutes": L * inp.interval_minutes,
            "batch_g": b,
            "refill_capacity_per_interval": inp.refill_capacity_per_interval,
            "stock_cooked_equiv_g": inp.stock_cooked_equiv_g,
        },
    }
