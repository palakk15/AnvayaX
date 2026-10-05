"""Calibrated distributions and proper scoring rules, including joint wind.

Discrete forecasts retain their original, equally weighted members. Their CRPS,
threshold-weighted CRPS and vector scores are exact empirical-distribution
scores (not the finite-ensemble ``fair`` estimators). Continuous scalar scores
use deterministic adaptive quadrature; Gaussian wind uses fixed samples.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.special import ndtr, ndtri, gammainc, gammaincinv
from scipy.spatial.distance import cdist


@dataclass
class Distribution:
    kind: str
    parameters: dict

    def __post_init__(self):
        if self.kind in ("empirical", "empirical_wind"):
            members = np.asarray(self.parameters["members"], dtype=float)
            valid_shape = (members.ndim == 1 if self.kind == "empirical" else
                           members.ndim == 2 and members.shape[1] == 2)
            if not valid_shape or len(members) == 0 or not np.isfinite(members).all():
                raise ValueError("Empirical distributions need nonempty, finite members of the correct shape")
            # Keep original ordering, duplicates and masses; also ensure JSON serialization.
            self.parameters = {**self.parameters, "members": members.tolist()}

    def cdf(self, x):
        p = self.parameters
        x = np.asarray(x)
        if self.kind == "normal":
            return ndtr((x - p["mean"]) / p["std"])
        if self.kind == "hurdle_gamma":
            return np.where(x < 0, 0, p["dry"] + (1 - p["dry"]) * gammainc(
                p["shape"], np.maximum(x, 0) / p["scale"]))
        if self.kind == "point":
            return (x >= p["mean"]).astype(float)
        if self.kind == "empirical":
            members = np.sort(np.asarray(p["members"]))
            return np.searchsorted(members, x, side="right") / len(members)
        raise ValueError("A joint wind distribution has no scalar CDF")

    def quantile(self, q):
        p = self.parameters
        if self.kind == "normal":
            return p["mean"] + p["std"] * ndtri(q)
        if self.kind == "hurdle_gamma":
            q = np.asarray(q)
            wet_q = np.clip((q - p["dry"]) / (1 - p["dry"]), 0, 1)
            return np.where(q <= p["dry"], 0, gammaincinv(p["shape"], wet_q) * p["scale"])
        if self.kind == "point":
            return np.full_like(np.asarray(q, dtype=float), p["mean"])
        if self.kind == "empirical":
            q = np.asarray(q, dtype=float)
            if not np.isfinite(q).all() or np.any((q < 0) | (q > 1)):
                raise ValueError("Quantile probabilities must lie in [0, 1]")
            members = np.sort(np.asarray(p["members"]))
            indices = np.clip(np.ceil(q * len(members)).astype(int) - 1, 0, len(members) - 1)
            return members[indices]
        raise ValueError("Use joint samples to summarize vector wind")

    def mean(self):
        p = self.parameters
        if self.kind == "hurdle_gamma":
            return (1 - p["dry"]) * p["shape"] * p["scale"]
        if self.kind in ("empirical", "empirical_wind"):
            result = np.asarray(p["members"]).mean(axis=0)
            return float(result) if self.kind == "empirical" else result
        return np.asarray(p["mean"]) if self.kind == "wind" else p["mean"]

    def samples(self, size=32):
        """Return a deterministic representation with equal member weights.

        Empirical kinds always return *all* original members, ignoring ``size``.
        Callers must weight each returned member by 1 / len(returned_samples).
        """
        if self.kind in ("empirical", "empirical_wind"):
            return np.asarray(self.parameters["members"], dtype=float).copy()
        if self.kind != "wind":
            return self.quantile((np.arange(size) + 0.5) / size)
        # Deterministic antithetic normal samples, identical across comparisons.
        rng = np.random.default_rng(137)
        z = rng.normal(size=((size + 1) // 2, 2))
        z = np.concatenate([z, -z])[:size]
        return np.asarray(self.parameters["mean"]) + z @ np.linalg.cholesky(
            np.asarray(self.parameters["covariance"])).T

    def to_dict(self):
        return {"kind": self.kind, "parameters": self.parameters}


def raw_distribution(forecast):
    """Preserve a canonical ForecastRecord's raw deterministic/ensemble law."""
    if forecast.variable == "wind_uv":
        return Distribution("empirical_wind", {"members": forecast.value})
    values = np.asarray(forecast.value, dtype=float)
    if values.ndim == 0:
        return Distribution("point", {"mean": float(values)})
    return Distribution("empirical", {"members": values.tolist()})


def _is_wind(distribution):
    return distribution.kind in ("wind", "empirical_wind")


def _atoms(distribution):
    if distribution.kind == "point":
        return np.array([distribution.parameters["mean"]], dtype=float)
    if distribution.kind == "empirical":
        return np.asarray(distribution.parameters["members"], dtype=float)
    if distribution.kind == "hurdle_gamma":
        return np.array([0.0])
    return np.array([], dtype=float)


def scalar_grid(distributions, observation=None, thresholds=()):
    ds = [d for d in distributions if d is not None]
    low = min(float(d.quantile(0.00001)) for d in ds)
    high = max(float(d.quantile(0.99999)) for d in ds)
    atoms = np.concatenate([_atoms(d) for d in ds])
    if len(atoms):
        low, high = min(low, float(atoms.min())), max(high, float(atoms.max()))
    if observation is not None:
        low, high = min(low, float(observation)), max(high, float(observation))
    if all(d.kind == "hurdle_gamma" for d in ds):
        low = 0.0
    high = max(high, low + 1e-3)
    # Extra quantile knots resolve dry atoms, skewed tails and sharp normals.
    # Every discrete atom must be a boundary: integration cannot straddle a jump.
    points = [np.linspace(low, high, 160), atoms]
    for d in ds:
        points.append(np.asarray(d.quantile(np.linspace(0.001, 0.999, 41))).reshape(-1))
    if observation is not None:
        points.append(np.array([observation]))
    points.append(np.asarray([t for t in thresholds if low <= t <= high]))
    return np.unique(np.concatenate(points))


def score_terms(distributions, observation, tail_threshold=None, tail_weight=0.0):
    """Return linear b and quadratic Q for score(w)=w.Q.w-2b.w+c.

    Missing experts have zero columns and must receive zero mixture weight.
    Wind score combines energy and two component CRPS terms with equal weights.
    """
    active = [d for d in distributions if d is not None]
    if not active:
        raise ValueError("At least one distribution is required")
    if any(_is_wind(d) != _is_wind(active[0]) for d in active):
        raise ValueError("Scalar and joint wind distributions cannot be mixed")
    m = len(distributions)
    if _is_wind(active[0]):
        if tail_threshold is not None:
            raise ValueError("Scalar rainfall tail scores do not apply to joint wind")
        samples = [None if d is None else d.samples() for d in distributions]
        q = np.zeros((m, m))
        b = np.zeros(m)
        y = np.asarray(observation)
        for i, a in enumerate(samples):
            if a is None:
                continue
            distance_y = np.linalg.norm(a - y, axis=1) + np.abs(a - y).sum(axis=1)
            b[i] = -0.5 * float(distance_y.mean()) / 3
            for j, other in enumerate(samples):
                if other is not None:
                    distance = cdist(a, other) + cdist(a[:, :1], other[:, :1]) + cdist(a[:, 1:], other[:, 1:])
                    q[i, j] = -0.5 * float(distance.mean()) / 3
        return q, b, 0.0
    if all(d.kind in ("point", "empirical") for d in active):
        # CRPS = E|X-y| - E|X-X'|/2. For twCRPS above t, apply
        # the chaining function v(x)=max(x,t) before evaluating distances.
        samples = [None if d is None else _atoms(d) for d in distributions]
        q, b = np.zeros((m, m)), np.zeros(m)
        y = float(observation)
        for i, a in enumerate(samples):
            if a is None:
                continue
            distance_y = np.abs(a - y).mean()
            if tail_threshold is not None and tail_weight:
                distance_y += tail_weight * np.abs(
                    np.maximum(a, tail_threshold) - max(y, tail_threshold)).mean()
            b[i] = -0.5 * distance_y
            for j, other in enumerate(samples):
                if other is None:
                    continue
                distance = np.abs(a[:, None] - other[None, :]).mean()
                if tail_threshold is not None and tail_weight:
                    distance += tail_weight * np.abs(
                        np.maximum(a[:, None], tail_threshold) -
                        np.maximum(other[None, :], tail_threshold)).mean()
                q[i, j] = -0.5 * distance
        return q, b, 0.0
    grid = scalar_grid(distributions, observation, () if tail_threshold is None else (tail_threshold,))
    # Midpoint integration correctly handles point-mass jumps at observations.
    mid = (grid[1:] + grid[:-1]) / 2
    dx = np.diff(grid)
    if tail_threshold is not None:
        dx = dx * (1 + tail_weight * (mid >= tail_threshold))
    f = np.stack([np.zeros_like(mid) if d is None else d.cdf(mid) for d in distributions])
    h = (mid >= observation).astype(float)
    return (f * dx) @ f.T, (f * dx) @ h, float(np.dot(h, dx))


def mixture_score(distributions, weights, observation, tail_threshold=None):
    if tail_threshold is None:
        q, b, c = score_terms(distributions, observation)
        return max(0.0, float(weights @ q @ weights - 2 * b @ weights + c))
    if any(d is not None and _is_wind(d) for d in distributions):
        raise ValueError("Scalar rainfall tail scores do not apply to joint wind")
    # Share integration knots so their difference isolates the upper-tail
    # integral, without introducing a change in base quadrature error.
    q0, b0, c0 = score_terms(distributions, observation, tail_threshold, 0)
    q1, b1, c1 = score_terms(distributions, observation, tail_threshold, 1)
    return max(0.0, float(weights @ (q1 - q0) @ weights - 2 * (b1 - b0) @ weights + c1 - c0))


def mixture_summary(distributions, weights, quantiles=(0.1, 0.5, 0.9)):
    ds = [(d, float(w)) for d, w in zip(distributions, weights) if d is not None and w > 0]
    mean = sum(w * d.mean() for d, w in ds)
    requested = tuple(quantiles)
    probabilities = tuple(sorted(set(requested + (0.5,))))
    if _is_wind(ds[0][0]):
        member_speeds = [(np.linalg.norm(d.samples(128), axis=1), w) for d, w in ds]
        speeds = np.concatenate([s for s, _ in member_speeds])
        sample_weights = np.concatenate([np.full(len(s), w / len(s)) for s, w in member_speeds])
        order = np.argsort(speeds)
        # Inverse empirical CDF; interpolation would invent speeds between atoms.
        cumulative = np.cumsum(sample_weights[order])
        indices = np.minimum(np.searchsorted(cumulative, probabilities, side="left"), len(speeds) - 1)
        qs = speeds[order][indices]
        values = {str(q): float(v) for q, v in zip(probabilities, qs)}
        return {"mean": mean.tolist(), "median": values["0.5"],
                "speed_mean": float(np.dot(speeds, sample_weights)),
                "quantiles": {str(q): values[str(q)] for q in requested}}
    lo = min(float(d.quantile(1e-7)) for d, _ in ds)
    hi = max(float(d.quantile(1 - 1e-7)) for d, _ in ds)
    values = {}
    for probability in probabilities:
        left, right = lo, hi
        for _ in range(60):
            mid = (left + right) / 2
            f = sum(w * float(d.cdf(mid)) for d, w in ds)
            if f >= probability:
                right = mid
            else:
                left = mid
        values[str(probability)] = float(right)
    return {"mean": float(mean), "median": values["0.5"],
            "quantiles": {str(q): values[str(q)] for q in requested}}


def exceedance(distribution, threshold):
    if _is_wind(distribution):
        return float(np.mean(np.linalg.norm(distribution.samples(256), axis=1) > threshold))
    return float(1 - distribution.cdf(threshold))
