"""Export bounded, explicitly synthetic demonstration data for the Vercel UI."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import numpy as np

from bharatblend.catalogue import catalogue
from bharatblend.models import SpatialGate, raw_features
from bharatblend.schemas import utc
from bharatblend.service import BlendService


VARIABLES = ("rainfall", "temperature", "wind_uv")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False) + "\n", encoding="utf-8")


def _compact(receipt: dict) -> dict:
    return {key: receipt[key] for key in (
        "status", "data_kind", "track", "target", "summary", "sources", "probabilities",
        "model_digest", "receipt_id", "evidence", "explanation", "eligibility", "warnings"
    ) if key in receipt}


def _case_patch(service: BlendService, query: dict, spatial: SpatialGate) -> dict:
    """Export the exact four-token context used by the saved spatial model."""
    key = (query["location_id"], query["variable"], utc(query["valid_start"]), utc(query["valid_end"]))
    issue = utc(query["issuance_time"])
    target = service._case(key, issue, set())
    if target is None:
        raise ValueError("Prepared case is unavailable for neural export")
    neighbors = [target]
    for neighbor_key in service.index:
        if neighbor_key[0] != key[0] and neighbor_key[1:] == key[1:]:
            other = service._case(neighbor_key, issue, set())
            if other is not None:
                neighbors.append(other)
    xy = np.asarray([[c.observation.latitude, c.observation.longitude] for c in neighbors])
    near = np.argsort(np.sum((xy - xy[0]) ** 2, axis=1), kind="stable")[:4]
    chosen = [neighbors[i] for i in near]
    bank = service.banks[query["variable"]]
    masks = np.asarray([[d is not None for d in bank.predict(c)] for c in neighbors])
    weights, logits = spatial.predict(neighbors, masks)
    return {
        "patch_locations": [c.observation.location_id for c in chosen],
        "patch_rows": [raw_features(c, bank.sources) for c in chosen],
        "spatial_reference": {"weights": weights[0].tolist(), "logits": logits[0].tolist()},
    }


def _demo_case(service: BlendService, query: dict, selection: str, spatial: SpatialGate) -> dict:
    base = service.predict(query)
    outages = {item["source_id"]: _compact(service.predict({**query, "excluded_sources": [item["source_id"]]}))
               for item in base["sources"]}
    return {"location_id": query["location_id"], "variable": query["variable"],
            "selection": selection, "base": _compact(base), "source_outages": outages,
            **_case_patch(service, query, spatial)}


def export_frontend(workspace: str | Path, destination: str | Path) -> dict:
    workspace = Path(workspace)
    destination = Path(destination)
    service = BlendService(workspace)
    if service.artifact["data_kind"] != "synthetic":
        raise ValueError("This static exporter is only for labeled synthetic demonstration receipts")
    cases = service.cases()
    if not cases:
        raise ValueError("The prepared workspace has no post-development cases")
    locations = sorted({case["location_id"] for case in cases})
    gate_export = {"schema_version": 1, "data_kind": "synthetic", "model_digest": service.model_digest,
                   "variables": {}}
    spatial_models = {}
    for variable in VARIABLES:
        specification = service.artifact["variables"][variable]
        spatial_path = workspace / f"{variable}_spatial.pt"
        if not specification["spatial_available"] or not spatial_path.is_file():
            raise ValueError(f"A trained spatial checkpoint is required for {variable}")
        spatial_models[variable] = SpatialGate.load(spatial_path, service.banks[variable].sources,
                                                     service.gates[variable].features)
        state = {key: tensor.detach().cpu().tolist()
                 for key, tensor in spatial_models[variable].network.state_dict().items()}
        gate_export["variables"][variable] = {
            "sources": specification["sources"], "additive": specification["additive"],
            "spatial": {"architecture": "one_layer_local_spatial_transformer", "neighbors": 4,
                        "heads": 4, "hidden": 32, "feedforward": 64, "state": state},
        }
    demo_cases = []
    for variable in VARIABLES:
        for location in locations:
            candidates = [c for c in cases if c["variable"] == variable and c["location_id"] == location]
            if not candidates:
                continue
            latest = max(candidates, key=lambda c: (c["valid_start"], c["issuance_time"]))
            query = {key: latest[key] for key in (
                "location_id", "variable", "valid_start", "valid_end", "issuance_time")}
            demo_cases.append(_demo_case(service, query,
                              "latest prepared post-development case at this location", spatial_models[variable]))

    # An intentionally illustrated heavy-rain fixture, labeled as presentation
    # selection. It has no role in model selection or aggregate verification.
    rain_cases = [c for c in service.archive_cases
                  if c.variable == "rainfall" and c.observation.location_id == "mumbai"
                  and c.issuance_time.isoformat() >= service.artifact["dev_end"]]
    if rain_cases:
        selected = max(rain_cases, key=lambda c: c.observation.value)
        query = {"location_id": "mumbai", "variable": "rainfall",
                 "valid_start": selected.observation.valid_start.isoformat(),
                 "valid_end": selected.observation.valid_end.isoformat(),
                 "issuance_time": selected.issuance_time.isoformat()}
        demo_cases.append(_demo_case(service, query,
                          "illustrative high-observation synthetic case; not a skill estimate",
                          spatial_models["rainfall"]))

    report = json.loads((workspace / "benchmark.json").read_text(encoding="utf-8"))
    if report.get("data_kind") != "synthetic" or report["files_sha256"]["models.json"] != service.model_digest:
        raise ValueError("Benchmark provenance does not match the synthetic model release")
    benchmark = {"data_kind": "synthetic", "run_id": report["run_id"],
                 "split": report["split"], "source_model_digest": service.model_digest,
                 "limitations": [text.replace("docs/baselines.md", "the private research guide") for text in report["limitations"]],
                 "variables": {}}
    for variable, row in report["variables"].items():
        benchmark["variables"][variable] = {
            "split_counts": row["split_counts"], "methods": row["methods"],
            "development_selected_baseline": row["development_selected_baseline"],
            "paired_comparisons_vs_selected_baseline": row["paired_comparisons_vs_selected_baseline"],
            "stratified_proper_scores": row["stratified_proper_scores"]}

    profile = catalogue()
    contexts = json.loads(Path("configs/model_context.json").read_text(encoding="utf-8"))
    if set(contexts) != {item["id"] for item in profile["models"]}:
        raise ValueError("Every catalogued model needs a four-axis context profile")
    for item in profile["models"]:
        item["use_context"] = contexts[item["id"]]
        if item["id"] == "graphcast_operational":
            item["name"] = "GraphCastGFS archive"
            item["description"] = ("Archived GraphCastGFS research/experimental product; "
                                   "distinct from NOAA's newer operational AIGFS.")
            item["status"] = "research_archive_candidate"
            item["evidence_urls"].append(
                "https://registry.opendata.aws/noaa-nws-graphcastgfs-pds/")
    additions = json.loads(Path("configs/atlas_additions.json").read_text(encoding="utf-8"))
    if {item["id"] for item in additions} & {item["id"] for item in profile["models"]}:
        raise ValueError("Atlas additions duplicate a core model ID")
    for item in additions:
        if not all(item.get("use_context", {}).get(axis) for axis in
                   ("region", "season", "lead", "weather", "analysis")):
            raise ValueError(f"Atlas addition {item['id']} lacks context analysis")
        item["catalogue_only"] = True
        item["enabled"] = False
        item["assessment_basis"] = ("Provider documentation and engineering suitability; "
                                    "not measured Indian forecast skill.")
    profile["models"].extend(additions)
    for dataset in profile["datasets"]:
        if dataset["id"] == "open_meteo_named_models":
            dataset["coverage"] = "Searchable Indian places and up to 96 future hourly provider values"
            dataset["reference_review"] = "Provider forecast and geocoding documentation reviewed on 2026-09-28"
    payload = {"schema_version": 1, "data_kind": "synthetic",
               "exported_at": datetime.now(UTC).isoformat(),
               "model_digest": service.model_digest,
               "message": "Generated integration cases, not actual output from the named weather models.",
               "cases": demo_cases}
    _write(destination / "demo.json", payload)
    _write(destination / "gate.json", gate_export)
    _write(destination / "benchmark.json", benchmark)
    _write(destination / "catalogue.json", {"models": profile["models"],
                                           "datasets": profile["datasets"]})
    return {"cases": len(demo_cases), "models": len(profile["models"]),
            "datasets": len(profile["datasets"]),
            "model_digest": service.model_digest,
            "files": {p.name: {"bytes": p.stat().st_size,
                                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(destination.glob("*.json"))}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="artifacts/research_v2")
    parser.add_argument("--output", default="site/data")
    arguments = parser.parse_args()
    print(json.dumps(export_frontend(arguments.workspace, arguments.output), indent=2))
