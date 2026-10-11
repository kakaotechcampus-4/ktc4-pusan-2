"""Contracts of the device side of the gaze evidence (frames -> 1 s slices).

* OTHER decisions carry a presenter-centric direction (``GAZE_DIRECTIONS``);
* frames become 1 s slices by majority vote (UNCERTAIN below the vote share,
  UNMEASURED without enough usable frames, gaps recorded as UNMEASURED);
* the session's recorder builds the timeline from frame decisions.

What reads the slices (timeline, issues, summaries) is the server core and is
tested in ``tests/unit/test_core.py``.
"""

from __future__ import annotations

import math

import pytest

from gaze_lab.calibration.references import ReferenceAnchorClassifier, direction_of, gaze_offset
from gaze_lab.config import CalibrationConfig, EvidenceConfig
from gaze_lab.evidence.gaze import GazeEvidenceRecorder, GazeFrame, GazeSlicer
from gaze_lab.schemas import GAZE_DIRECTIONS, CalibrationSample, FrameObservation, GazeVector, HeadPose

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


def test_the_recorder_builds_the_timeline_from_decisions():
    from gaze_lab.schemas import GazeDecision

    rec = GazeEvidenceRecorder(CFG)
    for i in range(24):
        rec.record(GazeDecision(t_ms=i * 125, frame_id=i, label="CAMERA", p_camera=0.9, p_bottom=0.05, face_valid=True))
    rec.flush()
    assert [s.state for s in rec.timeline.samples] == ["CAMERA", "CAMERA", "CAMERA"]
    assert rec.summary()["eye_contact_ratio"] == 1.0
    rec.reset()
    assert rec.timeline.samples == []
