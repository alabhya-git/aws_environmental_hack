// Before-and-after plate photos -> grams left. Pure image arithmetic and
// selection logic; no DOM and no model in this file, so it runs in Node tests.
//
// The idea: the "before" photo shows a plate whose food weight is already
// known (the serving that was recorded). The "after" photo shows the same
// plate when it comes back. In each photo we find the plate and the food on
// it, express the food as a share of the plate's area (so camera distance
// cancels out), and scale the known weight by how much that share shrank.
//
// Finding the food is done by a promptable segmentation model (see
// samWorker.ts). This file decides which of the model's candidate outlines is
// the plate and which is the food, combines them with the user's corrections,
// and turns areas into grams with an honest uncertainty.

export interface Frame {
  width: number;
  height: number;
  data: Uint8ClampedArray; // RGBA
}
export type Mask = Uint8Array; // 1 = selected, length width*height
export type RGB = [number, number, number];

/** Rotated ellipse in pixels: centre, semi-axes, rotation in radians. */
export interface Ellipse {
  cx: number;
  cy: number;
  a: number;
  b: number;
  theta: number;
}
export interface Point {
  x: number;
  y: number;
}

// ---------------------------------------------------------------- geometry
export function maskArea(m: Mask): number {
  let n = 0;
  for (let i = 0; i < m.length; i++) n += m[i];
  return n;
}

export function ellipseArea(e: Ellipse): number {
  return Math.PI * e.a * e.b;
}

/** Normalised radius: 1 on the ellipse, < 1 inside. */
export function ellipseRho(e: Ellipse, x: number, y: number): number {
  const dx = x - e.cx;
  const dy = y - e.cy;
  const c = Math.cos(e.theta);
  const s = Math.sin(e.theta);
  const u = (dx * c + dy * s) / e.a;
  const v = (-dx * s + dy * c) / e.b;
  return Math.sqrt(u * u + v * v);
}

export function ellipseMask(e: Ellipse, W: number, H: number): Mask {
  const m = new Uint8Array(W * H);
  const r = Math.max(e.a, e.b);
  const x0 = Math.max(0, Math.floor(e.cx - r));
  const x1 = Math.min(W - 1, Math.ceil(e.cx + r));
  const y0 = Math.max(0, Math.floor(e.cy - r));
  const y1 = Math.min(H - 1, Math.ceil(e.cy + r));
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if (ellipseRho(e, x, y) <= 1) m[y * W + x] = 1;
  return m;
}

/** Ellipse with the same centre and second moments as a filled region. */
export function fitEllipse(mask: Mask, W: number, H: number): Ellipse | null {
  let n = 0, sx = 0, sy = 0;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (mask[y * W + x]) { n++; sx += x; sy += y; }
  if (n < 20) return null;
  const mx = sx / n;
  const my = sy / n;
  let sxx = 0, syy = 0, sxy = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (!mask[y * W + x]) continue;
      const dx = x - mx;
      const dy = y - my;
      sxx += dx * dx;
      syy += dy * dy;
      sxy += dx * dy;
    }
  }
  sxx /= n; syy /= n; sxy /= n;
  const half = (sxx + syy) / 2;
  const root = Math.sqrt(((sxx - syy) / 2) ** 2 + sxy * sxy);
  // for a filled ellipse the variance along a semi-axis of length a is a^2 / 4
  return { cx: mx, cy: my, a: 2 * Math.sqrt(half + root), b: 2 * Math.sqrt(Math.max(half - root, 1e-6)), theta: 0.5 * Math.atan2(2 * sxy, sxx - syy) };
}

export function defaultPlate(W: number, H: number): Ellipse {
  const r = 0.44 * Math.min(W, H);
  return { cx: W / 2, cy: H / 2, a: r, b: r, theta: 0 };
}

/** 3x3 majority filter: removes specks and fills pinholes. */
export function majority(mask: Mask, W: number, H: number): Mask {
  const out = new Uint8Array(W * H);
  for (let y = 1; y < H - 1; y++) {
    for (let x = 1; x < W - 1; x++) {
      let n = 0;
      for (let oy = -1; oy <= 1; oy++) for (let ox = -1; ox <= 1; ox++) n += mask[(y + oy) * W + x + ox];
      if (n >= 5) out[y * W + x] = 1;
    }
  }
  return out;
}

export interface Component {
  area: number;
  cx: number;
  cy: number;
  seed: Point; // a pixel that is certainly inside the component
  pixels: Int32Array;
}

/** 4-connected components, largest first. */
export function components(mask: Mask, W: number, H: number, minArea = 1): Component[] {
  const seen = new Uint8Array(W * H);
  const out: Component[] = [];
  const stack: number[] = [];
  for (let start = 0; start < mask.length; start++) {
    if (!mask[start] || seen[start]) continue;
    const px: number[] = [];
    stack.push(start);
    seen[start] = 1;
    let sx = 0, sy = 0;
    while (stack.length) {
      const i = stack.pop()!;
      px.push(i);
      const x = i % W;
      const y = (i - x) / W;
      sx += x; sy += y;
      if (x > 0 && mask[i - 1] && !seen[i - 1]) { seen[i - 1] = 1; stack.push(i - 1); }
      if (x < W - 1 && mask[i + 1] && !seen[i + 1]) { seen[i + 1] = 1; stack.push(i + 1); }
      if (y > 0 && mask[i - W] && !seen[i - W]) { seen[i - W] = 1; stack.push(i - W); }
      if (y < H - 1 && mask[i + W] && !seen[i + W]) { seen[i + W] = 1; stack.push(i + W); }
    }
    if (px.length < minArea) continue;
    const cx = sx / px.length;
    const cy = sy / px.length;
    let best = px[0], bd = Infinity; // the centroid of a ring lies outside it, so take the nearest member
    for (const i of px) {
      const x = i % W;
      const y = (i - x) / W;
      const d = (x - cx) ** 2 + (y - cy) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    out.push({ area: px.length, cx, cy, seed: { x: best % W, y: Math.floor(best / W) }, pixels: Int32Array.from(px) });
  }
  return out.sort((p, q) => q.area - p.area);
}

/** The connected part of a mask that contains (or is nearest to) a point. */
export function componentAt(mask: Mask, W: number, H: number, p: Point): Mask {
  const comps = components(mask, W, H, 1);
  if (!comps.length) return new Uint8Array(W * H);
  const idx = Math.round(p.y) * W + Math.round(p.x);
  let pick = comps[0];
  if (!mask[idx]) {
    let bd = Infinity;
    for (const c of comps) {
      const d = (c.seed.x - p.x) ** 2 + (c.seed.y - p.y) ** 2 - c.area; // prefer near and large
      if (d < bd) { bd = d; pick = c; }
    }
  } else {
    for (const c of comps) if (c.pixels.includes(idx)) { pick = c; break; }
  }
  const out = new Uint8Array(W * H);
  for (const i of pick.pixels) out[i] = 1;
  return out;
}

export function contains(mask: Mask, W: number, H: number, p: Point, tol = 2): boolean {
  const x0 = Math.round(p.x);
  const y0 = Math.round(p.y);
  for (let dy = -tol; dy <= tol; dy++) {
    for (let dx = -tol; dx <= tol; dx++) {
      const x = x0 + dx;
      const y = y0 + dy;
      if (x >= 0 && y >= 0 && x < W && y < H && mask[y * W + x]) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------- colour
export interface Stats {
  mean: RGB;
  sd: number; // root mean per-channel variance
  n: number;
}

/** Colour statistics of the pixels where `inside` is set (and `within`, if given). */
export function colourStats(f: Frame, inside: Mask, within?: Mask, invert = false): Stats {
  let n = 0;
  const s = [0, 0, 0];
  const q = [0, 0, 0];
  for (let i = 0; i < inside.length; i++) {
    if (within && !within[i]) continue;
    if ((inside[i] === 1) === invert) continue;
    n++;
    for (let c = 0; c < 3; c++) {
      const v = f.data[i * 4 + c];
      s[c] += v;
      q[c] += v * v;
    }
  }
  if (!n) return { mean: [0, 0, 0], sd: 0, n: 0 };
  const mean: RGB = [s[0] / n, s[1] / n, s[2] / n];
  const varc = (q[0] / n - mean[0] ** 2 + q[1] / n - mean[1] ** 2 + q[2] / n - mean[2] ** 2) / 3;
  return { mean, sd: Math.sqrt(Math.max(varc, 0)), n };
}

export function dist(a: RGB, b: RGB): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/**
 * How cleanly a region stands apart from its surroundings: distance between
 * the two mean colours over their combined spread. A mound of rice against
 * steel scores high; "half the bowl, rice and metal together" scores low.
 */
export function separation(f: Frame, region: Mask, context: Mask): number {
  const inn = colourStats(f, region, context);
  const out = colourStats(f, region, context, true);
  if (inn.n < 10 || out.n < 10) return 0;
  return dist(inn.mean, out.mean) / (inn.sd + out.sd + 8);
}

// ---------------------------------------------------------------- choosing among the model's candidates
export interface Candidate {
  mask: Mask;
  iou: number; // the model's own confidence
  area: number;
}

export function toCandidates(buf: Uint8Array, n: number, W: number, H: number, scores: number[]): Candidate[] {
  const per = W * H;
  const out: Candidate[] = [];
  for (let k = 0; k < n; k++) {
    const mask = buf.slice(k * per, (k + 1) * per);
    out.push({ mask, iou: scores[k] ?? 0, area: maskArea(mask) });
  }
  return out;
}

/** How well a region is described by its own best-fit ellipse (1 = exactly an ellipse; a rectangle scores about 0.84). */
export function roundness(mask: Mask, W: number, H: number): { ellipse: Ellipse; fit: number } | null {
  const e = fitEllipse(mask, W, H);
  if (!e) return null;
  const em = ellipseMask(e, W, H);
  let both = 0, either = 0;
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] && em[i]) both++;
    if (mask[i] || em[i]) either++;
  }
  return { ellipse: e, fit: either ? both / either : 0 };
}

export interface PlateChoice {
  cand: Candidate;
  ellipse: Ellipse;
  fit: number;
}

/**
 * The plate is the largest round outline around the point. Roundness matters:
 * a tray, a placemat or the table can be the largest outline, but a plate or
 * bowl seen from above is an ellipse. If a slightly smaller outline is clearly
 * more confident, prefer it: the largest one often swallows a neighbour.
 */
export function pickPlate(cands: Candidate[], W: number, H: number, p: Point, foodArea = 0): PlateChoice | null {
  const img = W * H;
  const ok: PlateChoice[] = [];
  for (const c of cands) {
    if (c.iou < 0.5 || c.area < 0.1 * img || c.area > 0.985 * img || c.area < 1.25 * foodArea || !contains(c.mask, W, H, p, 6)) continue;
    const r = roundness(componentAt(c.mask, W, H, p), W, H);
    if (r) ok.push({ cand: c, ellipse: r.ellipse, fit: r.fit });
  }
  if (!ok.length) return null;
  const round = ok.filter((o) => o.fit >= 0.88);
  const pool = (round.length ? round : ok).sort((x, y) => y.cand.area - x.cand.area);
  let best = pool[0];
  for (const o of pool.slice(1)) if (o.cand.area >= 0.7 * best.cand.area && o.cand.iou > best.cand.iou + 0.02) best = o;
  return best;
}

export interface ColourModel {
  food: RGB;
  plate: RGB;
}

export interface FoodChoice {
  index: number;
  score: number;
}

/**
 * Rank the candidates around a tapped point as "the food here". Candidates
 * must include the point, avoid every "not food" point and be smaller than the
 * plate; among those, prefer the one that separates most cleanly in colour,
 * with a mild preference for larger regions and, if known, for the food's colour.
 */
export function rankFood(f: Frame, cands: Candidate[], opts: { point: Point; negatives: Point[]; plateMask: Mask; plateArea: number; colour?: ColourModel | null }): FoodChoice[] {
  const { width: W, height: H } = f;
  const out: FoodChoice[] = [];
  cands.forEach((c, index) => {
    if (c.area < 0.0015 * W * H || c.area > 0.82 * opts.plateArea) return;
    if (!contains(c.mask, W, H, opts.point, 3)) return;
    if (opts.negatives.some((n) => contains(c.mask, W, H, n, 0))) return;
    let score = separation(f, c.mask, opts.plateMask) * Math.pow(c.area / opts.plateArea, 0.2);
    if (opts.colour) {
      const m = colourStats(f, c.mask).mean;
      const df = dist(m, opts.colour.food);
      const dp = dist(m, opts.colour.plate);
      score *= 0.4 + 1.2 * (dp / (df + dp + 1e-6));
    }
    out.push({ index, score });
  });
  return out.sort((x, y) => y.score - x.score);
}

/** Below this the "food" is not distinguishable from the plate around it. */
export const MIN_SEPARATION = 0.55;

// ---------------------------------------------------------------- finding leftovers by colour
/** Exposure change between two photos of the same plate, from the plate's own colour. */
export function gainFrom(reference: RGB, now: RGB): RGB {
  return [0, 1, 2].map((c) => Math.min(1.7, Math.max(0.6, (now[c] + 1) / (reference[c] + 1)))) as RGB;
}

export function applyGain(m: ColourModel, g: RGB): ColourModel {
  return { food: [m.food[0] * g[0], m.food[1] * g[1], m.food[2] * g[2]], plate: [m.plate[0] * g[0], m.plate[1] * g[1], m.plate[2] * g[2]] };
}

/** Pixels on the plate that look like the food seen in the "before" photo. */
export function foodLike(f: Frame, plate: Ellipse, model: ColourModel): Mask {
  const { width: W, height: H } = f;
  const D = dist(model.food, model.plate);
  const m = new Uint8Array(W * H);
  if (D < 22) return m; // food and plate are the same colour: colour cannot find it
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (ellipseRho(plate, x, y) > 0.93) continue;
      const i = (y * W + x) * 4;
      const c: RGB = [f.data[i], f.data[i + 1], f.data[i + 2]];
      const df = dist(c, model.food);
      if (df < 0.62 * D && df < 0.75 * dist(c, model.plate)) m[y * W + x] = 1;
    }
  }
  return majority(m, W, H);
}

/** Where to prompt the model for leftovers: the larger food-coloured patches. */
export function colourSeeds(f: Frame, plate: Ellipse, model: ColourModel, max = 5): Component[] {
  const like = foodLike(f, plate, model);
  return components(like, f.width, f.height, Math.max(12, 0.004 * ellipseArea(plate))).slice(0, max);
}

/**
 * Fallback when the model is unavailable: food is whatever differs from the
 * plate's own rim colour. Crude, and meant to be corrected with the brush.
 */
export function classicalFood(f: Frame, plate: Ellipse): Mask {
  const { width: W, height: H } = f;
  const ring: number[][] = [[], [], []];
  for (let y = 0; y < H; y += 2) {
    for (let x = 0; x < W; x += 2) {
      const r = ellipseRho(plate, x, y);
      if (r < 0.84 || r > 0.98) continue;
      const i = (y * W + x) * 4;
      for (let c = 0; c < 3; c++) ring[c].push(f.data[i + c]);
    }
  }
  const med = ring.map((v) => (v.length ? [...v].sort((p, q) => p - q)[v.length >> 1] : 128)) as RGB;
  const d = new Float32Array(W * H);
  const hist = new Array(256).fill(0);
  let total = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (ellipseRho(plate, x, y) > 0.8) continue;
      const i = (y * W + x) * 4;
      const v = dist([f.data[i], f.data[i + 1], f.data[i + 2]], med);
      d[y * W + x] = v;
      hist[Math.min(255, Math.round(v))]++;
      total++;
    }
  }
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let wB = 0, sumB = 0, best = 0, t = 40;
  for (let i = 0; i < 256; i++) {
    wB += hist[i];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += i * hist[i];
    const v = wB * wF * (sumB / wB - (sum - sumB) / wF) ** 2;
    if (v > best) { best = v; t = i; }
  }
  t = Math.min(110, Math.max(30, t));
  const m = new Uint8Array(W * H);
  for (let i = 0; i < m.length; i++) if (d[i] > t) m[i] = 1;
  const clean = majority(m, W, H);
  const out = new Uint8Array(W * H);
  for (const c of components(clean, W, H, Math.max(12, 0.004 * ellipseArea(plate)))) for (const i of c.pixels) out[i] = 1;
  return out;
}

// ---------------------------------------------------------------- composing and measuring
/** Model selection plus brush strokes, minus eraser strokes, kept inside the plate. */
export function composeMask(base: Mask, add: Mask, erase: Mask, plate: Ellipse, W: number, H: number): Mask {
  const out = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      if ((base[i] || add[i]) && !erase[i] && ellipseRho(plate, x, y) <= 1) out[i] = 1;
    }
  }
  return out;
}

/** Food as a share of the plate's area, which does not depend on how close the camera was. */
export function plateShare(food: Mask, plate: Ellipse): number {
  const area = ellipseArea(plate);
  return area > 0 ? Math.min(1, maskArea(food) / area) : 0;
}

export type LeftoverFlag = "no_food_before" | "more_after_than_before" | "plate_nearly_full";

export interface Leftover {
  grams: number;
  sd_g: number;
  lo_g: number;
  hi_g: number;
  ratio: number; // after share / before share
  exponent: number;
  flags: LeftoverFlag[];
}

// How mass scales with visible area. Food spread flat scales with area
// (exponent 1); a mound that shrinks in every direction scales with area^1.5.
// We do not know which, so the answer is the middle and the range is both ends.
export const EXPONENT_FLAT = 1.0;
export const EXPONENT_MOUND = 1.5;
export const EXPONENT = 1.25;
const OUTLINE_ERROR = 0.14; // relative error in grams from two imperfect outlines

export function estimateLeftover(a: { shareBefore: number; shareAfter: number; referenceG: number }): Leftover {
  const flags: LeftoverFlag[] = [];
  if (a.shareBefore < 0.015) {
    flags.push("no_food_before");
    return { grams: 0, sd_g: Math.max(5, Math.round(a.referenceG / 2)), lo_g: 0, hi_g: a.referenceG, ratio: 0, exponent: EXPONENT, flags };
  }
  if (a.shareBefore > 0.9) flags.push("plate_nearly_full");
  const raw = a.shareAfter / a.shareBefore;
  if (raw > 1.08) flags.push("more_after_than_before");
  const r = Math.min(1, Math.max(0, raw));
  const mid = a.referenceG * Math.pow(r, EXPONENT);
  const flat = a.referenceG * Math.pow(r, EXPONENT_FLAT);
  const mound = a.referenceG * Math.pow(r, EXPONENT_MOUND);
  const sd = Math.sqrt(((flat - mound) / 2) ** 2 + (OUTLINE_ERROR * mid) ** 2 + 9);
  return { grams: Math.round(mid), sd_g: Math.max(5, Math.round(sd)), lo_g: Math.round(mound), hi_g: Math.round(flat), ratio: raw, exponent: EXPONENT, flags };
}

export const LEFTOVER_FLAG_TEXT: Record<LeftoverFlag, string> = {
  no_food_before: "No food is outlined in the before photo, so there is nothing to compare with. Tap the food in the before photo.",
  more_after_than_before: "The after photo shows more food than the before photo. Check both outlines, and that seconds were not added in between.",
  plate_nearly_full: "The plate was almost completely covered, where area says little about amount. Treat this as rough, or weigh it.",
};

// ---------------------------------------------------------------- when there is only an "after" photo
// Every confirmed "before" photo is also a labelled example: this share of the
// plate was this many grams of this food. Enough of those give a curve that can
// read an "after" photo on its own, with a wider uncertainty.
export interface CalPoint {
  coverage: number;
  grams: number;
}
export interface Fit {
  k: number;
  p: number; // grams = k * coverage^p
  n: number;
  rmse_g: number | null; // leave-one-out, needs at least 3 points
}

function fitPower(points: CalPoint[]): { k: number; p: number } | null {
  const pts = points.filter((q) => q.coverage > 0.003 && q.grams > 0);
  if (!pts.length) return null;
  const spread = Math.max(...pts.map((q) => q.coverage)) / Math.min(...pts.map((q) => q.coverage));
  if (pts.length < 3 || spread < 1.5) {
    // too little to fit a curve: use the middle exponent and fit only the scale
    const k = Math.exp(pts.reduce((s, q) => s + Math.log(q.grams) - EXPONENT * Math.log(q.coverage), 0) / pts.length);
    return { k, p: EXPONENT };
  }
  const xs = pts.map((q) => Math.log(q.coverage));
  const ys = pts.map((q) => Math.log(q.grams));
  const mx = xs.reduce((s, v) => s + v, 0) / xs.length;
  const my = ys.reduce((s, v) => s + v, 0) / ys.length;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < xs.length; i++) { sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) ** 2; }
  const p = Math.min(2, Math.max(0.7, sxx > 1e-9 ? sxy / sxx : EXPONENT));
  return { k: Math.exp(my - p * mx), p };
}

export function fitCalibration(points: CalPoint[]): Fit | null {
  const f = fitPower(points);
  if (!f) return null;
  const usable = points.filter((q) => q.coverage > 0.003 && q.grams > 0);
  let rmse: number | null = null;
  if (usable.length >= 3) {
    let se = 0;
    for (let i = 0; i < usable.length; i++) {
      const fi = fitPower(usable.filter((_, j) => j !== i));
      if (fi) se += (fi.k * Math.pow(usable[i].coverage, fi.p) - usable[i].grams) ** 2;
    }
    rmse = Math.sqrt(se / usable.length);
  }
  return { ...f, n: usable.length, rmse_g: rmse };
}

export function estimateFromCalibration(fit: Fit, coverage: number): { grams: number; sd_g: number } {
  if (coverage <= 0) return { grams: 0, sd_g: 5 };
  const g = fit.k * Math.pow(coverage, fit.p);
  const sd = fit.rmse_g === null ? Math.max(10, 0.3 * g) : Math.sqrt(fit.rmse_g ** 2 + (0.18 * g) ** 2);
  return { grams: Math.round(g), sd_g: Math.max(6, Math.round(sd)) };
}
