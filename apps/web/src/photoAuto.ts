// Orchestrates "find the plate and the food in this photo": asks the model
// for outlines and uses photo.ts to decide between them. Every step has a
// model-free fallback so the feature still works offline, by hand.
import {
  applyGain, Candidate, classicalFood, colourSeeds, colourStats, ColourModel, components, contains, defaultPlate,
  dist, Ellipse, ellipseArea, ellipseMask, ellipseRho, fitEllipse, Frame, gainFrom, Mask, MIN_SEPARATION, pickPlate, Point, rankFood, RGB,
} from "./photo";
import { outlines } from "./sam";

/** What the "before" photo taught us about this plate and this food. */
export interface PlateColours {
  food: RGB;
  plate: RGB;
  ring: RGB; // the plate's rim, used to compare lighting between the two photos
}

/** One piece of food the user (or the automatic pass) pointed at. */
export interface FoodObject {
  point: Point;
  cands: Candidate[];
  order: number[]; // usable candidates, smallest first
  pick: number; // position in `order`
  fixed?: Mask; // used instead of a model outline (colour patch or magic-wand fill)
  guess?: boolean; // the rough by-hand starting guess, recomputed when the plate outline moves
}

export interface Analysis {
  plate: Ellipse;
  plateFrom: "model" | "default";
  objects: FoodObject[];
  negatives: Point[];
  engine: "model" | "by-hand";
  note: string | null;
}

export function ringMask(plate: Ellipse, W: number, H: number): Mask {
  const m = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const r = ellipseRho(plate, x, y);
    if (r >= 0.84 && r <= 0.97) m[y * W + x] = 1;
  }
  return m;
}

/** Share of the smaller region that lies inside the other: 1 means the same piece found twice. */
function overlap(x: Mask | null, y: Mask): number {
  if (!x) return 0;
  let both = 0, nx = 0, ny = 0;
  for (let i = 0; i < y.length; i++) { nx += x[i]; ny += y[i]; if (x[i] && y[i]) both++; }
  return both / Math.max(1, Math.min(nx, ny));
}

export function objectMask(o: FoodObject): Mask | null {
  if (o.fixed) return o.fixed;
  const idx = o.order[o.pick];
  return idx === undefined ? null : o.cands[idx].mask;
}

export function unionMask(objects: FoodObject[], W: number, H: number): Mask {
  const out = new Uint8Array(W * H);
  for (const o of objects) {
    const m = objectMask(o);
    if (m) for (let i = 0; i < out.length; i++) if (m[i]) out[i] = 1;
  }
  return out;
}

/**
 * Turn the model's candidates around a point into a food object.
 * `strict` is for automatic guesses, which must earn their place: the outline
 * has to stand clearly apart from the plate and, when the food's colour is
 * known, has to look more like the food than like the plate. A tap from the
 * user is not second-guessed this way.
 */
function build(frame: Frame, point: Point, cands: Candidate[], negatives: Point[], plate: Ellipse, colour: ColourModel | null, keepArea?: number, strict = false): FoodObject | null {
  const { width: W, height: H } = frame;
  const plateMask = ellipseMask(plate, W, H);
  const ranked = rankFood(frame, cands, { point, negatives, plateMask, plateArea: ellipseArea(plate), colour });
  if (!ranked.length) return null;
  if (strict) {
    if (ranked[0].score < MIN_SEPARATION) return null;
    if (colour) {
      const mean = colourStats(frame, cands[ranked[0].index].mask).mean;
      if (dist(mean, colour.food) > 0.8 * dist(mean, colour.plate)) return null;
    }
  }
  const order = ranked.map((r) => r.index).sort((p, q) => cands[p].area - cands[q].area);
  let want = ranked[0].index;
  if (keepArea) {
    // the user chose a size for this piece earlier: stay as close to it as the new outlines allow
    want = order.reduce((best, i) => (Math.abs(cands[i].area - keepArea) < Math.abs(cands[best].area - keepArea) ? i : best), order[0]);
  }
  return { point, cands, order, pick: order.indexOf(want) };
}

/** Three points spread through a region: its middle and one towards each end of its long axis. */
export function spreadPoints(mask: Mask, W: number, H: number): Point[] {
  const e = fitEllipse(mask, W, H);
  if (!e) return [];
  const snap = (x: number, y: number): Point | null => {
    // nearest pixel of the region, searched outwards in rings
    for (let r = 0; r < 40; r += 2) {
      for (let k = 0; k < Math.max(1, r * 3); k++) {
        const t = (k / Math.max(1, r * 3)) * Math.PI * 2;
        const px = Math.round(x + r * Math.cos(t)), py = Math.round(y + r * Math.sin(t));
        if (px >= 0 && py >= 0 && px < W && py < H && mask[py * W + px]) return { x: px, y: py };
      }
    }
    return null;
  };
  const c = Math.cos(e.theta), sn = Math.sin(e.theta);
  const pts = [snap(e.cx, e.cy), snap(e.cx + 0.5 * e.a * c, e.cy + 0.5 * e.a * sn), snap(e.cx - 0.5 * e.a * c, e.cy - 0.5 * e.a * sn)];
  return pts.filter((p): p is Point => !!p);
}

/**
 * The model's outline depends on the single point it was asked about. Asking
 * again with points spread across the first answer gives an outline of the
 * whole piece that is much less sensitive to where the first point fell.
 */
async function refine(id: string, frame: Frame, obj: FoodObject, plate: Ellipse, colour: ColourModel | null, negatives: Point[] = []): Promise<FoodObject> {
  const first = objectMask(obj);
  if (!first) return obj;
  const pts = spreadPoints(first, frame.width, frame.height);
  if (pts.length < 2) return obj;
  const cands = await outlines(id, [...pts, ...negatives], [...pts.map(() => 1), ...negatives.map(() => 0)]);
  const next = build(frame, pts[0], cands, negatives, plate, colour, undefined, true);
  const got = next ? objectMask(next) : null;
  if (!next || !got) return obj;
  const a0 = first.reduce((n, v) => n + v, 0), a1 = got.reduce((n, v) => n + v, 0);
  return a1 >= 0.7 * a0 && a1 <= 1.8 * a0 ? { ...next, point: obj.point } : obj;
}

async function plateAround(id: string, frame: Frame, p: Point): Promise<{ plate: Ellipse; cands: Candidate[]; found: boolean }> {
  const { width: W, height: H } = frame;
  const cands = await outlines(id, [p], [1]);
  const best = pickPlate(cands, W, H, p);
  return { plate: best ? best.ellipse : defaultPlate(W, H), cands, found: !!best };
}

/** First pass over a photo with the model. `learned` is given for an "after" photo. */
export async function analyse(id: string, frame: Frame, learned: PlateColours | null): Promise<Analysis> {
  const { width: W, height: H } = frame;
  const centre = { x: W / 2, y: H / 2 };
  const { plate, cands, found } = await plateAround(id, frame, centre);
  const a: Analysis = { plate, plateFrom: found ? "model" : "default", objects: [], negatives: [], engine: "model", note: found ? null : "The plate was not found automatically. Use Plate to place the outline on the rim." };

  if (learned) {
    // An "after" photo: look for patches the colour of the food we saw before.
    const ringNow = colourStats(frame, ringMask(plate, W, H)).mean;
    const model = applyGain({ food: learned.food, plate: learned.plate }, gainFrom(learned.ring, ringNow));
    if (dist(model.food, model.plate) < 22) {
      a.note = "This food is close to the plate's colour, so it cannot be found automatically. Tap any food that is left.";
      return a;
    }
    for (const seed of colourSeeds(frame, plate, model)) {
      const c = await outlines(id, [seed.seed], [1]);
      // a reflection on a steel plate is as bright as rice: only keep a patch the model can outline cleanly
      const obj = build(frame, seed.seed, c, [], plate, model, undefined, true);
      const got = obj ? objectMask(obj) : null;
      if (obj && got && !a.objects.some((o) => overlap(objectMask(o), got) > 0.6)) a.objects.push(await refine(id, frame, obj, plate, model));
    }
    if (!a.objects.length) a.note = "No food like the before photo was found, so this reads as an empty plate. Tap any food that was missed.";
    return a;
  }

  // A "before" photo: the food is normally in the middle of the plate.
  const plateMask = ellipseMask(plate, W, H);
  const ranked = rankFood(frame, cands, { point: centre, negatives: [], plateMask, plateArea: ellipseArea(plate) });
  if (ranked.length && ranked[0].score >= MIN_SEPARATION) {
    const obj = build(frame, centre, cands, [], plate, null);
    if (obj) a.objects.push(await refine(id, frame, obj, plate, null));
  } else {
    // nothing clear in the middle: try the largest patch that differs from the plate's rim
    const guess = components(classicalFood(frame, plate), W, H, Math.max(12, 0.01 * ellipseArea(plate)))[0];
    if (guess) {
      const c = await outlines(id, [guess.seed], [1]);
      const obj = build(frame, guess.seed, c, [], plate, null, undefined, true);
      if (obj) a.objects.push(await refine(id, frame, obj, plate, null));
    }
  }
  if (!a.objects.length) a.note = "The food was not found automatically. Tap the food to outline it.";
  return a;
}

/** No model available: a rough colour-based guess to be corrected by hand. */
export function analyseByHand(frame: Frame): Analysis {
  const { width: W, height: H } = frame;
  const plate = defaultPlate(W, H);
  const fixed = classicalFood(frame, plate);
  return {
    plate, plateFrom: "default", negatives: [], engine: "by-hand",
    objects: [{ point: { x: W / 2, y: H / 2 }, cands: [], order: [], pick: 0, fixed, guess: true }],
    note: "Automatic outlining is not available right now. Place the plate outline, then tap or brush the food.",
  };
}

/** By hand, the starting guess depends on where the plate is: redo it when the outline is moved. */
export function reguess(frame: Frame, a: Analysis): Analysis {
  if (a.engine !== "by-hand" || !a.objects.some((o) => o.guess)) return a;
  const fixed = classicalFood(frame, a.plate);
  return { ...a, objects: a.objects.map((o) => (o.guess ? { ...o, fixed } : o)) };
}

/** Magic-wand fill from a tapped pixel, for when the model is unavailable. */
export function wand(frame: Frame, p: Point, plate: Ellipse, tol = 34): Mask {
  const { width: W, height: H, data } = frame;
  const out = new Uint8Array(W * H);
  const x0 = Math.round(p.x), y0 = Math.round(p.y);
  if (x0 < 0 || y0 < 0 || x0 >= W || y0 >= H) return out;
  let n = 0;
  const ref = [0, 0, 0];
  for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
    const x = Math.min(W - 1, Math.max(0, x0 + dx)), y = Math.min(H - 1, Math.max(0, y0 + dy));
    for (let c = 0; c < 3; c++) ref[c] += data[(y * W + x) * 4 + c];
    n++;
  }
  const mean: RGB = [ref[0] / n, ref[1] / n, ref[2] / n];
  const stack = [y0 * W + x0];
  out[y0 * W + x0] = 1;
  while (stack.length) {
    const i = stack.pop()!;
    const x = i % W, y = (i - x) / W;
    for (const j of [x > 0 ? i - 1 : -1, x < W - 1 ? i + 1 : -1, y > 0 ? i - W : -1, y < H - 1 ? i + W : -1]) {
      if (j < 0 || out[j]) continue;
      const jx = j % W, jy = (j - jx) / W;
      if (ellipseRho(plate, jx, jy) > 1) continue;
      if (dist([data[j * 4], data[j * 4 + 1], data[j * 4 + 2]], mean) > tol) continue;
      out[j] = 1;
      stack.push(j);
    }
  }
  return out;
}

/** The user tapped a piece of food to include. */
export async function addFood(id: string, frame: Frame, a: Analysis, p: Point, colour: ColourModel | null): Promise<Analysis> {
  if (a.engine === "by-hand") {
    return { ...a, objects: [...a.objects, { point: p, cands: [], order: [], pick: 0, fixed: wand(frame, p, a.plate) }] };
  }
  const cands = await outlines(id, [p, ...a.negatives], [1, ...a.negatives.map(() => 0)]);
  let obj = build(frame, p, cands, a.negatives, a.plate, colour);
  if (obj) obj = await refine(id, frame, obj, a.plate, colour, a.negatives);
  if (!obj) {
    // the model only offered the whole plate here: fall back to a colour fill from the tap
    obj = { point: p, cands: [], order: [], pick: 0, fixed: wand(frame, p, a.plate) };
  }
  return { ...a, objects: [...a.objects, obj], note: null };
}

/** The user tapped something that is not food. Every piece is re-outlined with that in mind. */
export async function excludeHere(id: string, frame: Frame, a: Analysis, p: Point, colour: ColourModel | null): Promise<Analysis> {
  const negatives = [...a.negatives, p];
  const { width: W, height: H } = frame;
  if (a.engine === "by-hand") {
    const hole = wand(frame, p, a.plate);
    return { ...a, negatives, objects: a.objects.map((o) => (o.fixed ? { ...o, fixed: o.fixed.map((v, i) => (hole[i] ? 0 : v)) as Mask } : o)) };
  }
  const objects: FoodObject[] = [];
  for (const o of a.objects) {
    if (o.fixed) {
      if (!contains(o.fixed, W, H, p, 0)) objects.push(o);
      else {
        const hole = wand(frame, p, a.plate, 26);
        objects.push({ ...o, fixed: o.fixed.map((v, i) => (hole[i] ? 0 : v)) as Mask });
      }
      continue;
    }
    const prev = objectMask(o);
    const cands = await outlines(id, [o.point, ...negatives], [1, ...negatives.map(() => 0)]);
    const next = build(frame, o.point, cands, negatives, a.plate, colour, prev ? prev.reduce((s, v) => s + v, 0) : undefined);
    if (next) objects.push(next);
  }
  return { ...a, negatives, objects, note: null };
}

/** Re-find the plate from a tap on it. */
export async function plateHere(id: string, frame: Frame, a: Analysis, p: Point): Promise<Analysis> {
  const r = await plateAround(id, frame, p);
  return r.found ? { ...a, plate: r.plate, plateFrom: "model", note: null } : { ...a, note: "No plate was found there. Drag the outline and its handles instead." };
}

/** Step the last piece to the next smaller or larger outline the model offered. */
export function resizeLast(a: Analysis, dir: -1 | 1): Analysis {
  const k = [...a.objects].reverse().findIndex((o) => !o.fixed && o.order.length > 1);
  if (k < 0) return a;
  const i = a.objects.length - 1 - k;
  const o = a.objects[i];
  const pick = Math.min(o.order.length - 1, Math.max(0, o.pick + dir));
  if (pick === o.pick) return a;
  const objects = a.objects.slice();
  objects[i] = { ...o, pick };
  return { ...a, objects };
}

export function canResize(a: Analysis): { smaller: boolean; larger: boolean } {
  const o = [...a.objects].reverse().find((x) => !x.fixed && x.order.length > 1);
  return { smaller: !!o && o.pick > 0, larger: !!o && o.pick < o.order.length - 1 };
}
