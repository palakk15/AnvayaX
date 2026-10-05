"""Train-only probabilistic competitors; see ppt.md for adaptations.

No method reads verifying values during prediction. The caller supplies a purged
training split, and supplies forward cross-fitted distributions to fixed BMA.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import digamma, expit, gammaln, logsumexp, ndtr

from .distributions import Distribution


class OptionalDependencyUnavailable(ImportError):
    """The optional competitor must be reported as skipped, not as a score."""


def _xgboost():
    try:
        import xgboost
    except (ImportError, OSError) as exc:
        raise OptionalDependencyUnavailable(
            "XGBoost competitor unavailable: install the research extra and ensure its native libraries load"
        ) from exc
    return xgboost


def _sources(sources):
    sources = list(sources)
    if not sources or len(set(sources)) != len(sources):
        raise ValueError("Provide nonempty, unique sources")
    return sources


def _raw_features(cases, variable, sources):
    """Source means, missing flags, forecast lead/season; equal-source spread.

    Each source receives equal mass regardless of its ensemble member count.
    The total spread includes both within-source and between-source variance.
    """
    rows, spreads = [], []
    dims = 2 if variable == "wind_uv" else 1
    for case in cases:
        if case.variable != variable:
            raise ValueError(f"Expected {variable} cases")
        means, variances, active, flags = [], [], [], []
        for source in sources:
            forecast = case.forecasts.get(source)
            if forecast is None:
                means.extend([np.nan] * dims)
                flags.append(0.0)
                continue
            values = np.asarray(forecast.value, dtype=float).reshape(-1, dims)
            mean = values.mean(axis=0)
            variance = values.var(axis=0)
            means.extend(np.log1p(mean) if variable in ("rainfall", "gust") else mean)
            active.append(mean)
            variances.append(variance)
            flags.append(1.0)
        if not active:
            raise ValueError("No eligible source forecasts for a baseline case")
        first = next(case.forecasts[s] for s in sources if s in case.forecasts)
        lead = (first.valid_start - first.initialization_time).total_seconds() / 86400
        day = first.valid_start.timetuple().tm_yday
        rows.append(means + flags + [lead, np.sin(day * 2 * np.pi / 365.25),
                                     np.cos(day * 2 * np.pi / 365.25)])
        spreads.append(np.mean(variances, axis=0) + np.var(active, axis=0))
    return np.asarray(rows), np.asarray(spreads)


def _ridge(x, y, strength=0.05):
    penalty = np.eye(x.shape[1]) * strength
    penalty[0, 0] = 1e-8
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


def _positive_covariance(errors, floor=0.0625):
    covariance = errors.T @ errors / max(len(errors), 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    return (eigenvectors * np.maximum(eigenvalues, floor)) @ eigenvectors.T


class _JSONModel:
    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), allow_nan=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class EMOSRegressor(_JSONModel):
    """Pooled Gaussian EMOS and explicitly adapted rainfall/vector regressions."""

    def __init__(self, variable, sources):
        if variable not in ("temperature", "rainfall", "wind_uv", "gust"):
            raise ValueError("Unsupported EMOS variable")
        self.variable, self.sources = variable, _sources(sources)
        self.parameters = None
        self.training_end = None

    def _matrix(self, cases, fit=False):
        raw, spread = _raw_features(cases, self.variable, self.sources)
        if fit:
            # An entirely absent training source cannot contribute a learned mean.
            medians = np.array([np.median(c[np.isfinite(c)]) if np.isfinite(c).any() else 0
                                for c in raw.T])
            filled = np.where(np.isfinite(raw), raw, medians)
            scale = filled.std(axis=0)
            self.transform = {"impute": medians.tolist(), "center": filled.mean(axis=0).tolist(),
                              "scale": np.where(scale < 1e-6, 1.0, scale).tolist()}
        filled = np.where(np.isfinite(raw), raw, self.transform["impute"])
        x = (filled - self.transform["center"]) / self.transform["scale"]
        return np.column_stack([np.ones(len(x)), x]), spread

    def fit(self, cases):
        cases = list(cases)
        if len(cases) < 12:
            raise ValueError("EMOS needs at least 12 training cases")
        x, spread = self._matrix(cases, fit=True)
        y = np.asarray([c.observation.value for c in cases], dtype=float)
        self.training_end = max(c.observation.available_at for c in cases).isoformat()
        if self.variable in ("rainfall", "gust"):
            self.parameters = self._fit_hurdle(x, y)
        elif self.variable == "wind_uv":
            beta = _ridge(x, y)
            covariance = _positive_covariance(y - x @ beta)
            self.parameters = {"kind": "wind", "beta": beta.tolist(), "covariance": covariance.tolist(),
                               "method": "pooled_joint_gaussian_regression"}
        else:
            beta = _ridge(x, y)
            s = spread[:, 0]
            s_scale = max(float(s.mean()), 1.0)
            s = s / s_scale
            initial = np.r_[beta, max(float(np.mean((y - x @ beta) ** 2)), 0.0625), 0.1]

            def objective(theta):
                mean = x @ theta[:-2]
                std = np.sqrt(theta[-2] + theta[-1] * s)
                z = (y - mean) / std
                phi = np.exp(-z * z / 2) / np.sqrt(2 * np.pi)
                h = 2 * ndtr(z) - 1
                loss = np.mean(std * (z * h + 2 * phi - 1 / np.sqrt(np.pi)))
                penalty = 1e-5 * np.dot(theta[1:-2], theta[1:-2])
                dvariance = (2 * phi - 1 / np.sqrt(np.pi)) / (2 * std)
                gradient = np.r_[-x.T @ h / len(y), dvariance.mean(), np.mean(dvariance * s)]
                gradient[1:-2] += 2e-5 * theta[1:-2]
                return loss + penalty, gradient

            result = minimize(objective, initial, jac=True, method="L-BFGS-B",
                              bounds=[(None, None)] * x.shape[1] + [(0.0625, None), (0, None)],
                              options={"maxiter": 300, "ftol": 1e-10})
            if not np.isfinite(result.fun):
                raise ValueError("Nonfinite EMOS optimization")
            theta = result.x
            self.parameters = {"kind": "normal", "beta": theta[:-2].tolist(),
                               "variance_intercept": float(theta[-2]),
                               "variance_slope": float(theta[-1] / s_scale),
                               "method": "pooled_gaussian_emos_minimum_crps", "converged": bool(result.success)}
        self.parameters["n_training"] = len(cases)
        return self

    @staticmethod
    def _fit_hurdle(x, y):
        wet = y > 0

        def occurrence_loss(beta):
            eta = x @ beta
            loss = np.mean(np.logaddexp(0, eta) - wet * eta) + 0.005 * np.dot(beta[1:], beta[1:])
            gradient = x.T @ (expit(eta) - wet) / len(y)
            gradient[1:] += 0.01 * beta[1:]
            return loss, gradient

        occurrence_result = minimize(occurrence_loss, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B")
        occurrence = occurrence_result.x
        beta = np.zeros(x.shape[1])
        beta[0] = np.log(max(float(y[wet].mean()) if wet.any() else 1, 1e-4))
        shape = 1.0
        amount_converged = None
        if wet.sum() >= 6:
            xw, yw = x[wet], y[wet]
            beta = _ridge(xw, np.log(yw))

            def gamma_loss(theta):
                eta = np.clip(xw @ theta[:-1], -15, 18)
                k = np.exp(theta[-1])
                ratio = yw * np.exp(-eta)
                loss = np.mean(gammaln(k) - k * np.log(k) + k * eta
                               - (k - 1) * np.log(yw) + k * ratio)
                loss += 0.002 * np.dot(theta[1:-1], theta[1:-1])
                deta = k * (1 - ratio) * ((xw @ theta[:-1] > -15) & (xw @ theta[:-1] < 18))
                grad_beta = xw.T @ deta / len(yw)
                grad_beta[1:] += 0.004 * theta[1:-1]
                grad_k = k * np.mean(digamma(k) - np.log(k) - 1 + eta - np.log(yw) + ratio)
                return loss, np.r_[grad_beta, grad_k]

            result = minimize(gamma_loss, np.r_[beta, 0.0], jac=True, method="L-BFGS-B",
                              bounds=[(None, None)] * len(beta) + [(np.log(0.15), np.log(100))],
                              options={"maxiter": 250})
            if np.isfinite(result.fun):
                beta, shape = result.x[:-1], float(np.exp(result.x[-1]))
                amount_converged = bool(result.success)
        return {"kind": "hurdle_gamma", "occurrence": occurrence.tolist(), "beta": beta.tolist(),
                "shape": shape, "method": "adapted_two_part_logistic_gamma_regression",
                "wet_training": int(wet.sum()), "zero_threshold": 0.0,
                "occurrence_converged": bool(occurrence_result.success), "amount_converged": amount_converged,
                "amount_fit": "gamma_likelihood" if wet.sum() >= 6 else "constant_insufficient_wet_support"}

    def predict(self, cases):
        if self.parameters is None:
            raise ValueError("Fit EMOS before prediction")
        cases = list(cases)
        if not cases:
            return []
        x, spread = self._matrix(cases)
        p = self.parameters
        mean = x @ np.asarray(p["beta"])
        if p["kind"] == "normal":
            variance = p["variance_intercept"] + p["variance_slope"] * spread[:, 0]
            return [Distribution("normal", {"mean": float(m), "std": float(np.sqrt(v))})
                    for m, v in zip(mean, variance)]
        if p["kind"] == "wind":
            return [Distribution("wind", {"mean": m.tolist(), "covariance": p["covariance"]}) for m in mean]
        wet = np.clip(expit(x @ p["occurrence"]), 1e-6, 1 - 1e-6)
        positive_mean = np.exp(np.clip(mean, -15, 18))
        return [Distribution("hurdle_gamma", {"dry": float(1 - probability), "shape": p["shape"],
                                              "scale": float(m / p["shape"])})
                for probability, m in zip(wet, positive_mean)]

    def to_dict(self):
        if self.parameters is None:
            raise ValueError("Cannot serialize an unfitted EMOS model")
        return {"model": "emos_regressor", "variable": self.variable, "sources": self.sources,
                "training_end": self.training_end, "transform": self.transform, "parameters": self.parameters}

    @classmethod
    def from_dict(cls, value):
        obj = cls(value["variable"], value["sources"])
        obj.training_end, obj.transform, obj.parameters = value["training_end"], value["transform"], value["parameters"]
        return obj


def _log_density(distribution, observation):
    p = distribution.parameters
    if distribution.kind == "normal":
        return -0.5 * ((float(observation) - p["mean"]) / p["std"]) ** 2 - np.log(p["std"]) - 0.5 * np.log(2 * np.pi)
    if distribution.kind == "hurdle_gamma":
        y = float(observation)
        if y < 0:
            return -np.inf
        if y == 0:
            return np.log(p["dry"]) if p["dry"] > 0 else -np.inf
        if p["dry"] >= 1:
            return -np.inf
        return (np.log1p(-p["dry"]) + (p["shape"] - 1) * np.log(y) - y / p["scale"]
                - gammaln(p["shape"]) - p["shape"] * np.log(p["scale"]))
    if distribution.kind == "wind":
        error = np.asarray(observation) - p["mean"]
        covariance = np.asarray(p["covariance"])
        sign, logdet = np.linalg.slogdet(covariance)
        if sign <= 0:
            raise ValueError("Wind covariance must be positive definite")
        return -np.log(2 * np.pi) - 0.5 * (logdet + error @ np.linalg.solve(covariance, error))
    raise ValueError("Fixed-component BMA requires normal, hurdle_gamma, or wind distributions")


class BMABlend(_JSONModel):
    """Maximum-likelihood weights on FIXED cross-fitted component distributions.

    This is not a full Bayesian posterior or joint component-parameter BMA fit.
    With incomplete rows, the M step optimizes the availability-conditioned
    mixture likelihood instead of treating missing components as bad forecasts.
    """

    def __init__(self, sources):
        self.sources = _sources(sources)
        self.weights = None

    def _mask(self, distributions):
        if any(len(row) != len(self.sources) for row in distributions):
            raise ValueError("Distribution columns must match sources")
        mask = np.asarray([[d is not None for d in row] for row in distributions])
        if not mask.size or not mask.any(axis=1).all():
            raise ValueError("Each case needs an eligible source distribution")
        return mask

    def fit(self, distributions, observations, *, iterations=200, tolerance=1e-8):
        if iterations < 1 or tolerance <= 0:
            raise ValueError("BMA iterations and tolerance must be positive")
        distributions, observations = list(distributions), list(observations)
        if len(distributions) != len(observations) or not observations:
            raise ValueError("Matched, nonempty distribution/observation rows required")
        mask = self._mask(distributions)
        if not mask.any(axis=0).all():
            raise ValueError("BMA cannot learn weights for sources absent from training")
        # Conditional likelihoods cannot identify relative mass across source
        # groups that never appear together, even if every source occurs alone.
        adjacency = mask.astype(int).T @ mask.astype(int) > 0
        reached = {0}
        while True:
            expanded = reached | {j for i in reached for j in np.flatnonzero(adjacency[i])}
            if expanded == reached:
                break
            reached = expanded
        if len(reached) != len(self.sources):
            raise ValueError("BMA needs connected source co-availability to identify relative weights")
        kinds = {d.kind for row in distributions for d in row if d is not None}
        if len(kinds) != 1:
            raise ValueError("BMA components must use the same distribution family")
        likelihood = np.array([[_log_density(d, y) if d is not None else -np.inf for d in row]
                               for row, y in zip(distributions, observations)])
        if not np.isfinite(likelihood).any(axis=1).all():
            raise ValueError("Some observations have zero density under every component")
        weights = np.full(len(self.sources), 1 / len(self.sources))
        trace = []
        for _ in range(iterations):
            logweights = np.log(np.maximum(weights, 1e-15))
            joined = likelihood + logweights
            denominator = logsumexp(joined, axis=1)
            trace.append(float(np.sum(denominator - np.log(mask @ weights))))
            responsibilities = np.exp(joined - denominator[:, None])
            if mask.all():
                updated = responsibilities.mean(axis=0)
            else:
                def mstep(logits):
                    masked = np.where(mask, logits, -np.inf)
                    normalizer = logsumexp(masked, axis=1)
                    probabilities = np.exp(masked - normalizer[:, None])
                    loss = np.sum(normalizer) - np.sum(responsibilities * logits)
                    gradient = (probabilities - responsibilities).sum(axis=0)
                    return loss, gradient
                result = minimize(mstep, logweights, jac=True, method="L-BFGS-B", options={"maxiter": 60})
                updated = np.exp(result.x - logsumexp(result.x))
            updated = np.maximum(updated, 1e-15)
            updated /= updated.sum()
            delta = np.max(np.abs(updated - weights))
            weights = updated
            if delta < tolerance:
                break
        self.weights = weights
        final_likelihood = logsumexp(likelihood + np.log(weights), axis=1) - np.log(mask @ weights)
        self.fit_info = {"method": "fixed_component_bma_em", "n_training": len(observations),
                         "iterations": len(trace), "log_likelihood": trace,
                         "final_log_likelihood": float(final_likelihood.sum()), "converged": bool(delta < tolerance),
                         "component_training": "caller_supplied_forward_cross_fitted"}
        return self

    def predict_weights(self, distributions):
        if self.weights is None:
            raise ValueError("Fit BMA before prediction")
        distributions = list(distributions)
        if not distributions:
            return np.empty((0, len(self.sources)))
        mask = self._mask(distributions)
        weights = mask * self.weights
        return weights / weights.sum(axis=1, keepdims=True)

    def to_dict(self):
        if self.weights is None:
            raise ValueError("Cannot serialize unfitted BMA")
        return {"model": "fixed_component_bma", "sources": self.sources,
                "weights": self.weights.tolist(), "fit_info": self.fit_info}

    @classmethod
    def from_dict(cls, value):
        obj = cls(value["sources"])
        obj.weights = np.asarray(value["weights"], dtype=float)
        if obj.weights.shape != (len(obj.sources),) or not np.isfinite(obj.weights).all() or (obj.weights <= 0).any():
            raise ValueError("Invalid serialized BMA simplex")
        obj.weights /= obj.weights.sum()
        obj.fit_info = value["fit_info"]
        return obj


class XGBoostStack(_JSONModel):
    """Direct distributional tree heads with forward-fit residual uncertainty.

    Native Booster JSON is embedded in ordinary JSON; pickle is never used.
    All parameters and the number of rounds are fixed before test evaluation.
    """

    def __init__(self, variable, sources, *, seed=42, rounds=64):
        if variable not in ("temperature", "rainfall", "wind_uv", "gust"):
            raise ValueError("Unsupported XGBoost variable")
        if rounds < 1:
            raise ValueError("rounds must be positive")
        self.variable, self.sources = variable, _sources(sources)
        self.seed, self.rounds = seed, rounds
        self.heads = None

    def _matrix(self, cases):
        raw, spread = _raw_features(cases, self.variable, self.sources)
        return np.column_stack([raw, np.log1p(spread)]).astype(np.float32)

    def _fit_head(self, x, y, objective):
        y = np.asarray(y)
        if len(y) < 6 or np.ptp(y) < 1e-10:
            return {"constant": float(y.mean()) if len(y) else 1.0}
        xgb = _xgboost()
        params = {"objective": objective, "max_depth": 3, "eta": 0.06, "min_child_weight": 4,
                  "subsample": 1.0, "colsample_bytree": 1.0, "lambda": 2.0,
                  "tree_method": "hist", "nthread": 2, "seed": self.seed, "verbosity": 0}
        return xgb.train(params, xgb.DMatrix(x, label=y, nthread=2), num_boost_round=self.rounds)

    @staticmethod
    def _head_predict(head, x):
        if isinstance(head, dict):
            return np.full(len(x), head["constant"])
        return head.predict(_xgboost().DMatrix(x, nthread=2)).astype(float)

    def _mean_heads(self, x, y):
        if self.variable in ("rainfall", "gust"):
            wet = y > 0
            return {"mean": self._fit_head(x[wet], y[wet], "reg:gamma"),
                    "wet": self._fit_head(x, wet.astype(float), "binary:logistic")}
        if self.variable == "wind_uv":
            return {f"mean_{j}": self._fit_head(x, y[:, j], "reg:squarederror") for j in (0, 1)}
        return {"mean": self._fit_head(x, y, "reg:squarederror")}

    def _means(self, heads, x):
        if self.variable == "wind_uv":
            return np.column_stack([self._head_predict(heads[f"mean_{j}"], x) for j in (0, 1)])
        return self._head_predict(heads["mean"], x)

    def fit(self, cases):
        _xgboost()  # Do not silently replace an unavailable competitor with constants.
        cases = sorted(cases, key=lambda c: c.issuance_time)
        if len(cases) < 24:
            raise ValueError("XGBoost needs at least 24 training cases")
        x = self._matrix(cases)
        y = np.asarray([c.observation.value for c in cases], dtype=float)
        times = sorted({c.issuance_time for c in cases})
        residual_x, residual_y = [], []
        boundaries = [times[len(times) // 2], times[3 * len(times) // 4]] if len(times) >= 4 else []
        for i, left in enumerate(boundaries):
            past = np.array([c.observation.available_at < left and c.observation.valid_end < left for c in cases])
            future = np.array([c.issuance_time >= left and
                               (i == len(boundaries) - 1 or c.issuance_time < boundaries[i + 1]) for c in cases])
            if past.sum() < 12 or not future.any():
                continue
            heads = self._mean_heads(x[past], y[past])
            errors = y[future] - self._means(heads, x[future])
            selected = y[future] > 0 if self.variable in ("rainfall", "gust") else np.ones(future.sum(), dtype=bool)
            residual_x.extend(x[future][selected])
            residual_y.extend(errors[selected])
        self.heads = self._mean_heads(x, y)
        self.training_end = max(c.observation.available_at for c in cases).isoformat()
        if len(residual_y) >= 12:
            rx, errors = np.asarray(residual_x), np.asarray(residual_y)
            uncertainty_fit = "purged_forward_cross_fitted_residuals"
        else:
            selected = y > 0 if self.variable in ("rainfall", "gust") else np.ones(len(y), dtype=bool)
            rx = x[selected]
            errors = y[selected] - self._means(self.heads, rx) if selected.any() else np.empty(0)
            uncertainty_fit = "in_sample_residuals_insufficient_forward_support"
        if self.variable == "wind_uv":
            for j in (0, 1):
                self.heads[f"variance_{j}"] = self._fit_head(rx, np.maximum(errors[:, j] ** 2, 0.0625), "reg:gamma")
            covariance = _positive_covariance(errors)
            self.correlation = float(np.clip(covariance[0, 1] / np.sqrt(covariance[0, 0] * covariance[1, 1]), -0.95, 0.95))
        else:
            self.heads["variance"] = self._fit_head(rx, np.maximum(errors ** 2, 0.0625), "reg:gamma")
            self.correlation = None
        self.fit_info = {"method": "direct_distributional_xgboost", "n_training": len(cases),
                         "uncertainty_training": uncertainty_fit, "n_residual_training": len(errors),
                         "nthread": 2, "rounds": self.rounds}
        return self

    def predict(self, cases):
        if self.heads is None:
            raise ValueError("Fit XGBoost before prediction")
        cases = list(cases)
        if not cases:
            return []
        x = self._matrix(cases)
        means = self._means(self.heads, x)
        if self.variable == "wind_uv":
            variances = np.column_stack([np.maximum(self._head_predict(self.heads[f"variance_{j}"], x), 0.0625)
                                        for j in (0, 1)])
            distributions = []
            for mean, variance in zip(means, variances):
                cross = self.correlation * np.sqrt(np.prod(variance))
                covariance = [[float(variance[0]), float(cross)], [float(cross), float(variance[1])]]
                distributions.append(Distribution("wind", {"mean": mean.tolist(), "covariance": covariance}))
            return distributions
        variances = np.maximum(self._head_predict(self.heads["variance"], x), 0.0625)
        if self.variable == "temperature":
            return [Distribution("normal", {"mean": float(m), "std": float(np.sqrt(v))}) for m, v in zip(means, variances)]
        means = np.maximum(means, 1e-4)
        wet = np.clip(self._head_predict(self.heads["wet"], x), 1e-6, 1 - 1e-6)
        shapes = np.clip(means ** 2 / variances, 0.15, 100)
        return [Distribution("hurdle_gamma", {"dry": float(1 - probability), "shape": float(shape),
                                              "scale": float(mean / shape)})
                for mean, shape, probability in zip(means, shapes, wet)]

    def to_dict(self):
        if self.heads is None:
            raise ValueError("Cannot serialize unfitted XGBoost")
        heads = {key: head if isinstance(head, dict) else {"booster_json": head.save_raw(raw_format="json").decode("utf-8")}
                 for key, head in self.heads.items()}
        return {"model": "xgboost_distributional_stack", "variable": self.variable, "sources": self.sources,
                "seed": self.seed, "rounds": self.rounds, "training_end": self.training_end,
                "heads": heads, "correlation": self.correlation, "fit_info": self.fit_info}

    @classmethod
    def from_dict(cls, value):
        xgb = _xgboost()
        obj = cls(value["variable"], value["sources"], seed=value["seed"], rounds=value["rounds"])
        obj.heads = {}
        for key, head in value["heads"].items():
            if "constant" in head:
                obj.heads[key] = head
            else:
                booster = xgb.Booster(params={"nthread": 2})
                booster.load_model(bytearray(head["booster_json"], "utf-8"))
                obj.heads[key] = booster
        obj.training_end = value["training_end"]
        obj.correlation, obj.fit_info = value["correlation"], value["fit_info"]
        return obj
