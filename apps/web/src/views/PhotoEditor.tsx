// Check and correct the outline of the plate and the food in one photo.
// The first outline is automatic; every tool here is for fixing it by hand.
import { PointerEvent as RPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { colourStats, composeMask, Ellipse, ellipseMask, ellipseRho, Frame, Mask, plateShare, Point } from "../photo";
import { addFood, analyse, analyseByHand, Analysis, canResize, excludeHere, PlateColours, plateHere, reguess, resizeLast, ringMask, unionMask } from "../photoAuto";
import { embed, forget, loadModel, modelState, ModelState, onModelState } from "../sam";

export interface PhotoResult {
  share: number; // food as a share of the plate's area
  thumb: string; // small JPEG with the outline drawn on, kept on this device
  colours: PlateColours;
  edited: boolean;
  engine: "model" | "by-hand";
}

type Tool = "add" | "remove" | "brush" | "erase" | "plate";
type Phase = "reading" | "model" | "looking" | "ready" | "failed";
type Drag = { kind: "paint"; last: Point } | { kind: "tap"; at: Point } | { kind: "move"; dx: number; dy: number } | { kind: "a" } | { kind: "b" };
interface Snapshot { a: Analysis; add: Mask; erase: Mask }

const MAX_SIDE = 512;
const TOOLS: Array<[Tool, string]> = [["add", "Add food"], ["remove", "Not food"], ["brush", "Brush"], ["erase", "Eraser"], ["plate", "Plate"]];
const HINT: Record<Tool, string> = {
  add: "Tap any food the outline missed.",
  remove: "Tap anything outlined that is not food.",
  brush: "Paint over food by hand.",
  erase: "Rub out outline that is not food.",
  plate: "Drag the dashed outline onto the plate rim and use the two handles to resize it. Tapping the plate finds it again.",
};

function stamp(mask: Mask, other: Mask, W: number, H: number, p: Point, r: number): void {
  const x0 = Math.max(0, Math.floor(p.x - r)), x1 = Math.min(W - 1, Math.ceil(p.x + r));
  const y0 = Math.max(0, Math.floor(p.y - r)), y1 = Math.min(H - 1, Math.ceil(p.y + r));
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    if ((x - p.x) ** 2 + (y - p.y) ** 2 <= r * r) { mask[y * W + x] = 1; other[y * W + x] = 0; }
  }
}

function handles(e: Ellipse): { ha: Point; hb: Point } {
  const c = Math.cos(e.theta), s = Math.sin(e.theta);
  return { ha: { x: e.cx + e.a * c, y: e.cy + e.a * s }, hb: { x: e.cx - e.b * s, y: e.cy + e.b * c } };
}

/** Draw the photo with the food tinted amber and the plate rim dashed. Decorations are optional so thumbnails stay clean. */
function paintScene(g: CanvasRenderingContext2D, f: Frame, s: HTMLCanvasElement, final: Mask | null, a: Analysis | null, opts: { handles: boolean; points: boolean }): void {
  const W = f.width, H = f.height;
  g.drawImage(s, 0, 0);
  if (final) {
    const img = g.getImageData(0, 0, W, H);
    const d = img.data;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = y * W + x;
      if (!final[i]) continue;
      const edge = x === 0 || y === 0 || x === W - 1 || y === H - 1 || !final[i - 1] || !final[i + 1] || !final[i - W] || !final[i + W];
      const m = edge ? 1 : 0.45;
      d[i * 4] = d[i * 4] * (1 - m) + 240 * m;
      d[i * 4 + 1] = d[i * 4 + 1] * (1 - m) + 176 * m;
      d[i * 4 + 2] = d[i * 4 + 2] * (1 - m) + 40 * m;
    }
    g.putImageData(img, 0, 0);
  }
  if (!a) return;
  const e = a.plate;
  for (const [w, col, dash] of [[4, "rgba(0,0,0,0.55)", []], [2, "#ffffff", [8, 6]]] as Array<[number, string, number[]]>) {
    g.save();
    g.translate(e.cx, e.cy);
    g.rotate(e.theta);
    g.beginPath();
    g.ellipse(0, 0, e.a, e.b, 0, 0, Math.PI * 2);
    g.lineWidth = w;
    g.strokeStyle = col;
    g.setLineDash(dash);
    g.stroke();
    g.restore();
  }
  if (opts.handles) {
    const { ha, hb } = handles(e);
    for (const p of [ha, hb]) {
      g.beginPath();
      g.arc(p.x, p.y, 9, 0, Math.PI * 2);
      g.fillStyle = "#ffffff";
      g.fill();
      g.lineWidth = 2.5;
      g.strokeStyle = "#16201a";
      g.stroke();
    }
  }
  if (!opts.points) return;
  const mark = (p: Point, plus: boolean) => {
    g.beginPath();
    g.arc(p.x, p.y, 8, 0, Math.PI * 2);
    g.fillStyle = plus ? "#1f6b43" : "#a8302a";
    g.fill();
    g.lineWidth = 2;
    g.strokeStyle = "#ffffff";
    g.stroke();
    g.beginPath();
    g.moveTo(p.x - 4, p.y);
    g.lineTo(p.x + 4, p.y);
    if (plus) { g.moveTo(p.x, p.y - 4); g.lineTo(p.x, p.y + 4); }
    g.stroke();
  };
  if (a.engine === "model") a.objects.forEach((o) => !o.fixed && mark(o.point, true));
  a.negatives.forEach((p) => mark(p, false));
}

export function PhotoEditor({ slot, blob, learned, onDone, onCancel }: { slot: "before" | "after"; blob: Blob; learned: PlateColours | null; onDone: (r: PhotoResult) => void; onCancel: () => void }) {
  const [phase, setPhase] = useState<Phase>("reading");
  const [ms, setMs] = useState<ModelState>(modelState());
  const [a, setA] = useState<Analysis | null>(null);
  const [tool, setTool] = useState<Tool>("add");
  const [brush, setBrush] = useState(12);
  const [busy, setBusy] = useState(false);
  const [edited, setEdited] = useState(false);
  const [tick, setTick] = useState(0);
  const [undoable, setUndoable] = useState(0);
  const frame = useRef<Frame | null>(null);
  const src = useRef<HTMLCanvasElement | null>(null);
  const add = useRef<Mask>(new Uint8Array(0));
  const erase = useRef<Mask>(new Uint8Array(0));
  const history = useRef<Snapshot[]>([]);
  const view = useRef<HTMLCanvasElement>(null);
  const drag = useRef<Drag | null>(null);
  const byHand = useRef(false);
  const id = useRef("p" + Math.random().toString(36).slice(2));
  const root = useRef<HTMLDivElement>(null);
  const colour = learned ? { food: learned.food, plate: learned.plate } : null;

  useEffect(() => onModelState(setMs), []);
  useEffect(() => { root.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [blob]); // on a phone the editor opens below the fold

  const run = useCallback(async (live: () => boolean) => {
    const f = frame.current!;
    add.current = new Uint8Array(f.width * f.height);
    erase.current = new Uint8Array(f.width * f.height);
    history.current = [];
    setUndoable(0);
    setEdited(false);
    if (byHand.current) {
      setA(analyseByHand(f));
      setPhase("ready");
      return;
    }
    try {
      setPhase(modelState().status === "ready" ? "looking" : "model");
      await loadModel();
      if (!live() || byHand.current) return;
      setPhase("looking");
      await embed(id.current, f);
      const res = await analyse(id.current, f, learned);
      if (!live() || byHand.current) return;
      setA(res);
      setPhase("ready");
    } catch {
      if (!live()) return;
      byHand.current = true;
      setA(analyseByHand(f));
      setPhase("ready");
    }
  }, [learned]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const bmp = await createImageBitmap(blob);
      const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
      const w = Math.max(1, Math.round(bmp.width * k)), h = Math.max(1, Math.round(bmp.height * k));
      const c = document.createElement("canvas");
      c.width = w;
      c.height = h;
      const g = c.getContext("2d", { willReadFrequently: true })!;
      g.drawImage(bmp, 0, 0, w, h);
      if (!alive) return;
      src.current = c;
      frame.current = { width: w, height: h, data: g.getImageData(0, 0, w, h).data };
      setTick((t) => t + 1);
      await run(() => alive);
    })().catch(() => alive && setPhase("failed"));
    const mine = id.current;
    return () => {
      alive = false;
      forget(mine);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blob]);

  const final = useMemo(() => {
    const f = frame.current;
    if (!f || !a) return null;
    return composeMask(unionMask(a.objects, f.width, f.height), add.current, erase.current, a.plate, f.width, f.height);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a, tick]);
  const share = final && a ? plateShare(final, a.plate) : 0;

  // ---- drawing: photo, amber outline of the food, dashed plate rim, the points tapped
  useEffect(() => {
    const c = view.current, f = frame.current, s = src.current;
    if (!c || !f || !s) return;
    if (c.width !== f.width || c.height !== f.height) { c.width = f.width; c.height = f.height; }
    paintScene(c.getContext("2d")!, f, s, final, a, { handles: tool === "plate", points: true });
  }, [final, a, tool, tick]);

  // ---- editing
  const snapshot = () => {
    if (!a) return;
    history.current.push({ a, add: add.current.slice(), erase: erase.current.slice() });
    if (history.current.length > 20) history.current.shift();
    setUndoable(history.current.length);
  };
  const undo = () => {
    const s = history.current.pop();
    if (!s) return;
    add.current = s.add;
    erase.current = s.erase;
    setA(s.a);
    setTick((t) => t + 1);
    setUndoable(history.current.length);
  };
  const clearAll = () => {
    if (!a) return;
    snapshot();
    add.current.fill(0);
    erase.current.fill(0);
    setA({ ...a, objects: [], negatives: [], note: null });
    setEdited(true);
    setTick((t) => t + 1);
  };

  const at = (e: RPointerEvent<HTMLCanvasElement>): Point => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * e.currentTarget.width, y: ((e.clientY - r.top) / r.height) * e.currentTarget.height };
  };

  const onDown = (e: RPointerEvent<HTMLCanvasElement>) => {
    const f = frame.current;
    if (!f || !a || phase !== "ready" || busy) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = at(e);
    snapshot();
    if (tool === "brush" || tool === "erase") {
      stamp(tool === "brush" ? add.current : erase.current, tool === "brush" ? erase.current : add.current, f.width, f.height, p, brush);
      drag.current = { kind: "paint", last: p };
      setEdited(true);
      setTick((t) => t + 1);
    } else if (tool === "plate") {
      const { ha, hb } = handles(a.plate);
      const reach = 18 * (f.width / e.currentTarget.getBoundingClientRect().width);
      if (Math.hypot(p.x - ha.x, p.y - ha.y) < reach) drag.current = { kind: "a" };
      else if (Math.hypot(p.x - hb.x, p.y - hb.y) < reach) drag.current = { kind: "b" };
      else if (ellipseRho(a.plate, p.x, p.y) <= 1) drag.current = { kind: "move", dx: p.x - a.plate.cx, dy: p.y - a.plate.cy };
      else drag.current = { kind: "tap", at: p };
    } else {
      drag.current = { kind: "tap", at: p };
    }
  };

  const onMove = (e: RPointerEvent<HTMLCanvasElement>) => {
    const f = frame.current, d = drag.current;
    if (!f || !a || !d) return;
    const p = at(e);
    if (d.kind === "paint") {
      const steps = Math.max(1, Math.ceil(Math.hypot(p.x - d.last.x, p.y - d.last.y) / (brush / 2)));
      for (let k = 1; k <= steps; k++) {
        const q = { x: d.last.x + ((p.x - d.last.x) * k) / steps, y: d.last.y + ((p.y - d.last.y) * k) / steps };
        stamp(tool === "brush" ? add.current : erase.current, tool === "brush" ? erase.current : add.current, f.width, f.height, q, brush);
      }
      d.last = p;
      setTick((t) => t + 1);
    } else if (d.kind === "move") {
      setA({ ...a, plate: { ...a.plate, cx: p.x - d.dx, cy: p.y - d.dy } });
      setEdited(true);
    } else if (d.kind === "a" || d.kind === "b") {
      const len = Math.max(12, Math.hypot(p.x - a.plate.cx, p.y - a.plate.cy));
      setA({ ...a, plate: { ...a.plate, [d.kind]: len } });
      setEdited(true);
    }
  };

  const onUp = async () => {
    const f = frame.current, d = drag.current;
    drag.current = null;
    if (f && a && d && (d.kind === "move" || d.kind === "a" || d.kind === "b")) setA(reguess(f, a));
    if (!f || !a || !d || d.kind !== "tap") return;
    setBusy(true);
    try {
      let next = a;
      if (tool === "add") next = await addFood(id.current, f, a, d.at, colour);
      else if (tool === "remove") next = await excludeHere(id.current, f, a, d.at, colour);
      else if (tool === "plate") next = a.engine === "model" ? await plateHere(id.current, f, a, d.at) : a;
      setA(next);
      setEdited(true);
    } catch {
      setA({ ...a, note: "That tap could not be outlined. Use the brush for this part." });
    } finally {
      setBusy(false);
    }
  };

  const outlineByHand = () => {
    byHand.current = true;
    if (frame.current) {
      setA(analyseByHand(frame.current));
      setPhase("ready");
    }
  };

  const done = () => {
    const f = frame.current, s = src.current, c = view.current;
    if (!f || !s || !a || !final || !c) return;
    const plateRegion = ellipseMask(a.plate, f.width, f.height);
    const food = colourStats(f, final);
    const rest = colourStats(f, final, plateRegion, true);
    const ring = colourStats(f, final, ringMask(a.plate, f.width, f.height), true);
    const clean = document.createElement("canvas"); // the outline without handles or tap marks
    clean.width = f.width;
    clean.height = f.height;
    paintScene(clean.getContext("2d")!, f, s, final, a, { handles: false, points: false });
    const t = document.createElement("canvas");
    t.width = 240;
    t.height = Math.round((240 * f.height) / f.width);
    t.getContext("2d")!.drawImage(clean, 0, 0, t.width, t.height);
    onDone({
      share, thumb: t.toDataURL("image/jpeg", 0.72), edited, engine: a.engine,
      colours: { food: food.n ? food.mean : rest.mean, plate: rest.mean, ring: ring.n ? ring.mean : rest.mean },
    });
  };

  const waiting = phase === "reading" || phase === "model" || phase === "looking";
  const size = a ? canResize(a) : { smaller: false, larger: false };
  const mb = (n: number) => (n / 1e6).toFixed(1);
  const status =
    phase === "model" && ms.status === "loading"
      ? ms.total ? `Getting the outlining model: ${mb(ms.loaded)} of ${mb(ms.total)} MB. This happens once.` : "Getting the outlining model. This happens once."
      : phase === "looking" ? "Finding the plate and the food…"
      : phase === "reading" ? "Reading the photo…"
      : phase === "failed" ? "That image could not be read. Try a JPEG or PNG photo."
      : a?.engine === "by-hand" ? "Outlining by hand." : "Outlined automatically. Check it, and fix anything that is off.";

  return (
    <div className="editor" ref={root}>
      <div className="row between">
        <h3>{slot === "before" ? "Before eating" : "After eating"}: check the outline</h3>
        <span className="small muted" role="status">{status}</span>
      </div>
      <div className="editor-work">
        <div className="editor-stage">
          <canvas ref={view} className={`editor-canvas tool-${tool}`} role="img"
            aria-label={`${slot} photo. Food outlined in amber covers ${(share * 100).toFixed(0)} percent of the plate.`}
            onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={() => { drag.current = null; }} />
          {waiting ? (
            <div className="editor-wait">
              <span className="spin" aria-hidden />
              <span>{status}</span>
              {phase !== "reading" ? <button type="button" className="btn sm" onClick={outlineByHand}>Outline by hand instead</button> : null}
            </div>
          ) : null}
          {busy ? <div className="editor-busy"><span className="spin" aria-hidden /></div> : null}
        </div>
        <div className="stack">
          <div className="readout">
            <span className="small muted">Food in this photo</span>
            <span className="figure" data-share={(share * 100).toFixed(2)}>{phase === "ready" ? `${(share * 100).toFixed(0)}%` : "…"}</span>
            <span className="small">of the plate is covered{edited ? " · adjusted by hand" : ""}</span>
          </div>
          {a?.note ? <div className="note amber">{a.note}</div> : null}
          <div className="seg tools" role="group" aria-label="Correction tool">
            {TOOLS.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={tool === k} disabled={phase !== "ready"} onClick={() => setTool(k)}>{label}</button>
            ))}
          </div>
          <p className="small muted">{HINT[tool]}</p>
          {tool === "brush" || tool === "erase" ? (
            <label className="field">
              <span>Brush size</span>
              <input type="range" min={4} max={40} value={brush} onChange={(e) => setBrush(Number(e.target.value))} style={{ accentColor: "var(--scale)" }} />
            </label>
          ) : null}
          <div className="row">
            {a?.engine === "model" ? (
              <>
                <button type="button" className="btn sm" disabled={!size.smaller || busy} onClick={() => { snapshot(); setA(resizeLast(a, -1)); setEdited(true); }}>Smaller outline</button>
                <button type="button" className="btn sm" disabled={!size.larger || busy} onClick={() => { snapshot(); setA(resizeLast(a, 1)); setEdited(true); }}>Larger outline</button>
              </>
            ) : null}
            <button type="button" className="btn sm" disabled={!undoable || busy} onClick={undo}>Undo</button>
            <button type="button" className="btn sm ghost" disabled={phase !== "ready" || busy} onClick={clearAll}>No food here</button>
            <button type="button" className="btn sm ghost" disabled={phase !== "ready" || busy} onClick={() => run(() => true)}>Start over</button>
          </div>
          <div className="row">
            <button type="button" className="btn primary" disabled={phase !== "ready" || busy} onClick={done}>Use this outline</button>
            <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}
