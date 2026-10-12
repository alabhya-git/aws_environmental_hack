"""Our NumPy-only normal numerics against SciPy (a test-only dependency)."""
import numpy as np
import pytest

from proofplate.numerics import LOG_FLOOR, log_ndtr, log_ndtr_diff, ndtr

sp = pytest.importorskip("scipy.special")


def test_log_ndtr_matches_scipy_across_both_tails():
    z = np.linspace(-38, 9, 4001)
    assert np.max(np.abs(log_ndtr(z) - sp.log_ndtr(z))) < 5e-7


def test_log_ndtr_far_tail_is_finite():
    z = np.array([-50.0, -200.0, -1000.0])
    ours = log_ndtr(z)
    assert np.all(np.isfinite(ours))
    assert np.allclose(ours, sp.log_ndtr(z), rtol=1e-6)


def test_ndtr_matches_scipy():
    z = np.linspace(-8, 8, 2001)
    assert np.max(np.abs(ndtr(z) - sp.ndtr(z))) < 2e-7


def test_log_ndtr_diff_matches_reference_in_centre_and_tails():
    for lo, hi in [(-1.0, 1.0), (0.2, 0.3), (5.0, 6.0), (-7.0, -6.5), (-0.01, 0.01), (8.0, 8.5)]:
        ref = np.log(sp.ndtr(hi) - sp.ndtr(lo)) if lo < 4 else np.log(sp.ndtr(-lo) - sp.ndtr(-hi))
        assert abs(float(log_ndtr_diff(hi, lo)) - ref) < 2e-5, (lo, hi)


def test_log_ndtr_diff_never_returns_nan_or_minus_inf():
    out = log_ndtr_diff(np.array([40.0, 1.0, -60.0]), np.array([39.999, 1.0, -61.0]))
    assert np.all(np.isfinite(out)) and np.all(out >= LOG_FLOOR)
