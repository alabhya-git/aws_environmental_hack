// node --test src/photo.test.mjs   (Node 24 strips types natively)
import assert from "node:assert/strict";
import test from "node:test";
import {
  applyGain, classicalFood, colourSeeds, colourStats, componentAt, components, composeMask, ellipseArea, ellipseMask,
  estimateFromCalibration, estimateLeftover, fitCalibration, fitEllipse, gainFrom, maskArea, pickPlate, plateShare, rankFood, roundness,
  separation, toCandidates, MIN_SEPARATION,
} from "./photo.ts";

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

const STEEL = [150, 154, 158];
const RICE = [236, 232, 220];
const TABLE = [58, 46, 38];

/** A top-down scene: table, an elliptical steel plate, and food discs given relative to the plate. */
function scene({ W = 240, H = 200, zoom = 1, dx = 0, dy = 0, food = [], gain = 1, rice = RICE, seed = 1, tilt = 1 }) {
  const rand = rng(seed);
  const plate = { cx: W / 2 + dx, cy: H / 2 + dy, a: 80 * zoom, b: 80 * zoom * tilt, theta: 0 };
  const data = new Uint8ClampedArray(W * H * 4);
  const foodMask = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      const u = (x - plate.cx) / plate.a;
      const v = (y - plate.cy) / plate.b;
      let c = u * u + v * v <= 1 ? STEEL : TABLE;
      for (const [fx, fy, fr] of food) {
        if ((u - fx) ** 2 + (v - fy) ** 2 <= fr * fr) { c = rice; foodMask[i] = 1; }
      }
      for (let k = 0; k < 3; k++) data[i * 4 + k] = c[k] * gain + (rand() - 0.5) * 10;
      data[i * 4 + 3] = 255;
    }
  }
  return { frame: { width: W, height: H, data }, plate, foodMask, W, H };
}

test("an ellipse is recovered from a filled region, including when rotated", () => {
  const truth = { cx: 130, cy: 96, a: 70, b: 40, theta: 0.5 };
  const got = fitEllipse(ellipseMask(truth, 260, 200), 260, 200);
  assert.ok(Math.abs(got.cx - truth.cx) < 1 && Math.abs(got.cy - truth.cy) < 1);
  assert.ok(Math.abs(got.a - truth.a) / truth.a < 0.03, `a ${got.a}`);
  assert.ok(Math.abs(got.b - truth.b) / truth.b < 0.03, `b ${got.b}`);
  assert.ok(Math.abs(got.theta - truth.theta) < 0.03, `theta ${got.theta}`);
  assert.ok(Math.abs(ellipseArea(got) - maskArea(ellipseMask(truth, 260, 200))) / ellipseArea(truth) < 0.04);
  assert.equal(fitEllipse(new Uint8Array(100), 10, 10), null);
});

test("food as a share of the plate does not depend on camera distance, position or tilt", () => {
  const food = [[0.1, -0.1, 0.45]];
  const near = scene({ food, zoom: 1.15, dx: -8, seed: 2 });
  const far = scene({ food, zoom: 0.7, dx: 20, dy: 10, seed: 3 });
  const tilted = scene({ food, zoom: 0.9, tilt: 0.7, seed: 4 });
  const shares = [near, far, tilted].map((s) => plateShare(s.foodMask, s.plate));
  for (const s of shares) assert.ok(Math.abs(s - 0.45 ** 2) < 0.012, `share ${s}`);
});

function candidatesFor(s) {
  const { W, H, plate, foodMask } = s;
  const bowl = ellipseMask(plate, W, H);
  const inner = ellipseMask({ ...plate, a: plate.a * 0.8, b: plate.b * 0.8 }, W, H); // "inside of the bowl", food and metal together
  const buf = new Uint8Array(3 * W * H);
  buf.set(bowl, 0); buf.set(inner, W * H); buf.set(foodMask, 2 * W * H);
  return toCandidates(buf, 3, W, H, [0.97, 0.86, 0.89]);
}

test("from one tap on the food: the plate is the largest outline and the food is the cleanly separated one", () => {
  const s = scene({ food: [[0, 0.05, 0.5]], seed: 5 });
  const cands = candidatesFor(s);
  const tap = { x: s.plate.cx, y: s.plate.cy };
  const choice = pickPlate(cands, s.W, s.H, tap);
  const plate = choice.cand;
  assert.equal(plate, cands[0]);
  assert.ok(Math.abs(choice.ellipse.a - 80) < 3 && Math.abs(choice.ellipse.b - 80) < 3 && choice.fit > 0.95);
  const ranked = rankFood(s.frame, cands, { point: tap, negatives: [], plateMask: plate.mask, plateArea: plate.area });
  assert.equal(ranked[0].index, 2, "the rice, not the bowl interior");
  assert.ok(ranked[0].score > MIN_SEPARATION);
  assert.ok(ranked.every((r) => r.index !== 0), "the plate itself is never offered as food");
  assert.ok(separation(s.frame, cands[2].mask, plate.mask) > 2 * separation(s.frame, cands[1].mask, plate.mask));
});

test("a 'not food' tap rules out any outline that covers it", () => {
  const s = scene({ food: [[0, 0.05, 0.5]], seed: 6 });
  const cands = candidatesFor(s);
  const tap = { x: s.plate.cx, y: s.plate.cy };
  const metal = { x: s.plate.cx + 0.7 * s.plate.a, y: s.plate.cy }; // inside the bowl interior, outside the rice
  const ranked = rankFood(s.frame, cands, { point: tap, negatives: [metal], plateMask: cands[0].mask, plateArea: cands[0].area });
  assert.deepEqual(ranked.map((r) => r.index), [2]);
  const onFood = rankFood(s.frame, cands, { point: tap, negatives: [tap], plateMask: cands[0].mask, plateArea: cands[0].area });
  assert.equal(onFood.length, 0);
});

test("a slightly smaller but clearly more confident outline is preferred as the plate", () => {
  const s = scene({ food: [], seed: 7 });
  const bowl = ellipseMask(s.plate, s.W, s.H);
  const sprawl = bowl.slice();
  for (let y = 0; y < 30; y++) for (let x = 0; x < s.W; x++) sprawl[y * s.W + x] = 1; // swallowed a neighbouring object
  const buf = new Uint8Array(2 * s.W * s.H);
  buf.set(sprawl, 0); buf.set(bowl, s.W * s.H);
  const cands = toCandidates(buf, 2, s.W, s.H, [0.9, 0.95]);
  assert.equal(pickPlate(cands, s.W, s.H, { x: s.plate.cx, y: s.plate.cy })?.cand.iou, 0.95);
  assert.equal(pickPlate(cands, s.W, s.H, { x: 2, y: 190 }), null, "a tap off the plate finds no plate");
});

test("a tray or placemat around the plate is not mistaken for the plate, because it is not round", () => {
  const s = scene({ W: 300, H: 240, food: [[0, 0, 0.45]], seed: 17 });
  const tray = new Uint8Array(s.W * s.H);
  for (let y = 12; y < 228; y++) for (let x = 20; x < 280; x++) tray[y * s.W + x] = 1;   // a rectangle holding the plate
  const bowl = ellipseMask(s.plate, s.W, s.H);
  assert.ok(roundness(tray, s.W, s.H).fit < 0.88 && roundness(bowl, s.W, s.H).fit > 0.95);
  const buf = new Uint8Array(3 * s.W * s.H);
  buf.set(tray, 0); buf.set(bowl, s.W * s.H); buf.set(s.foodMask, 2 * s.W * s.H);
  const cands = toCandidates(buf, 3, s.W, s.H, [0.98, 0.93, 0.9]);
  const tap = { x: s.plate.cx, y: s.plate.cy };
  const choice = pickPlate(cands, s.W, s.H, tap);
  assert.equal(choice.cand, cands[1], "the round bowl, not the larger and more confident tray");
  const ranked = rankFood(s.frame, cands, { point: tap, negatives: [], plateMask: bowl, plateArea: ellipseArea(choice.ellipse) });
  assert.equal(ranked[0].index, 2);
  const square = toCandidates(tray, 1, s.W, s.H, [0.9]);
  assert.equal(pickPlate(square, s.W, s.H, tap).cand, square[0], "a square plate is still accepted when nothing round is on offer");
});

test("grams left scale with how much the food's share shrank, between the flat and mound assumptions", () => {
  const e = estimateLeftover({ shareBefore: 0.4, shareAfter: 0.1, referenceG: 180 });
  assert.equal(e.hi_g, 45);                         // spread flat: mass follows area
  assert.equal(e.lo_g, Math.round(180 * 0.25 ** 1.5)); // a mound: 22.5
  assert.ok(e.grams > e.lo_g && e.grams < e.hi_g);
  assert.ok(e.sd_g >= (e.hi_g - e.lo_g) / 2, "uncertainty covers both shapes");
  assert.deepEqual(e.flags, []);
  let last = -1;
  for (const after of [0, 0.05, 0.1, 0.2, 0.3, 0.4]) {
    const g = estimateLeftover({ shareBefore: 0.4, shareAfter: after, referenceG: 180 }).grams;
    assert.ok(g > last || (after === 0 && g === 0));
    last = g;
  }
  assert.equal(estimateLeftover({ shareBefore: 0.4, shareAfter: 0.4, referenceG: 180 }).grams, 180);
});

test("impossible or unusable photo pairs are flagged, not silently trusted", () => {
  const more = estimateLeftover({ shareBefore: 0.2, shareAfter: 0.3, referenceG: 150 });
  assert.ok(more.flags.includes("more_after_than_before"));
  assert.equal(more.grams, 150, "never reports more left than was on the plate");
  const none = estimateLeftover({ shareBefore: 0.0, shareAfter: 0.1, referenceG: 150 });
  assert.ok(none.flags.includes("no_food_before"));
  assert.ok(none.sd_g >= 50);
  assert.ok(estimateLeftover({ shareBefore: 0.95, shareAfter: 0.5, referenceG: 150 }).flags.includes("plate_nearly_full"));
});

test("leftovers are found by the food's colour from the before photo, under different light", () => {
  const before = scene({ food: [[0, 0, 0.55]], seed: 8 });
  const plateRegion = ellipseMask(before.plate, before.W, before.H);
  const model = { food: colourStats(before.frame, before.foodMask).mean, plate: colourStats(before.frame, before.foodMask, plateRegion, true).mean };
  const after = scene({ food: [[-0.4, 0.3, 0.16], [0.45, -0.2, 0.1]], zoom: 0.85, gain: 0.85, seed: 9 });
  const ring = colourStats(after.frame, after.foodMask, ellipseMask(after.plate, after.W, after.H), true).mean;
  const adjusted = applyGain(model, gainFrom(model.plate, ring));
  const seeds = colourSeeds(after.frame, after.plate, adjusted);
  assert.equal(seeds.length, 2);
  for (const c of seeds) assert.ok(after.foodMask[c.seed.y * after.W + c.seed.x], "each seed sits on leftover food");
  const empty = scene({ food: [], zoom: 0.85, gain: 0.85, seed: 10 });
  assert.equal(colourSeeds(empty.frame, empty.plate, adjusted).length, 0);
});

test("when food and plate are the same colour, colour finds nothing rather than guessing", () => {
  const s = scene({ food: [[0, 0, 0.5]], rice: [156, 158, 160], seed: 11 });
  assert.equal(colourSeeds(s.frame, s.plate, { food: [156, 158, 160], plate: STEEL }).length, 0);
});

test("the fallback without the model roughly finds bright food on a grey plate", () => {
  const s = scene({ food: [[0.1, 0, 0.45]], seed: 12 });
  const got = classicalFood(s.frame, s.plate);
  const truth = maskArea(s.foodMask);
  assert.ok(Math.abs(maskArea(got) - truth) / truth < 0.15, `${maskArea(got)} vs ${truth}`);
});

test("brush strokes add, the eraser removes, and nothing counts outside the plate", () => {
  const W = 100, H = 100;
  const plate = { cx: 50, cy: 50, a: 30, b: 30, theta: 0 };
  const base = new Uint8Array(W * H), add = new Uint8Array(W * H), erase = new Uint8Array(W * H);
  for (let y = 40; y < 60; y++) for (let x = 40; x < 60; x++) base[y * W + x] = 1;
  for (let y = 45; y < 55; y++) for (let x = 60; x < 99; x++) add[y * W + x] = 1;    // runs off the plate
  for (let y = 40; y < 50; y++) for (let x = 40; x < 60; x++) erase[y * W + x] = 1;
  const m = composeMask(base, add, erase, plate, W, H);
  assert.equal(m[55 * W + 50], 1);
  assert.equal(m[45 * W + 50], 0, "erased");
  assert.equal(m[50 * W + 70], 1, "brushed on, inside the plate");
  assert.equal(m[50 * W + 95], 0, "outside the plate");
  assert.equal(components(m, W, H).length, 1);
});

test("before photos double as calibration for reading an after photo on its own", () => {
  const pts = [0.1, 0.2, 0.3, 0.45].map((c) => ({ coverage: c, grams: 400 * c ** 1.3 }));
  const fit = fitCalibration(pts);
  assert.ok(Math.abs(fit.p - 1.3) < 0.05 && fit.rmse_g !== null);
  const e = estimateFromCalibration(fit, 0.25);
  assert.ok(Math.abs(e.grams - 400 * 0.25 ** 1.3) < 4);
  const thin = fitCalibration([{ coverage: 0.3, grams: 180 }]);
  assert.equal(thin.p, 1.25);
  assert.equal(thin.rmse_g, null);
  assert.ok(estimateFromCalibration(thin, 0.3).sd_g >= 50, "one example gives a wide uncertainty");
  assert.equal(fitCalibration([]), null);
});
