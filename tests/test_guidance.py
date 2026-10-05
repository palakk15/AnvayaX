import numpy as np
import pytest

from bharatblend.guidance import ensemble_copula_coupling, heatwave_guidance, variogram_score


def test_ecc_preserves_marginals_and_template_ranks():
    template = np.array([[3, 1], [1, 3], [2, 2]])
    marginal = np.array([[10, 20], [15, 40], [30, 60]])
    coupled = ensemble_copula_coupling(marginal, template)
    assert np.array_equal(np.sort(coupled, axis=0), marginal)
    assert np.array_equal(np.argsort(coupled, axis=0), np.argsort(template, axis=0))


def test_heat_needs_daily_maxima_and_joint_persistence():
    normal = np.full((2, 2), 32)
    scenarios = np.array([[[38, 38], [38, 38]], [[38, 38], [36, 36]]])
    with pytest.raises(ValueError, match="daily maximum"):
        heatwave_guidance(scenarios, normal, ["coastal", "coastal"], target_kind="temperature_6h")
    result = heatwave_guidance(scenarios, normal, ["coastal", "coastal"], target_kind="daily_maximum_temperature")
    assert result["subdivision_two_day_probability"] == [0.5]
    # High absolute coastal temperatures without sufficient departure do not satisfy the criterion.
    result = heatwave_guidance(np.full((2, 2, 2), 38), np.full((2, 2), 36), ["coastal", "coastal"], target_kind="daily_maximum_temperature")
    assert result["subdivision_two_day_probability"] == [0]


def test_variogram_detects_incorrect_dependence():
    coherent = np.array([[0, 0], [2, 2]])
    incoherent = np.array([[0, 2], [2, 0]])
    assert variogram_score(coherent, [1, 1]) == 0
    assert variogram_score(incoherent, [1, 1]) > 0
