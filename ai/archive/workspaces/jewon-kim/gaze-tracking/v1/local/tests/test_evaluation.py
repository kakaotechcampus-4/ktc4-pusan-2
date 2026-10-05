"""Contracts of the offline evaluation layer (``ai/evaluation``).

What this file protects, one line per module:

``metrics.py``
    The doc 7 accounting rules -- UNCERTAIN is neither a wrong answer nor a
    free pass, IGNORE never reaches a denominator, an unknown label is an
    exception rather than a quietly shrunken denominator, an unmeasured
    statistic is ``None`` rather than ``0.0``, and an unmeasured release-gate
    row is a FAILED row.
``buckets.py``
    The ten doc 19 rules fire exactly at their documented cut points, are not
    mutually exclusive, are ground-truth-defined where the doc says so, and
    read a missing column as "no bucket" instead of raising.
``sanity.py``
    The schemas.py sign consequence (BOTTOM more negative than CAMERA) is
    checked *per participant and backbone*, never pooled, and the verdict
    ladder INSUFFICIENT -> INVERTED -> WEAK -> OK is applied in that order.

Everything here is a hand-built truth/prediction sequence, event timeline or
feature row -- the only honest way to pin a metric *definition*.  There is no
recorded dataset in this repo, so no test in this file quotes a model score.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

# ``evaluation`` lives in ``<root>/ai``, which conftest does not add (it adds
# ``<root>`` and ``<root>/ai/src``).  Same entry the modules' own
# ``_bootstrap_import_path`` would add.
_AI_ROOT = Path(__file__).resolve().parents[1] / "ai"
if str(_AI_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_ROOT))

from evaluation import buckets, metrics, sanity  # noqa: E402

from vision.config import ReleaseGateConfig  # noqa: E402
from vision.schemas import GazeLabel, GazeState, GazeStateEvent, SegmentLabel  # noqa: E402

CAMERA = GazeLabel.CAMERA.value
BOTTOM = GazeLabel.BOTTOM.value
UNCERTAIN = GazeState.UNCERTAIN.value
IGNORE = GazeLabel.IGNORE.value


# ==========================================================================
# metrics: label hygiene
# ==========================================================================


def test_ignore_truth_is_excluded_from_every_rate_and_counted_apart():
    scores = metrics.frame_metrics(
        [CAMERA, IGNORE, BOTTOM, IGNORE],
        [CAMERA, BOTTOM, BOTTOM, CAMERA],
    )

    assert scores["n"] == 4
    assert scores["n_ignored"] == 2
    assert scores["n_labelled"] == 2
    assert scores["n_decided"] == 2
    # Both IGNORE rows were predicted, wrongly, and still cost nothing.
    assert scores["accuracy"] == 1.0
    assert scores["macro_f1"] == 1.0
    assert sum(sum(row.values()) for row in scores["confusion"].values()) == 2


@pytest.mark.parametrize("missing", [None, "", "   ", float("nan"), np.nan, pd.NA])
def test_a_row_with_no_ground_truth_is_ignored_not_scored(missing):
    scores = metrics.frame_metrics([CAMERA, missing], [CAMERA, CAMERA])

    assert scores["n_ignored"] == 1
    assert scores["n_labelled"] == 1
    assert scores["n"] == 2


@pytest.mark.parametrize("bad", ["camra", "UNCERTAIN", "NONE", "LEFT", "0"])
def test_an_unknown_ground_truth_label_raises_instead_of_shrinking_the_denominator(bad):
    with pytest.raises(ValueError, match="unknown ground-truth label"):
        metrics.frame_metrics([CAMERA, bad], [CAMERA, CAMERA])


@pytest.mark.parametrize("bad", ["CAMRA", "IGNORE", "MAYBE", "-1"])
def test_an_unknown_predicted_label_raises(bad):
    with pytest.raises(ValueError, match="unknown predicted label"):
        metrics.frame_metrics([CAMERA, BOTTOM], [CAMERA, bad])


def test_the_offending_row_index_is_named_in_the_error():
    with pytest.raises(ValueError, match=r"row 2:"):
        metrics.frame_metrics([CAMERA, BOTTOM, "SIDE"], [CAMERA, BOTTOM, CAMERA])


def test_misaligned_sequences_raise_rather_than_zipping_short():
    with pytest.raises(ValueError, match="y_true has 3 rows, y_pred has 2"):
        metrics.frame_metrics([CAMERA] * 3, [CAMERA] * 2)


@pytest.mark.parametrize(
    "undecided",
    [UNCERTAIN, "uncertain", "none", "NULL", "nan", None, float("nan"), pd.NA, ""],
)
def test_every_spelling_of_no_decision_folds_into_uncertain(undecided):
    scores = metrics.frame_metrics([CAMERA], [undecided])

    assert scores["n_uncertain"] == 1
    assert scores["n_decided"] == 0
    assert scores["uncertain_ratio"] == 1.0
    assert scores["confusion"][CAMERA][UNCERTAIN] == 1


def test_enum_members_are_accepted_on_both_sides():
    scores = metrics.frame_metrics(
        [GazeLabel.CAMERA, GazeLabel.BOTTOM, GazeLabel.IGNORE],
        [GazeState.CAMERA, GazeState.UNCERTAIN, GazeState.BOTTOM],
    )

    assert scores["n_ignored"] == 1
    assert scores["n_uncertain"] == 1
    assert scores["accuracy"] == 1.0


def test_labels_are_case_and_whitespace_insensitive():
    assert metrics.frame_metrics([" camera "], ["Camera"])["accuracy"] == 1.0


# ==========================================================================
# metrics: the per-class arithmetic, hand-computed
# ==========================================================================

# 10 labelled frames.  CAMERA truth: 4 right, 1 called BOTTOM, 1 abstained.
# BOTTOM truth: 2 right, 1 called CAMERA, 1 abstained.
_HAND_TRUTH = [CAMERA] * 6 + [BOTTOM] * 4
_HAND_PRED = [CAMERA] * 4 + [BOTTOM, UNCERTAIN] + [BOTTOM, BOTTOM, CAMERA, UNCERTAIN]


@pytest.fixture(scope="module")
def hand_scores() -> Dict[str, Any]:
    return metrics.frame_metrics(_HAND_TRUTH, _HAND_PRED)


def test_confusion_keeps_uncertain_as_its_own_predicted_column(hand_scores):
    assert hand_scores["confusion"] == {
        CAMERA: {CAMERA: 4, BOTTOM: 1, UNCERTAIN: 1},
        BOTTOM: {CAMERA: 1, BOTTOM: 2, UNCERTAIN: 1},
    }


@pytest.mark.parametrize(
    "cls, precision, recall, f1",
    [
        (CAMERA, 4 / 5, 4 / 5, 0.8),
        (BOTTOM, 2 / 3, 2 / 3, 4 / 6),
    ],
)
def test_per_class_scores_exclude_abstentions_from_tp_fp_fn(
    hand_scores, cls, precision, recall, f1
):
    stats = hand_scores["per_class"][cls]

    assert stats["precision"] == pytest.approx(precision)
    assert stats["recall"] == pytest.approx(recall)
    assert stats["f1"] == pytest.approx(f1)


@pytest.mark.parametrize(
    "cls, precision, recall, f1",
    [
        (CAMERA, 4 / 5, 4 / 6, 8 / 11),
        (BOTTOM, 2 / 3, 2 / 4, 4 / 7),
    ],
)
def test_uncertain_as_error_adds_abstentions_to_fn_only(hand_scores, cls, precision, recall, f1):
    optimistic = hand_scores["per_class"][cls]
    pessimistic = hand_scores["per_class_uncertain_as_error"][cls]

    # Precision is untouched: an abstention is never *claimed* as a class.
    assert pessimistic["precision"] == pytest.approx(precision)
    assert pessimistic["precision"] == pytest.approx(optimistic["precision"])
    assert pessimistic["recall"] == pytest.approx(recall)
    assert pessimistic["f1"] == pytest.approx(f1)
    assert pessimistic["recall"] < optimistic["recall"]


def test_support_separates_decided_frames_from_abstentions(hand_scores):
    camera = hand_scores["per_class"][CAMERA]

    assert camera["support"] == 6.0
    assert camera["support_decided"] == 5.0
    assert camera["uncertain"] == 1.0
    assert camera["support"] == camera["support_decided"] + camera["uncertain"]


def test_macro_f1_is_the_unweighted_mean_over_exactly_camera_and_bottom(hand_scores):
    per_class = hand_scores["per_class"]
    expected = (per_class[CAMERA]["f1"] + per_class[BOTTOM]["f1"]) / 2

    assert hand_scores["macro_f1"] == pytest.approx(expected)
    assert hand_scores["macro_f1"] == pytest.approx(0.7333333333)
    assert hand_scores["macro_f1_uncertain_as_error"] == pytest.approx((8 / 11 + 4 / 7) / 2)
    assert hand_scores["macro_f1_uncertain_as_error"] < hand_scores["macro_f1"]


def test_headline_rates_are_computed_over_decided_frames_only(hand_scores):
    assert hand_scores["n_labelled"] == 10
    assert hand_scores["n_uncertain"] == 2
    assert hand_scores["n_decided"] == 8
    assert hand_scores["uncertain_ratio"] == pytest.approx(0.2)
    assert hand_scores["coverage"] == pytest.approx(0.8)
    assert hand_scores["accuracy"] == pytest.approx(6 / 8)


def test_macro_f1_weights_a_rare_class_as_heavily_as_a_common_one():
    # 9 CAMERA frames right, the single BOTTOM frame wrong: accuracy 0.9, macro F1 far lower.
    scores = metrics.frame_metrics([CAMERA] * 9 + [BOTTOM], [CAMERA] * 10)

    assert scores["accuracy"] == pytest.approx(0.9)
    assert scores["per_class"][BOTTOM]["f1"] == 0.0
    assert scores["macro_f1"] == pytest.approx(scores["per_class"][CAMERA]["f1"] / 2)


def test_a_class_that_never_appears_still_counts_in_the_macro_mean():
    scores = metrics.frame_metrics([CAMERA] * 4, [CAMERA] * 4)

    assert scores["per_class"][CAMERA]["f1"] == 1.0
    assert scores["per_class"][BOTTOM]["f1"] == 0.0
    assert scores["macro_f1"] == 0.5


def test_confusion_matrix_is_the_two_by_two_decided_view(hand_scores):
    assert hand_scores["confusion_labels"] == [CAMERA, BOTTOM]
    assert hand_scores["confusion_matrix"] == [[4, 1], [1, 2]]
    assert sum(sum(row) for row in hand_scores["confusion_matrix"]) == hand_scores["n_decided"]


# ==========================================================================
# metrics: degenerate runs must not produce NaN
# ==========================================================================


def test_a_run_that_decided_nothing_scores_zero_and_declares_full_abstention():
    scores = metrics.frame_metrics([CAMERA, BOTTOM, CAMERA], [UNCERTAIN] * 3)

    assert scores["uncertain_ratio"] == 1.0
    assert scores["coverage"] == 0.0
    for key in ("accuracy", "macro_f1", "bottom_recall", "camera_recall"):
        assert scores[key] == 0.0, key
        assert not math.isnan(scores[key]), key


def test_an_empty_run_is_all_zero_rather_than_nan():
    scores = metrics.frame_metrics([], [])

    assert scores["n"] == scores["n_labelled"] == scores["n_decided"] == 0
    for key in ("uncertain_ratio", "coverage", "accuracy", "macro_f1", "bottom_recall"):
        assert scores[key] == 0.0, key


def test_a_perfect_run_is_exactly_one_everywhere():
    scores = metrics.frame_metrics([CAMERA, BOTTOM] * 3, [CAMERA, BOTTOM] * 3)

    assert scores["macro_f1"] == 1.0
    assert scores["macro_f1_uncertain_as_error"] == 1.0
    assert scores["bottom_recall"] == scores["camera_recall"] == 1.0
    assert scores["uncertain_ratio"] == 0.0


# ==========================================================================
# metrics: the abstention breakdown
# ==========================================================================


def test_reasons_are_tallied_only_for_abstentions_on_labelled_frames():
    scores = metrics.frame_metrics(
        [CAMERA, BOTTOM, IGNORE, BOTTOM],
        [UNCERTAIN, BOTTOM, UNCERTAIN, UNCERTAIN],
        uncertain_reasons=["NO_FACE", None, "NO_FACE", "LOW_MARGIN"],
    )

    # The IGNORE row abstained too and is not in the tally; nor is the decided one.
    assert scores["uncertain_by_reason"] == {"LOW_MARGIN": 1, "NO_FACE": 1}


def test_a_missing_reason_is_reported_as_unspecified():
    scores = metrics.frame_metrics(
        [CAMERA, CAMERA], [UNCERTAIN, UNCERTAIN], uncertain_reasons=[None, ""]
    )

    assert scores["uncertain_by_reason"] == {"UNSPECIFIED": 2}


def test_reason_tally_is_ordered_by_count_then_name():
    scores = metrics.frame_metrics(
        [CAMERA] * 5,
        [UNCERTAIN] * 5,
        uncertain_reasons=["ZEBRA", "ALPHA", "NO_FACE", "NO_FACE", "NO_FACE"],
    )

    assert list(scores["uncertain_by_reason"]) == ["NO_FACE", "ALPHA", "ZEBRA"]


def test_a_short_reason_sequence_raises():
    with pytest.raises(ValueError, match="same length"):
        metrics.frame_metrics([CAMERA, BOTTOM], [CAMERA, BOTTOM], uncertain_reasons=["X"])


def test_frame_metrics_does_not_consume_the_caller_sequences():
    truths = [CAMERA, BOTTOM]
    predictions = [CAMERA, UNCERTAIN]

    metrics.frame_metrics(truths, predictions)

    assert truths == [CAMERA, BOTTOM]
    assert predictions == [CAMERA, UNCERTAIN]


def test_one_shot_iterables_survive_the_reason_breakdown():
    scores = metrics.frame_metrics(
        iter([CAMERA, BOTTOM]),
        iter([UNCERTAIN, BOTTOM]),
        uncertain_reasons=["NO_FACE", None],
    )

    assert scores["uncertain_by_reason"] == {"NO_FACE": 1}


# ==========================================================================
# metrics: latency
# ==========================================================================


def test_latency_drops_non_finite_values_and_counts_them():
    stats = metrics.latency_stats(
        [10, 20, float("inf"), float("-inf"), float("nan"), None, "abc", 30]
    )

    assert stats["n"] == 3
    assert stats["n_dropped"] == 5
    assert stats["mean"] == pytest.approx(20.0)
    assert stats["min"] == 10.0
    assert stats["max"] == 30.0


def test_latency_percentiles_use_linear_interpolation():
    stats = metrics.latency_stats([10, 20, 30])

    assert stats["p50"] == pytest.approx(20.0)
    assert stats["p95"] == pytest.approx(29.0)
    assert stats["p99"] == pytest.approx(29.8)


@pytest.mark.parametrize("statistic", ["p50", "p95", "p99", "mean", "min", "max"])
def test_an_unmeasured_latency_is_none_not_zero(statistic):
    assert metrics.latency_stats([])[statistic] is None
    assert metrics.latency_stats([float("nan"), None])[statistic] is None


def test_an_all_garbage_sample_reports_zero_kept_and_everything_dropped():
    stats = metrics.latency_stats([float("nan"), None, "x"])

    assert stats["n"] == 0
    assert stats["n_dropped"] == 3


def test_latency_accepts_a_pandas_series_with_a_non_default_index():
    series = pd.Series([5.0, 15.0], index=["a", "b"])

    assert metrics.latency_stats(series)["n"] == 2


# ==========================================================================
# metrics: the doc 7 release gate
# ==========================================================================


def _summary_at_threshold(gate: ReleaseGateConfig) -> Dict[str, float]:
    """A summary sitting exactly on every gate threshold."""
    return {name: float(getattr(gate, field)) for name, field, _, _ in metrics._GATE_SPEC}


def test_the_gate_has_exactly_the_six_doc7_rows():
    gate = metrics.evaluate_release_gate({})

    assert [row["metric"] for row in gate["rows"]] == [
        "macro_f1",
        "bottom_recall",
        "per_user_f1_min",
        "uncertain_ratio",
        "calibration_failure_rate",
        "p95_latency_ms",
    ]
    assert [row["direction"] for row in gate["rows"]] == ["min", "min", "min", "max", "max", "max"]


def test_the_shipped_pass_line_is_the_documented_one():
    assert _summary_at_threshold(ReleaseGateConfig()) == {
        "macro_f1": 0.85,
        "bottom_recall": 0.90,
        "per_user_f1_min": 0.75,
        "uncertain_ratio": 0.20,
        "calibration_failure_rate": 0.10,
        "p95_latency_ms": 125.0,
    }


def test_every_direction_is_inclusive_exactly_at_its_threshold():
    gate_cfg = ReleaseGateConfig()

    gate = metrics.evaluate_release_gate(_summary_at_threshold(gate_cfg), gate_cfg)

    assert gate["missing"] == []
    assert all(row["pass"] for row in gate["rows"])
    assert gate["passed"] is True


@pytest.mark.parametrize(
    "name, direction", [(name, direction) for name, _, direction, _ in metrics._GATE_SPEC]
)
def test_one_metric_just_past_its_threshold_fails_the_whole_gate(name, direction):
    summary = _summary_at_threshold(ReleaseGateConfig())
    summary[name] += -1e-6 if direction == "min" else 1e-6

    gate = metrics.evaluate_release_gate(summary)

    failed = {row["metric"] for row in gate["rows"] if not row["pass"]}
    assert failed == {name}
    assert gate["passed"] is False
    assert gate["missing"] == []


@pytest.mark.parametrize("name", [name for name, _, _, _ in metrics._GATE_SPEC])
def test_an_unmeasured_row_is_a_failed_row(name):
    summary = _summary_at_threshold(ReleaseGateConfig())
    summary.pop(name)

    gate = metrics.evaluate_release_gate(summary)

    row = next(row for row in gate["rows"] if row["metric"] == name)
    assert row["value"] is None
    assert row["pass"] is False
    assert gate["missing"] == [name]
    assert gate["passed"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, "n/a", True, False])
def test_a_value_that_is_not_a_finite_number_reads_as_missing(value):
    summary = _summary_at_threshold(ReleaseGateConfig())
    summary["macro_f1"] = value

    gate = metrics.evaluate_release_gate(summary)

    assert gate["missing"] == ["macro_f1"]
    assert gate["rows"][0]["value"] is None


def test_a_run_that_never_measured_latency_cannot_read_as_green():
    # The join latency_stats exists for: no sample -> None -> failed gate row.
    summary = _summary_at_threshold(ReleaseGateConfig())
    summary.pop("p95_latency_ms")
    summary["latency"] = metrics.latency_stats([])

    gate = metrics.evaluate_release_gate(summary)

    assert gate["missing"] == ["p95_latency_ms"]
    assert gate["passed"] is False


def test_nested_summary_keys_are_read_when_the_flat_ones_are_absent():
    gate = metrics.evaluate_release_gate(
        {
            "frame": {"macro_f1": 0.9, "bottom_recall": 0.95, "uncertain_ratio": 0.05},
            "per_user": {"macro_f1_min": 0.8},
            "calibration": {"failure_rate": 0.0},
            "latency": {"p95": 100.0},
        }
    )

    assert gate["missing"] == []
    assert gate["passed"] is True


def test_a_flat_key_wins_over_the_nested_fallback():
    gate = metrics.evaluate_release_gate({"macro_f1": 0.10, "frame": {"macro_f1": 0.99}})

    assert gate["rows"][0]["value"] == pytest.approx(0.10)


def test_a_broken_flat_key_falls_through_to_the_nested_one():
    gate = metrics.evaluate_release_gate({"macro_f1": None, "frame": {"macro_f1": 0.99}})

    assert gate["rows"][0]["value"] == pytest.approx(0.99)


def test_passed_is_the_and_of_every_row():
    summary = _summary_at_threshold(ReleaseGateConfig())
    summary["bottom_recall"] = 0.0

    gate = metrics.evaluate_release_gate(summary)

    assert gate["passed"] == all(row["pass"] for row in gate["rows"])
    assert gate["passed"] is False


def test_the_gate_echoes_the_thresholds_it_actually_used():
    relaxed = ReleaseGateConfig(min_macro_f1=0.5, max_p95_latency_ms=500.0)

    gate = metrics.evaluate_release_gate({"macro_f1": 0.6}, relaxed)

    assert gate["config"]["min_macro_f1"] == 0.5
    assert gate["config"]["max_p95_latency_ms"] == 500.0
    assert gate["rows"][0]["target"] == 0.5
    assert gate["rows"][0]["pass"] is True


def test_gate_table_renders_one_row_per_gate_line():
    table = metrics.gate_table(metrics.evaluate_release_gate({}))

    assert list(table.columns) == ["metric", "value", "target", "direction", "pass"]
    assert len(table) == len(metrics._GATE_SPEC)


# ==========================================================================
# metrics: segments
# ==========================================================================


def _segment(start: int, end: int, label: str, participant: str = "p1") -> Dict[str, Any]:
    return {"start_ms": start, "end_ms": end, "label": label, "participant_id": participant}


def test_an_event_stream_is_read_as_piecewise_constant_intervals():
    records = metrics.segment_predictions(
        [(0, CAMERA), (1000, BOTTOM), (2000, CAMERA)],
        [_segment(0, 1000, CAMERA), _segment(1000, 2000, BOTTOM)],
    )

    assert [record["pred_label"] for record in records] == [CAMERA, BOTTOM]
    assert records[0]["covered_ms"] == {CAMERA: 1000.0, BOTTOM: 0.0, UNCERTAIN: 0.0}
    assert records[1]["covered_ms"] == {CAMERA: 0.0, BOTTOM: 1000.0, UNCERTAIN: 0.0}


def test_a_segment_takes_the_class_that_covered_most_of_its_time():
    records = metrics.segment_predictions(
        [(0, CAMERA), (100, BOTTOM), (1000, BOTTOM)],
        [_segment(0, 1000, BOTTOM)],
    )

    assert records[0]["covered_ms"][CAMERA] == 100.0
    assert records[0]["covered_ms"][BOTTOM] == 900.0
    assert records[0]["pred_label"] == BOTTOM


def test_events_are_ordered_by_time_before_the_timeline_is_built():
    shuffled = [(2000, CAMERA), (0, CAMERA), (1000, BOTTOM)]

    records = metrics.segment_predictions(shuffled, [_segment(1000, 2000, BOTTOM)])

    assert records[0]["pred_label"] == BOTTOM
    assert records[0]["covered_ms"][BOTTOM] == 1000.0


@pytest.mark.parametrize(
    "events",
    [
        pytest.param([(0, CAMERA), (1000, BOTTOM)], id="tuple"),
        pytest.param([{"t_ms": 0, "label": CAMERA}, {"t_ms": 1000, "label": BOTTOM}], id="mapping"),
        pytest.param(
            [
                GazeStateEvent(
                    t_ms=0,
                    label=CAMERA,
                    confidence=0.9,
                    continuous_duration_ms=0,
                    face_valid=True,
                ),
                GazeStateEvent(
                    t_ms=1000,
                    label=BOTTOM,
                    confidence=0.9,
                    continuous_duration_ms=0,
                    face_valid=True,
                ),
            ],
            id="event",
        ),
    ],
)
def test_every_event_spelling_produces_the_same_timeline(events):
    records = metrics.segment_predictions(events, [_segment(0, 1000, CAMERA)])

    assert records[0]["pred_label"] == CAMERA
    assert records[0]["covered_ms"][CAMERA] == 1000.0


def test_an_event_without_a_readable_time_and_label_raises():
    with pytest.raises(ValueError, match=r"\(t_ms, label\) pair"):
        metrics.segment_predictions([(0,)], [_segment(0, 100, CAMERA)])


def test_an_event_with_no_label_is_an_abstention_not_a_class():
    records = metrics.segment_predictions(
        [{"t_ms": 0}, {"t_ms": 1000, "label": CAMERA}, {"t_ms": 2000, "label": CAMERA}],
        [_segment(0, 1000, CAMERA)],
    )

    assert records[0]["covered_ms"][UNCERTAIN] == 1000.0
    assert records[0]["pred_label"] is None


def test_the_last_event_holds_for_the_median_inter_event_gap_by_default():
    events = [(0, CAMERA), (100, CAMERA), (200, CAMERA), (1000, BOTTOM)]

    records = metrics.segment_predictions(events, [_segment(1000, 5000, BOTTOM)])

    # median([100, 100, 800]) == 100: the model is not credited with the 3.9 s
    # of segment it never reported on.
    assert records[0]["covered_ms"][BOTTOM] == 100.0
    assert records[0]["duration_ms"] == 4000


def test_an_explicit_hold_overrides_the_median():
    events = [(0, CAMERA), (100, CAMERA), (200, CAMERA), (1000, BOTTOM)]

    records = metrics.segment_predictions(
        events, [_segment(1000, 5000, BOTTOM)], hold_last_ms=2500
    )

    assert records[0]["covered_ms"][BOTTOM] == 2500.0


def test_a_zero_hold_gives_the_final_event_no_coverage_at_all():
    events = [(0, CAMERA), (1000, BOTTOM)]
    segments = [_segment(1000, 2000, BOTTOM)]

    held = metrics.segment_predictions(events, segments)
    assert held[0]["covered_ms"][BOTTOM] == 1000.0

    # With no hold the last event spans nothing, so it contributes no time and
    # the stream's window stops before the segment even starts.
    assert metrics.segment_predictions(events, segments, hold_last_ms=0) == []
    unheld = metrics.segment_predictions(
        events, segments, hold_last_ms=0, require_overlap=False
    )
    assert unheld[0]["covered_ms"][BOTTOM] == 0.0
    assert unheld[0]["pred_label"] is None


def test_a_lone_event_covers_nothing_under_the_default_hold():
    segments = [_segment(0, 1000, CAMERA)]

    assert metrics.segment_predictions([(0, CAMERA)], segments) == []

    kept = metrics.segment_predictions([(0, CAMERA)], segments, require_overlap=False)
    assert [record["pred_label"] for record in kept] == [None]


@pytest.mark.parametrize(
    "segment, kept",
    [
        pytest.param(_segment(-1000, 0, CAMERA), False, id="ends-exactly-at-window-start"),
        pytest.param(_segment(-1000, 1, CAMERA), True, id="one-ms-inside-the-start"),
        pytest.param(_segment(3000, 4000, CAMERA), False, id="starts-exactly-at-window-end"),
        pytest.param(_segment(2999, 4000, CAMERA), True, id="one-ms-inside-the-end"),
    ],
)
def test_require_overlap_uses_a_half_open_window_around_the_event_stream(segment, kept):
    events = [(0, CAMERA), (1000, BOTTOM), (2000, CAMERA)]  # window [0, 3000)

    records = metrics.segment_predictions(events, [segment])

    assert bool(records) is kept


def test_require_overlap_false_keeps_unobserved_segments_as_abstentions():
    events = [(0, CAMERA), (1000, CAMERA)]

    records = metrics.segment_predictions(
        events, [_segment(60_000, 61_000, BOTTOM)], require_overlap=False
    )

    assert len(records) == 1
    assert records[0]["pred_label"] is None
    assert records[0]["covered_ms"] == {CAMERA: 0.0, BOTTOM: 0.0, UNCERTAIN: 0.0}


@pytest.mark.parametrize(
    "segment",
    [
        pytest.param(_segment(0, 1000, IGNORE), id="ignore-label"),
        pytest.param(_segment(0, 1000, UNCERTAIN), id="uncertain-label"),
        pytest.param(_segment(0, 1000, "SIDE"), id="unknown-label"),
        pytest.param(_segment(1000, 1000, CAMERA), id="zero-length"),
        pytest.param(_segment(1000, 500, CAMERA), id="reversed"),
    ],
)
def test_unscorable_segments_are_dropped_rather_than_scored(segment):
    events = [(0, CAMERA), (1000, BOTTOM), (2000, CAMERA)]

    assert metrics.segment_predictions(events, [segment]) == []


def test_onset_latency_is_measured_from_the_start_of_the_segment():
    # The cue flips to BOTTOM at 1000; the stream only reports it at 1300.
    events = [(0, CAMERA), (1000, CAMERA), (1300, BOTTOM), (2000, BOTTOM)]

    records = metrics.segment_predictions(events, [_segment(1000, 2000, BOTTOM)])

    assert records[0]["onset_latency_ms"] == 300.0


def test_onset_is_zero_when_the_state_was_already_correct_at_the_segment_start():
    events = [(0, BOTTOM), (500, BOTTOM), (1000, BOTTOM), (1500, CAMERA)]

    records = metrics.segment_predictions(events, [_segment(200, 1000, BOTTOM)])

    assert records[0]["onset_latency_ms"] == 0.0


def test_a_never_detected_segment_has_no_onset():
    events = [(0, CAMERA), (500, CAMERA), (1000, CAMERA)]

    records = metrics.segment_predictions(events, [_segment(0, 1000, BOTTOM)])

    assert records[0]["onset_latency_ms"] is None
    assert records[0]["pred_label"] == CAMERA


def test_a_segment_with_no_decided_coverage_is_an_abstention_not_an_error():
    events = [(0, UNCERTAIN), (500, UNCERTAIN), (1000, BOTTOM), (2000, BOTTOM)]
    segments = [_segment(0, 900, CAMERA), _segment(1000, 2000, BOTTOM)]

    scores = metrics.segment_metrics_from_predictions(
        metrics.segment_predictions(events, segments)
    )

    assert scores["n_segments"] == 2
    assert scores["n_segments_decided"] == 1
    assert scores["segment_uncertain_ratio"] == pytest.approx(0.5)
    # The abstained CAMERA segment did not become a CAMERA error.
    assert scores["accuracy"] == 1.0
    assert scores["confusion"][CAMERA][BOTTOM] == 0
    assert scores["confusion"][CAMERA][UNCERTAIN] == 1


def test_time_weighted_accuracy_ignores_abstained_time():
    events = [(0, UNCERTAIN), (500, UNCERTAIN), (1000, BOTTOM), (2000, BOTTOM)]
    segments = [_segment(0, 900, CAMERA), _segment(1000, 2000, BOTTOM)]

    weighted = metrics.segment_metrics_from_predictions(
        metrics.segment_predictions(events, segments)
    )["time_weighted"]

    assert weighted["total_ms"] == pytest.approx(1900.0)
    assert weighted["decided_ms"] == pytest.approx(1000.0)
    assert weighted["uncertain_time_ratio"] == pytest.approx(900 / 1900)
    assert weighted["accuracy"] == 1.0


def test_detection_rate_distinguishes_never_detected_from_never_recorded():
    events = [(0, CAMERA), (500, CAMERA), (1000, CAMERA)]

    scores = metrics.segment_metrics_from_predictions(
        metrics.segment_predictions(events, [_segment(0, 1000, BOTTOM)])
    )

    assert scores["detection_rate"][BOTTOM] == 0.0
    assert scores["detection_rate"][CAMERA] is None
    assert scores["onset_latency_ms"][BOTTOM]["p95"] is None


def test_scoring_no_segments_at_all_reports_nothing_measured():
    scores = metrics.segment_metrics_from_predictions([])

    assert scores["n_segments"] == 0
    assert scores["detection_rate"] == {CAMERA: None, BOTTOM: None}
    assert scores["onset_latency_ms"][CAMERA]["p50"] is None
    assert scores["time_weighted"]["total_ms"] == 0.0
    assert scores["time_weighted"]["uncertain_time_ratio"] == 0.0


def test_segment_metrics_is_the_two_step_pipeline_in_one_call():
    events = [(0, CAMERA), (1000, BOTTOM), (2000, CAMERA), (3000, CAMERA)]
    segments = [_segment(0, 1000, CAMERA), _segment(1000, 2000, BOTTOM)]

    combined = metrics.segment_metrics(events, segments)
    stepwise = metrics.segment_metrics_from_predictions(
        metrics.segment_predictions(events, segments)
    )

    assert combined == stepwise
    assert combined["macro_f1"] == 1.0


def test_segment_label_dataclasses_are_accepted_alongside_dicts():
    segment = SegmentLabel(
        participant_id="p9", session_id="s1", start_ms=0, end_ms=1000, label=BOTTOM
    )

    records = metrics.segment_predictions([(0, BOTTOM), (1000, CAMERA)], [segment])

    assert records[0]["participant_id"] == "p9"
    assert records[0]["pred_label"] == BOTTOM


# ==========================================================================
# metrics: per-participant aggregation
# ==========================================================================


def _per_user_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"participant_id": "p1", "label": CAMERA, "pred_label": CAMERA, "latency_ms": 10.0},
            {"participant_id": "p1", "label": BOTTOM, "pred_label": BOTTOM, "latency_ms": 20.0},
            {"participant_id": "p2", "label": CAMERA, "pred_label": BOTTOM, "latency_ms": 30.0},
            {"participant_id": "p2", "label": BOTTOM, "pred_label": UNCERTAIN, "latency_ms": 40.0},
        ]
    )


@pytest.mark.parametrize("missing", ["label", "pred_label", "participant_id"])
def test_per_user_metrics_names_the_column_it_is_missing(missing):
    frame = _per_user_frame().drop(columns=[missing])

    with pytest.raises(ValueError, match=missing):
        metrics.per_user_metrics(frame)


def test_the_gate_reads_the_worst_participant_not_the_pooled_mean():
    result = metrics.per_user_metrics(_per_user_frame())
    summary = result.attrs["summary"]

    assert list(result["participant_id"]) == ["p1", "p2"]
    assert summary["n_participants"] == 2
    assert summary["macro_f1_min"] == 0.0
    assert summary["macro_f1_max"] == 1.0
    assert summary["macro_f1_min"] < summary["macro_f1_mean"]
    assert metrics.per_user_summary(result) == summary


def test_a_single_participant_has_zero_spread_not_nan():
    frame = _per_user_frame()
    only_p1 = metrics.per_user_metrics(frame[frame["participant_id"] == "p1"])

    summary = metrics.per_user_summary(only_p1)

    assert summary["n_participants"] == 1
    assert summary["macro_f1_std"] == 0.0


def test_an_unmeasured_per_user_column_summarises_to_none():
    frame = _per_user_frame().assign(latency_ms=float("nan"))

    summary = metrics.per_user_metrics(frame).attrs["summary"]

    assert summary["latency_p95_ms_min"] is None
    assert summary["latency_p95_ms_mean"] is None


# ==========================================================================
# buckets: the ten doc 19 rules and their cut points
# ==========================================================================


def _assign(rows: List[Dict[str, Any]], thresholds=None) -> pd.DataFrame:
    return buckets.assign_buckets(pd.DataFrame(rows), thresholds)


def _fires(name: str, row: Dict[str, Any], thresholds=None) -> bool:
    frame = _assign([row], thresholds)
    return bool(frame[buckets.BUCKET_COLUMN_PREFIX + name].iloc[0])


@pytest.mark.parametrize(
    "name, row, expected",
    [
        pytest.param("glasses", {"glasses": True}, True, id="glasses-true"),
        pytest.param("glasses", {"glasses": False}, False, id="glasses-false"),
        pytest.param("backlight", {"q_backlight_ratio": 1.6}, True, id="backlight-at-1.6"),
        pytest.param("backlight", {"q_backlight_ratio": 1.5999}, False, id="backlight-just-under"),
        pytest.param("small_face", {"q_face_area_ratio": 0.0299}, True, id="small-face-under"),
        pytest.param("small_face", {"q_face_area_ratio": 0.030}, False, id="small-face-at-cut"),
        pytest.param("small_face", {"q_face_area_ratio": 0.0}, False, id="small-face-no-bbox"),
        pytest.param(
            "head_down_eyes_camera",
            {"head_pitch": -0.20, "label": CAMERA},
            True,
            id="head-down-at-cut",
        ),
        pytest.param(
            "head_down_eyes_camera",
            {"head_pitch": -0.199, "label": CAMERA},
            False,
            id="head-down-just-above",
        ),
        pytest.param(
            "eyes_down_head_straight",
            {"head_pitch": 0.10, "label": BOTTOM},
            True,
            id="eyes-down-at-cut",
        ),
        pytest.param(
            "eyes_down_head_straight",
            {"head_pitch": -0.10, "label": BOTTOM},
            True,
            id="eyes-down-at-negative-cut",
        ),
        pytest.param(
            "eyes_down_head_straight",
            {"head_pitch": 0.1001, "label": BOTTOM},
            False,
            id="eyes-down-just-over",
        ),
        pytest.param(
            "partial_occlusion", {"q_landmark_visibility": 0.994}, True, id="occlusion-under"
        ),
        pytest.param(
            "partial_occlusion", {"q_landmark_visibility": 0.995}, False, id="occlusion-at-cut"
        ),
        pytest.param(
            "partial_occlusion",
            {"q_landmark_visibility": 1.0, "q_touches_border": True},
            True,
            id="occlusion-border",
        ),
        pytest.param("camera_off_center", {"camera_position": "bottom"}, True, id="camera-bottom"),
        pytest.param("camera_off_center", {"camera_position": "side_left"}, True, id="camera-side"),
        pytest.param(
            "camera_off_center", {"camera_position": "top_center"}, False, id="camera-top-center"
        ),
        pytest.param(
            "camera_off_center", {"camera_position": "TOP_CENTER"}, False, id="camera-uppercase"
        ),
        pytest.param("low_light", {"q_face_brightness": 59.9}, True, id="dark-face"),
        pytest.param("low_light", {"q_face_brightness": 60.0}, False, id="brightness-at-cut"),
        pytest.param(
            "low_light", {"q_face_brightness": 200.0, "lighting": "Dim"}, True, id="dim-metadata"
        ),
        pytest.param(
            "low_light",
            {"q_face_brightness": 200.0, "lighting": "normal"},
            False,
            id="normal-metadata",
        ),
    ],
)
def test_each_bucket_fires_at_its_cut_point_and_not_on_the_near_miss(name, row, expected):
    assert _fires(name, row) is expected


@pytest.mark.parametrize(
    "bucket, row",
    [
        ("head_down_eyes_camera", {"head_pitch": -0.9, "label": BOTTOM}),
        ("eyes_down_head_straight", {"head_pitch": 0.0, "label": CAMERA}),
    ],
)
def test_the_two_single_class_buckets_are_defined_by_ground_truth(bucket, row):
    # The geometry matches; the label does not, so this is not the situation
    # doc 19 names and the bucket must stay empty.
    assert _fires(bucket, row) is False


def test_leaning_back_is_relative_to_the_participants_own_median_depth():
    rows = [
        {"participant_id": "near", "head_depth_proxy": depth, "face_valid": True}
        for depth in (1.0, 1.0, 1.0, 1.15, 1.149)
    ] + [
        # A different capture scale entirely: absolutely further, relatively not.
        {"participant_id": "far", "head_depth_proxy": depth, "face_valid": True}
        for depth in (10.0, 10.0, 10.0, 10.0)
    ]

    frame = _assign(rows)

    assert list(frame["bucket_leaning_back"]) == [
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    assert frame.loc[3, "ctx_depth_ratio"] == pytest.approx(1.15)


def test_frames_within_the_transition_window_of_a_label_change_are_bucketed():
    rows = [
        {"participant_id": "p1", "session_id": "s1", "t_ms": t, "label": label}
        for t, label in ((0, CAMERA), (1000, CAMERA), (2000, BOTTOM), (3000, BOTTOM))
    ]

    frame = _assign(rows)

    # The change point sits at the midpoint 1500, so the two frames around it
    # are 500 ms away and the outer two are 1500 ms away.
    assert list(frame["ctx_ms_to_label_change"]) == [1500.0, 500.0, 500.0, 1500.0]
    assert list(frame["bucket_fast_transition"]) == [False, True, True, False]


def test_a_guard_band_does_not_read_as_two_separate_label_changes():
    rows = [
        {"participant_id": "p1", "session_id": "s1", "t_ms": t, "label": label}
        for t, label in ((0, CAMERA), (1000, IGNORE), (2000, BOTTOM))
    ]

    frame = buckets.add_context_columns(pd.DataFrame(rows))

    # One change, at the midpoint of the two *decided* frames -- not one on
    # each side of the IGNORE row.
    assert list(frame["ctx_ms_to_label_change"]) == [1000.0, 0.0, 1000.0]


def test_a_recording_with_no_label_change_has_no_transition_frames():
    rows = [
        {"participant_id": "p1", "session_id": "s1", "t_ms": t, "label": CAMERA}
        for t in (0, 100, 200)
    ]

    frame = _assign(rows)

    assert list(frame["ctx_ms_to_label_change"]) == [math.inf] * 3
    assert not frame["bucket_fast_transition"].any()


def test_the_transition_baseline_is_per_recording_not_across_takes():
    rows = [
        {"participant_id": "p1", "session_id": "a", "t_ms": 0, "label": CAMERA},
        {"participant_id": "p1", "session_id": "a", "t_ms": 100, "label": CAMERA},
        {"participant_id": "p1", "session_id": "b", "t_ms": 0, "label": BOTTOM},
        {"participant_id": "p1", "session_id": "b", "t_ms": 100, "label": BOTTOM},
    ]

    frame = buckets.add_context_columns(pd.DataFrame(rows))

    # Two single-class takes: the seam between them is not a gaze change.
    assert list(frame["ctx_ms_to_label_change"]) == [math.inf] * 4


def test_buckets_are_not_mutually_exclusive():
    row = {
        "glasses": True,
        "q_backlight_ratio": 2.0,
        "q_face_area_ratio": 0.005,
        "q_face_brightness": 40.0,
        "label": CAMERA,
    }

    frame = _assign([row])
    fired = [column for column in buckets.bucket_columns(frame) if frame[column].iloc[0]]

    assert set(fired) == {
        "bucket_glasses",
        "bucket_backlight",
        "bucket_small_face",
        "bucket_low_light",
    }


def test_a_table_missing_the_bucket_columns_lands_in_no_bucket_instead_of_raising():
    rows = [
        {"participant_id": "p1", "session_id": "s1", "t_ms": t, "label": CAMERA} for t in (0, 100)
    ]

    frame = _assign(rows)

    assert not frame[buckets.bucket_columns(frame)].to_numpy(dtype=bool).any()


def test_thresholds_are_live_rather_than_baked_into_the_module_level_rules():
    row = {"q_backlight_ratio": 1.7}

    assert _fires("backlight", row) is True
    assert _fires("backlight", row, buckets.BucketThresholds(backlight_ratio=2.0)) is False


def test_assign_buckets_leaves_the_callers_frame_untouched():
    original = pd.DataFrame([{"participant_id": "p1", "t_ms": 0, "label": CAMERA, "glasses": True}])
    before = original.copy(deep=True)

    buckets.assign_buckets(original)

    pd.testing.assert_frame_equal(original, before)


def test_a_table_concatenated_from_several_takes_keeps_its_duplicate_index():
    rows = [
        {"participant_id": "p1", "session_id": "s1", "t_ms": t, "label": label}
        for t, label in ((0, CAMERA), (1000, CAMERA), (2000, BOTTOM), (3000, BOTTOM))
    ]
    frame = pd.DataFrame(rows, index=[0, 1, 0, 1])

    out = buckets.assign_buckets(frame)

    assert list(out.index) == [0, 1, 0, 1]
    assert list(out["bucket_fast_transition"]) == [False, True, True, False]


def test_context_columns_exist_even_when_there_are_no_frames():
    out = buckets.add_context_columns(pd.DataFrame(columns=["label", "t_ms", "participant_id"]))

    assert "ctx_ms_to_label_change" in out.columns
    assert "ctx_depth_ratio" in out.columns
    assert out.empty


def test_the_rule_book_documents_exactly_the_rules_that_run():
    assert list(buckets.BUCKET_RULES) == list(buckets.BUCKETS)
    assert len(buckets.BUCKETS) == 10
    assert list(buckets.bucket_rules_table()["bucket"]) == list(buckets.BUCKETS)


def test_bucket_columns_are_prefixed_and_listed_in_rule_order():
    frame = _assign([{"label": CAMERA}])

    assert buckets.bucket_columns(frame) == [
        buckets.BUCKET_COLUMN_PREFIX + name for name in buckets.BUCKETS
    ]
    # A bucket must never overwrite the metadata column it was derived from.
    assert "glasses" not in buckets.bucket_columns(frame)


# ==========================================================================
# buckets: the report
# ==========================================================================


def _bucket_report_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"participant_id": "p1", "t_ms": 0, "label": CAMERA, "pred_label": CAMERA, "glasses": False},
            {"participant_id": "p1", "t_ms": 100, "label": CAMERA, "pred_label": CAMERA, "glasses": False},
            {"participant_id": "p1", "t_ms": 200, "label": BOTTOM, "pred_label": BOTTOM, "glasses": False},
            {"participant_id": "p1", "t_ms": 300, "label": BOTTOM, "pred_label": BOTTOM, "glasses": False},
            {"participant_id": "p2", "t_ms": 0, "label": CAMERA, "pred_label": BOTTOM, "glasses": True},
            {"participant_id": "p2", "t_ms": 100, "label": BOTTOM, "pred_label": CAMERA, "glasses": True},
        ]
    )


def test_the_report_leads_with_an_all_row_whose_delta_is_zero():
    report = buckets.bucket_report(_bucket_report_frame())

    assert report.loc[0, "bucket"] == "ALL"
    assert report.loc[0, "n"] == 6
    assert report.loc[0, "macro_f1_delta"] == 0.0
    assert list(report["bucket"])[1:] == list(buckets.BUCKETS)


def test_a_bucket_is_reported_as_a_delta_against_the_whole_run():
    report = buckets.bucket_report(_bucket_report_frame()).set_index("bucket")

    assert report.loc["ALL", "macro_f1"] == pytest.approx(2 / 3)
    assert report.loc["glasses", "n"] == 2
    assert report.loc["glasses", "macro_f1"] == 0.0
    assert report.loc["glasses", "macro_f1_delta"] == pytest.approx(-2 / 3)


def test_an_unexercised_bucket_is_kept_with_zero_n_and_nan_metrics():
    report = buckets.bucket_report(_bucket_report_frame()).set_index("bucket")

    assert report.loc["backlight", "n"] == 0
    assert math.isnan(report.loc["backlight", "macro_f1"])
    assert math.isnan(report.loc["backlight", "uncertain_ratio"])


def test_the_report_counts_the_class_balance_inside_each_bucket():
    report = buckets.bucket_report(_bucket_report_frame()).set_index("bucket")

    assert report.loc["ALL", "n_camera"] == 3
    assert report.loc["ALL", "n_bottom"] == 3
    assert report.loc["glasses", "n_camera"] == 1


def test_the_report_refuses_to_run_without_predictions():
    frame = _bucket_report_frame().drop(columns=["pred_label"])

    with pytest.raises(ValueError, match="pred_label"):
        buckets.bucket_report(frame)


def test_an_already_bucketed_frame_is_not_re_derived():
    frame = buckets.assign_buckets(_bucket_report_frame())
    frame["bucket_backlight"] = True

    report = buckets.bucket_report(frame).set_index("bucket")

    assert report.loc["backlight", "n"] == 6


def test_bucket_coverage_answers_did_we_record_this_case_at_all():
    coverage = buckets.bucket_coverage(_bucket_report_frame())

    assert coverage["glasses"] == 2
    assert coverage["backlight"] == 0
    assert set(coverage) == set(buckets.BUCKETS)


# ==========================================================================
# sanity: the schemas.py pitch ordering
# ==========================================================================


def _pitch_rows(participant: str, backbone: str, camera, bottom, valid: bool = True):
    rows = []
    for value in camera:
        rows.append(
            {
                "participant_id": participant,
                "backbone": backbone,
                "label": CAMERA,
                "gaze_pitch": value,
                "face_valid": valid,
            }
        )
    for value in bottom:
        rows.append(
            {
                "participant_id": participant,
                "backbone": backbone,
                "label": BOTTOM,
                "gaze_pitch": value,
                "face_valid": valid,
            }
        )
    return rows


_TIGHT_CAMERA = [0.38, 0.39, 0.40, 0.41, 0.42]
_TIGHT_BOTTOM = [-0.42, -0.41, -0.40, -0.39, -0.38]


@pytest.mark.parametrize("missing", ["label", "gaze_pitch"])
def test_the_ordering_check_names_the_column_it_needs(missing):
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)).drop(
        columns=[missing]
    )

    with pytest.raises(ValueError, match=missing):
        sanity.pitch_ordering_report(frame)


def test_bottom_more_negative_than_camera_is_what_ok_means():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))

    report = sanity.pitch_ordering_report(frame)

    assert len(report) == 1
    assert report.loc[0, "delta_gaze_pitch"] > 0
    assert report.loc[0, "delta_gaze_pitch"] == pytest.approx(0.8)
    assert report.loc[0, "verdict"] == sanity.VERDICT_OK


def test_a_pooled_mean_can_hide_one_inverted_participant():
    frame = pd.DataFrame(
        _pitch_rows("good", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)
        + _pitch_rows(
            "flipped",
            "geom",
            [-0.32, -0.31, -0.30, -0.29, -0.28],
            [-0.22, -0.21, -0.20, -0.19, -0.18],
        )
    )

    # Pooled, CAMERA still sits above BOTTOM and the run reads as fine.
    pooled_delta = (
        frame[frame["label"] == CAMERA]["gaze_pitch"].mean()
        - frame[frame["label"] == BOTTOM]["gaze_pitch"].mean()
    )
    assert pooled_delta > 0

    report = sanity.pitch_ordering_report(frame).set_index("participant_id")

    assert report.loc["good", "verdict"] == sanity.VERDICT_OK
    assert report.loc["flipped", "verdict"] == sanity.VERDICT_INVERTED
    assert report.attrs["summary"]["passed"] is False


def test_each_backbone_is_judged_on_its_own():
    frame = pd.DataFrame(
        _pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)
        + _pitch_rows("p1", "l2cs", _TIGHT_BOTTOM, _TIGHT_CAMERA)
    )

    report = sanity.pitch_ordering_report(frame).set_index("backbone")

    assert len(report) == 2
    assert report.loc["geom", "verdict"] == sanity.VERDICT_OK
    assert report.loc["l2cs", "verdict"] == sanity.VERDICT_INVERTED


def test_a_zero_delta_is_inverted_not_ok():
    values = [-0.2, -0.1, 0.0, 0.1, 0.2]
    frame = pd.DataFrame(_pitch_rows("p1", "geom", values, values))

    report = sanity.pitch_ordering_report(frame)

    assert report.loc[0, "delta_gaze_pitch"] == 0.0
    assert report.loc[0, "verdict"] == sanity.VERDICT_INVERTED


@pytest.mark.parametrize(
    "camera, bottom, verdict, why",
    [
        pytest.param(
            [0.4] * 4,
            [-0.4] * 9,
            sanity.VERDICT_INSUFFICIENT,
            "too few CAMERA frames to say anything, even with the ordering right",
            id="insufficient-beats-ok",
        ),
        pytest.param(
            [-0.4] * 4,
            [0.4] * 9,
            sanity.VERDICT_INSUFFICIENT,
            "too few frames outranks a clear inversion",
            id="insufficient-beats-inverted",
        ),
        pytest.param(
            [-1.0, -0.5, 0.0, 0.5, 1.0],
            [-0.9, -0.4, 0.1, 0.6, 1.1],
            sanity.VERDICT_INVERTED,
            "inverted outranks weak: the sign is checked before the effect size",
            id="inverted-beats-weak",
        ),
        pytest.param(
            [-1.0, -0.5, 0.0, 0.5, 1.0],
            [-1.1, -0.6, -0.1, 0.4, 0.9],
            sanity.VERDICT_WEAK,
            "ordered, but by a fraction of the within-class spread",
            id="weak-beats-ok",
        ),
        pytest.param(
            _TIGHT_CAMERA,
            _TIGHT_BOTTOM,
            sanity.VERDICT_OK,
            "separated far beyond the noise",
            id="ok",
        ),
    ],
)
def test_the_verdict_ladder_is_applied_in_order(camera, bottom, verdict, why):
    frame = pd.DataFrame(_pitch_rows("p1", "geom", camera, bottom))

    assert sanity.pitch_ordering_report(frame).loc[0, "verdict"] == verdict, why


def test_the_sample_floor_is_inclusive():
    enough = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))
    one_short = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA[:4], _TIGHT_BOTTOM))

    assert sanity.MIN_SAMPLES_PER_CLASS == 5
    assert sanity.pitch_ordering_report(enough).loc[0, "verdict"] != sanity.VERDICT_INSUFFICIENT
    assert sanity.pitch_ordering_report(one_short).loc[0, "verdict"] == sanity.VERDICT_INSUFFICIENT


def test_the_effect_size_floor_is_inclusive():
    frame = pd.DataFrame(
        _pitch_rows("p1", "geom", [-1.0, -0.5, 0.0, 0.5, 1.0], [-1.1, -0.6, -0.1, 0.4, 0.9])
    )
    measured = float(sanity.pitch_ordering_report(frame).loc[0, "effect_size"])

    at_the_floor = sanity.pitch_ordering_report(frame, min_effect_size=measured)
    just_above = sanity.pitch_ordering_report(frame, min_effect_size=measured * 1.0001)

    assert at_the_floor.loc[0, "verdict"] == sanity.VERDICT_OK
    assert just_above.loc[0, "verdict"] == sanity.VERDICT_WEAK


def test_a_class_with_no_measurable_spread_is_never_reported_ok():
    # Perfectly separated but zero within-class variance: the effect size is
    # undefined, and an undefined effect size is not evidence that the adapter
    # is right.
    frame = pd.DataFrame(_pitch_rows("p1", "geom", [0.1] * 5, [-0.1] * 5))

    report = sanity.pitch_ordering_report(frame)

    assert report.loc[0, "pooled_std"] == 0.0
    assert math.isnan(report.loc[0, "effect_size"])
    assert report.loc[0, "verdict"] == sanity.VERDICT_WEAK


def test_invalid_frames_are_dropped_before_the_means_are_taken():
    frame = pd.DataFrame(
        _pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)
        + _pitch_rows("p1", "geom", [-5.0] * 20, [], valid=False)
    )

    assert sanity.pitch_ordering_report(frame).loc[0, "verdict"] == sanity.VERDICT_OK
    assert (
        sanity.pitch_ordering_report(frame, require_valid=False).loc[0, "verdict"]
        == sanity.VERDICT_INVERTED
    )


def test_head_pitch_is_reported_for_context_and_carries_no_verdict():
    rows = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))
    # doc 19's "eyes down but head straight": head pitch ordered the other way.
    rows["head_pitch"] = [-0.3] * 5 + [0.0] * 5

    report = sanity.pitch_ordering_report(rows)

    assert report.loc[0, "mean_gaze_pitch_camera"] > report.loc[0, "mean_gaze_pitch_bottom"]
    assert report.loc[0, "mean_head_pitch_camera"] < report.loc[0, "mean_head_pitch_bottom"]
    assert report.loc[0, "verdict"] == sanity.VERDICT_OK


def test_a_table_without_grouping_keys_still_produces_one_row():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)).drop(
        columns=["participant_id", "backbone"]
    )

    report = sanity.pitch_ordering_report(frame)

    assert len(report) == 1
    assert "participant_id" not in report.columns
    assert report.loc[0, "verdict"] == sanity.VERDICT_OK


def test_missing_head_pitch_is_reported_as_nan_rather_than_raising():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))

    report = sanity.pitch_ordering_report(frame)

    assert math.isnan(report.loc[0, "mean_head_pitch_camera"])
    assert report.loc[0, "verdict"] == sanity.VERDICT_OK


# ==========================================================================
# sanity: the roll-up
# ==========================================================================


def test_one_inverted_row_fails_the_whole_check_and_is_named():
    frame = pd.DataFrame(
        _pitch_rows("good", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM)
        + _pitch_rows("flipped", "geom", _TIGHT_BOTTOM, _TIGHT_CAMERA)
    )

    summary = sanity.pitch_ordering_report(frame).attrs["summary"]

    assert summary["passed"] is False
    assert summary["n_rows"] == 2
    assert summary["verdicts"] == {sanity.VERDICT_OK: 1, sanity.VERDICT_INVERTED: 1}
    assert summary["inverted"] == [
        {"participant_id": "flipped", "backbone": "geom", "delta_gaze_pitch": pytest.approx(-0.8)}
    ]


def test_insufficient_rows_do_not_pass_by_never_having_been_measured():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", [0.4] * 3, [-0.4] * 3))

    summary = sanity.pitch_ordering_report(frame).attrs["summary"]

    # Advisory: nothing is INVERTED so nothing failed, but the count says the
    # check never actually ran on anybody.
    assert summary["passed"] is True
    assert summary["n_rows"] == 1
    assert summary["n_measured"] == 0


def test_an_empty_report_does_not_pass():
    summary = sanity.pitch_ordering_summary(pd.DataFrame())

    assert summary["passed"] is False
    assert summary["n_rows"] == 0
    assert summary["inverted"] == []


def test_the_report_carries_its_own_summary():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))
    report = sanity.pitch_ordering_report(frame)

    assert report.attrs["summary"] == sanity.pitch_ordering_summary(report)


def test_the_empty_summary_has_the_same_keys_as_a_real_one():
    frame = pd.DataFrame(_pitch_rows("p1", "geom", _TIGHT_CAMERA, _TIGHT_BOTTOM))
    populated = sanity.pitch_ordering_summary(sanity.pitch_ordering_report(frame))

    empty = sanity.pitch_ordering_summary(pd.DataFrame())

    assert set(empty) == set(populated)


# ==========================================================================
# sanity: the abstention triage report
# ==========================================================================


def _reason_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"pred_label": CAMERA, "invalid_reason": None, "uncertain_reason": None},
            {"pred_label": BOTTOM, "invalid_reason": None, "uncertain_reason": None},
            {"pred_label": UNCERTAIN, "invalid_reason": "NO_FACE", "uncertain_reason": None},
            {"pred_label": None, "invalid_reason": None, "uncertain_reason": "LOW_MARGIN"},
            {"pred_label": float("nan"), "invalid_reason": None, "uncertain_reason": None},
        ]
    )


def test_the_reason_report_refuses_to_run_without_predictions():
    with pytest.raises(ValueError, match="pred_label"):
        sanity.uncertain_reason_report(pd.DataFrame({"uncertain_reason": ["X"]}))


def test_only_undecided_rows_are_triaged_however_they_are_spelled():
    report = sanity.uncertain_reason_report(_reason_frame()).set_index("reason")

    assert report["n"].sum() == 3
    assert report.loc["NO_FACE", "n"] == 1
    assert report.loc["LOW_MARGIN", "n"] == 1
    assert report.loc["UNSPECIFIED", "n"] == 1


def test_the_classifier_reason_wins_over_the_preprocess_one():
    frame = pd.DataFrame(
        [{"pred_label": UNCERTAIN, "invalid_reason": "NO_FACE", "uncertain_reason": "LOW_MARGIN"}]
    )

    report = sanity.uncertain_reason_report(frame)

    assert list(report["reason"]) == ["LOW_MARGIN"]


def test_the_ratio_is_over_every_frame_not_only_the_abstentions():
    report = sanity.uncertain_reason_report(_reason_frame())

    # Three of five frames abstained, so the ratios sum to 0.6, not to 1.0.
    assert report["ratio_of_all_frames"].tolist() == pytest.approx([1 / 5] * 3)
    assert report["ratio_of_all_frames"].sum() == pytest.approx(3 / 5)
