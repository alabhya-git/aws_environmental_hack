Our first photo feature looked at a steel bowl of rice and reported that the bowl was 0% covered.

It was not a model failure. It was a design failure, and fixing it led to a version that runs a segmentation model entirely in the browser, never uploads a photo, and was debugged by one real picture that broke three things our generated test images had been hiding.

Some context. ProofPlate is our Environmental Hacks project (Waste and Energy track): it suggests the first portion for each diner in a college mess and learns from what comes back on the plate. The backend runs on AWS Lambda, API Gateway and DynamoDB. Leftovers are normally weighed, but a serving counter does not always have a scale free, so we wanted a photo option. Most of the code was written with Claude Code from our specification; the design decisions and the bugs below are the story.

## Version one: correct maths, wrong assumption

The first design was classical: photograph the empty plate once, then compare later photos against it and call the changed pixels food.

It worked on our test images. Then a teammate uploaded a photo of rice as the first photo. The app treated it as the empty-plate reference, compared it with itself, and reported 0%. The design assumed a fixed camera and a careful setup order. People at a counter have a phone and a plate.

So we deleted it and started from what the user actually has: a photo before eating and a photo after.

## The before photo is the calibration

The key realisation is that the before photo is not just a picture. It shows a plate whose weight **we already know**, because the serving was recorded in scoops of a calibrated scoop. That makes it a labelled example for free:

1. Find the plate and the food in each photo.
2. Express the food as a share of the plate's area. Dividing by the plate cancels out how close the camera was.
3. Scale the known weight by how much that share shrank.

No reference shot, no calibration step, no fixed camera.

## Segment Anything, in a web worker

For outlining we use SlimSAM, a compressed Segment Anything model: about 14 MB quantised, Apache-2.0. It runs in the browser with Transformers.js on ONNX Runtime Web, inside a web worker so the page stays responsive.

```ts
const raw = new T.RawImage(new Uint8ClampedArray(m.rgba), m.width, m.height, 4).rgb();
const inputs = await processor(raw);
const emb = await model.get_image_embeddings(inputs);   // once per photo, a few seconds
cache.set(m.id, { inputs, emb, w: m.width, h: m.height });
```

The expensive step, the image embedding, happens once per photo and takes about five seconds on a laptop. After that, asking "what is at this point?" takes 150 to 280 ms, which is fast enough for tap-to-correct.

Running it in the browser was a product decision, not only a technical one. A photo of someone's plate never leaves their device. The API receives numbers: grams, an uncertainty, and the two area shares.

It also solved an AWS constraint. Our web app is served by Lambda, and a Lambda response is capped at 6 MB, so a 14 MB model could not come from our own origin anyway. The browser fetches it once from a public CDN and caches it.

## The model proposes, the app decides

Segment Anything does not know what food is. Give it one point and it returns three candidate outlines at different scales. For a tap on the rice in our test photo, they covered 77%, 54% and 23% of the image: the whole bowl, the inside of the bowl, and the rice.

Choosing between them is our code, and it is plain geometry and colour:

- **The plate is the largest *round* outline.** We fit an ellipse to each candidate and measure how well it matches.
- **The food is the outline that separates most cleanly in colour** from the rest of the plate: the distance between the two mean colours over their combined spread. Rice against steel scores high. "Half the bowl, rice and metal together" scores low.

```ts
const round = ok.filter((o) => o.fit >= 0.88);   // a rectangle scores about 0.84
const pool = (round.length ? round : ok).sort((x, y) => y.cand.area - x.cand.area);
```

## One real photo, three bugs

All of this passed on generated images: flat-coloured plates with tidy discs of "rice". Then we cropped that real photo of rice in a steel bowl and ran it through the actual app.

**Bug 1: reflections are as white as rice.** In the after photo we look for patches that match the food's colour from the before photo. On steel, specular highlights matched perfectly. The model's outlines around those highlights were half the bowl, and we accepted them. The same photo that read 31% as "before" read **91%** as "after". Fix: an automatic guess must earn its place. It is kept only if it separates cleanly and its average colour is closer to the food than to the plate.

**Bug 2: the biggest outline is not always the plate.** We simulated a more distant shot by shrinking the photo onto a blank canvas. The pasted rectangle became the largest outline, got chosen as the plate, and the bowl became the "food". The ratio came out at 2.20 instead of 1. Real photos do not contain pasted rectangles, but they do contain trays and placemats. Fix: the roundness test above.

**Bug 3: the answer depended on where we asked.** The same photo read 31.1% in one slot and 36.3% in the other, because the outline varies with the prompt point, and the two slots prompt from different places. A 17% disagreement on identical pixels.

Our first fix for that was wrong, and instructive. We grew each outline over neighbouring food-coloured pixels. It looked perfect on generated images. On the real photo it crept along the bright rim of the bowl, the exact reflection problem again, this time as a refinement.

The fix that worked: ask the model a second time, with three points spread across its first answer. That converges on the whole piece wherever the first point fell. The same photo then read **34.6%** and **34.2%** in the two slots, and **34.9%** in the simulated distant shot.

## Area is not mass

A photo cannot see depth. A flat layer and a tall mound with the same footprint look identical from above. So we refuse to report one confident number:

```
grams left = grams before × (share after / share before) ^ 1.25
```

If food is spread flat, mass shrinks with area (exponent 1.0). If a mound shrinks in every direction, mass shrinks faster (exponent 1.5). The estimate uses the middle and the interface states both ends: "36 g, between 26 g if it was a mound and 52 g if it was spread flat".

That uncertainty is not decoration. It is saved with the record, and it widens the interval our Bayesian appetite model learns from. A photo teaches the model less sharply than a scale does, automatically.

## When the outline is wrong, the user fixes it

Automatic outlining will sometimes be wrong, so correction is a first-class feature: tap food to add it, tap something to exclude it, step to a smaller or larger outline, paint or erase with a brush, drag the plate ellipse, undo. If the model cannot be downloaded at all, the same tools work by hand from a rough colour guess, and a scripted browser test blocks the CDN to prove it.

## What we have not shown

- **The grams have not been checked against a scale.** No real before-and-after pair with weighed leftovers has been tested. On a generated pair the estimate was 36 g ± 14 against a generated truth of 27 g.
- Our real-photo evidence is one photograph. It shows the outlines are consistent, not that the grams are right.
- Food the colour of the plate, mixed dishes and steep camera angles are untested. Phones will be slower than a laptop.

## What we took away

- **Generated test data tests your assumptions, not the world.** Every one of the three bugs was invisible until a real photo arrived. Get one real sample before calling anything done.
- **Let the model propose and write the decision yourself.** The parts that made this work, roundness and colour separation, are a few dozen lines of arithmetic we can test and explain.
- **Be suspicious of a fix that only looks good on clean inputs.** The colour-growing refinement was elegant and wrong.
- **Report the range.** An honest "26 to 52 g" is more useful than a precise number nobody should trust.

Try it: https://imh92ymqaa.execute-api.ap-south-1.amazonaws.com (open the demo, serve a plate, and use "Leftovers from two photos" with your own plate).
Code and model card: https://github.com/alabhya-git/aws_environmental_hack
