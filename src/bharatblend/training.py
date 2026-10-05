"""Chronological cross-fitting, training, matched verification and study execution."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import numpy as np

from .calibration import CalibratorBank, raw_mean
from .dataset import join_cases, split_cases
from .distributions import Distribution, score_terms, mixture_score, exceedance, raw_distribution
from .experiments import generate_experiment_manifest, paired_block_bootstrap, temporal_block_ids
from .models import AdditiveGate, SpatialGate
from .schemas import ForecastRecord, ObservationRecord, load_jsonl, utc
from .ingest import audit_records


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def prepare_variable(cases, sources, variable, train_end, dev_end):
    cases = [c for c in cases if c.variable == variable]
    train, dev, test = split_cases(cases, train_end, dev_end)
    if min(len(train), len(dev), len(test)) < 12:
        raise ValueError(f"{variable}: need at least 12 cases in each purged split; got {len(train)}/{len(dev)}/{len(test)}")
    times = sorted({c.issuance_time for c in train})
    if len(times) < 8:
        raise ValueError(f"{variable}: at least 8 training issuance times are required for forward cross-fitting")
    boundaries = [times[len(times) // 2], times[3 * len(times) // 4], train_end]
    oof_cases, oof_distributions = [], []
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        past = [c for c in train if c.observation.available_at < left and c.observation.valid_end < left]
        future = [c for c in train if left <= c.issuance_time < right]
        bank = CalibratorBank(variable, sources).fit(past)
        for case in future:
            ds = bank.predict(case)
            if any(d is not None for d in ds):
                oof_cases.append(case)
                oof_distributions.append(ds)
    if len(oof_cases) < 12:
        raise ValueError(f"{variable}: insufficient forward out-of-fold calibration support")
    bank = CalibratorBank(variable, sources).fit(train)
    # A fixed-source benchmark uses complete support, unlike operational missingness replay.
    sources = [s for s in sources if s in bank.parameters]
    if len(sources) != len(bank.sources):
        raise ValueError("Insufficient calibration support for one or more requested sources")
    def cohort(rows):
        pairs = [(c, bank.predict(c)) for c in rows]
        return [(c, ds) for c, ds in pairs if all(d is not None for d in ds)]
    dev_pairs, test_pairs = cohort(dev), cohort(test)
    if not dev_pairs or not test_pairs:
        raise ValueError("No comparable held-out cases after source/version/lead eligibility checks")
    yy = np.asarray([c.observation.value for c in train])
    scale = max(float(np.std(yy)), 1)
    return {"bank": bank, "oof_cases": oof_cases, "oof_distributions": oof_distributions,
            "train": train, "dev": dev_pairs, "test": test_pairs, "scale": scale, "n_train": len(train),
            "split_counts": {"training": len(train), "cross_fitted": len(oof_cases), "development": len(dev_pairs),
                             "test": len(test_pairs), "test_excluded": len(test) - len(test_pairs)}}


def matrices(distributions, cases, scale=1, tail_weight=0):
    terms = [score_terms(ds, c.observation.value, 64.5 if c.variable == "rainfall" else None,
                         tail_weight if c.variable == "rainfall" else 0) for c, ds in zip(cases, distributions)]
    return np.stack([t[0] for t in terms]) / scale, np.stack([t[1] for t in terms]) / scale,


def weights_for(method, cases, distributions, sources, gate=None):
    mask = np.array([[d is not None for d in ds] for ds in distributions])
    if gate is not None:
        return gate.predict(cases, mask)[0]
    if method == "inverse_skill":
        skill = np.array([[1 / max(c.recent_skill.get(s, 1), 0.05) for s in sources] for c in cases]) * mask
        return skill / skill.sum(axis=1, keepdims=True)
    return mask / mask.sum(axis=1, keepdims=True)


def evaluate(cases, distributions, weights):
    values = np.asarray([mixture_score(ds, w, c.observation.value) for c, ds, w in zip(cases, distributions, weights)])
    means = [sum(wi * np.asarray(d.mean()) for d, wi in zip(ds, w) if d is not None)
             for ds, w in zip(distributions, weights)]
    y = np.asarray([c.observation.value for c in cases])
    errors = np.asarray(means) - y
    result = {"proper_score": float(values.mean()), "score_name": "energy_plus_component_crps" if cases[0].variable == "wind_uv" else "crps",
              "mae": float(np.abs(errors).mean()), "rmse": float(np.sqrt(np.mean(errors ** 2))),
              "bias": float(errors.mean()), "n_cases": len(cases)}
    if cases[0].variable == "rainfall":
        result["tail_crps_64.5"] = float(np.mean([mixture_score(ds, w, c.observation.value, 64.5)
                                                 for c, ds, w in zip(cases, distributions, weights)]))
        for threshold in (64.5, 115.6, 204.5):
            probabilities = np.array([sum(wi * exceedance(d, threshold) for d, wi in zip(ds, w) if d)
                                      for ds, w in zip(distributions, weights)])
            result[f"brier_{threshold}"] = float(np.mean((probabilities - (y > threshold)) ** 2))
    return result, values


def fit_gates(prepared, sources, *, accuracy=True, epochs=40, seed=42, tail_weight=0.5, removed_groups=(), iterations=90):
    cases, ds = prepared["oof_cases"], prepared["oof_distributions"]
    mask = np.array([[d is not None for d in row] for row in ds])
    q, b = matrices(ds, cases, prepared["scale"], tail_weight)
    # Regularization is selected on development only, never on the test observations.
    dc, dd = zip(*prepared["dev"])
    dmask = np.ones((len(dc), len(sources)), dtype=bool)
    dq, db = matrices(dd, dc, prepared["scale"], tail_weight)
    best, best_loss = None, float("inf")
    for regularization in (0.001, 0.01, 0.1):
        gate = AdditiveGate(sources, regularization, removed_groups).fit(cases, q, b, mask, iterations)
        w, _ = gate.predict(dc, dmask)
        loss = float(np.mean(np.einsum("ni,nij,nj->n", w, dq, w) - 2 * (db * w).sum(axis=1)))
        if loss < best_loss:
            best, best_loss = gate, loss
    static = AdditiveGate(sources, static=True).fit(cases, q, b, mask, iterations)
    spatial = SpatialGate(sources, best.features).fit(cases, q, b, mask, epochs, seed) if accuracy else None
    return best, static, spatial


def train_workspace(forecast_path, observation_path, output, train_end, dev_end, *, mode="hindcast", accuracy=True, epochs=40, seed=42):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    forecasts = load_jsonl(forecast_path, ForecastRecord)
    observations = load_jsonl(observation_path, ObservationRecord)
    registry = next((p for p in (Path("configs/model_registry.json"), Path(__file__).parent / "configs/model_registry.json",
                                 Path(__file__).resolve().parents[2] / "configs/model_registry.json") if p.exists()), None)
    report = audit_records(forecasts, observations, json.loads(registry.read_text(encoding="utf-8")) if registry else None)
    save_json(output / "input_audit.json", report)
    if not report["valid"]:
        raise ValueError(f"Archive audit failed; inspect {output / 'input_audit.json'} before training")
    kinds = {r.data_kind for r in forecasts + observations}
    if len(kinds) != 1:
        raise ValueError("A workspace cannot combine synthetic and real data")
    data_kind = kinds.pop()
    cases = join_cases(forecasts, observations, mode=mode)
    variables = sorted({c.variable for c in cases})
    artifact = {"schema_version": "1.0", "data_kind": data_kind, "evaluation_mode": mode,
                "train_end": train_end, "dev_end": dev_end, "seed": seed, "variables": {},
                "input_sha256": {"forecasts.jsonl": file_sha256(forecast_path),
                                 "observations.jsonl": file_sha256(observation_path)},
                "limitations": ["Results apply only to the supplied matched archive.",
                                "Scalar probabilities are not coherent multi-day heatwave or flood forecasts.",
                                "No claim of state-of-the-art skill without an independently audited real benchmark."]}
    metrics = {"data_kind": data_kind, "split": "held_out_test", "variables": {}, "limitations": artifact["limitations"]}
    prepared_all = {}
    for variable in variables:
        sources = sorted({s for c in cases if c.variable == variable for s in c.forecasts})
        print(f"Training {variable}: {len(sources)} sources", flush=True)
        prepared = prepare_variable(cases, sources, variable, utc(train_end), utc(dev_end))
        prepared_all[variable] = prepared
        additive, static, spatial = fit_gates(prepared, sources, accuracy=accuracy, epochs=epochs, seed=seed)
        if spatial:
            spatial.save(output / f"{variable}_spatial.pt")
        # Accuracy champion selected using the development score; it may be the additive model.
        dc, dd = zip(*prepared["dev"])
        ac_w = weights_for("additive", dc, dd, sources, additive)
        ac_score = evaluate(dc, dd, ac_w)[0]["proper_score"]
        champion = "additive"
        if spatial and evaluate(dc, dd, weights_for("spatial", dc, dd, sources, spatial))[0]["proper_score"] < ac_score:
            champion = "spatial"
        artifact["variables"][variable] = {"sources": sources, "bank": prepared["bank"].to_dict(),
            "additive": additive.to_dict(), "static": static.to_dict(), "spatial_available": spatial is not None,
            "accuracy_champion": champion, "split_counts": prepared["split_counts"],
            "tail_weight": 0.5 if variable == "rainfall" else 0, "scale": prepared["scale"],
            "accuracy_architecture": "small_spatial_attention_challenger" if spatial else None}
        tc, td = zip(*prepared["test"])
        methods, per_case = {}, {}
        for name, gate in (("calibrated_equal", None), ("inverse_skill", None), ("static", static), ("interpretable", additive), ("spatial", spatial)):
            if name == "spatial" and spatial is None:
                continue
            w = weights_for(name, tc, td, sources, gate)
            methods[name], per_case[name] = evaluate(tc, td, w)
        for index, source in enumerate(sources):
            w = np.zeros((len(tc), len(sources)))
            w[:, index] = 1
            methods[f"calibrated:{source}"], _ = evaluate(tc, td, w)
            # Score the mean and the unchanged empirical member distribution
            # separately. A Gaussian fit is not the raw wind ensemble.
            raw_ds = []
            member_ds = []
            for c in tc:
                f = c.forecasts[source]
                member_ds.append([raw_distribution(f)])
                if variable == "wind_uv":
                    members = np.asarray(f.value)
                    raw_ds.append([Distribution("empirical_wind", {"members": [members.mean(axis=0).tolist()]})])
                else:
                    raw_ds.append([Distribution("point", {"mean": float(raw_mean(f))})])
            methods[f"raw_mean:{source}"], _ = evaluate(tc, raw_ds, np.ones((len(tc), 1)))
            methods[f"raw_members:{source}"], _ = evaluate(tc, member_ds, np.ones((len(tc), 1)))
        chosen_name = "spatial" if champion == "spatial" else "interpretable"
        methods["accuracy"] = methods[chosen_name].copy()
        block_days = max(14, int(np.ceil(max((c.observation.valid_end - c.issuance_time).total_seconds() / 86400 for c in tc))))
        blocks = temporal_block_ids([c.observation.valid_end.isoformat() for c in tc], block_days=block_days)
        comparison = paired_block_bootstrap(per_case["interpretable"].tolist(), per_case["calibrated_equal"].tolist(), blocks, n_resamples=500, seed=seed)
        reliability = []
        if variable == "rainfall":
            weights = weights_for("additive", tc, td, sources, additive)
            for threshold in (64.5, 115.6, 204.5):
                probs = np.array([sum(wi * exceedance(d, threshold) for d, wi in zip(ds, w)) for ds, w in zip(td, weights)])
                y = np.array([c.observation.value > threshold for c in tc])
                bins = []
                for k in range(10):
                    selected = (probs >= k / 10) & (probs < (k + 1) / 10 if k < 9 else probs <= 1)
                    if selected.any():
                        bins.append({"forecast": float(probs[selected].mean()), "observed": float(y[selected].mean()), "count": int(selected.sum())})
                reliability.append({"threshold": threshold, "bins": bins})
        metrics["variables"][variable] = {"methods": methods, "n_cases": len(tc), "reliability": reliability,
                                          "paired_bootstrap_vs_equal": comparison, "accuracy_champion": champion}
    save_json(output / "models.json", artifact)
    save_json(output / "metrics.json", metrics)
    for path, name in ((forecast_path, "forecasts.jsonl"), (observation_path, "observations.jsonl")):
        if Path(path).resolve() != (output / name).resolve():
            shutil.copyfile(path, output / name)
    if registry is not None and registry.exists():
        shutil.copyfile(registry, output / "model_registry.json")
    # Use actual split dates in the manifest; never attach a real-data cohort label to demo scores.
    first = min(c.issuance_time for c in cases).isoformat()
    from datetime import timedelta
    last = (max(c.observation.valid_end for c in cases) + timedelta(days=1)).isoformat()
    splits = {"train": {"start": first, "end_exclusive": train_end},
              "development": {"start": train_end, "end_exclusive": dev_end},
              "test": {"start": dev_end, "end_exclusive": last}}
    manifest = generate_experiment_manifest(sources_by_variable={v: artifact["variables"][v]["sources"] for v in variables}, demo=data_kind == "synthetic", splits=splits)
    save_json(output / "experiments.json", manifest)
    return artifact, prepared_all
