"""Contracts of the gaze evidence the coach and review agents read.

* OTHER decisions carry a presenter-centric direction (``GAZE_DIRECTIONS``);
* frames become 1 s slices by majority vote (UNCERTAIN below the vote share,
  UNMEASURED without enough usable frames, gaps recorded as UNMEASURED);
* ratios divide by measured time only; runs merge consecutive equal states;
* issues use the agents' common evaluator format, with the thresholds of
  ``evidence.yaml``; GAZE_UNMEASURABLE is not actionable;
* the take summary, previous-take delta and intervention outcome have the
  shapes the review agent and the coach history expect.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from vision.calibration.references import ReferenceAnchorClassifier, direction_of, gaze_offset
from vision.config import CalibrationConfig, EvidenceConfig
from vision.evidence.gaze import (
    GAZE_COACH_ACTION,
    GazeEvidenceRecorder,
    GazeFrame,
    GazeSample,
    GazeSlicer,
    GazeTimeline,
    compare_summaries,
    evaluate_gaze,
    intervention_outcome,
    take_summary,
)
from vision.schemas import GAZE_DIRECTIONS, CalibrationSample, FrameObservation, GazeVector, HeadPose

CFG = EvidenceConfig()


# ==========================================================================
# Direction of an OTHER look
# ==========================================================================


def _cloud(label, centre, n=16):
    return [
        CalibrationSample(
            label=label,
            gaze=GazeVector(math.radians(centre[0] + 0.3 * ((i % 3) - 1)), math.radians(centre[1] + 0.3 * ((i % 5) - 2)), 0.9),
            head_pose=HeadPose(),
        )
        for i in range(n)
    ]


@pytest.fixture(scope="module")
def clf():
    samples = _cloud("CAMERA", (0.5, 18.0)) + _cloud("SCREEN", (0.8, 11.0)) + _cloud("BOTTOM", (0.6, 4.0))
    model, quality = ReferenceAnchorClassifier.fit(samples, CalibrationConfig())
    assert quality.ok, quality
    return model


def _decide(clf, yaw, pitch):
    obs = FrameObservation(frame_id=1, t_ms=0, face_confidence=1.0, face_valid=True, head_pose=HeadPose())
    return clf.decide(obs, GazeVector(math.radians(yaw), math.radians(pitch), 0.9))


@pytest.mark.parametrize(
    "yaw, pitch, expected",
    [
        # Raw frame: a look toward the image right (yaw > 0) is the presenter's LEFT.
        (40.0, 11.0, "LEFT"),
        (-40.0, 11.0, "RIGHT"),
        (0.8, 45.0, "UP"),
        (0.8, -30.0, "DOWN"),
        (35.0, -30.0, "DOWN_LEFT"),
        (-35.0, 45.0, "UP_RIGHT"),
    ],
)
def test_an_other_look_says_which_way_it_went(clf, yaw, pitch, expected):
    d = _decide(clf, yaw, pitch)
    assert d.label == "OTHER"
    assert d.direction == expected
    assert d.to_dict()["direction"] == expected


def test_inside_the_screen_area_has_no_direction_and_a_zero_offset(clf):
    d = _decide(clf, 0.8, 11.0)
    assert d.label == "SCREEN" and d.direction is None
    assert d.offset_deg == (0.0, 0.0)
    assert "direction" not in d.to_dict()


def test_offset_is_presenter_centric_and_measured_from_the_area_edge(clf):
    right, up = gaze_offset(clf.model, 40.0, 11.0)
    assert right < 0 and up == 0.0  # toward the presenter's left
    assert direction_of((0.0, 0.0)) is None
    assert direction_of((1.0, 0.0)) == "RIGHT" and direction_of((-1.0, -1.0)) == "DOWN_LEFT"
    assert set(GAZE_DIRECTIONS) == {direction_of((math.cos(a), math.sin(a))) for a in [k * math.pi / 4 for k in range(8)]}


# ==========================================================================
# Slices
# ==========================================================================


def frames(state, t0, n, *, step=125, direction=None, reliability=1.0, issues=()):
    return [GazeFrame(t0 + i * step, state, direction if state == "OTHER" else None, reliability, issues) for i in range(n)]


def slice_all(fs, cfg=CFG, t_end=None):
    slicer = GazeSlicer(cfg)
    out = []
    for f in fs:
        out.extend(slicer.push(f))
    out.extend(slicer.flush(t_end))
    return out


def test_slices_sit_on_a_grid_from_the_first_frame_and_take_the_majority():
    fs = frames("CAMERA", 1000, 6) + frames("BOTTOM", 1750, 2)
    (s,) = slice_all(fs)
    assert (s.t_ms, s.duration_ms, s.state, s.frames) == (1000, 1000, "CAMERA", 8)
    assert s.confidence == pytest.approx(6 / 8)


def test_a_split_slice_is_uncertain_and_a_thin_one_unmeasured():
    split = frames("CAMERA", 0, 4) + frames("BOTTOM", 500, 4)
    assert slice_all(split)[0].state == "UNCERTAIN"
    thin = frames("CAMERA", 0, 3) + frames("UNMEASURED", 375, 5)
    s = slice_all(thin)[0]
    assert (s.state, s.confidence) == ("UNMEASURED", 0.0)


def test_a_gap_without_frames_is_recorded_as_unmeasured_seconds():
    fs = frames("CAMERA", 0, 8) + frames("CAMERA", 3000, 8)
    states = [(s.t_ms, s.state) for s in slice_all(fs)]
    assert states == [(0, "CAMERA"), (1000, "UNMEASURED"), (2000, "UNMEASURED"), (3000, "CAMERA")]


def test_an_other_slice_keeps_the_most_common_direction_and_frequent_issues():
    fs = frames("OTHER", 0, 5, direction="LEFT", issues=("SECOND_FACE",)) + frames("OTHER", 625, 3, direction="UP")
    s = slice_all(fs)[0]
    assert (s.state, s.direction) == ("OTHER", "LEFT")
    assert s.issues == ("SECOND_FACE",)  # in 5 of 8 frames


def test_samples_round_trip_through_the_wire_dict():
    s = GazeSample(1000, 1000, "OTHER", "DOWN_LEFT", 0.875, 0.7, ("TOO_FAR",), 8)
    d = s.to_dict()
    assert set(d) == {"t_ms", "duration_ms", "state", "direction", "confidence", "reliability", "issues", "frames"}
    assert GazeSample.from_dict(d) == s


# ==========================================================================
# Timeline
# ==========================================================================


def seconds(*spec, reliability=1.0):
    """("CAMERA", 3), ("OTHER:LEFT", 2) ... -> contiguous 1 s samples from t=0."""
    out, t = [], 0
    for state, n in spec:
        state, _, direction = state.partition(":")
        for _ in range(n):
            out.append(GazeSample(t, 1000, state, direction or None, 1.0, reliability))
            t += 1000
    return GazeTimeline(out)


def test_ratios_divide_by_measured_time_only():
    tl = seconds(("CAMERA", 2), ("UNMEASURED", 2), ("BOTTOM", 1), ("UNCERTAIN", 1))
    st = tl.stats(6000, 6000)
    assert (st["tracked_ms"], st["measured_ms"], st["unmeasured_ms"], st["uncertain_ms"]) == (6000, 3000, 2000, 1000)
    assert st["ratio"]["CAMERA"] == pytest.approx(2 / 3)
    assert st["coverage"] == pytest.approx(0.5)


def test_a_window_clips_partial_samples():
    tl = seconds(("CAMERA", 4), ("BOTTOM", 2))
    st = tl.stats(5500, 2000)
    assert st["state_ms"]["CAMERA"] == 500 and st["state_ms"]["BOTTOM"] == 1500


def test_runs_merge_other_across_directions_and_break_on_gaps():
    tl = seconds(("CAMERA", 2), ("OTHER:LEFT", 2), ("OTHER:UP", 1))
    runs = tl.runs()
    assert [(r.state, r.duration_ms, r.direction) for r in runs] == [("CAMERA", 2000, None), ("OTHER", 3000, "LEFT")]
    gappy = GazeTimeline([GazeSample(0, 1000, "CAMERA"), GazeSample(2000, 1000, "CAMERA")])
    assert len(gappy.runs()) == 2


# ==========================================================================
# Coach: issues
# ==========================================================================


def test_reading_the_script_becomes_an_issue_after_three_seconds():
    assert evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 2)), 7000, CFG) == []
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 4)), 9000, CFG)
    assert issue["issue_type"] == "GAZE_ON_SCRIPT"
    assert set(issue) == {"evaluator", "issue_type", "t_ms", "severity", "confidence", "persistence_sec", "evidence", "actionable"}
    assert issue["evaluator"] == "gaze" and issue["actionable"] is True
    assert issue["severity"] == pytest.approx(4000 / CFG.script_full_ms)
    assert issue["persistence_sec"] == 4.0
    assert issue["evidence"]["bottom_ratio_5s"] == pytest.approx(0.8)
    assert GAZE_COACH_ACTION[issue["issue_type"]] == "LOOK_AT_CAMERA"


def test_looking_away_carries_its_direction():
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("OTHER:DOWN_LEFT", 3)), 8000, CFG)
    assert issue["issue_type"] == "GAZE_AWAY"
    assert issue["evidence"]["direction"] == "DOWN_LEFT"


def test_low_eye_contact_over_the_long_window():
    tl = seconds(*[("SCREEN", 4), ("CAMERA", 1)] * 4)
    issues = {i["issue_type"]: i for i in evaluate_gaze(tl, 20000, CFG)}
    low = issues["GAZE_LOW_EYE_CONTACT"]
    assert low["evidence"]["camera_ratio_30s"] == pytest.approx(0.2)
    assert low["severity"] == pytest.approx((0.3 - 0.2) / 0.3, abs=1e-4)


def test_an_unusable_gaze_is_reported_but_not_actionable():
    issues = evaluate_gaze(seconds(("CAMERA", 2), ("UNMEASURED", 4)), 6000, CFG)
    (unmeasurable,) = [i for i in issues if i["issue_type"] == "GAZE_UNMEASURABLE"]
    assert unmeasurable["actionable"] is False
    assert unmeasurable["evidence"]["coverage_5s"] == pytest.approx(0.2)


def test_low_reliability_alone_makes_the_gaze_unusable():
    tl = seconds(("CAMERA", 6), reliability=0.3)
    assert any(i["issue_type"] == "GAZE_UNMEASURABLE" for i in evaluate_gaze(tl, 6000, CFG))


def test_issues_are_most_severe_first_and_evaluated_as_of_t():
    tl = seconds(("SCREEN", 25), ("BOTTOM", 10))
    issues = evaluate_gaze(tl, 35000, CFG)
    assert [i["severity"] for i in issues] == sorted((i["severity"] for i in issues), reverse=True)
    early = evaluate_gaze(tl, 20000, CFG)  # replay: as it stood at 20 s
    assert early[0]["issue_type"] == "GAZE_ON_SCREEN"


# ==========================================================================
# Review: summary, delta, intervention outcome
# ==========================================================================


def test_take_summary_has_statistics_segments_and_problem_segments():
    tl = seconds(("CAMERA", 5), ("BOTTOM", 4), ("CAMERA", 3), ("OTHER:LEFT", 3), ("UNMEASURED", 1))
    s = take_summary(tl, CFG)
    assert s["eye_contact_ratio"] == pytest.approx(8 / 15, abs=1e-4)  # reported to 4 decimals
    assert s["other_direction_ms"]["LEFT"] == 3000
    assert [p["issue_type"] for p in s["problem_segments"]] == ["GAZE_ON_SCRIPT", "GAZE_AWAY"]
    assert s["problem_segments"][1]["direction"] == "LEFT"
    assert s["longest_run_ms"]["CAMERA"] == 5000 and s["episodes"]["CAMERA"] == 2
    assert len(s["segments"]) == 5


def test_previous_take_delta():
    prev = take_summary(seconds(("CAMERA", 2), ("BOTTOM", 8)), CFG)
    cur = take_summary(seconds(("CAMERA", 6), ("BOTTOM", 4)), CFG)
    delta = compare_summaries(prev, cur)
    assert delta["eye_contact_ratio"] == {"previous": 0.2, "current": 0.6, "delta": 0.4}
    assert delta["state_ratio"]["BOTTOM"]["delta"] == pytest.approx(-0.4)


def test_intervention_outcome_in_the_coach_history_shape():
    tl = seconds(("BOTTOM", 10), ("CAMERA", 10))  # feedback at 10 s, presenter looks up
    out = intervention_outcome(tl, 10000, "GAZE_ON_SCRIPT", CFG)
    assert out["intervention"] == {"type": "LOOK_AT_CAMERA", "issue_type": "GAZE_ON_SCRIPT", "t_ms": 10000}
    assert out["before"]["bottom_ratio_5s"] == 1.0
    assert out["after_5s"]["bottom_ratio_5s"] == 0.0
    assert out["effective"] is True
    pending = intervention_outcome(seconds(("BOTTOM", 10)), 10000, "GAZE_ON_SCRIPT", CFG)
    assert pending["effective"] is None
    with pytest.raises(ValueError):
        intervention_outcome(tl, 0, "GAZE_UNMEASURABLE", CFG)


def test_the_recorder_builds_the_timeline_from_decisions():
    from vision.schemas import GazeDecision

    rec = GazeEvidenceRecorder(CFG)
    for i in range(24):
        rec.record(GazeDecision(t_ms=i * 125, frame_id=i, label="CAMERA", p_camera=0.9, p_bottom=0.05, face_valid=True))
    rec.flush()
    assert [s.state for s in rec.timeline.samples] == ["CAMERA", "CAMERA", "CAMERA"]
    assert rec.summary()["eye_contact_ratio"] == 1.0
    rec.reset()
    assert rec.timeline.samples == []


def test_config_windows_name_the_evidence_keys():
    cfg = dataclasses.replace(CFG, short_window_ms=2500)
    (issue,) = evaluate_gaze(seconds(("CAMERA", 5), ("BOTTOM", 4)), 9000, cfg)
    assert "bottom_ratio_2.5s" in issue["evidence"]
