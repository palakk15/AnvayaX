"""Behavioral checks for separately fitted probabilistic competitors."""
from datetime import datetime, timedelta, timezone
import builtins
import json

import numpy as np
import pytest

from bharatblend.baselines import BMABlend, EMOSRegressor, OptionalDependencyUnavailable, XGBoostStack
from bharatblend.dataset import Case
from bharatblend.distributions import Distribution
from bharatblend.schemas import ForecastRecord, ObservationRecord


def sample_cases(variable="temperature", size=180, seed=42):
    rng = np.random.default_rng(seed)
    cases = []
    for i in range(size):
        init = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        signal = rng.normal()
        spread = 0.5 if i % 2 else 3.0
        if variable == "temperature":
            forecasts = ([18 + 4 * signal - spread, 18 + 4 * signal + spread],
                         [10 + 2 * signal - spread, 10 + 2 * signal + spread])
            y = 4 + 0.7 * np.mean(forecasts[0]) + 0.4 * np.mean(forecasts[1]) + rng.normal(scale=spread)
            units = "degC"
        elif variable == "rainfall":
            mean = float(np.exp(1.5 + signal))
            forecasts = ([mean * 0.8, mean * 1.2], [mean * 1.5])
            y = float(rng.gamma(4, mean / 4)) if rng.random() < 1 / (1 + np.exp(-2 * signal)) else 0.0
            units = "mm"
        else:
            u, v = float(2 + signal), float(1 - signal)
            forecasts = ([[u, v], [u + 1, v + 1]], [[u - 2, v + 3]])
            error = rng.normal(size=2)
            y = [float(1 + 2 * u + error[0]), float(-1 + 0.5 * v + 0.8 * error[0] + 0.4 * error[1])]
            units = "m/s"
        common = dict(location_id="fixture", latitude=20, longitude=75, variable=variable, units=units,
                      valid_start=init + timedelta(days=1), valid_end=init + timedelta(days=2), data_kind="synthetic")
        obs = ObservationRecord(**common, observation_id=str(i), available_at=init + timedelta(days=2, hours=4), value=y)
        fs = {s: ForecastRecord(**common, source_id=s, model_version="v1", initialization_time=init,
                                available_at=init + timedelta(hours=2), value=value)
              for s, value in zip(("a", "b"), forecasts)}
        cases.append(Case(obs, fs, init + timedelta(hours=4), {}))
    return cases


def changed_observations(cases):
    return [Case(c.observation.model_copy(update={"value": [9999, -9999] if c.variable == "wind_uv" else 9999}),
                 c.forecasts, c.issuance_time, c.recent_skill) for c in cases]


def test_gaussian_emos_learns_bias_and_spread_on_training_only():
    cases = sample_cases(size=280)
    model = EMOSRegressor("temperature", ["a", "b"]).fit(cases[:200])
    predictions = model.predict(cases[200:])
    y = np.array([c.observation.value for c in cases[200:]])
    learned_mse = np.mean((np.array([d.mean() for d in predictions]) - y) ** 2)
    raw_mse = np.mean((np.array([np.mean(c.forecasts["b"].value) for c in cases[200:]]) - y) ** 2)
    assert learned_mse < raw_mse * 0.2
    assert model.parameters["variance_slope"] > 0.1
    assert all(d.parameters["std"] >= 0.25 for d in predictions)
    assert [d.to_dict() for d in predictions] == [d.to_dict() for d in model.predict(changed_observations(cases[200:]))]
    assert model.training_end == cases[199].observation.available_at.isoformat()


@pytest.mark.parametrize("variable", ["temperature", "rainfall", "wind_uv"])
def test_emos_roundtrip_and_missing_source(variable, tmp_path):
    cases = sample_cases(variable, size=80)
    model = EMOSRegressor(variable, ["a", "b"]).fit(cases[:60])
    path = tmp_path / "emos.json"
    model.save(path)
    restored = EMOSRegressor.load(path)
    assert [d.to_dict() for d in model.predict(cases[60:])] == [d.to_dict() for d in restored.predict(cases[60:])]
    c = cases[-1]
    missing = Case(c.observation, {"a": c.forecasts["a"]}, c.issuance_time, {})
    prediction = restored.predict([missing])[0]
    assert np.isfinite(prediction.mean()).all()
    with pytest.raises(ValueError, match="eligible"):
        restored.predict([Case(c.observation, {}, c.issuance_time, {})])


def test_hurdle_emos_learns_occurrence_and_positive_amounts():
    cases = sample_cases("rainfall", size=240)
    model = EMOSRegressor("rainfall", ["a", "b"]).fit(cases[:180])
    test = cases[180:]
    dists = model.predict(test)
    raw = np.array([np.mean(c.forecasts["a"].value) for c in test])
    wet_prob = np.array([1 - d.cdf(0) for d in dists])
    assert wet_prob[raw > np.median(raw)].mean() > wet_prob[raw <= np.median(raw)].mean() + 0.3
    for d in dists:
        assert 0 < d.cdf(0) < 1
        assert d.cdf(0) <= d.cdf(10) <= d.cdf(100)
        assert d.parameters["shape"] > 0 and d.parameters["scale"] > 0
        assert d.quantile(0.99) >= d.quantile(0.5) >= 0
    assert "adapted" in model.parameters["method"]


def test_hurdle_emos_all_dry_stays_finite():
    cases = sample_cases("rainfall", size=30)
    cases = [Case(c.observation.model_copy(update={"value": 0.0}), c.forecasts, c.issuance_time, {}) for c in cases]
    model = EMOSRegressor("rainfall", ["a", "b"]).fit(cases)
    dist = model.predict(cases[:1])[0]
    assert dist.parameters["dry"] > 0.99
    assert np.isfinite(dist.quantile(0.9999999))


def test_joint_wind_learns_cross_component_dependence():
    cases = sample_cases("wind_uv", size=180)
    model = EMOSRegressor("wind_uv", ["a", "b"]).fit(cases[:140])
    predictions = model.predict(cases[140:])
    cov = np.asarray(predictions[0].parameters["covariance"])
    assert np.linalg.eigvalsh(cov).min() > 0
    assert cov[0, 1] > 0.3
    y = np.asarray([c.observation.value for c in cases[140:]])
    assert np.mean((np.array([d.mean() for d in predictions]) - y) ** 2) < 2


def test_fixed_component_bma_em_recovers_gaussian_mixture_and_monotone_likelihood():
    rng = np.random.default_rng(42)
    y = np.r_[rng.normal(-4, 0.5, 320), rng.normal(4, 0.5, 80)]
    row = [Distribution("normal", {"mean": -4, "std": 0.5}), Distribution("normal", {"mean": 4, "std": 0.5})]
    model = BMABlend(["a", "b"]).fit([row] * len(y), y)
    assert model.weights == pytest.approx([0.8, 0.2], abs=0.01)
    assert np.diff(model.fit_info["log_likelihood"]).min() >= -1e-7
    restored = BMABlend.from_dict(json.loads(json.dumps(model.to_dict(), allow_nan=False)))
    assert restored.predict_weights([[None, row[1]]]).tolist() == [[0.0, 1.0]]
    assert restored.predict_weights([row]).sum() == pytest.approx(1)


def test_bma_hurdle_uses_mass_at_zero_not_gamma_density():
    row = [Distribution("hurdle_gamma", {"dry": 0.95, "shape": 0.5, "scale": 2}),
           Distribution("hurdle_gamma", {"dry": 0.05, "shape": 0.5, "scale": 2})]
    dry = BMABlend(["a", "b"]).fit([row] * 30, [0.0] * 30)
    assert dry.weights[0] > 0.99
    wet = BMABlend(["a", "b"]).fit([row] * 30, [2.0] * 30)
    assert wet.weights[1] > 0.99


def test_bma_availability_conditioned_fit_and_joint_wind():
    good = Distribution("wind", {"mean": [1, 2], "covariance": [[1, 0.5], [0.5, 1]]})
    bad = Distribution("wind", {"mean": [10, 20], "covariance": [[1, 0], [0, 1]]})
    rows = [[good, bad]] * 20 + [[None, bad]] * 30
    model = BMABlend(["a", "b"]).fit(rows, [[1, 2]] * 50)
    assert model.weights[0] > 0.99
    assert np.all(np.diff(model.fit_info["log_likelihood"]) >= -1e-6)
    assert model.predict_weights(rows)[-1].tolist() == [0, 1]
    with pytest.raises(ValueError, match="eligible"):
        model.predict_weights([[None, None]])


def test_bma_rejects_unidentified_source_weights():
    normal = Distribution("normal", {"mean": 0, "std": 1})
    with pytest.raises(ValueError, match="absent from training"):
        BMABlend(["a", "b"]).fit([[normal, None]] * 20, [0.0] * 20)
    with pytest.raises(ValueError, match="connected"):
        BMABlend(["a", "b"]).fit([[normal, None], [None, normal]] * 10, [0.0] * 20)


def test_xgboost_missing_dependency_is_explicit(monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "xgboost":
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(OptionalDependencyUnavailable, match="unavailable"):
        XGBoostStack("temperature", ["a", "b"]).fit(sample_cases(size=30))


@pytest.mark.parametrize("variable", ["temperature", "rainfall", "wind_uv"])
def test_xgboost_fits_distributions_without_test_targets_and_json_roundtrips(variable):
    try:
        from bharatblend.baselines import _xgboost
        _xgboost()
    except OptionalDependencyUnavailable as exc:
        pytest.skip(str(exc))
    cases = sample_cases(variable, size=150)
    model = XGBoostStack(variable, ["a", "b"], rounds=32).fit(cases[:120])
    ds = model.predict(cases[120:])
    assert model.fit_info["uncertainty_training"] == "purged_forward_cross_fitted_residuals"
    assert [d.to_dict() for d in ds] == [d.to_dict() for d in model.predict(changed_observations(cases[120:]))]
    restored = XGBoostStack.from_dict(json.loads(json.dumps(model.to_dict(), allow_nan=False)))
    assert [d.to_dict() for d in ds] == [d.to_dict() for d in restored.predict(cases[120:])]
    assert all(np.isfinite(d.mean()).all() for d in ds)
    if variable == "temperature":
        predictions = np.array([d.mean() for d in ds])
        y = np.array([c.observation.value for c in cases[120:]])
        assert np.mean((predictions - y) ** 2) < np.var(y)
    elif variable == "wind_uv":
        assert all(np.linalg.eigvalsh(d.parameters["covariance"]).min() > 0 for d in ds)
    else:
        assert all(0 < d.cdf(0) < 1 and d.quantile(0.99) >= 0 for d in ds)
