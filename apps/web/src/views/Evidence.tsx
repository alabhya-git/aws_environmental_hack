import { useState } from "react";
import { download, getJson } from "../api";
import { useWs } from "../App";
import { BarRows, FrontierChart } from "../charts";
import { CLASS_LABEL, grams, kg, pct } from "../text";
import { ErrorNote, Field, Kind, KindChip, Kpi, Loading, Seg, useAsync, useToast } from "../ui";

type Tab = Kind;

function Scope({ g }: { g: any }) {
  return (
    <p className="small muted">
      {g.food_id} · {g.scope.sessions} services{g.scope.date_from ? `, ${g.scope.date_from} to ${g.scope.date_to}` : ""} · {g.scope.diners} diner tokens ·
      source: {g.origin}{g.measurement_status ? ` (${g.measurement_status})` : ""}
    </p>
  );
}

function GroupCard({ g }: { g: any }) {
  const coverage = g.plates.return_coverage;
  const excluded = Object.entries<number>(g.classification).filter(([k]) => !["interval", "right_censored"].includes(k));
  return (
    <section className="card stack l">
      <div className="row between">
        <h2>{g.kind === "observed" ? "Recorded plates" : "Generated plates"}</h2>
        <KindChip kind={g.kind}>{g.kind === "observed" ? "Observed · declared by operators" : `Simulated · ${g.origin}`}</KindChip>
      </div>
      <Scope g={g} />
      <div className="grid cols-4">
        <Kpi k="Edible food left per returned plate" v={g.plate_waste_per_returned_plate_g.value == null ? "–" : grams(g.plate_waste_per_returned_plate_g.value)} s={`n = ${g.plate_waste_per_returned_plate_g.n} returned plates`} />
        <Kpi k="Returns recorded" v={pct(coverage)} s={`${g.plates.returned} of ${g.plates.served} plates; ${g.plates.missing_or_open} missing or still out`} />
        <Kpi k="Said they wanted more" v={pct(g.wanted_more.of ? g.wanted_more.n / g.wanted_more.of : null)} s={`${g.wanted_more.n} of ${g.wanted_more.of} returned plates`} />
        <Kpi k="Served a different portion than suggested" v={g.overrides.of ? pct(g.overrides.n / g.overrides.of) : "–"} s={g.overrides.of ? `${g.overrides.n} of ${g.overrides.of} plates with a suggestion` : "no suggestions recorded"} />
      </div>
      <div className="grid cols-2">
        <div className="stack">
          <h3>Mass balance</h3>
          <table className="t"><tbody>
            <tr><td>Served (first portions and seconds)</td><td className="r">{kg(g.grams.served, 2)}</td></tr>
            <tr><td>Came back as edible leftovers</td><td className="r">{kg(g.grams.returned_edible, 2)}</td></tr>
            <tr><td>Eaten, on plates with exact accounting</td><td className="r">{kg(g.grams.consumed_exact, 2)} <span className="muted">· {g.grams.plates_with_exact_consumption} plates</span></td></tr>
            <tr><td>Plates with seconds</td><td className="r num">{g.seconds.plates_with_seconds}</td></tr>
            <tr><td>Asked for seconds, none available</td><td className="r num">{g.seconds.unavailable || 0}</td></tr>
            <tr><td>Cooked (entered per service)</td><td className="r">{g.kitchen.prepared_g ? kg(g.kitchen.prepared_g) : "not entered"}</td></tr>
            {Object.entries<number>(g.kitchen.surplus_by_destination_g).map(([dest, v]) => (
              <tr key={dest}><td>Kitchen surplus, {dest}</td><td className="r">{kg(v)}</td></tr>
            ))}
          </tbody></table>
          <p className="small muted">{g.kitchen.note} Plate waste and kitchen surplus are counted separately and never added twice.</p>
        </div>
        <div className="stack">
          <h3>What the model was allowed to learn from</h3>
          <table className="t"><tbody>
            <tr><td><span className="chip used">used</span> Enough: “about this much”</td><td className="r num">{g.learning.interval}</td></tr>
            <tr><td><span className="chip used">used</span> Wanted more: “at least this much”</td><td className="r num">{g.learning.right_censored}</td></tr>
            {excluded.map(([k, v]) => (
              <tr key={k}><td><span className="chip skip">not used</span> {CLASS_LABEL[k]?.short.replace(/ → .*/, "") || k}</td><td className="r num">{v}</td></tr>
            ))}
          </tbody></table>
          <p className="small muted">Leftovers caused by dislike, interruption or sharing are counted as waste but do not lower anyone's appetite estimate.</p>
        </div>
      </div>
    </section>
  );
}

function PredictionCard({ g }: { g: any }) {
  const c = g.prediction_check;
  return (
    <section className="card stack">
      <div className="row between">
        <h2>Did earlier plates predict the next one?</h2>
        <KindChip kind="predicted">Predicted vs {g.kind === "observed" ? "observed" : "simulated"} plates</KindChip>
      </div>
      <Scope g={g} />
      {c.n_uncensored + c.n_censored === 0 ? <p className="empty">Not enough repeat plates yet. A diner needs at least two usable plates before one can be predicted from the other.</p> : (
        <>
          <div className="grid cols-4">
            <Kpi k="Typical miss" v={c.mae_g == null ? "–" : grams(c.mae_g)} s={`mean absolute error, n = ${c.n_uncensored} plates with a known amount eaten`} />
            <Kpi k="Inside the stated range" v={pct(c.coverage_80)} s="share of plates inside the 10th–90th percentile range; 80% is the aim" />
            <Kpi k="Width of that range" v={c.mean_interval_width_g == null ? "–" : grams(c.mean_interval_width_g)} s="narrower is more useful, if coverage holds" />
            <Kpi k="“Wanted more” plates" v={c.censored_mean_log_score == null ? "–" : c.censored_mean_log_score.toFixed(2)} s={`mean log score, n = ${c.n_censored}; closer to 0 is better`} />
          </div>
          <p className="small muted">Method: {c.method}. Error is only computed where the amount eaten is known. A diner who wanted more has an unknown true appetite, so those plates are scored by the probability the model gave to “more than this”, not by an error in grams.</p>
        </>
      )}
    </section>
  );
}

function Frontier({ ev }: { ev: any }) {
  const [world, setWorld] = useState("baseline");
  const f = ev.frontier?.worlds?.[world];
  if (!f) return null;
  const eq = f.equal_availability;
  const usual = f.fixed.find((p: any) => p.scoops === 3);
  const def = f.proofplate.find((p: any) => p.friction_g === ev.frontier.default_friction_g);
  return (
    <section className="card stack l">
      <div className="row between top">
        <div>
          <h2>Does personalising beat simply changing everyone's portion?</h2>
          <p className="muted" style={{ maxWidth: "50em" }}>
            Any kitchen can cut plate waste by serving everyone less, at the price of more diners left wanting more. The question is whether
            ProofPlate gets a better trade than that. Each square is one fixed portion for everyone; each circle is ProofPlate at one setting.
          </p>
        </div>
        <KindChip kind="simulated">Simulated · not measured savings</KindChip>
      </div>
      <Seg label="Simulated world" value={world} onChange={setWorld} options={[["baseline", "Seconds are easy"], ["seconds_friction", "Only half walk back for seconds"]]} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))", gap: 22, alignItems: "center" }}>
        <FrontierChart
          title="Food left per plate against share of diners left wanting more, for fixed portions and for ProofPlate"
          fixed={f.fixed.map((p: any) => ({ x: p.left_hungry_rate, y: p.plate_waste_g_per_plate, label: p.scoops === 3 ? "3 scoops (usual)" : `${p.scoops} scoops`, emphasis: p.scoops === 3 }))}
          proofplate={f.proofplate.map((p: any) => ({ x: p.left_hungry_rate, y: p.plate_waste_g_per_plate, label: `trip cost ${p.friction_g} g`, emphasis: p.friction_g === ev.frontier.default_friction_g }))}
          naive={{ x: f.naive_average.left_hungry_rate, y: f.naive_average.plate_waste_g_per_plate, label: "personal average" }}
        />
        <div className="stack l">
          <div className="grid cols-2">
            <Kpi k="Usual portion, 3 scoops" v={`${usual.plate_waste_g_per_plate.toFixed(1)} g`} s={`left per plate · ${pct(usual.left_hungry_rate, 1)} left wanting more`} />
            <Kpi k="ProofPlate, app default" v={`${def.plate_waste_g_per_plate.toFixed(1)} g`} s={`left per plate · ${pct(def.left_hungry_rate, 1)} left wanting more`} />
          </div>
          {eq.within_fixed_range ? (
            <div className="note leaf">
              <strong>Like for like.</strong> To leave as few diners wanting more as ProofPlate does ({pct(eq.proofplate_left_wanting_more, 1)}), a fixed portion
              would leave about <strong>{eq.fixed_waste_g_at_same_rate.toFixed(1)} g</strong> per plate. ProofPlate left <strong>{eq.proofplate_waste_g.toFixed(1)} g</strong>.
              {eq.proofplate_waste_g < eq.fixed_waste_g_at_same_rate ? "" : " In this world the fixed portion does at least as well."}
            </div>
          ) : <div className="note">ProofPlate's default point lies outside the range the fixed portions reach here, so no like-for-like figure is given.</div>}
          <p className="small">
            At its default, ProofPlate leaves about as much on the plate as the usual portion and roughly halves the diners left wanting more.
            Serving everyone two scoops leaves the least food, and the most diners still hungry.
          </p>
        </div>
      </div>
      <details>
        <summary style={{ cursor: "pointer", fontWeight: 650 }}>Show every point as a table</summary>
          <div className="scroll-x" style={{ marginTop: 10 }}>
            <table className="t">
              <thead><tr><th>Policy</th><th>Setting</th><th className="r">Left per plate</th><th className="r">Left wanting more</th><th className="r">Trips for seconds</th></tr></thead>
              <tbody>
                {f.fixed.map((p: any) => <tr key={"f" + p.scoops}><td>Fixed portion</td><td>{p.scoops} scoops</td><td className="r">{p.plate_waste_g_per_plate.toFixed(1)} g</td><td className="r">{pct(p.left_hungry_rate, 1)}</td><td className="r">{p.refill_trips_per_plate.toFixed(2)}</td></tr>)}
                {f.proofplate.map((p: any) => <tr key={"p" + p.friction_g} style={p.friction_g === ev.frontier.default_friction_g ? { fontWeight: 650 } : undefined}><td>ProofPlate</td><td>trip cost {p.friction_g} g</td><td className="r">{p.plate_waste_g_per_plate.toFixed(1)} g</td><td className="r">{pct(p.left_hungry_rate, 1)}</td><td className="r">{p.refill_trips_per_plate.toFixed(2)}</td></tr>)}
                <tr><td>Personal average</td><td>no censoring</td><td className="r">{f.naive_average.plate_waste_g_per_plate.toFixed(1)} g</td><td className="r">{pct(f.naive_average.left_hungry_rate, 1)}</td><td className="r">{f.naive_average.refill_trips_per_plate.toFixed(2)}</td></tr>
              </tbody>
            </table>
          </div>
      </details>
      <p className="small muted">
        Means over {ev.frontier.seeds.length} seeds on paired worlds; final third of meals. {eq.note} “Left wanting more” is the simulator's hidden truth.
        This is a property of the simulator, not a field result.
      </p>
    </section>
  );
}

function Simulation() {
  const q = useAsync(() => getJson<any>("/api/evaluation"), []);
  const [scenario, setScenario] = useState("baseline");
  const [table, setTable] = useState(false);
  if (q.error) return <div className="card"><ErrorNote error={q.error} onRetry={q.reload} /></div>;
  if (!q.data) return <Loading label="Loading the evaluation artifact…" />;
  const ev = q.data;
  const sc = ev.scenarios.find((s: any) => s.name === scenario) || ev.scenarios[0];
  const short: Record<string, string> = { conventional: "Usual portion (3 scoops)", small_plus_seconds: "Small portion + seconds", cohort: "Cohort history", naive_average: "Personal average, no censoring", proofplate: "ProofPlate" };
  const rows = (key: string) => ev.policies.map((p: any) => {
    const m = sc.policies[p.key][key];
    return { label: short[p.key] || p.key, value: m.mean, lo: m.lo, hi: m.hi, highlight: p.key === "proofplate" };
  });
  return (
    <>
    <Frontier ev={ev} />
    <section className="card stack l">
      <div className="row between top">
        <div>
          <h2>Serving policies compared in a simulator</h2>
          <p className="muted" style={{ maxWidth: "48em" }}>{ev.what_this_is} Every policy faces the same diners, the same attendance and the same appetites, draw for draw.</p>
        </div>
        <KindChip kind="simulated">Simulated · not measured savings</KindChip>
      </div>
      <div className="row between top">
        <Field label="Scenario">
          <select className="input" value={sc.name} onChange={(e) => setScenario(e.target.value)}>
            {ev.scenarios.map((s: any) => <option key={s.name} value={s.name}>{s.name.replace(/_/g, " ")}</option>)}
          </select>
        </Field>
        <p className="small" style={{ maxWidth: "38em", flex: 1 }}>{sc.description}</p>
        <Seg label="View" value={table ? "table" : "chart"} onChange={(v) => setTable(v === "table")} options={[["chart", "Charts"], ["table", "Table"]]} />
      </div>
      {table ? (
        <div className="scroll-x">
          <table className="t">
            <thead><tr><th>Policy</th>{ev.metrics.map((m: any) => <th key={m.key} className="r">{m.label}</th>)}</tr></thead>
            <tbody>
              {ev.policies.map((p: any) => (
                <tr key={p.key} style={p.key === "proofplate" ? { fontWeight: 650 } : undefined}>
                  <td>{short[p.key]}</td>
                  {ev.metrics.map((m: any) => {
                    const v = sc.policies[p.key][m.key];
                    const isRate = m.key.endsWith("_rate");
                    return <td key={m.key} className="r">{isRate ? pct(v.mean, 1) : v.mean.toFixed(m.key.includes("trips") ? 2 : 1)}{v.lo != null ? <span className="muted small"> ({isRate ? pct(v.lo, 0) : v.lo.toFixed(0)}–{isRate ? pct(v.hi, 0) : v.hi.toFixed(0)})</span> : null}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="grid cols-2">
          <div className="stack s"><h3>Food left on the plate, grams per plate</h3><BarRows title="Plate waste per plate by policy" rows={rows("plate_waste_g_per_plate")} fmt={(v) => `${v.toFixed(1)} g`} /></div>
          <div className="stack s"><h3>Diners who left still wanting more</h3><BarRows title="Share of plates where the diner left wanting more, by policy" rows={rows("left_hungry_rate")} fmt={(v) => pct(v, 1)} /></div>
          <div className="stack s"><h3>Trips back for seconds, per plate</h3><BarRows title="Refill trips per plate by policy" rows={rows("refill_trips_per_plate")} fmt={(v) => v.toFixed(2)} /></div>
          <div className="stack s"><h3>Cooked but not served, kg per meal</h3><BarRows title="Kitchen surplus per meal by policy" rows={rows("kitchen_surplus_g_per_meal")} fmt={(v) => kg(v)} /></div>
        </div>
      )}
      <p className="small muted">
        Bars are means over {ev.seeds.length} seeds; the thin line is the 95% interval across seeds. Measured on the final third of {sc.meals} meals with {sc.diners} diners,
        after each learner has only seen the earlier meals. “Left still wanting more” uses the simulator's hidden appetite, which no real kitchen can see.
        Lower is better on all four.
      </p>
      <div className="grid cols-2">
        <div className="note">
          <strong>ProofPlate's predictions in this scenario.</strong> Typical miss {grams(sc.prediction.mae_g.mean)} on {sc.prediction.n_uncensored} plates;
          the 10th–90th range held {pct(sc.prediction.coverage_80.mean)} of them (aim 80%), mean width {grams(sc.prediction.mean_interval_width_g.mean)}.
        </div>
        <div className="note amber">
          <strong>Read this as a simulation.</strong>
          <ul className="plain" style={{ marginTop: 4 }}>{ev.limitations.slice(0, 3).map((x: string) => <li key={x}>{x}</li>)}</ul>
        </div>
      </div>
      <p className="small muted">
        {ev.tuning} Model <code>{ev.model_version}</code>, policy <code>{ev.policy_version}</code>, seeds {ev.seeds.join(", ")}, generated {ev.generated_at.slice(0, 10)}.
        Reproduce with <code>python -m evaluation.run_evaluation</code>; artifact hash <code>{ev.sha256.slice(0, 12)}</code>.
      </p>
    </section>
    </>
  );
}

export function Evidence() {
  const ws = useWs();
  const { api, mode, foodId } = ws;
  const [tab, setTab] = useState<Tab>(mode === "demo" ? "simulated" : "observed");
  const q = useAsync(() => api("evidence", foodId ? { food_id: foodId } : {}), [foodId]);
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const exportFile = async (what: "events" | "participations", format: "json" | "csv") => {
    setBusy(true);
    try {
      let cursor: number | null = 0;
      let parts: string[] = [];
      let name = "";
      while (cursor !== null) {
        const out: any = await api("export", { what, format, cursor });
        name = out.filename;
        parts.push(format === "csv" && parts.length ? out.content.split("\n").slice(1).join("\n") : out.content);
        cursor = out.next_cursor;
      }
      download(name, parts.join(format === "csv" ? "" : "\n"), format === "csv" ? "text/csv" : "application/json");
      toast.show(`Exported ${name}`);
    } catch (e) {
      toast.show((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const groups: any[] = q.data?.groups || [];
  const observed = groups.filter((g) => g.kind === "observed");
  const simulated = groups.filter((g) => g.kind === "simulated");
  return (
    <div className="stack l">
      <div className="row between top">
        <Seg<Tab> label="Kind of evidence" value={tab} onChange={setTab}
          options={[["observed", "Observed"], ["predicted", "Predicted"], ["simulated", "Simulated"]]} />
        <div className="row noprint">
          <button className="btn sm" disabled={busy} onClick={() => exportFile("participations", "csv")}>Export plates (CSV)</button>
          <button className="btn sm" disabled={busy} onClick={() => exportFile("events", "json")}>Export events (JSON)</button>
          <button className="btn sm" onClick={() => window.print()}>Print report</button>
        </div>
      </div>
      <p className="muted small" style={{ maxWidth: "62em" }}>
        <strong>Observed</strong> is what operators recorded. <strong>Predicted</strong> is what the model said before it saw the outcome.
        <strong> Simulated</strong> is generated data and policy comparisons. They are never mixed, and an outcome of a policy that was not actually used is only ever shown as simulated.
      </p>

      {q.error ? <ErrorNote error={q.error} onRetry={q.reload} /> : !q.data ? <Loading label="Adding up the records…" /> : (
        <>
          {tab === "observed" ? (observed.length ? observed.map((g) => <GroupCard key={g.food_id + g.origin + g.measurement_status} g={g} />) : (
            <div className="card empty">
              <h2>No observed records {mode === "demo" ? "in the demo" : "yet"}</h2>
              <p style={{ marginTop: 8, maxWidth: "40em", marginInline: "auto" }}>
                {mode === "demo"
                  ? "The demo holds generated data only, so there is nothing measured to report here. No real kitchen has been measured for this project yet; field results are unproven until a pilot is run."
                  : "This pilot workspace is empty until operators record plates in Serve. Nothing is pre-filled."}
              </p>
            </div>
          )) : null}

          {tab === "predicted" ? (groups.length ? groups.map((g) => <PredictionCard key={g.food_id + g.origin + g.measurement_status} g={g} />) : <div className="card empty">No plates recorded yet, so there is nothing to check predictions against.</div>) : null}

          {tab === "simulated" ? (
            <>
              <Simulation />
              {simulated.map((g) => <GroupCard key={g.food_id + g.origin + g.measurement_status} g={g} />)}
            </>
          ) : null}

          <ul className="plain small muted">{q.data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
        </>
      )}
      {toast.node}
    </div>
  );
}
