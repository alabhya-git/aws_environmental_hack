"""First-portion policy.

Two decisions are kept separate: the first portion s, and total availability
through seconds. Under the simplified unlimited-access assumption a diner with
demand D who is offered s and can take refills of size r withdraws

    A(D, s, r) = s + r * ceil(max(D - s, 0) / r)

and leaves max(A - D, 0) on the plate. This is a policy-simulation assumption,
not a claim about human behaviour. The policy minimises expected leftover plus
an explicit refill-friction cost, subject to availability constraints. Every
numeric default is a product assumption, not a health standard.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..model.demand import Posterior, summarise

POLICY_VERSION = "first-portion/1.0"


@dataclass(frozen=True)
class ServingConfig:
    scoop_g: float                      # calibrated mean grams per scoop
    step_scoops: float = 0.5            # allowed partial scoop
    min_scoops: float = 1.0             # never recommend less than this
    max_scoops: float = 8.0
    refill_scoops: float = 0.5          # size of one seconds serving
    refill_friction_g: float = 20.0     # cost of one refill trip, in gram-equivalents
    max_p_two_trips: float = 0.50       # availability: P(needs 2+ refill trips)
    max_change_scoops: float = 1.0      # per-meal change limit
    conventional_scoops: float = 3.0    # the site's usual first serving
    seconds_accessible: bool = True
    no_seconds_coverage: float = 0.80   # if seconds are not accessible, cover this share of demand
    min_evidence: int = 1               # eligible observations before personalising

    def candidates(self) -> np.ndarray:
        lo = max(self.min_scoops, self.step_scoops)
        n = int(round((self.max_scoops - lo) / self.step_scoops)) + 1
        return lo + self.step_scoops * np.arange(n)


@dataclass
class Recommendation:
    scoops: float
    grams: int
    basis: str                      # personal | cohort | conventional | requested | opted_out
    reasons: list[str]
    predictive: dict | None
    expected_leftover_g: float | None
    expected_refill_trips: float | None
    p_wants_more_than_first: float | None
    tradeoff: list[dict] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    policy_version: str = POLICY_VERSION

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def evaluate_portion(vals: np.ndarray, wts: np.ndarray, s_g: float, r_g: float) -> dict:
    """Expected outcomes of first portion s_g under the unlimited-access assumption."""
    short = np.maximum(vals - s_g, 0.0)
    trips = np.ceil(short / r_g - 1e-9)
    withdrawn = s_g + r_g * trips
    leftover = np.maximum(withdrawn - vals, 0.0)
    return {
        "expected_leftover_g": float(np.sum(wts * leftover)),
        "expected_refill_trips": float(np.sum(wts * trips)),
        "expected_withdrawn_g": float(np.sum(wts * withdrawn)),
        "p_wants_more": float(np.sum(wts[trips >= 1])),
        "p_two_trips": float(np.sum(wts[trips >= 2])),
    }


def recommend(
    post: Posterior | None,
    cfg: ServingConfig,
    *,
    cohort: Posterior | None = None,
    last_suggested_scoops: float | None = None,
    requested_scoops: float | None = None,
    opted_out: bool = False,
) -> Recommendation:
    constraints = {
        "min_scoops": cfg.min_scoops,
        "max_scoops": cfg.max_scoops,
        "step_scoops": cfg.step_scoops,
        "refill_scoops": cfg.refill_scoops,
        "refill_friction_g": cfg.refill_friction_g,
        "max_p_two_trips": cfg.max_p_two_trips,
        "max_change_scoops": cfg.max_change_scoops,
        "seconds_accessible": cfg.seconds_accessible,
        "scoop_g": cfg.scoop_g,
    }

    def plain(scoops: float, basis: str, reasons: list[str]) -> Recommendation:
        return Recommendation(scoops, int(round(scoops * cfg.scoop_g)), basis, reasons, None, None, None, None, [], constraints)

    # The diner's own request always wins; it is never silently overridden.
    if requested_scoops is not None:
        if requested_scoops <= 0:
            raise ValueError("a requested portion must be positive; use decline_food for zero")
        return plain(float(requested_scoops), "requested", ["DINER_REQUEST_HONOURED"])
    if opted_out:
        return plain(cfg.conventional_scoops, "opted_out", ["PERSONALISATION_DECLINED"])

    n_personal = (post.n_interval + post.n_censored) if post is not None else 0
    if post is not None and n_personal >= cfg.min_evidence:
        use, basis, reasons = post, "personal", ["PERSONAL_HISTORY"]
    elif cohort is not None and (cohort.n_interval + cohort.n_censored) >= 5:
        use, basis, reasons = cohort, "cohort", ["COLD_START_COHORT"]
    else:
        return plain(cfg.conventional_scoops, "conventional", ["COLD_START_CONVENTIONAL"])

    atoms = use.atoms()
    vals, cum = atoms
    wts = np.diff(cum, prepend=0.0)
    r_g = cfg.refill_scoops * cfg.scoop_g
    cands = cfg.candidates()
    table = []
    for s in cands:
        ev = evaluate_portion(vals, wts, float(s) * cfg.scoop_g, r_g)
        ev["scoops"] = float(s)
        ev["grams"] = int(round(float(s) * cfg.scoop_g))
        ev["cost"] = ev["expected_leftover_g"] + cfg.refill_friction_g * ev["expected_refill_trips"]
        ev["allowed"] = True
        table.append(ev)

    if cfg.seconds_accessible:
        for ev in table:
            ev["allowed"] = ev["p_two_trips"] <= cfg.max_p_two_trips
        allowed = [ev for ev in table if ev["allowed"]]
        if allowed:
            best = min(allowed, key=lambda ev: (ev["cost"], ev["scoops"]))
        else:  # nothing satisfies the availability limit: take the most generous portion
            best = table[-1]
            reasons.append("AVAILABILITY_LIMIT_NOT_MET_AT_MAX_PORTION")
    else:
        # Without accessible seconds a small first portion is not acceptable:
        # cover a stated share of the predictive demand instead of minimising waste.
        target = use.quantiles([cfg.no_seconds_coverage], atoms)[0]
        best = next((ev for ev in table if ev["grams"] >= target), table[-1])
        reasons.append("SECONDS_NOT_ACCESSIBLE_COVERAGE_RULE")

    scoops = best["scoops"]
    if last_suggested_scoops is not None and abs(scoops - last_suggested_scoops) > cfg.max_change_scoops + 1e-9:
        scoops = last_suggested_scoops + cfg.max_change_scoops * (1 if scoops > last_suggested_scoops else -1)
        scoops = float(np.clip(scoops, cands[0], cands[-1]))
        reasons.append("CHANGE_LIMITED")
        best = min(table, key=lambda ev: abs(ev["scoops"] - scoops))

    if use.n_censored:
        reasons.append("INCLUDES_WANTED_MORE_EVIDENCE")
    flags = use.diagnostics()["flags"]
    reasons.extend(flags)

    assert best["scoops"] >= cfg.step_scoops > 0, "zero portion can never be a recommendation"
    return Recommendation(
        scoops=best["scoops"],
        grams=best["grams"],
        basis=basis,
        reasons=reasons,
        predictive=summarise(use, atoms),
        expected_leftover_g=best["expected_leftover_g"],
        expected_refill_trips=best["expected_refill_trips"],
        p_wants_more_than_first=best["p_wants_more"],
        tradeoff=[
            {k: ev[k] for k in ("scoops", "grams", "expected_leftover_g", "expected_refill_trips", "p_wants_more", "p_two_trips", "cost", "allowed")}
            for ev in table
        ],
        constraints=constraints,
    )
