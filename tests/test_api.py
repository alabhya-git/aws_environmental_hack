"""End-to-end API behaviour through FastAPI, on the in-memory pilot store and the stateless demo."""
import os
import uuid

os.environ.update(PROOFPLATE_ENV="local", PROOFPLATE_AUTH="dev", PROOFPLATE_STORE="memory")

import pytest
from fastapi.testclient import TestClient

from proofplate import main

client = TestClient(main.app)
ALICE = {"x-dev-user": "alice"}
BOB = {"x-dev-user": "bob"}
OPER = {"x-dev-user": "oper"}


def eid():
    return uuid.uuid4().hex


def call(op, args, headers=ALICE, expect=200):
    r = client.post(f"/api/pilot/{op}", json={"args": args}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()["result"] if expect == 200 else r.json()["error"]


@pytest.fixture(scope="module", autouse=True)
def sites():
    repo = main.pilot_repo()
    repo.put_membership("dev:alice", "site-a", "manager")
    repo.put_membership("dev:bob", "site-b", "manager")
    repo.put_membership("dev:oper", "site-a", "operator")


@pytest.fixture(scope="module")
def setup():
    food = call("create_food", {"name": "Steamed rice"})
    cal = call("create_calibration", {"food_id": food["id"], "weights_g": [58, 61, 60, 63, 59, 62, 60, 57, 61, 60] * 2})
    ses = call("create_session", {"name": "Lunch", "service_date": "2026-10-11", "food_ids": [food["id"]]})
    diner = call("create_diner", {"token": "T-1"})
    return {"food": food["id"], "session": ses["id"], "token": diner["token"], "cal": cal}


def ev(s, type_, **kw):
    return {"event_id": eid(), "session_id": s["session"], "diner_token": kw.pop("token", s["token"]), "food_id": s["food"], "type": type_, **kw}


def test_health_and_config_are_public():
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/config").json()["auth"] == "dev"


def test_pilot_requires_identity_and_membership():
    assert client.post("/api/pilot/bootstrap", json={"args": {}}).status_code == 401
    r = client.post("/api/pilot/bootstrap", json={"args": {}}, headers={"x-dev-user": "stranger"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_MEMBER"


def test_pilot_workspace_starts_empty_and_is_measured():
    b = call("bootstrap", {}, headers=BOB)
    assert b["foods"] == [] and b["diners"] == [] and b["evidence_class"] == "measured"


def test_calibration_stats_and_versions(setup):
    cal = setup["cal"]
    assert cal["n"] == 20 and cal["cal_version"] == 1 and 59 < cal["mean_g"] < 61
    cal2 = call("create_calibration", {"food_id": setup["food"], "weights_g": [70] * 6})
    assert cal2["cal_version"] == 2
    cal3 = call("create_calibration", {"food_id": setup["food"], "weights_g": [58, 61, 60, 63, 59, 62, 60, 57, 61, 60]})
    assert cal3["cal_version"] == 3 and len(call("bootstrap", {})["calibrations"]) == 3


def test_full_loop_changes_the_next_recommendation(setup):
    s = setup
    st = call("diner_state", {"session_id": s["session"], "token": s["token"], "food_id": s["food"]})
    assert st["recommendation"]["basis"] == "conventional" and st["recommendation"]["scoops"] == 3.0
    first = call("record_event", ev(s, "serve_initial", scoops=3, recommended={"scoops": 3.0, "grams": 180, "basis": "conventional"}))
    assert first["participation"]["initial_g"] == 180 and first["event"]["origin"] == "measured"
    call("record_event", ev(s, "serve_seconds", scoops=0.5))
    out = call("record_event", ev(s, "return", grams=100, measurement="weighed", satisfaction="enough", leftover_reason="enough_food"))
    p = out["participation"]
    assert p["total_served_g"] == 210 and p["consumed_g"] == 110 and p["classification"] == "interval"
    learning = out["learning"]
    assert learning["used_for_learning"] is True
    assert learning["before"]["basis"] == "conventional" and learning["after"]["basis"] == "personal"
    assert learning["after"]["scoops"] < 3.0


def test_duplicate_event_is_idempotent(setup):
    s = setup
    call("create_diner", {"token": "T-dup"})
    e = ev(s, "serve_initial", token="T-dup", scoops=2)
    a = call("record_event", e)
    b = call("record_event", e)
    assert a["duplicate"] is False and b["duplicate"] is True
    assert b["participation"]["total_served_g"] == a["participation"]["total_served_g"] == 120
    again = ev(s, "serve_initial", token="T-dup", scoops=2)
    assert call("record_event", again, expect=409)["code"] == "ALREADY_SERVED"


def test_mass_balance_is_enforced_not_clamped(setup):
    s = setup
    call("create_diner", {"token": "T-mb"})
    assert call("record_event", ev(s, "return", token="T-mb", grams=10), expect=409)["code"] == "NO_FIRST_SERVING"
    assert call("record_event", ev(s, "serve_initial", token="T-mb", grams=0, measurement="weighed"), expect=422)["code"] == "ZERO_SERVING"
    call("record_event", ev(s, "serve_initial", token="T-mb", grams=150, measurement="weighed"))
    err = call("record_event", ev(s, "return", token="T-mb", grams=200, measurement="weighed"), expect=422)
    assert err["code"] == "RETURN_EXCEEDS_SERVED"
    ok = call("record_event", ev(s, "return", token="T-mb", grams=200, measurement="weighed", incoming_transfer_g=80, satisfaction="enough"))
    assert ok["participation"]["consumed_g"] == 30
    assert call("record_event", ev(s, "serve_seconds", token="T-mb", scoops=1), expect=409)["code"] == "RETURN_CLOSED"
    assert client.post("/api/pilot/record_event", json={"args": ev(s, "serve_initial", grams=-5)}, headers=ALICE).status_code == 422


def test_wanted_more_and_ambiguous_plates_are_classified_differently(setup):
    s = setup
    for tok, sat, code in [("T-wm", "wanted_more", "right_censored"), ("T-amb", "unsure", "ambiguous_empty")]:
        call("create_diner", {"token": tok})
        call("record_event", ev(s, "serve_initial", token=tok, scoops=3))
        out = call("record_event", ev(s, "return", token=tok, grams=0, measurement="weighed", satisfaction=sat))
        assert out["participation"]["classification"] == code
        assert out["learning"]["used_for_learning"] is (code == "right_censored")
    call("create_diner", {"token": "T-conf"})
    call("record_event", ev(s, "serve_initial", token="T-conf", scoops=3))
    out = call("record_event", ev(s, "return", token="T-conf", grams=120, measurement="weighed", satisfaction="unsure", leftover_reason="dislike"))
    assert out["participation"]["classification"] == "confounded" and out["learning"]["used_for_learning"] is False
    assert out["learning"]["before"]["scoops"] == out["learning"]["after"]["scoops"]


def test_unknown_transfer_invalidates_the_consumption_label(setup):
    s = setup
    call("create_diner", {"token": "T-share"})
    call("record_event", ev(s, "serve_initial", token="T-share", scoops=3))
    out = call("record_event", ev(s, "return", token="T-share", grams=0, measurement="weighed", satisfaction="enough", transfer_unknown=True))
    assert out["participation"]["consumed_g"] is None and out["participation"]["classification"] == "transfer_unknown"


def test_correction_supersedes_and_model_recomputes(setup):
    s = setup
    call("create_diner", {"token": "T-fix"})
    call("record_event", ev(s, "serve_initial", token="T-fix", scoops=3))
    ret = ev(s, "return", token="T-fix", grams=20, measurement="weighed", satisfaction="enough")
    call("record_event", ret)
    before = call("learn", {"token": "T-fix", "food_id": s["food"]})["predictive"]["median_g"]
    fix = ev(s, "return", token="T-fix", grams=120, measurement="weighed", satisfaction="enough", supersedes=ret["event_id"])
    out = call("record_event", fix)
    assert out["participation"]["returned_g"] == 120 and out["participation"]["consumed_g"] == 60
    after = call("learn", {"token": "T-fix", "food_id": s["food"]})
    assert after["predictive"]["median_g"] < before - 20
    assert after["counts"]["eligible"] == 1                       # one plate, not two
    twice = ev(s, "return", token="T-fix", grams=90, measurement="weighed", satisfaction="enough", supersedes=ret["event_id"])
    assert call("record_event", twice, expect=409)["code"] == "ALREADY_CORRECTED"
    bad = ev(s, "return", token="T-fix", grams=999, measurement="weighed", supersedes=fix["event_id"])
    assert call("record_event", bad, expect=422)["code"] == "RETURN_EXCEEDS_SERVED"


def test_requested_portion_and_opt_out(setup):
    s = setup
    st = call("diner_state", {"session_id": s["session"], "token": s["token"], "food_id": s["food"], "requested_scoops": 5})
    assert st["recommendation"]["scoops"] == 5 and st["recommendation"]["basis"] == "requested"
    call("update_diner", {"token": s["token"], "personalization_opt_out": True})
    st = call("diner_state", {"session_id": s["session"], "token": s["token"], "food_id": s["food"]})
    assert st["recommendation"]["basis"] == "opted_out"
    call("update_diner", {"token": s["token"], "personalization_opt_out": False})


def test_photo_estimate_is_labelled_and_widens_the_interval(setup):
    s = setup
    out = {}
    for tok, meas, extra in [("T-ph-w", "weighed", {}), ("T-ph-p", "photo_estimate", {"photo_sd_g": 25})]:
        call("create_diner", {"token": tok})
        call("record_event", ev(s, "serve_initial", token=tok, scoops=3))
        r = call("record_event", ev(s, "return", token=tok, grams=40, measurement=meas, satisfaction="enough",
                                    leftover_reason="enough_food", **extra))
        out[meas] = r["participation"]
        assert r["participation"]["return_measurement"] == meas
    assert out["photo_estimate"]["tolerance_g"] > out["weighed"]["tolerance_g"] + 10
    assert out["photo_estimate"]["photo_detail"] is None           # optional: older clients do not send it
    assert out["photo_estimate"]["consumed_g"] == out["weighed"]["consumed_g"]
    g = call("evidence", {})["groups"][0]
    assert g["return_methods"].get("photo_estimate", 0) >= 1 and g["return_methods"].get("weighed", 0) >= 1
    assert client.post("/api/pilot/record_event", json={"args": ev(s, "return", token="T-ph-p", grams=5, photo_sd_g=0)}, headers=ALICE).status_code == 422
    call("create_diner", {"token": "T-ph-d"})
    call("record_event", ev(s, "serve_initial", token="T-ph-d", scoops=3))
    detail = {"method": "before_after", "share_before": 0.41, "share_after": 0.09, "reference_g": 180, "exponent": 1.25, "edited": True, "engine": "model"}
    r = call("record_event", ev(s, "return", token="T-ph-d", grams=27, measurement="photo_estimate", photo_sd_g=11,
                                satisfaction="enough", leftover_reason="enough_food", photo_detail=detail))
    assert r["participation"]["photo_detail"] == detail and r["event"]["photo_detail"]["share_after"] == 0.09
    bad = {**detail, "share_after": 1.7}
    assert client.post("/api/pilot/record_event", json={"args": ev(s, "return", token="T-ph-d", grams=5, photo_detail=bad)}, headers=ALICE).status_code == 422
    assert client.post("/api/pilot/record_event", json={"args": ev(s, "return", token="T-ph-d", grams=5, photo_detail={**detail, "image": "data:..."})}, headers=ALICE).status_code == 422


def test_serving_style_setting_changes_the_policy_and_needs_a_manager(setup):
    s = setup
    call("create_diner", {"token": "T-style"})
    for g in (150, 190, 230):
        ses = call("create_session", {"name": f"S{g}", "service_date": "2026-10-10", "food_ids": [s["food"]]})["id"]
        base = {"session_id": ses, "diner_token": "T-style", "food_id": s["food"]}
        call("record_event", {**base, "event_id": eid(), "type": "serve_initial", "grams": 300, "measurement": "weighed"})
        call("record_event", {**base, "event_id": eid(), "type": "return", "grams": 300 - g, "measurement": "weighed",
                              "satisfaction": "enough", "leftover_reason": "enough_food"})
    assert call("update_settings", {"refill_friction_g": 40}, headers=OPER, expect=403)["code"] == "FORBIDDEN"
    args = {"session_id": s["session"], "token": "T-style", "food_id": s["food"]}
    lean = call("update_settings", {"refill_friction_g": 0})
    assert lean["refill_friction_g"] == 0
    small = call("diner_state", args)["recommendation"]
    call("update_settings", {"refill_friction_g": 80})
    big = call("diner_state", args)["recommendation"]
    # the per-meal change limit can hold both at the same portion; the setting must still reach the policy
    assert big["scoops"] >= small["scoops"]
    assert small["constraints"]["refill_friction_g"] == 0 and big["constraints"]["refill_friction_g"] == 80
    cost = lambda rec, scoops: next(r["cost"] for r in rec["tradeoff"] if r["scoops"] == scoops)
    assert cost(big, 1.0) > cost(small, 1.0)        # a small portion is penalised more when trips cost more
    assert call("update_settings", {"refill_friction_g": 20})["refill_friction_g"] == 20
    assert client.post("/api/pilot/update_settings", json={"args": {"refill_friction_g": 500}}, headers=ALICE).status_code == 422


def test_a_service_lists_its_own_menu_and_rejects_food_that_is_not_on_it(setup):
    s = setup
    dal = call("create_food", {"name": "Dal tadka"})
    ses = call("create_session", {"name": "Dinner", "service_date": "2026-10-12", "food_ids": [dal["id"]]})
    assert ses["food_ids"] == [dal["id"]]
    err = call("diner_state", {"session_id": ses["id"], "token": s["token"], "food_id": s["food"]}, expect=422)
    assert err["code"] == "FOOD_NOT_ON_MENU"
    too_many = [f"food-{i}" for i in range(13)]
    assert client.post("/api/pilot/create_session", json={"args": {"name": "X", "service_date": "2026-10-12", "food_ids": too_many}}, headers=ALICE).status_code == 422
    assert client.post("/api/pilot/create_session", json={"args": {"name": "X", "service_date": "2026-10-12", "food_ids": []}}, headers=ALICE).status_code == 422


def test_tenant_isolation(setup):
    s = setup
    b = call("bootstrap", {}, headers=BOB)
    assert b["site_id"] == "site-b" and b["diners"] == [] and b["sessions"] == []
    err = call("diner_state", {"session_id": s["session"], "token": s["token"], "food_id": s["food"]}, headers=BOB, expect=404)
    assert err["code"] == "SESSION_NOT_FOUND"
    r = client.post("/api/pilot/bootstrap", json={"args": {}, "site_id": "site-a"}, headers=BOB)
    assert r.status_code == 422                                   # a client-supplied tenant is rejected outright
    assert call("evidence", {}, headers=BOB)["groups"] == []


def test_operator_role_cannot_do_manager_actions(setup):
    assert call("create_food", {"name": "Dal"}, headers=OPER, expect=403)["code"] == "FORBIDDEN"
    assert call("delete_diner", {"token": setup["token"]}, headers=OPER, expect=403)["code"] == "FORBIDDEN"
    assert call("bootstrap", {}, headers=OPER)["role"] == "operator"


def test_kitchen_plan_and_infeasible_stock(setup):
    s = setup
    ok = call("kitchen_plan", {"session_id": s["session"], "food_id": s["food"], "refill_capacity_per_interval": 200})
    assert ok["status"] == "feasible" and ok["planned_total_g"] > 0 and ok["forecast"]["kind"] == "predicted"
    assert ok["observed"]["kind"] == "observed" and ok["observed"]["plates_served"] >= 1
    bad = call("kitchen_plan", {"session_id": s["session"], "food_id": s["food"], "stock_cooked_equiv_g": 5000, "refill_capacity_per_interval": 200})
    assert bad["status"] == "infeasible" and bad["infeasible_reasons"][0]["code"] == "STOCK"
    more = call("kitchen_plan", {"session_id": s["session"], "food_id": s["food"], "expected_attendance": 400,
                                 "stock_cooked_equiv_g": 500000, "refill_capacity_per_interval": 800})
    assert more["planned_total_g"] > ok["planned_total_g"]


def test_evidence_labels_and_missing_returns(setup):
    s = setup
    call("create_diner", {"token": "T-noreturn"})
    call("record_event", ev(s, "serve_initial", token="T-noreturn", scoops=3))
    g = call("evidence", {})["groups"][0]
    assert g["origin"] == "measured" and g["kind"] == "observed"
    assert g["plates"]["missing_or_open"] >= 1 and g["plates"]["return_coverage"] < 1
    assert g["plate_waste_per_returned_plate_g"]["n"] == g["plates"]["returned"]
    call("update_session", {"session_id": s["session"], "status": "closed"})
    g = call("evidence", {})["groups"][0]
    assert g["classification"].get("missing_return", 0) >= 1          # missing, not zero waste
    call("create_diner", {"token": "T-late"})
    assert call("record_event", ev(s, "serve_initial", token="T-late", scoops=3), expect=409)["code"] == "SESSION_CLOSED"
    call("update_session", {"session_id": s["session"], "status": "open"})


def test_export_and_import_keep_provenance(setup):
    s = setup
    exp = call("export", {"what": "participations", "format": "csv"})
    assert exp["content"].splitlines()[0].startswith("service_date,session_id,diner_token") and exp["count"] >= 5
    manifest = {"source_name": "Test file", "license": "CC0", "collection_method": "typed by hand for a test",
                "measurement_status": "synthetic", "what_it_measures": "nothing real", "food_id": s["food"]}
    bad_csv = "service_date,diner_token,initial_g,returned_g,satisfaction\n2026-10-01,T-9,180,400,enough\nnope,T-9,abc,,\n"
    prev = call("import_preview", {"csv_text": bad_csv, "manifest": manifest})
    assert prev["can_commit"] is False and prev["error_count"] >= 3
    assert call("import_commit", {"csv_text": bad_csv, "manifest": manifest}, expect=422)["code"] == "IMPORT_HAS_ERRORS"
    good = "service_date,diner_token,initial_g,seconds_g,returned_g,satisfaction\n" + "".join(
        f"2026-09-{d:02d},T-imp{k},180,0,{20 + k},enough\n" for d in (1, 2) for k in range(3))
    prev = call("import_preview", {"csv_text": good, "manifest": manifest})
    assert prev["can_commit"] and prev["label"] == {"origin": "imported", "measurement_status": "synthetic", "used_for_live_model": False}
    done = call("import_commit", {"csv_text": good, "manifest": manifest})
    assert done["events_created"] == 12
    assert call("import_commit", {"csv_text": good, "manifest": manifest})["events_created"] == 0     # re-import is a no-op
    groups = call("evidence", {})["groups"]
    imported = next(g for g in groups if g["origin"] == "imported")
    assert imported["measurement_status"] == "synthetic" and imported["kind"] == "simulated"
    learn = call("learn", {"token": "T-imp0", "food_id": s["food"]})
    assert learn["counts"]["eligible"] == 2 and all(not h["used_for_learning"] for h in learn["history"])  # never trains the measured model


def test_delete_diner_removes_evidence_from_models(setup):
    s = setup
    assert call("export_diner", {"token": "T-wm"})["events"]
    out = call("delete_diner", {"token": "T-wm"})
    assert out["deleted_events"] == 2
    assert call("learn", {"token": "T-wm", "food_id": s["food"]}, expect=404)["code"] == "DINER_NOT_FOUND"
    assert all(d["token"] != "T-wm" for d in call("bootstrap", {})["diners"])


# ------------------------------- public demo -------------------------------

def demo(op, args, sandbox=None, expect=200):
    r = client.post(f"/api/demo/{op}", json={"args": args, "sandbox": sandbox or []})
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else r.json()["error"]


def test_demo_is_seeded_simulated_and_stateless():
    b = demo("bootstrap", {})["result"]
    assert b["mode"] == "demo" and b["evidence_class"] == "simulated" and len(b["diners"]) >= 20
    ses = next(x for x in b["sessions"] if x["status"] == "open")
    food = b["foods"][0]["id"]
    st = demo("diner_state", {"session_id": ses["id"], "token": "T-104", "food_id": food})["result"]
    assert st["recommendation"]["basis"] == "personal" and st["recommendation"]["scoops"] <= 2.5
    hungry = demo("diner_state", {"session_id": ses["id"], "token": "T-221", "food_id": food})["result"]
    assert hungry["recommendation"]["scoops"] >= 3.5 and "INCLUDES_WANTED_MORE_EVIDENCE" in hungry["recommendation"]["reasons"]

    e1 = {"event_id": eid(), "session_id": ses["id"], "diner_token": "T-104", "food_id": food, "type": "serve_initial", "scoops": 2}
    r1 = demo("record_event", e1)
    assert r1["result"]["event"]["origin"] == "simulated" and len(r1["sandbox_append"]) == 1
    # the server kept nothing: without the sandbox the plate does not exist
    again = demo("diner_state", {"session_id": ses["id"], "token": "T-104", "food_id": food})["result"]
    assert again["participation"] is None
    with_box = demo("diner_state", {"session_id": ses["id"], "token": "T-104", "food_id": food}, r1["sandbox_append"])["result"]
    assert with_box["participation"]["initial_g"] == r1["result"]["event"]["grams"]


def test_whatif_same_empty_plate_different_evidence():
    b = demo("bootstrap", {})["result"]
    w = demo("whatif", {"token": "T-530", "food_id": b["foods"][0]["id"], "plate_g": 180})["result"]
    br = w["branches"]
    base, enough, more = (br[k]["predictive"]["median_g"] for k in ("unknown", "enough", "wanted_more"))
    assert br["unknown"]["used_for_learning"] is False
    assert more > base and more > enough
    assert br["wanted_more"]["scoops_without_change_limit"] >= br["enough"]["scoops_without_change_limit"]
    assert w["naive"]["scoops"] <= br["wanted_more"]["scoops_without_change_limit"]


def test_demo_cannot_reach_pilot_data_and_is_bounded(setup):
    b = demo("bootstrap", {})["result"]
    assert all(d["token"] != setup["token"] for d in b["diners"]) and b["site_id"] == "demo"
    assert demo("bootstrap", {}, sandbox=[{"op": "nonsense"}], expect=422)["code"] == "INVALID"
    big = [{"op": "delete", "kind": "x", "id": str(i)} for i in range(main.MAX_SANDBOX + 1)]
    assert client.post("/api/demo/bootstrap", json={"args": {}, "sandbox": big}).status_code == 422
    # a demo write lands only in the caller's journal; the pilot store is untouched
    before = len(main.pilot_repo().list_events("site-a"))
    ses = next(x for x in b["sessions"] if x["status"] == "open")
    demo("record_event", {"event_id": eid(), "session_id": ses["id"], "diner_token": "T-309", "food_id": b["foods"][0]["id"], "type": "serve_initial", "scoops": 3})
    assert len(main.pilot_repo().list_events("site-a")) == before
    assert main.pilot_repo().list_events("demo") == []


def test_production_refuses_dev_auth(monkeypatch):
    import importlib

    monkeypatch.setenv("PROOFPLATE_ENV", "aws")
    monkeypatch.setenv("PROOFPLATE_AUTH", "dev")
    with pytest.raises(RuntimeError):
        importlib.reload(main)
    monkeypatch.setenv("PROOFPLATE_ENV", "local")
    importlib.reload(main)
