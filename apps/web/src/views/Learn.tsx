import { useState } from "react";
import { useWs } from "../App";
import { DensityChart, SERIES, TrajectoryChart } from "../charts";
import { Plate } from "../Plate";
import { BASIS, CLASS_LABEL, grams, REASONS, SATISFACTION, scoopsText } from "../text";
import { ErrorNote, Field, KindChip, Kpi, Loading, Seg, Stepper, useAsync } from "../ui";
import { NeedsSetup } from "./Serve";

type Branch = "unknown" | "enough" | "wanted_more";
const BRANCH_COLOR: Record<Branch, string> = { unknown: SERIES[0], wanted_more: SERIES[1], enough: SERIES[2] };

function WhatIf({ token, foodId, scoopG }: { token: string; foodId: string; scoopG: number }) {
  const { api, mode } = useWs();
  const [scoops, setScoops] = useState(3);
  const [repeats, setRepeats] = useState(3);
  const [pick, setPick] = useState<Branch>("unknown");
  const plateG = Math.round(scoops * scoopG);
  const q = useAsync(() => api("whatif", { token, food_id: foodId, plate_g: plateG, repeats }), [token, foodId, plateG, repeats]);
  const w = q.data;
  return (
    <section className="panel stack l">
      <div className="row between top">
        <div>
          <h2>Same empty plate, different evidence</h2>
          <p className="muted" style={{ maxWidth: "46em" }}>
            Suppose {token} is served this much and the plate comes back empty, {repeats === 1 ? "once" : `${repeats} meals in a row`}. The scale reads the same in all three cases.
            What the diner says decides what the model may conclude. These are live results from the same model that runs Serve.
          </p>
        </div>
        <div className="row top">
          <Field label="Plate served and finished">
            <Stepper value={scoops} onChange={setScoops} min={1} max={6} step={0.5} format={(v) => `${scoopsText(v)} · ${Math.round(v * scoopG)} g`} />
          </Field>
          <Field label="Meals in a row">
            <Stepper value={repeats} onChange={setRepeats} min={1} max={6} step={1} format={(v) => `${v} ×`} />
          </Field>
        </div>
      </div>
      {q.error ? <ErrorNote error={q.error} onRetry={q.reload} /> : !w ? <Loading label="Running the model three ways…" /> : (
        <>
          <Seg<Branch> label="What the diner said" wide value={pick} onChange={setPick}
            options={[["unknown", "Nobody asked"], ["enough", "“That was enough”"], ["wanted_more", "“I wanted more”"]]} />
          <div className="grid cols-2" style={{ alignItems: "center", opacity: q.stale ? 0.55 : 1 }}>
            <div className="stack">
              <div className={`note ${pick === "unknown" ? "" : "leaf"}`}>
                <strong>{w.branches[pick].used_for_learning ? "Used for learning. " : "Not used for learning. "}</strong>
                {w.branches[pick].note}
              </div>
              <div className="row" style={{ gap: 28 }}>
                <Kpi k="Estimate after this plate" v={grams(w.branches[pick].predictive.median_g)}
                  s={<span style={{ color: "var(--amber)" }}>{grams(w.branches[pick].predictive.p10_g)}–{grams(w.branches[pick].predictive.p90_g)}</span>} />
                <Kpi k="Next first portion" v={scoopsText(w.branches[pick].scoops)}
                  s={w.branches[pick].scoops !== w.branches[pick].scoops_without_change_limit ? `model prefers ${scoopsText(w.branches[pick].scoops_without_change_limit)}; limited to one scoop per meal` : BASIS[w.branches[pick].basis]} />
              </div>
              <div className="note amber">
                <strong>{w.naive.label}</strong> would serve <strong className="num">{scoopsText(w.naive.scoops)}</strong> next time
                ({grams(w.naive.next_g)}). {w.naive.note}
              </div>
            </div>
            <div>
              <DensityChart
                title="How much this diner would choose to eat, under each reading of the empty plate"
                hiG={Math.max(...(["unknown", "enough", "wanted_more"] as Branch[]).map((b) => w.branches[b].density.hi_g))}
                series={(["unknown", "enough", "wanted_more"] as Branch[]).map((b) => ({
                  key: b, label: b === "unknown" ? "Nobody asked" : b === "enough" ? "Enough" : "Wanted more",
                  color: BRANCH_COLOR[b], bins: w.branches[b].density.bins, dash: b !== pick,
                }))}
                markers={[{ g: plateG, label: `plate: ${plateG} g` }]}
              />
              <p className="small muted" style={{ marginTop: 6 }}>
                <KindChip kind={mode === "demo" ? "simulated" : "predicted"}>{mode === "demo" ? "Predicted from simulated history" : "Predicted from measured history"}</KindChip>{" "}
                Solid line is the selected reading. {w.history_eligible} earlier plates count as evidence for this diner.
              </p>
            </div>
          </div>
          <div className="grid cols-3">
            {(["unknown", "enough", "wanted_more"] as Branch[]).map((b) => (
              <button key={b} className="card flat tight" onClick={() => setPick(b)} aria-pressed={pick === b}
                style={{ textAlign: "center", cursor: "pointer", borderColor: pick === b ? "var(--ink)" : undefined, borderWidth: pick === b ? 2 : 1 }}>
                <Plate scoops={w.branches[b].scoops} size={104} tone={pick === b ? "leaf" : "ghost"} label={`${w.branches[b].label}: next ${scoopsText(w.branches[b].scoops)}`} />
                <div style={{ fontWeight: 650 }}>{w.branches[b].label}</div>
                <div className="num">next: {scoopsText(w.branches[b].scoops)} · est. {grams(w.branches[b].predictive.median_g)}</div>
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export function Learn() {
  const ws = useWs();
  const { api, foodId, boot, calibration, mode } = ws;
  const token = ws.token || boot.diners[0]?.token || "";
  const q = useAsync(() => (token && foodId && calibration ? api("learn", { token, food_id: foodId }) : Promise.resolve(null)), [token, foodId, calibration?.id]);
  if (!boot.foods.length || !calibration) return <NeedsSetup />;
  if (!boot.diners.length) return <div className="card empty">No diner tokens are registered yet. Register one from Serve.</div>;
  const d = q.data;
  const kind = mode === "demo" ? "simulated" : "observed";
  return (
    <div className="stack l">
      <div className="row between top">
        <div className="row">
          <Field label="Diner token">
            <select className="input" value={token} onChange={(e) => ws.setToken(e.target.value)}>
              {boot.diners.map((x) => <option key={x.token} value={x.token}>{x.token}</option>)}
            </select>
          </Field>
          <Field label="Food">
            <select className="input" value={foodId || ""} onChange={(e) => ws.setFoodId(e.target.value)}>
              {boot.foods.map((f) => <option key={f.id} value={f.id}>{f.name} · recipe {f.recipe_version}</option>)}
            </select>
          </Field>
        </div>
        <p className="small muted" style={{ maxWidth: "34em" }}>
          Estimates describe one diner's choices for one recipe. There is no ranking of diners and nothing here judges a person.
        </p>
      </div>

      {q.error ? <ErrorNote error={q.error} onRetry={q.reload} /> : !d ? <Loading label="Replaying this diner's plates…" /> : (
        <>
          <section className="card stack l" style={{ opacity: q.stale ? 0.55 : 1 }}>
            <div className="row between">
              <h2>What the model believes about {d.diner.token}</h2>
              <KindChip kind={kind}>{mode === "demo" ? "Simulated history" : "Measured history"}</KindChip>
            </div>
            <div className="grid cols-4">
              <Kpi k="Usually chooses to eat" v={d.counts.eligible ? grams(d.predictive.median_g) : "no evidence yet"}
                s={d.counts.eligible ? <span style={{ color: "var(--amber)" }}>{grams(d.predictive.p10_g)}–{grams(d.predictive.p90_g)} on 8 meals in 10</span> : "showing the starting assumption"} />
              <Kpi k="Next first portion" v={scoopsText(d.recommendation.scoops)} s={`${grams(d.recommendation.grams)} · ${BASIS[d.recommendation.basis]}`} />
              <Kpi k="Plates used as evidence" v={`${d.counts.eligible} of ${d.counts.plates}`}
                s={`${d.predictive.n_interval} “about this much”, ${d.predictive.n_censored} “at least this much”`} />
              <Kpi k="A plain average would say" v={d.naive_average_g == null ? "–" : grams(d.naive_average_g)} s="treats every plate as exact" />
            </div>
            <ul className="plain small">
              {d.recommendation.reasons.map((r: string) => <li key={r}>{REASONS[r] || r}</li>)}
              <li>Starting assumption: typical amount {grams(d.prior.median_g)} ({d.prior.source.replace(/:.*/, "").replace(/-/g, " ")}). Scoop: {d.calibration.mean_g} g, calibration v{d.calibration.version}.</li>
            </ul>
            {d.history.length ? (
              <div className="grid cols-2">
                <div>
                  <h3>How each plate moved the estimate</h3>
                  <TrajectoryChart steps={d.history} scoopG={d.calibration.mean_g} title={`Estimate for ${d.diner.token} after each plate`} />
                </div>
                <div>
                  <h3>Where the next meal's appetite is likely to fall</h3>
                  <DensityChart title="Predictive distribution of the amount this diner would choose to eat" hiG={d.density.hi_g}
                    series={[{ key: "now", label: "Current estimate", color: "#1f6b43", bins: d.density.bins }]}
                    markers={[{ g: d.recommendation.grams, label: `first portion ${d.recommendation.grams} g` }]} />
                  <p className="small muted" style={{ marginTop: 6 }}>Hover to read the chance of wanting more than any amount. The spread includes meal-to-meal variation, not only uncertainty about the average.</p>
                </div>
              </div>
            ) : <p className="empty">No plates recorded for this diner and food yet.</p>}
          </section>

          {d.history.length ? (
            <section className="card stack">
              <h2>Plate by plate</h2>
              <div className="scroll-x">
                <table className="t">
                  <thead><tr><th>#</th><th>Date</th><th className="r">Served</th><th className="r">Came back</th><th className="r">Eaten</th><th>Said</th><th>How it was used</th><th className="r">Estimate after</th><th className="r">Portion after</th></tr></thead>
                  <tbody>
                    {d.history.map((h: any, i: number) => (
                      <tr key={i}>
                        <td className="num"><span className="row" style={{ gap: 8, flexWrap: "nowrap" }}><Plate size={26} tone="ink" scoops={h.total_served_g / d.calibration.mean_g} returnedShare={h.returned_g != null ? h.returned_g / Math.max(h.total_served_g, 1) : null} label={`Plate ${i + 1}`} />{i + 1}</span></td>
                        <td>{h.service_date || "–"}</td>
                        <td className="r">{grams(h.total_served_g)}</td>
                        <td className="r">{grams(h.returned_g)}</td>
                        <td className="r">{h.consumed_g == null ? "unknown" : grams(h.consumed_g)}</td>
                        <td>{h.satisfaction ? SATISFACTION[h.satisfaction] : "–"}{h.leftover_reason && !["none", "enough_food"].includes(h.leftover_reason) ? ` · ${h.leftover_reason}` : ""}</td>
                        <td><span className={`chip ${h.used_for_learning ? "used" : CLASS_LABEL[h.classification]?.tone || "skip"}`} style={{ whiteSpace: "normal" }}>{CLASS_LABEL[h.classification]?.short || h.classification}{!h.used_for_learning && h.eligible ? " (kept out of this model: different data source)" : ""}</span></td>
                        <td className="r">{grams(h.after.median_g)}</td>
                        <td className="r">{scoopsText(h.after.scoops)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="plain small muted">{d.limitations.map((x: string) => <li key={x}>{x}</li>)}</ul>
            </section>
          ) : null}

          <WhatIf token={d.diner.token} foodId={d.food_id} scoopG={d.calibration.mean_g} />
        </>
      )}
    </div>
  );
}
