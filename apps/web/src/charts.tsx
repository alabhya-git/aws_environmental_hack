// Hand-built SVG charts. One y-axis each, thin marks, ink-coloured text, direct
// labels plus a legend when there is more than one series, and a hover readout.
import { MouseEvent, ReactNode, useRef, useState } from "react";
import { CLASS_LABEL, grams, kg } from "./text";

const INK = "#16201a";
const AXIS = "#b3b8ae";
const GRID = "#dcdfd8";
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"];

function useHover(width: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; px: number } | null>(null);
  const onMove = (e: MouseEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    setPos({ x: ((e.clientX - r.left) / r.width) * width, px: e.clientX - r.left });
  };
  return { ref, pos, onMove, onLeave: () => setPos(null) };
}

function Tip({ left, top = 6, children }: { left: number; top?: number; children: ReactNode }) {
  return (
    <div className="tip" style={{ left, top }}>
      {children}
    </div>
  );
}

export interface DensitySeries {
  key: string;
  label: string;
  color: string;
  bins: Array<{ g: number; p: number }>;
  dash?: boolean;
}

/** Posterior predictive distributions of how much a diner would choose to eat. */
export function DensityChart({ series, hiG, markers = [], height = 210, title }: { series: DensitySeries[]; hiG: number; markers?: Array<{ g: number; label: string; color?: string }>; height?: number; title: string }) {
  const W = 640, H = height, L = 10, R = 10, T = 26, B = 30;
  const h = useHover(W);
  const maxP = Math.max(1e-9, ...series.flatMap((s) => s.bins.map((b) => b.p)));
  const x = (g: number) => L + (Math.min(g, hiG) / hiG) * (W - L - R);
  const y = (p: number) => H - B - (p / maxP) * (H - T - B);
  const ticks: number[] = [];
  const stepG = hiG > 700 ? 120 : 60;
  for (let g = 0; g <= hiG + 1; g += stepG) ticks.push(g);
  const hoverG = h.pos ? Math.max(0, Math.min(hiG, ((h.pos.x - L) / (W - L - R)) * hiG)) : null;
  const tail = (s: DensitySeries, g: number) => s.bins.filter((b) => b.g > g).reduce((a, b) => a + b.p, 0);
  return (
    <div className="chart" ref={h.ref} onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke={AXIS} />
        {ticks.map((g) => (
          <g key={g}>
            <line x1={x(g)} x2={x(g)} y1={H - B} y2={H - B + 4} stroke={AXIS} />
            <text x={x(g)} y={H - B + 17} textAnchor="middle">{g}</text>
          </g>
        ))}
        <text x={W - R} y={H - 2} textAnchor="end">grams of this food</text>
        {/* dashed (unselected) series first so the selected one is drawn on top;
            curves can coincide, so identity is carried by the legend, not by labels on the lines */}
        {[...series].sort((a, b) => Number(!!b.dash) - Number(!!a.dash)).map((s) => {
          const d = s.bins.map((b, k) => `${k ? "L" : "M"}${x(b.g).toFixed(1)},${y(b.p).toFixed(1)}`).join(" ");
          return (
            <g key={s.key}>
              {series.length === 1 ? <path d={`${d} L${x(s.bins[s.bins.length - 1].g)},${H - B} L${x(s.bins[0].g)},${H - B} Z`} fill={s.color} opacity="0.12" /> : null}
              <path d={d} fill="none" stroke={s.color} strokeWidth={s.dash ? 2 : 2.5} strokeDasharray={s.dash ? "5 4" : undefined} strokeLinejoin="round" opacity={s.dash ? 0.75 : 1} />
            </g>
          );
        })}
        {markers.map((m) => (
          <g key={m.label}>
            <line x1={x(m.g)} x2={x(m.g)} y1={T - 4} y2={H - B} stroke={m.color || INK} strokeWidth="1.5" strokeDasharray="3 3" />
            <text className="lab" x={x(m.g)} y={T - 10} textAnchor={x(m.g) > W - 90 ? "end" : x(m.g) < 90 ? "start" : "middle"}>{m.label}</text>
          </g>
        ))}
        {hoverG !== null ? <line x1={x(hoverG)} x2={x(hoverG)} y1={T} y2={H - B} stroke={INK} strokeWidth="1" opacity="0.5" /> : null}
      </svg>
      {hoverG !== null && h.pos ? (
        <Tip left={Math.min(Math.max(h.pos.px, 110), (h.ref.current?.clientWidth || 400) - 110)}>
          <strong>Chance of wanting more than {Math.round(hoverG)} g</strong>
          {series.map((s) => (
            <div key={s.key}>
              <i style={{ background: s.color }} />
              {s.label}: {(tail(s, hoverG) * 100).toFixed(0)}%
            </div>
          ))}
        </Tip>
      ) : null}
      {series.length > 1 ? (
        <div className="legend" style={{ marginTop: 6 }}>
          {series.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export interface BarRow {
  label: string;
  value: number | null;
  lo?: number | null;
  hi?: number | null;
  highlight?: boolean;
}

/** One measure, one row per policy. Whiskers are the interval across seeds. */
export function BarRows({ rows, fmt, title }: { rows: BarRow[]; fmt: (v: number) => string; title: string }) {
  const max = Math.max(1e-9, ...rows.map((r) => Math.max(r.value || 0, r.hi || 0))) * 1.28;
  return (
    <div className="barrows" role="img" aria-label={title}>
      {rows.map((r) => {
        const w = ((r.value || 0) / max) * 100;
        return [
          <div key={r.label + "n"} className={`name${r.highlight ? " hi" : ""}`}>{r.label}</div>,
          <div key={r.label + "b"} className="track" title={`${r.label}: ${r.value == null ? "n/a" : fmt(r.value)}${r.lo != null && r.hi != null ? ` (95% interval ${fmt(r.lo)} to ${fmt(r.hi)})` : ""}`}>
            <div className={`bar${r.highlight ? " hi" : ""}`} style={{ width: `${w}%` }} />
            {r.lo != null && r.hi != null ? <div className="whisk" style={{ left: `${(Math.max(r.lo, 0) / max) * 100}%`, width: `${((r.hi - Math.max(r.lo, 0)) / max) * 100}%` }} /> : null}
            <span className="val" style={{ left: `${Math.max(w, ((r.hi || 0) / max) * 100)}%` }}>{r.value == null ? "n/a" : fmt(r.value)}</span>
          </div>,
        ];
      })}
    </div>
  );
}

/** Kitchen: forecast cumulative withdrawals against cooked food available. */
export function CumulativeChart({ points, title }: { points: Array<{ minute: number; p50_g: number; p95_g: number; available_g: number }>; title: string }) {
  const W = 640, H = 240, L = 46, R = 96, T = 14, B = 30;
  const h = useHover(W);
  if (!points.length) return null;
  const pts = [{ minute: 0, p50_g: 0, p95_g: 0, available_g: points[0].available_g }, ...points];
  const maxM = pts[pts.length - 1].minute;
  const maxG = Math.max(...pts.map((p) => Math.max(p.p95_g, p.available_g))) * 1.08;
  const x = (m: number) => L + (m / maxM) * (W - L - R);
  const y = (g: number) => H - B - (g / maxG) * (H - T - B);
  const line = (k: "p50_g" | "p95_g") => pts.map((p, i) => `${i ? "L" : "M"}${x(p.minute).toFixed(1)},${y(p[k]).toFixed(1)}`).join(" ");
  // food becomes available at the START of an interval and stays until the next batch
  let step = `M${x(0)},${y(points[0].available_g)}`;
  points.forEach((p, i) => {
    const from = i === 0 ? 0 : points[i - 1].minute;
    step += ` L${x(from)},${y(p.available_g)} L${x(p.minute)},${y(p.available_g)}`;
  });
  const yt = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxG);
  const idx = h.pos ? Math.max(1, Math.min(pts.length - 1, Math.round(((h.pos.x - L) / (W - L - R)) * (pts.length - 1)))) : null;
  const last = pts[pts.length - 1];
  return (
    <div className="chart" ref={h.ref} onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {yt.map((g) => (
          <g key={g}>
            <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} stroke={g === 0 ? AXIS : GRID} />
            <text x={L - 6} y={y(g) + 4} textAnchor="end">{(g / 1000).toFixed(0)} kg</text>
          </g>
        ))}
        {pts.map((p) => (
          <text key={p.minute} x={x(p.minute)} y={H - B + 17} textAnchor="middle">{p.minute}</text>
        ))}
        <text x={L} y={H - 1}>minutes into service</text>
        <path d={step} fill="none" stroke={INK} strokeWidth="2" />
        <path d={line("p95_g")} fill="none" stroke={SERIES[0]} strokeWidth="2" strokeDasharray="5 4" />
        <path d={line("p50_g")} fill="none" stroke={SERIES[0]} strokeWidth="2" />
        {(() => {
          // end-of-line labels, pushed apart so they never overlap
          const items = [
            { t: "cooked, ready", y: y(last.available_g) + 4 },
            { t: "needed, 95th", y: y(last.p95_g) + 4 },
            { t: "needed, typical", y: y(last.p50_g) + 4 },
          ].sort((a, b) => a.y - b.y);
          for (let i = 1; i < items.length; i++) items[i].y = Math.max(items[i].y, items[i - 1].y + 15);
          return items.map((it) => <text key={it.t} className="lab" x={W - R + 6} y={it.y}>{it.t}</text>);
        })()}
        {idx !== null ? (
          <g>
            <line x1={x(pts[idx].minute)} x2={x(pts[idx].minute)} y1={T} y2={H - B} stroke={INK} opacity="0.4" />
            <circle cx={x(pts[idx].minute)} cy={y(pts[idx].p50_g)} r="4.5" fill={SERIES[0]} stroke="#fcfcfb" strokeWidth="2" />
            <circle cx={x(pts[idx].minute)} cy={y(pts[idx].p95_g)} r="4.5" fill={SERIES[0]} stroke="#fcfcfb" strokeWidth="2" />
          </g>
        ) : null}
      </svg>
      {idx !== null && h.pos ? (
        <Tip left={Math.min(Math.max(h.pos.px, 110), (h.ref.current?.clientWidth || 400) - 110)}>
          <strong>By minute {pts[idx].minute}</strong>
          <div><i style={{ background: INK }} />cooked and ready: {kg(pts[idx].available_g)}</div>
          <div><i style={{ background: SERIES[0] }} />needed, typical: {kg(pts[idx].p50_g)}</div>
          <div><i style={{ background: SERIES[0] }} />needed, 95th percentile: {kg(pts[idx].p95_g)}</div>
        </Tip>
      ) : null}
      <div className="legend" style={{ marginTop: 6 }}>
        <span><i style={{ background: INK }} />Cooked food ready (plan)</span>
        <span><i style={{ background: SERIES[0] }} />Forecast withdrawals, typical</span>
        <span><i style={{ background: `repeating-linear-gradient(90deg, ${SERIES[0]} 0 4px, transparent 4px 7px)` }} />Forecast, 95th percentile</span>
      </div>
    </div>
  );
}

export interface FrontierPoint {
  x: number; // share of plates where the diner left wanting more
  y: number; // grams left per plate
  label: string;
  emphasis?: boolean;
}

/** Plate waste against diners left wanting more. Down and left is better on both. */
export function FrontierChart({ fixed, proofplate, naive, title }: { fixed: FrontierPoint[]; proofplate: FrontierPoint[]; naive: FrontierPoint; title: string }) {
  const W = 640, H = 330, L = 50, R = 20, T = 18, B = 44;
  const [hover, setHover] = useState<{ p: FrontierPoint; series: string; color: string } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const all = [...fixed, ...proofplate, naive];
  const maxX = Math.ceil((Math.max(...all.map((p) => p.x)) * 1.12) * 20) / 20;
  const maxY = Math.ceil((Math.max(...all.map((p) => p.y)) * 1.12) / 10) * 10;
  const x = (v: number) => L + (v / maxX) * (W - L - R);
  const y = (v: number) => H - B - (v / maxY) * (H - T - B);
  const path = (pts: FrontierPoint[]) => [...pts].sort((a, b) => a.x - b.x).map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const xt = Array.from({ length: Math.round(maxX / 0.1) + 1 }, (_, i) => i * 0.1).filter((v) => v <= maxX + 1e-9);
  const yt = Array.from({ length: Math.round(maxY / 10) + 1 }, (_, i) => i * 10);
  const dot = (p: FrontierPoint, series: string, color: string, shape: "circle" | "square" | "diamond") => {
    const cx = x(p.x), cy = y(p.y), r = p.emphasis ? 7 : 5;
    const common = { fill: color, stroke: "#fcfcfb", strokeWidth: 2 };
    return (
      <g key={series + p.label} onMouseEnter={() => setHover({ p, series, color })} onMouseLeave={() => setHover(null)}>
        <circle cx={cx} cy={cy} r="14" fill="transparent" />
        {shape === "circle" ? <circle cx={cx} cy={cy} r={r} {...common} /> : shape === "square" ? <rect x={cx - r} y={cy - r} width={2 * r} height={2 * r} rx="1.5" {...common} /> : <path d={`M${cx},${cy - r - 1} L${cx + r + 1},${cy} L${cx},${cy + r + 1} L${cx - r - 1},${cy} Z`} {...common} />}
      </g>
    );
  };
  const def = proofplate.find((p) => p.emphasis);
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {yt.map((v) => (
          <g key={"y" + v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={v === 0 ? AXIS : GRID} />
            <text x={L - 6} y={y(v) + 4} textAnchor="end">{v} g</text>
          </g>
        ))}
        {xt.map((v) => (
          <text key={"x" + v} x={x(v)} y={H - B + 17} textAnchor="middle">{Math.round(v * 100)}%</text>
        ))}
        <text x={(L + W - R) / 2} y={H - 6} textAnchor="middle">share of plates where the diner left still wanting more</text>
        <text x={12} y={T + 2} transform={`rotate(-90 12 ${T + 2})`} textAnchor="end">food left per plate</text>
        <text className="lab" x={L + 8} y={H - B - 8}>better ↙</text>
        <path d={path(fixed)} fill="none" stroke={SERIES[0]} strokeWidth="2" />
        <path d={path(proofplate)} fill="none" stroke={SERIES[2]} strokeWidth="2" />
        {fixed.map((p) => dot(p, "Fixed portion", SERIES[0], "square"))}
        {proofplate.map((p) => dot(p, "ProofPlate", SERIES[2], "circle"))}
        {dot(naive, "Personal average, no censoring", SERIES[1], "diamond")}
        {fixed.map((p) => <text key={"fl" + p.label} className={p.emphasis ? "lab" : undefined} x={x(p.x) + 9} y={y(p.y) - 7}>{p.label}</text>)}
        {def ? <text className="lab" x={x(def.x) - 10} y={y(def.y) + 20} textAnchor="end">ProofPlate, app default</text> : null}
        <text x={x(naive.x) + 4} y={y(naive.y) + 26}>personal average</text>
      </svg>
      {hover ? (
        <Tip left={Math.min(Math.max((x(hover.p.x) / W) * (ref.current?.clientWidth || 400), 130), (ref.current?.clientWidth || 400) - 130)} top={(y(hover.p.y) / H) * (ref.current?.clientHeight || 300) - 34}>
          <strong><i style={{ background: hover.color }} />{hover.series}: {hover.p.label}</strong>
          <div>{hover.p.y.toFixed(1)} g left per plate</div>
          <div>{(hover.p.x * 100).toFixed(1)}% left wanting more</div>
        </Tip>
      ) : null}
      <div className="legend" style={{ marginTop: 6 }}>
        <span><i style={{ background: SERIES[0] }} />■ Same fixed portion for everyone</span>
        <span><i style={{ background: SERIES[2] }} />● ProofPlate (by cost given to a trip for seconds)</span>
        <span><i style={{ background: SERIES[1] }} />◆ Personal average, no censoring</span>
      </div>
    </div>
  );
}

export interface HistoryStep {
  service_date: string | null;
  total_served_g: number;
  returned_g: number | null;
  consumed_g: number | null;
  classification: string;
  used_for_learning: boolean;
  satisfaction: string | null;
  after: { median_g: number; p10_g: number; p90_g: number; scoops: number; basis: string };
}

/** How each plate moved this diner's estimate. Band = 10th to 90th percentile. */
export function TrajectoryChart({ steps, scoopG, title }: { steps: HistoryStep[]; scoopG: number; title: string }) {
  const W = 640, H = 230, L = 46, R = 14, T = 14, B = 30;
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  if (!steps.length) return null;
  const maxG = Math.max(...steps.map((s) => Math.max(s.after.p90_g, s.total_served_g, s.after.scoops * scoopG))) * 1.1;
  const x = (i: number) => (steps.length === 1 ? (L + W - R) / 2 : L + (i / (steps.length - 1)) * (W - L - R));
  const y = (g: number) => H - B - (g / maxG) * (H - T - B);
  const band = steps.map((s, i) => `${i ? "L" : "M"}${x(i)},${y(s.after.p90_g)}`).join(" ") + " " + steps.map((_, i) => `L${x(steps.length - 1 - i)},${y(steps[steps.length - 1 - i].after.p10_g)}`).join(" ") + " Z";
  const med = steps.map((s, i) => `${i ? "L" : "M"}${x(i)},${y(s.after.median_g)}`).join(" ");
  const yt = [0, 0.5, 1].map((f) => Math.round((f * maxG) / 20) * 20);
  const hs = hover !== null ? steps[hover] : null;
  return (
    <div className="chart" ref={ref} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {yt.map((g) => (
          <g key={g}>
            <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} stroke={g === 0 ? AXIS : GRID} />
            <text x={L - 6} y={y(g) + 4} textAnchor="end">{g} g</text>
          </g>
        ))}
        <path d={band} fill="#e9b949" opacity="0.28" />
        <path d={med} fill="none" stroke="#1f6b43" strokeWidth="2" />
        {steps.map((s, i) => {
          const c = s.consumed_g;
          const cx = x(i);
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={cx - 16} y={T} width="32" height={H - T - B} fill="transparent" />
              <rect x={cx - 9} y={y(s.after.scoops * scoopG) - 1.5} width="18" height="3" rx="1.5" fill={INK} />
              {c === null ? null : s.classification === "right_censored" ? (
                <path d={`M${cx},${y(c) - 7} L${cx + 6},${y(c) + 4} L${cx - 6},${y(c) + 4} Z`} fill="#eb6834" stroke="#fcfcfb" strokeWidth="2" />
              ) : (
                <circle cx={cx} cy={y(c)} r="5" fill={s.used_for_learning ? "#1f6b43" : "#fcfcfb"} stroke={s.used_for_learning ? "#fcfcfb" : "#898781"} strokeWidth="2" />
              )}
              <text x={cx} y={H - B + 17} textAnchor="middle">{i + 1}</text>
            </g>
          );
        })}
        <text x={L} y={H - 1}>plate number (oldest first)</text>
      </svg>
      {hs && hover !== null ? (
        <Tip left={Math.min(Math.max((x(hover) / W) * (ref.current?.clientWidth || 400), 120), (ref.current?.clientWidth || 400) - 120)}>
          <strong>Plate {hover + 1}{hs.service_date ? ` · ${hs.service_date}` : ""}</strong>
          <div>Served {grams(hs.total_served_g)}, came back {grams(hs.returned_g)}</div>
          <div>{CLASS_LABEL[hs.classification]?.short || hs.classification}</div>
          <div>Estimate after: {grams(hs.after.median_g)} ({grams(hs.after.p10_g)}–{grams(hs.after.p90_g)})</div>
        </Tip>
      ) : null}
      <div className="legend" style={{ marginTop: 6 }}>
        <span><i style={{ background: "#1f6b43" }} />Estimated typical amount</span>
        <span><i style={{ background: "#e9b949", height: 8, opacity: 0.5 }} />10th–90th percentile</span>
        <span><i style={{ background: INK }} />Suggested first portion</span>
        <span>● eaten, used</span>
        <span>▲ wanted more (at least this)</span>
        <span>○ not used</span>
      </div>
    </div>
  );
}
