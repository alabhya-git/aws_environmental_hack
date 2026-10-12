# Evaluation

> **Status: SIMULATED.** Every number below comes from a labelled simulator with fixed seeds. None is a measurement of a real kitchen, and none should be quoted as measured savings.

- Generated: 2026-10-11T14:02:29+00:00 · seeds [101, 202, 303, 404, 505, 606, 707, 808] · model `censored-lognormal-grid/1.0` · policy `first-portion/1.0`
- Split: Chronological. Each learner starts empty, predicts before each meal and updates after it. Metrics are computed on the final third of meals only (the test period).
- Tuning: One policy default (the limit on needing two trips for seconds) was chosen with evaluation/explore_policy.py on development seeds 9001-9002, which are not among the reported seeds. Nothing was chosen using the outputs reported here.
- Resources: Every policy has the same seconds access, refill size, refill uptake and unlimited stock; kitchen surplus is prepared minus withdrawn under each policy's own preparation rule.
- Intervals are 95% t-intervals across seeds. 'vs conventional' is a paired difference on identical latent streams.
- Reproduce: `python -m evaluation.run_evaluation` (writes `evaluation/outputs/evaluation.json`, which the app's Evidence view reads).

## Policies

- **conventional** — Fixed conventional portion (3 scoops)
- **small_plus_seconds** — Fixed smaller portion (2 scoops) + seconds
- **cohort** — Cohort history, same portion for everyone
- **naive_average** — Personal moving average (ignores 'wanted more')
- **proofplate** — ProofPlate: censored personal model

`naive_average` is an ablation: it personalises without the censoring logic, to show what that logic is for.

## baseline

Mixed appetites, learner's own distribution family.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 28.9 (26.1 to 31.8) | 0.0 (0.0 to 0.0) | 19.8% (17.6 to 22.0) | 1.08 (0.96 to 1.20) | 2425 (2072 to 2778) | 0% (0 to 0) |
| small_plus_seconds | 12.4 (11.5 to 13.3) | -16.5 (-18.5 to -14.5) | 35.8% (32.8 to 38.8) | 1.91 (1.79 to 2.04) | 1975 (1319 to 2630) | 2% (-2 to 5) |
| cohort | 30.5 (27.0 to 34.0) | 1.5 (-2.1 to 5.2) | 19.0% (17.0 to 20.9) | 1.04 (0.96 to 1.11) | 2620 (2217 to 3024) | 0% (0 to 0) |
| naive_average | 25.9 (23.9 to 28.0) | -3.0 (-5.9 to -0.0) | 14.3% (11.5 to 17.1) | 0.84 (0.72 to 0.96) | 2500 (1970 to 3030) | 0% (0 to 0) |
| proofplate | 29.5 (27.1 to 31.9) | 0.6 (-2.5 to 3.6) | 10.8% (9.2 to 12.3) | 0.66 (0.56 to 0.75) | 2883 (2449 to 3317) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 35.6 (34.1 to 37.2) g on 2009 uncensored plates; 80% interval covered 80% (78 to 82) with mean width 125 (120 to 131) g; mean log score on 288 censored plates -0.83 (-0.91 to -0.75).

## gamma_family

Demand drawn from a gamma family the learner does not assume.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 29.3 (26.0 to 32.7) | 0.0 (0.0 to 0.0) | 19.5% (16.2 to 22.8) | 1.11 (0.98 to 1.24) | 2277 (1945 to 2610) | 2% (-2 to 5) |
| small_plus_seconds | 13.1 (11.6 to 14.5) | -16.2 (-18.5 to -14.0) | 36.3% (32.2 to 40.3) | 1.95 (1.82 to 2.08) | 1910 (1398 to 2423) | 2% (-2 to 5) |
| cohort | 29.3 (26.0 to 32.7) | 0.0 (0.0 to 0.0) | 19.5% (16.2 to 22.8) | 1.11 (0.98 to 1.24) | 2277 (1945 to 2610) | 2% (-2 to 5) |
| naive_average | 25.8 (23.5 to 28.0) | -3.6 (-5.9 to -1.3) | 14.6% (11.5 to 17.7) | 0.87 (0.76 to 0.98) | 2360 (1757 to 2962) | 2% (-2 to 5) |
| proofplate | 29.3 (25.9 to 32.8) | -0.0 (-2.6 to 2.6) | 11.1% (8.8 to 13.4) | 0.68 (0.58 to 0.78) | 2747 (2100 to 3394) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 35.2 (33.9 to 36.6) g on 2004 uncensored plates; 80% interval covered 79% (77 to 82) with mean width 125 (121 to 129) g; mean log score on 306 censored plates -0.77 (-0.90 to -0.63).

## two_mode_meals

Each diner has light and heavy meals (bimodal): strong misspecification.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 35.2 (32.0 to 38.4) | 0.0 (0.0 to 0.0) | 19.5% (16.0 to 23.1) | 1.08 (0.93 to 1.22) | 2365 (2009 to 2720) | 2% (-2 to 5) |
| small_plus_seconds | 15.3 (14.1 to 16.6) | -19.9 (-21.9 to -17.9) | 33.9% (29.6 to 38.1) | 1.82 (1.68 to 1.96) | 2059 (1750 to 2369) | 2% (-2 to 5) |
| cohort | 33.6 (29.7 to 37.5) | -1.6 (-5.5 to 2.2) | 20.5% (17.2 to 23.8) | 1.12 (0.96 to 1.28) | 2478 (1960 to 2996) | 2% (-2 to 5) |
| naive_average | 30.2 (27.9 to 32.6) | -5.0 (-8.0 to -2.0) | 17.5% (14.9 to 20.1) | 1.03 (0.90 to 1.17) | 2519 (1985 to 3054) | 2% (-2 to 5) |
| proofplate | 32.5 (29.5 to 35.4) | -2.8 (-5.0 to -0.5) | 15.9% (13.1 to 18.7) | 0.90 (0.77 to 1.04) | 2476 (1949 to 3002) | 3% (-4 to 10) |

ProofPlate prediction on the test period: MAE 48.4 (45.7 to 51.2) g on 1863 uncensored plates; 80% interval covered 78% (76 to 80) with mean width 179 (171 to 188) g; mean log score on 427 censored plates -0.71 (-0.75 to -0.66).

## appetite_rises

Half the diners' appetite rises 35% a third of the way in.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 23.0 (20.7 to 25.4) | 0.0 (0.0 to 0.0) | 27.5% (24.3 to 30.7) | 1.63 (1.44 to 1.82) | 3094 (2331 to 3857) | 0% (0 to 0) |
| small_plus_seconds | 11.0 (10.2 to 11.9) | -12.0 (-13.6 to -10.5) | 44.5% (40.7 to 48.2) | 2.43 (2.27 to 2.59) | 3253 (2988 to 3517) | 2% (-2 to 5) |
| cohort | 31.7 (27.0 to 36.4) | 8.7 (4.9 to 12.4) | 22.2% (19.2 to 25.3) | 1.34 (1.15 to 1.54) | 2833 (2203 to 3463) | 0% (0 to 0) |
| naive_average | 27.5 (25.5 to 29.4) | 4.4 (2.3 to 6.5) | 16.1% (14.0 to 18.3) | 1.01 (0.87 to 1.14) | 3080 (2672 to 3487) | 2% (-2 to 5) |
| proofplate | 25.7 (23.2 to 28.1) | 2.6 (0.5 to 4.8) | 15.3% (13.1 to 17.5) | 1.02 (0.87 to 1.16) | 3012 (2559 to 3465) | 2% (-2 to 5) |

ProofPlate prediction on the test period: MAE 41.5 (38.0 to 45.0) g on 1906 uncensored plates; 80% interval covered 83% (80 to 86) with mean width 149 (142 to 155) g; mean log score on 426 censored plates -0.85 (-0.94 to -0.76).

## late_joiners

40% of diners first appear halfway through (cold starts).

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 29.4 (26.3 to 32.4) | 0.0 (0.0 to 0.0) | 19.0% (16.7 to 21.3) | 1.07 (0.94 to 1.21) | 2437 (2055 to 2818) | 0% (0 to 0) |
| small_plus_seconds | 13.0 (12.0 to 14.0) | -16.4 (-18.6 to -14.1) | 33.6% (29.5 to 37.7) | 1.95 (1.81 to 2.08) | 2438 (1764 to 3111) | 2% (-2 to 5) |
| cohort | 30.9 (28.2 to 33.5) | 1.5 (-2.1 to 5.1) | 18.5% (17.1 to 19.9) | 1.03 (0.93 to 1.13) | 2632 (2206 to 3057) | 0% (0 to 0) |
| naive_average | 25.4 (23.5 to 27.3) | -3.9 (-7.2 to -0.6) | 14.5% (12.3 to 16.7) | 0.89 (0.79 to 0.99) | 2575 (2117 to 3032) | 0% (0 to 0) |
| proofplate | 29.0 (26.4 to 31.5) | -0.4 (-3.4 to 2.6) | 11.5% (9.7 to 13.3) | 0.70 (0.59 to 0.80) | 2878 (2530 to 3226) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 36.1 (33.6 to 38.6) g on 1986 uncensored plates; 80% interval covered 81% (78 to 83) with mean width 134 (127 to 141) g; mean log score on 312 censored plates -0.80 (-0.86 to -0.74).

## disliked_recipe

Three meals where the recipe went wrong and half the diners leave food.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 33.6 (29.5 to 37.7) | 0.0 (0.0 to 0.0) | 17.8% (15.2 to 20.4) | 0.97 (0.82 to 1.12) | 2578 (2176 to 2979) | 0% (0 to 0) |
| small_plus_seconds | 15.9 (13.5 to 18.3) | -17.7 (-20.0 to -15.4) | 32.0% (28.2 to 35.7) | 1.77 (1.60 to 1.94) | 2103 (1837 to 2369) | 2% (-2 to 5) |
| cohort | 36.9 (33.2 to 40.7) | 3.3 (-1.8 to 8.5) | 16.0% (13.6 to 18.4) | 0.89 (0.80 to 0.98) | 2964 (2487 to 3441) | 0% (0 to 0) |
| naive_average | 27.9 (23.7 to 32.1) | -5.7 (-8.8 to -2.6) | 15.8% (12.9 to 18.7) | 0.89 (0.77 to 1.01) | 2827 (2321 to 3333) | 0% (0 to 0) |
| proofplate | 34.7 (29.9 to 39.5) | 1.1 (-1.8 to 4.0) | 10.2% (8.3 to 12.0) | 0.57 (0.49 to 0.65) | 2927 (2360 to 3494) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 33.9 (32.4 to 35.5) g on 1870 uncensored plates; 80% interval covered 81% (79 to 83) with mean width 123 (118 to 129) g; mean log score on 290 censored plates -0.74 (-0.79 to -0.69).

## missing_returns

A quarter of plates never get a return recorded.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 28.9 (26.1 to 31.8) | 0.0 (0.0 to 0.0) | 19.8% (17.6 to 22.0) | 1.08 (0.96 to 1.20) | 2425 (2072 to 2778) | 0% (0 to 0) |
| small_plus_seconds | 12.4 (11.5 to 13.3) | -16.5 (-18.5 to -14.5) | 35.8% (32.8 to 38.8) | 1.91 (1.79 to 2.04) | 1975 (1319 to 2630) | 2% (-2 to 5) |
| cohort | 31.6 (28.6 to 34.7) | 2.7 (-1.5 to 6.9) | 18.5% (16.6 to 20.5) | 1.01 (0.96 to 1.06) | 2738 (2348 to 3129) | 0% (0 to 0) |
| naive_average | 26.1 (23.9 to 28.4) | -2.8 (-5.9 to 0.3) | 14.2% (11.8 to 16.7) | 0.85 (0.74 to 0.97) | 2498 (1967 to 3029) | 0% (0 to 0) |
| proofplate | 29.7 (27.8 to 31.5) | 0.7 (-2.3 to 3.7) | 10.8% (9.2 to 12.4) | 0.67 (0.58 to 0.76) | 2956 (2434 to 3479) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 36.2 (34.2 to 38.1) g on 1555 uncensored plates; 80% interval covered 81% (78 to 84) with mean width 128 (121 to 135) g; mean log score on 225 censored plates -0.82 (-0.91 to -0.73).

## seconds_friction

Only half of diners who want more walk back for seconds.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 25.6 (22.7 to 28.6) | 0.0 (0.0 to 0.0) | 39.6% (35.5 to 43.7) | 0.40 (0.36 to 0.44) | 3398 (3136 to 3660) | 0% (0 to 0) |
| small_plus_seconds | 7.9 (7.0 to 8.8) | -17.7 (-19.9 to -15.6) | 65.1% (61.7 to 68.4) | 0.68 (0.62 to 0.74) | 3265 (3061 to 3469) | 0% (0 to 0) |
| cohort | 27.3 (23.6 to 30.9) | 1.6 (-2.2 to 5.4) | 38.0% (35.3 to 40.8) | 0.39 (0.34 to 0.43) | 3235 (2722 to 3748) | 0% (0 to 0) |
| naive_average | 13.6 (12.2 to 14.9) | -12.1 (-14.5 to -9.7) | 47.9% (43.4 to 52.5) | 0.49 (0.45 to 0.54) | 2599 (1769 to 3428) | 0% (0 to 0) |
| proofplate | 25.1 (22.5 to 27.7) | -0.5 (-3.7 to 2.7) | 28.4% (25.0 to 31.8) | 0.30 (0.26 to 0.35) | 2592 (2180 to 3003) | 0% (0 to 0) |

ProofPlate prediction on the test period: MAE 30.4 (28.6 to 32.3) g on 1536 uncensored plates; 80% interval covered 79% (76 to 83) with mean width 118 (113 to 124) g; mean log score on 761 censored plates -0.60 (-0.65 to -0.55).

## demand_spike

Three late meals with a jump in attendance and appetite.

| Policy | Plate waste g/plate | vs conventional | Left wanting more | Seconds trips/plate | Kitchen surplus g/meal | Short meals |
|---|---|---|---|---|---|---|
| conventional | 27.1 (24.8 to 29.4) | 0.0 (0.0 to 0.0) | 20.8% (18.0 to 23.6) | 1.21 (1.05 to 1.37) | 1967 (1747 to 2187) | 22% (17 to 27) |
| small_plus_seconds | 12.4 (11.5 to 13.4) | -14.7 (-16.4 to -13.0) | 36.4% (32.8 to 40.1) | 2.08 (1.92 to 2.24) | 2386 (1799 to 2973) | 17% (12 to 23) |
| cohort | 30.1 (27.0 to 33.2) | 3.0 (-1.7 to 7.7) | 19.1% (16.6 to 21.6) | 1.12 (1.02 to 1.23) | 2167 (1725 to 2609) | 22% (17 to 27) |
| naive_average | 26.9 (25.4 to 28.3) | -0.2 (-3.4 to 2.9) | 15.2% (13.4 to 16.9) | 0.92 (0.81 to 1.02) | 2303 (1773 to 2833) | 19% (9 to 28) |
| proofplate | 28.3 (26.5 to 30.0) | 1.2 (-1.8 to 4.2) | 13.1% (11.4 to 14.8) | 0.78 (0.68 to 0.88) | 2423 (1985 to 2861) | 11% (2 to 20) |

ProofPlate prediction on the test period: MAE 36.5 (34.3 to 38.7) g on 2100 uncensored plates; 80% interval covered 80% (78 to 82) with mean width 128 (120 to 136) g; mean log score on 371 censored plates -0.83 (-0.91 to -0.75).

## Limitations

- Behaviour (who walks back for seconds, who reports wanting more) follows stated assumptions, not observed diners.
- 'Left wanting more' uses the simulator's hidden demand, which no real kitchen can observe.
- Stock-outs are scored after the fact and do not feed back into what later diners received.
- A simulated difference is a property of this simulator. Field effect is unproven until measured in a pilot.

## Waste against availability (frontier sweep)

A fixed portion trades plate waste against diners left wanting more by moving everyone's portion. ProofPlate moves along its own curve through the cost it gives to a trip for seconds (20 g is the app default). Same worlds and seeds as above; means over seeds. **Simulated.**

### baseline

| Policy | Setting | Plate waste g/plate | Left wanting more | Seconds trips/plate |
|---|---|---|---|---|
| Fixed portion | 2.0 scoops | 12.4 | 35.8% | 1.91 |
| Fixed portion | 2.5 scoops | 18.6 | 27.4% | 1.47 |
| Fixed portion | 3.0 scoops | 28.9 | 19.8% | 1.08 |
| Fixed portion | 3.5 scoops | 43.8 | 13.4% | 0.76 |
| Fixed portion | 4.0 scoops | 62.6 | 9.0% | 0.52 |
| ProofPlate | trip cost 2 g | 22.4 | 15.9% | 0.96 |
| ProofPlate | trip cost 8 g | 23.2 | 14.7% | 0.89 |
| ProofPlate | trip cost 20 g | 29.5 | 10.8% | 0.66 |
| ProofPlate | trip cost 40 g | 40.2 | 7.4% | 0.42 |
| Personal average, no censoring | - | 25.9 | 14.3% | 0.84 |

At ProofPlate's default setting 10.8% of plates left the diner wanting more, with 29.5 g left per plate. A fixed portion giving the same 10.8% would leave about 55.2 g per plate (interpolated).

### seconds_friction

| Policy | Setting | Plate waste g/plate | Left wanting more | Seconds trips/plate |
|---|---|---|---|---|
| Fixed portion | 2.0 scoops | 7.9 | 65.1% | 0.68 |
| Fixed portion | 2.5 scoops | 14.7 | 52.5% | 0.53 |
| Fixed portion | 3.0 scoops | 25.6 | 39.6% | 0.40 |
| Fixed portion | 3.5 scoops | 41.2 | 28.3% | 0.29 |
| Fixed portion | 4.0 scoops | 60.9 | 19.0% | 0.20 |
| ProofPlate | trip cost 2 g | 16.5 | 42.0% | 0.46 |
| ProofPlate | trip cost 8 g | 18.0 | 38.5% | 0.41 |
| ProofPlate | trip cost 20 g | 25.1 | 28.4% | 0.30 |
| ProofPlate | trip cost 40 g | 36.9 | 18.8% | 0.20 |
| Personal average, no censoring | - | 13.6 | 47.9% | 0.49 |

At ProofPlate's default setting 28.4% of plates left the diner wanting more, with 25.1 g left per plate. A fixed portion giving the same 28.4% would leave about 41.1 g per plate (interpolated).
