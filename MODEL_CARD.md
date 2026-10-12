# Model card — ProofPlate demand model and serving policy

Model `censored-lognormal-grid/1.0` · policy `first-portion/1.0` · planner `kitchen-plan/1.0`

## What is being estimated

For one diner, one food and one recipe: **D**, the amount the diner would choose to eat at this meal if seconds were easy to get.

D is a choice, observed in a mess hall. It is **not** a nutritional need, a calorie target or a medical measure, and nothing in the product treats it as one.

## Why not just average what people ate

A plate that comes back empty is ambiguous. The diner may have had exactly enough, or may still be hungry. A scale cannot tell these apart.

A learner that treats every empty plate as "that amount was right" will keep serving a hungry diner the same portion. In the simulator's low-uptake scenario (`seconds_friction`), that learner (`naive_average`) leaves less on the plate but more diners wanting more than the fixed conventional portion does. See `EVALUATION.md`.

ProofPlate asks one question when the plate comes back, "was that enough?", and uses the answer to decide what the plate is allowed to teach.

## How a plate becomes evidence

Implemented in `services/api/proofplate/domain/rules.py::classify`.

| What was recorded | Used as | Likelihood |
|---|---|---|
| "Enough", amount eaten known, no confounding reason | D was about what was eaten | interval: F(hi) − F(lo) |
| "Wanted more", amount eaten known | D was at least what was eaten | right-censored: 1 − F(lo) |
| "Not sure", food left, reason "had enough" | D was about what was eaten | interval |
| Empty plate, "not sure / not asked" | nothing | not used |
| Food left because of dislike, interruption or sharing | waste is recorded | not used |
| Food shared in unknown amounts | consumption unknown | not used |
| No return recorded | missing, never zero waste | not used |
| Declined the food | counted separately | not used |

"Wanted more" stays a lower bound whatever leftover reason is given. A right-censored plate can only move the estimate up (tested in `tests/test_model.py`).

The interval half-width comes from how the plate was measured: the scoop calibration's spread for scoop-estimated servings, the scale resolution for weighed returns, plus 3% for the behavioural assumption that "enough" means demand was about what was eaten.

## Leftovers from two photos

A scale reading is the default. As an option, leftovers can be estimated from two photos: the plate as served, and the same plate when it comes back. The before photo's weight is already known from the recorded serving, so it calibrates the after photo. There is no separate calibration step.

**1. Outline.** In each photo the app finds the plate and the food on it. It uses SlimSAM, a compressed Segment Anything model (Apache-2.0), run in the browser with Transformers.js on ONNX Runtime Web. The model only proposes outlines around a point; the app's own logic chooses between them:

- The plate is the largest *round* outline around the centre of the photo. Roundness matters because a tray, a placemat or the table can be the largest outline, but a plate or bowl seen from above is an ellipse.
- The food is the outline that separates most cleanly in colour from the rest of the plate.
- The model is then asked again with points spread across that first outline, so the result does not depend on where the first point happened to fall.

**2. Leftovers.** In the after photo, the app looks for patches the colour of the food seen before, corrected for lighting using the plate's own rim. A patch is kept only if the model can outline it cleanly and it is more food-coloured than plate-coloured. This rule exists because a reflection on a steel plate is as bright as rice.

**3. Correct.** Either outline can be fixed by hand: tap food to add it, tap what is not food to remove it, step to a smaller or larger outline, paint or erase with a brush, drag the plate outline, undo, or mark the plate as empty.

**4. Measure.** Food is expressed as a share of the plate's area, which cancels out how close the camera was. Then

    grams left = grams before x (share after / share before) ^ 1.25

Area cannot see depth. Food spread flat loses mass in proportion to area (exponent 1.0); a mound that shrinks in every direction loses it faster (exponent 1.5). The estimate uses the middle, and the stated range runs between the two. The standard deviation combines half that range, 14% for imperfect outlines, and 3 g.

**5. Record.** The result is saved as measurement `photo_estimate` with its standard deviation, which widens the interval the demand model learns from, so a photo teaches it less sharply than a scale. A `photo_detail` record keeps the two shares, the reference grams, the exponent, whether an outline was corrected by hand, and whether the model or the by-hand path was used. The photos themselves are analysed on the device and are never uploaded or stored by the server.

**After photo only.** Each confirmed before photo is also an example of "this share of the plate is this many grams of this food". With such examples saved on the device, an after photo can be read on its own, with a wider uncertainty.

**Without the model.** If the model cannot be downloaded, the app starts from a rough colour guess and the same tools work by hand (taps fill by colour).

### What was checked

- 13 unit tests of the selection and estimation logic on synthetic frames (`apps/web/src/photo.test.mjs`).
- A browser test on a generated pair in which the after photo is taken closer, off-centre and darker (`tests/e2e/photo_flow.py`): both outlines were within the test's tolerance of the rendered truth, and the estimate was 36 g +/- 14 (range 26 to 52) against a generated truth of 27 g. The generator assumes an exponent of 1.35, so the truth lying inside the range is by construction.
- One real photograph supplied by the team, white rice in a steel bowl: the bowl and the rice were outlined with no input. Used in both slots it read 34.6% and 34.2% of the plate, and 34.9% when rescaled to imitate a more distant shot. This shows the outlines are consistent, not that the grams are right.

### What was not checked

- **No real before and after pair with weighed leftovers has been tested.** The gram estimate has not been validated against a scale.
- Mixed dishes, sauces, food the same colour as the plate, strong shadows, steep camera angles and square plates.
- Phones. On a laptop the first photo takes about 10 s including a one-time 14 MB model download, and about 5 s after that; phones will be slower.
- The first use needs jsDelivr and Hugging Face to be reachable to fetch the library and the model.

## The model

D is lognormal with unknown location μ and spread σ. The posterior over (μ, σ) is held as normalised log-weights on a bounded grid: 72 values of μ covering 15 g to 1,200 g, and 30 values of σ from 0.06 to 1.0.

- Updates add log-likelihoods and renormalise. The model for a diner is rebuilt by replaying their eligible plates in event-time order, so a duplicate event or a correction can never be counted twice.
- Normal CDFs use a NumPy-only implementation, evaluated in the log domain so far-tail probabilities stay finite. It is checked against SciPy in `tests/test_numerics.py`.
- Predictions are the posterior *predictive*: they include meal-to-meal variation as well as uncertainty about the average, so the stated range does not collapse as plates accumulate.
- Posterior mass on a grid edge raises a flag that is shown to the operator instead of silently truncating.

This is a deliberately simple probabilistic model. It is not claimed to be better than other censored models; a moving average is not Bayesian and is used only as a comparator.

## Priors (assumptions, not standards)

| Quantity | Default | Meaning |
|---|---|---|
| Typical amount | 180 g, log-sd 0.70 | broad starting guess for cooked rice |
| Meal-to-meal spread σ | mode 0.25, log-sd 0.60 | how much one diner varies |

When at least 20 eligible plates from at least 5 *other* diners exist for the same food and data source, the prior's centre moves to their pooled median (log-sd 0.5). The pooling is exact leave-one-out, so a diner's own plates are never counted in both the prior and the likelihood. Measured and simulated evidence are never pooled together.

## Serving policy

Candidates are half-scoop steps of the calibrated scoop. Under the stated simplification that a diner offered `s` takes refills of size `r` until satisfied,

    served A(D, s, r) = s + r · ceil(max(D − s, 0) / r),   leftover = max(A − D, 0)

the policy picks the portion that minimises **expected leftover + (cost of one trip for seconds) × expected trips**.

| Default | Value | Why |
|---|---|---|
| Cost of one trip for seconds | 20 g-equivalent | balances waste against walking back |
| Chance of needing two trips | at most 50% | availability limit |
| Change from the last portion received | at most 1 scoop per meal | conservative movement |
| Smallest portion | 1 scoop | a zero portion can never be recommended |
| Seconds not accessible | cover the 80th percentile | small first portions are only acceptable with seconds |

A diner's request always wins. Opting out gives the usual serving. With no history the suggestion comes from the cohort, or from the kitchen's usual portion if there is no cohort yet.

The two-trip limit was chosen with `evaluation/explore_policy.py` on development seeds 9001–9002, which are not among the reported evaluation seeds. Nothing was tuned on reported results.

## Kitchen planner

Fixed-seed Monte Carlo over what will be **served** (first portions, refills and rounding), not just what will be eaten. It includes head-count uncertainty, a shared appetite effect for the whole meal, arrival timing, refill uptake and refill-station capacity, and checks cooked food against need interval by interval given batch size, lead time and stock.

If stock or refill capacity cannot meet the target, the plan is returned as **infeasible** with the reason and options. The target is never quietly relaxed. "Chance of running short" is a modelled quantity under the listed assumptions, not a promise.

## Known limits

- A diner with only "wanted more" plates has no upper evidence, so their estimate has a long upper tail. The per-meal change limit keeps suggestions from jumping.
- Recipe changes, weather, exams and menu pairings are not modelled. A new recipe is a new food version with its own calibration and history.
- Self-reported satisfaction can be wrong. Walking back for seconds takes effort, so "didn't ask" is not treated as "was full".
- A recency-weighting option exists (`recency_half_life`) and is off by default.
- No field data has been collected. Effect in a real kitchen is unproven.
