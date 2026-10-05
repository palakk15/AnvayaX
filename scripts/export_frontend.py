"""Export bounded, explicitly synthetic demonstration data for the Vercel UI."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

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
    demo_cases = []
    for variable in VARIABLES:
        for location in locations:
            candidates = [c for c in cases if c["variable"] == variable and c["location_id"] == location]
            if not candidates:
                continue
            latest = max(candidates, key=lambda c: (c["valid_start"], c["issuance_time"]))
            query = {key: latest[key] for key in (
                "location_id", "variable", "valid_start", "valid_end", "issuance_time")}
            base = service.predict(query)
            outages = {}
            for source in [item["source_id"] for item in base["sources"]]:
                outages[source] = _compact(service.predict({**query, "excluded_sources": [source]}))
            demo_cases.append({"location_id": location, "variable": variable,
                               "selection": "latest prepared post-development case at this location",
                               "base": _compact(base), "source_outages": outages})

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
        base = service.predict(query)
        outages = {item["source_id"]: _compact(service.predict({**query, "excluded_sources": [item["source_id"]]}))
                   for item in base["sources"]}
        demo_cases.append({"location_id": "mumbai", "variable": "rainfall",
                           "selection": "illustrative high-observation synthetic case; not a skill estimate",
                           "base": _compact(base), "source_outages": outages})

    report = json.loads((workspace / "benchmark.json").read_text(encoding="utf-8"))
    if report.get("data_kind") != "synthetic" or report["files_sha256"]["models.json"] != service.model_digest:
        raise ValueError("Benchmark provenance does not match the synthetic model release")
    benchmark = {"data_kind": "synthetic", "run_id": report["run_id"],
                 "split": report["split"], "source_model_digest": service.model_digest,
                 "limitations": [text.replace("docs/baselines.md", "ppt.md") for text in report["limitations"]],
                 "variables": {}}
    for variable, row in report["variables"].items():
        benchmark["variables"][variable] = {
            "split_counts": row["split_counts"], "methods": row["methods"],
            "development_selected_baseline": row["development_selected_baseline"],
            "paired_comparisons_vs_selected_baseline": row["paired_comparisons_vs_selected_baseline"],
            "stratified_proper_scores": row["stratified_proper_scores"]}

    registry = json.loads(Path("configs/model_registry.json").read_text(encoding="utf-8"))
    data_registry = json.loads(Path("configs/data_registry.json").read_text(encoding="utf-8"))
    payload = {"schema_version": 1, "data_kind": "synthetic",
               "exported_at": datetime.now(UTC).isoformat(),
               "model_digest": service.model_digest,
               "message": "Generated integration cases, not actual output from the named weather models.",
               "cases": demo_cases}
    _write(destination / "demo.json", payload)
    _write(destination / "benchmark.json", benchmark)
    _write(destination / "catalogue.json", {"models": registry["models"],
                                           "datasets": data_registry["datasets"]})
    return {"cases": len(demo_cases), "models": len(registry["models"]),
            "datasets": len(data_registry["datasets"]),
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
