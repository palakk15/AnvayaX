"""Frozen-cohort comparisons with development-only comparator selection.

This runner does not replace the operational gate. It records baseline fits,
held-out case scores and strata in a separate, versioned research run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np

from .dataset import join_cases
from .distributions import Distribution, raw_distribution
from .experiments import paired_block_bootstrap, temporal_block_ids
from .ingest import audit_records
from .models import AdditiveGate, SpatialGate
from .schemas import ForecastRecord, ObservationRecord, load_jsonl, utc
from .training import evaluate, prepare_variable, save_json, weights_for, file_sha256


def case_identity(case):
    """Include issuance and exact verification window, not just calendar day."""
    obs = case.observation
    return {"location_id": obs.location_id, "latitude": obs.latitude,
            "longitude": obs.longitude, "variable": case.variable,
            "valid_start": obs.valid_start.isoformat(), "valid_end": obs.valid_end.isoformat(),
            "issuance_time": case.issuance_time.isoformat(), "observation_id": obs.observation_id}


def case_id(case):
    value = json.dumps(case_identity(case), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def strata_for(case):
    obs = case.observation
    month = obs.valid_start.month
    season = ("winter" if month <= 2 else "pre_monsoon" if month <= 5 else
              "southwest_monsoon" if month <= 9 else "post_monsoon")
    leads = [(obs.valid_start - forecast.initialization_time).total_seconds() / 3600
             for forecast in case.forecasts.values()]
    # Explicit ranges retain asynchronous source initializations rather than
    # pretending every member had the same lead.
    lead_label = f"{min(leads):g}" if min(leads) == max(leads) else f"{min(leads):g}..{max(leads):g}"
    return {"region": obs.region, "location": obs.location_id, "season": season,
            "lead_hours": lead_label,
            "duration_hours": f"{(obs.valid_end - obs.valid_start).total_seconds() / 3600:g}"}


def stratified_scores(cases, scores_by_method):
    """Descriptive scores only; small strata do not establish significance."""
    groups = {}
    for index, case in enumerate(cases):
        for dimension, value in strata_for(case).items():
            groups.setdefault((dimension, value), []).append(index)
    return [{"dimension": dimension, "value": value, "n_cases": len(indices),
             "n_issuances": len({cases[i].issuance_time for i in indices}),
             "methods": {name: float(np.asarray(scores)[indices].mean())
                         for name, scores in scores_by_method.items()}}
            for (dimension, value), indices in sorted(groups.items())]


def _single_distributions(model, cases):
    return [[distribution] for distribution in model.predict(cases)]


def _raw_mean_distribution(forecast):
    values = np.asarray(forecast.value, dtype=float)
    if forecast.variable == "wind_uv":
        return Distribution("empirical_wind", {"members": [values.mean(axis=0).tolist()]})
    return Distribution("point", {"mean": float(values.mean())})


def run_benchmark(directory, *, include_xgboost=False, variables=None, seed=42, n_resamples=500):
    """Fit baselines on training data and verify on one locked common cohort.

    BMA sees forward cross-fitted training distributions. EMOS and the optional
    tree stack see raw training inputs. Neither sees test outcomes during fitting
    or comparator selection. Existing deployed gate artifacts are never changed.
    """
    from .baselines import BMABlend, EMOSRegressor, XGBoostStack

    directory = Path(directory)
    artifact = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    selected_variables = list(variables or artifact["variables"])
    if not selected_variables or any(v not in artifact["variables"] for v in selected_variables):
        raise ValueError("Every requested variable must exist in the trained workspace")
    if include_xgboost:
        # Fail explicitly before creating a partial research run if unavailable.
        import xgboost  # noqa: F401
    forecasts = load_jsonl(directory / "forecasts.jsonl", ForecastRecord)
    observations = load_jsonl(directory / "observations.jsonl", ObservationRecord)
    audit = audit_records(forecasts, observations)
    if not audit["valid"]:
        raise ValueError("Benchmark input archive failed its audit")
    if {r.data_kind for r in forecasts + observations} != {artifact["data_kind"]}:
        raise ValueError("Benchmark inputs and trained model provenance disagree")
    if set(artifact.get("input_sha256", {})) != {"forecasts.jsonl", "observations.jsonl"}:
        raise ValueError("Workspace lacks complete training input fingerprints; retrain into a new version before benchmarking")
    for name, expected in artifact["input_sha256"].items():
        if name not in ("forecasts.jsonl", "observations.jsonl"):
            raise ValueError("Unexpected input fingerprint in model artifact")
        if file_sha256(directory / name) != expected:
            raise ValueError("Input archive changed after training; create a new version before benchmarking")
    cases = join_cases(forecasts, observations, mode=artifact["evaluation_mode"])
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / "benchmark_runs" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    dependencies = {}
    for package in ("numpy", "scipy", "xgboost", "torch"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    report = {"schema_version": "1.0", "run_id": run_id, "status": "running",
              "data_kind": artifact["data_kind"], "split": "held_out_test", "seed": seed,
              "train_end": artifact["train_end"], "dev_end": artifact["dev_end"],
              "evaluation_mode": artifact["evaluation_mode"], "variables": {},
              "training_input_fingerprints_verified": bool(artifact.get("input_sha256")),
              "files_sha256": {name: file_sha256(directory / name)
                               for name in ("models.json", "forecasts.jsonl", "observations.jsonl")},
              "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                              ("benchmark.py", "baselines.py", "distributions.py", "calibration.py",
                               "dataset.py", "models.py", "training.py", "experiments.py", "schemas.py")},
              "dependencies": dependencies,
              "protocol": {
                  "comparison_support": "identical_complete_source_development_and_test_cases",
                  "baseline_training": "purged_training_only; BMA uses forward_cross_fitted_components",
                  "comparator_selection": "minimum_development_proper_score_among_prespecified_baselines",
                  "test_usage": "final_evaluation_only; no_gate_or_hyperparameter_promotion",
                  "uncertainty": "paired_temporal_block_percentile_intervals; descriptive_multiple_comparisons",
                  "strata": "descriptive_region_location_season_exact_lead_and_duration"},
              "limitations": [
                  "Synthetic results validate implementation and do not establish weather forecast skill.",
                  "All comparisons condition on complete source support; outage results are separate.",
                  "Baseline adaptations are described in ppt.md; this is not a reproduction claim.",
                  "Pointwise confidence intervals are not a multiplicity-adjusted superiority test.",
                  "No benchmark result automatically changes the deployed accuracy champion."]}
    try:
        for variable in selected_variables:
            print(f"Benchmark {variable}: fitting training-only baselines", flush=True)
            specification = artifact["variables"][variable]
            sources = specification["sources"]
            prep = prepare_variable(cases, sources, variable, utc(artifact["train_end"]), utc(artifact["dev_end"]))
            # Detect edited input archives that would silently invalidate the
            # saved gate's source calibration. Exact deterministic JSON params
            # are intentionally compared before any test result is reported.
            if prep["bank"].to_dict() != specification["bank"]:
                raise ValueError("Source calibration differs from the saved workspace; train a new version first")
            dc, dd = zip(*prep["dev"])
            tc, td = zip(*prep["test"])
            training = [c for c in prep["train"] if all(s in c.forecasts for s in sources)]
            emos = EMOSRegressor(variable, sources).fit(training)
            bma = BMABlend(sources).fit(prep["oof_distributions"],
                                       [c.observation.value for c in prep["oof_cases"]])
            save_json(destination / f"{variable}_emos.json", emos.to_dict())
            save_json(destination / f"{variable}_bma.json", bma.to_dict())
            predictors = {
                "emos_adapted": lambda rows, ds: (_single_distributions(emos, rows), np.ones((len(rows), 1))),
                "bma_fixed_components": lambda rows, ds: (ds, bma.predict_weights(ds)),
                "calibrated_equal": lambda rows, ds: (ds, weights_for("calibrated_equal", rows, ds, sources)),
                "inverse_skill": lambda rows, ds: (ds, weights_for("inverse_skill", rows, ds, sources)),
            }
            if include_xgboost:
                tree = XGBoostStack(variable, sources, seed=seed).fit(training)
                tree.save(destination / f"{variable}_xgboost.json")
                predictors["xgboost_distributional_stack"] = lambda rows, ds: (
                    _single_distributions(tree, rows), np.ones((len(rows), 1)))
            for name, key in (("static", "static"), ("interpretable", "additive")):
                gate = AdditiveGate.from_dict(specification[key])
                predictors[name] = lambda rows, ds, gate=gate: (ds, weights_for("gate", rows, ds, sources, gate))
            if specification["spatial_available"]:
                gate = SpatialGate.load(directory / f"{variable}_spatial.pt", sources,
                                        AdditiveGate.from_dict(specification["additive"]).features)
                report["files_sha256"][f"{variable}_spatial.pt"] = file_sha256(directory / f"{variable}_spatial.pt")
                predictors["spatial"] = lambda rows, ds, gate=gate: (ds, weights_for("gate", rows, ds, sources, gate))
            for index, source in enumerate(sources):
                def calibrated(rows, ds, index=index):
                    weights = np.zeros((len(rows), len(sources)))
                    weights[:, index] = 1
                    return ds, weights
                predictors[f"calibrated:{source}"] = calibrated
                predictors[f"raw_members:{source}"] = lambda rows, ds, source=source: (
                    [[raw_distribution(c.forecasts[source])] for c in rows], np.ones((len(rows), 1)))
                predictors[f"raw_mean:{source}"] = lambda rows, ds, source=source: (
                    [[_raw_mean_distribution(c.forecasts[source])] for c in rows], np.ones((len(rows), 1)))
            predictors["raw_equal_mixture"] = lambda rows, ds: (
                [[raw_distribution(c.forecasts[s]) for s in sources] for c in rows],
                np.full((len(rows), len(sources)), 1 / len(sources)))

            # Evaluate development first and freeze the comparator before test.
            development = {name: evaluate(dc, *predict(dc, dd))[0] for name, predict in predictors.items()}
            competitors = [name for name in predictors if name not in {"interpretable", "spatial"}]
            reference = min(competitors, key=lambda name: development[name]["proper_score"])
            methods, case_scores = {}, {}
            for name, predict in predictors.items():
                methods[name], values = evaluate(tc, *predict(tc, td))
                case_scores[name] = values.tolist()
            block_days = max(14, int(np.ceil(max(
                (c.observation.valid_end - c.issuance_time).total_seconds() / 86400 for c in tc))))
            blocks = temporal_block_ids([c.observation.valid_end for c in tc], block_days=block_days)
            comparisons = {name: paired_block_bootstrap(
                case_scores[name], case_scores[reference], blocks, n_resamples=n_resamples, seed=seed)
                for name in ("interpretable", "spatial") if name in case_scores}
            case_rows = [{**case_identity(c), "case_id": case_id(c), "block": block,
                          "observation": c.observation.value,
                          "proper_scores": {name: values[i] for name, values in case_scores.items()}}
                         for i, (c, block) in enumerate(zip(tc, blocks))]
            save_json(destination / f"{variable}_case_scores.json", case_rows)
            report["variables"][variable] = {
                "sources": sources, "split_counts": prep["split_counts"],
                "baseline_training_cases": len(training), "methods": methods,
                "development_methods": development, "development_selected_baseline": reference,
                "paired_comparisons_vs_selected_baseline": comparisons, "bootstrap_block_days": block_days,
                "stratified_proper_scores": stratified_scores(tc, case_scores),
                "case_scores_path": f"{variable}_case_scores.json",
                "cohort_sha256": hashlib.sha256("\n".join(case_id(c) for c in tc).encode()).hexdigest(),
            }
            print(f"Benchmark {variable}: {len(methods)} methods, {len(tc)} common test cases", flush=True)
        report["status"] = "completed"
        save_json(destination / "report.json", report)
        save_json(directory / "benchmark.json", {**report, "run_path": str(destination.relative_to(directory))})
    except Exception as exc:
        report["status"] = "failed"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        save_json(destination / "report.json", report)
        raise
    return report
