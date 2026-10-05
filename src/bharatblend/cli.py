"""Reproducible CLI: demo, audit, train, forecast, study and provider discovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import numpy as np

from .schemas import ForecastRecord, ObservationRecord, load_jsonl, utc, write_jsonl
from .training import save_json, train_workspace, prepare_variable, matrices, evaluate


def run_study(directory, variables=None, seed=42):
    from .dataset import Case, join_cases
    from .models import AdditiveGate
    from .experiments import nonempty_subsets
    directory = Path(directory)
    artifact = json.loads((directory / "models.json").read_text())
    cases = join_cases(load_jsonl(directory / "forecasts.jsonl", ForecastRecord),
                       load_jsonl(directory / "observations.jsonl", ObservationRecord), mode=artifact["evaluation_mode"])
    results = []
    for variable in variables or artifact["variables"]:
        sources = artifact["variables"][variable]["sources"]
        prep = prepare_variable(cases, sources, variable, utc(artifact["train_end"]), utc(artifact["dev_end"]))
        q, b = matrices(prep["oof_distributions"], prep["oof_cases"], prep["scale"], artifact["variables"][variable]["tail_weight"])
        # Complete-source support is frozen for every subset; exclusions only change inputs.
        common = np.array([all(d is not None for d in ds) for ds in prep["oof_distributions"]])
        def subset_case(c, selected):
            return Case(c.observation, {s: f for s, f in c.forecasts.items() if s in selected}, c.issuance_time,
                        {s: v for s, v in c.recent_skill.items() if s in selected})
        for subset in nonempty_subsets(sources):
            indices = [sources.index(s) for s in subset]
            tc = [subset_case(c, subset) for c, keep in zip(prep["oof_cases"], common) if keep]
            subq = q[common][:, indices][:, :, indices]
            subb = b[common][:, indices]
            gate = AdditiveGate(subset, artifact["variables"][variable]["additive"]["regularization"])
            gate.fit(tc, subq, subb, np.ones((len(tc), len(subset)), dtype=bool), iterations=60)
            tests = [subset_case(c, subset) for c, _ in prep["test"]]
            ds = [[dist[i] for i in indices] for _, dist in prep["test"]]
            weights, _ = gate.predict(tests, np.ones((len(tests), len(subset)), dtype=bool))
            metrics, _ = evaluate(tests, ds, weights)
            results.append({"variable": variable, "sources": list(subset), "status": "completed", "data_kind": artifact["data_kind"],
                            "method": "retrained_additive_fixed_development_regularization", "seed": seed, "metrics": metrics})
            print(f"Subset {variable}: {','.join(subset)} score={metrics['proper_score']:.4f}", flush=True)
    manifest = json.loads((directory / "experiments.json").read_text())
    old = manifest.get("results", [])
    replaced = {r["variable"] for r in results}
    manifest["results"] = [r for r in old if r.get("variable") not in replaced] + results
    manifest["executed_subset_count"] = len(manifest["results"])
    manifest["execution_note"] = "Completed subset results are listed separately; other planned studies remain pending. Do not select a production model on these test results."
    save_json(directory / "experiments.json", manifest)
    return manifest


def run_ablations(directory, variables=None):
    from .dataset import Case, join_cases
    from .models import AdditiveGate
    directory = Path(directory)
    artifact = json.loads((directory / "models.json").read_text())
    cases = join_cases(load_jsonl(directory / "forecasts.jsonl", ForecastRecord),
                       load_jsonl(directory / "observations.jsonl", ObservationRecord), mode=artifact["evaluation_mode"])
    results = []
    for variable in variables or artifact["variables"]:
        specification = artifact["variables"][variable]
        sources = specification["sources"]
        prepared = prepare_variable(cases, sources, variable, utc(artifact["train_end"]), utc(artifact["dev_end"]))
        train = prepared["oof_cases"]
        distributions = prepared["oof_distributions"]
        mask = np.array([[d is not None for d in ds] for ds in distributions])
        tc, td = zip(*prepared["test"])
        test_mask = np.ones((len(tc), len(sources)), dtype=bool)
        q, b = matrices(distributions, train, prepared["scale"], specification["tail_weight"])
        configurations = [(f"without_{group}", [group], specification["tail_weight"])
                          for group in ("lead", "season", "geography", "regime", "recent_skill", "disagreement", "age", "interaction")]
        if variable == "rainfall":
            configurations += [("without_tail_objective", [], 0.0),
                               ("without_regime_and_tail", ["regime"], 0.0)]
        for name, removed, tail in configurations:
            aq, ab = (q, b) if tail == specification["tail_weight"] else matrices(distributions, train, prepared["scale"], tail)
            gate = AdditiveGate(sources, specification["additive"]["regularization"], removed_groups=removed)
            gate.fit(train, aq, ab, mask, iterations=90)
            weights, _ = gate.predict(tc, test_mask)
            metrics, _ = evaluate(tc, td, weights)
            results.append({"variable": variable, "experiment": name, "status": "completed", "data_kind": artifact["data_kind"],
                            "retrained": True, "removed_groups": removed, "tail_weight": tail, "metrics": metrics})
            print(f"Ablation {variable}: {name}", flush=True)
        base_gate = AdditiveGate.from_dict(specification["additive"])
        for removed_source in sources:
            altered = [Case(c.observation, {s: f for s, f in c.forecasts.items() if s != removed_source}, c.issuance_time,
                            {s: value for s, value in c.recent_skill.items() if s != removed_source}) for c in tc]
            ds = [[None if s == removed_source else d for s, d in zip(sources, row)] for row in td]
            availability = np.array([[d is not None for d in row] for row in ds])
            if not availability.any(axis=1).all():
                continue
            weights, _ = base_gate.predict(altered, availability)
            metrics, _ = evaluate(altered, ds, weights)
            results.append({"variable": variable, "experiment": f"outage_{removed_source}", "status": "completed", "data_kind": artifact["data_kind"],
                            "retrained": False, "context_recomputed": True, "metrics": metrics})
    manifest = json.loads((directory / "experiments.json").read_text())
    manifest["ablation_results"] = results
    manifest["executed_ablation_count"] = len(results)
    save_json(directory / "experiments.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bharatblend", description="Auditable probabilistic forecast blending")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generate explicitly synthetic fixtures and train both tracks")
    demo.add_argument("--output", default="artifacts/demo")
    demo.add_argument("--cycles", type=int, default=96)
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--epochs", type=int, default=40)
    demo.add_argument("--no-accuracy", action="store_true")
    train = commands.add_parser("train", help="Train on supplied canonical historical forecast/observation files")
    for flag in ("forecasts", "observations", "output", "train-end", "dev-end"):
        train.add_argument(f"--{flag}", required=True)
    train.add_argument("--mode", choices=["hindcast", "operational"], default="hindcast")
    train.add_argument("--epochs", type=int, default=40)
    train.add_argument("--no-accuracy", action="store_true")
    audit = commands.add_parser("audit", help="Validate canonical archive metadata and capabilities")
    audit.add_argument("--forecasts", required=True)
    audit.add_argument("--observations")
    audit.add_argument("--registry", default="configs/model_registry.json")
    audit.add_argument("--output")
    infer = commands.add_parser("forecast", help="Issue one forecast and persist its evidence receipt")
    infer.add_argument("--workspace", default="artifacts/demo")
    infer.add_argument("--request", required=True, help="Path to a JSON request")
    infer.add_argument("--output")
    study = commands.add_parser("study", help="Actually retrain and evaluate every source subset")
    study.add_argument("--workspace", default="artifacts/demo")
    study.add_argument("--variables", nargs="+", choices=["rainfall", "temperature", "wind_uv"])
    ablate = commands.add_parser("ablate", help="Retrain feature/tail ablations and replay source outages")
    ablate.add_argument("--workspace", default="artifacts/demo")
    ablate.add_argument("--variables", nargs="+", choices=["rainfall", "temperature", "wind_uv"])
    benchmark = commands.add_parser("benchmark", help="Fit EMOS/BMA/tree competitors and compare on a locked common cohort")
    benchmark.add_argument("--workspace", default="artifacts/demo")
    benchmark.add_argument("--variables", nargs="+", choices=["rainfall", "temperature", "wind_uv"])
    benchmark.add_argument("--with-xgboost", action="store_true", help="Require and fit the optional XGBoost stack")
    benchmark.add_argument("--seed", type=int, default=42)
    catalog = commands.add_parser("catalog", help="Read-only listing of NOAA AIWP archive keys")
    catalog.add_argument("--prefix", default="")
    catalog.add_argument("--limit", type=int, default=10)
    probe = commands.add_parser("public-probe", help="Save bounded NOAA catalogue/README evidence without downloading weather objects")
    probe.add_argument("--output", default="artifacts/public_probe")
    probe.add_argument("--prefix", action="append")
    probe.add_argument("--max-keys", type=int, default=2)
    probe.add_argument("--byte-budget", type=int, default=10_000_000)
    download = commands.add_parser("download", help="Download one explicit NOAA archive object within a byte budget")
    download.add_argument("--key", required=True)
    download.add_argument("--output", default="artifacts/public_probe")
    download.add_argument("--byte-budget", type=int, default=10_000_000)
    download.add_argument("--expected-size", type=int)
    download.add_argument("--expected-sha256")
    convert = commands.add_parser("canonicalize", help="Validate CSV/JSONL and write canonical JSONL")
    convert.add_argument("--input", required=True)
    convert.add_argument("--kind", choices=["forecast", "observation"], required=True)
    convert.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "demo":
        from .demo import generate_demo
        if args.cycles < 40:
            parser.error("Use at least 40 synthetic cycles for purged forward splits")
        output = Path(args.output)
        manifest = generate_demo(output, args.cycles, args.seed)
        save_json(output / "demo_manifest.json", manifest)
        train_workspace(output / "forecasts.jsonl", output / "observations.jsonl", output,
                        manifest["train_end"], manifest["dev_end"], mode="operational", accuracy=not args.no_accuracy,
                        epochs=args.epochs, seed=args.seed)
        print(f"Synthetic workspace ready: {output.resolve()}")
    elif args.command == "train":
        train_workspace(args.forecasts, args.observations, args.output, args.train_end, args.dev_end,
                        mode=args.mode, accuracy=not args.no_accuracy, epochs=args.epochs)
    elif args.command == "audit":
        from .ingest import audit_manifest
        result = audit_manifest(args.forecasts, args.observations, args.registry)
        if args.output:
            save_json(args.output, result)
        else:
            print(json.dumps(result, indent=2, default=str))
    elif args.command == "forecast":
        from .service import BlendService
        result = BlendService(args.workspace).predict(json.loads(Path(args.request).read_text(encoding="utf-8")))
        if args.output:
            save_json(args.output, result)
        else:
            print(json.dumps(result, indent=2))
    elif args.command == "study":
        run_study(args.workspace, args.variables)
    elif args.command == "ablate":
        run_ablations(args.workspace, args.variables)
    elif args.command == "benchmark":
        from .benchmark import run_benchmark
        run_benchmark(args.workspace, variables=args.variables, include_xgboost=args.with_xgboost, seed=args.seed)
    elif args.command == "catalog":
        from .ingest import NOAAAIWPCatalog
        print(json.dumps(NOAAAIWPCatalog().list_objects(prefix=args.prefix, max_keys=args.limit), indent=2))
    elif args.command == "public-probe":
        from .public_data import probe_noaa_archive, DEFAULT_PREFIXES
        result = probe_noaa_archive(args.output, prefixes=args.prefix or DEFAULT_PREFIXES,
                                    max_keys=args.max_keys, byte_budget=args.byte_budget)
        print(json.dumps({k: result[k] for k in ("listed_object_count", "bytes_transferred", "ready_for_training")}, indent=2))
    elif args.command == "download":
        from .public_data import download_noaa_object
        result = download_noaa_object(args.key, args.output, byte_budget=args.byte_budget,
                                      expected_size=args.expected_size, expected_sha256=args.expected_sha256)
        print(json.dumps(result, indent=2))
    else:
        from .ingest import read_records
        write_jsonl(args.output, read_records(args.input, args.kind))


if __name__ == "__main__":
    main()
