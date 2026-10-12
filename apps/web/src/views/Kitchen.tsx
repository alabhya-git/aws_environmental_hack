import { useEffect, useState } from "react";
import { useWs } from "../App";
import { CumulativeChart } from "../charts";
import { grams, kg, pct } from "../text";
import { ErrorNote, Field, KindChip, Kpi, Loading, Seg, useAsync } from "../ui";
import { NeedsSetup, SessionPicker } from "./Serve";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

function NumberField({ label, hint, value, onChange, min, max, step = 1, unit }: { label: string; hint?: string; value: number; onChange: (v: number) => void; min: number; max: number; step?: number; unit?: string }) {
  return (
    <Field label={`${label}${unit ? ` (${unit})` : ""}`} hint={hint}>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input className="input num" type="number" min={min} max={max} step={step} value={value} style={{ maxWidth: 110 }}
          onChange={(e) => { const n = Number(e.target.value); if (!Number.isNaN(n)) onChange(Math.min(max, Math.max(min, n))); }} />
        <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ flex: 1, minWidth: 80, accentColor: "#2f7d4f" }} aria-label={label} />
      </div>
    </Field>
  );
}

export function Kitchen() {
  const ws = useWs();
  const { api, session, sessionId, foodId, calibration, mode } = ws;
  const [form, setForm] = useState<any | null>(null);
  const [policy, setPolicy] = useState<"proofplate" | "conventional">("proofplate");

  useEffect(() => {
    if (!session) return;
    setForm({
      expected_attendance: session.expected_attendance, attendance_cv: session.attendance_cv, lead_minutes: session.lead_minutes,
      batch_g: session.batch_g, stock_cooked_equiv_g: session.stock_cooked_equiv_g, refill_capacity_per_interval: session.refill_capacity_per_interval,
    });
  }, [session?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const args = useDebounced(form, 350);
  const q = useAsync(
    () => (args && sessionId && foodId && calibration ? api("kitchen_plan", { session_id: sessionId, food_id: foodId, policy, ...args }) : Promise.resolve(null)),
    [JSON.stringify(args), sessionId, foodId, policy, calibration?.id],
  );
  if (!ws.boot.foods.length || !calibration || !session) return <NeedsSetup />;
  const set = (k: string) => (v: number) => setForm((f: any) => ({ ...f, [k]: v }));
  const p = q.data;
  const changed = form && (form.expected_attendance !== session.expected_attendance || form.refill_capacity_per_interval !== session.refill_capacity_per_interval || form.lead_minutes !== session.lead_minutes || form.stock_cooked_equiv_g !== session.stock_cooked_equiv_g || form.batch_g !== session.batch_g || form.attendance_cv !== session.attendance_cv);

  return (
    <div className="stack l">
      <div className="row between top">
        <SessionPicker />
        <Seg label="First portions used in this plan" value={policy} onChange={setPolicy}
          options={[["proofplate", "Suggested first portions"], ["conventional", "Usual portion for everyone"]]} />
      </div>

      {form ? (
        <section className="card">
          <div className="row between"><h3>What this service looks like</h3>{changed ? <button className="btn sm ghost" onClick={() => setForm({ ...form, expected_attendance: session.expected_attendance, attendance_cv: session.attendance_cv, lead_minutes: session.lead_minutes, batch_g: session.batch_g, stock_cooked_equiv_g: session.stock_cooked_equiv_g, refill_capacity_per_interval: session.refill_capacity_per_interval })}>Back to the saved values</button> : null}</div>
          <div className="grid cols-3" style={{ marginTop: 12 }}>
            <NumberField label="Expected diners" value={form.expected_attendance} onChange={set("expected_attendance")} min={10} max={600} step={5} />
            <NumberField label="Head-count uncertainty" unit="± %" hint="How far off the count usually is" value={Math.round(form.attendance_cv * 100)} onChange={(v) => set("attendance_cv")(v / 100)} min={0} max={40} />
            <NumberField label="Seconds the station can serve" unit={`per ${session.interval_minutes} min`} value={form.refill_capacity_per_interval} onChange={set("refill_capacity_per_interval")} min={0} max={200} step={5} />
            <NumberField label="Cooking lead time" unit="min" hint="From starting a batch to it being ready" value={form.lead_minutes} onChange={set("lead_minutes")} min={0} max={session.intervals * session.interval_minutes} step={session.interval_minutes} />
            <NumberField label="Batch size" unit="kg cooked" value={form.batch_g / 1000} onChange={(v) => set("batch_g")(Math.round(v * 1000))} min={1} max={20} step={0.5} />
            <NumberField label="Stock on hand" unit="kg cooked equivalent" value={form.stock_cooked_equiv_g / 1000} onChange={(v) => set("stock_cooked_equiv_g")(Math.round(v * 1000))} min={0} max={150} step={5} />
          </div>
        </section>
      ) : null}

      {q.error ? <ErrorNote error={q.error} onRetry={q.reload} /> : !p ? <Loading label="Simulating the service…" /> : (
        <div className="stack l" style={{ opacity: q.stale ? 0.55 : 1 }} aria-busy={q.stale}>
          {p.status === "infeasible" ? (
            <section className="banner stack" role="alert">
              <h2><span aria-hidden>⚠</span> This plan cannot meet its targets as set</h2>
              {p.infeasible_reasons.map((r: any) => (
                <div key={r.code} className="stack s">
                  <p><strong>{r.code === "STOCK" ? "Not enough stock." : r.code === "REFILL_CAPACITY" ? "Seconds would be turned away." : "Lead time."}</strong> {r.message}</p>
                  {r.code === "STOCK" ? <p className="small">With the stock on hand, the modelled chance of running short is <strong>{pct(r.detail.modelled_risk_with_stock)}</strong>, against a target of {pct(p.risk.target_alpha)}.</p> : null}
                  <ul className="plain small">{r.options.map((o: string) => <li key={o}>{o}</li>)}</ul>
                </div>
              ))}
              <p className="small">Nothing was relaxed to make the numbers work. The figures below show what the current settings actually give.</p>
            </section>
          ) : null}

          <section className="panel stack l">
            <div className="row between">
              <h2>Preparation plan</h2>
              <div className="row">
                <KindChip kind="predicted">{mode === "demo" ? "Predicted from simulated history" : "Predicted from measured history"}</KindChip>
                <span className={`chip ${p.status === "feasible" ? "used" : "warn"}`}>{p.status === "feasible" ? "Feasible" : "Not feasible"}</span>
              </div>
            </div>
            <div className="grid cols-4">
              <Kpi xl k="Cook before service" v={kg(p.initial_cook_g, 0)} s={`${p.batches.filter((b: any) => b.trigger === "before_service").length} batches of ${kg(p.assumptions.batch_g)}`} />
              <Kpi xl k="Hold in reserve" v={kg(p.reserve_g, 0)} s={p.reserve_g ? "cooked during service, see schedule" : "nothing to cook during service"} />
              <Kpi k="Chance of running short" v={pct(p.risk.modelled_shortage_risk, 1)} s={`target ${pct(p.risk.target_alpha)} · ±${pct(2 * p.risk.monte_carlo_se, 1)} simulation error${p.risk.near_boundary ? " · close to the limit" : ""}`} />
              <Kpi k="Expected to be served" v={kg(p.forecast.withdrawals_g.p50)} s={`${kg(p.forecast.withdrawals_g.p10)}–${kg(p.forecast.withdrawals_g.p90)} likely range`} />
            </div>
            <div className="grid cols-2">
              <div>
                <h3>Food ready against food needed, through the service</h3>
                <CumulativeChart points={p.forecast.cumulative_by_interval} title="Cumulative cooked food available and forecast withdrawals by minute" />
              </div>
              <div className="stack">
                <h3>Batch schedule</h3>
                <div className="scroll-x">
                  <table className="t">
                    <thead><tr><th>Batch</th><th className="r">Size</th><th>Start</th><th>Ready</th><th>Rule</th></tr></thead>
                    <tbody>
                      {p.batches.map((b: any) => (
                        <tr key={b.index}>
                          <td className="num nw">{b.index}</td>
                          <td className="r nw">{kg(b.grams)}</td>
                          <td className="nw">{b.start_minute == null ? "before service" : `minute ${b.start_minute}`}</td>
                          <td className="nw">{b.ready_minute === 0 ? "at opening" : `minute ${b.ready_minute}`}</td>
                          <td>{b.trigger === "before_service" ? "Always" : b.trigger === "scheduled" ? "Always" : <span>Only if <strong className="num">{kg(b.threshold_g)}</strong> has been served by minute {b.start_minute}</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="small muted">A conditional batch stays uncooked unless early serving runs high. Uncooked reserve is not waste.</p>
              </div>
            </div>
          </section>

          <section className="grid cols-3">
            <div className="card stack">
              <h3>What the forecast expects</h3>
              <table className="t"><tbody>
                <tr><td>Eaten</td><td className="r">{kg(p.forecast.consumed_g.p50)}</td></tr>
                <tr><td>Left on plates</td><td className="r">{kg(p.forecast.plate_leftover_g.mean)}</td></tr>
                <tr><td>Cooked but not served</td><td className="r">{kg(p.forecast.unserved_surplus_g.mean)}</td></tr>
                <tr><td>Requests for seconds</td><td className="r num">{Math.round(p.forecast.refill_requests.mean)}</td></tr>
                <tr><td>Seconds turned away</td><td className="r">{pct(p.forecast.denied_refill_share, 1)}</td></tr>
                <tr><td>Diners attending</td><td className="r num">{Math.round(p.forecast.attendance.p10)}–{Math.round(p.forecast.attendance.p90)}</td></tr>
              </tbody></table>
              <p className="small muted">Served is more than eaten because portions come in half scoops and some food is left. Plans are built on what is served.</p>
            </div>
            <div className="card stack">
              <h3>First portions in this plan</h3>
              <table className="t"><tbody>
                <tr><td>Diners with their own history</td><td className="r num">{p.first_portions.known_diners}</td></tr>
                <tr><td>Their average first portion</td><td className="r">{grams(p.first_portions.known_mean_g)}</td></tr>
                <tr><td>New diners get</td><td className="r">{grams(p.first_portions.cold_start_g)}</td></tr>
                <tr><td>Usual portion here</td><td className="r">{grams(p.first_portions.conventional_g)}</td></tr>
              </tbody></table>
              <h3 style={{ marginTop: 6 }}>Why the reserve is this size</h3>
              <table className="t"><tbody>
                {p.sensitivity.map((s: any) => <tr key={s.label}><td>{s.label}</td><td className="r">{kg(s.withdrawals_p95_g)}</td></tr>)}
              </tbody></table>
              <p className="small muted">95th-percentile need. Treating diners as independent with an exact head-count understates it.</p>
            </div>
            <div className="card stack">
              <div className="row between"><h3>So far in this service</h3><KindChip kind={mode === "demo" ? "simulated" : "observed"}>{mode === "demo" ? "Demo entries" : "Observed"}</KindChip></div>
              <table className="t"><tbody>
                <tr><td>Plates served</td><td className="r num">{p.observed.plates_served}</td></tr>
                <tr><td>Food served</td><td className="r">{kg(p.observed.served_g, 2)}</td></tr>
                <tr><td>Returns recorded</td><td className="r num">{p.observed.returns_recorded} of {p.observed.plates_served}</td></tr>
                <tr><td>Came back on plates</td><td className="r">{kg(p.observed.returned_g, 2)}</td></tr>
                <tr><td>Cooked (entered by kitchen)</td><td className="r">{p.observed.prepared_g == null ? "not entered" : kg(p.observed.prepared_g)}</td></tr>
              </tbody></table>
              <p className="small muted">These are records, kept apart from the forecast. Enter cooked and surplus amounts in Setup when service ends.</p>
            </div>
          </section>

          <details className="card">
            <summary style={{ cursor: "pointer", fontWeight: 650 }}>Assumptions behind this plan</summary>
            <ul className="plain small" style={{ marginTop: 10 }}>
              <li>{p.risk.n_sims.toLocaleString()} simulated services, fixed seed {p.risk.seed}. The same inputs always give the same plan.</li>
              <li>Head-count varies by ±{pct(p.assumptions.attendance_cv)} and each service shares a common appetite effect of ±{pct(p.assumptions.meal_effect_sd)}.</li>
              <li>{pct(p.assumptions.refill_uptake)} of diners who want more walk back for each extra serving.</li>
              <li>Arrivals are front-loaded across {p.assumptions.arrival_profile.length} intervals. Seconds still queued when service ends count as turned away.</li>
              <li>“Chance of running short” is modelled under these assumptions. It is not a guarantee that nobody goes without.</li>
              <li>The app does not make food-safety decisions about surplus. Follow the kitchen's own procedure.</li>
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}
