"""Stable normal-distribution numerics using NumPy only.

SciPy is deliberately not a runtime dependency (it would triple the Lambda
package). The complementary error function uses the Chebyshev fit from
Numerical Recipes (`erfcc`), whose *fractional* error is below 1.2e-7 for all
arguments. Because the bound is relative, it is evaluated directly in the log
domain, which keeps far-tail log-probabilities finite and accurate.
tests/test_numerics.py checks every function here against SciPy.
"""
from __future__ import annotations

import math

import numpy as np

SQRT2 = math.sqrt(2.0)
LOG_HALF = math.log(0.5)
# Finite floor for log-likelihoods: an impossible observation must not turn the
# whole posterior into NaN. Callers treat hitting the floor as a diagnostic.
LOG_FLOOR = -700.0

_ERFCC = (
    -1.26551223, 1.00002368, 0.37409196, 0.09678418, -0.18628806,
    0.27886807, -1.13520398, 1.48851587, -0.82215223, 0.17087277,
)


def _log_erfc_nonneg(x: np.ndarray) -> np.ndarray:
    """log(erfc(x)) for x >= 0, without underflow for large x."""
    t = 1.0 / (1.0 + 0.5 * x)
    poly = np.zeros_like(t)
    for c in reversed(_ERFCC[1:]):
        poly = t * (c + poly)
    return np.log(t) - x * x + _ERFCC[0] + poly


def log_ndtr(z) -> np.ndarray:
    """log of the standard normal CDF, accurate in both tails."""
    z = np.asarray(z, dtype=float)
    x = np.abs(z) / SQRT2
    log_tail = LOG_HALF + _log_erfc_nonneg(x)  # log P(Z > |z|)
    with np.errstate(over="ignore", invalid="ignore"):
        upper = np.log1p(-np.exp(log_tail))  # z > 0: log(1 - tail)
    return np.where(z <= 0, log_tail, upper)


def ndtr(z) -> np.ndarray:
    """Standard normal CDF."""
    return np.exp(log_ndtr(z))


def log_ndtr_diff(z_hi, z_lo) -> np.ndarray:
    """log(Phi(z_hi) - Phi(z_lo)) for z_hi >= z_lo, floored at LOG_FLOOR.

    Works on whichever tail keeps precision: when both points are in the upper
    tail the difference is taken between survival functions instead.
    """
    z_hi, z_lo = np.broadcast_arrays(np.asarray(z_hi, float), np.asarray(z_lo, float))
    flip = z_lo > 0
    a = np.where(flip, -z_hi, z_lo)
    b = np.where(flip, -z_lo, z_hi)
    lb = log_ndtr(b)
    la = log_ndtr(a)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        out = lb + np.log1p(-np.exp(np.minimum(la - lb, 0.0)))
    out = np.where(np.isfinite(out), out, LOG_FLOOR)
    return np.maximum(out, LOG_FLOOR)


def logsumexp(a: np.ndarray) -> float:
    m = float(np.max(a))
    if not math.isfinite(m):
        return m
    return m + math.log(float(np.sum(np.exp(a - m))))


def normal_nodes(n: int) -> np.ndarray:
    """Midpoint quantile nodes of a standard normal (equal-probability strata)."""
    from statistics import NormalDist

    nd = NormalDist()
    return np.array([nd.inv_cdf((k + 0.5) / n) for k in range(n)])
