"""Labelled simulator for policy comparison. Everything produced here is SIMULATED.

Design rules:
  * Policies are compared on paired latent streams: the same diners, the same
    attendance, the same demand draws and the same behavioural random numbers.
  * The learner sees only what an operator could record (portions, seconds,
    returned grams, stated satisfaction, stated reason). Latent demand is used
    to produce behaviour and to score outcomes, never as a model input.
  * Every policy gets the same seconds access, refill size and uptake.
  * Some worlds draw demand from a different family than the learner assumes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from proofplate.domain.rules import classify
from proofplate.model.demand import Posterior, Prior
from proofplate.planning.kitchen import DinerForecast, KitchenInputs, plan
from proofplate.planning.serving import ServingConfig, recommend

POLICIES = ["conventional", "small_plus_seconds", "cohort", "naive_average", "proofplate"]
POLICY_LABELS = {
    "conventional": "Fixed conventional portion (3 scoops)",
    "small_plus_seconds": "Fixed smaller portion (2 scoops) + seconds",
    "cohort": "Cohort history, same portion for everyone",
    "naive_average": "Personal moving average (ignores 'wanted more')",
    "proofplate": "ProofPlate: censored personal model",
}


@dataclass(frozen=True)
class World:
    name: str
    description: str
    seed: int = 0
    n_diners: int = 60
    n_meals: int = 24
    generator: str = "lognormal"        # lognormal | gamma | mixture
    pop_median_g: float = 190.0
    between_diner_sd: float = 0.32
    within_sigma: tuple[float, float] = (0.12, 0.30)
    meal_effect_sd: float = 0.08
    attend_p: float = 0.80
    scoop_g: float = 60.0
    scoop_sd_g: float = 5.0
    refill_uptake: float = 0.85
    p_report: float = 0.85              # share of plates where satisfaction is actually recorded
    p_dislike: float = 0.03
    p_interrupt: float = 0.03
    p_missing_return: float = 0.05
    drift_at: int | None = None         # meal index where half the diners' appetite rises
    drift_factor: float = 1.35
    late_joiner_share: float = 0.0      # diners who first appear halfway through
    disliked_meals: int = 0             # whole meals where the recipe went wrong
    spike_meals: int = 0                # meals with a jump in attendance
    batch_g: int = 2500


@dataclass
class Latent:
    attend: np.ndarray
    demand: np.ndarray
    u_uptake: np.ndarray
    u_report: np.ndarray
    u_confound: np.ndarray
    frac: np.ndarray
    u_missing: np.ndarray
    z_scoop: np.ndarray
    bad_meal: np.ndarray


def draw_latent(w: World) -> Latent:
    rng = np.random.default_rng(w.seed)
    n, m = w.n_diners, w.n_meals
    mu = math.log(w.pop_median_g) + w.between_diner_sd * rng.standard_normal(n)
    sg = rng.uniform(*w.within_sigma, size=n)
    meal = w.meal_effect_sd * rng.standard_normal(m)
    shift = np.zeros((n, m))
    if w.drift_at is not None:
        movers = rng.random(n) < 0.5
        shift[np.ix_(movers, np.arange(m) >= w.drift_at)] = math.log(w.drift_factor)
    loc = mu[:, None] + meal[None, :] + shift
    eps = rng.standard_normal((n, m))
    if w.generator == "lognormal":
        demand = np.exp(loc + sg[:, None] * eps)
    elif w.generator == "gamma":
        cv = np.broadcast_to(sg[:, None], (n, m))
        shape = 1.0 / cv**2
        demand = rng.gamma(shape, np.exp(loc) * np.exp(0.5 * cv**2) / shape)
    elif w.generator == "mixture":  # light meals and heavy meals: two modes per diner
        light = rng.random((n, m)) < 0.25
        demand = np.exp(loc + np.where(light, math.log(0.55), math.log(1.12)) + 0.12 * eps)
    else:
        raise ValueError(w.generator)
    attend = rng.random((n, m)) < w.attend_p
    if w.late_joiner_share:
        late = rng.random(n) < w.late_joiner_share
        attend[np.ix_(late, np.arange(m) < m // 2)] = False
    bad = np.zeros(m, dtype=bool)
    if w.disliked_meals:
        bad[rng.choice(np.arange(2, m), size=w.disliked_meals, replace=False)] = True
    if w.spike_meals:
        for j in rng.choice(np.arange(m // 2, m), size=w.spike_meals, replace=False):
            attend[:, j] = rng.random(n) < min(1.0, w.attend_p * 1.25)
            demand[:, j] *= 1.18
    return Latent(attend, demand, rng.random((n, m, 8)), rng.random((n, m)), rng.random((n, m)),
                  rng.uniform(0.35, 0.7, (n, m)), rng.random((n, m)), rng.standard_normal((n, m)), bad)


def plate_outcome(w: World, lat: Latent, i: int, j: int, first_g: float, refill_g: float) -> dict:
    """What happens on one plate. Returns recorded fields plus hidden truth."""
    D = float(lat.demand[i, j])
    n_scoops = max(first_g / w.scoop_g, 0.5)
    actual_first = max(first_g + w.scoop_sd_g * math.sqrt(n_scoops) * float(lat.z_scoop[i, j]), 5.0)
    u = float(lat.u_confound[i, j])
    bad_meal = bool(lat.bad_meal[j]) and u < 0.5
    reported = float(lat.u_report[i, j]) < w.p_report
    out = {"first_g": int(round(first_g)), "seconds_g": [], "trips": 0, "hidden_demand_g": D, "hidden_unmet_g": 0.0}
    if bad_meal or u < w.p_dislike or u < w.p_dislike + w.p_interrupt:
        dislike = bad_meal or u < w.p_dislike
        eaten = float(lat.frac[i, j]) * min(D, actual_first)
        out.update(returned_g=int(round(actual_first - eaten)), satisfaction="unsure" if not reported else "enough",
                   leftover_reason=("dislike" if dislike else "interrupted") if reported else "unknown", eaten_g=eaten)
    else:
        need = int(math.ceil(max(D - actual_first, 0.0) / refill_g - 1e-9))
        taken = 0
        while taken < min(need, 8) and float(lat.u_uptake[i, j, taken]) < w.refill_uptake:
            taken += 1
        total = actual_first + taken * refill_g
        out["seconds_g"] = [int(round(refill_g))] * taken
        out["trips"] = taken
        if total >= D:
            ret = int(round(total - D))
            out.update(returned_g=ret, satisfaction="enough" if reported else "unsure",
                       leftover_reason="enough_food" if ret > 3 and reported else ("none" if ret <= 3 else "unknown"), eaten_g=D)
        else:
            out.update(returned_g=0, satisfaction="wanted_more" if reported else "unsure", leftover_reason="none",
                       eaten_g=total, hidden_unmet_g=D - total)
    out["missing_return"] = float(lat.u_missing[i, j]) < w.p_missing_return
    return out


def participation(w: World, o: dict) -> dict:
    """The recorded plate, in the same shape the app's domain rules consume."""
    total = o["first_g"] + sum(o["seconds_g"])
    returned = min(o["returned_g"], total)
    return {
        "status": "returned", "total_served_g": total, "returned_g": returned, "consumed_g": total - returned,
        "satisfaction": o["satisfaction"], "leftover_reason": o["leftover_reason"], "transfer_unknown": False,
        "serve_measurement": "scoop_estimate", "return_measurement": "weighed", "scoop_g": w.scoop_g,
        "scoop_sd_g": w.scoop_sd_g, "scale_resolution_g": 1, "seconds_g": o["seconds_g"],
    }


@dataclass
class Learner:
    """Per-policy memory built only from recorded plates."""

    cfg: ServingConfig
    prior: Prior = field(default_factory=Prior)
    posts: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)
    consumed: dict = field(default_factory=dict)
    cohort: Posterior | None = None

    def __post_init__(self) -> None:
        self.cohort = Posterior.from_prior(prior=self.prior)

    def observe(self, i: int, p: dict) -> None:
        self.consumed.setdefault(i, []).append(p["consumed_g"])
        c = classify(p)
        if c["eligible"]:
            self.posts[i] = (self.posts.get(i) or Posterior.from_prior(prior=self.prior)).update(c["observation"])
            self.cohort = self.cohort.update(c["observation"])

    def cohort_or_none(self) -> Posterior | None:
        return self.cohort if (self.cohort.n_interval + self.cohort.n_censored) >= 5 else None


def first_portion(policy: str, L: Learner, i: int) -> float:
    cfg = L.cfg
    if policy.startswith("fixed:"):  # frontier sweep: everyone gets the same k scoops
        return float(policy.split(":")[1]) * cfg.scoop_g
    if policy == "conventional":
        return cfg.conventional_scoops * cfg.scoop_g
    if policy == "small_plus_seconds":
        return 2.0 * cfg.scoop_g
    if policy == "cohort":
        return float(recommend(None, cfg, cohort=L.cohort_or_none()).grams)
    if policy == "naive_average":
        xs = L.consumed.get(i, [])[-5:]
        if not xs:
            return cfg.conventional_scoops * cfg.scoop_g
        s = round((sum(xs) / len(xs)) / cfg.scoop_g / cfg.step_scoops) * cfg.step_scoops
        return float(np.clip(s, cfg.min_scoops, cfg.max_scoops)) * cfg.scoop_g
    rec = recommend(L.posts.get(i), cfg, cohort=L.cohort_or_none(), last_suggested_scoops=L.last.get(i))
    L.last[i] = rec.scoops  # the change limit is relative to the portion last received
    return float(rec.grams)


def prepare(policy: str, w: World, L: Learner, history: list[float], expected: int, firsts: dict[int, float], j: int) -> float:
    """Cooked grams for the meal. Fixed policies use the kitchen's usual rule
    (recent withdrawal per head plus a 15% buffer); model policies use the
    planner. All round up to whole batches."""
    b = w.batch_g
    if policy in ("conventional", "small_plus_seconds", "naive_average") or policy.startswith("fixed:") or not history:
        per_head = (np.mean(history[-5:]) if history else L.cfg.conventional_scoops * L.cfg.scoop_g)
        return float(math.ceil(expected * per_head * 1.15 / b) * b)
    cold_post = L.cohort_or_none() or Posterior.from_prior(prior=L.prior)
    cold_first = float(recommend(None, L.cfg, cohort=L.cohort_or_none()).grams)
    if policy == "cohort":
        known = []
    else:
        known = [DinerForecast(L.posts[i].sample, firsts.get(i, cold_first)) for i in L.posts]
    inp = KitchenInputs(expected_attendance=expected, scoop_g=w.scoop_g, refill_g=L.cfg.refill_scoops * w.scoop_g,
                        attendance_cv=0.08, meal_effect_sd=w.meal_effect_sd, batch_g=b, lead_intervals=6,
                        stock_cooked_equiv_g=10**8, refill_capacity_per_interval=10**6,
                        refill_uptake=w.refill_uptake, n_sims=300, seed=w.seed * 1000 + j)
    return float(plan(inp, known, DinerForecast(cold_post.sample, cold_first))["planned_total_g"])


def run(w: World, policies: list[str] = POLICIES, test_from: float = 2 / 3, cfg: ServingConfig | None = None,
        prior: Prior | None = None) -> dict:
    lat = draw_latent(w)
    cfg = cfg or ServingConfig(scoop_g=w.scoop_g)
    refill_g = cfg.refill_scoops * cfg.scoop_g
    first_test = int(w.n_meals * test_from)
    expected = int(round(w.n_diners * w.attend_p))
    out = {}
    for policy in policies:
        L = Learner(cfg, prior or Prior())
        acc = {k: 0.0 for k in ("plates", "waste_g", "served_g", "eaten_g", "trips", "hungry", "wanted_more_reported",
                                 "returns", "missing", "first_g", "prepared_g", "surplus_g", "stockout_meals", "meals",
                                 "known_plates", "cold_plates")}
        pred = {"abs_err": [], "covered": [], "width": [], "log_score": []}
        per_head: list[float] = []
        series = []
        for j in range(w.n_meals):
            present = np.flatnonzero(lat.attend[:, j])
            firsts = {int(i): first_portion(policy, L, int(i)) for i in present}
            prepared = prepare(policy, w, L, per_head, expected, firsts, j)
            withdrawn = 0.0
            meal_waste = 0.0
            recorded = []
            for i in present:
                i = int(i)
                test = j >= first_test
                if policy == "proofplate" and test and i in L.posts:
                    atoms = L.posts[i].atoms(n_nodes=24)
                    q10, q50, q90 = L.posts[i].quantiles([0.1, 0.5, 0.9], atoms)
                o = plate_outcome(w, lat, i, j, firsts[i], refill_g)
                total = o["first_g"] + sum(o["seconds_g"])
                withdrawn += total
                meal_waste += o["returned_g"]
                p = participation(w, o)
                if not o["missing_return"]:
                    recorded.append((i, p))
                if test:
                    acc["plates"] += 1
                    acc["waste_g"] += o["returned_g"]
                    acc["served_g"] += total
                    acc["eaten_g"] += o["eaten_g"]
                    acc["trips"] += o["trips"]
                    acc["first_g"] += o["first_g"]
                    acc["hungry"] += o["hidden_unmet_g"] > 5
                    acc["missing"] += o["missing_return"]
                    acc["returns"] += not o["missing_return"]
                    acc["wanted_more_reported"] += (o["satisfaction"] == "wanted_more") and not o["missing_return"]
                    acc["known_plates" if i in L.posts else "cold_plates"] += 1
                    if policy == "proofplate" and i in L.posts and not o["missing_return"]:
                        c = classify(p)
                        if c["code"] == "interval":
                            pred["abs_err"].append(abs(q50 - p["consumed_g"]))
                            pred["covered"].append(q10 <= p["consumed_g"] <= q90)
                            pred["width"].append(q90 - q10)
                        elif c["code"] == "right_censored":
                            sf = 1.0 - float(L.posts[i].cdf(c["observation"].lo_g, atoms))
                            pred["log_score"].append(math.log(max(sf, 1e-6)))
            # learn only after the meal is over (predict first, then update)
            for i, p in recorded:
                L.observe(i, p)
            per_head.append(withdrawn / max(len(present), 1))
            if j >= first_test:
                acc["meals"] += 1
                acc["prepared_g"] += prepared
                acc["surplus_g"] += max(prepared - withdrawn, 0.0)
                acc["stockout_meals"] += withdrawn > prepared
            series.append({"meal": j, "plates": int(len(present)), "waste_per_plate_g": meal_waste / max(len(present), 1),
                           "prepared_g": prepared, "withdrawn_g": withdrawn})
        n = max(acc["plates"], 1)
        out[policy] = {
            "plates": int(acc["plates"]), "meals": int(acc["meals"]),
            "plate_waste_g_per_plate": acc["waste_g"] / n,
            "first_portion_g": acc["first_g"] / n,
            "served_g_per_plate": acc["served_g"] / n,
            "eaten_g_per_plate": acc["eaten_g"] / n,
            "refill_trips_per_plate": acc["trips"] / n,
            "left_hungry_rate": acc["hungry"] / n,
            "reported_wanted_more_rate": acc["wanted_more_reported"] / max(acc["returns"], 1),
            "missing_return_rate": acc["missing"] / n,
            "kitchen_surplus_g_per_meal": acc["surplus_g"] / max(acc["meals"], 1),
            "prepared_g_per_meal": acc["prepared_g"] / max(acc["meals"], 1),
            "stockout_meal_rate": acc["stockout_meals"] / max(acc["meals"], 1),
            "total_edible_waste_g_per_plate": (acc["waste_g"] + acc["surplus_g"]) / n,
            "cold_start_plate_share": acc["cold_plates"] / n,
            "series": series,
        }
        if policy == "proofplate":
            k = len(pred["abs_err"])
            out[policy]["prediction"] = {
                "n_uncensored": k, "mae_g": float(np.mean(pred["abs_err"])) if k else None,
                "coverage_80": float(np.mean(pred["covered"])) if k else None,
                "mean_interval_width_g": float(np.mean(pred["width"])) if k else None,
                "n_censored": len(pred["log_score"]),
                "censored_mean_log_score": float(np.mean(pred["log_score"])) if pred["log_score"] else None,
            }
    return out


SCENARIOS = [
    World("baseline", "Mixed appetites, learner's own distribution family."),
    World("gamma_family", "Demand drawn from a gamma family the learner does not assume.", generator="gamma"),
    World("two_mode_meals", "Each diner has light and heavy meals (bimodal): strong misspecification.", generator="mixture"),
    World("appetite_rises", "Half the diners' appetite rises 35% a third of the way in.", drift_at=8),
    World("late_joiners", "40% of diners first appear halfway through (cold starts).", late_joiner_share=0.4),
    World("disliked_recipe", "Three meals where the recipe went wrong and half the diners leave food.", disliked_meals=3),
    World("missing_returns", "A quarter of plates never get a return recorded.", p_missing_return=0.25),
    World("seconds_friction", "Only half of diners who want more walk back for seconds.", refill_uptake=0.5),
    World("demand_spike", "Three late meals with a jump in attendance and appetite.", spike_meals=3),
]


def with_seed(w: World, seed: int) -> World:
    return replace(w, seed=seed)
