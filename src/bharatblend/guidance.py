"""Explicitly optional scenario/heat guidance, requiring suitable input targets.

These utilities do not transform six-hour temperature samples into daily maxima.
They do not issue official alerts or infer hydrological flooding.
"""
from __future__ import annotations

import numpy as np


def ensemble_copula_coupling(sorted_marginal_samples, ensemble_template):
    """ECC-Q: assign ordered calibrated marginal samples to template member ranks.

    Arrays have shape (members, ...space/time/variable dimensions). Member counts
    and support must match. Stable member order resolves ties reproducibly.
    """
    marginal = np.asarray(sorted_marginal_samples, dtype=float)
    template = np.asarray(ensemble_template, dtype=float)
    if marginal.shape != template.shape or marginal.ndim < 2 or marginal.shape[0] < 2:
        raise ValueError("ECC requires matching (members, targets...) arrays and at least two members")
    if not np.isfinite(marginal).all() or not np.isfinite(template).all():
        raise ValueError("ECC inputs must be finite on common support")
    ranks = np.argsort(np.argsort(template, axis=0, kind="stable"), axis=0, kind="stable")
    return np.take_along_axis(np.sort(marginal, axis=0), ranks, axis=0)


def heatwave_guidance(daily_max_scenarios, daily_normals, station_types, *, target_kind):
    """IMD-criterion guidance for ONE meteorological subdivision.

    Temperatures: (members, consecutive days, stations), degree Celsius. Normals:
    (days, stations). station_types: plains/coastal/hills. At least two reporting
    stations satisfying criteria on each of two consecutive days are required.
    Spatially/temporally independent marginals must be coupled before this call.
    """
    if target_kind != "daily_maximum_temperature":
        raise ValueError("Heatwave guidance requires genuine daily maximum temperature targets")
    temperatures = np.asarray(daily_max_scenarios, dtype=float)
    normals = np.asarray(daily_normals, dtype=float)
    if temperatures.ndim != 3 or normals.shape != temperatures.shape[1:]:
        raise ValueError("Expected scenarios (members, days, stations) and normals (days, stations)")
    if temperatures.shape[1] < 2 or temperatures.shape[2] < 2:
        raise ValueError("Subdivision persistence guidance needs at least two days and two stations")
    if len(station_types) != temperatures.shape[2] or any(s not in ("plains", "coastal", "hills") for s in station_types):
        raise ValueError("Provide plains/coastal/hills classification for every station")
    if not np.isfinite(temperatures).all() or not np.isfinite(normals).all():
        raise ValueError("Incomplete station/day trajectories cannot establish a persistence event")
    departure = temperatures - normals
    hot = np.zeros_like(temperatures, dtype=bool)
    severe = np.zeros_like(hot)
    for j, kind in enumerate(station_types):
        base = {"plains": 40, "coastal": 37, "hills": 30}[kind]
        hot[:, :, j] = (temperatures[:, :, j] >= base) & (departure[:, :, j] >= 4.5)
        severe[:, :, j] = (temperatures[:, :, j] >= base) & (departure[:, :, j] > 6.4)
        if kind == "plains":
            hot[:, :, j] |= temperatures[:, :, j] >= 45
            severe[:, :, j] |= temperatures[:, :, j] >= 47
    day = hot.sum(axis=2) >= 2
    severe_day = severe.sum(axis=2) >= 2
    persistence = day[:, 1:] & day[:, :-1]
    severe_persistence = severe_day[:, 1:] & severe_day[:, :-1]
    return {"station_day_probability": hot.mean(axis=0).tolist(),
            "subdivision_two_day_probability": persistence.mean(axis=0).tolist(),
            "severe_two_day_probability": severe_persistence.mean(axis=0).tolist(),
            "criteria_source": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1627107&lang=2&reg=48",
            "status": "experimental_criterion_guidance_not_an_official_declaration"}


def variogram_score(ensemble, observed, power=0.5):
    """Uniform pair-weight variogram score for a finite coherent ensemble."""
    samples, obs = np.asarray(ensemble, dtype=float), np.asarray(observed, dtype=float)
    if samples.ndim != 2 or samples.shape[1:] != obs.shape or len(obs) < 2:
        raise ValueError("Expected (members, targets) ensemble and matching observed target vector")
    if not np.isfinite(samples).all() or not np.isfinite(obs).all() or not 0 < power < 2:
        raise ValueError("Finite data and 0 < power < 2 are required")
    upper = np.triu_indices(len(obs), 1)
    expected = np.mean(np.abs(samples[:, :, None] - samples[:, None, :]) ** power, axis=0)
    truth = np.abs(obs[:, None] - obs[None, :]) ** power
    return float(np.mean((expected[upper] - truth[upper]) ** 2))
