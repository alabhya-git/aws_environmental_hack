"""CSV/JSON export and validated import.

An import always keeps its own provenance: origin is "imported" and the
manifest states whether the file holds measured or synthetic records. Nothing
in an import can be promoted to a pilot measurement, and synthetic imports are
kept out of the live model's training set.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re

from .domain import schemas as S
from .domain.rules import RuleError, validate_append
from .service import Ctx, SiteView, _need

EXPORT_COLUMNS = [
    "service_date", "session_id", "diner_token", "food_id", "origin", "measurement_status", "status",
    "initial_g", "seconds_total_g", "total_served_g", "returned_g", "losses_g", "incoming_transfer_g", "consumed_g",
    "satisfaction", "leftover_reason", "seconds_status", "classification", "eligible",
    "recommended_scoops", "recommended_basis", "serve_measurement", "return_measurement", "calibration_version",
    "served_at", "returned_at",
]
IMPORT_COLUMNS = [
    "service_date", "session_name", "diner_token", "initial_g", "seconds_g", "returned_g",
    "satisfaction", "leftover_reason", "serve_measurement", "return_measurement", "losses_g",
]
MAX_IMPORT_ROWS = 2000


def export(ctx: Ctx, a: S.ExportArgs) -> dict:
    foods = [f["id"] for f in ctx.repo.list(ctx.site_id, "food")]
    if a.what == "events":
        items = ctx.repo.list_events(ctx.site_id)
        page = items[a.cursor:a.cursor + a.limit]
        content = json.dumps(page, indent=1) if a.format == "json" else _csv(page, sorted({k for e in page for k in e}))
    else:
        rows = []
        for fid in foods:
            for r in SiteView(ctx, fid).rows:
                rec = r.get("recommended") or {}
                rows.append({**{k: r.get(k) for k in EXPORT_COLUMNS}, "seconds_total_g": sum(r["seconds_g"]),
                             "recommended_scoops": rec.get("scoops"), "recommended_basis": rec.get("basis")})
        rows.sort(key=lambda r: (r["served_at"] or "", r["diner_token"]))
        items = rows
        page = rows[a.cursor:a.cursor + a.limit]
        content = json.dumps(page, indent=1) if a.format == "json" else _csv(page, EXPORT_COLUMNS)
    nxt = a.cursor + len(page)
    return {
        "filename": f"proofplate-{ctx.mode}-{a.what}.{a.format}", "format": a.format, "content": content,
        "count": len(page), "total": len(items), "next_cursor": nxt if nxt < len(items) else None,
        "provenance_note": "Each row carries its own origin and measurement_status. Simulated and imported rows are never relabelled as measured.",
    }


def _csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else (json.dumps(r[k]) if isinstance(r[k], (dict, list)) else r[k])) for k in columns})
    return buf.getvalue()


def _int(row: dict, key: str, n: int, errors: list, required: bool = False):
    raw = (row.get(key) or "").strip()
    if raw == "":
        if required:
            errors.append({"row": n, "field": key, "message": "required"})
        return None
    if not re.fullmatch(r"\d{1,4}", raw):
        errors.append({"row": n, "field": key, "message": f"'{raw}' is not a whole number of grams between 0 and {S.MAX_G}"})
        return None
    v = int(raw)
    if v > S.MAX_G:
        errors.append({"row": n, "field": key, "message": f"{v} g is above the {S.MAX_G} g limit"})
        return None
    return v


def _parse(ctx: Ctx, a: S.ImportArgs) -> dict:
    _need(ctx.repo.get(ctx.site_id, "food", a.manifest.food_id), "FOOD_NOT_FOUND", "Food")
    text = a.csv_text.lstrip("﻿")
    sha = hashlib.sha256(text.encode()).hexdigest()
    reader = csv.DictReader(io.StringIO(text))
    cols = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in ("service_date", "diner_token", "initial_g") if c not in cols]
    unknown = [c for c in cols if c not in IMPORT_COLUMNS]
    if missing or unknown:
        raise RuleError("IMPORT_COLUMNS", f"Column problem. Missing: {missing or 'none'}. Not recognised: {unknown or 'none'}. Expected: {IMPORT_COLUMNS}")
    status = "synthetic" if ctx.mode == "demo" else a.manifest.measurement_status
    errors: list[dict] = []
    plates: list[dict] = []
    seen: set[tuple] = set()
    n = 1
    for n, row in enumerate(reader, start=2):  # header is line 1
        if n - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": n, "field": "", "message": f"more than {MAX_IMPORT_ROWS} rows; split the file"})
            break
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        before = len(errors)
        date = row.get("service_date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            errors.append({"row": n, "field": "service_date", "message": "use YYYY-MM-DD"})
        token = row.get("diner_token", "")
        if not re.fullmatch(S.ID, token):
            errors.append({"row": n, "field": "diner_token", "message": "use an opaque token such as T-104; no names"})
        initial = _int(row, "initial_g", n, errors, required=True)
        if initial == 0:
            errors.append({"row": n, "field": "initial_g", "message": "a serving must be more than 0 g"})
        seconds = _int(row, "seconds_g", n, errors) or 0
        returned = _int(row, "returned_g", n, errors)
        losses = _int(row, "losses_g", n, errors) or 0
        sat = row.get("satisfaction") or "unsure"
        if sat not in ("enough", "wanted_more", "unsure"):
            errors.append({"row": n, "field": "satisfaction", "message": "use enough, wanted_more or unsure"})
        reason = row.get("leftover_reason") or "none"
        if reason not in ("none", "enough_food", "dislike", "interrupted", "shared", "unknown"):
            errors.append({"row": n, "field": "leftover_reason", "message": "not a recognised reason"})
        meas = {}
        for key in ("serve_measurement", "return_measurement"):
            meas[key] = row.get(key) or "weighed"
            if meas[key] not in ("weighed", "scoop_estimate"):
                errors.append({"row": n, "field": key, "message": "use weighed or scoop_estimate"})
        name = row.get("session_name") or "imported"
        if (date, name, token) in seen:
            errors.append({"row": n, "field": "diner_token", "message": "this diner appears twice in the same service"})
        seen.add((date, name, token))
        if len(errors) > before:
            continue
        if returned is not None and returned > initial + seconds - losses:
            errors.append({"row": n, "field": "returned_g", "message": f"returned {returned} g is more than served {initial + seconds} g minus losses {losses} g"})
            continue
        sid = f"imp-{date}-{hashlib.sha1((a.manifest.source_name + name).encode()).hexdigest()[:6]}"
        base = {
            "session_id": sid, "diner_token": token, "food_id": a.manifest.food_id, "site_id": ctx.site_id,
            "operator": ctx.operator, "origin": "imported", "measurement_status": status, "entry": "import",
            "import_ref": sha[:16], "losses_g": 0, "incoming_transfer_g": 0, "transfer_unknown": False,
            "scoop_g": None, "scoop_sd_g": None, "calibration_version": None, "scale_resolution_g": 1,
        }

        def eid(kind: str) -> str:
            return "imp" + hashlib.sha1(f"{sha}|{n}|{kind}".encode()).hexdigest()[:29]

        evs = [{**base, "event_id": eid("i"), "type": "serve_initial", "grams": initial,
                "measurement": meas["serve_measurement"], "event_time": f"{date}T07:00:00+00:00"}]
        if seconds:
            evs.append({**base, "event_id": eid("s"), "type": "serve_seconds", "grams": seconds,
                        "measurement": meas["serve_measurement"], "event_time": f"{date}T07:10:00+00:00"})
        if returned is not None:
            evs.append({**base, "event_id": eid("r"), "type": "return", "grams": returned, "losses_g": losses,
                        "satisfaction": sat, "leftover_reason": reason, "measurement": meas["return_measurement"],
                        "event_time": f"{date}T07:30:00+00:00"})
        plates.append({"row": n, "session_id": sid, "session_name": name, "service_date": date, "token": token, "events": evs})
    return {"sha256": sha, "status": status, "errors": errors, "plates": plates, "rows": max(n - 1, 0)}


def import_preview(ctx: Ctx, a: S.ImportArgs) -> dict:
    ctx.need_manager()
    p = _parse(ctx, a)
    return {
        "sha256": p["sha256"], "rows": p["rows"], "valid_rows": len(p["plates"]), "errors": p["errors"][:200],
        "error_count": len(p["errors"]),
        "will_create": {"plates": len(p["plates"]), "events": sum(len(x["events"]) for x in p["plates"]),
                        "sessions": len({x["session_id"] for x in p["plates"]}), "diners": len({x["token"] for x in p["plates"]})},
        "label": {"origin": "imported", "measurement_status": p["status"],
                  "used_for_live_model": p["status"] == "measured" and ctx.mode == "pilot"},
        "sample": [{"row": x["row"], "token": x["token"], "service_date": x["service_date"],
                    "events": [{"type": e["type"], "grams": e["grams"]} for e in x["events"]]} for x in p["plates"][:5]],
        "can_commit": not p["errors"] and bool(p["plates"]),
    }


def import_commit(ctx: Ctx, a: S.ImportArgs) -> dict:
    ctx.need_manager()
    p = _parse(ctx, a)
    if p["errors"] or not p["plates"]:
        raise RuleError("IMPORT_HAS_ERRORS", "Fix every row error before importing. Nothing was written.")
    repo, site = ctx.repo, ctx.site_id
    now = S.utcnow().isoformat()
    created = duplicates = 0
    for plate in p["plates"]:
        if not repo.get(site, "session", plate["session_id"]):
            repo.put(site, "session", plate["session_id"], {
                **S.SessionIn(name=plate["session_name"][:60] or "imported", service_date=plate["service_date"],
                              food_ids=[a.manifest.food_id]).model_dump(),
                "status": "closed", "created_at": now, "origin": "imported", "import_ref": p["sha256"][:16]})
        if not repo.get(site, "diner", plate["token"]):
            repo.put(site, "diner", plate["token"], {"token": plate["token"], "consent": True, "portion_preference_scoops": None,
                                                     "personalization_opt_out": False, "created_at": now, "origin": "imported"})
        existing = [e for e in repo.list_events(site, diner_token=plate["token"], food_id=a.manifest.food_id)
                    if e["session_id"] == plate["session_id"]]
        ids = {e["event_id"] for e in existing}
        for ev in plate["events"]:
            ev = {**ev, "ingested_at": now}
            if ev["event_id"] in ids:
                duplicates += 1
                continue
            validate_append(existing, ev, session_open=True)
            repo.append_event(site, ev, expect_count=len(existing))
            existing.append(ev)
            created += 1
    repo.put(site, "import", p["sha256"][:16], {
        "manifest": a.manifest.model_dump(), "sha256": p["sha256"], "rows": p["rows"], "events_created": created,
        "measurement_status": p["status"], "operator": ctx.operator, "created_at": now, "schema_version": 1})
    return {"events_created": created, "duplicates_skipped": duplicates, "plates": len(p["plates"]),
            "sha256": p["sha256"], "label": {"origin": "imported", "measurement_status": p["status"]}}


OPS = {
    "export": (S.ExportArgs, export, False),
    "import_preview": (S.ImportArgs, import_preview, False),
    "import_commit": (S.ImportArgs, import_commit, True),
}
