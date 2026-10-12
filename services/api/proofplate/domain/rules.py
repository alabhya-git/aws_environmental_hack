"""Accounting invariants and the mapping from a plate to model evidence.

Quantity relationship (all integer grams):
    total served = initial + all additional servings
    consumed     = total served + incoming transfers - edible returned - losses
Contradictory quantities are rejected, never clamped. A missing return is
missing, not zero waste.
"""
from __future__ import annotations

import math
from typing import Iterable

from ..model.demand import Observation


class RuleError(Exception):
    """A request that breaks an invariant. `code` is stable; `status` is HTTP."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


CONFOUNDING_REASONS = {"dislike", "interrupted", "shared"}


def effective_events(events: Iterable[dict]) -> list[dict]:
    """Apply corrections: an event with `supersedes` replaces its target; a
    `void` event removes its target. Result is ordered by event time, then
    ingestion time, then id, which is the documented replay order (late
    arrivals are placed by event time)."""
    events = list(events)
    replaced = {e["supersedes"] for e in events if e.get("supersedes")}
    live = [e for e in events if e["event_id"] not in replaced and e["type"] != "void"]
    return sorted(live, key=lambda e: (e["event_time"], e.get("ingested_at", ""), e["event_id"]))


def project(events: Iterable[dict]) -> dict:
    """Fold one participation's events (same session, diner, food) into state."""
    evs = effective_events(events)
    initial = [e for e in evs if e["type"] == "serve_initial"]
    seconds = [e for e in evs if e["type"] == "serve_seconds"]
    returns = [e for e in evs if e["type"] == "return"]
    declined = [e for e in evs if e["type"] == "decline_food"]
    p: dict = {
        "status": "empty", "initial_g": None, "seconds_g": [], "total_served_g": 0,
        "returned_g": None, "losses_g": 0, "incoming_transfer_g": 0, "transfer_unknown": False,
        "satisfaction": None, "leftover_reason": None, "seconds_status": None, "seconds_wait_min": None,
        "consumed_g": None, "recommended": None, "requested_scoops": None,
        "served_at": None, "returned_at": None, "event_ids": [e["event_id"] for e in evs],
        "origin": evs[0]["origin"] if evs else None, "measurement_status": evs[0].get("measurement_status") if evs else None,
        "serve_measurement": None, "return_measurement": None, "scoop_g": None, "scoop_sd_g": None,
        "calibration_version": None, "scale_resolution_g": 1,
        "return_sd_g": None, "photo_detail": None,
        "initial_event_id": initial[0]["event_id"] if initial else None,
        "return_event_id": returns[0]["event_id"] if returns else None,
    }
    if declined and not initial:
        p["status"] = "declined"
        p["served_at"] = declined[0]["event_time"]
        return p
    if not initial:
        return p
    first = initial[0]
    p.update(
        status="open", initial_g=first["grams"], served_at=first["event_time"],
        recommended=first.get("recommended"), requested_scoops=first.get("requested_scoops"),
        serve_measurement=first.get("measurement"), scoop_g=first.get("scoop_g"), scoop_sd_g=first.get("scoop_sd_g"),
        calibration_version=first.get("calibration_version"), scale_resolution_g=first.get("scale_resolution_g") or 1,
    )
    p["seconds_g"] = [e["grams"] for e in seconds]
    p["total_served_g"] = first["grams"] + sum(p["seconds_g"])
    p["losses_g"] = sum(e.get("losses_g", 0) for e in evs)
    p["incoming_transfer_g"] = sum(e.get("incoming_transfer_g", 0) for e in evs)
    p["transfer_unknown"] = any(e.get("transfer_unknown") for e in evs)
    if returns:
        r = returns[0]
        p.update(
            status="returned", returned_g=r["grams"], returned_at=r["event_time"],
            satisfaction=r.get("satisfaction") or "unsure", leftover_reason=r.get("leftover_reason") or "none",
            seconds_status=r.get("seconds_status") or ("served" if seconds else "not_requested"),
            seconds_wait_min=r.get("seconds_wait_min"), return_measurement=r.get("measurement"),
            return_sd_g=r.get("photo_sd_g"), photo_detail=r.get("photo_detail"),
        )
        if not p["transfer_unknown"]:
            p["consumed_g"] = p["total_served_g"] + p["incoming_transfer_g"] - p["returned_g"] - p["losses_g"]
    return p


def validate_append(existing: Iterable[dict], new: dict, session_open: bool = True) -> None:
    """Raise RuleError if appending `new` would break an invariant."""
    existing = list(existing)
    by_id = {e["event_id"]: e for e in existing}
    t = new["type"]
    target = None
    if new.get("supersedes"):
        target = by_id.get(new["supersedes"])
        if target is None:
            raise RuleError("CORRECTION_TARGET_NOT_FOUND", "The event being corrected does not exist for this plate.", 404)
        if any(e.get("supersedes") == target["event_id"] for e in existing):
            raise RuleError("ALREADY_CORRECTED", "That event has already been corrected; correct the newest version instead.", 409)
        if t != "void" and t != target["type"]:
            raise RuleError("CORRECTION_TYPE_MISMATCH", "A correction must have the same type as the event it replaces.")
    elif t == "void":
        raise RuleError("VOID_NEEDS_TARGET", "A void event must name the event it removes.")

    if t in ("serve_initial", "serve_seconds", "return") and new.get("grams") is None:
        raise RuleError("GRAMS_REQUIRED", "This event needs a quantity in grams.")
    if t == "serve_initial" and (new.get("grams") or 0) <= 0:
        raise RuleError("ZERO_SERVING", "A serving must be more than 0 g. Use 'declined this food' for no serving.")
    if t == "serve_seconds" and (new.get("grams") or 0) <= 0:
        raise RuleError("ZERO_SERVING", "A seconds serving must be more than 0 g.")

    before = project(existing)
    if target is None:
        if not session_open:
            raise RuleError("SESSION_CLOSED", "This meal service is closed. Corrections are still possible.", 409)
        if t == "serve_initial" and before["status"] != "empty":
            raise RuleError("ALREADY_SERVED", "A first serving is already recorded for this diner and food in this service.", 409)
        if t == "serve_seconds":
            if before["status"] == "empty" or before["status"] == "declined":
                raise RuleError("NO_FIRST_SERVING", "Record the first serving before seconds.", 409)
            if before["status"] == "returned":
                raise RuleError("RETURN_CLOSED", "The plate has already been returned; a closed return is not reopened as a new meal.", 409)
        if t == "return":
            if before["status"] in ("empty", "declined"):
                raise RuleError("NO_FIRST_SERVING", "Nothing was served, so there is no plate to return.", 409)
            if before["status"] == "returned":
                raise RuleError("RETURN_CLOSED", "A return is already recorded. Use a correction to change it.", 409)
        if t == "decline_food" and before["status"] != "empty":
            raise RuleError("ALREADY_SERVED", "This diner already has a record for this food in this service.", 409)

    after = project(existing + [new])
    if after["status"] == "returned":
        available = after["total_served_g"] + after["incoming_transfer_g"] - after["losses_g"]
        if available < 0:
            raise RuleError("LOSSES_EXCEED_SERVED", "Recorded losses are larger than everything served.")
        if after["returned_g"] > available:
            raise RuleError(
                "RETURN_EXCEEDS_SERVED",
                f"Returned {after['returned_g']} g but only {available} g was available on this plate "
                "(served + transfers in - losses). Check the tare, or record food received from someone else.",
            )
    if target is not None and target["type"] == "serve_initial" and t == "void" and (before["seconds_g"] or before["status"] == "returned"):
        raise RuleError("VOID_BREAKS_PLATE", "Void the return and seconds first; a plate cannot have seconds without a first serving.", 409)


def tolerance_g(p: dict) -> float:
    """Half-width of the consumption interval, from how the plate was measured.

    Scoop-estimated servings carry the calibration spread for each scoop;
    weighed quantities carry the scale resolution. A further 3% covers the
    behavioural assumption that 'enough' means demand was about what was eaten.
    """
    res = float(p.get("scale_resolution_g") or 1)
    if p.get("serve_measurement") == "weighed":
        serve_u = res
    else:
        sd = float(p.get("scoop_sd_g") or 6.0)
        scoop = float(p.get("scoop_g") or 60.0)
        n_scoops = max(p["total_served_g"] / scoop, 0.5)
        serve_u = sd * math.sqrt(n_scoops)
    rm = p.get("return_measurement")
    if rm == "weighed":
        ret_u = res
    elif rm == "photo_estimate":
        ret_u = float(p.get("return_sd_g") or 15.0)  # a photo estimate carries its own stated uncertainty
    else:
        ret_u = 8.0
    meas = math.hypot(serve_u, ret_u)
    return max(4.0, 1.5 * meas) + 0.03 * max(p.get("consumed_g") or 0, 0)


# classification code -> (counts toward learning, short label)
CLASSES = {
    "interval": (True, "Enough: demand was about what was eaten"),
    "right_censored": (True, "Wanted more: demand was at least what was eaten"),
    "ambiguous_empty": (False, "Empty plate, satisfaction unknown: not used for learning"),
    "confounded": (False, "Leftover had another cause: waste recorded, appetite estimate unchanged"),
    "unsure_leftover": (False, "Unsure, leftover of unknown cause: not used for learning"),
    "transfer_unknown": (False, "Food was shared in unknown amounts: consumption unknown"),
    "missing_return": (False, "No return recorded: missing, not zero waste"),
    "open": (False, "Served, return not yet recorded"),
    "declined": (False, "Declined this food"),
    "empty": (False, "No record"),
}


def classify(p: dict, session_open: bool = True, ref: str = "") -> dict:
    """Decide what, if anything, this plate tells the demand model."""
    status = p["status"]
    out = {"code": status, "eligible": False, "observation": None, "tolerance_g": None}
    if status in ("empty", "declined"):
        return out
    if status == "open":
        out["code"] = "open" if session_open else "missing_return"
        return out
    if p["transfer_unknown"] or p["consumed_g"] is None:
        out["code"] = "transfer_unknown"
        return out
    consumed = p["consumed_g"]
    tol = tolerance_g(p)
    out["tolerance_g"] = round(tol, 1)
    reason, sat, returned = p["leftover_reason"], p["satisfaction"], p["returned_g"]
    if sat == "wanted_more":
        # Explicit signal. It stays a lower bound whatever the leftover reason:
        # the diner told us demand exceeded what they ate.
        out.update(code="right_censored", eligible=True,
                   observation=Observation("right_censored", max(consumed - tol, 0.0), ref=ref))
        return out
    if reason in CONFOUNDING_REASONS and returned > 0:
        out["code"] = "confounded"
        return out
    if sat == "enough":
        out.update(code="interval", eligible=True,
                   observation=Observation("interval", max(consumed - tol, 0.0), consumed + tol, ref=ref))
        return out
    # unsure
    if returned <= max(3, p.get("scale_resolution_g") or 1):
        out["code"] = "ambiguous_empty"
        return out
    if reason == "enough_food":
        out.update(code="interval", eligible=True,
                   observation=Observation("interval", max(consumed - tol, 0.0), consumed + tol, ref=ref))
        return out
    out["code"] = "unsure_leftover"
    return out


def calibration_stats(weights_g: list[int]) -> dict:
    n = len(weights_g)
    mean = sum(weights_g) / n
    var = sum((w - mean) ** 2 for w in weights_g) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    return {"n": n, "mean_g": round(mean, 2), "sd_g": round(sd, 2), "cv": round(sd / mean, 4) if mean else None,
            "min_g": min(weights_g), "max_g": max(weights_g), "se_mean_g": round(sd / math.sqrt(n), 2)}
