"""Waste-versus-availability frontier. Everything written here is SIMULATED.

A fixed portion can only trade plate waste against diners left wanting more by
moving everyone's portion up or down. This sweep traces that trade-off for
fixed portions (2 to 4 scoops) and for ProofPlate (by varying the cost it
gives to a trip for seconds), on the same paired worlds and seeds as the main
evaluation, so the two curves can be compared directly.

    python -m evaluation.run_frontier

Adds a "frontier" block to evaluation/outputs/evaluation.json (and the copy the
API serves) and a section to EVALUATION.md. Run after run_evaluation.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "api"), str(ROOT)]

from evaluation.run_evaluation import SEEDS  # noqa: E402
from evaluation.simulator import SCENARIOS, run, with_seed  # noqa: E402
from proofplate.planning.serving import ServingConfig  # noqa: E402

FIXED = [2.0, 2.5, 3.0, 3.5, 4.0]
FRICTION = [2.0, 8.0, 20.0, 40.0]          # grams-equivalent cost of one trip for seconds; 20 is the app default
WORLDS = ["baseline", "seconds_friction"]
KEYS = ["plate_waste_g_per_plate", "left_hungry_rate", "refill_trips_per_plate", "first_portion_g"]


def job(args):
    world, kind, value = args
    if kind == "fixed":
        out = run(world, [f"fixed:{v}" for v in FIXED])
        return [(world.name, world.seed, "fixed", v, {k: out[f"fixed:{v}"][k] for k in KEYS}) for v in FIXED]
    if kind == "naive":
        out = run(world, ["naive_average"])
        return [(world.name, world.seed, "naive", 0.0, {k: out["naive_average"][k] for k in KEYS})]
    out = run(world, ["proofplate"], cfg=ServingConfig(scoop_g=world.scoop_g, refill_friction_g=value))
    return [(world.name, world.seed, "proofplate", value, {k: out["proofplate"][k] for k in KEYS})]


def main() -> None:
    t0 = time.time()
    worlds = [with_seed(s, seed) for s in SCENARIOS if s.name in WORLDS for seed in SEEDS]
    jobs = [(w, "fixed", 0.0) for w in worlds] + [(w, "naive", 0.0) for w in worlds] + [(w, "pp", f) for w in worlds for f in FRICTION]
    with ProcessPoolExecutor(max_workers=6) as pool:
        rows = [r for chunk in pool.map(job, jobs) for r in chunk]

    def point(world: str, kind: str, value: float) -> dict:
        sel = [m for (w, _s, k, v, m) in rows if w == world and k == kind and v == value]
        return {k: float(np.mean([m[k] for m in sel])) for k in KEYS} | {"n_seeds": len(sel)}

    frontier = {"status": "SIMULATED", "seeds": SEEDS, "default_friction_g": 20.0, "worlds": {}}
    for world in WORLDS:
        frontier["worlds"][world] = {
            "fixed": [{"scoops": v, **point(world, "fixed", v)} for v in FIXED],
            "proofplate": [{"friction_g": f, **point(world, "proofplate", f)} for f in FRICTION],
            "naive_average": point(world, "naive", 0.0),
        }
        # equal-availability comparison: fixed-portion waste interpolated at ProofPlate's default hunger rate
        fx = sorted(frontier["worlds"][world]["fixed"], key=lambda p: p["left_hungry_rate"])
        pp = next(p for p in frontier["worlds"][world]["proofplate"] if p["friction_g"] == 20.0)
        h = [p["left_hungry_rate"] for p in fx]
        wst = [p["plate_waste_g_per_plate"] for p in fx]
        inside = h[0] <= pp["left_hungry_rate"] <= h[-1]
        frontier["worlds"][world]["equal_availability"] = {
            "proofplate_left_wanting_more": pp["left_hungry_rate"], "proofplate_waste_g": pp["plate_waste_g_per_plate"],
            "fixed_waste_g_at_same_rate": float(np.interp(pp["left_hungry_rate"], h, wst)) if inside else None,
            "within_fixed_range": inside,
            "note": "Fixed-portion waste is linearly interpolated between the two neighbouring fixed portions.",
        }
    frontier["runtime_seconds"] = round(time.time() - t0, 1)

    for path in (ROOT / "evaluation/outputs/evaluation.json", ROOT / "services/api/proofplate/assets/evaluation.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        body["frontier"] = frontier
        path.write_text(json.dumps(body, indent=1), encoding="utf-8")

    md = ROOT / "EVALUATION.md"
    text = md.read_text(encoding="utf-8").split("\n## Waste against availability")[0].rstrip() + "\n"
    L = ["", "## Waste against availability (frontier sweep)", "",
         "A fixed portion trades plate waste against diners left wanting more by moving everyone's portion. "
         "ProofPlate moves along its own curve through the cost it gives to a trip for seconds (20 g is the app default). "
         "Same worlds and seeds as above; means over seeds. **Simulated.**", ""]
    for world in WORLDS:
        fw = frontier["worlds"][world]
        L += [f"### {world}", "", "| Policy | Setting | Plate waste g/plate | Left wanting more | Seconds trips/plate |", "|---|---|---|---|---|"]
        L += [f"| Fixed portion | {p['scoops']} scoops | {p['plate_waste_g_per_plate']:.1f} | {p['left_hungry_rate']:.1%} | {p['refill_trips_per_plate']:.2f} |" for p in fw["fixed"]]
        L += [f"| ProofPlate | trip cost {p['friction_g']:.0f} g | {p['plate_waste_g_per_plate']:.1f} | {p['left_hungry_rate']:.1%} | {p['refill_trips_per_plate']:.2f} |" for p in fw["proofplate"]]
        n = fw["naive_average"]
        L += [f"| Personal average, no censoring | - | {n['plate_waste_g_per_plate']:.1f} | {n['left_hungry_rate']:.1%} | {n['refill_trips_per_plate']:.2f} |", ""]
        eq = fw["equal_availability"]
        if eq["within_fixed_range"]:
            L += [f"At ProofPlate's default setting {eq['proofplate_left_wanting_more']:.1%} of plates left the diner wanting more, with "
                  f"{eq['proofplate_waste_g']:.1f} g left per plate. A fixed portion giving the same {eq['proofplate_left_wanting_more']:.1%} "
                  f"would leave about {eq['fixed_waste_g_at_same_rate']:.1f} g per plate (interpolated).", ""]
        else:
            L += ["ProofPlate's default point lies outside the range the fixed portions reach, so no equal-availability comparison is made.", ""]
    md.write_text(text + "\n".join(L), encoding="utf-8")
    print(f"frontier done in {frontier['runtime_seconds']}s")
    for world in WORLDS:
        print(world, json.dumps(frontier["worlds"][world]["equal_availability"]))
        for p in frontier["worlds"][world]["fixed"]:
            print("  fixed", p["scoops"], round(p["plate_waste_g_per_plate"], 1), f"{p['left_hungry_rate']:.1%}")
        for p in frontier["worlds"][world]["proofplate"]:
            print("  pp   ", p["friction_g"], round(p["plate_waste_g_per_plate"], 1), f"{p['left_hungry_rate']:.1%}")


if __name__ == "__main__":
    main()
