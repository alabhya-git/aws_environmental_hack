"""The mechanisms that would invalidate the product if they were wrong."""
import math

import numpy as np
import pytest

from proofplate.model import GridConfig, Observation, Posterior, Prior, fit
from proofplate.model.baselines import naive_next_portion
from proofplate.planning import ServingConfig, recommend

sp = pytest.importorskip("scipy.stats")

CFG = ServingConfig(scoop_g=60.0)


def enough(g, tol=6, ref=""):
    return Observation("interval", g - tol, g + tol, ref=ref)


def wanted_more(g, ref=""):
    return Observation("right_censored", g, ref=ref)


def test_posterior_is_normalised_and_finite_over_many_updates():
    post = Posterior.from_prior()
    rng = np.random.default_rng(1)
    for k in range(200):
        g = float(rng.lognormal(math.log(190), 0.2))
        post = post.update(enough(g) if k % 3 else wanted_more(g * 0.8))
        assert np.all(np.isfinite(post.logw))
        assert abs(post.weights.sum() - 1.0) < 1e-9
    assert post.n_interval + post.n_censored == 200


def test_interval_and_censored_likelihoods_match_analytic_lognormal():
    post = Posterior.from_prior()
    mu, sigma = post.config.axes()
    i, j = 30, 12
    dist = sp.lognorm(s=sigma[j], scale=math.exp(mu[i]))
    ll_int = post.log_likelihood(Observation("interval", 150.0, 170.0))[i, j]
    assert ll_int == pytest.approx(math.log(dist.cdf(170.0) - dist.cdf(150.0)), abs=1e-5)
    ll_cens = post.log_likelihood(Observation("right_censored", 210.0))[i, j]
    assert ll_cens == pytest.approx(math.log(dist.sf(210.0)), abs=1e-5)


def test_wanted_more_is_not_treated_as_exact_consumption():
    history = [enough(180), enough(175), enough(185)]
    exact = fit(history + [enough(120)])
    censored = fit(history + [wanted_more(120)])
    base = fit(history)
    m_exact, m_cens, m_base = (p.quantiles([0.5])[0] for p in (exact, censored, base))
    assert m_exact < m_base - 5          # an exact 120 g pulls the estimate down
    assert m_cens >= m_base - 1e-6       # "at least 120 g" must not
    assert not np.allclose(exact.logw, censored.logw)


def test_wanted_more_above_history_raises_the_estimate():
    base = fit([enough(180), enough(175)])
    more = base.update(wanted_more(240))
    assert more.quantiles([0.5])[0] > base.quantiles([0.5])[0] + 5


def test_wanted_more_never_lowers_any_predictive_quantile():
    base = fit([enough(200), enough(150), enough(260)])
    for a in (40, 120, 200, 300):
        after = base.update(wanted_more(a))
        for q in (0.1, 0.5, 0.9):
            assert after.quantiles([q])[0] >= base.quantiles([q])[0] - 1e-6


def test_larger_consumption_evidence_increases_prediction_and_suggestion():
    low = fit([enough(120)] * 4)
    high = fit([enough(120)] * 4 + [enough(300)] * 6)
    assert high.quantiles([0.5])[0] > low.quantiles([0.5])[0] + 40
    assert recommend(high, CFG).scoops > recommend(low, CFG).scoops


def test_predictive_interval_reflects_meal_variation_not_only_the_mean():
    rng = np.random.default_rng(7)
    data = rng.lognormal(math.log(200), 0.30, size=60)
    post = fit([enough(float(x), tol=3) for x in data])
    q10, q90 = post.quantiles([0.10, 0.90])
    true10, true90 = (math.exp(math.log(200) + 0.30 * z) for z in (-1.2816, 1.2816))
    assert q90 - q10 > 0.75 * (true90 - true10)   # does not collapse as n grows
    assert abs(post.quantiles([0.5])[0] - 200) < 20


def test_grid_boundary_is_flagged_not_silently_truncated():
    post = fit([enough(1150, tol=10)] * 8)
    assert "POSTERIOR_AT_AMOUNT_BOUNDARY" in post.diagnostics()["flags"]
    huge = fit([enough(5000, tol=10)] * 3)
    assert "OBSERVATION_OUTSIDE_MODEL_RANGE" in huge.diagnostics()["flags"] or \
        "POSTERIOR_AT_AMOUNT_BOUNDARY" in huge.diagnostics()["flags"]
    assert np.all(np.isfinite(huge.logw))


def test_near_zero_quantities_are_handled_explicitly():
    base = Posterior.from_prior()
    assert np.allclose(base.update(wanted_more(0)).logw, base.logw)      # no information
    tiny = base.update(Observation("interval", 0.0, 12.0))
    assert np.all(np.isfinite(tiny.logw))
    assert tiny.quantiles([0.5])[0] < base.quantiles([0.5])[0]


def test_replay_is_deterministic_and_digest_tracks_evidence():
    obs = [enough(180, ref="e1"), wanted_more(150, ref="e2"), enough(210, ref="e3")]
    a, b = fit(obs), fit(obs)
    assert np.array_equal(a.logw, b.logw) and a.digest() == b.digest()
    assert fit(obs[:2]).digest() != a.digest()


def test_recency_weighting_is_optional_and_follows_drift():
    obs = [enough(120)] * 10 + [enough(240)] * 4
    plain = fit(obs).quantiles([0.5])[0]
    recent = fit(obs, recency_half_life=3).quantiles([0.5])[0]
    assert recent > plain


def test_naive_average_underserves_the_hungry_diner_where_the_model_does_not():
    # three plates of 180 g finished, every time with "wanted more"
    naive = naive_next_portion([180, 180, 180])
    post = fit([wanted_more(180)] * 3)
    assert naive == 180
    assert post.quantiles([0.5])[0] > 200


def test_prior_and_grid_are_configurable_and_reported():
    post = Posterior.from_prior(GridConfig(n_mu=40, n_sigma=16), Prior(median_g=90, source="test"))
    assert post.logw.shape == (40, 16)
    assert 60 < post.quantiles([0.5])[0] < 135
