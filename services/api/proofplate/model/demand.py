"""Censored Bayesian demand model.

Latent D is the amount of one food a diner would choose to eat at this meal
with adequate access. It is not a nutritional need. D is modelled as lognormal
with unknown location mu and dispersion sigma; the posterior over (mu, sigma)
is held as normalised log-weights on a bounded grid.

Evidence enters in exactly two forms:
  * interval  [lo, hi]  ->  likelihood F(hi) - F(lo)   ("that was enough")
  * right-censored at lo ->  likelihood 1 - F(lo)      ("I wanted more")
Anything else (ambiguous empty plate, confounded leftover, missing return) is
recorded by the domain layer and never reaches this module.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Literal, Sequence

import numpy as np

from ..numerics import LOG_FLOOR, log_ndtr, log_ndtr_diff, logsumexp, normal_nodes

MODEL_VERSION = "censored-lognormal-grid/1.0"


@dataclass(frozen=True)
class GridConfig:
    """Bounded grid. Bounds are in grams of cooked food on the plate."""

    mu_min_g: float = 15.0
    mu_max_g: float = 1200.0
    n_mu: int = 72
    sigma_min: float = 0.06
    sigma_max: float = 1.0
    n_sigma: int = 30

    def axes(self) -> tuple[np.ndarray, np.ndarray]:
        mu = np.linspace(math.log(self.mu_min_g), math.log(self.mu_max_g), self.n_mu)
        sigma = np.exp(np.linspace(math.log(self.sigma_min), math.log(self.sigma_max), self.n_sigma))
        return mu, sigma


@dataclass(frozen=True)
class Prior:
    """Documented assumptions, not standards. `source` says where they came from."""

    median_g: float = 180.0
    mu_sd: float = 0.70
    sigma_mode: float = 0.25
    sigma_log_sd: float = 0.60
    source: str = "default-assumption"

    def describe(self) -> dict:
        return {
            "median_g": self.median_g,
            "mu_sd": self.mu_sd,
            "sigma_mode": self.sigma_mode,
            "sigma_log_sd": self.sigma_log_sd,
            "source": self.source,
        }


@dataclass(frozen=True)
class Observation:
    kind: Literal["interval", "right_censored"]
    lo_g: float
    hi_g: float | None = None
    weight: float = 1.0
    ref: str = ""  # event id, for digests and audit

    def __post_init__(self) -> None:
        if self.lo_g < 0 or (self.hi_g is not None and self.hi_g < self.lo_g):
            raise ValueError("observation bounds must satisfy 0 <= lo <= hi")
        if self.kind == "interval" and self.hi_g is None:
            raise ValueError("interval observation needs hi_g")


@dataclass(frozen=True)
class Posterior:
    config: GridConfig
    prior: Prior
    logw: np.ndarray  # (n_mu, n_sigma), normalised
    n_interval: int = 0
    n_censored: int = 0
    floor_hits: int = 0  # observations the grid called (almost) impossible
    refs: tuple[str, ...] = field(default_factory=tuple)

    # ---- construction -------------------------------------------------
    @staticmethod
    def from_prior(config: GridConfig | None = None, prior: Prior | None = None) -> "Posterior":
        config = config or GridConfig()
        prior = prior or Prior()
        mu, sigma = config.axes()
        lp_mu = -0.5 * ((mu - math.log(prior.median_g)) / prior.mu_sd) ** 2
        # lognormal prior on sigma with the stated mode; grid is log-spaced in
        # sigma, so the Jacobian is absorbed by weighting cells equally in log sigma
        loc = math.log(prior.sigma_mode) + prior.sigma_log_sd**2
        lp_sigma = -0.5 * ((np.log(sigma) - loc) / prior.sigma_log_sd) ** 2 - np.log(sigma)
        logw = lp_mu[:, None] + lp_sigma[None, :]
        return Posterior(config, prior, logw - logsumexp(logw))

    # ---- likelihood ---------------------------------------------------
    def log_likelihood(self, obs: Observation) -> np.ndarray:
        mu, sigma = self.config.axes()
        MU, SG = mu[:, None], sigma[None, :]

        def z(x: float) -> np.ndarray:
            return (math.log(x) - MU) / SG

        if obs.kind == "right_censored":
            if obs.lo_g <= 0.5:  # "wanted more than ~nothing" carries no information
                return np.zeros((self.config.n_mu, self.config.n_sigma))
            return np.maximum(log_ndtr(-z(obs.lo_g)), LOG_FLOOR)
        hi = max(float(obs.hi_g), 0.5)  # type: ignore[arg-type]
        if obs.lo_g <= 0.5:  # interval touching zero: P(D <= hi)
            return np.maximum(log_ndtr(z(hi)), LOG_FLOOR)
        return log_ndtr_diff(z(hi), z(obs.lo_g))

    def update(self, obs: Observation) -> "Posterior":
        ll = self.log_likelihood(obs)
        logw = self.logw + obs.weight * ll
        norm = logsumexp(logw)
        hit = bool(float(np.max(ll)) <= LOG_FLOOR + 1e-9)
        return replace(
            self,
            logw=logw - norm,
            n_interval=self.n_interval + (obs.kind == "interval"),
            n_censored=self.n_censored + (obs.kind == "right_censored"),
            floor_hits=self.floor_hits + hit,
            refs=self.refs + ((obs.ref,) if obs.ref else ()),
        )

    def update_all(self, observations: Iterable[Observation]) -> "Posterior":
        post = self
        for obs in observations:
            post = post.update(obs)
        return post

    # ---- summaries ----------------------------------------------------
    @property
    def weights(self) -> np.ndarray:
        return np.exp(self.logw)

    def atoms(self, n_nodes: int = 40, prune: float = 1e-7) -> tuple[np.ndarray, np.ndarray]:
        """Deterministic weighted atoms of the posterior *predictive* for D.

        Each retained grid cell contributes n_nodes equal-probability lognormal
        quantiles, so the spread includes meal-to-meal variation (sigma) and
        parameter uncertainty, not only uncertainty about the mean.
        Returns (values_g ascending, cumulative weights ending at 1).
        """
        w = self.weights
        mu, sigma = self.config.axes()
        keep = w > prune * float(w.max())
        wi = w[keep]
        wi = wi / wi.sum()
        MU = np.broadcast_to(mu[:, None], w.shape)[keep]
        SG = np.broadcast_to(sigma[None, :], w.shape)[keep]
        nodes = normal_nodes(n_nodes)
        vals = np.exp(MU[:, None] + SG[:, None] * nodes[None, :]).ravel()
        wts = np.repeat(wi / n_nodes, n_nodes)
        order = np.argsort(vals, kind="stable")
        return vals[order], np.cumsum(wts[order])

    def quantiles(self, qs: Sequence[float], atoms=None) -> list[float]:
        vals, cum = atoms if atoms is not None else self.atoms()
        idx = np.searchsorted(cum, np.asarray(qs, float), side="left")
        return [float(vals[min(i, len(vals) - 1)]) for i in idx]

    def cdf(self, x_g, atoms=None) -> np.ndarray:
        vals, cum = atoms if atoms is not None else self.atoms()
        idx = np.searchsorted(vals, np.asarray(x_g, float), side="right")
        return np.where(idx > 0, cum[np.maximum(idx - 1, 0)], 0.0)

    def sample(self, rng: np.random.Generator, size: int) -> np.ndarray:
        """Draw D from the posterior predictive (exact, not from atoms)."""
        w = self.weights.ravel()
        cell = rng.choice(w.size, size=size, p=w / w.sum())
        mu, sigma = self.config.axes()
        i, j = np.unravel_index(cell, self.logw.shape)
        return np.exp(mu[i] + sigma[j] * rng.standard_normal(size))

    def density(self, lo_g: float = 0.0, hi_g: float = 600.0, bins: int = 60, atoms=None) -> list[dict]:
        edges = np.linspace(lo_g, hi_g, bins + 1)
        c = self.cdf(edges, atoms)
        p = np.diff(c)
        return [
            {"g": float(0.5 * (edges[k] + edges[k + 1])), "p": float(p[k])}
            for k in range(bins)
        ]

    def diagnostics(self) -> dict:
        w = self.weights
        edge_mu = float(w[0, :].sum() + w[-1, :].sum())
        edge_sigma = float(w[:, 0].sum() + w[:, -1].sum())
        mu, sigma = self.config.axes()
        ess = float(1.0 / np.sum((w / w.sum()) ** 2))
        flags = []
        if edge_mu > 0.02:
            flags.append("POSTERIOR_AT_AMOUNT_BOUNDARY")
        if edge_sigma > 0.10:
            flags.append("POSTERIOR_AT_SPREAD_BOUNDARY")
        if self.floor_hits:
            flags.append("OBSERVATION_OUTSIDE_MODEL_RANGE")
        return {
            "boundary_mass_amount": edge_mu,
            "boundary_mass_spread": edge_sigma,
            "effective_cells": ess,
            "typical_median_g": float(math.exp(float(np.sum(w.sum(axis=1) * mu)))),
            "typical_spread_sigma": float(np.sum(w.sum(axis=0) * sigma)),
            "flags": flags,
        }

    def digest(self) -> str:
        h = hashlib.sha256()
        h.update(MODEL_VERSION.encode())
        h.update(repr((self.config, self.prior)).encode())
        for r in self.refs:
            h.update(b"|" + r.encode())
        return h.hexdigest()[:16]


def fit(
    observations: Sequence[Observation],
    config: GridConfig | None = None,
    prior: Prior | None = None,
    recency_half_life: float | None = None,
) -> Posterior:
    """Replay observations (oldest first) from the prior.

    recency_half_life, when set, down-weights older observations by
    0.5 ** (age_in_meals / half_life). It is a versioned, evaluated option and
    is off by default.
    """
    post = Posterior.from_prior(config, prior)
    n = len(observations)
    for k, obs in enumerate(observations):
        if recency_half_life:
            obs = replace(obs, weight=obs.weight * 0.5 ** ((n - 1 - k) / recency_half_life))
        post = post.update(obs)
    return post


def summarise(post: Posterior, atoms=None) -> dict:
    atoms = atoms if atoms is not None else post.atoms()
    q10, q25, q50, q75, q90 = post.quantiles([0.10, 0.25, 0.50, 0.75, 0.90], atoms)
    return {
        "median_g": q50,
        "p10_g": q10,
        "p25_g": q25,
        "p75_g": q75,
        "p90_g": q90,
        "n_interval": post.n_interval,
        "n_censored": post.n_censored,
        "model_version": MODEL_VERSION,
        "model_digest": post.digest(),
        "prior": post.prior.describe(),
        "diagnostics": post.diagnostics(),
    }
