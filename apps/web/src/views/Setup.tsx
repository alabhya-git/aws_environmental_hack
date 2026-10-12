import { ChangeEvent, FormEvent, useMemo, useState } from "react";
import { download, OpArgs, OpName } from "../api";
import { useWs } from "../App";
import { kg, scoopsText } from "../text";
import { ErrorNote, Field, useToast } from "../ui";

const TEMPLATE = "service_date,session_name,diner_token,initial_g,seconds_g,returned_g,satisfaction,leftover_reason,serve_measurement,return_measurement,losses_g\n";
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

function useAction() {
  const ws = useWs();
  const toast = useToast();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const run = async <O extends OpName>(op: O, args: OpArgs[O], done: string): Promise<any | null> => {
    setBusy(true);
    setError(null);
    try {
      const out = await ws.api(op, args);
      await ws.reload();
      toast.show(done);
      return out;
    } catch (e) {
      setError(e);
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { run, error, busy, toast, setError };
}

function Calibration() {
  const ws = useWs();
  const a = useAction();
  const [name, setName] = useState("");
  const [recipe, setRecipe] = useState("v1");
  const [foodId, setFoodId] = useState(ws.foodId || "");
  const [raw, setRaw] = useState("");
  const [scoopId, setScoopId] = useState("ladle-A");
  const [res, setRes] = useState(1);
  const weights = useMemo(() => raw.split(/[\s,;]+/).filter(Boolean).map(Number).filter((n) => Number.isFinite(n) && n > 0).map(Math.round), [raw]);
  const mean = weights.length ? weights.reduce((x, y) => x + y, 0) / weights.length : 0;
  const sd = weights.length > 1 ? Math.sqrt(weights.reduce((s, w) => s + (w - mean) ** 2, 0) / (weights.length - 1)) : 0;
  const addFood = async (e: FormEvent) => {
    e.preventDefault();
    const out = await a.run("create_food", { name, recipe_version: recipe }, "Food added");
    if (out) { setFoodId(out.id); ws.setFoodId(out.id); setName(""); }
  };
  const calibrate = async (e: FormEvent) => {
    e.preventDefault();
    const out = await a.run("create_calibration", { food_id: foodId || ws.boot.foods[0]?.id, scoop_id: scoopId, weights_g: weights, scale_resolution_g: res }, "Calibration saved as a new version");
    if (out) setRaw("");
  };
  return (
    <section className="card stack l">
      <h2>Foods and scoop calibration</h2>
      <p className="muted">Tare the container, then weigh about twenty representative scoops of the cooked dish. Recalibrate whenever the scoop, recipe or consistency changes. Each calibration is kept as its own version; old plates keep the one they were served with.</p>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Food</th><th>Recipe</th><th>Scoop</th><th className="r">Mean</th><th className="r">Spread (SD)</th><th className="r">Scoops weighed</th><th>Version</th><th>Source</th></tr></thead>
          <tbody>
            {ws.boot.foods.length === 0 ? <tr><td colSpan={8} className="muted">No foods yet. Add the first one below.</td></tr> : null}
            {ws.boot.foods.map((f) => {
              const c = ws.boot.calibrations.find((x) => x.food_id === f.id);
              return (
                <tr key={f.id}>
                  <td><strong>{f.name}</strong></td><td>{f.recipe_version}</td>
                  {c ? <><td>{c.scoop_id}</td><td className="r">{c.mean_g} g</td><td className="r">{c.sd_g} g</td><td className="r">{c.n}</td><td>v{c.cal_version}</td><td>{c.origin}{c.measurement === "generated" ? " (generated)" : " (weighed)"}</td></> : <td colSpan={6}><span className="chip warn">not calibrated</span></td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid cols-2">
        <form className="stack" onSubmit={addFood}>
          <h3>Add a food or a new recipe version</h3>
          <div className="row top">
            <Field label="Food name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Dal tadka" required maxLength={60} /></Field>
            <Field label="Recipe version"><input className="input" value={recipe} onChange={(e) => setRecipe(e.target.value)} style={{ maxWidth: 110 }} required /></Field>
          </div>
          <div><button className="btn" disabled={a.busy || !name.trim()}>Add food</button></div>
          <p className="small muted">Weigh one food at a time. Do not infer a dish's weight from the total of a mixed plate.</p>
        </form>
        <form className="stack" onSubmit={calibrate}>
          <h3>Record a scoop calibration</h3>
          <div className="row top">
            <Field label="Food">
              <select className="input" value={foodId || ws.boot.foods[0]?.id || ""} onChange={(e) => setFoodId(e.target.value)}>
                {ws.boot.foods.map((f) => <option key={f.id} value={f.id}>{f.name} · {f.recipe_version}</option>)}
              </select>
            </Field>
            <Field label="Scoop label"><input className="input" value={scoopId} onChange={(e) => setScoopId(e.target.value.replace(/[^A-Za-z0-9_.:\-]/g, ""))} style={{ maxWidth: 130 }} /></Field>
            <Field label="Scale reads to (g)"><input className="input num" type="number" min={1} max={50} value={res} onChange={(e) => setRes(Number(e.target.value) || 1)} style={{ maxWidth: 90 }} /></Field>
          </div>
          <Field label="Scoop weights in grams" hint="One per scoop, separated by spaces, commas or new lines. At least 5; about 20 is better.">
            <textarea className="input" value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="58 61 60 63 59 62 60 57 61 60 …" />
          </Field>
          <div className="row">
            <button className="btn primary" disabled={a.busy || weights.length < 5 || !ws.boot.foods.length}>Save calibration</button>
            <span className="num muted">{weights.length ? `${weights.length} scoops · mean ${mean.toFixed(1)} g · SD ${sd.toFixed(1)} g` : "nothing entered yet"}</span>
          </div>
        </form>
      </div>
      <ErrorNote error={a.error} />
      {a.toast.node}
    </section>
  );
}

function Sessions() {
  const ws = useWs();
  const a = useAction();
  const [name, setName] = useState("Lunch");
  const [date, setDate] = useState(today());
  const [att, setAtt] = useState(120);
  const [usual, setUsual] = useState(3);
  const [secs, setSecs] = useState(true);
  const [menu, setMenu] = useState<string[] | null>(null);
  const [closing, setClosing] = useState<string | null>(null);
  // default menu: the most recently added foods that have a calibrated scoop (a service holds at most 12 foods)
  const calibrated = ws.boot.foods.filter((f) => ws.boot.calibrations.some((c) => c.food_id === f.id));
  const newest = (list: any[]) => [...list].sort((x, y) => String(y.created_at || "").localeCompare(String(x.created_at || ""))).slice(0, 12).map((f) => f.id as string);
  const onMenu = menu ?? newest(calibrated.length ? calibrated : ws.boot.foods);
  const [prepared, setPrepared] = useState("");
  const [surplus, setSurplus] = useState("");
  const [dest, setDest] = useState("unknown");
  const create = async (e: FormEvent) => {
    e.preventDefault();
    const out = await a.run("create_session", { name, service_date: date, food_ids: onMenu, expected_attendance: att, conventional_scoops: usual, seconds_accessible: secs }, "Meal service created");
    if (out) { ws.setSessionId(out.id); setMenu(null); }
  };
  const close = async (id: string) => {
    const patch: OpArgs["update_session"] = { session_id: id, status: "closed" };
    if (prepared) patch.prepared_g = Math.round(Number(prepared) * 1000);
    if (surplus) { patch.kitchen_surplus_g = Math.round(Number(surplus) * 1000); patch.surplus_destination = dest as OpArgs["update_session"]["surplus_destination"]; }
    if (await a.run("update_session", patch, "Service closed. Plates without a return now count as missing.")) { setClosing(null); setPrepared(""); setSurplus(""); }
  };
  return (
    <section className="card stack l">
      <h2>Meal services</h2>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Date</th><th>Name</th><th>Status</th><th className="r">Expected diners</th><th className="r">Cooked</th><th className="r">Surplus</th><th>Surplus went to</th><th /></tr></thead>
          <tbody>
            {ws.boot.sessions.length === 0 ? <tr><td colSpan={8} className="muted">No services yet.</td></tr> : null}
            {ws.boot.sessions.slice(0, 12).map((s) => (
              <tr key={s.id}>
                <td>{s.service_date}</td><td>{s.name}</td>
                <td><span className={`chip ${s.status === "open" ? "open" : "skip"}`}>{s.status}</span></td>
                <td className="r">{s.expected_attendance}</td>
                <td className="r">{s.prepared_g == null ? "–" : kg(s.prepared_g)}</td>
                <td className="r">{s.kitchen_surplus_g == null ? "–" : kg(s.kitchen_surplus_g)}</td>
                <td>{s.surplus_destination || "–"}</td>
                <td>
                  {s.status === "open" ? <button className="btn sm" onClick={() => setClosing(s.id)}>Close…</button> : <button className="btn sm ghost" disabled={a.busy} onClick={() => a.run("update_session", { session_id: s.id, status: "open" }, "Service reopened")}>Reopen</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {closing ? (
        <div className="note stack">
          <strong>Close {closing}</strong>
          <div className="row top">
            <Field label="Cooked in total (kg)"><input className="input num" type="number" min={0} step={0.1} value={prepared} onChange={(e) => setPrepared(e.target.value)} style={{ maxWidth: 130 }} /></Field>
            <Field label="Cooked but not served (kg)"><input className="input num" type="number" min={0} step={0.1} value={surplus} onChange={(e) => setSurplus(e.target.value)} style={{ maxWidth: 130 }} /></Field>
            <Field label="What happened to it" hint="Follow your own food-safety procedure. Kept or donated food is not waste.">
              <select className="input" value={dest} onChange={(e) => setDest(e.target.value)}>
                <option value="retained">Kept for later use</option><option value="donated">Donated</option><option value="discarded">Discarded</option><option value="unknown">Not recorded</option>
              </select>
            </Field>
          </div>
          <div className="row"><button className="btn dark" disabled={a.busy} onClick={() => close(closing)}>Close service</button><button className="btn ghost" onClick={() => setClosing(null)}>Cancel</button></div>
        </div>
      ) : null}
      <form className="stack" onSubmit={create}>
        <h3>New meal service</h3>
        <div className="row top">
          <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} style={{ maxWidth: 160 }} /></Field>
          <Field label="Date"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></Field>
          <Field label="Expected diners"><input className="input num" type="number" min={1} max={5000} value={att} onChange={(e) => setAtt(Number(e.target.value))} style={{ maxWidth: 110 }} /></Field>
          <Field label="Usual first serving"><select className="input" value={usual} onChange={(e) => setUsual(Number(e.target.value))}>{[1, 1.5, 2, 2.5, 3, 3.5, 4, 5].map((v) => <option key={v} value={v}>{scoopsText(v)}</option>)}</select></Field>
          <label className="row" style={{ minHeight: 44, marginTop: 20 }}><input type="checkbox" checked={secs} onChange={(e) => setSecs(e.target.checked)} /> Seconds are easy to get</label>
        </div>
        {ws.boot.foods.length ? (
          <div className="field">
            <span>On the menu</span>
            <div className="chips">
              {ws.boot.foods.map((f) => {
                const on = onMenu.includes(f.id);
                return (
                  <button type="button" key={f.id} aria-pressed={on} disabled={!on && onMenu.length >= 12}
                    onClick={() => setMenu(on ? onMenu.filter((x) => x !== f.id) : [...onMenu, f.id])}>
                    {f.name} · {f.recipe_version}
                  </button>
                );
              })}
            </div>
            <small>Foods served at this service. Only these can be recorded in it.</small>
          </div>
        ) : null}
        <div><button className="btn primary" disabled={a.busy || !onMenu.length}>Create service</button>{!ws.boot.foods.length ? <span className="muted small"> Add a food first.</span> : !onMenu.length ? <span className="muted small"> Put at least one food on the menu.</span> : null}</div>
        <p className="small muted">Batch size, lead time, stock and refill capacity start at defaults and can be adjusted on the Kitchen page.</p>
      </form>
      <ErrorNote error={a.error} />
      {a.toast.node}
    </section>
  );
}

function Diners() {
  const ws = useWs();
  const a = useAction();
  const [token, setToken] = useState("");
  const [consent, setConsent] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (await a.run("create_diner", { ...(token ? { token } : {}), consent }, "Token registered")) { setToken(""); setConsent(false); }
  };
  const exportOne = async (t: string) => {
    try {
      const out = await ws.api("export_diner", { token: t });
      download(`proofplate-${t}.json`, JSON.stringify(out, null, 1), "application/json");
    } catch (e) { a.setError(e); }
  };
  return (
    <section className="card stack l">
      <h2>Diner tokens</h2>
      <p className="muted">A token is an opaque code on a card. No name, photo, body measurement or health detail is stored. Deleting a token removes its plates; every later estimate is rebuilt without them.</p>
      <form className="row top" onSubmit={add}>
        <Field label="Token" hint="Leave empty to generate one"><input className="input" value={token} onChange={(e) => setToken(e.target.value.toUpperCase().replace(/[^A-Z0-9_.:\-]/g, "").slice(0, 24))} placeholder="T-501" style={{ maxWidth: 170 }} /></Field>
        <label className="row" style={{ minHeight: 44, marginTop: 20 }}><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> Diner agreed to take part (18+)</label>
        <button className="btn primary" disabled={!consent || a.busy} style={{ marginTop: 20 }}>Register token</button>
      </form>
      <div className="scroll-x" style={{ maxHeight: 360, overflowY: "auto" }}>
        <table className="t">
          <thead><tr><th>Token</th><th>Suggestions</th><th>Standing request</th><th>Source</th><th /></tr></thead>
          <tbody>
            {ws.boot.diners.length === 0 ? <tr><td colSpan={5} className="muted">No tokens registered.</td></tr> : null}
            {ws.boot.diners.map((d) => (
              <tr key={d.token}>
                <td><strong>{d.token}</strong></td>
                <td>
                  <label className="row"><input type="checkbox" checked={!d.personalization_opt_out} disabled={a.busy}
                    onChange={(e) => a.run("update_diner", { token: d.token, personalization_opt_out: !e.target.checked, portion_preference_scoops: d.portion_preference_scoops }, e.target.checked ? "Personal suggestions on" : "Opted out: usual serving from now on")} /> personal</label>
                </td>
                <td>
                  <select className="input" style={{ minHeight: 36, maxWidth: 150 }} value={d.portion_preference_scoops ?? ""} disabled={a.busy}
                    onChange={(e) => a.run("update_diner", { token: d.token, personalization_opt_out: d.personalization_opt_out, portion_preference_scoops: e.target.value ? Number(e.target.value) : null }, "Standing request saved")}>
                    <option value="">none</option>
                    {[1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6].map((v) => <option key={v} value={v}>always {scoopsText(v)}</option>)}
                  </select>
                </td>
                <td>{d.origin}</td>
                <td className="row" style={{ justifyContent: "flex-end" }}>
                  <button className="btn sm ghost" onClick={() => exportOne(d.token)}>Export</button>
                  {confirm === d.token ? (
                    <><button className="btn sm danger" disabled={a.busy} onClick={async () => { await a.run("delete_diner", { token: d.token }, `Deleted ${d.token} and its plates`); setConfirm(null); if (ws.token === d.token) ws.setToken(""); }}>Delete for good</button><button className="btn sm ghost" onClick={() => setConfirm(null)}>Keep</button></>
                  ) : <button className="btn sm ghost danger" onClick={() => setConfirm(d.token)}>Delete…</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorNote error={a.error} />
      {a.toast.node}
    </section>
  );
}

function ImportData() {
  const ws = useWs();
  const a = useAction();
  const [csv, setCsv] = useState("");
  const [preview, setPreview] = useState<any | null>(null);
  const [m, setM] = useState({ source_name: "", license: "", collection_method: "", measurement_status: "measured" as "measured" | "synthetic", what_it_measures: "" });
  const manifest = { ...m, food_id: ws.foodId || ws.boot.foods[0]?.id };
  const ready = csv.trim() && m.source_name.length > 1 && m.license.length > 1 && m.collection_method.length > 1 && m.what_it_measures.length > 1 && manifest.food_id;
  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) { setCsv(await f.text()); setPreview(null); }
  };
  const doPreview = async () => {
    a.setError(null);
    try { setPreview(await ws.api("import_preview", { csv_text: csv, manifest })); } catch (e) { a.setError(e); setPreview(null); }
  };
  const commit = async () => {
    const out = await a.run("import_commit", { csv_text: csv, manifest }, "Import finished");
    if (out) { setPreview(null); setCsv(""); a.toast.show(`Imported ${out.events_created} events (${out.duplicates_skipped} already present)`); }
  };
  const set = (k: keyof typeof m) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => { setM({ ...m, [k]: e.target.value } as typeof m); setPreview(null); };
  return (
    <section className="card stack l">
      <div className="row between">
        <h2>Import plates recorded elsewhere</h2>
        <button className="btn sm" onClick={() => download("proofplate-import-template.csv", TEMPLATE, "text/csv")}>Download empty template</button>
      </div>
      <p className="muted">For paper sheets or an offline tablet. Every imported row keeps the label “imported” with the status you declare below. Synthetic files are stored for comparison but never train the live model, and nothing imported is ever relabelled as a pilot measurement. Re-importing the same file adds nothing.</p>
      <div className="grid cols-2">
        <div className="stack">
          <Field label="Where the file came from"><input className="input" value={m.source_name} onChange={set("source_name")} placeholder="Hostel 4 lunch sheets, week 1" /></Field>
          <Field label="How it was collected"><input className="input" value={m.collection_method} onChange={set("collection_method")} placeholder="Kitchen scale, plates weighed before and after" /></Field>
          <Field label="What it measures"><input className="input" value={m.what_it_measures} onChange={set("what_it_measures")} placeholder="Cooked rice served and returned, per diner token" /></Field>
          <div className="row top">
            <Field label="License or permission"><input className="input" value={m.license} onChange={set("license")} placeholder="Own data" /></Field>
            <Field label="These records are">
              <select className="input" value={m.measurement_status} onChange={set("measurement_status")}>
                <option value="measured">really measured</option><option value="synthetic">synthetic or made up</option>
              </select>
            </Field>
          </div>
        </div>
        <div className="stack">
          <Field label="CSV file"><input className="input" type="file" accept=".csv,text/csv" onChange={onFile} /></Field>
          <Field label="or paste CSV text"><textarea className="input" value={csv} onChange={(e) => { setCsv(e.target.value); setPreview(null); }} placeholder={TEMPLATE + "2026-10-12,Lunch,T-501,180,0,35,enough,enough_food,weighed,weighed,0"} /></Field>
        </div>
      </div>
      <div className="row">
        <button className="btn" disabled={!ready || a.busy} onClick={doPreview}>Check the file</button>
        <button className="btn primary" disabled={!preview?.can_commit || a.busy} onClick={commit}>Import {preview?.can_commit ? `${preview.will_create.plates} plates` : ""}</button>
        {!ready ? <span className="muted small">Fill in all four description fields and add a CSV.</span> : null}
      </div>
      {preview ? (
        <div className={`note ${preview.can_commit ? "leaf" : "bad"} stack`}>
          <strong>{preview.can_commit ? `Ready: ${preview.valid_rows} of ${preview.rows} rows are valid.` : `${preview.error_count} problem${preview.error_count === 1 ? "" : "s"} found. Nothing has been imported.`}</strong>
          <span>Will be stored as <strong>{preview.label.origin}</strong>, status <strong>{preview.label.measurement_status}</strong>. {preview.label.used_for_live_model ? "These plates will train the live model." : "These plates will not train the live model."}</span>
          <span className="small">{preview.will_create.plates} plates · {preview.will_create.events} events · {preview.will_create.sessions} services · {preview.will_create.diners} tokens · file hash {preview.sha256.slice(0, 12)}</span>
          {preview.errors.length ? (
            <table className="t"><thead><tr><th>Line</th><th>Column</th><th>Problem</th></tr></thead>
              <tbody>{preview.errors.slice(0, 12).map((e: any, i: number) => <tr key={i}><td className="num">{e.row}</td><td>{e.field}</td><td>{e.message}</td></tr>)}</tbody></table>
          ) : null}
        </div>
      ) : null}
      <ErrorNote error={a.error} />
      {a.toast.node}
    </section>
  );
}

function ServingStyle() {
  const ws = useWs();
  const a = useAction();
  const st = ws.boot.settings;
  const options: Array<[number, string, string]> = [
    [8, "Lean", "smaller first portions, more trips for seconds"],
    [20, "Balanced", "the default"],
    [40, "Generous", "larger first portions, fewer trips for seconds"],
  ];
  return (
    <div className="stack">
      <h3>How much weight to give a trip back for seconds</h3>
      <div className="chips">
        {options.map(([v, label, hint]) => (
          <button key={v} type="button" aria-pressed={st.refill_friction_g === v} disabled={a.busy} title={hint}
            onClick={() => a.run("update_settings", { refill_friction_g: v }, `Serving style set to ${label.toLowerCase()}`)}>
            {label} · {v} g
          </button>
        ))}
      </div>
      <p className="small muted">
        The suggestion balances food left on the plate against trips back for seconds. This sets the cost given to one trip, in grams of leftover.
        The Evidence page shows, in simulation, where each setting lands on waste and on diners left wanting more.
      </p>
      <ErrorNote error={a.error} />
      {a.toast.node}
    </div>
  );
}

export function Setup() {
  const ws = useWs();
  const st = ws.boot.settings;
  return (
    <div className="stack l">
      {ws.mode === "demo" ? (
        <div className="note amber"><strong>Demo sandbox.</strong> Anything you add here is stored only in this browser and labelled simulated. “Reset demo” removes it. It cannot reach pilot records.</div>
      ) : ws.boot.role !== "manager" ? (
        <div className="note">You are signed in as an operator. Adding foods, calibrations, services, imports and deletions needs the kitchen-manager role.</div>
      ) : null}
      <Calibration />
      <Sessions />
      <Diners />
      <ImportData />
      <section className="card stack">
        <h2>Assumptions in force</h2>
        <p className="muted">Product defaults, not health standards. They are recorded with every suggestion.</p>
        <ServingStyle />
        <div className="scroll-x">
          <table className="t"><tbody>
            <tr><td>Portions offered</td><td>{scoopsText(st.min_scoops)} to {scoopsText(st.max_scoops)}, in steps of {scoopsText(st.step_scoops)}</td></tr>
            <tr><td>One serving of seconds</td><td>{scoopsText(st.refill_scoops)}</td></tr>
            <tr><td>Cost given to one trip for seconds</td><td>{st.refill_friction_g} g-equivalent of leftover</td></tr>
            <tr><td>Largest change from the last serving</td><td>{scoopsText(st.max_change_scoops)} per meal</td></tr>
            <tr><td>Chance of needing two trips for seconds</td><td>kept at or under {(st.max_p_two_trips * 100).toFixed(0)}%</td></tr>
            <tr><td>Starting guess for a typical amount</td><td>{st.prior_median_g} g, deliberately broad</td></tr>
            <tr><td>Kitchen shortage target</td><td>{(st.alpha * 100).toFixed(0)}% modelled chance of running short</td></tr>
            <tr><td>Model / policy version</td><td><code>{ws.boot.model_version}</code> · <code>{ws.boot.policy_version}</code></td></tr>
          </tbody></table>
        </div>
      </section>
    </div>
  );
}
