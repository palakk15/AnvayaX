"""Exact-window joins and observation-arrival-aware rolling skill."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import heapq

import numpy as np

from .schemas import ForecastRecord, ObservationRecord


@dataclass
class Case:
    observation: ObservationRecord
    forecasts: dict[str, ForecastRecord]
    issuance_time: datetime
    recent_skill: dict[str, float]

    @property
    def variable(self):
        return self.observation.variable

    @property
    def key(self):
        o = self.observation
        return f"{o.location_id}|{o.variable}|{o.valid_start.isoformat()}|{o.valid_end.isoformat()}|{self.issuance_time.isoformat()}"


def join_cases(forecasts, observations, issuance_delay_hours=4, mode="hindcast") -> list[Case]:
    """Construct same-initialization cohorts; operational mode enforces actual availability.

    Retrospective hindcast release dates never masquerade as historical publication dates.
    Revisions select the latest good observation for verification; live skill uses arrival times.
    """
    if mode not in ("hindcast", "operational"):
        raise ValueError("mode must be hindcast or operational")
    obs_by_key = {}
    for obs in observations:
        if obs.quality == "good":
            prev = obs_by_key.get(obs.target_key())
            if prev is None or obs.available_at > prev.available_at:
                obs_by_key[obs.target_key()] = obs
    groups = {}
    for forecast in forecasts:
        key = forecast.target_key()
        obs = obs_by_key.get(key)
        if obs is None:
            continue
        if forecast.data_kind != obs.data_kind:
            raise ValueError("Real and synthetic forecast/observation pairs cannot be mixed")
        if abs(forecast.latitude - obs.latitude) > 1e-5 or abs(forecast.longitude - obs.longitude) > 1e-5:
            raise ValueError("Location coordinates differ; explicit spatial alignment is required")
        # Unseen-weather evaluation excludes every checkpoint's training overlap.
        if forecast.training_end is not None and forecast.initialization_time <= forecast.training_end:
            continue
        issue = forecast.initialization_time + timedelta(hours=issuance_delay_hours)
        if issue > forecast.valid_start:
            continue
        if mode == "operational" and (forecast.forecast_kind != "operational" or forecast.available_at > issue):
            continue
        if forecast.forecast_kind == "operational" and forecast.available_at > issue:
            continue
        group = groups.setdefault((key, forecast.initialization_time), (obs, issue, {}))
        if forecast.source_id in group[2]:
            raise ValueError("Duplicate source/version for the same target and initialization; resolve explicitly")
        group[2][forecast.source_id] = forecast
    cases = [Case(obs, fs, issue, {}) for obs, issue, fs in groups.values()]
    cases.sort(key=lambda c: (c.issuance_time, c.key))
    add_delayed_skill(cases)
    return cases


def add_delayed_skill(cases: list[Case], half_life_days=30):
    """Past normalized absolute errors, released only after verifying observations arrive.

    The statistic is a gate feature, not a claim of probabilistic CRPS calibration.
    Separate keys by variable, region, source and coarse lead band.
    """
    pending, history = [], {}
    sequence = 0
    scales = {"rainfall": 25.0, "temperature": 5.0, "wind_uv": 5.0, "gust": 5.0}
    for case in cases:
        now = case.issuance_time
        while pending and pending[0][0] <= now:
            available, _, key, error = heapq.heappop(pending)
            if key in history:
                total, count, last = history[key]
                decay = 2 ** (-max(0, (available - last).total_seconds()) / (86400 * half_life_days))
                history[key] = (decay * total + error, decay * count + 1, available)
            else:
                history[key] = (error, 1.0, available)
        for source, f in case.forecasts.items():
            lead = (f.valid_start - f.initialization_time).total_seconds() / 3600
            band = int(lead // 48)
            key = (case.variable, case.observation.region, source, band)
            total, count, _ = history.get(key, (1.0, 1.0, None))
            case.recent_skill[source] = total / count
            mean = np.mean(np.atleast_1d(f.value), axis=0)
            error = float(np.linalg.norm(np.asarray(mean) - np.asarray(case.observation.value)))
            error /= scales[case.variable]
            # Hindcasts produced after an event do not supply historical operational skill.
            if f.forecast_kind == "operational":
                sequence += 1
                heapq.heappush(pending, (case.observation.available_at, sequence, key, error))


def split_cases(cases, train_end: datetime, dev_end: datetime):
    if train_end >= dev_end:
        raise ValueError("train_end must precede dev_end")
    # Both the full target window and target availability must fall inside training/development.
    train = [c for c in cases if c.observation.valid_end < train_end and c.observation.available_at < train_end]
    dev = [c for c in cases if c.issuance_time >= train_end and c.observation.valid_end < dev_end
           and c.observation.available_at < dev_end]
    test = [c for c in cases if c.issuance_time >= dev_end]
    return train, dev, test
