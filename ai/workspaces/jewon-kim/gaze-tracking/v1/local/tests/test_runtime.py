"""Contract tests for ``vision.runtime``: session lifecycle, placement, versioning.

What this file protects.

* ``VisionSession`` as a state machine -- which stage may be called when, what a
  doc 5-2 retry clears and what it deliberately does not (the frame counter
  keeps running, so ids stay unique across a whole take), and that an injected
  pipeline/backbone stays the caller's property.
* The failure policy: an invalid frame never reaches the backbone, a raising
  backbone degrades one frame to UNCERTAIN/BACKBONE_FAILED and stays
  diagnosable through ``last_backbone_error``, and ``process_frame`` before
  calibration refuses instead of guessing.
* ``latency_percentile`` as NEAREST-RANK over a bounded recent window, and
  ``dump_events`` as the doc 6-2 seven-key JSONL contract (plus the debug keys).
* ``CameraPlacementCheck``: TOP is the only supported geometry, ``ok`` aliases
  ``supported``, the rejection reasons are ordered by root cause, and the
  pitch/yaw signs follow ``schemas.py`` read on the RAW un-mirrored frame.
* ``runtime.version``: the three wire constants, which backbone name a stamp
  carries, and "a missing commit must never stop a take".

Almost nothing here needs pixels: a stub preprocess pipeline and a scripted
backbone stand in, so every test is deterministic, camera-free and fast.
Hand-built angles appear only where the point is one specific code path (a
two-cluster calibration through the quality gate, a cue geometry through the
placement verdict).  No accuracy number is measured off invented data.
"""

from __future__ import annotations

import json
import math
import subprocess
from types import SimpleNamespace
from typing import List, Optional, Tuple

import numpy as np
import pytest

from vision.backbones.base import GazeBackbone
from vision.runtime import session as session_mod
from vision.runtime import version as version_mod
from vision.runtime.placement import (
    TARGET_CAMERA,
    TARGET_SCREEN,
    CameraPlacement,
    CameraPlacementCheck,
    PlacementReason,
)
from vision.runtime.session import VisionSession, collect_calibration
from vision.runtime.version import (
    _GIT_TIMEOUT_S,
    CLASSIFIER_VERSION,
    COMMIT_ENV_VAR,
    MODEL_VERSION,
    _run_git,
    current_ai_version,
    git_commit,
)
from vision.schemas import (
    AiVersion,
    CalibrationStatus,
    FrameObservation,
    GazeLabel,
    GazeState,
    GazeStateEvent,
    GazeVector,
    HeadPose,
    InvalidReason,
)
from vision.temporal.smoother import TEMPORAL_RULE_VERSION

#: A frame the stub pipeline never looks at; only its identity travels.
FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

#: Calibration anchors.  schemas.py: BOTTOM must sit MORE NEGATIVE in pitch.
CAMERA_PITCH = math.radians(-2.0)
BOTTOM_PITCH = math.radians(-18.0)

#: doc 6-2 event shape -- exactly these keys go on the wire.
EVENT_CONTRACT_KEYS = {
    "type",
    "model_version",
    "t_ms",
    "label",
    "confidence",
    "continuous_duration_ms",
    "face_valid",
}
EVENT_DEBUG_KEYS = {"is_transition", "smoothed_p_camera", "smoothed_p_bottom"}


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class StubPipeline:
    """Stands in for ``PreprocessPipeline``: no MediaPipe, no pixels, full log.

    Only the two members ``VisionSession`` uses are implemented, which is also
    the point: it pins how small that surface is.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[int, int]] = []
        self.closed = 0
        self.face_valid = True
        self.invalid_reason: Optional[str] = None

    def process_bgr(self, bgr: np.ndarray, frame_id: int, t_ms: int) -> FrameObservation:
        self.calls.append((int(frame_id), int(t_ms)))
        return FrameObservation(
            frame_id=int(frame_id),
            t_ms=int(t_ms),
            face_confidence=0.99 if self.face_valid else 0.0,
            face_valid=bool(self.face_valid),
            head_pose=HeadPose(),
            invalid_reason=self.invalid_reason,
            image_size=(640, 480),
        )

    def close(self) -> None:
        self.closed += 1

    @property
    def frame_ids(self) -> List[int]:
        return [frame_id for frame_id, _ in self.calls]


class ScriptedBackbone(GazeBackbone):
    """A backbone whose answer the test sets directly (doc 3-2 interface only)."""

    name = "scripted"
    version = "test-1"

    def __init__(self, yaw: float = 0.0, pitch: float = 0.0, confidence: float = 0.9) -> None:
        self.yaw = yaw
        self.pitch = pitch
        self.confidence = confidence
        self.error: Optional[Exception] = None
        self.calls = 0
        self.closed = 0

    def predict(
        self,
        face_crop,
        left_eye_crop,
        right_eye_crop,
        head_pose,
        *,
        landmarks=None,
        blendshapes=None,
        image_size=None,
    ) -> GazeVector:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return GazeVector(
            gaze_yaw=float(self.yaw),
            gaze_pitch=float(self.pitch),
            confidence=float(self.confidence),
            backbone=self.name,
        )

    def close(self) -> None:
        self.closed += 1


class FakeClock:
    """``perf_counter`` scripted so every frame has an exact, known latency."""

    def __init__(self, latencies_ms) -> None:
        self._values: List[float] = []
        for i, ms in enumerate(latencies_ms):
            self._values.extend([float(i), float(i) + ms / 1000.0])
        self._i = 0

    def perf_counter(self) -> float:
        value = self._values[self._i]
        self._i += 1
        return value


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def stubs():
    """A fresh (pipeline, backbone) pair, both owned by the test."""
    return StubPipeline(), ScriptedBackbone()


@pytest.fixture
def session(fresh_cfg, stubs):
    """An uncalibrated session over the stubs; closed at teardown."""
    pipeline, backbone = stubs
    sess = VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline)
    yield sess
    sess.close()


def _calibrate(sess: VisionSession, backbone: ScriptedBackbone, per_class: int = 12, t0: int = 0):
    """Feed two tight, well-separated gaze clusters and fit (doc 5-1).

    Hand-built on purpose: the point is the calibration state machine, not a
    model score.  The clusters obey the schemas.py ordering (BOTTOM below
    CAMERA in pitch) so the advisory INVERTED_PITCH warning stays silent.
    """
    t_ms = t0
    cues = ((GazeLabel.CAMERA.value, CAMERA_PITCH), (GazeLabel.BOTTOM.value, BOTTOM_PITCH))
    for cue, pitch in cues:
        for i in range(per_class):
            jitter = ((i % 5) - 2) * math.radians(0.2)
            backbone.yaw = jitter
            backbone.pitch = pitch + jitter
            sess.add_calibration_frame(FRAME, cue, t_ms)
            t_ms += 125
    return sess.finish_calibration(), t_ms


def _add_cloud(
    check: CameraPlacementCheck,
    target: str,
    *,
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    n: int = 10,
    jitter_deg: float = 0.3,
    confidence: float = 1.0,
) -> None:
    """A deterministic, tight cloud of gaze samples around one direction."""
    for i in range(n):
        d_yaw = ((i % 5) - 2) * jitter_deg / 2.0
        d_pitch = ((i % 3) - 1) * jitter_deg / 2.0
        check.add_sample(
            target,
            GazeVector(
                gaze_yaw=math.radians(yaw_deg + d_yaw),
                gaze_pitch=math.radians(pitch_deg + d_pitch),
                confidence=confidence,
                backbone="scripted",
            ),
        )


def _placement(cfg, cam: Tuple[float, float], scr: Tuple[float, float], **kwargs):
    """Verdict for a camera cloud at ``cam`` and a screen cloud at ``scr`` (degrees)."""
    check = CameraPlacementCheck(cfg.placement)
    _add_cloud(check, TARGET_CAMERA, yaw_deg=cam[0], pitch_deg=cam[1], **kwargs)
    _add_cloud(check, TARGET_SCREEN, yaw_deg=scr[0], pitch_deg=scr[1], **kwargs)
    return check.evaluate()


# ==========================================================================
# Session -- stage ordering
# ==========================================================================


def test_scoring_a_frame_before_calibration_refuses_instead_of_guessing(session):
    with pytest.raises(RuntimeError) as excinfo:
        session.process_frame(FRAME, 0)

    assert "calibration" in str(excinfo.value)
    assert session.frames_processed == 0  # refused before any preprocessing


def test_new_calibration_cues_need_an_explicit_retry_once_a_model_exists(session, stubs):
    _pipeline, backbone = stubs
    _calibrate(session, backbone)

    with pytest.raises(RuntimeError, match="reset_calibration"):
        session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 9_000)

    session.reset_calibration()
    assert session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 9_000) is True


@pytest.mark.parametrize("cue", ["IGNORE", GazeLabel.IGNORE, "SIDEWAYS", "", "camera_"])
def test_a_cue_the_classifier_cannot_train_on_is_rejected_loudly(session, cue):
    with pytest.raises(ValueError):
        session.add_calibration_frame(FRAME, cue, 0)

    assert session.calibration_counts == {"CAMERA": 0, "BOTTOM": 0}


@pytest.mark.parametrize("cue", ["camera", " Camera ", GazeLabel.CAMERA])
def test_cue_labels_are_canonicalised_before_they_are_stored(session, cue):
    assert session.add_calibration_frame(FRAME, cue, 0) is True

    assert session.calibration_counts["CAMERA"] == 1
    assert session.calibration_samples[0].label == GazeLabel.CAMERA.value


def test_finish_calibration_keeps_a_model_it_has_marked_retry_required(fresh_cfg, stubs):
    """RETRY_REQUIRED is the caller's decision; the model is still scoreable."""
    pipeline, backbone = stubs
    sess = VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline)
    # Three per class: enough to compute statistics, far below min_samples_per_class.
    quality, t_ms = _calibrate(sess, backbone, per_class=3)

    assert quality.status == CalibrationStatus.RETRY_REQUIRED.value
    assert quality.ok is False
    assert sess.is_calibrated is True
    assert sess.calibration_quality is quality
    # is_calibrated answers only "can this session score a frame" -- so it can.
    event, decision = sess.process_frame(FRAME, t_ms)
    assert decision.label in {state.value for state in GazeState}
    assert event.label in {state.value for state in GazeState}
    sess.close()


def test_a_cue_with_no_counterpart_leaves_the_session_unable_to_score(session, stubs):
    _pipeline, backbone = stubs
    backbone.pitch = CAMERA_PITCH
    for i in range(12):
        session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, i * 125)

    quality = session.finish_calibration()

    assert quality.reason == "NOT_ENOUGH_SAMPLES"
    assert session.is_calibrated is False
    assert session.classifier is None
    with pytest.raises(RuntimeError):
        session.process_frame(FRAME, 5_000)


def test_reset_calibration_clears_the_take_but_never_the_frame_counter(session, stubs):
    pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    session.process_frame(FRAME, t_ms)
    frames_before = session.frames_processed

    assert session.events and session.latency_percentile() > 0.0

    session.reset_calibration()

    assert session.is_calibrated is False
    assert session.classifier is None
    assert session.calibration_quality is None
    assert session.calibration_samples == []
    assert session.calibration_counts == {"CAMERA": 0, "BOTTOM": 0}
    assert session.events == []
    assert session.latency_percentile() == 0.0
    assert session.last_decision is None
    assert session.last_event_emitted is False
    assert session.smoother.state == GazeState.UNCERTAIN.value
    # Deliberately not reset: ids must stay unique across a retry (doc 5-2).
    assert session.frames_processed == frames_before
    session.preview(FRAME, t_ms + 125)
    assert pipeline.frame_ids[-1] == frames_before


def test_every_stage_draws_from_one_frame_id_space(session, stubs):
    """preview / placement / calibration / live share the counter, so no two
    frames of one take can collide on ``frame_id`` -- retry included."""
    pipeline, backbone = stubs

    session.preview(FRAME, 0)
    session.add_placement_frame(FRAME, TARGET_CAMERA, 125)
    session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 250)
    session.reset_calibration()
    session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 375)
    _calibrate(session, backbone, per_class=3, t0=500)
    session.process_frame(FRAME, 5_000)

    ids = pipeline.frame_ids
    assert ids == list(range(len(ids)))
    assert session.frames_processed == len(ids)


def test_preview_shows_a_frame_without_advancing_any_stage(session, stubs):
    pipeline, backbone = stubs
    backbone.pitch = CAMERA_PITCH

    obs, gaze = session.preview(FRAME, 40)

    assert obs is session.last_observation
    assert gaze is session.last_gaze is not None
    assert session.calibration_counts == {"CAMERA": 0, "BOTTOM": 0}
    assert session.placement_counts == {TARGET_CAMERA: 0, TARGET_SCREEN: 0}
    assert session.events == []
    assert session.last_event is None
    assert session.smoother.state == GazeState.UNCERTAIN.value
    assert pipeline.frame_ids == [0]  # it did consume an id: the frame was real


def test_placement_and_calibration_buffers_reset_independently(session, stubs):
    _pipeline, _backbone = stubs
    session.add_placement_frame(FRAME, TARGET_CAMERA, 0)
    session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 125)

    session.reset_calibration()
    assert session.placement_counts[TARGET_CAMERA] == 1
    assert session.calibration_counts["CAMERA"] == 0

    session.add_calibration_frame(FRAME, GazeLabel.BOTTOM.value, 250)
    session.reset_placement()
    assert session.placement_counts[TARGET_CAMERA] == 0
    assert session.placement_result is None
    assert session.calibration_counts["BOTTOM"] == 1


def test_finish_placement_reports_the_verdict_without_enforcing_it(session, stubs):
    """An unsupported placement is advisory: calibration must stay reachable."""
    _pipeline, backbone = stubs
    for i in range(10):
        backbone.pitch = math.radians(10.0)  # screen ABOVE the lens -> camera BOTTOM
        session.add_placement_frame(FRAME, TARGET_SCREEN, i * 125)
        backbone.pitch = 0.0
        session.add_placement_frame(FRAME, TARGET_CAMERA, i * 125 + 60)

    result = session.finish_placement()

    assert result.supported is False
    assert session.placement_result is result
    assert session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 5_000) is True


def test_warmup_costs_backbone_calls_but_no_frame_ids(session, stubs):
    pipeline, backbone = stubs

    session.warmup(3)

    assert backbone.calls == 3
    assert pipeline.calls == []
    assert session.frames_processed == 0


# ==========================================================================
# Session -- failure policy
# ==========================================================================


def test_a_raising_backbone_abstains_for_one_frame_and_stays_diagnosable(session, stubs):
    _pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    backbone.error = ValueError("no iris landmarks")

    event, decision = session.process_frame(FRAME, t_ms)

    assert decision.label == GazeState.UNCERTAIN.value
    assert decision.uncertain_reason == InvalidReason.BACKBONE_FAILED.value
    assert decision.face_valid is False  # "was this frame usable", not "a face was seen"
    assert decision.p_camera == decision.p_bottom == 0.5
    assert session.last_gaze is None
    assert session.last_backbone_error == "ValueError: no iris landmarks"
    assert event.face_valid is False

    # The take survives it: the next frame decides normally again.
    backbone.error = None
    backbone.pitch = CAMERA_PITCH
    _event, recovered = session.process_frame(FRAME, t_ms + 125)
    assert recovered.uncertain_reason is None
    assert recovered.label == GazeState.CAMERA.value


def test_a_backbone_that_dies_during_calibration_is_counted_not_swallowed(session, stubs):
    _pipeline, backbone = stubs
    backbone.error = RuntimeError("checkpoint unloaded")

    assert session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 0) is False

    assert session.calibration_counts["CAMERA"] == 0
    assert session.rejected_frames == {InvalidReason.BACKBONE_FAILED.value: 1}
    assert session.last_backbone_error == "RuntimeError: checkpoint unloaded"


def test_an_unusable_frame_is_never_offered_to_the_backbone(session, stubs):
    pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    calls_after_calibration = backbone.calls
    pipeline.face_valid = False
    pipeline.invalid_reason = InvalidReason.EYES_CLOSED.value

    _event, decision = session.process_frame(FRAME, t_ms)

    assert backbone.calls == calls_after_calibration
    assert decision.uncertain_reason == InvalidReason.EYES_CLOSED.value
    assert decision.label == GazeState.UNCERTAIN.value
    assert session.last_gaze is None


def test_a_rejected_calibration_frame_is_reported_under_its_preprocess_reason(session, stubs):
    pipeline, _backbone = stubs
    pipeline.face_valid = False
    pipeline.invalid_reason = InvalidReason.FACE_TOO_SMALL.value

    accepted = session.add_calibration_frame(FRAME, GazeLabel.BOTTOM.value, 0)

    assert accepted is False
    assert session.rejected_frames == {InvalidReason.FACE_TOO_SMALL.value: 1}


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_a_non_finite_gaze_never_becomes_a_calibration_sample(session, stubs, bad):
    _pipeline, backbone = stubs
    backbone.pitch = bad

    assert session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 0) is False
    assert session.calibration_samples == []
    assert session.rejected_frames[InvalidReason.BACKBONE_FAILED.value] == 1


def test_a_backbone_that_returns_nan_is_as_diagnosable_as_one_that_raises(session, stubs):
    """A NaN-returning backbone is as dead as a raising one, so it leaves a trace.

    Both are bucketed as BACKBONE_FAILED, but only the raising branch used to
    record anything, which left the run the module docstring warns about --
    every frame abstaining -- reading exactly like a user who left the room.
    """
    _pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    backbone.pitch = math.nan

    _event, decision = session.process_frame(FRAME, t_ms)

    assert decision.uncertain_reason == InvalidReason.BACKBONE_FAILED.value
    assert session.last_backbone_error is not None


def test_the_returned_calibration_samples_cannot_rewrite_what_was_fitted(session, stubs):
    """The copy is deep: CalibrationSample holds a mutable GazeVector.

    A shallow ``list(...)`` handed the caller the very vectors a doc 5-2 retry
    would refit on, so an overlay that "normalised" a returned angle silently
    moved the anchors of the next calibration.
    """
    _pipeline, backbone = stubs
    quality, _t_ms = _calibrate(session, backbone)

    for sample in session.calibration_samples:
        sample.gaze.gaze_pitch = 1.234

    refit = session.finish_calibration()

    assert refit.camera_centroid == quality.camera_centroid


def test_the_head_pose_of_a_returned_calibration_sample_is_a_copy_too(session, stubs):
    """The deep copy covers every mutable field of a sample, not just the gaze."""
    _pipeline, backbone = stubs
    session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 0)

    handed_out = session.calibration_samples[0]
    handed_out.head_pose.yaw = 1.234
    handed_out.gaze.gaze_yaw = 5.678

    kept = session.calibration_samples[0]
    assert kept.head_pose.yaw == 0.0
    assert kept.gaze.gaze_yaw == 0.0


def test_last_gaze_is_cleared_when_a_frame_produced_no_estimate(session, stubs):
    """An overlay that keeps drawing the last face makes a covered lens look tracked."""
    pipeline, backbone = stubs
    backbone.pitch = CAMERA_PITCH
    session.preview(FRAME, 0)
    assert session.last_gaze is not None

    pipeline.face_valid = False
    pipeline.invalid_reason = InvalidReason.NO_FACE.value
    obs, gaze = session.preview(FRAME, 125)

    assert gaze is None
    assert session.last_gaze is None
    assert session.last_observation is obs


# ==========================================================================
# Session -- latency
# ==========================================================================


def test_latency_percentile_is_zero_before_any_frame_is_measured(session):
    assert session.latency_percentile() == 0.0
    assert session.latency_percentile(50.0) == 0.0


@pytest.mark.parametrize(
    "percentile, expected",
    [
        (0.0, 10.0),  # rank clamps up to 1
        (10.0, 10.0),
        (50.0, 50.0),  # an interpolated quantile would say 55.0
        (95.0, 100.0),  # ...and 95.5 here
        (100.0, 100.0),
        (150.0, 100.0),  # rank clamps down to the last sample, no IndexError
    ],
)
def test_latency_percentile_reports_a_latency_that_was_actually_measured(
    fresh_cfg, stubs, monkeypatch, percentile, expected
):
    pipeline, backbone = stubs
    sess = VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline)
    _quality, t_ms = _calibrate(sess, backbone)
    measured = [30.0, 10.0, 90.0, 40.0, 100.0, 20.0, 70.0, 50.0, 80.0, 60.0]
    monkeypatch.setattr(
        session_mod, "time", SimpleNamespace(perf_counter=FakeClock(measured).perf_counter)
    )

    for i in range(len(measured)):
        sess.process_frame(FRAME, t_ms + i * 125)

    assert sess.latency_percentile(percentile) == pytest.approx(expected)
    assert sess.last_decision.latency_ms == pytest.approx(measured[-1])
    sess.close()


def test_the_latency_window_forgets_frames_older_than_its_history(fresh_cfg, stubs, monkeypatch):
    """A live overlay reports the recent window; the whole take belongs in a table."""
    pipeline, backbone = stubs
    sess = VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline)
    _quality, t_ms = _calibrate(sess, backbone)
    n = session_mod._LATENCY_HISTORY + 76
    measured = [float(i + 1) for i in range(n)]
    monkeypatch.setattr(
        session_mod, "time", SimpleNamespace(perf_counter=FakeClock(measured).perf_counter)
    )

    for i in range(n):
        sess.process_frame(FRAME, t_ms + i * 125)

    assert sess.latency_percentile(0.0) == pytest.approx(77.0)
    assert sess.latency_percentile(100.0) == pytest.approx(float(n))
    sess.close()


# ==========================================================================
# Session -- events out
# ==========================================================================


def test_events_collects_exactly_the_frames_that_went_on_the_wire(session, stubs):
    _pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    emitted = 0

    for i in range(24):
        backbone.pitch = CAMERA_PITCH if i < 12 else BOTTOM_PITCH
        event, _decision = session.process_frame(FRAME, t_ms + i * 125)
        assert isinstance(event, GazeStateEvent)  # an event on every frame (doc 6-2)
        if session.last_event_emitted:
            emitted += 1

    assert emitted == len(session.events)
    assert [e.t_ms for e in session.events] == sorted(e.t_ms for e in session.events)
    assert any(e.is_transition for e in session.events)


def test_dumped_events_carry_the_doc_6_2_keys_and_nothing_else(session, stubs, tmp_path):
    _pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    for i in range(24):
        backbone.pitch = CAMERA_PITCH if i < 12 else BOTTOM_PITCH
        session.process_frame(FRAME, t_ms + i * 125)

    target = session.dump_events(tmp_path / "nested" / "events.jsonl")

    assert target.is_file()  # parent directories are created for the caller
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(session.events)
    for line, event in zip(lines, session.events):
        payload = json.loads(line)
        assert set(payload) == EVENT_CONTRACT_KEYS
        assert payload["type"] == "GAZE_STATE"
        assert payload["model_version"] == MODEL_VERSION
        assert payload["label"] == event.label
        assert isinstance(payload["t_ms"], int)
        assert isinstance(payload["face_valid"], bool)
        assert payload["confidence"] == pytest.approx(round(event.confidence, 4))


def test_debug_dump_adds_the_non_contract_keys_without_dropping_any(session, stubs, tmp_path):
    _pipeline, backbone = stubs
    _quality, t_ms = _calibrate(session, backbone)
    session.process_frame(FRAME, t_ms)

    target = session.dump_events(tmp_path / "debug.jsonl", debug=True)

    payload = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
    assert set(payload) == EVENT_CONTRACT_KEYS | EVENT_DEBUG_KEYS
    assert payload["smoothed_p_camera"] + payload["smoothed_p_bottom"] == pytest.approx(
        1.0, abs=1e-4
    )


def test_dumping_a_session_that_emitted_nothing_writes_an_empty_file(session, tmp_path):
    target = session.dump_events(tmp_path / "empty.jsonl")

    assert target.read_text(encoding="utf-8") == ""


def test_the_wire_version_comes_from_the_session_not_from_the_schema_default(fresh_cfg, stubs):
    pipeline, backbone = stubs
    sess = VisionSession(
        fresh_cfg, backbone=backbone, pipeline=pipeline, model_version="gaze_test_v9"
    )
    _quality, t_ms = _calibrate(sess, backbone)

    event, _decision = sess.process_frame(FRAME, t_ms)

    assert event.model_version == "gaze_test_v9"
    assert event.to_dict()["model_version"] == "gaze_test_v9"
    sess.close()


def test_collect_calibration_replays_a_cue_timeline_into_one_report(session, stubs):
    _pipeline, backbone = stubs
    frames = [(FRAME, GazeLabel.CAMERA.value, i * 125) for i in range(12)]
    frames += [(FRAME, GazeLabel.BOTTOM.value, 1500 + i * 125) for i in range(12)]

    # The backbone answers per frame, so let it follow the cue as it is asked.
    original = ScriptedBackbone.predict

    def cued(self, *args, **kwargs):
        self.pitch = CAMERA_PITCH if self.calls < 12 else BOTTOM_PITCH
        return original(self, *args, **kwargs)

    backbone.predict = cued.__get__(backbone, ScriptedBackbone)
    quality = collect_calibration(session, frames)

    assert quality is session.calibration_quality
    assert session.calibration_counts == {"CAMERA": 12, "BOTTOM": 12}
    assert session.is_calibrated is True


# ==========================================================================
# Session -- lifecycle
# ==========================================================================


def test_close_never_releases_collaborators_the_caller_injected(session, stubs):
    pipeline, backbone = stubs

    session.close()
    session.close()

    assert pipeline.closed == 0
    assert backbone.closed == 0


def test_close_releases_what_the_session_built_itself_exactly_once(fresh_cfg, monkeypatch):
    pipeline, backbone = StubPipeline(), ScriptedBackbone()
    monkeypatch.setattr(session_mod, "PreprocessPipeline", lambda cfg: pipeline)
    monkeypatch.setattr(session_mod, "build_backbone", lambda cfg: backbone)
    sess = VisionSession(fresh_cfg)

    sess.close()
    sess.close()

    assert pipeline.closed == 1
    assert backbone.closed == 1


def test_the_context_manager_closes_the_session_on_exit(fresh_cfg, stubs):
    pipeline, backbone = stubs

    with VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline) as sess:
        assert sess.preview(FRAME, 0)[0].frame_id == 0

    with pytest.raises(RuntimeError, match="closed"):
        sess.preview(FRAME, 125)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda s: s.preview(FRAME, 0), id="preview"),
        pytest.param(lambda s: s.add_placement_frame(FRAME, TARGET_CAMERA, 0), id="add_placement"),
        pytest.param(lambda s: s.finish_placement(), id="finish_placement"),
        pytest.param(lambda s: s.add_calibration_frame(FRAME, "CAMERA", 0), id="add_calibration"),
        pytest.param(lambda s: s.finish_calibration(), id="finish_calibration"),
        pytest.param(lambda s: s.process_frame(FRAME, 0), id="process_frame"),
        pytest.param(lambda s: s.warmup(1), id="warmup"),
    ],
)
def test_a_closed_session_refuses_every_frame_facing_call(session, call):
    session.close()

    with pytest.raises(RuntimeError, match="closed"):
        call(session)


def test_repr_names_the_backbone_and_the_calibration_progress(session, stubs):
    _pipeline, _backbone = stubs
    session.add_calibration_frame(FRAME, GazeLabel.CAMERA.value, 0)

    text = repr(session)

    assert "scripted" in text
    assert "1C/0B" in text
    assert "calibrated=False" in text


def test_a_failing_backbone_build_does_not_leak_the_pipeline(fresh_cfg, monkeypatch):
    """A build that raises leaves nothing open: __init__ never returns an object.

    build_backbone raising is a documented path (CheckpointMissingError when the
    weights are not installed), and the caller has no session to close() -- so
    the MediaPipe graph built one line earlier has to be released right there.
    """
    pipeline = StubPipeline()
    monkeypatch.setattr(session_mod, "PreprocessPipeline", lambda cfg: pipeline)

    def boom(cfg):
        raise RuntimeError("checkpoint not found")

    monkeypatch.setattr(session_mod, "build_backbone", boom)

    with pytest.raises(RuntimeError):
        VisionSession(fresh_cfg)

    assert pipeline.closed == 1


def test_a_failing_backbone_build_never_closes_an_injected_pipeline(fresh_cfg, monkeypatch):
    """The cleanup on a failed build still respects ownership.

    An injected pipeline outlives the session that borrowed it (doc 23's
    Experiment 1 shares one landmarker graph), and a session that never came
    into existence has even less claim on it.
    """
    pipeline = StubPipeline()

    def boom(cfg):
        raise RuntimeError("checkpoint not found")

    monkeypatch.setattr(session_mod, "build_backbone", boom)

    with pytest.raises(RuntimeError):
        VisionSession(fresh_cfg, pipeline=pipeline)

    assert pipeline.closed == 0


def test_the_session_runs_the_real_preprocess_path_on_the_face_fixture(cfg, face_rgb):
    """One real frame through the shipped pipeline, to keep the stub honest."""
    pytest.importorskip("mediapipe")
    from vision.config import resolve_path
    from vision.preprocess.pipeline import PreprocessPipeline

    if not resolve_path(cfg.preprocess.landmarker_model_path).is_file():
        pytest.skip("ai/models/face_landmarker.task not installed")

    bgr = np.ascontiguousarray(face_rgb[:, :, ::-1])
    backbone = ScriptedBackbone(pitch=CAMERA_PITCH)
    with PreprocessPipeline(cfg) as pipeline:
        with VisionSession(cfg, backbone=backbone, pipeline=pipeline) as sess:
            obs, gaze = sess.preview(bgr, 0)

            assert obs.frame_id == 0
            assert obs.image_size == (face_rgb.shape[1], face_rgb.shape[0])
            assert obs.face_valid is True
            assert gaze is not None and gaze.backbone == "scripted"
        # The injected pipeline outlives the session that borrowed it.
        assert pipeline.process_bgr(bgr, 1, 33).frame_id == 1


# ==========================================================================
# Placement
# ==========================================================================


def test_a_screen_below_the_lens_is_the_supported_top_geometry(fresh_cfg):
    result = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, -10.0))

    assert result.placement == CameraPlacement.TOP.value
    assert result.supported is True
    assert result.ok is result.supported
    assert result.reason == PlacementReason.OK.value
    assert result.axis == "vertical"
    assert result.delta_pitch_deg < 0.0
    assert result.n_camera == result.n_screen == 10
    assert result.offset_deg == pytest.approx(10.0, abs=0.1)
    assert "above the screen" in result.hint
    assert CameraPlacement.TOP.value in result.summary()


@pytest.mark.parametrize(
    "screen_deg, expected, sign",
    [
        ((0.0, -10.0), CameraPlacement.TOP, "pitch_negative"),
        ((0.0, 10.0), CameraPlacement.BOTTOM, "pitch_positive"),
        ((10.0, 0.0), CameraPlacement.SIDE_RIGHT, "yaw_positive"),
        ((-10.0, 0.0), CameraPlacement.SIDE_LEFT, "yaw_negative"),
    ],
)
def test_the_verdict_follows_the_schemas_sign_convention(fresh_cfg, screen_deg, expected, sign):
    """pitch > 0 is UP and yaw > 0 is image-right on the RAW un-mirrored frame."""
    result = _placement(fresh_cfg, cam=(0.0, 0.0), scr=screen_deg)

    assert result.placement == expected.value
    assert result.supported is (expected is CameraPlacement.TOP)
    assert result.ok is result.supported
    if sign.startswith("pitch"):
        assert result.axis == "vertical"
        assert (result.delta_pitch_deg > 0) is sign.endswith("positive")
    else:
        assert result.axis == "horizontal"
        assert (result.delta_yaw_deg > 0) is sign.endswith("positive")


def test_placement_is_read_off_the_centroids_not_off_the_absolute_angles(fresh_cfg):
    """A user sitting off-axis still gets TOP: only the camera->screen delta counts."""
    result = _placement(fresh_cfg, cam=(12.0, 6.0), scr=(12.0, -4.0))

    assert result.placement == CameraPlacement.TOP.value
    assert result.camera_centroid_deg[1] == pytest.approx(6.0, abs=0.2)
    assert result.screen_centroid_deg[1] == pytest.approx(-4.0, abs=0.2)
    assert result.delta_pitch_deg == pytest.approx(-10.0, abs=0.2)


def test_rejection_reasons_are_ordered_by_root_cause(fresh_cfg):
    """NOT_ENOUGH_SAMPLES -> TARGETS_NOT_SEPARATED -> AMBIGUOUS_AXIS -> DISPLACEMENT_TOO_SMALL.

    Every case below also qualifies for each later reason, so what is asserted
    is the precedence, not merely the detection.
    """
    # Too few frames -- and the two cues are identical as well.
    too_few = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, 0.0), n=5)
    assert too_few.reason == PlacementReason.NOT_ENOUGH_SAMPLES.value

    # Enough frames, but the person never moved their eyes: no axis, no
    # displacement either.
    same = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, 0.0))
    assert same.reason == PlacementReason.TARGETS_NOT_SEPARATED.value

    # Separable, but diagonally offset AND below the displacement floor: the
    # ambiguous axis is the more specific complaint.
    diagonal = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(2.0, 2.0), jitter_deg=0.05)
    assert diagonal.reason == PlacementReason.AMBIGUOUS_AXIS.value
    assert diagonal.axis_dominance < fresh_cfg.placement.min_axis_dominance

    # Separable and unambiguously vertical, just too small to be a screen.
    tiny = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, -2.0), jitter_deg=0.05)
    assert tiny.reason == PlacementReason.DISPLACEMENT_TOO_SMALL.value
    assert abs(tiny.delta_pitch_deg) < fresh_cfg.placement.min_delta_deg


@pytest.mark.parametrize(
    "reason",
    [
        PlacementReason.NOT_ENOUGH_SAMPLES,
        PlacementReason.TARGETS_NOT_SEPARATED,
        PlacementReason.AMBIGUOUS_AXIS,
        PlacementReason.DISPLACEMENT_TOO_SMALL,
    ],
)
def test_every_rejection_is_inconclusive_unsupported_and_carries_a_hint(fresh_cfg, reason):
    cases = {
        PlacementReason.NOT_ENOUGH_SAMPLES: dict(cam=(0.0, 0.0), scr=(0.0, -10.0), n=5),
        PlacementReason.TARGETS_NOT_SEPARATED: dict(cam=(0.0, 0.0), scr=(0.0, 0.0)),
        PlacementReason.AMBIGUOUS_AXIS: dict(cam=(0.0, 0.0), scr=(7.0, 7.0), jitter_deg=0.05),
        PlacementReason.DISPLACEMENT_TOO_SMALL: dict(
            cam=(0.0, 0.0), scr=(0.0, -2.0), jitter_deg=0.05
        ),
    }
    result = _placement(fresh_cfg, **cases[reason])

    assert result.reason == reason.value
    assert result.placement == CameraPlacement.INCONCLUSIVE.value
    assert result.supported is False
    assert result.ok is False
    assert result.hint
    assert set(result.to_dict()) >= {"placement", "supported", "reason", "hint", "mode"}


def test_the_verdict_survives_a_check_that_never_saw_a_sample(fresh_cfg):
    check = CameraPlacementCheck(fresh_cfg.placement)

    result = check.evaluate()

    assert result.reason == PlacementReason.NOT_ENOUGH_SAMPLES.value
    assert result.n_camera == result.n_screen == 0
    assert result.delta_pitch_deg == 0.0
    assert result.delta_yaw_deg == 0.0
    assert result.camera_centroid_deg == [0.0, 0.0]
    assert result.offset_deg == 0.0


def test_only_one_cue_collected_is_still_not_enough(fresh_cfg):
    check = CameraPlacementCheck(fresh_cfg.placement)
    _add_cloud(check, TARGET_CAMERA, pitch_deg=0.0, n=20)

    result = check.evaluate()

    assert result.reason == PlacementReason.NOT_ENOUGH_SAMPLES.value
    assert (result.n_camera, result.n_screen) == (20, 0)


@pytest.mark.parametrize(
    "gaze, kept",
    [
        pytest.param(None, False, id="no_gaze"),
        pytest.param(GazeVector(math.nan, 0.0, 1.0), False, id="nan_yaw"),
        pytest.param(GazeVector(0.0, math.inf, 1.0), False, id="inf_pitch"),
        pytest.param(GazeVector(0.0, 0.0, 0.29), False, id="below_confidence"),
        pytest.param(GazeVector(0.0, 0.0, 0.30), True, id="exactly_at_confidence"),
        pytest.param(GazeVector(0.0, 0.0, 1.0), True, id="confident"),
    ],
)
def test_add_sample_keeps_only_frames_the_verdict_can_stand_on(fresh_cfg, gaze, kept):
    fresh_cfg.placement.min_sample_confidence = 0.30
    check = CameraPlacementCheck(fresh_cfg.placement)

    assert check.add_sample(TARGET_CAMERA, gaze) is kept
    assert check.count(TARGET_CAMERA) == int(kept)


@pytest.mark.parametrize("target", [" camera ", "screen", "Screen"])
def test_target_names_are_normalised(fresh_cfg, target):
    check = CameraPlacementCheck(fresh_cfg.placement)

    assert check.add_sample(target, GazeVector(0.0, 0.0, 1.0)) is True
    assert check.count(target) == 1


def test_an_unknown_target_is_a_programming_error_not_a_dropped_frame(fresh_cfg):
    check = CameraPlacementCheck(fresh_cfg.placement)

    with pytest.raises(ValueError, match="unknown placement target"):
        check.add_sample("SCREEN_CENTRE", GazeVector(0.0, 0.0, 1.0))


def test_reset_empties_both_cues(fresh_cfg):
    check = CameraPlacementCheck(fresh_cfg.placement)
    _add_cloud(check, TARGET_CAMERA, n=10)
    _add_cloud(check, TARGET_SCREEN, pitch_deg=-10.0, n=10)

    check.reset()

    assert check.count(TARGET_CAMERA) == check.count(TARGET_SCREEN) == 0
    assert check.evaluate().reason == PlacementReason.NOT_ENOUGH_SAMPLES.value


def test_geometric_mode_reaches_a_verdict_without_the_learned_path(fresh_cfg, monkeypatch):
    """The fallback exists so the check works without sklearn; prove it is used."""
    fresh_cfg.placement.mode = "geometric"

    def fail(*args, **kwargs):
        raise AssertionError("geometric mode must not fit a model")

    monkeypatch.setattr(CameraPlacementCheck, "_evaluate_learned", fail)
    result = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, -10.0))

    assert result.placement == CameraPlacement.TOP.value
    assert result.mode == "geometric"
    assert result.loo_accuracy == 0.0
    assert result.axis_dominance == 0.0
    assert result.separation > fresh_cfg.placement.min_separation


@pytest.mark.parametrize(
    "cam, scr, jitter, expected",
    [
        ((0.0, 0.0), (0.0, 0.0), 0.3, PlacementReason.TARGETS_NOT_SEPARATED),
        ((0.0, 0.0), (0.0, -2.0), 0.05, PlacementReason.DISPLACEMENT_TOO_SMALL),
    ],
)
def test_geometric_mode_rejects_on_spread_then_on_displacement(
    fresh_cfg, cam, scr, jitter, expected
):
    fresh_cfg.placement.mode = "geometric"

    result = _placement(fresh_cfg, cam=cam, scr=scr, jitter_deg=jitter)

    assert result.reason == expected.value
    assert result.mode == "geometric"
    assert result.loo_accuracy == 0.0


def test_a_perfectly_still_gaze_yields_a_finite_separation(fresh_cfg):
    """Zero within-cluster spread must not divide by zero or emit a JSON-breaking inf."""
    fresh_cfg.placement.mode = "geometric"

    still = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, -10.0), jitter_deg=0.0)

    assert math.isfinite(still.separation)
    assert still.separation == 1000.0
    assert still.placement == CameraPlacement.TOP.value

    identical = _placement(fresh_cfg, cam=(0.0, 0.0), scr=(0.0, 0.0), jitter_deg=0.0)
    assert identical.separation == 0.0
    assert identical.reason == PlacementReason.TARGETS_NOT_SEPARATED.value


# ==========================================================================
# Version
# ==========================================================================


def test_the_wire_version_constants_are_the_ones_downstream_parses():
    assert MODEL_VERSION == "gaze_v1.0.0"
    assert CLASSIFIER_VERSION == "per_user_lr_v1"
    assert TEMPORAL_RULE_VERSION == "gaze_temporal_v1.0"
    # The schema defaults and the runtime constants must not drift apart.
    event = GazeStateEvent(
        t_ms=0, label="CAMERA", confidence=1.0, continuous_duration_ms=0, face_valid=True
    )
    assert event.model_version == MODEL_VERSION
    assert AiVersion().gaze_classifier == CLASSIFIER_VERSION
    assert AiVersion().temporal_rule == TEMPORAL_RULE_VERSION


@pytest.mark.parametrize(
    "backbone_name, stamped",
    [("l2cs", "l2cs"), ("  GazeTR  ", "gazetr"), (None, "mediapipe_geom"), ("", "mediapipe_geom")],
)
def test_the_stamp_names_the_backbone_that_actually_ran(cfg, monkeypatch, backbone_name, stamped):
    """Experiment 1 builds several backbones against one config, so a stamp that
    read cfg.backbone.name would mislabel every row but one."""
    monkeypatch.setenv(COMMIT_ENV_VAR, "cafebabe1234")

    stamp = current_ai_version(cfg, backbone_name)

    assert cfg.backbone.name == "mediapipe_geom"
    assert stamp.gaze_backbone == stamped
    assert stamp.code_commit == "cafebabe1234"
    assert stamp.config_hash == cfg.hash()
    assert set(stamp.to_dict()) == {"ai_version"}


def test_a_session_stamps_its_own_backbone_not_the_configured_one(fresh_cfg, stubs, monkeypatch):
    monkeypatch.setenv(COMMIT_ENV_VAR, "0123456789ab")
    pipeline, backbone = stubs
    sess = VisionSession(fresh_cfg, backbone=backbone, pipeline=pipeline)

    stamp = sess.ai_version()

    assert fresh_cfg.backbone.name == "mediapipe_geom"
    assert stamp.gaze_backbone == "scripted"
    assert stamp.feature_set == "C"
    assert stamp.temporal_rule == TEMPORAL_RULE_VERSION
    sess.close()


def test_an_unknown_feature_set_fails_the_stamp_rather_than_being_recorded(fresh_cfg, monkeypatch):
    monkeypatch.setenv(COMMIT_ENV_VAR, "deadbeef")
    fresh_cfg.calibration.feature_set = "Z"

    with pytest.raises(ValueError, match="feature_set"):
        current_ai_version(fresh_cfg, "mediapipe_geom")


def test_the_feature_set_is_canonicalised_in_the_stamp(fresh_cfg, monkeypatch):
    monkeypatch.setenv(COMMIT_ENV_VAR, "deadbeef")
    fresh_cfg.calibration.feature_set = " b "

    assert current_ai_version(fresh_cfg).feature_set == "B"


def test_a_config_change_moves_the_hash_the_stamp_carries(fresh_cfg, monkeypatch):
    """"Same hash => same numbers" only holds if any section's sweep moves it."""
    monkeypatch.setenv(COMMIT_ENV_VAR, "deadbeef")
    before = current_ai_version(fresh_cfg).config_hash

    fresh_cfg.calibration.p_max_threshold += 0.05

    assert current_ai_version(fresh_cfg).config_hash != before


def test_the_commit_override_wins_and_is_never_cached(monkeypatch, tmp_path):
    """A cached override would freeze the stamp of a long-running process."""
    monkeypatch.setenv(COMMIT_ENV_VAR, "  1111aaaa  ")
    assert git_commit(tmp_path) == "1111aaaa"

    monkeypatch.setenv(COMMIT_ENV_VAR, "2222bbbb")
    assert git_commit(tmp_path) == "2222bbbb"


def test_an_empty_override_falls_back_to_the_git_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv(COMMIT_ENV_VAR, "   ")
    monkeypatch.setattr(version_mod, "_run_git", lambda args, cwd: None)

    assert git_commit(tmp_path / "not-a-checkout") == "unknown"


def test_a_missing_repository_degrades_to_unknown_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.delenv(COMMIT_ENV_VAR, raising=False)

    def never(args, cwd):
        raise AssertionError("git must not be spawned for a path that does not exist")

    monkeypatch.setattr(version_mod, "_run_git", never)

    assert git_commit(tmp_path / "gone") == "unknown"


@pytest.mark.parametrize(
    "status, expected",
    [("", "abc123def456"), (" M ai/src/vision/config.py", "abc123def456-dirty")],
)
def test_uncommitted_tracked_changes_are_stamped_dirty(monkeypatch, tmp_path, status, expected):
    monkeypatch.delenv(COMMIT_ENV_VAR, raising=False)
    seen: List[List[str]] = []

    def fake_run_git(args, cwd):
        seen.append(list(args))
        return "abc123def456" if args[0] == "rev-parse" else status

    monkeypatch.setattr(version_mod, "_run_git", fake_run_git)

    assert git_commit(tmp_path) == expected
    # Untracked output dirs (ai/reports, ai/datasets) must not mark a run dirty.
    assert seen[1] == ["status", "--porcelain", "--untracked-files=no"]


def test_the_commit_lookup_is_cached_per_repo_and_refreshable(monkeypatch, tmp_path):
    monkeypatch.delenv(COMMIT_ENV_VAR, raising=False)
    calls: List[List[str]] = []

    def fake_run_git(args, cwd):
        calls.append(list(args))
        return "0f0f0f0f0f0f" if args[0] == "rev-parse" else ""

    monkeypatch.setattr(version_mod, "_run_git", fake_run_git)

    assert git_commit(tmp_path) == "0f0f0f0f0f0f"
    assert git_commit(tmp_path) == "0f0f0f0f0f0f"
    assert len(calls) == 2  # rev-parse + status, once -- not once per frame

    git_commit(tmp_path, refresh=True)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "outcome", ["missing_binary", "not_a_repository", "timeout", "unreadable_cwd"]
)
def test_every_git_failure_mode_collapses_to_no_commit(monkeypatch, tmp_path, outcome):
    """No git, no repo, a hung filesystem: all mean "there is no commit to record"."""

    def fake_run(cmd, cwd, capture_output, text, timeout):
        assert cmd[0] == "git"
        assert timeout == _GIT_TIMEOUT_S  # a hung git must not stall a take
        if outcome == "missing_binary":
            raise FileNotFoundError("git")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, timeout)
        if outcome == "unreadable_cwd":
            raise NotADirectoryError(cwd)
        return SimpleNamespace(returncode=128, stdout="", stderr="not a git repository")

    monkeypatch.setattr(
        version_mod,
        "subprocess",
        SimpleNamespace(run=fake_run, SubprocessError=subprocess.SubprocessError),
    )

    assert _run_git(["rev-parse", "--short=12", "HEAD"], tmp_path) is None

    monkeypatch.delenv(COMMIT_ENV_VAR, raising=False)
    assert git_commit(tmp_path) == "unknown"
