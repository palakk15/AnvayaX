"""Canonical, timestamped input contracts. Missing is never zero rainfall."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Variable = Literal["rainfall", "temperature", "wind_uv", "gust"]
UNITS = {"rainfall": "mm", "temperature": "degC", "wind_uv": "m/s", "gust": "m/s"}


def utc(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone; use UTC, e.g. 2025-01-01T00:00:00Z")
    return dt.astimezone(timezone.utc)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    location_id: str
    latitude: float
    longitude: float
    variable: Variable
    units: str
    valid_start: datetime
    valid_end: datetime
    region: str = "unknown"
    elevation_m: float = 0
    coastal_distance_km: float = 0
    data_kind: Literal["real", "synthetic"]

    @field_validator("valid_start", "valid_end", mode="after")
    @classmethod
    def timestamps(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def check_target(self):
        if self.valid_end < self.valid_start:
            raise ValueError("valid_end must not precede valid_start")
        if self.variable == "rainfall" and self.valid_end == self.valid_start:
            raise ValueError("Rainfall needs a positive, explicit accumulation interval")
        if self.units != UNITS[self.variable]:
            raise ValueError(f"Expected {UNITS[self.variable]} for {self.variable}; convert explicitly")
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError("Invalid latitude/longitude")
        if not self.location_id.strip():
            raise ValueError("location_id cannot be empty")
        return self

    def target_key(self):
        return self.location_id, self.variable, self.valid_start, self.valid_end


class ForecastRecord(Target):
    source_id: str
    model_version: str
    initialization_time: datetime
    available_at: datetime
    value: float | list[float] | list[list[float]]
    forecast_kind: Literal["operational", "hindcast"] = "operational"
    initialization_source: str | None = None
    training_end: datetime | None = None
    context: dict[str, float] = Field(default_factory=dict)

    @field_validator("initialization_time", "available_at", "training_end", mode="after")
    @classmethod
    def forecast_times(cls, value):
        return None if value is None else utc(value)

    @model_validator(mode="after")
    def check_forecast(self):
        if self.initialization_time > self.valid_start:
            raise ValueError("Forecast initialization follows its target start")
        if self.available_at < self.initialization_time:
            raise ValueError("A forecast cannot be available before initialization")
        if not self.source_id.strip() or not self.model_version.strip():
            raise ValueError("Explicit source_id and model_version are required")
        if self.variable == "wind_uv":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("Wind forecasts require [[u,v], ...], even for one member")
            if any(not isinstance(v, list) or len(v) != 2 for v in self.value):
                raise ValueError("Each wind member must contain exactly [u,v]")
            flat = [x for row in self.value for x in row]
        else:
            flat = self.value if isinstance(self.value, list) else [self.value]
            if not flat or any(isinstance(v, list) for v in flat):
                raise ValueError("Scalar forecasts require a number or nonempty member list")
        if any(not math.isfinite(float(v)) for v in flat):
            raise ValueError("Forecast values must be finite; omit missing products")
        if self.variable in ("rainfall", "gust") and min(flat) < 0:
            raise ValueError("Rainfall/gust values cannot be negative")
        if any(not math.isfinite(v) for v in self.context.values()):
            raise ValueError("Forecast context values must be finite")
        return self


class ObservationRecord(Target):
    observation_id: str
    available_at: datetime
    value: float | list[float]
    quality: Literal["good", "suspect", "bad"] = "good"

    @field_validator("available_at", mode="after")
    @classmethod
    def obs_time(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def check_observation(self):
        if self.available_at < self.valid_end:
            raise ValueError("Verifying observation cannot be published before its interval ends")
        if self.variable == "wind_uv":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("Observed wind requires [u,v]")
            flat = self.value
        else:
            if isinstance(self.value, list):
                raise ValueError("A scalar observation requires a scalar value")
            flat = [self.value]
        if any(not math.isfinite(v) for v in flat):
            raise ValueError("Observation values must be finite")
        if self.variable in ("rainfall", "gust") and min(flat) < 0:
            raise ValueError("Rainfall/gust observations cannot be negative")
        return self


T = TypeVar("T", bound=BaseModel)


def load_jsonl(path: str | Path, cls: type[T]) -> list[T]:
    records = []
    with Path(path).open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    records.append(cls.model_validate_json(line))
                except Exception as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc
    return records


def write_jsonl(path: str | Path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            value = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
            stream.write(json.dumps(value, allow_nan=False) + "\n")
