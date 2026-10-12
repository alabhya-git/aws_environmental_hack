"""Deliberately simple comparators. None of these is Bayesian."""
from __future__ import annotations

from typing import Sequence


def moving_average(consumed_g: Sequence[float], window: int = 5) -> float | None:
    """Mean of the last `window` values. Callers decide which values are eligible."""
    xs = list(consumed_g)[-window:]
    return sum(xs) / len(xs) if xs else None


def naive_next_portion(all_consumed_g: Sequence[float], window: int = 5) -> float | None:
    """What a learner does if it treats every plate, including empty plates from
    diners who wanted more, as an exact measure of appetite."""
    return moving_average(all_consumed_g, window)


def cohort_mean(consumed_by_diner: dict[str, Sequence[float]], exclude: str | None = None) -> float | None:
    xs = [x for d, vals in consumed_by_diner.items() if d != exclude for x in vals]
    return sum(xs) / len(xs) if xs else None
