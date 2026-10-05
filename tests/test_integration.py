"""Train tiny fixtures and check the actual service, not just API mocks."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from bharatblend.api import create_app
from bharatblend.demo import generate_demo
from bharatblend.models import SpatialGate
from bharatblend.schemas import ForecastRecord, ObservationRecord, load_jsonl, write_jsonl
from bharatblend.service import BlendService
from bharatblend.training import train_workspace


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("trained")
    manifest = generate_demo(root, cycles=40)
    # Rainfall-only integration fixture is sufficient to exercise the entire artifact contract.
    write_jsonl(root / "forecasts.jsonl", [f for f in load_jsonl(root / "forecasts.jsonl", ForecastRecord) if f.variable == "rainfall"])
    write_jsonl(root / "observations.jsonl", [o for o in load_jsonl(root / "observations.jsonl", ObservationRecord) if o.variable == "rainfall"])
    train_workspace(root / "forecasts.jsonl", root / "observations.jsonl", root,
                    manifest["train_end"], manifest["dev_end"], mode="operational", accuracy=True, epochs=2)
    return root


def request(service):
    case = service.cases()[0]
    return {k: case[k] for k in ("location_id", "variable", "valid_start", "valid_end", "issuance_time")}


def test_trained_forecast_receipt_reconstruction_and_persistence(workspace):
    service = BlendService(workspace)
    result = service.predict(request(service))
    assert result["data_kind"] == "synthetic"
    assert result["status"] == "blended"
    assert sum(s["weight"] for s in result["sources"]) == pytest.approx(1)
    assert result["summary"]["mean"] == pytest.approx(sum(s["weight"] * s["calibrated_mean"] for s in result["sources"]))
    for source in result["sources"]:
        assert sum(source["term_contributions"].values()) == pytest.approx(source["logit"])
    for probability in result["probabilities"]:
        assert sum(probability["contributions"].values()) == pytest.approx(probability["probability"])
    assert json.loads((workspace / "receipts" / f"{result['receipt_id']}.json").read_text())["receipt_id"] == result["receipt_id"]


def test_outage_single_source_and_no_source(workspace):
    service = BlendService(workspace)
    query = request(service)
    ids = service.artifact["variables"]["rainfall"]["sources"]
    single = service.predict({**query, "excluded_sources": ids[1:]})
    assert single["status"] == "single_source"
    assert single["sources"][0]["weight"] == 1
    unavailable = service.predict({**query, "excluded_sources": ids})
    assert unavailable["status"] == "unavailable"
    assert unavailable["sources"] == []


def test_explanation_audits_active_missing_and_user_excluded_models(workspace):
    service = BlendService(workspace)
    query = {**request(service), 'excluded_sources': ['gfs']}
    response = TestClient(create_app(workspace)).post('/explain', json=query)
    assert response.status_code == 200
    body = response.json()
    evidence = body['evidence']
    statuses = {r['source_id']: r for r in evidence['eligibility']}
    assert statuses['gfs']['status'] == 'excluded'
    assert any('explicitly' in reason for reason in statuses['gfs']['reasons'])
    assert any('no native' in reason for reason in statuses['pangu_weather']['reasons'])
    assert all(statuses[s['source_id']]['status'] == 'active' for s in evidence['sources'])
    assert all(s['logit_reconstruction_error'] < 1e-8 for s in evidence['sources'])
    assert evidence['context_feature_values']['availability.gfs'] == 0
    assert evidence['data_kind'] == 'synthetic'
    assert body['narration']['provider'] == 'deterministic'


def test_accuracy_artifact_and_actual_api(workspace):
    service = BlendService(workspace)
    query = {**request(service), "track": "accuracy"}
    result = TestClient(create_app(workspace)).post("/forecast", json=query)
    assert result.status_code == 200
    assert result.json()["data_kind"] == "synthetic"
    spatial = SpatialGate.load(workspace / "rainfall_spatial.pt", service.banks["rainfall"].sources,
                               service.gates["rainfall"].features)
    case = next(c for c in service.archive_cases if c.issuance_time.isoformat() == query["issuance_time"])
    w, _ = spatial.predict([case], np.ones((1, 3), dtype=bool))
    assert np.isfinite(w).all() and np.sum(w) == pytest.approx(1)


def test_model_version_duration_and_lead_support_are_not_extrapolated(workspace):
    service = BlendService(workspace)
    case = next(c for c in service.archive_cases if c.variable == "rainfall")
    source = next(iter(case.forecasts))
    forecast = case.forecasts[source]
    bank = service.banks["rainfall"]
    assert bank.distribution(source, forecast) is not None
    assert bank.distribution(source, forecast.model_copy(update={"model_version": "unvalidated-upgrade"})) is None
    from datetime import timedelta
    assert bank.distribution(source, forecast.model_copy(update={"valid_end": forecast.valid_end + timedelta(hours=6)})) is None
    assert bank.distribution(source, forecast.model_copy(update={"initialization_time": forecast.initialization_time - timedelta(days=100)})) is None


def test_predevelopment_forecast_is_rejected(workspace):
    service = BlendService(workspace)
    query = request(service)
    query["issuance_time"] = "2022-01-01T04:00:00Z"
    with pytest.raises(ValueError, match="later development"):
        service.predict(query)


def test_baselines_use_common_cohort_without_replacing_deployed_models(workspace):
    from bharatblend.benchmark import run_benchmark
    from bharatblend.training import file_sha256

    before = {name: file_sha256(workspace / name) for name in ("models.json", "metrics.json")}
    report = run_benchmark(workspace, n_resamples=40)
    assert report["status"] == "completed"
    assert report["training_input_fingerprints_verified"] is True
    result = report["variables"]["rainfall"]
    count = result["split_counts"]["test"]
    assert all(row["n_cases"] == count for row in result["methods"].values())
    reference = result["development_selected_baseline"]
    assert reference not in ("interpretable", "spatial")
    candidates = {k: v for k, v in result["development_methods"].items() if k not in ("interpretable", "spatial")}
    assert reference == min(candidates, key=lambda k: candidates[k]["proper_score"])
    for name, digest in before.items():
        assert file_sha256(workspace / name) == digest
    assert "emos_adapted" in result["methods"] and "bma_fixed_components" in result["methods"]
    saved = json.loads((workspace / "benchmark.json").read_text())
    scores = json.loads((workspace / saved["run_path"] / "rainfall_case_scores.json").read_text())
    assert len(scores) == count == len({row["case_id"] for row in scores})
    service = BlendService(workspace)
    assert service.benchmark()["run_id"] == report["run_id"]
    assert TestClient(create_app(workspace)).get("/benchmark").status_code == 200
    # Raw empirical scoring retains spread; it must not be relabelled mean-only.
    assert result["methods"]["raw_members:gefs"]["proper_score"] != result["methods"]["raw_mean:gefs"]["proper_score"]


def test_benchmark_rejects_modified_archive_and_wrong_model_report(workspace, tmp_path):
    import shutil
    from bharatblend.benchmark import run_benchmark

    for name in ("models.json", "forecasts.jsonl", "observations.jsonl"):
        shutil.copyfile(workspace / name, tmp_path / name)
    original_model = (tmp_path / "models.json").read_text()
    legacy = json.loads(original_model)
    legacy.pop("input_sha256")
    (tmp_path / "models.json").write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="training input fingerprints"):
        run_benchmark(tmp_path)
    (tmp_path / "models.json").write_text(original_model)
    rows = load_jsonl(tmp_path / "observations.jsonl", ObservationRecord)
    rows[-1] = rows[-1].model_copy(update={"value": rows[-1].value + 100})
    write_jsonl(tmp_path / "observations.jsonl", rows)
    with pytest.raises(ValueError, match="changed after training"):
        run_benchmark(tmp_path)
    service = BlendService(tmp_path)
    (tmp_path / "benchmark.json").write_text(json.dumps({"data_kind": "synthetic", "files_sha256": {"models.json": "wrong"}}))
    with pytest.raises(ValueError, match="different model version"):
        service.benchmark()
