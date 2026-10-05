"""Reproducible experiment plans and paired verification utilities.

This module does not train forecasting models or manufacture benchmark results.
Planned experiments stay pending until an actual training/scoring callback runs.
All functions use the standard library so the evaluation protocol can be checked
independently of the forecasting implementation.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
RAINFALL_SOURCES = ("gfs", "gefs", "graphcast_operational")
SURFACE_SOURCES = RAINFALL_SOURCES + ("pangu_weather", "fourcastnet_v2_small")
DEFAULT_SOURCES = {
    "rainfall": RAINFALL_SOURCES,
    "temperature": SURFACE_SOURCES,
    "wind_uv": SURFACE_SOURCES,
}
FEATURE_GROUPS = (
    "lead", "season", "geography", "regime", "recent_skill", "disagreement", "age"
)
DEFAULT_SPLITS = {
    "train": {"start": "2022-01-01", "end_exclusive": "2024-01-01"},
    "development": {"start": "2024-01-01", "end_exclusive": "2025-01-01"},
    "test": {"start": "2025-01-01", "end_exclusive": "2026-01-01"},
}
DEMO_SPLITS = {
    "train": {"start": "2000-01-01", "end_exclusive": "2000-01-21"},
    "development": {"start": "2000-01-21", "end_exclusive": "2000-01-26"},
    "test": {"start": "2000-01-26", "end_exclusive": "2000-02-01"},
}


def nonempty_subsets(sources: Sequence[str]) -> list[tuple[str, ...]]:
    """Return every subset in cardinality order, preserving input source order."""
    if isinstance(sources, (str, bytes)):
        raise TypeError("sources must be a sequence of source IDs, not a single string")
    sources = tuple(sources)
    if not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError("sources must contain at least one nonempty source ID")
    if len(set(sources)) != len(sources):
        raise ValueError("duplicate source IDs must be resolved before enumeration")
    return [s for n in range(1, len(sources) + 1) for s in itertools.combinations(sources, n)]


def _stable_id(specification: Mapping[str, Any]) -> str:
    encoded = json.dumps(specification, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"{specification['category']}-{digest}"


def _validate_splits(splits: Mapping[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(splits, allow_nan=False))
    previous_end = None
    for partition in ("train", "development", "test"):
        if partition not in copied:
            raise ValueError(f"missing split: {partition}")
        current = copied[partition]
        start = _as_utc(current["start"])
        end = _as_utc(current["end_exclusive"])
        if start >= end:
            raise ValueError(f"empty or reversed split: {partition}")
        if previous_end is not None and start < previous_end:
            raise ValueError("chronological splits must not overlap")
        previous_end = end
    return copied


def generate_experiment_manifest(
    *,
    sources_by_variable: Mapping[str, Sequence[str]] | None = None,
    selected_gate: str = "additive",
    seed: int = 42,
    demo: bool = False,
    splits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the complete study without claiming any experiment was run.

    Defaults enumerate seven rainfall subsets and 31 subsets EACH for temperature
    and wind. ``accuracy`` refers to a masked spatial gate over calibrated sources,
    not the separately labelled direct distributional stacking baseline. Split
    defaults are proposed archive cohorts, not a claim that those archives exist.
    """
    if selected_gate not in {"additive", "accuracy"}:
        raise ValueError("selected_gate must be additive or accuracy")
    source_map = dict(DEFAULT_SOURCES if sources_by_variable is None else sources_by_variable)
    if not source_map:
        raise ValueError("sources_by_variable cannot be empty")
    for variable, sources in source_map.items():
        if variable not in DEFAULT_SOURCES:
            raise ValueError(f"unsupported variable: {variable}")
        nonempty_subsets(sources)
    frozen_splits = _validate_splits((DEMO_SPLITS if demo else DEFAULT_SPLITS) if splits is None else splits)
    protocol = {
        "data_kind": "synthetic" if demo else "observational_required",
        "splits": frozen_splits,
        "cohort_status": "unverified_until_archive_and_checkpoint_audit",
        "comparison_support": "same_available_sources_valid_time_variable_and_accumulation",
        "forecast_horizon": "supported_lead_union_for_operations_intersection_for_comparisons",
        "purge": "exclude_target_windows_crossing_split_boundaries",
        "preprocessing": "fit_on_training_only_with_forward_cross_fitting",
        "selection": "development_only_test_is_locked",
        "seed": seed,
        "selected_gate": selected_gate,
    }
    protocol_id = hashlib.sha256(
        json.dumps(protocol, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()[:16]
    experiments: list[dict[str, Any]] = []

    def add(category: str, variable: str, sources: Sequence[str], method: str,
            **settings: Any) -> None:
        spec = {
            "category": category,
            "variables": [variable],
            "sources": list(sources),
            "method": method,
            "seed": seed,
            "protocol_id": protocol_id,
            "settings": settings,
        }
        experiments.append({"id": _stable_id(spec), **spec, "status": "pending", "result": None})

    for variable, values in source_map.items():
        sources = tuple(values)
        for subset in nonempty_subsets(sources):
            add("source_subset", variable, subset, selected_gate, retrain=True)
        for source in sources:
            for method in ("raw_single", "calibrated_single"):
                add("baseline", variable, (source,), method)
        for method in ("equal", "inverse_skill", "static", "additive", "accuracy"):
            add("baseline", variable, sources, method, calibrated_sources=True)
        add("baseline", variable, sources, "raw_equal", calibrated_sources=False)
        add("baseline", variable, sources, "direct_distributional_stack", exposes_model_weights=False)
        for gate in ("additive", "accuracy"):
            for feature in FEATURE_GROUPS:
                add("feature_ablation", variable, sources, gate,
                    removed_feature_groups=[feature], retrain=True)
        for tail in (0.0, 1.0):
            for dropout in (0.0, 0.15):
                add("objective_ablation", variable, sources, selected_gate,
                    tail_lambda=tail, source_dropout=dropout, retrain=True)
        for regime, tail in itertools.product((False, True), (0.0, 1.0)):
            add("interaction", variable, sources, selected_gate,
                interaction="regime_x_tail", regime_enabled=regime,
                tail_lambda=tail, retrain=True)
        for recent_skill, dropout in itertools.product((False, True), (0.0, 0.15)):
            add("interaction", variable, sources, selected_gate,
                interaction="recent_skill_x_dropout", recent_skill_enabled=recent_skill,
                source_dropout=dropout, retrain=True)
        for regime, context in itertools.product((False, True), ("ordinary", "extreme")):
            add("interaction", variable, sources, selected_gate,
                interaction="regime_adaptation_x_evaluation_regime", regime_enabled=regime,
                evaluation_regime=context, regime_definition="freeze_on_training_development",
                retrain=True)
        for source in sources:
            for dropout, outage in itertools.product((0.0, 0.15), (False, True)):
                add("interaction", variable, sources, selected_gate,
                    interaction="source_dropout_x_outage", source_dropout=dropout,
                    outage_source=source, outage_enabled=outage,
                    unavailable_sources=[source] if outage else [],
                    recompute_gate_features=True, retrain=True,
                    comparison_support="same_frozen_holdout_cases_before_source_removal")
            add("stress", variable, sources, selected_gate, scenario="source_outage",
                unavailable_sources=[source], retrain=False, recompute_gate_features=True)
            add("stress", variable, sources, selected_gate, scenario="late_forecast",
                delayed_source=source, forecast_delay_hours=6,
                availability_policy="issue_time_deadline", retrain=False)
            add("stress", variable, sources, selected_gate, scenario="duplicate_source",
                duplicate_of=source, expected_behavior="deduplicate_or_reject", retrain=False)
            add("stress", variable, sources, selected_gate, scenario="source_version_change",
                changed_source=source, version_pair=None,
                requires="real_versioned_archive_pair", retrain=False)
        families = {
            "physical_nwp": [s for s in sources if s in {"gfs", "gefs"}],
            "ai": [s for s in sources if s not in {"gfs", "gefs"}],
        }
        for family, removed in families.items():
            retained = [s for s in sources if s not in removed]
            if not removed:
                continue
            add("stress", variable, sources, selected_gate, scenario="family_outage",
                family=family, unavailable_sources=removed,
                recompute_gate_features=True, retrain=False)
            if retained:
                add("family_ablation", variable, retained, selected_gate,
                    removed_family=family, retrain=True)
        for delay in (6, 24, 48):
            add("stress", variable, sources, selected_gate, scenario="observation_delay",
                observation_delay_hours=delay, skill_policy="labels_available_at_issue_time",
                retrain=False)
        add("stress", variable, sources, selected_gate, scenario="all_sources_unavailable",
            unavailable_sources=list(sources), expected_behavior="explicit_unavailable_or_labelled_climatology",
            retrain=False)
        add("stress", variable, sources, selected_gate, scenario="unseen_region",
            region=None, requires="prespecified_geographic_holdout", retrain=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "experiment_plan",
        "protocol_id": protocol_id,
        "protocol": protocol,
        "sources_by_variable": {v: list(s) for v, s in source_map.items()},
        "status": "pending",
        "experiment_count": len(experiments),
        "experiments": experiments,
    }


def run_subset_study(
    train_and_score: Callable[..., Mapping[str, Any]],
    sources: Sequence[str],
    variables: Sequence[str],
    seed: int = 42,
    *,
    data_kind: str = "unspecified",
    output_jsonl: str | Path | None = None,
    raise_errors: bool = False,
) -> dict[str, Any]:
    """Actually invoke a supplied trainer/scorer for every nonempty source subset.

    The callback receives keyword arguments ``sources`` (tuple), ``variables``
    (tuple), and ``seed`` (int); it returns a nonempty JSON-compatible mapping of
    actual scores and provenance. Train-only fitting, common verification support
    and locked splits are the caller's responsibility. A closure may supply data
    and model configuration. No scores are synthesized or substituted on failure.

    Optional JSONL logging uses exclusive creation to avoid overwriting an earlier
    study and flushes after each completed/failed callback. ``data_kind`` is an
    explicit caller declaration, not an automatic audit of the underlying data.
    """
    if data_kind not in {"observational", "synthetic", "unspecified"}:
        raise ValueError("data_kind must be observational, synthetic or unspecified")
    if not callable(train_and_score):
        raise TypeError("train_and_score must be callable")
    if isinstance(variables, (str, bytes)):
        raise TypeError("variables must be a sequence of variable IDs, not a single string")
    variables = tuple(variables)
    if (not variables or any(not isinstance(v, str) or not v.strip() for v in variables)
            or len(set(variables)) != len(variables)):
        raise ValueError("variables must be nonempty and unique")
    subsets = nonempty_subsets(sources)
    results: list[dict[str, Any]] = []
    log = None
    if output_jsonl is not None:
        path = Path(output_jsonl)
        path.parent.mkdir(parents=True, exist_ok=True)
        log = path.open("x", encoding="utf-8")
    try:
        for subset in subsets:
            spec = {"category": "source_subset", "sources": list(subset),
                    "variables": list(variables), "seed": seed, "data_kind": data_kind}
            row = {"id": _stable_id(spec), **spec, "status": "running", "result": None}
            started = time.perf_counter()
            failure = None
            try:
                result = train_and_score(sources=subset, variables=variables, seed=seed)
                if not isinstance(result, Mapping) or not result:
                    raise ValueError("callback must return a nonempty mapping of real scores")
                # Also rejects NaN/infinity rather than publishing invalid scores.
                row["result"] = json.loads(json.dumps(dict(result), allow_nan=False))
                row["status"] = "completed"
            except Exception as exc:  # noqa: BLE001 - Record failures from arbitrary user callbacks.
                failure = exc
                row["status"] = "failed"
                row["error"] = {"type": type(exc).__name__, "message": str(exc)}
            row["elapsed_seconds"] = time.perf_counter() - started
            results.append(row)
            if log is not None:
                log.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                log.flush()
            if failure is not None and raise_errors:
                raise failure
    finally:
        if log is not None:
            log.close()
    failed = sum(r["status"] == "failed" for r in results)
    return {"schema_version": SCHEMA_VERSION, "kind": "subset_study_results",
            "data_kind": data_kind, "seed": seed,
            "status": "completed" if failed == 0 else "completed_with_failures",
            "completed_count": len(results) - failed, "failed_count": failed,
            "results": results}


def _as_utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime):
        raise TypeError("timestamps must be ISO strings or datetime objects")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def temporal_block_ids(
    valid_times: Iterable[str | datetime], *, block_days: int = 7,
    origin: str | datetime = "1970-01-01T00:00:00+00:00",
) -> list[str]:
    """Assign all cases in each UTC interval the same block, including all sites.

    Naive timestamps are explicitly interpreted as UTC. The default seven-day
    blocks are an engineering starting point; choose/freeze the block duration
    using training/development dependence diagnostics, not a favorable test CI.
    """
    if not isinstance(block_days, int) or isinstance(block_days, bool) or block_days < 1:
        raise ValueError("block_days must be a positive integer")
    anchor = _as_utc(origin)
    width = timedelta(days=block_days)
    return [f"time-{math.floor((_as_utc(t) - anchor) / width)}" for t in valid_times]


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (position - lo) * (ordered[hi] - ordered[lo])


def paired_block_bootstrap(
    scores_a: Sequence[float], scores_b: Sequence[float], block_ids: Sequence[Any],
    *, n_resamples: int = 2000, confidence: float = 0.95, seed: int = 42,
    drop_nonfinite: bool = False,
) -> dict[str, Any]:
    """Paired cluster bootstrap CI for mean(score_a - score_b).

    Sample whole event/time blocks WITH replacement; every spatial case and every
    paired score within a selected block is retained. The estimand is the mean
    across cases, not an equal-weight mean across events of unequal sizes. This
    assumes the supplied blocks are sufficiently independent. Unpaired/misaligned
    rows must be joined by the caller before invoking this function.

    With fewer than two nonempty independent blocks, return no confidence interval.
    A two-block CI is computable but does not establish reliable significance.
    This helper does not manufacture a p-value from percentile-bootstrap tails.
    """
    if len(scores_a) != len(scores_b) or len(scores_a) != len(block_ids):
        raise ValueError("paired scores and block IDs must have equal lengths")
    if not isinstance(n_resamples, int) or isinstance(n_resamples, bool) or n_resamples < 2:
        raise ValueError("n_resamples must be an integer of at least two")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    grouped: dict[Any, list[float]] = defaultdict(list)
    dropped = 0
    for a, b, block in zip(scores_a, scores_b, block_ids):
        a, b = float(a), float(b)
        if not math.isfinite(a) or not math.isfinite(b):
            if drop_nonfinite:
                dropped += 1
                continue
            raise ValueError("nonfinite paired scores; clean data or set drop_nonfinite=True")
        if block is None or (isinstance(block, float) and not math.isfinite(block)):
            raise ValueError("each retained score needs a valid event/time block ID")
        difference = a - b
        if not math.isfinite(difference):
            raise ValueError("paired score difference overflows; rescale scores before bootstrapping")
        grouped[block].append(difference)
    n_pairs = sum(len(values) for values in grouped.values())
    mean_difference = math.fsum(math.fsum(v) for v in grouped.values()) / n_pairs if n_pairs else None
    result = {
        "statistic": "mean(score_a_minus_score_b)",
        "mean_difference": mean_difference,
        "direction": "negative_favors_a_for_lower_is_better_scores",
        "n_pairs": n_pairs, "n_blocks": len(grouped), "dropped_pairs": dropped,
        "confidence_level": confidence, "confidence_interval": None,
        "n_resamples": 0, "seed": seed,
        "status": "insufficient_blocks", "warnings": [],
    }
    if len(grouped) < 2:
        result["warnings"].append("At least two independent nonempty blocks are required for a CI.")
        return result
    if len(grouped) < 20:
        result["warnings"].append("Few independent blocks: interval precision and coverage may be poor.")
    blocks = [(math.fsum(v), len(v)) for v in grouped.values()]
    rng = random.Random(seed)
    means = []
    for _ in range(n_resamples):
        sample = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        means.append(math.fsum(total for total, _ in sample) / sum(count for _, count in sample))
    alpha = (1 - confidence) / 2
    result["confidence_interval"] = [_quantile(means, alpha), _quantile(means, 1 - alpha)]
    result["n_resamples"] = n_resamples
    result["status"] = "computed"
    return result


def holm_adjust(p_values: Mapping[str, float | None]) -> dict[str, float | None]:
    """Holm-adjust a prespecified family of valid p-values; missing values stay None.

    ``None`` hypotheses remain in the family size conservatively; they are never
    declared significant. Obtain valid dependence-aware p-values independently;
    the block-bootstrap CI above deliberately does not generate them.
    """
    valid = []
    for name, value in p_values.items():
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("p-values must lie in [0, 1] or be None")
        valid.append((name, value))
    adjusted = {name: None for name in p_values}
    previous = 0.0
    for rank, (name, value) in enumerate(sorted(valid, key=lambda pair: pair[1])):
        previous = max(previous, min(1.0, (len(p_values) - rank) * value))
        adjusted[name] = previous
    return adjusted
