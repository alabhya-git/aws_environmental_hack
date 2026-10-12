// Runs the segmentation model off the main thread.
//
// Model: SlimSAM (a compressed Segment Anything), Apache-2.0, ~14 MB quantised,
// run with Transformers.js on ONNX Runtime Web (WebAssembly). The library and
// the weights are downloaded from their public CDNs the first time and cached
// by the browser. The photo itself is processed here, in this browser, and is
// never sent anywhere.
const LIB = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.6";
const MODEL = "Xenova/slimsam-77-uniform";

/* eslint-disable @typescript-eslint/no-explicit-any */
const ctx = self as any;
let T: any = null;
let model: any = null;
let processor: any = null;
let loading: Promise<void> | null = null;
const cache = new Map<string, { inputs: any; emb: any; w: number; h: number }>();

function load(): Promise<void> {
  if (!loading) {
    loading = (async () => {
      T = await import(/* @vite-ignore */ LIB);
      T.env.allowLocalModels = false;
      const progress = (p: any) => {
        if (p.status === "progress" && typeof p.file === "string" && p.file.endsWith(".onnx")) {
          ctx.postMessage({ kind: "progress", file: p.file, loaded: p.loaded, total: p.total });
        }
      };
      model = await T.SamModel.from_pretrained(MODEL, { dtype: "q8", device: "wasm", progress_callback: progress });
      processor = await T.AutoProcessor.from_pretrained(MODEL);
    })();
    loading.catch(() => { loading = null; });
  }
  return loading;
}

ctx.onmessage = async (e: MessageEvent) => {
  const m = e.data;
  try {
    if (m.kind === "load") {
      await load();
      ctx.postMessage({ kind: "ready" });
    } else if (m.kind === "embed") {
      await load();
      const t0 = performance.now();
      const raw = new T.RawImage(new Uint8ClampedArray(m.rgba), m.width, m.height, 4).rgb();
      const inputs = await processor(raw);
      const emb = await model.get_image_embeddings(inputs);
      if (cache.size >= 3) cache.delete(cache.keys().next().value as string);
      cache.set(m.id, { inputs, emb, w: m.width, h: m.height });
      ctx.postMessage({ kind: "embedded", id: m.id, ms: Math.round(performance.now() - t0) });
    } else if (m.kind === "decode") {
      const c = cache.get(m.id);
      if (!c) throw new Error("image is no longer loaded");
      const [rh, rw] = c.inputs.reshaped_input_sizes[0];
      const flat: number[] = [];
      for (const [x, y] of m.points as number[][]) flat.push((x / c.w) * rw, (y / c.h) * rh);
      const input_points = new T.Tensor("float32", flat, [1, 1, m.points.length, 2]);
      const input_labels = new T.Tensor("int64", (m.labels as number[]).map((l) => BigInt(l)), [1, 1, m.labels.length]);
      const out = await model({ ...c.emb, input_points, input_labels });
      const masks = await processor.post_process_masks(out.pred_masks, c.inputs.original_sizes, c.inputs.reshaped_input_sizes);
      const t = masks[0];
      const n = t.dims[1];
      const buf = new Uint8Array(n * c.w * c.h);
      for (let i = 0; i < buf.length; i++) buf[i] = t.data[i] ? 1 : 0;
      ctx.postMessage({ kind: "decoded", reqId: m.reqId, n, width: c.w, height: c.h, scores: Array.from(out.iou_scores.data as Float32Array), masks: buf.buffer }, [buf.buffer]);
    } else if (m.kind === "forget") {
      cache.delete(m.id);
    }
  } catch (err) {
    ctx.postMessage({ kind: "error", reqId: m.reqId, where: m.kind, id: m.id, message: String((err as Error)?.message || err) });
  }
};
