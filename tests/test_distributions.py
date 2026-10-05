"""Hand-calculable checks that raw ensemble verification preserves all atoms."""
from types import SimpleNamespace

import numpy as np
import pytest

from bharatblend.distributions import (
    Distribution, exceedance, mixture_score, mixture_summary, raw_distribution,
    scalar_grid, score_terms,
)


def empirical(members):
    return Distribution("empirical", {"members": members})


def test_raw_factory_preserves_members_order_duplicates_and_deterministic_values():
    scalar = raw_distribution(SimpleNamespace(variable="temperature", value=7.0))
    ensemble = raw_distribution(SimpleNamespace(variable="rainfall", value=[0.0, 4.0, 0.0]))
    wind = raw_distribution(SimpleNamespace(variable="wind_uv", value=[[3.0, 4.0], [-3.0, -4.0]]))
    assert scalar.kind == "point"
    assert ensemble.kind == "empirical"
    assert wind.kind == "empirical_wind"
    assert ensemble.samples(100).tolist() == [0.0, 4.0, 0.0]
    assert wind.samples(1).tolist() == [[3.0, 4.0], [-3.0, -4.0]]
    values = ensemble.samples()
    values[0] = 999
    assert ensemble.samples()[0] == 0
    reloaded = Distribution(**ensemble.to_dict())
    assert reloaded.mean() == pytest.approx(4 / 3)


def test_empirical_cdf_inverse_quantiles_and_strict_exceedance_at_zero_atom():
    d = empirical([0, 0, 4, 12])
    assert d.cdf(np.array([-1, 0, 3, 4, 12])).tolist() == [0, 0.5, 0.5, 0.75, 1]
    assert d.quantile([0, 0.5, 0.50001, 0.75, 1]).tolist() == [0, 0, 4, 4, 12]
    assert d.mean() == 4
    assert exceedance(d, 0) == 0.5
    assert exceedance(d, 4) == 0.25
    assert exceedance(d, 12) == 0
    with pytest.raises(ValueError, match="probabilities"):
        d.quantile(-0.1)


def test_exact_empirical_crps_and_tail_crps():
    d = empirical([0, 2])
    weights = np.array([1.0])
    assert mixture_score([d], weights, 1) == pytest.approx(0.5, abs=1e-14)
    assert mixture_score([d], weights, 0) == pytest.approx(0.5, abs=1e-14)
    assert mixture_score([d], weights, 3) == pytest.approx(1.5, abs=1e-14)
    assert mixture_score([d], weights, 1, tail_threshold=1) == pytest.approx(0.25, abs=1e-14)
    assert mixture_score([d], weights, 1, tail_threshold=3) == 0
    assert mixture_score([d], weights, 1, tail_threshold=-10) == pytest.approx(0.5, abs=1e-14)
    q, b, c = score_terms([d], 1, tail_threshold=1, tail_weight=3)
    assert weights @ q @ weights - 2 * b @ weights + c == pytest.approx(1.25)


def test_exact_mixture_discrete_crps_matches_weighted_member_identity():
    ds = [empirical([0, 0, 4]), Distribution("point", {"mean": 2}), None]
    weights = np.array([0.75, 0.25, 0.0])
    # Every original member has mass 1/4 here, including duplicated zeros.
    atoms = np.array([0, 0, 4, 2])
    for y in (0, 1, 9):
        expected = np.abs(atoms - y).mean() - 0.5 * np.abs(atoms[:, None] - atoms[None, :]).mean()
        assert mixture_score(ds, weights, y) == pytest.approx(expected, abs=1e-14)
    result = mixture_summary(ds, weights)
    assert result["mean"] == pytest.approx(1.5)
    assert result["median"] == pytest.approx(0, abs=1e-12)
    assert result["quantiles"]["0.9"] == pytest.approx(4)


def test_mixed_continuous_quadrature_includes_every_discrete_atom_and_zero():
    atoms = [0, 0.01, 0.02, 0.03, 10000]
    discrete = empirical(atoms)
    normal = Distribution("normal", {"mean": 1, "std": 2})
    grid = scalar_grid([discrete, normal], observation=0.025, thresholds=[0.015])
    assert set(atoms + [0.025, 0.015]).issubset(set(grid))
    gamma = Distribution("hurdle_gamma", {"dry": 0.7, "shape": 2, "scale": 10})
    assert 0 in scalar_grid([normal, gamma])
    # A near-point normal plus an ensemble should converge to the exact law.
    tiny = Distribution("normal", {"mean": 2, "std": 1e-7})
    ds = [empirical([0, 4]), tiny]
    exact = [ds[0], Distribution("point", {"mean": 2})]
    weights = np.array([0.5, 0.5])
    assert mixture_score(ds, weights, 1) == pytest.approx(mixture_score(exact, weights, 1), abs=1e-6)


def test_opposing_wind_members_keep_joint_law_and_speed_separate_from_vector_mean():
    wind = Distribution("empirical_wind", {"members": [[3, 4], [-3, -4]]})
    summary = mixture_summary([wind], [1.0])
    assert summary["mean"] == [0.0, 0.0]
    assert summary["speed_mean"] == 5.0
    assert summary["median"] == 5.0
    assert summary["quantiles"] == {"0.1": 5.0, "0.5": 5.0, "0.9": 5.0}
    assert exceedance(wind, 4.999) == 1
    assert exceedance(wind, 5) == 0
    # Energy=2.5, u-CRPS=1.5, v-CRPS=2.0; score averages the three.
    assert mixture_score([wind], np.array([1.0]), [0, 0]) == pytest.approx(2.0, abs=1e-14)


def test_wind_mixture_unequal_member_counts_get_correct_component_mass():
    a = Distribution("empirical_wind", {"members": [[0, 0], [3, 4]]})
    b = Distribution("empirical_wind", {"members": [[6, 8]]})
    summary = mixture_summary([a, b], [0.25, 0.75])
    assert summary["mean"] == pytest.approx([4.875, 6.5])
    assert summary["speed_mean"] == pytest.approx(8.125)
    assert summary["quantiles"] == {"0.1": 0.0, "0.5": 10.0, "0.9": 10.0}
    # Explicitly expanded equivalent law has eight equiprobable members.
    expanded = Distribution("empirical_wind", {"members": [[0, 0], [3, 4]] + [[6, 8]] * 6})
    expected = mixture_score([expanded], np.array([1.0]), [2, 3])
    assert mixture_score([a, b], np.array([0.25, 0.75]), [2, 3]) == pytest.approx(expected, abs=1e-14)


def test_gaussian_and_empirical_wind_can_mix_without_member_count_assumptions():
    gaussian = Distribution("wind", {"mean": [0, 0], "covariance": [[1, 0], [0, 1]]})
    raw = Distribution("empirical_wind", {"members": [[3, 4]]})
    summary = mixture_summary([gaussian, raw], [0.5, 0.5])
    assert summary["mean"] == [1.5, 2.0]
    expected_speed = 0.5 * np.linalg.norm(gaussian.samples(128), axis=1).mean() + 2.5
    assert summary["speed_mean"] == pytest.approx(expected_speed)
    assert mixture_score([gaussian, raw], np.array([0.5, 0.5]), [3, 4]) >= 0


def test_summary_median_is_computed_independently_of_requested_quantiles():
    scalar = empirical([0, 2, 10])
    wind = Distribution("empirical_wind", {"members": [[0, 0], [2, 0], [10, 0]]})
    for distribution in (scalar, wind):
        result = mixture_summary([distribution], [1.0], quantiles=(0.9, 0.1))
        assert result["median"] == pytest.approx(2)
        assert result["quantiles"] == pytest.approx({"0.9": 10, "0.1": 0}, abs=1e-12)


@pytest.mark.parametrize("kind,members", [
    ("empirical", []), ("empirical", [[1, 2]]), ("empirical", [float("nan")]),
    ("empirical_wind", []), ("empirical_wind", [1, 2]), ("empirical_wind", [[1, 2, 3]]),
])
def test_malformed_empirical_laws_are_rejected(kind, members):
    with pytest.raises(ValueError, match="Empirical distributions"):
        Distribution(kind, {"members": members})


def test_mixed_vector_scalar_score_and_vector_tail_score_rejected():
    raw = Distribution("empirical_wind", {"members": [[0, 0]]})
    with pytest.raises(ValueError, match="cannot be mixed"):
        score_terms([raw, empirical([0])], [0, 0])
    with pytest.raises(ValueError, match="tail scores"):
        mixture_score([raw], np.array([1.0]), [0, 0], tail_threshold=1)
    with pytest.raises(ValueError, match="At least one"):
        score_terms([None], 0)
