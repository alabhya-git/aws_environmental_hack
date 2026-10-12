"""Reproducible policy evaluation. Everything written here is SIMULATED.

    python -m evaluation.run_evaluation            # full run
    python -m evaluation.run_evaluation --quick    # 2 seeds, for a smoke test

Writes evaluation/outputs/evaluation.json and EVALUATION.md, and copies the JSON
into the API package so the app's Evidence view shows the same numbers.
No parameter was tuned on these outputs. One policy default was chosen on
separate development seeds (see explore_policy.py and MODEL_CARD.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "api"), str(ROOT)]

from evaluation.simulator import POLICIES, POLICY_LABELS, SCENARIOS, run, with_seed  # noqa: E402
from proofplate.model.demand import MODEL_VERSION, GridConfig, Prior  # noqa: E402
from proofplate.planning.serving import POLICY_VERSION, ServingConfig  # noqa: E402

SEEDS = [101, 202, 303, 404, 505, 606, 707, 808]
METRICS = [
    ("plate_waste_g_per_plate", "Plate waste (g per plate)", "lower"),
    ("left_hungry_rate", "Left wanting more, true share of plates", "lower"),
    ("refill_trips_per_plate", "Trips for seconds per plate", "lower"),
    ("kitchen_surplus_g_per_meal", "Kitchen surplus (g per meal)", "lower"),
    ("stockout_meal_rate", "Meals where cooked food ran short", "lower"),
    ("first_portion_g", "Mean first portion (g)", "info"),
    ("eaten_g_per_plate", "Eaten (g per plate)", "info"),
    ("reported_wanted_more_rate", "Reported 'wanted more' share", "info"),
]
T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26}


def mean_ci(xs: list[float]) -> dict:
    n = len(xs)
    m = float(np.mean(xs))
    if n < 2:
        return {"mean": m, "lo": None, "hi": None, "n": n}
    half = T975.get(n - 1, 1.96) * float(np.std(xs, ddof=1)) / math.sqrt(n)
    return {"mean": m, "lo": m - half, "hi": m + half, "n": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    seeds = SEEDS[:2] if args.quick else SEEDS
    t0 = time.time()
    scenarios = []
    from concurrent.futures import ProcessPoolExecutor

    jobs = [with_seed(sc, s) for sc in SCENARIOS for s in seeds]
    with ProcessPoolExecutor(max_workers=6) as pool:   # each world is independent and seeded
        results = list(pool.map(run, jobs))
    by_world = {(w.name, w.seed): r for w, r in zip(jobs, results)}
    for sc in SCENARIOS:
        per_seed = [by_world[(sc.name, s)] for s in seeds]
        entry = {"name": sc.name, "description": sc.description, "generator": sc.generator,
                 "diners": sc.n_diners, "meals": sc.n_meals, "policies": {}}
        for pol in POLICIES:
            m = {key: mean_ci([r[pol][key] for r in per_seed]) for key, _, _ in METRICS}
            # paired difference against the conventional policy on identical latent streams
            m["paired_vs_conventional"] = {
                key: mean_ci([r[pol][key] - r["conventional"][key] for r in per_seed]) for key, _, _ in METRICS[:5]}
            m["plates"] = int(sum(r[pol]["plates"] for r in per_seed))
            entry["policies"][pol] = m
        preds = [r["proofplate"]["prediction"] for r in per_seed]
        entry["prediction"] = {
            "n_uncensored": int(sum(p["n_uncensored"] for p in preds)),
            "mae_g": mean_ci([p["mae_g"] for p in preds if p["mae_g"] is not None]),
            "coverage_80": mean_ci([p["coverage_80"] for p in preds if p["coverage_80"] is not None]),
            "mean_interval_width_g": mean_ci([p["mean_interval_width_g"] for p in preds if p["mean_interval_width_g"] is not None]),
            "n_censored": int(sum(p["n_censored"] for p in preds)),
            "censored_mean_log_score": mean_ci([p["censored_mean_log_score"] for p in preds if p["censored_mean_log_score"] is not None]),
        }
        entry["series_seed0"] = {pol: [{"meal": s["meal"], "waste_per_plate_g": round(s["waste_per_plate_g"], 2)}
                                       for s in per_seed[0][pol]["series"]] for pol in POLICIES}
        scenarios.append(entry)
        print(f"{sc.name:18s} done  ({time.time() - t0:5.0f}s)", flush=True)

    cfg = ServingConfig(scoop_g=60.0)
    body = {
        "status": "SIMULATED",
        "what_this_is": "Paired policy comparison in a labelled simulator. It is not a measurement of any real kitchen.",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "seeds": seeds, "model_version": MODEL_VERSION, "policy_version": POLICY_VERSION,
        "split": "Chronological. Each learner starts empty, predicts before each meal and updates after it. "
                 "Metrics are computed on the final third of meals only (the test period).",
        "tuning": "One policy default (the limit on needing two trips for seconds) was chosen with "
                  "evaluation/explore_policy.py on development seeds 9001-9002, which are not among the reported seeds. "
                  "Nothing was chosen using the outputs reported here.",
        "config": {"serving": cfg.__dict__, "grid": GridConfig().__dict__, "prior": Prior().describe()},
        "equal_resources": "Every policy has the same seconds access, refill size, refill uptake and unlimited stock; "
                           "kitchen surplus is prepared minus withdrawn under each policy's own preparation rule.",
        "policies": [{"key": p, "label": POLICY_LABELS[p]} for p in POLICIES],
        "metrics": [{"key": k, "label": lab, "direction": d} for k, lab, d in METRICS],
        "scenarios": scenarios,
        "limitations": [
            "Behaviour (who walks back for seconds, who reports wanting more) follows stated assumptions, not observed diners.",
            "'Left wanting more' uses the simulator's hidden demand, which no real kitchen can observe.",
            "Stock-outs are scored after the fact and do not feed back into what later diners received.",
            "A simulated difference is a property of this simulator. Field effect is unproven until measured in a pilot.",
        ],
        "runtime_seconds": round(time.time() - t0, 1),
    }
    body["sha256"] = hashlib.sha256(json.dumps(body["scenarios"], sort_keys=True).encode()).hexdigest()
    out = ROOT / "evaluation" / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=1)
    (out / "evaluation.json").write_text(text, encoding="utf-8")
    (ROOT / "services/api/proofplate/assets/evaluation.json").write_text(text, encoding="utf-8")
    (ROOT / "EVALUATION.md").write_text(render(body), encoding="utf-8")
    print(f"wrote evaluation.json ({len(text) / 1e3:.0f} kB) in {body['runtime_seconds']}s, sha {body['sha256'][:12]}")


def fmt(ci: dict, pct: bool = False, digits: int = 1) -> str:
    if ci["mean"] is None:
        return "n/a"
    k = 100 if pct else 1
    unit = "%" if pct else ""
    s = f"{ci['mean'] * k:.{digits}f}{unit}"
    if ci.get("lo") is not None:
        s += f" ({ci['lo'] * k:.{digits}f} to {ci['hi'] * k:.{digits}f})"
    return s


def render(b: dict) -> str:
    L = [
        "# Evaluation", "",
        "> **Status: SIMULATED.** Every number below comes from a labelled simulator with fixed seeds. "
        "None is a measurement of a real kitchen, and none should be quoted as measured savings.", "",
        f"- Generated: {b['generated_at']} · seeds {b['seeds']} · model `{b['model_version']}` · policy `{b['policy_version']}`",
        f"- Split: {b['split']}", f"- Tuning: {b['tuning']}", f"- Resources: {b['equal_resources']}",
        "- Intervals are 95% t-intervals across seeds. 'vs conventional' is a paired difference on identical latent streams.",
        "- Reproduce: `python -m evaluation.run_evaluation` (writes `evaluation/outputs/evaluation.json`, which the app's Evidence view reads).", "",
        "## Policies", "",
    ]
    L += [f"- **{p['key']}** — {p['label']}" for p in b["policies"]]
    L += ["", "`naive_average` is an ablation: it personalises without the censoring logic, to show what that logic is for.", ""]
    for sc in b["scenarios"]:
        L += [f"## {sc['name']}", "", sc["description"], "",
              "| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |",
              "|---|---|---|---|---|---|---|"]
        for p in b["policies"]:
            m = sc["policies"][p["key"]]
            L.append(f"| {p['key']} | {fmt(m['plate_waste_g_per_plate'])} | {fmt(m['paired_vs_conventional']['plate_waste_g_per_plate'])} | "
                     f"{fmt(m['left_hungry_rate'], pct=True)} | {fmt(m['refill_trips_per_plate'], digits=2)} | "
                     f"{fmt(m['kitchen_surplus_g_per_meal'], digits=0)} | {fmt(m['stockout_meal_rate'], pct=True, digits=0)} |")
        pr = sc["prediction"]
        L += ["", f"ProofPlate prediction on the test period: MAE {fmt(pr['mae_g'])} g on {pr['n_uncensored']} uncensored plates; "
                  f"80% interval covered {fmt(pr['coverage_80'], pct=True, digits=0)} with mean width {fmt(pr['mean_interval_width_g'], digits=0)} g; "
                  f"mean log score on {pr['n_censored']} censored plates {fmt(pr['censored_mean_log_score'], digits=2)}.", ""]
    L += ["## Limitations", ""] + [f"- {x}" for x in b["limitations"]] + [""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
