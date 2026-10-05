import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from bharatblend.experiments import (
    FEATURE_GROUPS,
    RAINFALL_SOURCES,
    SURFACE_SOURCES,
    generate_experiment_manifest,
    holm_adjust,
    nonempty_subsets,
    paired_block_bootstrap,
    run_subset_study,
    temporal_block_ids,
)


def test_manifest_enumerates_full_variable_specific_studies_without_results():
    manifest = generate_experiment_manifest()
    experiments = manifest["experiments"]
    subsets = [row for row in experiments if row["category"] == "source_subset"]
    assert Counter(row["variables"][0] for row in subsets) == {
        "rainfall": 7, "temperature": 31, "wind_uv": 31,
    }
    assert len({row["id"] for row in experiments}) == manifest["experiment_count"]
    assert all(row["status"] == "pending" and row["result"] is None for row in experiments)
    for row in subsets:
        expected = RAINFALL_SOURCES if row["variables"] == ["rainfall"] else SURFACE_SOURCES
        assert set(row["sources"]) <= set(expected)
        assert row["settings"]["retrain"] is True
    assert manifest["protocol"]["data_kind"] == "observational_required"
    assert manifest["protocol"]["cohort_status"] == "unverified_until_archive_and_checkpoint_audit"


def test_manifest_contains_baselines_ablations_and_factorial_outage_studies():
    rows = generate_experiment_manifest(sources_by_variable={"rainfall": RAINFALL_SOURCES})["experiments"]
    baseline = [row for row in rows if row["category"] == "baseline"]
    assert {row["method"] for row in baseline} >= {
        "raw_single", "calibrated_single", "raw_equal", "equal", "inverse_skill",
        "static", "additive", "accuracy", "direct_distributional_stack",
    }
    for gate in ("additive", "accuracy"):
        ablations = [row for row in rows if row["category"] == "feature_ablation" and row["method"] == gate]
        assert {tuple(row["settings"]["removed_feature_groups"]) for row in ablations} == {
            (feature,) for feature in FEATURE_GROUPS
        }
    objectives = [row["settings"] for row in rows if row["category"] == "objective_ablation"]
    assert {(row["tail_lambda"], row["source_dropout"]) for row in objectives} == {
        (0.0, 0.0), (0.0, 0.15), (1.0, 0.0), (1.0, 0.15),
    }
    for source in RAINFALL_SOURCES:
        interaction = [row["settings"] for row in rows
                       if row["settings"].get("interaction") == "source_dropout_x_outage"
                       and row["settings"]["outage_source"] == source]
        assert {(row["source_dropout"], row["outage_enabled"]) for row in interaction} == {
            (0.0, False), (0.0, True), (0.15, False), (0.15, True),
        }
        for row in interaction:
            assert row["unavailable_sources"] == ([source] if row["outage_enabled"] else [])
    scenarios = {row["settings"]["scenario"] for row in rows if row["category"] == "stress"}
    assert {"source_outage", "late_forecast", "source_version_change", "family_outage",
            "observation_delay", "all_sources_unavailable", "unseen_region"} <= scenarios


def test_manifest_is_deterministic_and_protocol_changes_change_ids():
    initial = generate_experiment_manifest(seed=4)
    assert initial == generate_experiment_manifest(seed=4)
    assert initial["protocol_id"] != generate_experiment_manifest(seed=5)["protocol_id"]
    assert initial["experiments"][0]["id"] != generate_experiment_manifest(demo=True)["experiments"][0]["id"]
    assert generate_experiment_manifest(demo=True)["protocol"]["data_kind"] == "synthetic"


@pytest.mark.parametrize("sources", [[], ["gfs", "gfs"], [" "], [None]])
def test_reject_invalid_source_sets(sources):
    with pytest.raises(ValueError):
        nonempty_subsets(sources)


def test_manifest_rejects_empty_or_overlapping_splits():
    with pytest.raises(ValueError, match="missing split"):
        generate_experiment_manifest(splits={})
    with pytest.raises(ValueError, match="overlap"):
        generate_experiment_manifest(splits={
            "train": {"start": "2022-01-01", "end_exclusive": "2024-02-01"},
            "development": {"start": "2024-01-01", "end_exclusive": "2025-01-01"},
            "test": {"start": "2025-01-01", "end_exclusive": "2026-01-01"},
        })


@pytest.mark.parametrize("sources,expected", [(RAINFALL_SOURCES, 7), (SURFACE_SOURCES, 31)])
def test_subset_study_calls_real_callback_once_per_subset(tmp_path, sources, expected):
    calls = []

    def score(**kwargs):
        calls.append(kwargs)
        return {"metric": 3.25 / len(kwargs["sources"]), "artifact": "fixture-only"}

    output = tmp_path / "results.jsonl"
    study = run_subset_study(score, sources, ["temperature"], 13,
                             data_kind="synthetic", output_jsonl=output)
    assert study["status"] == "completed"
    assert study["completed_count"] == expected
    assert study["data_kind"] == "synthetic"
    assert [call["sources"] for call in calls] == nonempty_subsets(sources)
    assert all(call["variables"] == ("temperature",) and call["seed"] == 13 for call in calls)
    assert [json.loads(line) for line in output.read_text().splitlines()] == study["results"]
    assert all(row["result"]["metric"] == 3.25 / len(row["sources"]) for row in study["results"])
    with pytest.raises(FileExistsError):
        run_subset_study(score, sources, ["temperature"], output_jsonl=output)
    assert len(calls) == expected


def test_subset_study_records_failures_without_inventing_scores(tmp_path):
    def score(*, sources, **kwargs):
        if sources == ("gfs",):
            raise RuntimeError("missing observation archive")
        if sources == ("gefs",):
            return {"crps": float("nan")}
        return {"crps": 1.5}

    study = run_subset_study(score, ["gfs", "gefs"], ["rainfall"])
    assert study["status"] == "completed_with_failures"
    assert study["failed_count"] == 2
    assert study["completed_count"] == 1
    assert [row["result"] for row in study["results"]] == [None, None, {"crps": 1.5}]
    output = tmp_path / "failed.jsonl"
    with pytest.raises(RuntimeError, match="missing observation archive"):
        run_subset_study(score, ["gfs"], ["rainfall"], output_jsonl=output, raise_errors=True)
    assert json.loads(output.read_text())["status"] == "failed"


@pytest.mark.parametrize("variables", [[], [""], ["rainfall", "rainfall"], [None]])
def test_subset_study_rejects_invalid_variable_ids(variables):
    with pytest.raises(ValueError):
        run_subset_study(lambda **kwargs: {"score": 1}, ["gfs"], variables)


def test_reject_string_in_place_of_source_or_variable_sequence():
    with pytest.raises(TypeError, match="sequence of source IDs"):
        nonempty_subsets("gfs")
    with pytest.raises(TypeError, match="sequence of variable IDs"):
        run_subset_study(lambda **kwargs: {"score": 1}, ["gfs"], "rainfall")


def test_temporal_blocks_keep_all_locations_and_timezone_equivalent_instants_together():
    times = ["2025-01-01T00:00:00Z", "2025-01-01T05:30:00+05:30",
             datetime(2025, 1, 7, 23, 59, tzinfo=UTC), "2025-01-08",
             "2024-12-31T23:59:59Z"]
    assert temporal_block_ids(times, origin="2025-01-01") == [
        "time-0", "time-0", "time-0", "time-1", "time--1",
    ]


def test_bootstrap_is_paired_case_weighted_and_resamples_whole_spatial_blocks():
    # Three spatial cells in one event, one in another: case mean is zero,
    # unlike the equally weighted mean of event means, which would be -1.
    scores_a, scores_b, blocks = [2, 3, 4, 7], [1, 2, 3, 10], ["first"] * 3 + ["second"]
    result = paired_block_bootstrap(scores_a, scores_b, blocks, n_resamples=501, seed=9)
    assert result["mean_difference"] == 0
    assert result["confidence_interval"] == [-3, 1]
    assert result["n_pairs"] == 4 and result["n_blocks"] == 2
    # Duplicating every spatial cell adds no independent event information.
    expanded = paired_block_bootstrap(scores_a * 20, scores_b * 20, blocks * 20,
                                      n_resamples=501, seed=9)
    assert expanded["confidence_interval"] == result["confidence_interval"]
    assert expanded["mean_difference"] == result["mean_difference"]
    assert paired_block_bootstrap(scores_a, scores_b, blocks, n_resamples=501, seed=9) == result


def test_bootstrap_difference_sign_and_no_false_precision_from_one_event():
    result = paired_block_bootstrap([1, 2, 3], [4, 5, 6], ["a", "b", "c"], n_resamples=100)
    assert result["mean_difference"] == -3
    assert result["confidence_interval"] == [-3, -3]
    assert result["direction"] == "negative_favors_a_for_lower_is_better_scores"
    one_event = paired_block_bootstrap([1] * 100, [4] * 100, ["a"] * 100)
    assert one_event["mean_difference"] == -3
    assert one_event["status"] == "insufficient_blocks"
    assert one_event["confidence_interval"] is None and one_event["n_resamples"] == 0
    assert one_event["warnings"]
    empty = paired_block_bootstrap([], [], [])
    assert empty["mean_difference"] is None and empty["confidence_interval"] is None


def test_bootstrap_missing_pairs_are_dropped_as_pairs_only_when_requested():
    with pytest.raises(ValueError, match="nonfinite"):
        paired_block_bootstrap([1, float("nan")], [3, 4], ["a", "b"])
    result = paired_block_bootstrap([1, float("nan"), 5], [3, 4, 7], ["a", "b", "c"],
                                    drop_nonfinite=True)
    assert result["dropped_pairs"] == 1 and result["n_pairs"] == 2
    assert result["n_blocks"] == 2 and result["mean_difference"] == -2
    with pytest.raises(ValueError, match="equal lengths"):
        paired_block_bootstrap([1], [2, 3], ["a"])
    with pytest.raises(ValueError, match="valid event/time block"):
        paired_block_bootstrap([1], [2], [None])


def test_holm_is_monotone_and_retains_unavailable_tests_in_family_size():
    result = holm_adjust({"a": .01, "b": .02, "c": .9, "unavailable": None})
    assert result == {"a": .04, "b": .06, "c": 1.0, "unavailable": None}
    assert holm_adjust({"a": .04, "b": .041}) == {"a": .08, "b": .08}
    for value in (-.01, 1.01, float("nan")):
        with pytest.raises(ValueError, match="p-values"):
            holm_adjust({"invalid": value})
