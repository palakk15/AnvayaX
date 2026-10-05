from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy.special import ndtr

from bharatblend.dataset import Case, join_cases, split_cases
from bharatblend.distributions import Distribution, exceedance, mixture_score, mixture_summary
from bharatblend.models import AdditiveGate, masked_weights, spatial_patches
from bharatblend.schemas import ForecastRecord, ObservationRecord, utc


def records(start=None, **updates):
    init = datetime(2025, 1, 1, tzinfo=timezone.utc)
    start = start or init + timedelta(days=1)
    common = dict(location_id="mumbai", latitude=19.0, longitude=72.0,
                  variable="rainfall", units="mm", valid_start=start,
                  valid_end=start + timedelta(days=1), data_kind="synthetic")
    forecast = dict(**common, source_id="gfs", model_version="fixture-v1", initialization_time=init,
                    available_at=init + timedelta(hours=3), value=10.0)
    forecast.update(updates)
    obs = ObservationRecord(**common, observation_id="one", available_at=common["valid_end"] + timedelta(hours=12), value=20.0)
    return ForecastRecord(**forecast), obs


def test_exact_rainfall_windows_and_delayed_forecast_availability():
    f, o = records()
    assert len(join_cases([f], [o], mode="operational")) == 1
    shifted = o.model_copy(update={"valid_start": o.valid_start + timedelta(hours=3), "valid_end": o.valid_end + timedelta(hours=3)})
    assert join_cases([f], [shifted]) == []
    late = f.model_copy(update={"available_at": f.initialization_time + timedelta(hours=5)})
    assert join_cases([late], [o], mode="operational") == []


def test_training_overlap_and_hindcast_release_are_explicit():
    f, o = records()
    overlap = f.model_copy(update={"training_end": f.initialization_time})
    assert join_cases([overlap], [o]) == []
    hindcast = f.model_copy(update={"forecast_kind": "hindcast", "available_at": o.valid_end + timedelta(days=90)})
    assert len(join_cases([hindcast], [o], mode="hindcast")) == 1
    assert join_cases([hindcast], [o], mode="operational") == []


def test_future_observation_cannot_change_issuance_skill():
    f, o = records()
    a = join_cases([f], [o])[0]
    b = join_cases([f], [o.model_copy(update={"value": 9000})])[0]
    assert a.recent_skill == b.recent_skill == {"gfs": 1.0}


def test_chronological_split_requires_target_and_publication_before_boundary():
    f, o = records()
    cases = join_cases([f], [o])
    boundary = o.valid_end + timedelta(hours=1)
    train, _, _ = split_cases(cases, boundary, boundary + timedelta(days=1))
    assert train == []


def test_schema_rejects_naive_times_wrong_units_and_negative_rain():
    with pytest.raises(ValueError, match="timezone"):
        utc("2025-01-01")
    with pytest.raises(ValueError):
        records(value=-1)
    with pytest.raises(ValueError, match="convert explicitly"):
        records(units="m")
    with pytest.raises(ValueError, match="finite"):
        records(value=[float("inf")])


def test_normal_crps_quadrature_matches_closed_form():
    normal = Distribution("normal", {"mean": 2, "std": 3})
    for y in (-10, 0, 2, 7):
        z = (y - 2) / 3
        phi = np.exp(-z*z/2) / np.sqrt(2*np.pi)
        expected = 3 * (z * (2*ndtr(z)-1) + 2*phi - 1/np.sqrt(np.pi))
        assert mixture_score([normal], np.array([1.0]), y) == pytest.approx(expected, abs=0.006)


def test_hurdle_atom_monotone_tail_and_mixture_quantiles():
    dry = Distribution("hurdle_gamma", {"dry": 0.8, "shape": 2, "scale": 10})
    wet = Distribution("hurdle_gamma", {"dry": 0.1, "shape": 2, "scale": 90})
    assert dry.cdf(-1) == 0
    assert dry.cdf(0) == 0.8
    assert dry.quantile(0.5) == 0
    assert exceedance(wet, 64.5) >= exceedance(wet, 115.6) >= exceedance(wet, 204.5)
    weights = np.array([0.75, 0.25])
    summary = mixture_summary([dry, wet], weights)
    assert summary["median"] == pytest.approx(0, abs=1e-8)
    assert summary["mean"] == pytest.approx(0.75 * dry.mean() + 0.25 * wet.mean())
    assert mixture_score([dry, wet], weights, 200, 64.5) <= mixture_score([dry, wet], weights, 200)


def test_missing_source_simplex_and_exact_logit_receipt():
    f, o = records()
    cases = [Case(o, {"gfs": f, "ai": f.model_copy(update={"source_id": "ai", "value": 40+i})}, f.initialization_time + timedelta(hours=4), {}) for i in range(15)]
    q = np.tile(np.eye(2), (len(cases), 1, 1))
    b = np.tile([0.2, 0.8], (len(cases), 1))
    gate = AdditiveGate(["gfs", "ai"]).fit(cases, q, b, np.ones((len(cases), 2), dtype=bool), iterations=20)
    w, logits = gate.predict(cases[:1], np.array([[True, True]]))
    terms = gate.explanation(cases[0])
    assert sum(terms["gfs"].values()) == pytest.approx(logits[0, 0])
    assert np.log(w[0, 0] / w[0, 1]) == pytest.approx(sum(terms["gfs"].values()) - sum(terms["ai"].values()))
    assert masked_weights([[1, 5]], [[True, False]]).tolist() == [[1, 0]]
    with pytest.raises(ValueError, match="eligible"):
        masked_weights([[1, 5]], [[False, False]])
    reloaded = AdditiveGate.from_dict(gate.to_dict())
    assert np.allclose(reloaded.predict(cases[:1], [[True, True]])[0], w)


def test_spatial_context_never_mixes_valid_windows():
    f, o = records()
    issue = f.initialization_time + timedelta(hours=4)
    a = Case(o, {"gfs": f}, issue, {})
    b = Case(o.model_copy(update={"valid_start": o.valid_start + timedelta(days=1)}), {"gfs": f}, issue, {})
    patches, padding = spatial_patches([a, b], np.array([[1.0], [999.0]]))
    assert patches[0, 0, 0] == 1
    assert padding[0].tolist() == [False, True, True, True]


def test_wind_rotation_and_probability_support():
    d = Distribution("wind", {"mean": [3, 4], "covariance": [[1, 0], [0, 1]]})
    score = mixture_score([d], np.array([1.0]), [3, 4])
    assert score >= 0
    summary = mixture_summary([d], [1.0])
    assert summary["mean"] == [3, 4]
    assert 4.5 < summary["speed_mean"] < 6
    assert exceedance(d, 1) >= exceedance(d, 10)
