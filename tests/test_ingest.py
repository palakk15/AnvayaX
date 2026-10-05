"""Offline fixtures exercise provenance, provider bounds and accumulation semantics."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from bharatblend.ingest import (
    NOAAAIWPCatalog,
    audit_manifest,
    audit_records,
    convert_netcdf,
    describe_noaa_source,
    load_registry,
    read_records,
)
from bharatblend.schemas import ForecastRecord, ObservationRecord, write_jsonl


def forecast(**updates):
    payload = {
        "location_id": "station-a", "latitude": 19, "longitude": 73,
        "variable": "rainfall", "units": "mm", "data_kind": "synthetic",
        "valid_start": "2025-01-01T06:00:00Z", "valid_end": "2025-01-01T12:00:00Z",
        "source_id": "gfs", "model_version": "fixture-v1",
        "initialization_time": "2025-01-01T00:00:00Z",
        "available_at": "2025-01-01T03:00:00Z", "value": [0.0, 2.0, 4.0],
    }
    payload.update(updates)
    return ForecastRecord.model_validate(payload)


def observation(**updates):
    payload = {key: value for key, value in forecast().model_dump().items()
               if key in ObservationRecord.model_fields}
    payload.update(observation_id="fixture-obs", available_at="2025-01-01T15:00:00Z", value=2)
    payload.update(updates)
    return ObservationRecord.model_validate(payload)


def test_csv_and_jsonl_roundtrip_preserve_members_and_provenance(tmp_path):
    rows = [forecast(), forecast(variable="wind_uv", units="m/s", value=[[3, 4], [5, 6]])]
    jsonl = tmp_path / "forecast.jsonl"
    write_jsonl(jsonl, rows)
    csv_path = tmp_path / "forecast.csv"
    payloads = [row.model_dump(mode="json") for row in rows]
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=payloads[0].keys())
        writer.writeheader()
        for row in payloads:
            row["value"] = json.dumps(row["value"])
            writer.writerow(row)
    assert read_records(csv_path) == read_records(jsonl) == rows


def test_csv_reports_bad_row_and_never_infers_provenance(tmp_path):
    payload = forecast().model_dump(mode="json")
    payload.pop("data_kind")
    path = tmp_path / "missing.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=payload.keys())
        writer.writeheader()
        payload["value"] = json.dumps(payload["value"])
        writer.writerow(payload)
    with pytest.raises(ValueError, match="(?s)CSV row 2.*data_kind"):
        read_records(path)


def test_manifest_audits_mixed_provenance_and_training_overlap(tmp_path):
    forecast_path, obs_path = tmp_path / "f.jsonl", tmp_path / "o.jsonl"
    write_jsonl(forecast_path, [forecast(training_end="2025-02-01T00:00:00Z")])
    write_jsonl(obs_path, [observation(data_kind="real")])
    report = audit_manifest(forecast_path, obs_path)
    assert not report["valid"]
    assert {row["code"] for row in report["errors"]} == {"training_overlap", "mixed_provenance"}
    assert report["remote_availability_verified"] is False
    assert report["skill_verified"] is False


def test_audit_duplicate_forecasts_and_invalid_payloads():
    report = audit_records([forecast(), forecast(), {"source_id": "broken"}], [observation(), observation()])
    assert {row["code"] for row in report["errors"]} == {
        "duplicate_forecast", "duplicate_observation", "invalid_forecast",
    }


def test_registry_cutoff_only_applies_to_matching_version():
    registry = {"models": [{"id": "gfs", "version": "new-checkpoint", "model_class": "ai",
                             "training_end": "2025-02-01T00:00:00Z", "enabled": False,
                             "kind": "forecast_expert", "capabilities": {"rainfall": True}}]}
    assert audit_records([forecast()], registry=registry)["valid"]
    report = audit_records([forecast(model_version="new-checkpoint")], registry=registry)
    assert not report["valid"]
    assert report["errors"][0]["code"] == "training_overlap"


def test_registry_is_candid_about_capabilities_and_skill():
    path = Path(__file__).resolve().parents[1] / "configs/model_registry.json"
    registry = load_registry(path)
    models = {row["id"]: row for row in registry["models"]}
    assert len(models) == 27
    assert all(row["india_skill_metrics"] is None and not row["enabled"] for row in models.values())
    for source in ("pangu_weather", "fourcastnet_v2_small", "aurora_original", "neuralgcm_standard"):
        assert models[source]["capabilities"]["rainfall"] is False
    assert models["neuralgcm_precipitation"]["grid"] == "2.8 degrees"
    assert models["corrdiff"]["kind"] == "downscaler"
    report = audit_records([forecast(source_id="pangu_weather")], registry=registry)
    assert report["errors"][0]["code"] == "unsupported_variable"


@pytest.mark.parametrize("contents", [{"models": [{"id": "a"}, {"id": "a"}]}, {"models": [{}]}])
def test_bad_registry_rejected(tmp_path, contents):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(contents), encoding="utf-8")
    with pytest.raises(ValueError):
        load_registry(path)


class Response:
    def __init__(self, body, status_code=200):
        self.body, self.status_code, self.closed = body, status_code, False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("provider HTTP error")

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def listing(key="GRAP_v100_GFS/2025/a b.nc", contents=None):
    item = f"<Contents><Key>{key}</Key><Size>42</Size><LastModified>2025-01-02T00:00:00Z</LastModified></Contents>"
    return ("<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
            "<IsTruncated>true</IsTruncated><NextContinuationToken>opaque</NextContinuationToken>"
            + (item if contents is None else contents) + "</ListBucketResult>").encode()


def test_s3_listing_is_one_readonly_bounded_page():
    response = Response(listing())
    session = Session(response)
    result = NOAAAIWPCatalog(session).list_objects("GRAP", max_keys=1)
    assert len(session.calls) == 1 and response.closed
    kwargs = session.calls[0][1]
    assert kwargs["params"] == {"list-type": 2, "prefix": "GRAP", "max-keys": 1}
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
    assert result["objects"][0]["url"].endswith("/a%20b.nc")
    assert result["next_continuation_token"] == "opaque"
    assert result["content_verified"] is False


@pytest.mark.parametrize("body,message", [
    (listing("PANG/a.nc"), "outside"),
    (b"x" * 2_000_001, "2 MB"),
    (b"<!DOCTYPE test><test/>", "document types"),
    (b"<error/>", "ListBucketResult"),
], ids=["wrong-prefix", "oversized-response", "doctype", "wrong-root"])
def test_s3_rejects_bad_provider_responses(body, message):
    response = Response(body)
    with pytest.raises(ValueError, match=message):
        NOAAAIWPCatalog(Session(response)).list_objects("GRAP", max_keys=1)
    assert response.closed


def test_s3_rejects_redirect_and_too_many_objects():
    with pytest.raises(ValueError, match="redirects"):
        NOAAAIWPCatalog(Session(Response(b"", status_code=302))).list_objects()
    item = "<Contents><Key>GRAP/a.nc</Key><Size>1</Size></Contents>"
    with pytest.raises(ValueError, match="object limit"):
        NOAAAIWPCatalog(Session(Response(listing(contents=item * 2)))).list_objects(max_keys=1)


def test_s3_validation_precedes_network_and_noaa_descriptions_are_not_access_claims():
    session = Session(Response(b""))
    with pytest.raises(ValueError, match="max_keys"):
        NOAAAIWPCatalog(session).list_objects(max_keys=True)
    assert not session.calls
    assert not describe_noaa_source("gfs")["availability_verified"]
    assert NOAAAIWPCatalog(session).describe()["documented_sources"]["pangu_weather"]["rainfall"] is False


def netcdf_fixture(tmp_path, *, values=None, source_units="m", bounds=False, lon=None):
    xr = pytest.importorskip("xarray")
    times = np.array(["2025-01-01T12:00", "2025-01-01T18:00"], dtype="datetime64[ns]")
    ds = xr.Dataset(
        {"tp": (("time", "lat", "lon"), np.array([.001, .002]).reshape(2, 1, 1)
                 if values is None else values, {"units": source_units})},
        coords={"valid_time": ("time", times), "latitude": ("lat", [19.0]),
                "longitude": ("lon", [287.0] if lon is None else lon)},
    )
    if bounds:
        ds["time_bounds"] = (("time", "bounds"), np.column_stack((times - np.timedelta64(6, "h"), times)))
        ds["valid_time"].attrs["bounds"] = "time_bounds"
        ds["valid_time"].encoding["units"] = "hours since 2025-01-01"
        ds["time_bounds"].encoding["units"] = "hours since 2025-01-01"
    path = tmp_path / "fixture.nc"
    ds.to_netcdf(path, engine="scipy")
    return path


def convert(path, **updates):
    args = dict(source_id="graphcast_operational", model_version="fixture-v1",
                initialization_time="2025-01-01T00:00:00Z", available_at="2025-01-01T03:00:00Z",
                data_kind="synthetic", time_zone="UTC",
                fields=[{"variable": "rainfall", "field": "tp", "units": "mm",
                         "source_units": "m", "statistic": "interval_total", "interval_hours": 6}])
    args.update(updates)
    return convert_netcdf(path, **args)


def test_netcdf_converts_units_longitudes_and_retains_exact_intervals(tmp_path):
    records = convert(netcdf_fixture(tmp_path, bounds=True))
    assert [row.value for row in records] == pytest.approx([1, 2])
    assert all(row.longitude == -73 for row in records)
    assert all((row.valid_end - row.valid_start).total_seconds() == 21600 for row in records)
    assert records[0].valid_start.hour == 6
    assert all(row.forecast_kind == "hindcast" and row.data_kind == "synthetic" for row in records)


def test_netcdf_rejects_declared_interval_mismatch_instead_of_splitting(tmp_path):
    field = {"variable": "rainfall", "field": "tp", "units": "mm", "source_units": "m",
             "statistic": "interval_total", "interval_hours": 12}
    with pytest.raises(ValueError, match="interval bounds"):
        convert(netcdf_fixture(tmp_path, bounds=True), fields=[field])


@pytest.mark.parametrize("kwargs,message", [
    ({"time_zone": None}, "time_zone"),
    ({"max_records": 1}, "subset first"),
    ({"max_records": True}, "positive integer"),
    ({"member_dimension": "unknown"}, "ensemble dimension"),
])
def test_netcdf_requires_explicit_mapping_and_bounds(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        convert(netcdf_fixture(tmp_path), **kwargs)


@pytest.mark.parametrize("statistic", ["cumulative", "rate", "instantaneous"])
def test_netcdf_rejects_ambiguous_rainfall_statistics(tmp_path, statistic):
    field = {"variable": "rainfall", "field": "tp", "units": "mm", "source_units": "m",
             "statistic": statistic, "interval_hours": 0 if statistic == "instantaneous" else 6}
    with pytest.raises(ValueError):
        convert(netcdf_fixture(tmp_path), fields=[field])


def test_netcdf_missing_cells_are_not_zero_rainfall(tmp_path):
    with pytest.raises(ValueError, match="Non-finite values"):
        convert(netcdf_fixture(tmp_path, values=np.array([np.nan, .002]).reshape(2, 1, 1)))


def test_netcdf_source_units_conflict_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="source_units conflict"):
        convert(netcdf_fixture(tmp_path, source_units="mm"))


def test_netcdf_converts_ensemble_temperature_and_wind_without_averaging(tmp_path):
    xr = pytest.importorskip("xarray")
    values = np.array([273.15, 283.15]).reshape(1, 2, 1, 1)
    ds = xr.Dataset(
        {"t2m": (("time", "member", "lat", "lon"), values, {"units": "K"}),
         "u10": (("time", "member", "lat", "lon"), values * 0 + 3, {"units": "m s-1"}),
         "v10": (("time", "member", "lat", "lon"), values * 0 + 4, {"units": "m/s"})},
        coords={"valid_time": ("time", np.array(["2025-01-01T12:00"], dtype="datetime64[ns]")),
                "member": [0, 1], "latitude": ("lat", [19]), "longitude": ("lon", [73])},
    )
    path = tmp_path / "ensemble.nc"
    ds.to_netcdf(path, engine="scipy")
    common = {"statistic": "instantaneous", "interval_hours": 0}
    records = convert(path, member_dimension="member", fields=[
        {**common, "variable": "temperature", "field": "t2m", "units": "degC", "source_units": "K"},
        {**common, "variable": "wind_uv", "field": ["u10", "v10"], "units": "m/s", "source_units": "m/s"},
    ])
    assert records[0].value == pytest.approx([0, 10])
    assert records[1].value == [[3, 4], [3, 4]]
    assert records[0].valid_start == records[0].valid_end
