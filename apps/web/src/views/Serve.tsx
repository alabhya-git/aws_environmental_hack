import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, newEventId, OpArgs } from "../api";
import { Link, useWs } from "../App";
import { Plate } from "../Plate";
import { BASIS, CLASS_LABEL, grams, istTime, LEFTOVER_REASONS, REASONS, SATISFACTION, scoopsText, SECONDS_STATUS } from "../text";
import { ErrorNote, Field, Loading, Stepper, useToast } from "../ui";
import { PhotoDetail, PhotoPair } from "./PhotoPair";

type EventArgs = OpArgs["record_event"];
type EventFields = Omit<EventArgs, "event_id" | "session_id" | "diner_token" | "food_id">;

export function SessionPicker() {
  const ws = useWs();
  const [open, setOpen] = useState(false);
  const foodNow = ws.boot.foods.find((f) => f.id === ws.foodId);
  // only foods on this service's menu can be served in it
  const menu = ws.session ? ws.boot.foods.filter((f) => ws.session.food_ids.includes(f.id)) : ws.boot.foods;
  return (
    <div className={`ctx${open ? " open" : ""}`}>
      <button type="button" className="btn sm ctx-summary" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {ws.session ? `${ws.session.service_date} · ${ws.session.name}` : "No service"} · {foodNow?.name || "No food"}
        {ws.calibration ? ` · ${ws.calibration.mean_g} g/scoop` : " · not calibrated"} · {open ? "close" : "change"}
      </button>
      <div className="row ctx-fields">
      <Field label="Meal service">
        <select className="input" value={ws.sessionId || ""} onChange={(e) => ws.setSessionId(e.target.value)}>
          {ws.boot.sessions.map((s) => (
            <option key={s.id} value={s.id}>{s.service_date} · {s.name}{s.status === "closed" ? " (closed)" : ""}</option>
          ))}
        </select>
      </Field>
      <Field label="Food">
        <select className="input" value={ws.foodId || ""} onChange={(e) => ws.setFoodId(e.target.value)}>
          {menu.map((f) => (
            <option key={f.id} value={f.id}>{f.name} · recipe {f.recipe_version}</option>
          ))}
        </select>
      </Field>
      <div className="field">
        <span>Scoop calibration</span>
        {ws.calibration ? (
          <span className="chip used" style={{ minHeight: 44 }} title={`Calibration v${ws.calibration.cal_version}, ${ws.calibration.n} scoops weighed`}>
            {ws.calibration.mean_g} g ± {ws.calibration.sd_g} g per scoop · v{ws.calibration.cal_version}
          </span>
        ) : (
          <span className="chip warn" style={{ minHeight: 44 }}>Not calibrated</span>
        )}
      </div>
      </div>
    </div>
  );
}

export function NeedsSetup() {
  const ws = useWs();
  const missing = !ws.boot.foods.length ? "a food" : !ws.boot.sessions.length ? "a meal service" : !ws.calibration ? "a scoop calibration" : null;
  if (!missing) return null;
  const onlyCalibration = missing === "a scoop calibration";
  return (
    <div className="stack l">
      {onlyCalibration ? <SessionPicker /> : null}
      <div className="card stack">
        <h2>{onlyCalibration ? "This food's scoop has not been calibrated yet" : `This workspace needs ${missing} first`}</h2>
        <p className="muted">
          {onlyCalibration
            ? "Portions are suggested in scoops, so the scoop has to be weighed for this food first. Pick another food above, or calibrate this one."
            : "The pilot workspace starts empty on purpose: nothing here is pre-filled or simulated."}
        </p>
        <div><Link to={`/${ws.mode}/setup`} className="btn primary">Open Setup</Link></div>
      </div>
    </div>
  );
}

function ReturnForm({ plate, scoopG, busy, onSubmit, onCancel, correcting }: { plate: any; scoopG: number; busy: boolean; correcting: boolean; onCancel?: () => void; onSubmit: (fields: EventFields) => void }) {
  const [returned, setReturned] = useState(correcting && plate.returned_g != null ? String(plate.returned_g) : "");
  const [measurement, setMeasurement] = useState<NonNullable<EventArgs["measurement"]>>("weighed");
  const [satisfaction, setSatisfaction] = useState<EventArgs["satisfaction"]>(correcting ? plate.satisfaction : null);
  const [reason, setReason] = useState<NonNullable<EventArgs["leftover_reason"]>>(correcting ? plate.leftover_reason || "none" : "none");
  const [seconds, setSeconds] = useState<NonNullable<EventArgs["seconds_status"]>>(plate.seconds_g?.length ? "served" : "not_requested");
  const [photoSd, setPhotoSd] = useState<number | null>(null);
  const [photoDetail, setPhotoDetail] = useState<PhotoDetail | null>(null);
  const typed = (v: string) => { setReturned(v); if (measurement === "photo_estimate") { setMeasurement("weighed"); setPhotoSd(null); setPhotoDetail(null); } };
  const [losses, setLosses] = useState("");
  const [shared, setShared] = useState(false);
  const g = returned === "" ? null : Number(returned);
  const valid = g !== null && Number.isInteger(g) && g >= 0 && satisfaction != null;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    onSubmit({
      type: "return", grams: g, measurement, satisfaction,
      ...(measurement === "photo_estimate" && photoSd ? { photo_sd_g: photoSd, ...(photoDetail ? { photo_detail: photoDetail } : {}) } : {}),
      leftover_reason: g! > 0 ? reason : "none",
      seconds_status: seconds, losses_g: losses ? Number(losses) : 0, transfer_unknown: shared,
    });
  };
  return (
    <form className="stack l" onSubmit={submit}>
      <PhotoPair plateKey={`${plate.session_id}|${plate.diner_token}|${plate.food_id}`} foodId={plate.food_id} initialG={plate.initial_g} totalG={plate.total_served_g}
        onUse={(grams, sd, detail) => { setReturned(String(grams)); setMeasurement("photo_estimate"); setPhotoSd(sd); setPhotoDetail(detail); }} />
      <div className="stack">
      <h3>{correcting ? "Correct the return" : "Plate came back"}</h3>
      <div className="row top">
        <Field label="Edible food left (grams)" hint="Weigh the plate, subtract the empty plate.">
          <input className="input big num" inputMode="numeric" pattern="[0-9]*" value={returned} onChange={(e) => typed(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))} placeholder="0" style={{ maxWidth: 170 }} aria-label="Edible food left in grams" />
        </Field>
        <div className="field">
          <span>Quick</span>
          <div className="chips">
            <button type="button" aria-pressed={returned === "0"} onClick={() => typed("0")}>Empty plate</button>
            <button type="button" onClick={() => typed(String(Math.round(scoopG / 2)))}>≈ ½ scoop</button>
            <button type="button" onClick={() => typed(String(Math.round(scoopG)))}>≈ 1 scoop</button>
          </div>
        </div>
        <div className="field">
          <span>How measured</span>
          <div className="chips">
            <button type="button" aria-pressed={measurement === "weighed"} onClick={() => setMeasurement("weighed")}>Weighed</button>
            <button type="button" aria-pressed={measurement === "scoop_estimate"} onClick={() => { setMeasurement("scoop_estimate"); setPhotoSd(null); }}>Estimated by eye</button>
            {measurement === "photo_estimate" ? <button type="button" aria-pressed>Photo estimate ± {photoSd} g</button> : null}
          </div>
        </div>
      </div>
      <div className="field">
        <span>Ask the diner: was that enough?</span>
        <div className="choice" role="group" aria-label="Was that enough?">
          {(["enough", "wanted_more", "unsure"] as const).map((k) => (
            <button type="button" key={k} aria-pressed={satisfaction === k} onClick={() => setSatisfaction(k)}>{SATISFACTION[k]}</button>
          ))}
        </div>
      </div>
      {g !== null && g > 0 ? (
        <div className="field">
          <span>Why was food left?</span>
          <div className="chips">
            {LEFTOVER_REASONS.map(([k, label]) => (
              <button type="button" key={k} aria-pressed={reason === k} onClick={() => setReason(k as typeof reason)}>{label}</button>
            ))}
          </div>
        </div>
      ) : null}
      <details>
        <summary className="small" style={{ cursor: "pointer" }}>Seconds, spills and sharing</summary>
        <div className="row top" style={{ marginTop: 10 }}>
          <Field label="Seconds">
            <select className="input" value={seconds} onChange={(e) => setSeconds(e.target.value as typeof seconds)}>
              {SECONDS_STATUS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          <Field label="Spilled or dropped (g)">
            <input className="input num" inputMode="numeric" value={losses} onChange={(e) => setLosses(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))} style={{ maxWidth: 130 }} />
          </Field>
          <label className="row" style={{ minHeight: 44, marginTop: 20 }}>
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Food was shared, amount unknown
          </label>
        </div>
      </details>
      <div className="row">
        <button className="btn primary big" disabled={!valid || busy}>{busy ? "Saving…" : correcting ? "Save correction" : "Save return"}</button>
        {onCancel ? <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button> : null}
        {!valid && g !== null ? <span className="muted small">Choose an answer to “was that enough?”.</span> : null}
      </div>
      </div>
    </form>
  );
}

export function Serve() {
  const ws = useWs();
  const { api, sessionId, foodId, session, calibration, mode } = ws;
  const toast = useToast();
  const [input, setInput] = useState(ws.token);
  const [state, setState] = useState<any | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<number | null>(null);
  const [learning, setLearning] = useState<any | null>(null);
  const [notFound, setNotFound] = useState<string | null>(null);
  const [pending, setPending] = useState<EventArgs | null>(null);
  const [log, setLog] = useState<any | null>(null);
  const [correcting, setCorrecting] = useState(false);
  const [consent, setConsent] = useState(false);

  const loadLog = useCallback(() => {
    if (!sessionId) return;
    api("session_log", { session_id: sessionId }).then(setLog).catch(() => undefined);
  }, [api, sessionId]);

  const load = useCallback(async (token: string, keepLearning = false) => {
    if (!sessionId || !foodId || !token) return;
    setBusy(true);
    setError(null);
    setNotFound(null);
    if (!keepLearning) setLearning(null);
    setCorrecting(false);
    try {
      const s = await api("diner_state", { session_id: sessionId, token, food_id: foodId });
      setState(s);
      setChosen(null);
      ws.setToken(token);
    } catch (e) {
      setState(null);
      if ((e as ApiError).code === "DINER_NOT_FOUND") setNotFound(token);
      else setError(e);
    } finally {
      setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, sessionId, foodId]);

  useEffect(() => { loadLog(); }, [loadLog]);
  useEffect(() => { if (ws.token) load(ws.token); /* reload when service or food changes */ }, [sessionId, foodId]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (ev: EventArgs) => {
    setBusy(true);
    setError(null);
    setPending(ev);
    try {
      const out = await api("record_event", ev);
      setPending(null);
      toast.show(out.duplicate ? "Already saved earlier. Nothing was added twice." : mode === "demo" ? "Saved in this browser's demo sandbox" : "Saved to the pilot record");
      if (out.learning) setLearning(out.learning);
      await load(String(ev.diner_token), true);
      loadLog();
    } catch (e) {
      const err = e as ApiError;
      if (!err.offline) setPending(null); // a rejected event is not retried; an unsent one is kept
      setError(e);
      if (err.offline) toast.show("Not saved: no connection. Retry when you are back online.", true);
      setBusy(false);
    }
  };
  const event = (fields: EventFields) =>
    send({ event_id: newEventId(), session_id: sessionId!, diner_token: ws.token, food_id: foodId!, ...fields });

  const register = async () => {
    if (!notFound) return;
    setBusy(true);
    try {
      await api("create_diner", { token: notFound, consent: true });
      await ws.reload();
      setConsent(false);
      await load(notFound);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  if (!ws.boot.foods.length || !calibration || !ws.boot.sessions.length) return <NeedsSetup />;

  const rec = state?.recommendation;
  const plate = state?.participation;
  const status: string = plate?.status || "empty";
  const scoopG: number = calibration.mean_g;
  const shown = chosen ?? rec?.scoops ?? 0;
  const closed = session?.status === "closed";
  const pred = rec?.personal_predictive || rec?.predictive;
  const counts = rec?.evidence_counts;

  return (
    <div className="stack l">
      <div className="row between top">
        <SessionPicker />
        <span className="chip" title="Seconds stay available whatever the suggestion says">
          {session?.seconds_accessible ? "Seconds: available" : "Seconds: not accessible this service"}
        </span>
      </div>
      <div className="serve">
        <div className="stack">
          <form className="row" onSubmit={(e) => { e.preventDefault(); load(input.trim()); }}>
            <Field label="Diner token" hint="Scan or type the token on the diner's card. No names.">
              <input className="input big" value={input} onChange={(e) => setInput(e.target.value.toUpperCase().replace(/[^A-Z0-9_.:\-]/g, "").slice(0, 24))} placeholder="T-104" autoFocus autoComplete="off" aria-label="Diner token" style={{ maxWidth: 260 }} />
            </Field>
            <button className="btn dark big" disabled={!input.trim() || busy} style={{ alignSelf: "center", marginTop: 4 }}>Find</button>
            {mode === "demo" ? (
              <div className="field" style={{ alignSelf: "center" }}>
                <span>Try one of these</span>
                <div className="chips">
                  {["T-104", "T-221", "T-415", "T-530", "T-309"].map((t) => (
                    <button type="button" key={t} aria-pressed={ws.token === t} onClick={() => { setInput(t); load(t); }}>{t}</button>
                  ))}
                </div>
              </div>
            ) : null}
          </form>

          {pending && (error as ApiError | null)?.offline ? (
            <div className="note bad row between" role="alert">
              <span><strong>Pending, not saved.</strong> The last action did not reach the server. It has not changed any estimate.</span>
              <button className="btn sm" onClick={() => send(pending)}>Retry now</button>
            </div>
          ) : error ? <ErrorNote error={error} /> : null}

          {notFound ? (
            <div className="card stack">
              <h2>Token {notFound} is not registered</h2>
              <p className="muted">Register it only if the diner agrees to take part. The token is all that is stored about them.</p>
              <label className="row"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> The diner agreed to take part (18 or older)</label>
              <div><button className="btn primary" disabled={!consent || busy} onClick={register}>Register {notFound}</button></div>
            </div>
          ) : null}

          {busy && !state ? <Loading label="Looking up…" /> : null}

          {state && rec ? (
            <div className="panel stack l">
              <div className="row between">
                <h2>{state.diner.token} · {state.food.name}</h2>
                <div className="row">
                  <span className={`chip ${mode === "demo" ? "sim" : "obs"}`}>{mode === "demo" ? "Simulated history" : "Measured history"}</span>
                  <span className="chip">{BASIS[rec.basis] || rec.basis}</span>
                </div>
              </div>

              {status === "empty" ? (
                <>
                  <div className="suggest">
                    <Plate scoops={shown} label={`Plate with ${scoopsText(shown)}`} />
                    <div className="stack">
                      <div className="kpi">
                        <span className="k">{chosen !== null && chosen !== rec.scoops ? "Diner's chosen first portion" : "Suggested first portion"}</span>
                        <span className="v xl num">{scoopsText(shown)}</span>
                        <span className="s num">{grams(shown * scoopG)} with this scoop</span>
                      </div>
                      {pred ? (
                        <p>
                          Usually chooses to eat about <strong className="num">{grams(pred.median_g)}</strong>
                          <span style={{ color: "var(--amber)" }} className="num"> ({grams(pred.p10_g)}–{grams(pred.p90_g)} on 8 meals in 10)</span>.
                        </p>
                      ) : null}
                      <ul className="plain small">
                        {rec.reasons.map((r: string) => <li key={r}>{REASONS[r] || r}</li>)}
                        {counts ? <li>{counts.eligible} of {counts.plates} earlier plates count as evidence{Object.keys(counts.excluded).length ? `; ${Object.values<number>(counts.excluded).reduce((a, b) => a + b, 0)} were recorded but not used` : ""}.</li> : null}
                        {rec.expected_refill_trips != null ? <li>Expect about {rec.expected_leftover_g.toFixed(0)} g left and a {(rec.p_wants_more_than_first * 100).toFixed(0)}% chance they come back for seconds.</li> : null}
                      </ul>
                    </div>
                  </div>
                  <div className="row between top">
                    <div className="field">
                      <span>Choose a different portion</span>
                      <Stepper value={shown} onChange={setChosen} min={0.5} max={rec.constraints?.max_scoops ?? 8} step={0.5} format={scoopsText} />
                      {chosen !== null && chosen !== rec.scoops ? <small>The diner's choice is served and recorded as a request. <button type="button" className="btn sm ghost" onClick={() => setChosen(null)}>Back to suggestion</button></small> : null}
                    </div>
                    <div className="row">
                      <button className="btn ghost" disabled={busy || closed} onClick={() => event({ type: "decline_food" })}>Doesn't want this food</button>
                      <button className="btn primary big" disabled={busy || closed} onClick={() => event({
                        type: "serve_initial", scoops: shown,
                        recommended: { scoops: rec.scoops, grams: rec.grams, basis: rec.basis, model_digest: rec.predictive?.model_digest ?? null },
                        ...(chosen !== null && chosen !== rec.scoops ? { requested_scoops: chosen } : {}),
                      })}>
                        Serve {scoopsText(shown)}
                      </button>
                    </div>
                  </div>
                  {closed ? <div className="note">This service is closed. Reopen it in Setup to serve, or pick the open service.</div> : null}
                </>
              ) : null}

              {status === "declined" ? <div className="note">Recorded: this diner did not want {state.food.name} at this service. Other food is unaffected.</div> : null}

              {status === "open" ? (
                <>
                  <div className="suggest">
                    <Plate scoops={plate.total_served_g / scoopG} label={`Served ${grams(plate.total_served_g)}`} />
                    <div className="stack">
                      <div className="kpi">
                        <span className="k">On the plate</span>
                        <span className="v num">{grams(plate.total_served_g)}</span>
                        <span className="s">First serving {grams(plate.initial_g)} at {istTime(plate.served_at)}{plate.seconds_g.length ? ` · seconds ${plate.seconds_g.map((x: number) => grams(x)).join(" + ")}` : ""}</span>
                      </div>
                      <div className="field">
                        <span>Seconds</span>
                        <div className="row">
                          <button className="btn" disabled={busy} onClick={() => event({ type: "serve_seconds", scoops: 0.5 })}>+ ½ scoop</button>
                          <button className="btn" disabled={busy} onClick={() => event({ type: "serve_seconds", scoops: 1 })}>+ 1 scoop</button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <hr style={{ border: 0, borderTop: "1px solid var(--line)", width: "100%" }} />
                  <ReturnForm plate={plate} scoopG={scoopG} busy={busy} correcting={false} onSubmit={event} />
                </>
              ) : null}

              {status === "returned" && !correcting ? (
                <>
                  <div className="suggest">
                    <Plate scoops={plate.total_served_g / scoopG} returnedShare={plate.returned_g / Math.max(plate.total_served_g, 1)} tone="ink" label={`Served ${grams(plate.total_served_g)}, ${grams(plate.returned_g)} came back`} />
                    <div className="stack">
                      <div className="row" style={{ gap: 26 }}>
                        <div className="kpi"><span className="k">Served</span><span className="v num">{grams(plate.total_served_g)}</span></div>
                        <div className="kpi"><span className="k" style={{ color: "var(--amber)" }}>Came back</span><span className="v num">{grams(plate.returned_g)}</span></div>
                        <div className="kpi"><span className="k">Eaten</span><span className="v num">{plate.consumed_g == null ? "unknown" : grams(plate.consumed_g)}</span></div>
                      </div>
                      <div className="row">
                        <span className="chip">{SATISFACTION[plate.satisfaction] || "–"}</span>
                        <span className={`chip ${CLASS_LABEL[plate.classification]?.tone || "skip"}`}>{CLASS_LABEL[plate.classification]?.short || plate.classification}</span>
                      </div>
                      <div><button className="btn sm" onClick={() => setCorrecting(true)}>Correct this return</button></div>
                    </div>
                  </div>
                  {learning ? (
                    <div className={`note ${learning.used_for_learning ? "leaf" : ""} stack`}>
                      <strong>{learning.used_for_learning ? "This plate updated the estimate." : "Recorded, but the estimate did not move."}</strong>
                      <span>{learning.label}.</span>
                      <div className="beforeafter" style={{ marginTop: 4 }}>
                        <div>
                          <Plate scoops={learning.before.scoops} size={110} tone="ghost" label={`Before: ${scoopsText(learning.before.scoops)}`} />
                          <div className="small muted">Suggestion before</div>
                          <div style={{ fontWeight: 650 }} className="num">{scoopsText(learning.before.scoops)}{learning.before.median_g ? ` · est. ${grams(learning.before.median_g)}` : ""}</div>
                        </div>
                        <div className="arrow" aria-hidden>→</div>
                        <div>
                          <Plate scoops={learning.after.scoops} size={110} label={`Next time: ${scoopsText(learning.after.scoops)}`} />
                          <div className="small muted">Next first portion</div>
                          <div style={{ fontWeight: 650 }} className="num">{scoopsText(learning.after.scoops)}{learning.after.median_g ? ` · est. ${grams(learning.after.median_g)}` : ""}</div>
                        </div>
                      </div>
                    </div>
                  ) : null}
                  <div className="row">
                    <button className="btn dark" onClick={() => { setState(null); setInput(""); ws.setToken(""); setLearning(null); }}>Next diner</button>
                    <Link to={`/${mode}/learn`} className="btn">See why in Learn</Link>
                  </div>
                </>
              ) : null}

              {status === "returned" && correcting ? (
                <ReturnForm plate={plate} scoopG={scoopG} busy={busy} correcting onCancel={() => setCorrecting(false)}
                  onSubmit={(f) => event({ ...f, supersedes: plate.return_event_id })} />
              ) : null}
            </div>
          ) : null}

          {!state && !notFound && !busy ? (
            <div className="empty">
              <Plate scoops={0} size={120} tone="ghost" label="Empty plate" />
              <p style={{ marginTop: 8 }}>Enter a diner token to see the suggested first portion.</p>
            </div>
          ) : null}
        </div>

        <aside className="card">
          <div className="row between"><h3>This service</h3>{log ? <span className="small muted">{log.total} plates</span> : null}</div>
          {!log ? <Loading label="Loading…" /> : log.plates.length === 0 ? (
            <p className="empty small">Nothing served yet in this service.</p>
          ) : (
            <div className="loglist" style={{ marginTop: 6 }}>
              {log.plates.slice(0, 14).map((p: any) => (
                <button key={p.diner_token + p.food_id} onClick={() => { setInput(p.diner_token); load(p.diner_token); }}>
                  <span className="who"><span className="glyph"><Plate size={28} tone="ink" scoops={p.total_served_g / scoopG} returnedShare={p.returned_g != null ? p.returned_g / Math.max(p.total_served_g, 1) : null} label={`${p.diner_token} plate`} /></span><span><strong>{p.diner_token}</strong> <span className="muted small num">{p.status === "declined" ? "" : grams(p.total_served_g)}</span></span></span>
                  <span className={`chip ${CLASS_LABEL[p.classification]?.tone || "skip"}`}>{p.status === "open" ? "plate out" : p.status === "declined" ? "declined" : `${grams(p.returned_g)} back`}</span>
                </button>
              ))}
            </div>
          )}
        </aside>
      </div>
      {toast.node}
    </div>
  );
}
