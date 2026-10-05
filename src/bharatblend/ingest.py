"""Local canonical data ingestion and bounded, read-only provider discovery.

Providers are described here, never enabled or downloaded automatically. A listing
is evidence that an object exists, not that its meteorological content is usable.
"""

from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

from bharatblend.schemas import ForecastRecord, ObservationRecord, UNITS, load_jsonl


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include their UTC offset")
    return result.astimezone(timezone.utc)


def _payload(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return record.model_dump(mode="json")


def read_records(path: str | Path, kind: str = "forecast") -> list[Any]:
    """Read and schema-validate JSONL or CSV. CSV ``value`` cells contain JSON.

    Missing provenance is never inferred as real data. Timestamp/unit/value
    validation belongs to the shared canonical schemas.
    """
    classes = {"forecast": ForecastRecord, "observation": ObservationRecord}
    if kind not in classes:
        raise ValueError("kind must be 'forecast' or 'observation'")
    cls = classes[kind]
    path = Path(path)
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return list(load_jsonl(path, cls))
    if path.suffix.lower() != ".csv":
        raise ValueError("Expected a .jsonl, .ndjson, or .csv file")
    records = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "value" not in reader.fieldnames:
            raise ValueError("CSV requires a header including a value column")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV contains duplicate column names")
        for line, row in enumerate(reader, 2):
            try:
                if None in row:
                    raise ValueError("More CSV fields than header columns")
                payload = {key: value for key, value in row.items() if value not in (None, "")}
                payload["value"] = json.loads(payload["value"])
                if "context" in payload:
                    payload["context"] = json.loads(payload["context"])
                records.append(cls.model_validate(payload))
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{path}: CSV row {line}: {exc}") from exc
    return records


def load_registry(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        registry = json.load(stream)
    if not isinstance(registry, dict) or not isinstance(registry.get("models"), list):
        raise ValueError("Model registry must contain a models array")
    if any(not isinstance(model, dict) or not isinstance(model.get("id"), str)
           or not model["id"].strip() for model in registry["models"]):
        raise ValueError("Every model registry entry needs a nonempty string id")
    ids = [model["id"] for model in registry["models"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Model registry contains duplicate ids")
    return registry


def audit_records(
    forecasts: Iterable[ForecastRecord],
    observations: Iterable[ObservationRecord] = (),
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize provenance, schema errors and source eligibility without joins.

    This does not certify skill or temporal availability of remote files. Exact
    forecast/observation joins and observation-latency filtering are performed by
    the dataset layer. A disabled registry source is a warning, not an implicit
    instruction to enable it.
    """
    forecast_rows = list(forecasts)
    observation_rows = list(observations)
    models = {item["id"]: item for item in (registry or {}).get("models", [])}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    provenance: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    seen_forecasts: set[tuple[Any, ...]] = set()
    seen_observations: set[str] = set()

    def issue(target: list, code: str, message: str, **context: Any) -> None:
        target.append({"code": code, "message": message, **context})

    for index, raw in enumerate(forecast_rows):
        try:
            row = _payload(ForecastRecord.model_validate(_payload(raw)))
        except (ValueError, TypeError) as exc:
            issue(errors, "invalid_forecast", str(exc), record_index=index)
            continue
        source = row["source_id"]
        by_source[source].append(row)
        provenance[row["data_kind"]] += 1
        key = tuple(row.get(k) for k in (
            "source_id", "model_version", "initialization_source", "initialization_time",
            "available_at", "valid_start", "valid_end", "location_id", "variable",
        ))
        if key in seen_forecasts:
            issue(errors, "duplicate_forecast", "Duplicate canonical forecast identity", source_id=source, record_index=index)
        seen_forecasts.add(key)
        spec = models.get(source)
        record_cutoff = row.get("training_end")
        registry_cutoff = None
        if spec and row["model_version"] == spec.get("version"):
            registry_cutoff = spec.get("training_end")
        cutoffs = [value for value in (record_cutoff, registry_cutoff) if value]
        if cutoffs:
            cutoff = max(_utc(value) for value in cutoffs)
            if _utc(row["initialization_time"]) <= cutoff:
                issue(errors, "training_overlap", "Initialization overlaps the source model training period", source_id=source, record_index=index)
        if spec and (spec.get("version") is None or row["model_version"] == spec.get("version")):
            capability = spec.get("capabilities", {}).get(row["variable"])
            if capability is False:
                issue(errors, "unsupported_variable", "Registry version does not natively provide this variable; use a separately registered diagnostic pipeline", source_id=source, variable=row["variable"], record_index=index)

    for index, raw in enumerate(observation_rows):
        try:
            row = _payload(ObservationRecord.model_validate(_payload(raw)))
        except (ValueError, TypeError) as exc:
            issue(errors, "invalid_observation", str(exc), record_index=index)
            continue
        provenance[row["data_kind"]] += 1
        quality[row["quality"]] += 1
        if row["observation_id"] in seen_observations:
            issue(errors, "duplicate_observation", "Duplicate observation_id", observation_id=row["observation_id"], record_index=index)
        seen_observations.add(row["observation_id"])

    sources = []
    for source, rows in sorted(by_source.items()):
        spec = models.get(source)
        if registry is not None and spec is None:
            issue(warnings, "unregistered_source", "Source has no registry entry", source_id=source)
        if spec and not spec.get("enabled", False):
            issue(warnings, "source_disabled", "Registry source is not enabled for operations", source_id=source)
        if spec and spec.get("kind") != "forecast_expert":
            issue(warnings, "nonforecast_component", "Component requires a versioned forecasting pipeline before acting as an independent expert", source_id=source)
        versions = sorted({row["model_version"] for row in rows})
        if len(versions) > 1:
            issue(warnings, "mixed_versions", "Keep source versions separate when fitting and evaluating", source_id=source)
        if spec and spec.get("version") and any(version != spec["version"] for version in versions):
            issue(warnings, "version_mismatch", "Some supplied versions differ from the documented registry version; audit their capabilities and cutoff separately", source_id=source)
        no_cutoff = sum(not row.get("training_end") for row in rows)
        if spec and spec.get("model_class") in {"ai", "hybrid"} and no_cutoff:
            issue(warnings, "missing_record_training_cutoff", "Some AI records lack their checkpoint training cutoff; registry metadata only applies to an exact version match", source_id=source, count=no_cutoff)
        sources.append({
            "source_id": source,
            "record_count": len(rows),
            "versions": versions,
            "variables": sorted({row["variable"] for row in rows}),
            "initialization_sources": sorted({row.get("initialization_source") or "unknown" for row in rows}),
            "first_initialization": min(_utc(row["initialization_time"]) for row in rows).isoformat(),
            "last_initialization": max(_utc(row["initialization_time"]) for row in rows).isoformat(),
            "forecast_kinds": sorted({row["forecast_kind"] for row in rows}),
            "enabled_for_operations": bool(spec and spec.get("enabled", False)),
        })
    if "real" in provenance and "synthetic" in provenance:
        issue(errors, "mixed_provenance", "Real and synthetic records must not share an experiment")
    if not forecast_rows:
        issue(errors, "empty_forecasts", "At least one forecast record is required")
    return {
        "valid": not errors,
        "forecast_record_count": len(forecast_rows),
        "observation_record_count": len(observation_rows),
        "source_count": len(sources),
        "data_kind_counts": dict(provenance),
        "observation_quality_counts": dict(quality),
        "sources": sources,
        "errors": errors,
        "warnings": warnings,
        "remote_availability_verified": False,
        "skill_verified": False,
    }


def audit_manifest(
    forecast_path: str | Path,
    observation_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit supplied canonical files; return a JSON-serializable report."""
    return audit_records(
        read_records(forecast_path, "forecast"),
        read_records(observation_path, "observation") if observation_path else [],
        load_registry(registry_path) if registry_path else None,
    )


class NOAAAIWPCatalog:
    """Discover at most one bounded page in NOAA's public AIWP S3 catalogue."""

    bucket = "noaa-oar-mlwp-data"
    endpoint = "https://noaa-oar-mlwp-data.s3.amazonaws.com/"

    def __init__(self, session: Any = None) -> None:
        self.session = session

    def describe(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "documentation": "https://registry.opendata.aws/aiwp/",
            "enabled": False,
            "documented_sources": {
                "graphcast_operational": {"from": "2022-01", "rainfall": True},
                "pangu_weather": {"from": "2020-10", "rainfall": False},
                "fourcastnet_v2_small": {"from": "2020-10", "rainfall": False},
            },
            "note": "Dates describe provider documentation, not a verified complete inventory. GFS/IFS initializations are distinct sources.",
        }

    def list_objects(self, prefix: str = "", max_keys: int = 100, continuation_token: str | None = None) -> dict[str, Any]:
        if not isinstance(max_keys, int) or isinstance(max_keys, bool) or not 1 <= max_keys <= 1000:
            raise ValueError("max_keys must be an integer from 1 to 1000")
        if not isinstance(prefix, str) or len(prefix) > 1024:
            raise ValueError("prefix must be a string of at most 1024 characters")
        if continuation_token is not None and (not isinstance(continuation_token, str)
                                                or len(continuation_token) > 8192):
            raise ValueError("continuation_token must be a string of at most 8192 characters")
        endpoint = urlparse(self.endpoint)
        if endpoint.scheme != "https" or endpoint.netloc != "noaa-oar-mlwp-data.s3.amazonaws.com":
            raise ValueError("Only the trusted NOAA AIWP HTTPS endpoint is allowed")
        client = self.session
        if client is None:
            import requests
            client = requests
        params: dict[str, Any] = {"list-type": 2, "prefix": prefix, "max-keys": max_keys}
        if continuation_token:
            params["continuation-token"] = continuation_token
        response = client.get(self.endpoint, params=params, timeout=(5, 30),
                              allow_redirects=False, stream=True)
        try:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError("Catalogue redirects are not allowed")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError("Catalogue response exceeds the 2 MB limit")
        finally:
            response.close()
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Catalogue XML must not contain document types or entities")
        root = ET.fromstring(body)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        if root.tag != "{http://s3.amazonaws.com/doc/2006-03-01/}ListBucketResult":
            raise ValueError("Expected an S3 ListBucketResult response")
        objects = []
        for item in root.findall("s3:Contents", ns):
            if len(objects) >= max_keys:
                raise ValueError("Provider exceeded the requested object limit")
            key = item.findtext("s3:Key", namespaces=ns)
            if not key or not key.startswith(prefix):
                raise ValueError("Provider returned an object outside the requested prefix")
            size = int(item.findtext("s3:Size", "0", ns))
            if size < 0:
                raise ValueError("Object size cannot be negative")
            objects.append({
                "key": key,
                "size_bytes": size,
                "last_modified": item.findtext("s3:LastModified", namespaces=ns),
                "etag": item.findtext("s3:ETag", namespaces=ns),
                "url": self.endpoint + quote(key, safe="/"),
            })
        return {
            "bucket": self.bucket,
            "prefix": prefix,
            "objects": objects,
            "truncated": root.findtext("s3:IsTruncated", "false", ns).lower() == "true",
            "next_continuation_token": root.findtext("s3:NextContinuationToken", namespaces=ns),
            "content_verified": False,
        }


def describe_noaa_source(source_id: str) -> dict[str, Any]:
    """Describe access paths without a network request or enabling a provider."""
    sources = {
        "gfs": {
            "bucket": "noaa-gfs-bdp-pds",
            "documentation": "https://registry.opendata.aws/noaa-gfs-bdp-pds/",
            "historical_documentation": "https://data.ucar.edu/en/dataset/ncep-gfs-0-25-degree-global-forecast-grids-historical-archive",
        },
        "gefs": {
            "bucket": "noaa-gefs-pds",
            "documentation": "https://registry.opendata.aws/noaa-gefs/",
            "historical_documentation": "https://registry.opendata.aws/noaa-gefs-reforecast/",
            "reforecast_note": "GEFS v12 2000–2019 reforecasts have reduced member counts; they are not the operational ensemble archive.",
        },
    }
    if source_id not in sources:
        raise ValueError("Supported NOAA source descriptions: gfs, gefs")
    return {"source_id": source_id, "enabled": False, "availability_verified": False, **sources[source_id]}


def _convert_unit(value: float, source_units: str, target_units: str) -> float:
    aliases = {"m s-1": "m/s", "m s**-1": "m/s", "m s^-1": "m/s", "degree_Celsius": "degC", "degrees_Celsius": "degC", "C": "degC", "kelvin": "K", "kg m-2": "mm", "kg m**-2": "mm"}
    source_units = aliases.get(source_units, source_units)
    target_units = aliases.get(target_units, target_units)
    if source_units == target_units:
        return value
    if (source_units, target_units) == ("m", "mm"):
        return value * 1000
    if (source_units, target_units) == ("K", "degC"):
        return value - 273.15
    raise ValueError(f"Unsupported unit conversion: {source_units!r} to {target_units!r}")


def convert_netcdf(
    path: str | Path,
    *,
    source_id: str,
    model_version: str,
    initialization_time: str | datetime,
    available_at: str | datetime,
    fields: Sequence[Mapping[str, Any]],
    data_kind: str,
    forecast_kind: str = "hindcast",
    initialization_source: str | None = None,
    training_end: str | datetime | None = None,
    time_coordinate: str = "valid_time",
    latitude_coordinate: str = "latitude",
    longitude_coordinate: str = "longitude",
    member_dimension: str | None = None,
    time_zone: str | None = None,
    location_prefix: str = "grid",
    max_records: int = 100_000,
) -> list[ForecastRecord]:
    """Convert a *local*, rectilinear NetCDF subset with explicit field mapping.

    Each mapping requires ``variable``, ``field`` (two names for wind_uv),
    ``units``, ``source_units``, ``statistic`` and ``interval_hours``. Time
    coordinates are interval endpoints. ``instantaneous`` requires zero hours;
    rainfall requires ``interval_total`` and positive hours. Cumulative totals,
    rates, curvilinear grids and implicit 12h-to-6h disaggregation are rejected.
    A timezone-naive NetCDF time axis requires explicit ``time_zone='UTC'``.
    """
    import numpy as np
    try:
        import xarray as xr
    except ImportError as exc:
        raise ImportError("NetCDF conversion needs xarray and a NetCDF engine") from exc
    if not Path(path).is_file():
        raise ValueError("convert_netcdf requires a local file, not a remote URL")
    if not fields:
        raise ValueError("At least one explicit field mapping is required")
    if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    initialization_time = _utc(initialization_time)
    available_at = _utc(available_at)
    if training_end is not None:
        training_end = _utc(training_end)
    result: list[ForecastRecord] = []
    with xr.open_dataset(path) as dataset:
        for coord in (time_coordinate, latitude_coordinate, longitude_coordinate):
            if coord not in dataset.coords or dataset[coord].ndim != 1:
                raise ValueError(f"Expected one-dimensional coordinate {coord!r}")
        time_dim = dataset[time_coordinate].dims[0]
        lat_dim = dataset[latitude_coordinate].dims[0]
        lon_dim = dataset[longitude_coordinate].dims[0]
        if len({time_dim, lat_dim, lon_dim}) != 3:
            raise ValueError("Only rectilinear latitude/longitude grids are supported")
        if member_dimension and (member_dimension not in dataset.dims
                                 or member_dimension in {time_dim, lat_dim, lon_dim}):
            raise ValueError("member_dimension must name a separate ensemble dimension")
        for dim in {time_dim, lat_dim, lon_dim} | ({member_dimension} if member_dimension else set()):
            if not dataset.sizes[dim]:
                raise ValueError(f"Empty dimension {dim!r}")
        for coord in (latitude_coordinate, longitude_coordinate):
            values = np.asarray(dataset[coord].values, dtype=float)
            if not np.isfinite(values).all():
                raise ValueError(f"Non-finite coordinates in {coord!r}")
            if coord == longitude_coordinate:
                if np.any((values < -180) | (values > 360)):
                    raise ValueError("Longitude must use -180..180 or 0..360 degrees")
                values = (values + 180) % 360 - 180
            elif np.any(np.abs(values) > 90):
                raise ValueError("Latitude must lie in -90..90 degrees")
            if len(np.unique(np.round(values, 8))) != len(values):
                raise ValueError(f"Duplicate canonical coordinates in {coord!r}")
        times = np.asarray(dataset[time_coordinate].values)
        if not np.issubdtype(times.dtype, np.datetime64) or np.isnat(times).any():
            raise ValueError("Time coordinate must contain decoded datetimes without NaT")
        if len(np.unique(times)) != len(times):
            raise ValueError("Time coordinate contains duplicate endpoints")
        count = len(fields) * dataset.sizes[time_dim] * dataset.sizes[lat_dim] * dataset.sizes[lon_dim]
        if count > max_records:
            raise ValueError(f"Conversion would create {count} records; subset first or raise max_records explicitly")
        for mapping in fields:
            required = {"variable", "field", "units", "source_units", "statistic", "interval_hours"}
            if required - mapping.keys():
                raise ValueError(f"Missing explicit field mapping keys: {sorted(required - mapping.keys())}")
            variable = str(mapping["variable"])
            if variable not in UNITS or mapping["units"] != UNITS[variable]:
                raise ValueError("Field mapping must use a canonical variable and its canonical units")
            hours = float(mapping["interval_hours"])
            statistic = mapping["statistic"]
            if not math.isfinite(hours) or hours < 0:
                raise ValueError("interval_hours must be finite and nonnegative")
            if statistic not in {"instantaneous", "interval_total", "interval_mean"}:
                raise ValueError("Unsupported statistic; cumulative/rate data must be explicitly converted before import")
            if (statistic == "instantaneous") != (hours == 0):
                raise ValueError("instantaneous fields require zero hours; interval fields require positive hours")
            if variable == "rainfall" and (statistic != "interval_total" or hours <= 0):
                raise ValueError("Rainfall must contain positive-duration interval totals; no implicit accumulation splitting")
            names = mapping["field"]
            names = [names] if isinstance(names, str) else list(names)
            expected_fields = 2 if variable == "wind_uv" else 1
            if len(names) != expected_fields:
                raise ValueError(f"{variable} needs {expected_fields} field(s)")
            for name in names:
                if name not in dataset:
                    raise ValueError(f"Missing NetCDF field {name!r}")
                dimensions = set(dataset[name].dims)
                expected_dims = {time_dim, lat_dim, lon_dim} | ({member_dimension} if member_dimension else set())
                if dimensions != expected_dims:
                    raise ValueError(f"{name}: expected dimensions {sorted(expected_dims)}, got {sorted(dimensions)}; select levels/members explicitly")
                attr_units = dataset[name].attrs.get("units")
                if attr_units is not None:
                    # Identity conversion checks equivalent aliases without guessing unknown units.
                    try:
                        identity = _convert_unit(1.0, str(attr_units), str(mapping["source_units"]))
                    except ValueError as exc:
                        raise ValueError(f"{name}: configured source_units conflict with NetCDF units") from exc
                    if identity != 1.0:
                        raise ValueError(f"{name}: configured source_units conflict with NetCDF units")
            for ti, time_value in enumerate(dataset[time_coordinate].values):
                if not np.issubdtype(np.asarray(time_value).dtype, np.datetime64):
                    raise ValueError("Time coordinate must contain decoded datetimes, not numeric lead hours")
                if np.isnat(time_value):
                    raise ValueError("Time coordinate contains NaT")
                if time_zone != "UTC":
                    raise ValueError("Decoded NetCDF times require explicit time_zone='UTC'")
                end = _utc(np.datetime_as_string(time_value, unit="us") + "+00:00")
                start = end - timedelta(hours=hours)
                bounds_name = mapping.get("interval_bounds") or dataset[time_coordinate].attrs.get("bounds")
                if bounds_name and hours > 0:
                    if bounds_name not in dataset:
                        raise ValueError(f"Missing declared interval bounds {bounds_name!r}")
                    bounds = np.asarray(dataset[bounds_name].isel({time_dim: ti}).values)
                    if bounds.shape != (2,) or not np.issubdtype(bounds.dtype, np.datetime64):
                        raise ValueError("Interval bounds must contain two decoded datetimes per time")
                    expected = np.array([start.replace(tzinfo=None), end.replace(tzinfo=None)], dtype="datetime64[us]")
                    if not np.array_equal(bounds, expected):
                        raise ValueError("Configured interval conflicts with NetCDF interval bounds; no splitting is performed")
                for yi, lat in enumerate(dataset[latitude_coordinate].values):
                    for xi, lon in enumerate(dataset[longitude_coordinate].values):
                        selector = {time_dim: ti, lat_dim: yi, lon_dim: xi}
                        channels = [np.asarray(dataset[name].isel(selector).values, dtype=float).reshape(-1) for name in names]
                        if any(not np.isfinite(channel).all() for channel in channels):
                            raise ValueError(f"Non-finite values at time={ti}, latitude={yi}, longitude={xi}; apply an explicit missing-data policy before import")
                        converted = [[_convert_unit(float(value), str(mapping["source_units"]), str(mapping["units"])) for value in channel] for channel in channels]
                        if variable == "wind_uv":
                            value: Any = [list(pair) for pair in zip(*converted)]
                        else:
                            value = converted[0] if member_dimension else converted[0][0]
                        normalized_lon = (float(lon) + 180) % 360 - 180
                        payload = {
                            "source_id": source_id,
                            "model_version": model_version,
                            "initialization_time": initialization_time,
                            "available_at": available_at,
                            "valid_start": start,
                            "valid_end": end,
                            "location_id": f"{location_prefix}:{float(lat):.8f}:{normalized_lon:.8f}",
                            "latitude": float(lat),
                            "longitude": normalized_lon,
                            "variable": variable,
                            "units": mapping["units"],
                            "value": value,
                            "data_kind": data_kind,
                            "forecast_kind": forecast_kind,
                            "initialization_source": initialization_source,
                            "training_end": training_end,
                        }
                        result.append(ForecastRecord.model_validate(payload))
    return result
