from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from bharatblend import api

REQUEST = {
    "location_id": "mumbai",
    "variable": "rainfall",
    "valid_start": "2024-07-01T03:00:00Z",
    "valid_end": "2024-07-02T03:00:00Z",
    "issuance_time": "2024-06-30T00:00:00Z",
}


class FakeService:
    created = 0

    def __init__(self, data_dir):
        type(self).created += 1
        self.data_dir = data_dir

    def status(self):
        return {"status": "ready", "data_kind": "synthetic"}

    def registry(self):
        return {"models": [{"source_id": "test_source"}]}

    def cases(self):
        return [deepcopy(REQUEST)]

    def predict(self, request):
        if request["location_id"] == "unknown":
            raise ValueError("Unknown location_id")
        return {
            "status": "blended",
            "data_kind": "synthetic",
            "track": request["track"],
            "target": request,
            "receipt_id": "test-receipt",
        }

    def metrics(self):
        return {"data_kind": "synthetic", "variables": {}}

    def experiments(self):
        return {"data_kind": "synthetic", "manifest": [], "status": "prepared"}

    def benchmark(self):
        return {"data_kind": "synthetic", "variables": {}, "status": "not_run"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    FakeService.created = 0
    monkeypatch.setattr(api, "BlendService", FakeService)
    application = api.create_app(tmp_path)
    assert FakeService.created == 0
    with TestClient(application) as test_client:
        yield test_client


def test_prepared_artifact_routes_share_lazy_service(client):
    for route in ("health", "registry", "cases", "metrics", "experiments", "benchmark"):
        assert client.get(f"/{route}").status_code == 200
    assert FakeService.created == 1


def test_reference_catalogue_available_without_loading_workspace(client):
    result = client.get('/catalogue')
    assert result.status_code == 200
    assert len(result.json()['models']) == 27
    assert len(result.json()['datasets']) == 13
    assert FakeService.created == 0


def test_explanation_defaults_to_local_evidence(client):
    result = client.post('/explain', json=REQUEST)
    assert result.status_code == 200
    assert result.json()['narration']['provider'] == 'deterministic'
    assert result.json()['evidence']['data_kind'] == 'synthetic'


def test_public_city_validation_before_network_access(client):
    assert client.post('/public/refresh', json={'city': '../untrusted'}).status_code == 422
    assert client.get('/public/forecast?city=../untrusted').status_code == 422


def test_forecast_preserves_provenance_and_defaults(client):
    response = client.post("/forecast", json=REQUEST)
    assert response.status_code == 200
    result = response.json()
    assert result["data_kind"] == "synthetic"
    assert result["target"]["thresholds"] == [64.5, 115.6, 204.5]
    assert result["target"]["excluded_sources"] == []
    assert result["track"] == "interpretable"


def test_counterfactual_inputs_are_forwarded(client):
    request = REQUEST | {
        "excluded_sources": ["test_source"],
        "thresholds": [100.0],
        "track": "accuracy",
    }
    result = client.post("/forecast", json=request).json()
    assert result["target"]["excluded_sources"] == ["test_source"]
    assert result["target"]["thresholds"] == [100.0]
    assert result["track"] == "accuracy"


@pytest.mark.parametrize(
    "change",
    [
        {"valid_start": "2024-07-01T03:00:00"},
        {"valid_end": "2024-07-02T03:00:00"},
        {"issuance_time": "2024-06-30T00:00:00"},
        {"valid_end": "2024-07-01T03:00:00Z"},
        {"valid_end": "2024-06-29T03:00:00Z"},
        {"issuance_time": "2024-07-01T04:00:00Z"},
        {"thresholds": [-1]},
        {"thresholds": ["NaN"]},
        {"thresholds": ["Infinity"]},
        {"thresholds": list(range(33))},
        {"location_id": "   "},
        {"excluded_sources": ["source"] * 65},
        {"track": "unknown"},
        {"variable": "gust"},
        {"unexpected": True},
    ],
)
def test_invalid_targets_fail_before_service_access(client, change):
    assert client.post("/forecast", json=REQUEST | change).status_code == 422
    assert FakeService.created == 0


@pytest.mark.parametrize("variable", ["temperature", "wind_uv"])
def test_instantaneous_variables_allow_equal_valid_times(client, variable):
    response = client.post(
        "/forecast",
        json=REQUEST | {"variable": variable, "valid_end": REQUEST["valid_start"]},
    )
    assert response.status_code == 200
    assert response.json()["target"]["thresholds"] == []


def test_service_validation_is_422(client):
    response = client.post("/forecast", json=REQUEST | {"location_id": "unknown"})
    assert response.status_code == 422
    assert "Unknown location_id" in response.json()["detail"]


def test_explicit_empty_thresholds_remain_empty(client):
    response = client.post("/forecast", json=REQUEST | {"thresholds": []})
    assert response.status_code == 200
    assert response.json()["target"]["thresholds"] == []


def test_timezones_are_normalized_before_forecast_lookup(client):
    response = client.post(
        "/forecast", json=REQUEST | {"valid_start": "2024-07-01T08:30:00+05:30"}
    )
    assert response.status_code == 200
    assert response.json()["target"]["valid_start"] == REQUEST["valid_start"]


def test_temperature_thresholds_can_be_below_zero(client):
    response = client.post("/forecast", json=REQUEST | {"variable": "temperature", "thresholds": [-5]})
    assert response.status_code == 200
    assert response.json()["target"]["thresholds"] == [-5]


def test_missing_artifacts_return_unavailable_without_training(monkeypatch, tmp_path):
    class MissingService:
        def __init__(self, data_dir):
            raise FileNotFoundError("Prepared artifacts are missing")

    monkeypatch.setattr(api, "BlendService", MissingService)
    with TestClient(api.create_app(tmp_path)) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["detail"]["status"] == "unavailable"
        assert client.post("/forecast", json=REQUEST).status_code == 503
