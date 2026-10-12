"""Use cases. Everything here is storage-agnostic and site-scoped: callers
pass a Ctx whose site_id came from trusted server-side state, never from a
client-supplied tenant id."""
from __future__ import annotations

import secrets
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np

from .domain import schemas as S
from .domain.rules import CLASSES, RuleError, calibration_stats, classify, project, validate_append
from .model.baselines import naive_next_portion
from .model.demand import MODEL_VERSION, Observation, Posterior, Prior, fit, summarise
from .numerics import logsumexp
from .planning.kitchen import DinerForecast, KitchenInputs, plan
from .planning.serving import POLICY_VERSION, ServingConfig, recommend
from .repositories.base import ConflictError, Repository

DEFAULT_SETTINGS = {
    "name": "Dining site", "timezone": "Asia/Kolkata",
    "step_scoops": 0.5, "min_scoops": 1.0, "max_scoops": 8.0, "refill_scoops": 0.5,
    "refill_friction_g": 20.0, "max_p_two_trips": 0.50, "max_change_scoops": 1.0,
    "refill_uptake": 0.85, "meal_effect_sd": 0.08, "alpha": 0.05,
    "prior_median_g": 180.0, "recency_half_life": None,
}


@dataclass
class Ctx:
    repo: Repository
    site_id: str
    operator: str
    role: str   # manager | operator | demo
    mode: str   # pilot | demo

    @property
    def origin(self) -> str:
        return "simulated" if self.mode == "demo" else "measured"

    @property
    def train_class(self) -> str:
        """Evidence class the live model learns from. Never mixed."""
        return "simulated" if self.mode == "demo" else "measured"

    def need_manager(self) -> None:
        if self.role not in ("manager", "demo"):
            raise RuleError("FORBIDDEN", "This action needs a kitchen-manager role.", 403)


def evidence_class(p: dict) -> str:
    return "simulated" if p.get("origin") == "simulated" or p.get("measurement_status") == "synthetic" else "measured"


def settings(ctx: Ctx) -> dict:
    return {**DEFAULT_SETTINGS, **(ctx.repo.get(ctx.site_id, "site", "settings") or {})}


def latest_calibration(ctx: Ctx, food_id: str) -> Optional[dict]:
    cals = [c for c in ctx.repo.list(ctx.site_id, "calibration") if c["food_id"] == food_id]
    return max(cals, key=lambda c: c["cal_version"]) if cals else None


def _need(doc: Optional[dict], code: str, what: str) -> dict:
    if not doc:
        raise RuleError(code, f"{what} was not found.", 404)
    return doc


# --------------------------------------------------------------------------
# Site view: every plate for one food, projected and classified once
# --------------------------------------------------------------------------
class SiteView:
    def __init__(self, ctx: Ctx, food_id: str) -> None:
        self.ctx, self.food_id = ctx, food_id
        self.settings = settings(ctx)
        self.sessions = {s["id"]: s for s in ctx.repo.list(ctx.site_id, "session")}
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for e in ctx.repo.list_events(ctx.site_id, food_id=food_id):
            groups[(e["session_id"], e["diner_token"])].append(e)
        self.rows: list[dict] = []
        for (sid, token), evs in groups.items():
            p = project(evs)
            if p["status"] == "empty":
                continue
            is_open = self.sessions.get(sid, {}).get("status", "open") == "open"
            c = classify(p, is_open, ref=f"{sid}:{token}:{','.join(p['event_ids'])}")
            self.rows.append({
                "session_id": sid, "diner_token": token, "food_id": food_id, **p,
                "service_date": self.sessions.get(sid, {}).get("service_date"),
                "classification": c["code"], "eligible": c["eligible"], "observation": c["observation"],
                "tolerance_g": c["tolerance_g"], "evidence_class": evidence_class(p),
            })
        self.rows.sort(key=lambda r: (r["served_at"] or "", r["session_id"], r["diner_token"]))
        self._cohort_built = False

    # ---- evidence selection -------------------------------------------
    def training_rows(self, token: Optional[str] = None) -> list[dict]:
        cls = self.ctx.train_class
        rows = [r for r in self.rows if r["eligible"] and r["evidence_class"] == cls
                and (token is None or r["diner_token"] == token)]
        return sorted(rows, key=lambda r: (r["returned_at"], r["session_id"]))

    def default_prior(self) -> Prior:
        return Prior(median_g=float(self.settings["prior_median_g"]))

    def _build_cohort(self) -> None:
        self._base = Posterior.from_prior(prior=self.default_prior())
        self._ll: dict[str, np.ndarray] = {}
        self._cnt: dict[str, list[int]] = {}
        for r in self.training_rows():
            obs: Observation = r["observation"]
            tok = r["diner_token"]
            self._ll[tok] = self._ll.get(tok, 0.0) + self._base.log_likelihood(obs)
            c = self._cnt.setdefault(tok, [0, 0])
            c[0 if obs.kind == "interval" else 1] += 1
        self._total = sum(self._ll.values()) if self._ll else 0.0
        self._cohort_built = True

    def cohort(self, exclude: Optional[str] = None) -> tuple[Optional[Posterior], int]:
        """Pooled posterior over other diners' evidence (exact leave-one-out,
        so a diner's own plates are never counted twice). Returns (posterior
        or None when there is too little evidence, number of diners pooled)."""
        if not self._cohort_built:
            self._build_cohort()
        toks = [t for t in self._ll if t != exclude]
        n_int = sum(self._cnt[t][0] for t in toks)
        n_cen = sum(self._cnt[t][1] for t in toks)
        if n_int + n_cen < 5:
            return None, len(toks)
        logw = self._base.logw + self._total - (self._ll.get(exclude, 0.0) if exclude else 0.0)
        post = replace(self._base, logw=logw - logsumexp(logw), n_interval=n_int, n_censored=n_cen,
                       refs=(f"cohort:{len(toks)}:{n_int}:{n_cen}",))
        return post, len(toks)

    def personal(self, token: str) -> tuple[Posterior, Optional[Posterior], list[dict]]:
        own = self.training_rows(token)
        cohort, n_diners = self.cohort(exclude=token)
        prior = self.default_prior()
        if cohort is not None and cohort.n_interval + cohort.n_censored >= 20 and n_diners >= 5:
            prior = Prior(median_g=round(cohort.quantiles([0.5])[0], 1), mu_sd=0.5,
                          source=f"cohort-of-{n_diners}-other-diners:{self.ctx.train_class}")
        post = fit([r["observation"] for r in own], prior=prior, recency_half_life=self.settings["recency_half_life"])
        return post, cohort, own

    def last_suggested(self, token: str) -> Optional[float]:
        """Reference for the per-meal change limit: the first portion this
        diner most recently received, in scoops."""
        step = float(self.settings["step_scoops"])
        for r in reversed(self.rows):
            if r["diner_token"] == token and r.get("initial_g") and r.get("scoop_g"):
                return max(step, round(r["initial_g"] / r["scoop_g"] / step) * step)
        return None


def serving_config(st: dict, session: Optional[dict], calib: dict) -> ServingConfig:
    return ServingConfig(
        scoop_g=float(calib["mean_g"]), step_scoops=st["step_scoops"], min_scoops=st["min_scoops"],
        max_scoops=st["max_scoops"], refill_scoops=st["refill_scoops"], refill_friction_g=st["refill_friction_g"],
        max_p_two_trips=st["max_p_two_trips"], max_change_scoops=st["max_change_scoops"],
        conventional_scoops=float((session or {}).get("conventional_scoops", 3.0)),
        seconds_accessible=bool((session or {}).get("seconds_accessible", True)),
    )


def _counts(view: SiteView, token: str) -> dict:
    mine = [r for r in view.rows if r["diner_token"] == token]
    excluded: dict[str, int] = defaultdict(int)
    for r in mine:
        if not r["eligible"] and r["classification"] not in ("open",):
            excluded[r["classification"]] += 1
    return {"plates": len(mine), "eligible": sum(r["eligible"] for r in mine), "excluded": dict(excluded)}


def _recommendation(ctx: Ctx, view: SiteView, token: str, session: Optional[dict], calib: dict, diner: dict,
                    requested: Optional[float] = None) -> dict:
    cfg = serving_config(view.settings, session, calib)
    post, cohort, own = view.personal(token)
    standing = diner.get("portion_preference_scoops")
    rec = recommend(
        post, cfg, cohort=cohort, last_suggested_scoops=view.last_suggested(token),
        requested_scoops=requested if requested is not None else standing,
        opted_out=bool(diner.get("personalization_opt_out")),
    ).as_dict()
    used = own if rec["basis"] == "personal" else (view.training_rows() if rec["basis"] == "cohort" else [])
    rec.update({
        "source_mode": ctx.mode, "evidence_class": ctx.train_class,
        "model_version": MODEL_VERSION, "policy_version": POLICY_VERSION,
        "training_cutoff": max((r["returned_at"] for r in used), default=None),
        "calibration": {"id": calib["id"], "version": calib["cal_version"], "mean_g": calib["mean_g"], "sd_g": calib["sd_g"], "n": calib["n"]},
        "evidence_counts": _counts(view, token),
        "personal_predictive": summarise(post) if own else None,
    })
    return rec


def _public_row(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("observation", "event_ids")}
    out["label"] = CLASSES[r["classification"]][1]
    return out


# --------------------------------------------------------------------------
# Setup: foods, calibration, sessions, diners
# --------------------------------------------------------------------------
def bootstrap(ctx: Ctx, _: S.Empty) -> dict:
    repo, site = ctx.repo, ctx.site_id
    foods = repo.list(site, "food")
    cals = repo.list(site, "calibration")
    return {
        "mode": ctx.mode, "role": ctx.role, "operator": ctx.operator, "site_id": site,
        "evidence_class": ctx.train_class, "settings": settings(ctx),
        "foods": foods,
        "calibrations": sorted(cals, key=lambda c: (c["food_id"], -c["cal_version"])),
        "sessions": sorted(repo.list(site, "session"), key=lambda s: (s["service_date"], s["id"]), reverse=True),
        "diners": [d for d in repo.list(site, "diner") if not d.get("deleted")],
        "model_version": MODEL_VERSION, "policy_version": POLICY_VERSION,
    }


def update_settings(ctx: Ctx, a: S.SettingsPatch) -> dict:
    ctx.need_manager()
    cur = ctx.repo.get(ctx.site_id, "site", "settings") or {}
    patch = {k: v for k, v in a.model_dump().items() if v is not None}
    doc = {**{k: v for k, v in cur.items() if k not in ("id", "version")}, **patch,
           "updated_at": S.utcnow().isoformat(), "updated_by": ctx.operator}
    ctx.repo.put(ctx.site_id, "site", "settings", doc)
    return settings(ctx)


def create_food(ctx: Ctx, a: S.FoodIn) -> dict:
    ctx.need_manager()
    fid = f"{''.join(ch if ch.isalnum() else '-' for ch in a.name.lower()).strip('-')[:40]}-{a.recipe_version}"
    try:
        return ctx.repo.put(ctx.site_id, "food", fid, {**a.model_dump(), "created_at": S.utcnow().isoformat(), "unit": "scoop"}, create_only=True)
    except ConflictError:
        raise RuleError("FOOD_EXISTS", "That food and recipe version already exists. A changed recipe needs a new version.", 409)


def create_calibration(ctx: Ctx, a: S.CalibrationIn) -> dict:
    ctx.need_manager()
    _need(ctx.repo.get(ctx.site_id, "food", a.food_id), "FOOD_NOT_FOUND", "Food")
    prev = latest_calibration(ctx, a.food_id)
    version = (prev["cal_version"] + 1) if prev else 1
    doc = {
        **a.model_dump(), **calibration_stats(a.weights_g), "cal_version": version,
        "operator": ctx.operator, "created_at": S.utcnow().isoformat(), "origin": ctx.origin,
    }
    # Calibrations are immutable history: each one is a new record.
    return ctx.repo.put(ctx.site_id, "calibration", f"{a.food_id}.{version:04d}", doc, create_only=True)


def create_session(ctx: Ctx, a: S.SessionIn) -> dict:
    ctx.need_manager()
    for fid in a.food_ids:
        _need(ctx.repo.get(ctx.site_id, "food", fid), "FOOD_NOT_FOUND", f"Food {fid}")
    sid = f"{a.service_date}-{secrets.token_hex(3)}"
    doc = {**a.model_dump(), "status": "open", "created_at": S.utcnow().isoformat(), "origin": ctx.origin}
    return ctx.repo.put(ctx.site_id, "session", sid, doc, create_only=True)


def update_session(ctx: Ctx, a: S.SessionPatch) -> dict:
    ctx.need_manager()
    cur = _need(ctx.repo.get(ctx.site_id, "session", a.session_id), "SESSION_NOT_FOUND", "Meal service")
    patch = {k: v for k, v in a.model_dump(exclude={"session_id"}).items() if v is not None}
    if cur.get("kitchen_surplus_g") is not None or "kitchen_surplus_g" in patch:
        prepared = patch.get("prepared_g", cur.get("prepared_g"))
        surplus = patch.get("kitchen_surplus_g", cur.get("kitchen_surplus_g"))
        if prepared is not None and surplus is not None and surplus > prepared:
            raise RuleError("SURPLUS_EXCEEDS_PREPARED", "Kitchen surplus cannot be larger than what was prepared.")
    try:
        return ctx.repo.put(ctx.site_id, "session", a.session_id, {**cur, **patch}, expect_version=cur["version"])
    except ConflictError:
        raise RuleError("STALE", "This meal service was changed by someone else. Reload and try again.", 409)


def create_diner(ctx: Ctx, a: S.DinerIn) -> dict:
    if not a.consent:
        raise RuleError("CONSENT_REQUIRED", "A token is only created for a diner who agrees to take part.")
    token = a.token or f"T-{secrets.token_hex(3).upper()}"
    doc = {"token": token, "consent": True, "portion_preference_scoops": a.portion_preference_scoops,
           "personalization_opt_out": a.personalization_opt_out, "created_at": S.utcnow().isoformat(), "origin": ctx.origin}
    try:
        return ctx.repo.put(ctx.site_id, "diner", token, doc, create_only=True)
    except ConflictError:
        raise RuleError("TOKEN_EXISTS", "That token is already registered.", 409)


def update_diner(ctx: Ctx, a: S.DinerIn) -> dict:
    cur = _need(ctx.repo.get(ctx.site_id, "diner", a.token or ""), "DINER_NOT_FOUND", "Diner token")
    cur.update(portion_preference_scoops=a.portion_preference_scoops, personalization_opt_out=a.personalization_opt_out)
    return ctx.repo.put(ctx.site_id, "diner", cur["token"], cur)


def export_diner(ctx: Ctx, a: S.DinerRef) -> dict:
    diner = _need(ctx.repo.get(ctx.site_id, "diner", a.token), "DINER_NOT_FOUND", "Diner token")
    return {"diner": diner, "events": ctx.repo.list_events(ctx.site_id, diner_token=a.token)}


def delete_diner(ctx: Ctx, a: S.DinerRef) -> dict:
    ctx.need_manager()
    _need(ctx.repo.get(ctx.site_id, "diner", a.token), "DINER_NOT_FOUND", "Diner token")
    n = ctx.repo.delete_diner_events(ctx.site_id, a.token)
    ctx.repo.delete(ctx.site_id, "diner", a.token)
    # Models are rebuilt by replay, so the deleted plates are gone from every later estimate.
    return {"deleted_token": a.token, "deleted_events": n,
            "note": "Events are removed and excluded from all later model rebuilds. Session-level kitchen totals entered by staff are unaffected."}


# --------------------------------------------------------------------------
# Serve
# --------------------------------------------------------------------------
def _serve_context(ctx: Ctx, session_id: str, token: str, food_id: str) -> tuple[dict, dict, dict, Optional[dict]]:
    session = _need(ctx.repo.get(ctx.site_id, "session", session_id), "SESSION_NOT_FOUND", "Meal service")
    if food_id not in session["food_ids"]:
        raise RuleError("FOOD_NOT_ON_MENU", "That food is not on this service's menu.")
    diner = ctx.repo.get(ctx.site_id, "diner", token)
    if not diner or diner.get("deleted"):
        raise RuleError("DINER_NOT_FOUND", "That token is not registered. Register it first.", 404)
    food = _need(ctx.repo.get(ctx.site_id, "food", food_id), "FOOD_NOT_FOUND", "Food")
    return session, diner, food, latest_calibration(ctx, food_id)


def diner_state(ctx: Ctx, a: S.DinerStateArgs) -> dict:
    session, diner, food, calib = _serve_context(ctx, a.session_id, a.token, a.food_id)
    view = SiteView(ctx, a.food_id)
    row = next((r for r in view.rows if r["session_id"] == a.session_id and r["diner_token"] == a.token), None)
    out = {"diner": diner, "food": food, "session": session, "calibration": calib,
           "participation": _public_row(row) if row else None, "recommendation": None, "calibration_required": calib is None}
    if calib:
        out["recommendation"] = _recommendation(ctx, view, a.token, session, calib, diner, a.requested_scoops)
    return out


def record_event(ctx: Ctx, a: S.EventIn) -> dict:
    session, diner, food, calib = _serve_context(ctx, a.session_id, a.diner_token, a.food_id)
    ev = a.model_dump(mode="json")
    if a.type in ("serve_initial", "serve_seconds") and ev["grams"] is None:
        if a.scoops is None:
            raise RuleError("GRAMS_REQUIRED", "Give the serving in scoops or grams.")
        if not calib:
            raise RuleError("CALIBRATION_REQUIRED", "Calibrate this scoop for this food before serving by scoops.", 409)
        ev["grams"] = int(round(a.scoops * calib["mean_g"]))
        ev["measurement"] = "scoop_estimate"
    now = S.utcnow().isoformat()
    ev.update({
        "event_time": ev["event_time"] or now, "ingested_at": now, "site_id": ctx.site_id, "operator": ctx.operator,
        "origin": ctx.origin, "entry": "demo_entry" if ctx.mode == "demo" else "operator",
        "scoop_g": calib["mean_g"] if calib else None, "scoop_sd_g": calib["sd_g"] if calib else None,
        "calibration_version": calib["cal_version"] if calib else None,
        "scale_resolution_g": calib["scale_resolution_g"] if calib else 1,
    })

    before_rec = None
    if a.type == "return" and calib:
        before_rec = _recommendation(ctx, SiteView(ctx, a.food_id), a.diner_token, session, calib, diner)

    stored, created = None, False
    for _attempt in range(4):
        existing = [e for e in ctx.repo.list_events(ctx.site_id, diner_token=a.diner_token, food_id=a.food_id)
                    if e["session_id"] == a.session_id]
        dup = next((e for e in existing if e["event_id"] == a.event_id), None)
        if dup:
            stored, created = dup, False
            break
        validate_append(existing, ev, session_open=session["status"] == "open")
        try:
            stored, created = ctx.repo.append_event(ctx.site_id, ev, expect_count=len(existing))
            break
        except ConflictError:
            continue  # someone else wrote to this plate: re-read and re-validate
    if stored is None:
        raise RuleError("BUSY", "This plate is being updated by another station. Try again.", 409)

    view = SiteView(ctx, a.food_id)
    row = next((r for r in view.rows if r["session_id"] == a.session_id and r["diner_token"] == a.diner_token), None)
    out = {"event": stored, "duplicate": not created, "participation": _public_row(row) if row else None, "learning": None}
    if a.type == "return" and calib and row and created:
        after = _recommendation(ctx, view, a.diner_token, session, calib, diner)
        out["learning"] = {
            "used_for_learning": bool(row["eligible"]) and row["evidence_class"] == ctx.train_class,
            "classification": row["classification"], "label": CLASSES[row["classification"]][1],
            "before": _brief(before_rec), "after": _brief(after),
        }
    return out


def _brief(rec: Optional[dict]) -> Optional[dict]:
    if not rec:
        return None
    pred = rec.get("personal_predictive") or rec.get("predictive") or {}
    return {"scoops": rec["scoops"], "grams": rec["grams"], "basis": rec["basis"],
            "median_g": pred.get("median_g"), "p10_g": pred.get("p10_g"), "p90_g": pred.get("p90_g"),
            "reasons": rec["reasons"]}


def session_log(ctx: Ctx, a: S.SessionRef) -> dict:
    session = _need(ctx.repo.get(ctx.site_id, "session", a.session_id), "SESSION_NOT_FOUND", "Meal service")
    rows = []
    for fid in session["food_ids"]:
        rows += [_public_row(r) for r in SiteView(ctx, fid).rows if r["session_id"] == a.session_id]
    rows.sort(key=lambda r: r["served_at"] or "", reverse=True)
    return {"session": session, "plates": rows[:300], "total": len(rows)}


# --------------------------------------------------------------------------
# Learn
# --------------------------------------------------------------------------
def _density(post: Posterior, scoop_g: float) -> dict:
    atoms = post.atoms()
    hi = min(max(480.0, float(post.quantiles([0.97], atoms)[0])), 780.0)
    hi = float(np.ceil(hi / 60.0) * 60.0)
    return {"lo_g": 0.0, "hi_g": hi, "bins": post.density(0.0, hi, 64, atoms)}


def learn(ctx: Ctx, a: S.LearnArgs) -> dict:
    diner = _need(ctx.repo.get(ctx.site_id, "diner", a.token), "DINER_NOT_FOUND", "Diner token")
    calib = latest_calibration(ctx, a.food_id)
    if not calib:
        raise RuleError("CALIBRATION_REQUIRED", "Calibrate this food's scoop first.", 409)
    view = SiteView(ctx, a.food_id)
    cfg = serving_config(view.settings, None, calib)
    post_final, cohort, own = view.personal(a.token)
    used_refs = {r["observation"].ref for r in own}

    post = Posterior.from_prior(prior=post_final.prior)
    last = None
    steps = []
    mine = [r for r in view.rows if r["diner_token"] == a.token]
    for r in mine[-60:]:
        used = bool(r["eligible"]) and r["observation"].ref in used_refs
        if used:
            post = post.update(r["observation"])
        rec = recommend(post, cfg, cohort=cohort, last_suggested_scoops=last)
        if rec.basis in ("personal", "cohort"):
            last = rec.scoops
        q10, q50, q90 = post.quantiles([0.10, 0.50, 0.90])
        steps.append({**_public_row(r), "used_for_learning": used,
                      "after": {"median_g": q50, "p10_g": q10, "p90_g": q90, "scoops": rec.scoops, "basis": rec.basis}})

    consumed_all = [r["consumed_g"] for r in mine if r["consumed_g"] is not None]
    naive = naive_next_portion(consumed_all)
    rec_now = _recommendation(ctx, view, a.token, None, calib, diner)
    return {
        "diner": diner, "food_id": a.food_id, "evidence_class": ctx.train_class,
        "calibration": {"version": calib["cal_version"], "mean_g": calib["mean_g"], "sd_g": calib["sd_g"]},
        "history": steps, "counts": _counts(view, a.token),
        "predictive": summarise(post_final), "density": _density(post_final, calib["mean_g"]),
        "recommendation": rec_now,
        "naive_average_g": naive,
        "prior": post_final.prior.describe(),
        "limitations": [
            "Estimates describe this food and recipe only.",
            "Demand here means what this diner chooses to eat at this meal with access to seconds. It is not a nutritional need.",
        ],
    }


def whatif(ctx: Ctx, a: S.WhatIfArgs) -> dict:
    """Same empty plate, different evidence. All three branches run the real
    likelihoods on this diner's actual posterior."""
    _need(ctx.repo.get(ctx.site_id, "diner", a.token), "DINER_NOT_FOUND", "Diner token")
    calib = latest_calibration(ctx, a.food_id)
    if not calib:
        raise RuleError("CALIBRATION_REQUIRED", "Calibrate this food's scoop first.", 409)
    view = SiteView(ctx, a.food_id)
    cfg = serving_config(view.settings, None, calib)
    free = replace(cfg, max_change_scoops=99.0)
    base, cohort, own = view.personal(a.token)
    last = view.last_suggested(a.token)
    tol = max(4.0, 1.5 * float(np.hypot(calib["sd_g"] * np.sqrt(max(a.plate_g / calib["mean_g"], 0.5)), calib["scale_resolution_g"]))) + 0.03 * a.plate_g

    def branch(post: Posterior, used: bool, label: str, note: str) -> dict:
        usable = post if (post.n_interval + post.n_censored) else None
        rec = recommend(usable, cfg, cohort=cohort, last_suggested_scoops=last)
        rec_free = recommend(usable, free, cohort=cohort)
        show = post if usable is not None else (cohort or post)
        return {"label": label, "note": note, "used_for_learning": used,
                "predictive": summarise(show), "density": _density(show, calib["mean_g"]),
                "scoops": rec.scoops, "grams": rec.grams, "basis": rec.basis,
                "scoops_without_change_limit": rec_free.scoops, "reasons": rec.reasons}

    n = a.repeats
    enough_obs = Observation("interval", max(a.plate_g - tol, 0.0), a.plate_g + tol)
    more_obs = Observation("right_censored", max(a.plate_g - tol, 0.0))
    branches = {
        "unknown": branch(base, False, "Empty plate, nobody asked",
                          "Ambiguous: the plate alone cannot tell enough from still hungry. Recorded as 0 g waste; the estimate is not moved."),
        "enough": branch(base.update_all([enough_obs] * n), True,
                         "Empty plate, said it was enough",
                         f"Demand was about {a.plate_g} g (within {tol:.0f} g): interval likelihood F(hi) - F(lo)."),
        "wanted_more": branch(base.update_all([more_obs] * n), True,
                              "Empty plate, wanted more",
                              f"Demand was at least about {a.plate_g} g: right-censored likelihood 1 - F(lo). It can only move the estimate up."),
    }
    consumed_all = [r["consumed_g"] for r in view.rows if r["diner_token"] == a.token and r["consumed_g"] is not None]
    naive_g = naive_next_portion(consumed_all + [a.plate_g] * n)
    step = cfg.step_scoops
    naive_scoops = max(cfg.min_scoops, round(naive_g / cfg.scoop_g / step) * step)
    return {
        "plate_g": a.plate_g, "repeats": n, "tolerance_g": round(tol, 1), "scoop_g": calib["mean_g"],
        "history_eligible": len(own), "evidence_class": ctx.train_class,
        "branches": branches,
        "naive": {"label": "A plain moving average", "note": "Treats every empty plate as an exact measure of appetite, so a hungry diner keeps getting the same portion.",
                  "next_g": naive_g, "scoops": naive_scoops},
    }


# --------------------------------------------------------------------------
# Kitchen
# --------------------------------------------------------------------------
def kitchen_plan(ctx: Ctx, a: S.KitchenArgs) -> dict:
    session = _need(ctx.repo.get(ctx.site_id, "session", a.session_id), "SESSION_NOT_FOUND", "Meal service")
    calib = latest_calibration(ctx, a.food_id)
    if not calib:
        raise RuleError("CALIBRATION_REQUIRED", "Calibrate this food's scoop first.", 409)
    over = {k: v for k, v in a.model_dump(exclude={"session_id", "food_id", "policy", "alpha"}).items() if v is not None}
    s = {**session, **over}
    view = SiteView(ctx, a.food_id)
    st = view.settings
    cfg = serving_config(st, s, calib)
    conventional_g = cfg.conventional_scoops * cfg.scoop_g

    diners = {d["token"]: d for d in ctx.repo.list(ctx.site_id, "diner") if not d.get("deleted")}
    known: list[DinerForecast] = []
    firsts: list[float] = []
    for tok in sorted({r["diner_token"] for r in view.training_rows()} & set(diners)):
        post, cohort, own = view.personal(tok)
        d = diners[tok]
        if a.policy == "conventional":
            first = conventional_g
        else:
            first = recommend(post, cfg, cohort=cohort, last_suggested_scoops=view.last_suggested(tok),
                              requested_scoops=d.get("portion_preference_scoops"),
                              opted_out=bool(d.get("personalization_opt_out"))).grams
        known.append(DinerForecast(post.sample, float(first)))
        firsts.append(float(first))

    cohort_all, n_pooled = view.cohort()
    cold_post = cohort_all or Posterior.from_prior(prior=view.default_prior())
    cold_first = conventional_g if a.policy == "conventional" else recommend(None, cfg, cohort=cohort_all).grams
    cold = DinerForecast(cold_post.sample, float(cold_first))

    inp = KitchenInputs(
        expected_attendance=int(s["expected_attendance"]), scoop_g=cfg.scoop_g, refill_g=cfg.refill_scoops * cfg.scoop_g,
        attendance_cv=float(s["attendance_cv"]), meal_effect_sd=float(st["meal_effect_sd"]),
        intervals=int(s["intervals"]), interval_minutes=int(s["interval_minutes"]),
        batch_g=int(s["batch_g"]), lead_intervals=int(round(s["lead_minutes"] / s["interval_minutes"])),
        max_parallel_batches=int(s["max_parallel_batches"]), stock_cooked_equiv_g=int(s["stock_cooked_equiv_g"]),
        refill_capacity_per_interval=int(s["refill_capacity_per_interval"]), refill_uptake=float(st["refill_uptake"]),
        alpha=float(a.alpha if a.alpha is not None else st["alpha"]),
    )
    out = plan(inp, known, cold)

    served = [r for r in view.rows if r["session_id"] == a.session_id and r["status"] in ("open", "returned")]
    returned = [r for r in served if r["status"] == "returned"]
    out["observed"] = {
        "kind": "observed", "plates_served": len(served), "served_g": sum(r["total_served_g"] for r in served),
        "returns_recorded": len(returned), "returned_g": sum(r["returned_g"] for r in returned),
        "prepared_g": session.get("prepared_g"), "kitchen_surplus_g": session.get("kitchen_surplus_g"),
        "surplus_destination": session.get("surplus_destination"),
    }
    out["inputs"] = {**{k: s[k] for k in ("expected_attendance", "attendance_cv", "intervals", "interval_minutes", "batch_g",
                                           "lead_minutes", "max_parallel_batches", "stock_cooked_equiv_g",
                                           "refill_capacity_per_interval", "seconds_accessible", "conventional_scoops")},
                     "alpha": inp.alpha, "policy": a.policy}
    out["first_portions"] = {
        "known_diners": len(known), "known_mean_g": float(np.mean(firsts)) if firsts else None,
        "cold_start_g": float(cold_first), "conventional_g": float(conventional_g), "scoop_g": cfg.scoop_g,
        "cold_start_basis": "cohort" if cohort_all is not None else "default-prior-assumption",
    }
    out["scope"] = {"food_id": a.food_id, "session_id": a.session_id, "evidence_class": ctx.train_class,
                    "training_plates": len(view.training_rows()), "source_mode": ctx.mode}
    return out


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------
def _prediction_check(view: SiteView, rows: list[dict], cap: int = 250) -> dict:
    """Learn-then-predict on the workspace's own plates: predict each eligible
    plate from that diner's earlier plates only, then update."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["returned_at"], r["session_id"])):
        by[r["diner_token"]].append(r)
    abs_err, covered, widths, log_scores = [], [], [], []
    done = 0
    for tok, rs in by.items():
        post = Posterior.from_prior(prior=view.default_prior())
        for k, r in enumerate(rs):
            obs: Observation = r["observation"]
            if k >= 1 and done < cap:
                atoms = post.atoms(n_nodes=24)
                q10, q50, q90 = post.quantiles([0.10, 0.50, 0.90], atoms)
                if obs.kind == "interval":
                    c = r["consumed_g"]
                    abs_err.append(abs(q50 - c))
                    covered.append(q10 <= c <= q90)
                    widths.append(q90 - q10)
                else:
                    log_scores.append(float(np.log(max(1.0 - float(post.cdf(obs.lo_g, atoms)), 1e-6))))
                done += 1
            post = post.update(obs)
    n = len(abs_err)
    return {
        "kind": "predicted-vs-observed", "method": "per-diner, chronological; each plate predicted from that diner's earlier eligible plates",
        "n_uncensored": n, "mae_g": float(np.mean(abs_err)) if n else None,
        "coverage_80": float(np.mean(covered)) if n else None, "mean_interval_width_g": float(np.mean(widths)) if n else None,
        "n_censored": len(log_scores), "censored_mean_log_score": float(np.mean(log_scores)) if log_scores else None,
        "capped_at": cap if done >= cap else None,
    }


def evidence(ctx: Ctx, a: S.EvidenceArgs) -> dict:
    foods = [f["id"] for f in ctx.repo.list(ctx.site_id, "food") if a.food_id in (None, f["id"])]
    sessions = {s["id"]: s for s in ctx.repo.list(ctx.site_id, "session")}
    groups = []
    for fid in foods:
        view = SiteView(ctx, fid)
        keyed: dict[tuple, list[dict]] = defaultdict(list)
        for r in view.rows:
            keyed[(r["origin"], r["measurement_status"] or ("generated" if r["origin"] == "simulated" else "declared"))].append(r)
        for (origin, mstatus), rows in sorted(keyed.items(), key=lambda kv: str(kv[0])):
            served = [r for r in rows if r["status"] in ("open", "returned")]
            ret = [r for r in served if r["status"] == "returned"]
            exact = [r for r in ret if r["consumed_g"] is not None]
            with_rec = [r for r in served if (r.get("recommended") or {}).get("grams")]
            overrides = [r for r in with_rec if abs(r["initial_g"] - r["recommended"]["grams"]) > 0.25 * (r.get("scoop_g") or 60)]
            sec = defaultdict(int)
            for r in ret:
                sec[r["seconds_status"]] += 1
            cls = defaultdict(int)
            for r in rows:
                cls[r["classification"]] += 1
            methods: dict[str, int] = defaultdict(int)
            for r in ret:
                methods[r.get("return_measurement") or "unspecified"] += 1
            sids = sorted({r["session_id"] for r in rows})
            dates = sorted(d for d in {sessions.get(s, {}).get("service_date") for s in sids} if d)
            kitchen = defaultdict(int)
            prepared = 0
            for sid in sids:
                s = sessions.get(sid, {})
                if s.get("kitchen_surplus_g") is not None:
                    kitchen[s.get("surplus_destination") or "unknown"] += s["kitchen_surplus_g"]
                prepared += s.get("prepared_g") or 0
            elig = [r for r in rows if r["eligible"]]
            groups.append({
                "food_id": fid, "origin": origin, "measurement_status": mstatus,
                "evidence_class": evidence_class(rows[0]),
                "kind": "observed" if origin != "simulated" and mstatus != "synthetic" else "simulated",
                "scope": {"sessions": len(sids), "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
                          "diners": len({r["diner_token"] for r in rows})},
                "plates": {"served": len(served), "returned": len(ret), "missing_or_open": len(served) - len(ret),
                           "declined": sum(r["status"] == "declined" for r in rows),
                           "return_coverage": (len(ret) / len(served)) if served else None},
                "grams": {"served": sum(r["total_served_g"] for r in served), "returned_edible": sum(r["returned_g"] for r in ret),
                          "consumed_exact": sum(r["consumed_g"] for r in exact), "plates_with_exact_consumption": len(exact)},
                "plate_waste_per_returned_plate_g": {"value": (sum(r["returned_g"] for r in ret) / len(ret)) if ret else None, "n": len(ret)},
                "wanted_more": {"n": sum(r["satisfaction"] == "wanted_more" for r in ret), "of": len(ret)},
                "seconds": {"plates_with_seconds": sum(bool(r["seconds_g"]) for r in served), **dict(sec)},
                "overrides": {"n": len(overrides), "of": len(with_rec)},
                "classification": dict(cls),
                "return_methods": dict(methods),
                "learning": {"eligible": len(elig), "interval": cls.get("interval", 0), "right_censored": cls.get("right_censored", 0),
                             "excluded": len(rows) - len(elig)},
                "kitchen": {"prepared_g": prepared or None, "surplus_by_destination_g": dict(kitchen),
                            "note": "Entered by staff per service. Surplus that is retained or donated is not discarded waste."},
                "prediction_check": _prediction_check(view, elig),
            })
    return {
        "scope": {"site_id": ctx.site_id, "mode": ctx.mode, "generated_at": S.utcnow().isoformat(), "food_filter": a.food_id},
        "groups": groups,
        "notes": [
            "Observed values are declared by the operator who entered them; the app does not independently verify a weighing.",
            "A missing return is missing, not zero waste. Return coverage is shown for every group.",
            "Outcomes of a serving policy that was not actually used are never shown here; policy comparisons live in the simulation section.",
        ],
    }


OPS = {
    "bootstrap": (S.Empty, bootstrap, False),
    "update_settings": (S.SettingsPatch, update_settings, True),
    "create_food": (S.FoodIn, create_food, True),
    "create_calibration": (S.CalibrationIn, create_calibration, True),
    "create_session": (S.SessionIn, create_session, True),
    "update_session": (S.SessionPatch, update_session, True),
    "create_diner": (S.DinerIn, create_diner, True),
    "update_diner": (S.DinerIn, update_diner, True),
    "export_diner": (S.DinerRef, export_diner, False),
    "delete_diner": (S.DinerRef, delete_diner, True),
    "diner_state": (S.DinerStateArgs, diner_state, False),
    "record_event": (S.EventIn, record_event, True),
    "session_log": (S.SessionRef, session_log, False),
    "learn": (S.LearnArgs, learn, False),
    "whatif": (S.WhatIfArgs, whatif, False),
    "kitchen_plan": (S.KitchenArgs, kitchen_plan, False),
    "evidence": (S.EvidenceArgs, evidence, False),
}
