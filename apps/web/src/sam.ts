// Main-thread handle on the segmentation worker. One worker for the page,
// started the first time a photo panel is opened.
import { Candidate, Frame, Point, toCandidates } from "./photo";

export type ModelState =
  | { status: "idle" }
  | { status: "loading"; loaded: number; total: number }
  | { status: "ready" }
  | { status: "unavailable"; reason: string };

let worker: Worker | null = null;
let state: ModelState = { status: "idle" };
let readyPromise: Promise<void> | null = null;
let seq = 0;
const listeners = new Set<(s: ModelState) => void>();
const pending = new Map<number, { resolve: (v: any) => void; reject: (e: Error) => void }>();
const embeds = new Map<string, { resolve: () => void; reject: (e: Error) => void }>();
const sizes = new Map<string, { loaded: number; total: number }>();

function set(s: ModelState): void {
  state = s;
  listeners.forEach((fn) => fn(s));
}
export function modelState(): ModelState {
  return state;
}
export function onModelState(fn: (s: ModelState) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Start downloading the model. Safe to call repeatedly. Rejects if it cannot be loaded. */
export function loadModel(): Promise<void> {
  if (readyPromise) return readyPromise;
  readyPromise = new Promise<void>((resolve, reject) => {
    try {
      worker = new Worker(new URL("./samWorker.ts", import.meta.url), { type: "module" });
    } catch (e) {
      set({ status: "unavailable", reason: "This browser cannot start the analysis worker." });
      reject(e as Error);
      return;
    }
    set({ status: "loading", loaded: 0, total: 0 });
    worker.onerror = () => {
      set({ status: "unavailable", reason: "The selection model could not be started." });
      reject(new Error("worker failed"));
    };
    worker.onmessage = (e: MessageEvent) => {
      const m = e.data;
      if (m.kind === "progress") {
        sizes.set(m.file, { loaded: m.loaded, total: m.total });
        let loaded = 0, total = 0;
        sizes.forEach((v) => { loaded += v.loaded; total += v.total; });
        if (state.status === "loading") set({ status: "loading", loaded, total });
      } else if (m.kind === "ready") {
        set({ status: "ready" });
        resolve();
      } else if (m.kind === "embedded") {
        embeds.get(m.id)?.resolve();
        embeds.delete(m.id);
      } else if (m.kind === "decoded") {
        pending.get(m.reqId)?.resolve(m);
        pending.delete(m.reqId);
      } else if (m.kind === "error") {
        const err = new Error(m.message);
        if (m.where === "load") {
          set({ status: "unavailable", reason: "The selection model could not be downloaded. Check the connection, or outline the food with the brush." });
          readyPromise = null;
          reject(err);
        } else if (m.where === "embed") {
          embeds.get(m.id)?.reject(err);
          embeds.delete(m.id);
        } else if (m.reqId != null) {
          pending.get(m.reqId)?.reject(err);
          pending.delete(m.reqId);
        }
      }
    };
    worker.postMessage({ kind: "load" });
  });
  readyPromise.catch(() => undefined);
  return readyPromise;
}

/** Let the model look at an image once; outlines can then be asked for quickly. */
export async function embed(id: string, frame: Frame): Promise<void> {
  await loadModel();
  const copy = new Uint8ClampedArray(frame.data); // the worker takes ownership of the copy
  return new Promise<void>((resolve, reject) => {
    embeds.set(id, { resolve, reject });
    worker!.postMessage({ kind: "embed", id, rgba: copy.buffer, width: frame.width, height: frame.height }, [copy.buffer]);
  });
}

/** Candidate outlines for a set of include (1) and exclude (0) points. */
export async function outlines(id: string, points: Point[], labels: number[]): Promise<Candidate[]> {
  const reqId = ++seq;
  const m: any = await new Promise((resolve, reject) => {
    pending.set(reqId, { resolve, reject });
    worker!.postMessage({ kind: "decode", id, reqId, points: points.map((p) => [p.x, p.y]), labels });
  });
  return toCandidates(new Uint8Array(m.masks), m.n, m.width, m.height, m.scores);
}

export function forget(id: string): void {
  worker?.postMessage({ kind: "forget", id });
}
