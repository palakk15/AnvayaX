"""Read-only HTTP interface for prepared BharatBlend forecast artifacts.

Starting the application never trains models or downloads forecast data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from bharatblend.service import BlendService
from bharatblend.catalogue import catalogue
from bharatblend.explainability import GroqNarrator, build_evidence, deterministic_narrative
from bharatblend.live_data import CITIES, fetch_public_forecast, load_snapshot
from bharatblend.settings import local_settings
import requests


class ForecastRequest(BaseModel):
    """One forecast target, resolved using only the requested issuance time."""

    model_config = ConfigDict(extra="forbid")

    location_id: str = Field(min_length=1, max_length=128)
    variable: Literal["rainfall", "temperature", "wind_uv"]
    valid_start: datetime
    valid_end: datetime
    issuance_time: datetime
    track: Literal["interpretable", "accuracy"] = "interpretable"
    excluded_sources: list[str] = Field(default_factory=list, max_length=64)
    thresholds: list[FiniteFloat] | None = Field(default=None, max_length=32)

    @field_validator("location_id")
    @classmethod
    def require_location(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("location_id cannot be blank.")
        return value

    @field_validator("valid_start", "valid_end", "issuance_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must include a timezone; use ISO 8601 UTC (Z).")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def check_target(self) -> ForecastRequest:
        if self.valid_end < self.valid_start:
            raise ValueError("valid_end must be at or after valid_start.")
        if self.variable == "rainfall" and self.valid_end == self.valid_start:
            raise ValueError("Rainfall requires a positive accumulation interval.")
        if self.issuance_time > self.valid_start:
            raise ValueError("issuance_time must be at or before valid_start.")
        if self.variable in {"rainfall", "wind_uv"} and any(
            threshold < 0 for threshold in (self.thresholds or [])
        ):
            raise ValueError("Rainfall and wind-speed thresholds must be nonnegative.")
        return self

    def service_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if self.thresholds is None:
            payload["thresholds"] = (
                [64.5, 115.6, 204.5] if self.variable == "rainfall" else []
            )
        return payload


class ExplainRequest(ForecastRequest):
    use_groq: bool = False


class PublicForecastRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    city: str = 'Delhi'

    @field_validator('city')
    @classmethod
    def supported_city(cls, value):
        if value not in CITIES:
            raise ValueError('Choose a supported Indian city.')
        return value


def create_app(data_dir: Path | str = "artifacts/demo") -> FastAPI:
    """Build an app whose service is loaded lazily from existing artifacts."""

    application = FastAPI(
        title="BharatBlend",
        version="0.1.0",
        description=(
            "Adaptive forecast blending with explicit provenance and explanation receipts. "
            "Synthetic demonstrations are not operational weather guidance."
        ),
    )
    application.state.data_dir = Path(data_dir)
    application.state.blend_service = None
    service_lock = Lock()

    def get_service() -> BlendService:
        if application.state.blend_service is None:
            with service_lock:
                if application.state.blend_service is None:
                    application.state.blend_service = BlendService(application.state.data_dir)
        return application.state.blend_service

    def invoke(method: str, *args: Any) -> Any:
        try:
            return getattr(get_service(), method)(*args)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail={"status": "unavailable", "reason": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/health", tags=["Workspace"])
    def health() -> dict[str, Any]:
        return invoke("status")

    @application.get("/registry", tags=["Workspace"])
    def registry() -> dict[str, Any]:
        return invoke("registry")

    @application.get("/cases", tags=["Workspace"])
    def cases() -> list[dict[str, Any]]:
        return invoke("cases")

    @application.post("/forecast", tags=["Forecast"])
    def forecast(request: ForecastRequest) -> dict[str, Any]:
        return invoke("predict", request.service_payload())

    @application.get("/metrics", tags=["Verification"])
    def metrics() -> dict[str, Any]:
        return invoke("metrics")

    @application.get("/experiments", tags=["Verification"])
    def experiments() -> dict[str, Any]:
        return invoke("experiments")

    @application.get("/benchmark", tags=["Verification"])
    def benchmark() -> dict[str, Any]:
        return invoke("benchmark")

    @application.get('/catalogue', tags=['Reference'])
    def source_catalogue():
        return catalogue()

    @application.get('/explain/config', tags=['Explanation'])
    def explanation_config():
        try:
            return GroqNarrator().status()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @application.post('/explain', tags=['Explanation'])
    def explain(request: ExplainRequest):
        payload = request.service_payload()
        payload.pop('use_groq', None)
        receipt = invoke('predict', payload)
        evidence = build_evidence(receipt, catalogue())
        narration = {'status': 'local', 'provider': 'deterministic', 'text': deterministic_narrative(evidence)}
        if request.use_groq:
            try:
                narration = GroqNarrator().explain(evidence)
            except ValueError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {'evidence': evidence, 'narration': narration}

    @application.get('/public/forecast', tags=['Public forecasts'])
    def public_snapshot(city: str = 'Delhi'):
        try:
            snapshot = load_snapshot(city)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail='Cached provider file unavailable.') from exc
        if snapshot is None:
            raise HTTPException(status_code=404, detail='No downloaded forecast for this city; request a refresh.')
        return snapshot

    @application.post('/public/refresh', tags=['Public forecasts'])
    def refresh_public(request: PublicForecastRequest):
        try:
            return fetch_public_forecast(request.city)
        except (requests.RequestException, ValueError, OSError) as exc:
            raise HTTPException(status_code=502, detail='Provider forecast download unavailable. Cached data retained.') from exc

    return application


app = create_app(local_settings().get("BHARATBLEND_DATA_DIR", "artifacts/demo"))
