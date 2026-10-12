// Leftovers from two photos: the plate as served, and the plate as returned.
// The before photo's weight is already known from the recorded serving, so it
// calibrates the after photo. Photos are analysed in this browser and are not
// uploaded; only the resulting grams, uncertainty and area shares are saved.
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { useWs } from "../App";
import { CalPoint, estimateFromCalibration, estimateLeftover, fitCalibration, LEFTOVER_FLAG_TEXT } from "../photo";
import { PlateColours } from "../photoAuto";
import { loadModel } from "../sam";
import { PhotoEditor, PhotoResult } from "./PhotoEditor";

type Slot = "before" | "after";
interface Saved extends PhotoResult { ts: number }
export interface PhotoDetail {
  method: "before_after" | "after_only";
  share_before: number | null;
  share_after: number;
  reference_g: number;
  exponent: number;
  edited: boolean;
  engine: "model" | "by-hand";
}

const BEFORE_KEY = "proofplate.photo.before.v2";
const CAL_KEY = "proofplate.photo.cal.v2";

function read<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or unavailable: the photo setup lasts until the page closes */
  }
}
function saveBefore(plateKey: string, s: Saved | null): void {
  const all = read<Record<string, Saved>>(BEFORE_KEY, {});
  if (s) all[plateKey] = s;
  else delete all[plateKey];
  const keep = Object.entries(all).sort((x, y) => y[1].ts - x[1].ts).slice(0, 20); // thumbnails are small, but not free
  write(BEFORE_KEY, Object.fromEntries(keep));
}
type Cal = CalPoint & { key: string };
function saveCal(foodId: string, key: string, point: CalPoint | null): void {
  const all = read<Record<string, Cal[]>>(CAL_KEY, {});
  const rest = (all[foodId] || []).filter((p) => p.key !== key);
  all[foodId] = point ? [...rest, { ...point, key }].slice(-40) : rest;
  write(CAL_KEY, all);
}

function Tile({ slot, saved, hasBlob, onFile, onAdjust, onRemove }: { slot: Slot; saved: Saved | null; hasBlob: boolean; onFile: (b: Blob) => void; onAdjust: () => void; onRemove: () => void }) {
  const title = slot === "before" ? "Before eating" : "After eating";
  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) onFile(f);
  };
  const warm = () => void loadModel().catch(() => undefined); // start fetching the model while the user picks a photo
  return (
    <div className="tile">
      <div className="tile-pic">
        {saved ? <img src={saved.thumb} alt={`${title} photo with the food outlined`} /> : <span className="small muted">No photo yet</span>}
      </div>
      <div className="stack s">
        <strong>{title}</strong>
        <span className="small muted">
          {saved ? `Food covers ${(saved.share * 100).toFixed(0)}% of the plate${saved.edited ? ", adjusted by hand" : ""}.` : slot === "before" ? "The plate as it was served." : "The plate when it comes back."}
        </span>
        <div className="row">
          <label className="btn sm" onPointerDown={warm}>
            Take photo
            <input type="file" accept="image/*" capture="environment" className="sr" onChange={pick} aria-label={`Take the ${slot} photo`} />
          </label>
          <label className="btn sm" onPointerDown={warm}>
            Upload
            <input type="file" accept="image/*" className="sr" onChange={pick} aria-label={`Upload the ${slot} photo`} />
          </label>
          {saved && hasBlob ? <button type="button" className="btn sm ghost" onClick={onAdjust}>Adjust</button> : null}
          {saved ? <button type="button" className="btn sm ghost" onClick={onRemove}>Remove</button> : null}
        </div>
      </div>
    </div>
  );
}

export function PhotoPair({ plateKey, foodId, initialG, totalG, onUse }: { plateKey: string; foodId: string; initialG: number; totalG: number; onUse: (grams: number, sd: number, detail: PhotoDetail) => void }) {
  const { mode } = useWs();
  const [before, setBefore] = useState<Saved | null>(() => read<Record<string, Saved>>(BEFORE_KEY, {})[plateKey] || null);
  const [after, setAfter] = useState<Saved | null>(null);
  const [editing, setEditing] = useState<{ slot: Slot; blob: Blob } | null>(null);
  const [ref, setRef] = useState(String(initialG));
  const [note, setNote] = useState<string | null>(null);
  const blobs = useRef<Partial<Record<Slot, Blob>>>({});
  const queue = useRef<Blob | null>(null); // the generated "after" photo, opened once "before" is confirmed

  useEffect(() => {
    setBefore(read<Record<string, Saved>>(BEFORE_KEY, {})[plateKey] || null);
    setAfter(null);
    setEditing(null);
    setRef(String(initialG));
    blobs.current = {};
  }, [plateKey, initialG]);

  const refG = Math.max(0, Number(ref) || 0);
  useEffect(() => {
    // each confirmed before photo is also an example of "this share of the plate is this many grams"
    if (before && refG > 0 && before.share > 0.015) saveCal(foodId, plateKey, { coverage: before.share, grams: refG });
  }, [before, refG, foodId, plateKey]);

  const fit = useMemo(() => {
    const pts = (read<Record<string, Cal[]>>(CAL_KEY, {})[foodId] || []).filter((p) => p.key !== plateKey);
    return pts.length ? fitCalibration(pts) : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [foodId, plateKey, after]);

  const open = (slot: Slot, blob: Blob) => {
    blobs.current[slot] = blob;
    setNote(null);
    setEditing({ slot, blob });
  };
  const done = (r: PhotoResult) => {
    if (!editing) return;
    const saved: Saved = { ...r, ts: Date.now() };
    if (editing.slot === "before") {
      setBefore(saved);
      saveBefore(plateKey, saved);
      setAfter(null); // an after photo outlined against an older before photo no longer applies
    } else setAfter(saved);
    const next = editing.slot === "before" ? queue.current : null;
    queue.current = null;
    setEditing(next ? { slot: "after", blob: next } : null);
    if (next) blobs.current.after = next;
  };

  const loadSamples = async () => {
    setNote(null);
    try {
      const get = async (f: string) => (await fetch("/samples/photos/" + f)).blob();
      const [b, a] = await Promise.all([get("pair-before.jpg"), get("pair-after.jpg")]);
      queue.current = a;
      open("before", b);
      setNote("These two photos are generated demonstration images, not real food. Confirm each outline to see the result.");
    } catch {
      setNote("The sample photos could not be loaded.");
    }
  };

  const learned: PlateColours | null = editing?.slot === "after" && before ? before.colours : null;
  const pair = before && after ? estimateLeftover({ shareBefore: before.share, shareAfter: after.share, referenceG: refG }) : null;
  const alone = !before && after && fit ? estimateFromCalibration(fit, after.share) : null;
  const cap = (g: number) => Math.min(g, totalG);

  return (
    <div className="photos stack">
      <div className="row between">
        <h3>Leftovers from two photos</h3>
        {mode === "demo" ? <button type="button" className="btn sm ghost" onClick={loadSamples}>Try a generated before and after pair</button> : null}
      </div>
      <p className="small muted">
        Photograph the plate as served and again when it comes back. The app outlines the plate and the food in each, and you can correct either outline.
        Photos are analysed on this device and are not uploaded.
      </p>
      <div className="pair">
        <Tile slot="before" saved={before} hasBlob={!!blobs.current.before} onFile={(b) => open("before", b)}
          onAdjust={() => blobs.current.before && open("before", blobs.current.before)}
          onRemove={() => { setBefore(null); saveBefore(plateKey, null); saveCal(foodId, plateKey, null); }} />
        <Tile slot="after" saved={after} hasBlob={!!blobs.current.after} onFile={(b) => open("after", b)}
          onAdjust={() => blobs.current.after && open("after", blobs.current.after)} onRemove={() => setAfter(null)} />
      </div>
      {note ? <div className="note" role="status">{note}</div> : null}

      {editing ? (
        <PhotoEditor key={editing.slot + String(editing.blob.size)} slot={editing.slot} blob={editing.blob} learned={learned} onDone={done} onCancel={() => { queue.current = null; setEditing(null); }} />
      ) : null}

      {!editing && before ? (
        <label className="field" style={{ maxWidth: 330 }}>
          <span>Food on the plate in the before photo (grams)</span>
          <input className="input num" inputMode="numeric" value={ref} onChange={(e) => setRef(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
            onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} aria-label="Grams of food in the before photo" style={{ maxWidth: 130 }} />
          <small>Taken from the recorded first serving. Change it only if the photo shows a different amount.</small>
        </label>
      ) : null}

      {!editing && pair ? (
        <div className="stack">
          <div className="readout">
            <span className="small muted">Left on the plate, from the two photos</span>
            <span className="figure">{cap(pair.grams)} g</span>
            <span className="small">
              ± {pair.sd_g} g. The food covers {(Math.min(pair.ratio, 9.99) * 100).toFixed(0)}% of the area it did before:
              between {pair.lo_g} g if it was a mound and {pair.hi_g} g if it was spread flat.
            </span>
          </div>
          {pair.flags.map((f) => <div key={f} className="note amber">{LEFTOVER_FLAG_TEXT[f]}</div>)}
          <div>
            <button type="button" className="btn primary" disabled={refG <= 0 || pair.flags.includes("no_food_before")}
              onClick={() => onUse(cap(pair.grams), pair.sd_g, { method: "before_after", share_before: before!.share, share_after: after!.share, reference_g: refG, exponent: pair.exponent, edited: before!.edited || after!.edited, engine: before!.engine === "model" && after!.engine === "model" ? "model" : "by-hand" })}>
              Use {cap(pair.grams)} g as the returned amount
            </button>
          </div>
        </div>
      ) : null}

      {!editing && !before && after ? (
        alone && fit ? (
          <div className="stack">
            <div className="readout">
              <span className="small muted">Left on the plate, from this photo and {fit.n} earlier before-photo{fit.n === 1 ? "" : "s"} of this food</span>
              <span className="figure">{cap(alone.grams)} g</span>
              <span className="small">± {alone.sd_g} g. Less certain than a before and after pair; add the before photo if you have it.</span>
            </div>
            <div>
              <button type="button" className="btn primary" onClick={() => onUse(cap(alone.grams), alone.sd_g, { method: "after_only", share_before: null, share_after: after.share, reference_g: 0, exponent: fit.p, edited: after.edited, engine: after.engine })}>
                Use {cap(alone.grams)} g as the returned amount
              </button>
            </div>
          </div>
        ) : (
          <div className="note">An after photo alone cannot be turned into grams yet. Add the before photo, or weigh the plate.</div>
        )
      ) : null}
      {!editing && before && !after ? <div className="note leaf">The before photo is saved on this device. Add the after photo when the plate comes back.</div> : null}
    </div>
  );
}
