"""Deterministic synthetic integration fixtures, never a weather benchmark."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

from .schemas import ForecastRecord, ObservationRecord, write_jsonl

LOCATIONS = [
    ("mumbai", 19.08, 72.88, "west_coast", 14, 2),
    ("pune", 18.52, 73.86, "western_ghats", 560, 110),
    ("delhi", 28.61, 77.21, "northern_plains", 216, 950),
    ("chennai", 13.08, 80.27, "east_coast", 7, 3),
    ("guwahati", 26.14, 91.74, "northeast", 55, 450),
    ("jaipur", 26.91, 75.79, "arid_northwest", 431, 650),
]
SOURCES = ["gfs", "gefs", "graphcast_operational", "pangu_weather", "fourcastnet_v2_small"]


def generate_demo(output, cycles=96, seed=42):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    forecasts, observations = [], []
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    for cycle in range(cycles):
        init = start + timedelta(days=cycle * 5)
        for lead in (24, 120):
            valid = init + timedelta(hours=lead)
            wet_season = 0.5 + 0.5 * np.cos((valid.timetuple().tm_yday - 210) * 2 * np.pi / 365.25)
            for location, lat, lon, region, elevation, coast in LOCATIONS:
                heavy = rng.random() < 0.06
                rain = float(rng.gamma(1.3, 25 + wet_season * 40) * (2.5 if heavy else 1)) if rng.random() < 0.3 + 0.55 * wet_season else 0.0
                temperature = float(26 + 7 * np.sin((valid.timetuple().tm_yday - 80) * 2 * np.pi / 365.25) - elevation / 200 + rng.normal(0, 2))
                wind = np.array([3 + wet_season * 5, -1 + wet_season * 2]) + rng.normal(0, 1.5, 2)
                for variable, truth, units in (("rainfall", rain, "mm"), ("temperature", temperature, "degC"), ("wind_uv", wind.tolist(), "m/s")):
                    end = valid + timedelta(hours=24) if variable == "rainfall" else valid
                    base = dict(location_id=location, latitude=lat, longitude=lon, region=region, elevation_m=elevation,
                                coastal_distance_km=coast, variable=variable, units=units,
                                valid_start=valid, valid_end=end, data_kind="synthetic")
                    observations.append(ObservationRecord(**base, observation_id=f"synthetic-{cycle}-{lead}-{location}-{variable}",
                                                          available_at=end + timedelta(hours=18), value=truth))
                    for index, source in enumerate(SOURCES[:3] if variable == "rainfall" else SOURCES):
                        members = 8 if source == "gefs" else 1
                        uncertainty = 1 + lead / 120
                        if variable == "rainfall":
                            factor = [1.25 - 0.35 * wet_season, 1.05, 0.95 - 0.3 * wet_season][index]
                            central = max(0, rain * factor + rng.normal(0, 8 * uncertainty) + [5, 2, -1][index])
                            value = np.maximum(0, central + rng.normal(0, 5, members)).tolist()
                        elif variable == "temperature":
                            central = temperature + [2, 0.6, -0.6, 1, -1.5][index] + rng.normal(0, uncertainty * [1.5, 1.2, 0.8, 1.0, 1.1][index])
                            value = (central + rng.normal(0, 0.8, members)).tolist()
                        else:
                            central = wind + [1.5, 0.8, -0.5, 1.0, -1.0][index] + rng.normal(0, uncertainty, 2)
                            value = (central + rng.normal(0, 1, (members, 2))).tolist()
                        forecasts.append(ForecastRecord(**base, source_id=source, model_version="synthetic-v1",
                                                        initialization_time=init, available_at=init + timedelta(hours=3),
                                                        initialization_source="synthetic_generator", value=value,
                                                        training_end=datetime(2021, 12, 31, tzinfo=timezone.utc)))
    write_jsonl(output / "forecasts.jsonl", forecasts)
    write_jsonl(output / "observations.jsonl", observations)
    return {"data_kind": "synthetic", "seed": seed, "cycles": cycles, "forecast_records": len(forecasts),
            "observation_records": len(observations), "train_end": (start + timedelta(days=int(cycles * 0.60) * 5)).isoformat(),
            "dev_end": (start + timedelta(days=int(cycles * 0.80) * 5)).isoformat(),
            "warning": "Artificial integration fixture; scores do not measure real weather skill."}
