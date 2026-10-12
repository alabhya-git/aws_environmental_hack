A kitchen scale reads 0 g under two returned plates. One diner had exactly enough. The other is still hungry and decided the queue for seconds was not worth it.

To the scale, these are the same event. To any system trying to learn portion sizes from leftovers, treating them as the same is a quiet disaster. This post is about that problem, the statistics that fix it, and what happened when we tested the fix honestly.

We built ProofPlate for Environmental Hacks (Waste and Energy track). It suggests the first portion for each diner in a college mess, keeps seconds one tap away, and turns what comes back on the plates into the kitchen's next cooking plan. It runs on AWS Lambda, API Gateway, DynamoDB and Cognito. Most of the code was written with Claude Code from our specification; the decisions, and the mistakes below, are what this post is about.

## Why "just average what they ate" fails

The obvious way to personalise a portion is a moving average of what each diner ate. Serve 180 g, plate comes back empty, record "eats 180 g". Next time, serve 180 g.

Now follow the hungry diner. They are served 180 g and finish it. The average says 180 g. They are served 180 g again. The system has learned, with growing confidence, that 180 g is correct. It will never serve them more, because the only evidence it accepts is the amount it chose to serve.

That is not a hypothetical. We built a simulator in which only half of the diners who want more bother to walk back for seconds, and ran the plain average against the usual fixed portion on identical diners:

| Policy | Food left per plate | Diners left wanting more |
|---|---|---|
| Usual fixed portion (3 scoops) | 25.6 g | 39.6% |
| Personal moving average | 13.6 g | 47.9% |

The average looks like a waste-reduction success. It got there by under-feeding people, and it reports this as optimisation.

## The fix is sixty years old

Medical statisticians solved this long ago. If a patient is still alive when a study ends, you do not record their survival time as "five years". You record "at least five years". The observation is *right-censored*: you know a lower bound, not the value.

A finished plate from a diner who wanted more is the same thing. Their appetite was *at least* what they ate. So ProofPlate asks one question when a plate comes back, "was that enough?", and uses the answer to choose the likelihood:

| What happened | What it tells the model | Likelihood |
|---|---|---|
| "Enough", 150 g eaten | appetite was about 150 g | F(hi) − F(lo) |
| "Wanted more", 180 g eaten | appetite was at least 180 g | 1 − F(lo) |
| Empty plate, nobody asked | nothing | not used |
| Food left because they disliked it | waste, but not appetite | not used |

Here F is the cumulative distribution of that diner's appetite for that dish. The whole idea fits in a few lines of the model:

```python
if obs.kind == "right_censored":
    return np.maximum(log_ndtr(-z(obs.lo_g)), LOG_FLOOR)   # log(1 - F(lo))
...
return log_ndtr_diff(z(hi), z(obs.lo_g))                   # log(F(hi) - F(lo))
```

A "wanted more" plate can only push the estimate up. A test checks exactly that: after such a plate, no predictive quantile is allowed to fall.

## A model small enough to run on every request

Appetite is modelled as lognormal with an unknown typical amount and an unknown meal-to-meal spread. We do not run MCMC. The posterior lives on a fixed grid of 72 × 30 = 2,160 cells, stored as log-weights. An update adds a log-likelihood and renormalises.

That choice was about operations as much as statistics:

- **It is fast.** A diner's whole history replays in milliseconds inside a Lambda function, so there is no model server and nothing to keep warm.
- **It is deterministic.** We never store the posterior. Every request rebuilds it by replaying that diner's immutable events from DynamoDB. A duplicated request or a corrected weighing cannot double-count, because there is no incremental state to corrupt.
- **It predicts, not just averages.** The output is the predictive distribution for the next meal, so the stated range includes ordinary day-to-day variation and does not collapse as plates accumulate.

One detail we enjoyed: the Lambda package has no SciPy, which would have tripled its size. The normal CDF comes from a Chebyshev fit of the complementary error function, evaluated in the log domain so tail probabilities stay finite instead of underflowing to minus infinity. A test compares it with SciPy across both tails.

## Our first policy made waste worse

With a distribution for each diner, the portion policy minimises expected leftover plus a cost for each trip back for seconds, in half-scoop steps of a calibrated scoop.

Our first version also capped the chance of needing two trips at 15%. It sounded considerate. In the demo it suggested **7.5 scoops** for a diner whose only evidence was "wanted more" five times, because nothing in their history bounded their appetite from above. In simulation it left *more* food on the plate than the usual portion.

Two changes fixed it. The cap was relaxed to 50%, chosen on development seeds that are not among the reported ones. And a suggestion may move at most one scoop from what the diner last received, so the hungry diner goes from three scoops to four, then is asked again.

## What the evidence says, including the part we did not want

Nine scenarios, eight seeds, every policy facing the same diners and appetites, scored on the final third of meals. In the baseline scenario:

| Policy | Food left per plate | Left wanting more | Trips for seconds |
|---|---|---|---|
| Usual fixed portion | 28.9 g | 19.8% | 1.08 |
| Small portion + seconds | 12.4 g | 35.8% | 1.91 |
| Personal average | 25.9 g | 14.3% | 0.84 |
| ProofPlate | 29.5 g | 10.8% | 0.66 |

Read the first and last rows. **At its default setting, ProofPlate does not reduce plate waste.** It leaves about the same and roughly halves the number of diners who leave hungry.

That is not the headline we hoped for in a waste hackathon, so we asked a sharper question. Any kitchen can cut waste by serving everyone less; the cost is hungrier diners. Does personalising buy a better trade than that? We swept fixed portions from 2 to 4 scoops and traced the curve. For a fixed portion to leave only 10.8% of diners wanting more, it has to leave about **55 g** per plate. ProofPlate got there at 29.5 g.

Two cautions belong next to those numbers. They come from a simulator built on stated assumptions, and "left wanting more" uses the simulator's hidden appetite, which no real kitchen can see. No real mess has been measured. The app's Evidence page keeps observed, predicted and simulated results on separate tabs, and the observed tab is empty.

## What we would tell another team

- **Ask what your data cannot tell you.** The interesting part of this project was one missing bit of information per plate, and one question that recovers it.
- **Simulate your baseline before your model.** The plain average looked brilliant on waste alone. A second metric showed what it was doing.
- **Publish the row that hurts.** "About the same waste, half as many hungry diners" is a claim we can defend line by line.

Try it: https://imh92ymqaa.execute-api.ap-south-1.amazonaws.com (open the demo, then the Learn tab, and switch between the three readings of the same empty plate).
Code, model card and full evaluation: https://github.com/alabhya-git/aws_environmental_hack
