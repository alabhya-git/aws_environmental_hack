"""Development-only exploration of serving-policy defaults.

Uses seeds 9001-9002, which are NOT among the reported evaluation seeds, so
that choosing defaults here does not tune on the reported results.

    python -m evaluation.explore_policy
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "api"), str(ROOT)]

from evaluation.simulator import SCENARIOS, run, with_seed  # noqa: E402
from proofplate.model.demand import Prior  # noqa: E402
from proofplate.planning.serving import ServingConfig  # noqa: E402

DEV_SEEDS = [9001, 9002]
WORLDS = [s for s in SCENARIOS if s.name in ("baseline", "seconds_friction", "gamma_family")]
CONFIGS = {
    "p2<=.30 lam20 (current)": (ServingConfig(60.0), Prior()),
    "p2 off  lam20": (ServingConfig(60.0, max_p_two_trips=1.0), Prior()),
    "p2<=.50 lam20": (ServingConfig(60.0, max_p_two_trips=0.5), Prior()),
    "p2 off  lam30": (ServingConfig(60.0, max_p_two_trips=1.0, refill_friction_g=30.0), Prior()),
    "p2<=.50 lam30": (ServingConfig(60.0, max_p_two_trips=0.5, refill_friction_g=30.0), Prior()),
    "p2<=.50 lam20 tight-prior": (ServingConfig(60.0, max_p_two_trips=0.5), Prior(mu_sd=0.4, sigma_log_sd=0.4)),
}


def row(label: str, m: dict) -> str:
    return (f"  {label:28s} waste {m['plate_waste_g_per_plate']:5.1f}  hungry {m['left_hungry_rate']:5.1%}  "
            f"trips {m['refill_trips_per_plate']:.2f}  first {m['first_portion_g']:5.0f}  surplus {m['kitchen_surplus_g_per_meal']:5.0f}")


def avg(rs: list[dict]) -> dict:
    return {k: sum(r[k] for r in rs) / len(rs) for k in rs[0] if isinstance(rs[0][k], (int, float))}


def main() -> None:
    for w in WORLDS:
        print(w.name)
        base = [run(with_seed(w, s), ["conventional", "small_plus_seconds", "naive_average"]) for s in DEV_SEEDS]
        for p in ("conventional", "small_plus_seconds", "naive_average"):
            print(row(p, avg([b[p] for b in base])))
        for label, (cfg, prior) in CONFIGS.items():
            rs = [run(with_seed(w, s), ["proofplate"], cfg=cfg, prior=prior)["proofplate"] for s in DEV_SEEDS]
            print(row("PP " + label, avg(rs)), flush=True)


if __name__ == "__main__":
    main()
