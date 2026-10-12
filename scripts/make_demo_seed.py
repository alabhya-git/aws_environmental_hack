"""Generate the public demo's fixed, SIMULATED seed dataset.

Run from the repo root:  python scripts/make_demo_seed.py
Writes services/api/proofplate/assets/demo_seed.json and a copy under
data/simulated/ with a manifest. No real person or measurement is involved.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "api"), str(ROOT)]

from evaluation.simulator import World, draw_latent, plate_outcome  # noqa: E402
from proofplate.domain.rules import calibration_stats  # noqa: E402

SEED = 20261011
SITE = "demo"
FOOD = "steamed-rice-v1"
DATES = [f"2026-10-{d:02d}" for d in range(1, 9)]
TODAY = "2026-10-11"


def main() -> None:
    rng = np.random.default_rng(SEED)
    records, events = [], []

    def put(kind, id, data):
        records.append({"kind": kind, "id": id, "data": {**data, "id": id, "version": 1}})

    put("site", "settings", {"name": "Demo mess (simulated)", "timezone": "Asia/Kolkata"})
    put("food", FOOD, {"name": "Steamed rice", "recipe_version": "v1", "unit": "scoop", "cost_per_kg": None,
                       "created_at": "2026-10-01T03:00:00+00:00"})
    weights = [int(round(x)) for x in rng.normal(60, 4.2, 20)]
    put("calibration", f"{FOOD}.0001", {
        "food_id": FOOD, "scoop_id": "ladle-A", "weights_g": weights, "scale_resolution_g": 1, "measurement": "generated",
        **calibration_stats(weights), "cal_version": 1, "operator": "simulated", "created_at": "2026-10-01T03:30:00+00:00",
        "origin": "simulated"})
    cal = records[-1]["data"]

    base_session = dict(food_ids=[FOOD], attendance_cv=0.10, intervals=6, interval_minutes=15, batch_g=5000, lead_minutes=30,
                        max_parallel_batches=2, stock_cooked_equiv_g=60000, refill_capacity_per_interval=110,
                        seconds_accessible=True, conventional_scoops=3.0, origin="simulated")
    for k, d in enumerate(DATES):
        prepared = int(rng.choice([30000, 32500, 35000]))
        put("session", f"{d}-lunch", {**base_session, "name": "Lunch", "service_date": d, "expected_attendance": 140,
                                      "status": "closed", "created_at": f"{d}T03:00:00+00:00", "prepared_g": prepared,
                                      "kitchen_surplus_g": int(rng.integers(2500, 7000)),
                                      "surplus_destination": ["retained", "discarded", "donated"][k % 3]})
    put("session", f"{TODAY}-lunch", {**base_session, "name": "Lunch", "service_date": TODAY, "expected_attendance": 140,
                                      "status": "open", "created_at": f"{TODAY}T03:00:00+00:00"})

    def diner(token):
        put("diner", token, {"token": token, "consent": True, "portion_preference_scoops": None,
                             "personalization_opt_out": False, "created_at": "2026-10-01T03:00:00+00:00", "origin": "simulated"})

    n_ev = [0]

    def emit(date, token, kind, grams, minute, **extra):
        n_ev[0] += 1
        events.append({
            "event_id": f"seed{n_ev[0]:06d}xx", "session_id": f"{date}-lunch", "diner_token": token, "food_id": FOOD,
            "type": kind, "grams": int(grams), "scoops": None, "measurement": "generated",
            "satisfaction": None, "leftover_reason": None, "seconds_status": None, "seconds_wait_min": None,
            "losses_g": 0, "incoming_transfer_g": 0, "transfer_unknown": False, "requested_scoops": None,
            "recommended": None, "supersedes": None, "note": None,
            "event_time": f"{date}T07:{minute:02d}:00+00:00", "ingested_at": f"{date}T07:{minute:02d}:05+00:00",
            "site_id": SITE, "operator": "simulated", "origin": "simulated", "entry": "generated",
            "scoop_g": cal["mean_g"], "scoop_sd_g": cal["sd_g"], "calibration_version": 1, "scale_resolution_g": 1,
            **extra})

    def plate(date, token, first, returned, sat, reason="none", seconds=(), sec_status=None, minute=5):
        emit(date, token, "serve_initial", first, minute)
        for k, s in enumerate(seconds):
            emit(date, token, "serve_seconds", s, minute + 8 + k)
        emit(date, token, "return", returned, minute + 25, satisfaction=sat, leftover_reason=reason,
             seconds_status=sec_status or ("served" if seconds else "not_requested"))

    three = int(round(3 * cal["mean_g"]))
    half = int(round(0.5 * cal["mean_g"]))

    # --- four scripted characters for the walkthrough (tokens only, no people) ---
    diner("T-104")  # served three scoops, leaves about one every time
    for d, left in zip(DATES[:6], [61, 55, 66, 58, 63, 57]):
        plate(d, "T-104", three, left, "enough", "enough_food")
    diner("T-221")  # finishes the plate and says they wanted more; rarely walks back
    for k, d in enumerate(DATES[:5]):
        plate(d, "T-221", three, 0, "wanted_more", sec_status="declined" if k % 2 else "not_requested")
    diner("T-415")  # leftovers that are not about appetite
    plate(DATES[0], "T-415", three, 8, "enough", "enough_food")
    plate(DATES[1], "T-415", three, 95, "unsure", "dislike")
    plate(DATES[2], "T-415", three, 12, "enough", "enough_food")
    plate(DATES[4], "T-415", three, 110, "unsure", "interrupted")
    plate(DATES[5], "T-415", three, 0, "enough")
    diner("T-530")  # empty plates where nobody asked
    for d in DATES[:4]:
        plate(d, "T-530", three, 0, "unsure")
    plate(DATES[5], "T-530", three + half, 6, "enough", "enough_food", seconds=[half])
    diner("T-309")  # registered today, no history: cold start

    # --- background cohort from the simulator under the conventional policy ---
    w = World("demo-seed", "seed cohort", seed=SEED, n_diners=22, n_meals=len(DATES), p_missing_return=0.06)
    lat = draw_latent(w)
    for i in range(w.n_diners):
        token = f"T-{600 + i * 7}"
        diner(token)
        for j, d in enumerate(DATES):
            if not lat.attend[i, j]:
                continue
            o = plate_outcome(w, lat, i, j, float(three), float(half))
            minute = int(rng.integers(0, 30))
            emit(d, token, "serve_initial", o["first_g"], minute)
            for k, s in enumerate(o["seconds_g"]):
                emit(d, token, "serve_seconds", s, minute + 8 + k)
            if not o["missing_return"]:
                total = o["first_g"] + sum(o["seconds_g"])
                emit(d, token, "return", min(o["returned_g"], total), minute + 25, satisfaction=o["satisfaction"],
                     leftover_reason=o["leftover_reason"], seconds_status="served" if o["seconds_g"] else "not_requested")

    body = {"site": SITE, "records": records, "events": events}
    sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    body["manifest"] = {
        "dataset": "proofplate-demo-seed", "schema_version": 1, "status": "SIMULATED", "seed": SEED,
        "generator": "scripts/make_demo_seed.py + evaluation/simulator.py", "sha256": sha,
        "units": "integer grams of cooked food; UTC timestamps",
        "counts": {"diners": sum(r["kind"] == "diner" for r in records), "sessions": sum(r["kind"] == "session" for r in records),
                   "events": len(events)},
        "collection_method": "None. Generated from a random seed and a stated behaviour model; five tokens are hand-scripted for the walkthrough.",
        "limitations": [
            "No real diner, kitchen or weighing is represented.",
            "Generated appetites follow the simulator's assumptions; they are not evidence about real people.",
            "Results computed on this seed must be labelled simulated.",
        ],
        "license": "CC0-1.0 (generated for this project)",
    }
    out = json.dumps(body, separators=(",", ":"))
    for path in (ROOT / "services/api/proofplate/assets/demo_seed.json", ROOT / "data/simulated/demo_seed.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out, encoding="utf-8")
    (ROOT / "data/simulated/MANIFEST.json").write_text(json.dumps(body["manifest"], indent=2), encoding="utf-8")
    print(f"seed written: {len(records)} records, {len(events)} events, sha256 {sha[:16]}")


if __name__ == "__main__":
    main()
