"""Load trained artifacts and issue auditable forecasts without retraining."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np

from .calibration import CalibratorBank, raw_mean
from .dataset import Case, join_cases
from .distributions import mixture_summary, exceedance
from .models import AdditiveGate, SpatialGate, raw_features
from .schemas import ForecastRecord, ObservationRecord, load_jsonl, utc, UNITS
from .catalogue import catalogue


class BlendService:
    def __init__(self, data_dir="artifacts/demo"):
        self.data_dir = Path(data_dir)
        self.artifact = json.loads((self.data_dir / "models.json").read_text(encoding="utf-8"))
        self.forecasts = load_jsonl(self.data_dir / "forecasts.jsonl", ForecastRecord)
        self.observations = load_jsonl(self.data_dir / "observations.jsonl", ObservationRecord)
        if any(r.data_kind != self.artifact["data_kind"] for r in self.forecasts + self.observations):
            raise ValueError("Forecast/observation provenance differs from the trained workspace")
        self.archive_cases = join_cases(self.forecasts, self.observations, mode=self.artifact["evaluation_mode"])
        self.banks, self.gates, self.spatial = {}, {}, {}
        for variable, model in self.artifact["variables"].items():
            self.banks[variable] = CalibratorBank.from_dict(model["bank"])
            self.gates[variable] = AdditiveGate.from_dict(model["additive"])
        self.index = {}
        for forecast in self.forecasts:
            self.index.setdefault(forecast.target_key(), []).append(forecast)
        self.case_index = {(c.observation.target_key(), c.issuance_time): c for c in self.archive_cases}
        self.model_digest = hashlib.sha256((self.data_dir / "models.json").read_bytes()).hexdigest()

    def status(self):
        return {"status": "ready", "data_kind": self.artifact["data_kind"], "version": "0.1.0",
                "evaluation_mode": self.artifact["evaluation_mode"], "variables": list(self.banks),
                "source_ids": sorted({f.source_id for f in self.forecasts}),
                "forecast_records": len(self.forecasts), "model_digest": self.model_digest,
                "accuracy_champions": {v: a["accuracy_champion"] for v, a in self.artifact["variables"].items()},
                "limitations": self.artifact["limitations"]}

    def registry(self):
        path = self.data_dir / "model_registry.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": []}

    def metrics(self):
        return json.loads((self.data_dir / "metrics.json").read_text(encoding="utf-8"))

    def experiments(self):
        return json.loads((self.data_dir / "experiments.json").read_text(encoding="utf-8"))

    def benchmark(self):
        path = self.data_dir / "benchmark.json"
        if not path.exists():
            return {"status": "not_run", "data_kind": self.artifact["data_kind"], "variables": {}}
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("data_kind") != self.artifact["data_kind"]:
            raise ValueError("Benchmark provenance differs from the loaded workspace")
        if report.get("files_sha256", {}).get("models.json") != self.model_digest:
            raise ValueError("Benchmark belongs to a different model version; rerun it for this workspace")
        return report

    def cases(self):
        result = []
        for c in self.archive_cases:
            if c.issuance_time < utc(self.artifact["dev_end"]):
                continue
            o = c.observation
            result.append({"location_id": o.location_id, "location_name": o.location_id.replace("_", " ").title(),
                           "latitude": o.latitude, "longitude": o.longitude, "variable": o.variable,
                           "valid_start": o.valid_start.isoformat(), "valid_end": o.valid_end.isoformat(),
                           "issuance_time": c.issuance_time.isoformat(), "source_ids": list(c.forecasts)})
        return result

    def _case(self, key, issue, excluded):
        bank = self.banks[key[1]]
        candidates = self.index.get(key, [])
        eligible = {}
        for f in sorted(candidates, key=lambda f: f.initialization_time):
            if f.source_id in excluded or f.source_id not in bank.sources:
                continue
            if f.initialization_time > issue:
                continue
            if f.forecast_kind == "operational" and f.available_at > issue:
                continue
            if f.forecast_kind == "hindcast" and self.artifact["evaluation_mode"] == "operational":
                continue
            if bank.distribution(f.source_id, f) is not None:
                eligible[f.source_id] = f
        if not eligible:
            return None
        previous = self.case_index.get((key, issue))
        if previous:
            obs = previous.observation
            recent = previous.recent_skill.copy()
        else:
            f = next(iter(eligible.values()))
            base = {k: getattr(f, k) for k in ("location_id", "latitude", "longitude", "variable", "units", "valid_start", "valid_end", "region", "elevation_m", "coastal_distance_km", "data_kind")}
            obs = ObservationRecord(**base, observation_id="unverified-target", available_at=f.valid_end,
                                    value=[0, 0] if f.variable == "wind_uv" else 0)
            recent = {}  # Conservative training prior until an observation-arrival replay is available.
        return Case(obs, eligible, issue, recent)

    def eligibility_audit(self, key, issue, excluded, active):
        """Report the actual rejection rules without inferring weather-related reasons."""
        bank = self.banks[key[1]]
        candidates = self.index.get(key, [])
        profiles = {m['id']: m for m in catalogue()['models']}
        rows = []
        for source in sorted(set(profiles) | set(bank.sources)):
            model = profiles.get(source, {})
            reasons = []
            if source in active:
                reasons = ['Exact target, issuance cutoff, calibrated version and supported lead passed.']
                status = 'active'
            else:
                status = 'excluded'
                if source in excluded:
                    reasons.append('Removed explicitly in this request.')
                if model.get('capabilities', {}).get(key[1]) is False:
                    reasons.append('The catalogued product has no native output for this variable.')
                if source not in bank.sources:
                    reasons.append('No trained calibrator for this source and variable in the loaded workspace.')
                matching = [f for f in candidates if f.source_id == source]
                if not matching:
                    reasons.append('No archive record for this location and exact valid interval.')
                if source in bank.sources and source not in excluded:
                    for f in matching:
                        prefix = f'{f.model_version} initialized {f.initialization_time.isoformat()}: '
                        if f.initialization_time > issue:
                            reasons.append(prefix + 'initialization is after the issuance cutoff.')
                        elif f.forecast_kind == 'operational' and f.available_at > issue:
                            reasons.append(prefix + 'published after the issuance cutoff.')
                        elif f.forecast_kind == 'hindcast' and self.artifact['evaluation_mode'] == 'operational':
                            reasons.append(prefix + 'hindcast is not eligible in operational mode.')
                        elif bank.distribution(source, f) is None:
                            parameters = bank.parameters.get(source)
                            if parameters is None:
                                reasons.append(prefix + 'no fitted calibration parameters.')
                            else:
                                if f.model_version not in parameters['versions']:
                                    reasons.append(prefix + 'model version was not calibrated.')
                                duration = (f.valid_end - f.valid_start).total_seconds() / 3600
                                if duration not in parameters.get('durations_hours', [duration]):
                                    reasons.append(prefix + f'{duration:g}-hour accumulation duration was not calibrated.')
                                if f.initialization_source not in parameters.get('initialization_sources', [f.initialization_source]):
                                    reasons.append(prefix + 'initialization provider was not calibrated.')
                                lead = (f.valid_start - f.initialization_time).total_seconds() / 3600
                                if not parameters['lead_range'][0] <= lead <= parameters['lead_range'][1]:
                                    reasons.append(prefix + f'{lead:g}-hour lead is outside the fitted support {parameters["lead_range"]}.')
                if not reasons:
                    reasons.append('No calibrated distribution available for this exact request.')
            rows.append({'source_id': source, 'name': model.get('name', source), 'status': status,
                         'reasons': list(dict.fromkeys(reasons))})
        return rows

    def predict(self, request):
        variable = request.get("variable")
        track = request.get("track", "interpretable")
        if variable not in self.banks:
            raise ValueError("Variable has not been trained in this workspace")
        if track not in ("interpretable", "accuracy"):
            raise ValueError("Unknown forecast track")
        issue, start, end = map(utc, (request["issuance_time"], request["valid_start"], request["valid_end"]))
        if end < start or (variable == "rainfall" and start == end) or issue > start:
            raise ValueError("Invalid target interval or issuance time")
        if issue < utc(self.artifact["dev_end"]):
            raise ValueError("This artifact was selected using later development data; historical issuance is unsupported")
        thresholds = request.get("thresholds")
        if thresholds is None:
            thresholds = [64.5, 115.6, 204.5] if variable == "rainfall" else []
        if len(thresholds) > 32 or any(not np.isfinite(t) or (variable != "temperature" and t < 0) for t in thresholds):
            raise ValueError("Invalid event thresholds")
        excluded = set(request.get("excluded_sources", []))
        key = request["location_id"], variable, start, end
        case = self._case(key, issue, excluded)
        target = {"location_id": key[0], "variable": variable, "valid_start": start.isoformat(), "valid_end": end.isoformat(), "issuance_time": issue.isoformat()}
        receipt = {"track": track, "data_kind": self.artifact["data_kind"], "target": target,
                   "calibration_adjustment": 0.0, "warnings": [], "sources": [], "probabilities": [],
                   "model_digest": self.model_digest, "evaluation_mode": self.artifact["evaluation_mode"]}
        if receipt["data_kind"] == "synthetic":
            receipt["warnings"].append("Synthetic integration demonstration; not real weather guidance or real-world skill evidence.")
        if case is None:
            receipt.update(status="unavailable", summary={}, evidence={"reason": "No source matches the exact target, publication cutoff, trained version and lead range."}, explanation={})
        else:
            bank = self.banks[variable]
            distributions = bank.predict(case)
            mask = np.array([[d is not None for d in distributions]])
            actual_gate = "additive" if track == "interpretable" else self.artifact["variables"][variable]["accuracy_champion"]
            gate = self.gates[variable]
            explanation = {}
            if actual_gate == "spatial":
                if variable not in self.spatial:
                    self.spatial[variable] = SpatialGate.load(self.data_dir / f"{variable}_spatial.pt", bank.sources, gate.features)
                neighbors = [case]
                # Rebuild all contexts from eligible forecasts, applying exclusions throughout.
                for neighbor_key in self.index:
                    if neighbor_key[0] != key[0] and neighbor_key[1:] == key[1:]:
                        other = self._case(neighbor_key, issue, excluded)
                        if other is not None:
                            neighbors.append(other)
                masks = np.array([[d is not None for d in bank.predict(c)] for c in neighbors])
                ws, zs = self.spatial[variable].predict(neighbors, masks)
                w, logits = ws[0], zs[0]
                receipt["explanation"] = {"type": "exact_probability_contributions_only", "gate": "spatial_attention",
                    "note": "Weight reasons are not an exact additive decomposition; source removal is sensitivity, not causality."}
            else:
                ws, zs = gate.predict([case], mask)
                w, logits = ws[0], zs[0]
                explanation = gate.explanation(case)
                receipt["explanation"] = {"type": "exact_additive_logit_decomposition", "gate": "additive",
                    "note": "Log-odds between two sources equal the difference of their summed logit terms. Weights are not probabilities that models are correct."}
            receipt["summary"] = mixture_summary(distributions, w)
            receipt["summary"]["units"] = UNITS[variable]
            receipt["status"] = "blended" if int(mask.sum()) > 1 else "single_source"
            receipt["target"].update(latitude=case.observation.latitude, longitude=case.observation.longitude)
            for i, source in enumerate(bank.sources):
                if distributions[i] is None:
                    continue
                f = case.forecasts[source]
                raw, calibrated = np.asarray(raw_mean(f)), np.asarray(distributions[i].mean())
                receipt["sources"].append({"source_id": source, "model_version": f.model_version,
                    "initialization_time": f.initialization_time.isoformat(), "available_at": f.available_at.isoformat(),
                    "forecast_kind": f.forecast_kind, "weight": float(w[i]), "raw_mean": raw.tolist(),
                    "calibrated_mean": calibrated.tolist(), "age_hours": (issue - f.initialization_time).total_seconds() / 3600,
                    "logit": float(logits[i]), "term_contributions": explanation.get(source, {}),
                    "distribution": distributions[i].to_dict(), "calibration_sample_count": bank.parameters[source]["n_training"]})
            for threshold in thresholds:
                contributions = {s: float(w[i] * exceedance(distributions[i], threshold)) for i, s in enumerate(bank.sources) if distributions[i] is not None}
                receipt["probabilities"].append({"threshold": threshold, "probability": sum(contributions.values()), "contributions": contributions})
            receipt["evidence"] = {"split_counts": self.artifact["variables"][variable]["split_counts"],
                "context_feature_values": {k: float(v) for k, v in raw_features(case, bank.sources).items()},
                "accuracy_champion": self.artifact["variables"][variable]["accuracy_champion"],
                "recent_skill": case.recent_skill, "recent_skill_definition": "delayed normalized absolute error feature",
                "excluded_sources": sorted(excluded), "weight_stability": "not_estimated_for_this_receipt",
                "context_support": "supplied stations/grid locations; no claim of fine-scale national validation"}
            if excluded:
                receipt["warnings"].append("Source-removal sensitivity: context and weights recomputed; not a causal weather explanation.")
            if not case.recent_skill:
                receipt["warnings"].append("No exact issuance replay for recent skill; neutral pooled prior used.")
        receipt['eligibility'] = self.eligibility_audit(key, issue, excluded,
                                                      {s['source_id'] for s in receipt['sources']})
        encoded = json.dumps(receipt, sort_keys=True, allow_nan=False)
        receipt["receipt_id"] = hashlib.sha256(encoded.encode()).hexdigest()[:24]
        directory = self.data_dir / "receipts"
        directory.mkdir(exist_ok=True)
        (directory / f"{receipt['receipt_id']}.json").write_text(json.dumps(receipt, indent=2, allow_nan=False), encoding="utf-8")
        return receipt
