"""Small pooled source calibrators, serialized as inspectable JSON parameters."""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from .distributions import Distribution


def raw_mean(forecast):
    values = np.asarray(forecast.value)
    return values.mean(axis=0) if values.ndim else values.item()


def predictors(f):
    raw = np.asarray(raw_mean(f))
    lead = (f.valid_start - f.initialization_time).total_seconds() / (3600 * 240)
    day = f.valid_start.timetuple().tm_yday
    if f.variable == "wind_uv":
        return np.array([1, raw[0] / 10, raw[1] / 10, lead, np.sin(day * 2 * np.pi / 365.25)])
    value = float(raw)
    value = np.log1p(value) if f.variable in ("rainfall", "gust") else value / 20
    return np.array([1, value, lead, np.sin(day * 2 * np.pi / 365.25), np.cos(day * 2 * np.pi / 365.25)])


def ridge(x, y, alpha=1.0):
    penalty = np.eye(x.shape[1]) * alpha
    penalty[0, 0] = 1e-8
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


class CalibratorBank:
    def __init__(self, variable, sources):
        self.variable = variable
        self.sources = list(sources)
        self.parameters = {}
        self.training_end = None

    def fit(self, cases):
        if not cases:
            raise ValueError("Cannot calibrate without training cases")
        self.training_end = max(c.observation.available_at for c in cases).isoformat()
        for source in self.sources:
            rows = [c for c in cases if source in c.forecasts]
            if len(rows) < 12:
                continue
            x = np.stack([predictors(c.forecasts[source]) for c in rows])
            y = np.asarray([c.observation.value for c in rows])
            if self.variable in ("rainfall", "gust"):
                wet = y > 0.1
                def objective(beta, x=x, wet=wet):
                    p = np.clip(expit(x @ beta), 1e-8, 1 - 1e-8)
                    loss = -np.sum(wet * np.log(p) + (1 - wet) * np.log(1 - p)) + 0.5 * np.dot(beta[1:], beta[1:])
                    grad = x.T @ (p - wet) + np.r_[0, beta[1:]]
                    return loss, grad
                occurrence = minimize(objective, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B").x
                if wet.sum() < 6:
                    beta = np.zeros(x.shape[1])
                    beta[0] = np.log(max(float(y[wet].mean()) if wet.any() else 1, 0.1))
                    shape = 1.0
                else:
                    beta = ridge(x[wet], np.log(np.maximum(y[wet], 0.1)))
                    residual = np.log(y[wet]) - x[wet] @ beta
                    beta[0] += min(float(np.var(residual)) / 2, 1.0)
                    means = np.exp(np.clip(x[wet] @ beta, -5, 10))
                    shape = float(np.clip(1 / max(np.mean(((y[wet] - means) / means) ** 2), 0.05), 0.15, 30))
                params = {"kind": "hurdle_gamma", "occurrence": occurrence.tolist(), "beta": beta.tolist(), "shape": shape}
            elif self.variable == "wind_uv":
                beta = ridge(x, y)
                errors = y - x @ beta
                cov = np.cov(errors, rowvar=False) + np.eye(2) * 0.25
                params = {"kind": "wind", "beta": beta.tolist(), "covariance": cov.tolist()}
            else:
                beta = ridge(x, y)
                std = max(float(np.sqrt(np.mean((y - x @ beta) ** 2))), 0.25)
                params = {"kind": "normal", "beta": beta.tolist(), "std": std}
            params["n_training"] = len(rows)
            params["versions"] = sorted({c.forecasts[source].model_version for c in rows})
            params["durations_hours"] = sorted({(c.forecasts[source].valid_end - c.forecasts[source].valid_start).total_seconds() / 3600 for c in rows})
            params["initialization_sources"] = list({c.forecasts[source].initialization_source for c in rows})
            params["lead_range"] = [min((c.forecasts[source].valid_start - c.forecasts[source].initialization_time).total_seconds() / 3600 for c in rows),
                                    max((c.forecasts[source].valid_start - c.forecasts[source].initialization_time).total_seconds() / 3600 for c in rows)]
            self.parameters[source] = params
        return self

    def distribution(self, source, forecast):
        p = self.parameters.get(source)
        if p is None or forecast.model_version not in p["versions"]:
            return None
        duration = (forecast.valid_end - forecast.valid_start).total_seconds() / 3600
        if duration not in p.get("durations_hours", [duration]):
            return None
        if forecast.initialization_source not in p.get("initialization_sources", [forecast.initialization_source]):
            return None
        lead = (forecast.valid_start - forecast.initialization_time).total_seconds() / 3600
        if not p["lead_range"][0] <= lead <= p["lead_range"][1]:
            return None
        x = predictors(forecast)
        mu = x @ np.asarray(p["beta"])
        if p["kind"] == "hurdle_gamma":
            mean_positive = float(np.exp(np.clip(mu, -5, 10)))
            return Distribution("hurdle_gamma", {"dry": float(1 - np.clip(expit(x @ p["occurrence"]), 1e-5, 1 - 1e-5)),
                                                 "shape": p["shape"], "scale": mean_positive / p["shape"]})
        if p["kind"] == "wind":
            return Distribution("wind", {"mean": mu.tolist(), "covariance": p["covariance"]})
        return Distribution("normal", {"mean": float(mu), "std": p["std"]})

    def predict(self, case):
        return [self.distribution(s, case.forecasts[s]) if s in case.forecasts else None for s in self.sources]

    def to_dict(self):
        return {"variable": self.variable, "sources": self.sources, "training_end": self.training_end, "parameters": self.parameters}

    @classmethod
    def from_dict(cls, value):
        obj = cls(value["variable"], value["sources"])
        obj.parameters = value["parameters"]
        obj.training_end = value["training_end"]
        return obj
