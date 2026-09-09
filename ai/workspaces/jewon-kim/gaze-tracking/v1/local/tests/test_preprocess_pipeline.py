"""Contract tests for the preprocess *integration* half (doc 3-1).

Covers ``vision.preprocess.landmarker`` and ``vision.preprocess.pipeline``:
the boundary where MediaPipe stops and the rest of the codebase's plain-numpy
world begins.  The properties locked in here are the ones that are invisible
until something downstream is silently mirrored, dropped or mis-gated:

* One input contract at every door -- HxWx3 uint8, enforced by ``detect`` and
  by BOTH pipeline entry points (``cv2.cvtColor`` takes grayscale and BGRA
  without complaint, so ``process_bgr`` cannot delegate the check to it),
  closed means closed, and the caller's buffer is never mutated or wrapped
  without a copy.
* The MediaPipe *shape* contract: 478 landmarks (iris refinement on), a
  52-entry blendshape dict, a 4x4 transform, and ``presence`` that is exactly
  ``in_bounds_fraction`` of the landmarks it ships with.
* The anatomical left/right convention the landmarker docstring says not to
  "fix": subject-left (473 / 362..263) sits on the IMAGE RIGHT.
* Running-mode contracts: LIVE_STREAM refused, unknown mode rejected, VIDEO
  demands a timestamp and clamps non-increasing ones instead of crashing.
* The validity gate ORDER in ``PreprocessPipeline._invalid_reason`` is
  first-match-wins, each gate is reachable from its own config threshold, and
  an invalid-but-detected frame stays FULLY POPULATED -- doc 3-1 asks for a
  reason code, not for a hole.
* ``process_bgr`` and ``process_rgb`` agree on the same pixels, and
  ``to_record`` is a pixel-free ``q_``-prefixed row.

There is no recorded dataset in this repo, so everything that needs pixels uses
``tests/fixtures/face.jpg`` / ``static_face_30fps.mp4``.  Hand-built arrays
appear only where the point is one specific code path (the in-bounds counter,
the gate-ordering ladder); no accuracy number is measured anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vision.config import PreprocessConfig, VisionConfig, resolve_path
from vision.preprocess.landmarker import (NUM_LANDMARKS, FaceLandmarkerWrapper,
                                          LandmarkResult, in_bounds_fraction)
from vision.preprocess.pipeline import PreprocessPipeline
from vision.preprocess.sampler import iter_video_frames
from vision.schemas import FrameQuality, HeadPose, InvalidReason

# Subject-left / subject-right landmark ids, per the landmarker docstring.
LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER = 473, 263, 362
RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER = 468, 33, 133


# --------------------------------------------------------------------------
# Local helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mediapipe_ready() -> None:
    """Skip the whole test when mediapipe or the gitignored .task is absent.

    Mirrors ``conftest``: both are downloads (doc 16), so a checkout without
    them is a normal state, not a broken one.
    """
    pytest.importorskip("mediapipe")
    from vision.config import load_config

    model = resolve_path(load_config().preprocess.landmarker_model_path)
    if not model.is_file():
        pytest.skip("ai/models/face_landmarker.task not installed")


def _observe(cfg: VisionConfig, rgb: np.ndarray, frame_id: int = 0, t_ms: int = 0):
    """One frame through a throwaway pipeline built from ``cfg``."""
    with PreprocessPipeline(cfg) as pipeline:
        return pipeline.process_rgb(rgb, frame_id=frame_id, t_ms=t_ms)


def _gate(pre: PreprocessConfig, presence, quality, face_crop, left_eye, right_eye):
    """Drive ``PreprocessPipeline._invalid_reason`` without a MediaPipe graph.

    The gate ladder reads nothing but ``self.pre``, so an uninitialised instance
    is enough -- and it is the only way to reach CROP_FAILED, which no threshold
    on a real face can provoke.
    """
    pipeline = object.__new__(PreprocessPipeline)
    pipeline.pre = pre
    result = LandmarkResult(
        landmarks=np.zeros((NUM_LANDMARKS, 3), dtype=np.float32),
        blendshapes={},
        transform_matrix=None,
        presence=float(presence),
    )
    return pipeline._invalid_reason(result, quality, face_crop, left_eye, right_eye)


def _pixels(h: int = 8, w: int = 8) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


# --------------------------------------------------------------------------
# landmarker: input validation and lifecycle
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame, message",
    [
        pytest.param(np.zeros((8, 8), np.uint8), "HxWx3", id="two_dimensional"),
        pytest.param(np.zeros((8, 8, 1), np.uint8), "HxWx3", id="single_channel"),
        pytest.param(np.zeros((8, 8, 4), np.uint8), "HxWx3", id="rgba"),
        pytest.param(np.zeros((8, 8, 3), np.float32), "uint8", id="float32"),
        pytest.param(np.zeros((8, 8, 3), np.uint16), "uint8", id="uint16"),
        pytest.param(np.zeros((8, 8, 3), np.int8), "uint8", id="signed"),
    ],
)
def test_detect_rejects_anything_that_is_not_uint8_hwc3(
    mediapipe_ready, cfg, frame, message
):
    """``detect`` validates its own input, and it must be strict.

    A float frame silently reinterpreted as bytes would land as noise inside
    MediaPipe rather than as an error at the call site.  The pipeline's two
    entry points share this guard; see the entry-point test further down.
    """
    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        with pytest.raises(ValueError, match=message):
            wrapper.detect(frame)


def test_detect_after_close_raises_and_close_stays_idempotent(mediapipe_ready, cfg):
    wrapper = FaceLandmarkerWrapper(cfg.preprocess)
    wrapper.close()
    wrapper.close()  # second close must not double-free the graph

    with pytest.raises(RuntimeError, match="closed"):
        wrapper.detect(_pixels())


def test_context_manager_closes_the_graph_on_exit(mediapipe_ready, cfg, face_rgb):
    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        assert wrapper.detect(face_rgb) is not None

    with pytest.raises(RuntimeError, match="closed"):
        wrapper.detect(face_rgb)


def test_missing_model_file_names_the_download(mediapipe_ready, fresh_cfg):
    """The FileNotFoundError is the install instructions; keep them in it."""
    fresh_cfg.preprocess.landmarker_model_path = "ai/models/definitely_absent.task"

    with pytest.raises(FileNotFoundError) as excinfo:
        FaceLandmarkerWrapper(fresh_cfg.preprocess)

    assert "definitely_absent.task" in str(excinfo.value)
    assert "face_landmarker.task" in str(excinfo.value)


# --------------------------------------------------------------------------
# landmarker: running modes
# --------------------------------------------------------------------------


def test_live_stream_mode_is_refused_rather_than_half_supported(
    mediapipe_ready, fresh_cfg
):
    """LIVE_STREAM is async; this class's detect() contract is synchronous."""
    fresh_cfg.preprocess.running_mode = "LIVE_STREAM"

    with pytest.raises(NotImplementedError, match="LIVE_STREAM"):
        FaceLandmarkerWrapper(fresh_cfg.preprocess)


def test_unknown_running_mode_is_a_value_error(mediapipe_ready, fresh_cfg):
    fresh_cfg.preprocess.running_mode = "STREAMING"

    with pytest.raises(ValueError, match="unknown running_mode"):
        FaceLandmarkerWrapper(fresh_cfg.preprocess)


@pytest.mark.parametrize("spelling", ["video", "Video", "VIDEO"])
def test_running_mode_is_case_normalised_before_it_is_matched(
    mediapipe_ready, fresh_cfg, spelling
):
    """A YAML author writing ``running_mode: video`` must not get IMAGE mode."""
    fresh_cfg.preprocess.running_mode = spelling

    with FaceLandmarkerWrapper(fresh_cfg.preprocess) as wrapper:
        assert wrapper.running_mode == "VIDEO"


def test_video_mode_requires_an_explicit_timestamp(mediapipe_ready, fresh_cfg, face_rgb):
    fresh_cfg.preprocess.running_mode = "VIDEO"

    with FaceLandmarkerWrapper(fresh_cfg.preprocess) as wrapper:
        with pytest.raises(ValueError, match="requires t_ms"):
            wrapper.detect(face_rgb)


def test_video_mode_clamps_repeated_and_backwards_timestamps(
    mediapipe_ready, fresh_cfg, face_rgb
):
    """MediaPipe rejects non-increasing stamps; clamping keeps the take alive.

    Two frames sharing a millisecond, or a jittery capture clock stepping
    backwards, must both still produce a detection.
    """
    fresh_cfg.preprocess.running_mode = "VIDEO"

    with FaceLandmarkerWrapper(fresh_cfg.preprocess) as wrapper:
        stamps = []
        for t_ms in (100, 100, 50, 101, 5_000):
            assert wrapper.detect(face_rgb, t_ms=t_ms) is not None
            stamps.append(wrapper._last_timestamp_ms)

    assert stamps == [100, 101, 102, 103, 5_000]
    assert all(b > a for a, b in zip(stamps, stamps[1:]))


def test_image_mode_ignores_the_timestamp_argument(mediapipe_ready, cfg, face_rgb):
    """IMAGE mode is what keeps offline runs deterministic (preprocess.yaml)."""
    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        assert wrapper.running_mode == "IMAGE"
        first = wrapper.detect(face_rgb, t_ms=9_000)
        second = wrapper.detect(face_rgb, t_ms=0)
        third = wrapper.detect(face_rgb)

    assert np.array_equal(first.landmarks, second.landmarks)
    assert np.array_equal(first.landmarks, third.landmarks)


# --------------------------------------------------------------------------
# landmarker: the shape of what comes back
# --------------------------------------------------------------------------


def test_detection_carries_478_landmarks_and_52_blendshapes(landmark_result):
    """478 = 468 mesh points + 2 x 5 iris: proof iris refinement is enabled."""
    landmarks = landmark_result.landmarks
    assert landmarks.shape == (NUM_LANDMARKS, 3) == (478, 3)
    assert landmarks.dtype == np.float32

    scores = landmark_result.blendshapes
    assert len(scores) == 52
    assert "_neutral" in scores
    assert all(isinstance(v, float) for v in scores.values())
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_transform_matrix_is_a_homogeneous_4x4(landmark_result):
    matrix = landmark_result.transform_matrix
    assert matrix is not None
    assert matrix.shape == (4, 4)
    assert matrix.dtype == np.float64
    assert np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0])


def test_landmarks_are_normalised_image_coordinates(face_landmarks):
    """x/y are fractions of width/height; z is a relative depth, not metric."""
    xy = face_landmarks[:, :2]
    assert xy.min() >= 0.0 and xy.max() <= 1.0
    # z is unbounded by design -- assert only that it is finite and signed.
    assert np.isfinite(face_landmarks).all()
    assert face_landmarks[:, 2].min() < 0.0


def test_subject_left_landmarks_sit_on_the_image_right(face_landmarks):
    """The convention the landmarker docstring says NOT to "fix".

    MediaPipe's "left" set (362..263, 474..477) is the SUBJECT's left eye, which
    in a non-mirrored frame appears at a LARGER image x than the "right" set.
    ``left_eye_crop`` and ``eyeLookInLeft`` must keep meaning the same eye.
    """
    left_x = face_landmarks[[LEFT_EYE_OUTER, LEFT_EYE_INNER, LEFT_IRIS_CENTER], 0]
    right_x = face_landmarks[[RIGHT_EYE_OUTER, RIGHT_EYE_INNER, RIGHT_IRIS_CENTER], 0]

    assert left_x.min() > right_x.max()


@pytest.mark.parametrize(
    "iris, outer, inner",
    [
        pytest.param(LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER, id="left"),
        pytest.param(RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER, id="right"),
    ],
)
def test_iris_centre_lies_between_its_own_eye_corners(
    face_landmarks, iris, outer, inner
):
    """A refined iris that fell outside its eye would mean a mismatched index map."""
    lo, hi = sorted((face_landmarks[outer, 0], face_landmarks[inner, 0]))
    assert lo < face_landmarks[iris, 0] < hi


def test_presence_is_exactly_the_in_bounds_fraction_it_ships_with(landmark_result):
    """``presence`` is an honest proxy, not a fabricated confidence."""
    assert landmark_result.presence == pytest.approx(
        in_bounds_fraction(landmark_result.landmarks)
    )
    # The fixture face is fully inside the frame.
    assert landmark_result.presence == pytest.approx(1.0)


def test_a_blank_frame_produces_no_detection(mediapipe_ready, cfg):
    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        assert wrapper.detect(np.zeros((480, 640, 3), np.uint8)) is None


def test_non_contiguous_input_is_copied_before_it_is_wrapped(
    mediapipe_ready, cfg, face_rgb
):
    """mp.Image wraps the buffer without copying, hence the ascontiguousarray.

    A Fortran-ordered array holds identical pixels; if the copy were dropped the
    strides would be misread and the landmarks would not match.
    """
    fortran = np.asfortranarray(face_rgb)
    assert not fortran.flags["C_CONTIGUOUS"]

    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        skewed = wrapper.detect(fortran)
        straight = wrapper.detect(face_rgb)

    assert np.array_equal(skewed.landmarks, straight.landmarks)


def test_detect_does_not_mutate_the_callers_frame(mediapipe_ready, cfg, face_rgb):
    before = face_rgb.copy()

    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        wrapper.detect(face_rgb)

    assert np.array_equal(before, face_rgb)


# --------------------------------------------------------------------------
# in_bounds_fraction -- shared by the presence proxy and landmark_visibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "points, expected",
    [
        pytest.param(np.zeros((0, 3), np.float32), 0.0, id="no_landmarks"),
        pytest.param(np.full((4, 3), 0.5, np.float32), 1.0, id="all_inside"),
        pytest.param(
            np.array([[0.0, 0.0, 9.0], [1.0, 1.0, -9.0]], np.float32),
            1.0,
            id="closed_interval_endpoints_count_as_inside",
        ),
        pytest.param(
            np.array([[-1e-6, 0.5, 0.0], [0.5, 1.0 + 1e-6, 0.0]], np.float32),
            0.0,
            id="just_outside_is_outside",
        ),
        pytest.param(
            np.array([[0.5, 0.5, 0.0], [1.5, 0.5, 0.0]], np.float32),
            0.5,
            id="half_pushed_out_of_frame",
        ),
        pytest.param(
            np.array([[0.5, 0.5, 0.0], [np.nan, 0.5, 0.0]], np.float32),
            0.5,
            id="nan_counts_as_outside_not_inside",
        ),
        pytest.param(
            np.array([[0.5, 0.5, 0.0], [np.inf, -np.inf, 0.0]], np.float32),
            0.5,
            id="infinite_counts_as_outside",
        ),
    ],
)
def test_in_bounds_fraction_counts_the_closed_unit_square(points, expected):
    """Boundary rule: [0, 1] inclusive, and non-finite never counts as visible."""
    assert in_bounds_fraction(points) == pytest.approx(expected)


def test_in_bounds_fraction_ignores_the_depth_channel():
    """z is a relative depth with no [0, 1] meaning; it must not gate anything."""
    inside = np.array([[0.5, 0.5, 0.0], [0.5, 0.5, 500.0]], np.float32)

    assert in_bounds_fraction(inside) == pytest.approx(1.0)


def test_in_bounds_fraction_does_not_mutate_its_input():
    points = np.array([[0.5, 0.5, 0.0], [2.0, 2.0, 0.0]], np.float32)
    before = points.copy()

    in_bounds_fraction(points)

    assert np.array_equal(before, points)


# --------------------------------------------------------------------------
# pipeline: the happy path on the real fixture
# --------------------------------------------------------------------------


def test_fixture_frame_is_valid_with_crops_at_the_configured_sizes(
    face_observation, cfg, face_image_size
):
    obs = face_observation
    assert obs.face_valid is True
    assert obs.invalid_reason is None

    size = int(cfg.preprocess.face_crop_size)
    eye_w, eye_h = (int(v) for v in cfg.preprocess.eye_crop_size)

    assert obs.face_crop.shape == (size, size, 3) == (224, 224, 3)
    # eye_crop_size is (w, h); a numpy shape is (h, w).
    assert obs.left_eye_crop.shape == (eye_h, eye_w, 3) == (36, 60, 3)
    assert obs.right_eye_crop.shape == (eye_h, eye_w, 3)
    for crop in (obs.face_crop, obs.left_eye_crop, obs.right_eye_crop):
        assert crop.dtype == np.uint8

    assert obs.image_size == face_image_size
    assert obs.landmarks.shape == (NUM_LANDMARKS, 3)
    assert len(obs.blendshapes) == 52
    assert obs.face_confidence == pytest.approx(1.0)
    assert obs.preprocess_ms > 0.0


def test_face_bbox_is_a_clamped_rectangle_inside_the_frame(face_observation):
    x, y, w, h = face_observation.face_bbox
    width, height = face_observation.image_size

    assert x >= 0 and y >= 0
    assert w > 0 and h > 0
    assert x + w <= width and y + h <= height
    # The tight visible-face rectangle, not the margined crop window.
    assert face_observation.quality.face_area_ratio == pytest.approx(
        (w * h) / (width * height)
    )


def test_frame_id_and_timestamp_are_passed_through_untouched(
    mediapipe_ready, cfg, face_rgb
):
    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(face_rgb, frame_id=417, t_ms=13_900)

    assert obs.frame_id == 417
    assert obs.t_ms == 13_900


def test_process_bgr_and_process_rgb_agree_on_the_same_pixels(
    mediapipe_ready, cfg, face_jpg, face_rgb
):
    """The BGR entry point must be a colour swap, not a second code path."""
    cv2 = pytest.importorskip("cv2")
    bgr = cv2.imread(str(face_jpg), cv2.IMREAD_COLOR)

    with PreprocessPipeline(cfg) as pipeline:
        from_rgb = pipeline.process_rgb(face_rgb, frame_id=1, t_ms=10)
        from_bgr = pipeline.process_bgr(bgr, frame_id=1, t_ms=10)

    assert np.array_equal(from_rgb.landmarks, from_bgr.landmarks)
    assert np.array_equal(from_rgb.face_crop, from_bgr.face_crop)
    assert np.array_equal(from_rgb.left_eye_crop, from_bgr.left_eye_crop)
    assert from_rgb.face_bbox == from_bgr.face_bbox
    assert from_rgb.quality.to_dict() == from_bgr.quality.to_dict()
    assert from_rgb.head_pose.to_dict() == from_bgr.head_pose.to_dict()
    assert from_rgb.blendshapes == from_bgr.blendshapes


@pytest.mark.parametrize("entry_point", ["process_rgb", "process_bgr"])
def test_pipeline_never_writes_into_the_callers_frame(
    mediapipe_ready, cfg, face_rgb, entry_point
):
    frame = face_rgb.copy()
    before = frame.copy()

    with PreprocessPipeline(cfg) as pipeline:
        getattr(pipeline, entry_point)(frame, frame_id=0, t_ms=0)

    assert np.array_equal(before, frame)


@pytest.mark.parametrize("entry_point", ["process_rgb", "process_bgr"])
@pytest.mark.parametrize(
    "frame, message",
    [
        pytest.param(np.zeros((8, 8), np.uint8), "HxWx3", id="two_dimensional"),
        pytest.param(np.zeros((8, 8, 1), np.uint8), "HxWx3", id="single_channel"),
        pytest.param(np.zeros((8, 8, 4), np.uint8), "HxWx3", id="bgra"),
        pytest.param(np.zeros((8, 8, 3), np.float32), "uint8", id="float32"),
        pytest.param(np.zeros((8, 8, 3), np.uint16), "uint8", id="uint16"),
    ],
)
def test_both_entry_points_validate_the_frame_before_the_colour_conversion(
    mediapipe_ready, cfg, entry_point, frame, message
):
    """The pipeline doors enforce the same contract ``detect`` does.

    ``cv2.cvtColor(..., COLOR_BGR2RGB)`` accepts 1, 3 AND 4 channel input
    without complaint (verified), so ``process_bgr`` could not lean on it:
    a grayscale or BGRA frame came out HxWx3 uint8, sailed through ``detect``
    and reached the backbone as ``face_valid=True`` with nothing recording that
    the caller had handed over the wrong thing.
    """
    with PreprocessPipeline(cfg) as pipeline:
        with pytest.raises(ValueError, match=message):
            getattr(pipeline, entry_point)(frame, frame_id=0, t_ms=0)


def test_closing_the_pipeline_closes_the_landmarker(mediapipe_ready, cfg, face_rgb):
    pipeline = PreprocessPipeline(cfg)
    pipeline.close()

    with pytest.raises(RuntimeError, match="closed"):
        pipeline.process_rgb(face_rgb, frame_id=0, t_ms=0)


# --------------------------------------------------------------------------
# pipeline: the validity gates
# --------------------------------------------------------------------------


def test_face_valid_is_exactly_the_absence_of_an_invalid_reason(
    mediapipe_ready, fresh_cfg, face_rgb
):
    fresh_cfg.preprocess.min_eye_openness = 0.99
    invalid = _observe(fresh_cfg, face_rgb)

    assert invalid.face_valid is False
    assert invalid.invalid_reason is not None
    assert invalid.invalid_reason in {r.value for r in InvalidReason}


def _tighten(cfg: VisionConfig, gate: str, baseline) -> None:
    """Push exactly one threshold past what the fixture frame actually measures."""
    pre = cfg.preprocess
    if gate == InvalidReason.LOW_FACE_CONFIDENCE.value:
        pre.min_face_confidence = baseline.face_confidence + 0.01
    elif gate == InvalidReason.FACE_TOO_SMALL.value:
        pre.min_face_area_ratio = baseline.quality.face_area_ratio * 2.0
    elif gate == InvalidReason.OUT_OF_FRAME.value:
        # The border test fires when the bbox comes within tolerance of an edge.
        pre.border_tolerance_px = max(baseline.image_size)
    elif gate == InvalidReason.EYES_CLOSED.value:
        pre.min_eye_openness = baseline.quality.min_eye_openness + 0.01
    else:  # pragma: no cover - guards the parametrisation itself
        raise AssertionError("no threshold drives {}".format(gate))


@pytest.mark.parametrize(
    "gate",
    [
        InvalidReason.LOW_FACE_CONFIDENCE.value,
        InvalidReason.FACE_TOO_SMALL.value,
        InvalidReason.OUT_OF_FRAME.value,
        InvalidReason.EYES_CLOSED.value,
    ],
)
def test_every_threshold_gate_is_reachable_from_its_own_config_key(
    mediapipe_ready, fresh_cfg, face_rgb, face_observation, gate
):
    """Each doc 3-1 reason must be drivable by the one knob that names it.

    Thresholds are derived from what the fixture frame actually measures, so the
    test does not encode a magic number that a new fixture would invalidate.
    """
    _tighten(fresh_cfg, gate, face_observation)

    assert _observe(fresh_cfg, face_rgb).invalid_reason == gate


@pytest.mark.parametrize(
    "earlier, later",
    [
        (InvalidReason.LOW_FACE_CONFIDENCE.value, InvalidReason.FACE_TOO_SMALL.value),
        (InvalidReason.FACE_TOO_SMALL.value, InvalidReason.OUT_OF_FRAME.value),
        (InvalidReason.OUT_OF_FRAME.value, InvalidReason.EYES_CLOSED.value),
        (InvalidReason.LOW_FACE_CONFIDENCE.value, InvalidReason.EYES_CLOSED.value),
    ],
)
def test_first_matching_gate_wins_when_several_fire_at_once(
    mediapipe_ready, fresh_cfg, face_rgb, face_observation, earlier, later
):
    """doc 3-1 fixes the order; a bucket report is only readable if it is stable."""
    _tighten(fresh_cfg, earlier, face_observation)
    _tighten(fresh_cfg, later, face_observation)

    assert _observe(fresh_cfg, face_rgb).invalid_reason == earlier


def test_gate_ladder_order_including_crop_failed():
    """The full ladder, driven directly -- CROP_FAILED is unreachable by config.

    Each row switches on exactly one more failure than the row below it, so the
    sequence of answers is the documented order read off the code.
    """
    pre = PreprocessConfig(
        min_face_confidence=0.5, min_face_area_ratio=0.01, min_eye_openness=0.12
    )
    good = FrameQuality(
        face_area_ratio=0.05,
        left_eye_openness=0.20,
        right_eye_openness=0.20,
        touches_border=False,
    )
    crop = _pixels()

    def quality(**changes) -> FrameQuality:
        merged = FrameQuality(**{**good.__dict__, **changes})
        return merged

    assert _gate(pre, 1.0, good, crop, crop, crop) is None
    assert (
        _gate(pre, 0.4, quality(face_area_ratio=0.0), None, None, None)
        == InvalidReason.LOW_FACE_CONFIDENCE.value
    )
    assert (
        _gate(pre, 1.0, quality(face_area_ratio=0.001, touches_border=True), None, None, None)
        == InvalidReason.FACE_TOO_SMALL.value
    )
    assert (
        _gate(pre, 1.0, quality(touches_border=True, left_eye_openness=0.0), None, None, None)
        == InvalidReason.OUT_OF_FRAME.value
    )
    assert (
        _gate(pre, 1.0, quality(left_eye_openness=0.0), None, None, None)
        == InvalidReason.EYES_CLOSED.value
    )
    assert _gate(pre, 1.0, good, None, crop, crop) == InvalidReason.CROP_FAILED.value


@pytest.mark.parametrize(
    "left, right, expected",
    [
        pytest.param(True, True, None, id="both_eyes"),
        pytest.param(True, False, None, id="left_only_survives"),
        pytest.param(False, True, None, id="right_only_survives"),
        pytest.param(False, False, InvalidReason.CROP_FAILED.value, id="no_eye_at_all"),
    ],
)
def test_one_missing_eye_crop_is_survivable_but_two_are_not(left, right, expected):
    """The face-crop backbones (doc 3-2) never read the eye crops, so one is enough."""
    pre = PreprocessConfig()
    quality = FrameQuality(
        face_area_ratio=0.05, left_eye_openness=0.2, right_eye_openness=0.2
    )
    crop = _pixels()

    reason = _gate(
        pre, 1.0, quality, crop, crop if left else None, crop if right else None
    )

    assert reason == expected


def test_min_eye_openness_gate_reads_the_worse_eye():
    """One winking eye is enough to make the frame unusable for gaze."""
    pre = PreprocessConfig(min_eye_openness=0.12)
    crop = _pixels()
    one_closed = FrameQuality(
        face_area_ratio=0.05, left_eye_openness=0.30, right_eye_openness=0.01
    )

    assert one_closed.min_eye_openness == pytest.approx(0.01)
    assert (
        _gate(pre, 1.0, one_closed, crop, crop, crop)
        == InvalidReason.EYES_CLOSED.value
    )


def test_gates_are_inclusive_at_the_configured_floor():
    """``x < threshold`` -- a value sitting exactly on the floor must pass.

    Off-by-one here would reject every frame that lands on a round threshold.
    """
    pre = PreprocessConfig(
        min_face_confidence=0.50, min_face_area_ratio=0.010, min_eye_openness=0.12
    )
    crop = _pixels()
    exactly_at_floor = FrameQuality(
        face_area_ratio=0.010, left_eye_openness=0.12, right_eye_openness=0.12
    )

    assert _gate(pre, 0.50, exactly_at_floor, crop, crop, crop) is None
    assert (
        _gate(pre, np.nextafter(0.50, 0.0), exactly_at_floor, crop, crop, crop)
        == InvalidReason.LOW_FACE_CONFIDENCE.value
    )


# --------------------------------------------------------------------------
# pipeline: the two invalid shapes doc 3-1 promises
# --------------------------------------------------------------------------


def test_no_face_frame_is_a_reason_code_over_untouched_defaults(mediapipe_ready, cfg):
    """NO_FACE is the one branch that legitimately has nothing to report."""
    blank = np.zeros((480, 640, 3), np.uint8)

    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(blank, frame_id=7, t_ms=875)

    assert obs.invalid_reason == InvalidReason.NO_FACE.value
    assert obs.face_valid is False
    assert obs.face_confidence == 0.0
    assert obs.frame_id == 7 and obs.t_ms == 875
    assert obs.image_size == (640, 480)  # (width, height), still reported
    assert obs.landmarks is None
    assert obs.blendshapes is None
    assert obs.face_bbox is None
    assert obs.face_crop is None
    assert obs.left_eye_crop is None
    assert obs.right_eye_crop is None
    assert obs.head_pose == HeadPose()
    # Every measured quality field reads as "nothing seen".  landmark_visibility
    # is the one the dataclass default gets wrong (1.0), so the pipeline passes
    # it explicitly; it has its own test below.
    assert obs.quality.face_area_ratio == 0.0
    assert obs.quality.face_brightness == 0.0
    assert obs.quality.background_brightness == 0.0
    assert obs.quality.face_contrast == 0.0
    assert obs.quality.min_eye_openness == 0.0
    assert obs.quality.touches_border is False
    assert obs.preprocess_ms > 0.0


def test_no_face_frame_does_not_claim_full_landmark_visibility(mediapipe_ready, cfg):
    """A NO_FACE row must not poison a doc 19 visibility bucket with a 1.0.

    ``face_area_ratio`` correctly reports 0.0 on the same record, so the two
    quality fields currently contradict each other.
    """
    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(np.zeros((480, 640, 3), np.uint8), 0, 0)

    assert obs.quality.landmark_visibility == 0.0


@pytest.fixture(scope="module")
def clipped_face_rgb(face_rgb: np.ndarray) -> np.ndarray:
    """The fixture face with the top of the head cut off by the frame edge.

    A real partially-visible face, which is what the OUT_OF_FRAME bucket and the
    decaying presence proxy are actually about.
    """
    return np.ascontiguousarray(face_rgb[150:700])


def test_presence_decays_as_the_face_slides_out_of_frame(
    mediapipe_ready, cfg, clipped_face_rgb
):
    """The proxy is honest: a fully visible face is 1.0, a clipped one is less.

    It must stay identical to ``landmark_visibility`` -- both are
    ``in_bounds_fraction`` of the same landmarks, and the failure buckets read
    one while the gate reads the other.
    """
    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(clipped_face_rgb, 0, 0)

    assert obs.landmarks is not None, "the clipped face must still be detected"
    assert 0.0 < obs.face_confidence < 1.0
    assert obs.face_confidence == pytest.approx(obs.quality.landmark_visibility)
    assert obs.face_confidence == pytest.approx(in_bounds_fraction(obs.landmarks))
    assert obs.invalid_reason == InvalidReason.OUT_OF_FRAME.value


def test_negative_border_tolerance_disables_the_out_of_frame_rule(
    mediapipe_ready, fresh_cfg, clipped_face_rgb
):
    """``border_tolerance_px < 0`` is the documented off switch for the gate.

    The same clipped frame that fails at the shipped tolerance must pass, and
    the rest of the observation must be unchanged by the knob.
    """
    fresh_cfg.preprocess.border_tolerance_px = -1
    obs = _observe(fresh_cfg, clipped_face_rgb)

    assert obs.quality.touches_border is False
    assert obs.invalid_reason is None
    assert obs.face_valid is True


def test_invalid_but_detected_frame_stays_fully_populated(
    mediapipe_ready, fresh_cfg, face_rgb, face_observation
):
    """doc 3-1's last bullet asks for a reason code, not for a hole.

    The doc 19 failure buckets and the doc 5-2 calibration hints need the
    landmarks, head pose and quality of the frame that failed.
    """
    _tighten(fresh_cfg, InvalidReason.EYES_CLOSED.value, face_observation)
    obs = _observe(fresh_cfg, face_rgb)

    assert obs.invalid_reason == InvalidReason.EYES_CLOSED.value
    assert obs.face_valid is False

    assert obs.landmarks is not None and obs.landmarks.shape == (NUM_LANDMARKS, 3)
    assert obs.blendshapes and len(obs.blendshapes) == 52
    assert obs.face_crop is not None
    assert obs.left_eye_crop is not None and obs.right_eye_crop is not None
    assert obs.face_bbox is not None
    assert obs.face_confidence == pytest.approx(face_observation.face_confidence)
    assert obs.quality.face_area_ratio == pytest.approx(
        face_observation.quality.face_area_ratio
    )
    assert obs.head_pose.to_dict() == face_observation.head_pose.to_dict()


# --------------------------------------------------------------------------
# pipeline: the serialisable view
# --------------------------------------------------------------------------


def test_to_record_is_pixel_free_and_json_safe(face_observation):
    record = face_observation.to_record()

    assert not any(
        key in record for key in ("face_crop", "left_eye_crop", "right_eye_crop",
                                  "landmarks", "blendshapes", "face_bbox", "image_size")
    )
    assert not any(isinstance(v, np.ndarray) for v in record.values())
    json.dumps(record)  # must not raise


def test_to_record_namespaces_every_quality_signal_with_q(face_observation):
    record = face_observation.to_record()
    quality = face_observation.quality.to_dict()

    assert {k for k in record if k.startswith("q_")} == {"q_" + k for k in quality}
    for key, value in quality.items():
        assert record["q_" + key] == value
    # The derived backlight ratio rides along with the stored fields.
    assert "q_backlight_ratio" in record


def test_to_record_flattens_head_pose_with_a_head_prefix(face_observation):
    record = face_observation.to_record()
    pose = face_observation.head_pose

    assert record["head_yaw"] == pose.yaw
    assert record["head_pitch"] == pose.pitch
    assert record["head_roll"] == pose.roll
    assert record["head_reprojection_error"] == pose.reprojection_error
    assert record["head_depth_proxy"] == pose.depth_proxy


# --------------------------------------------------------------------------
# pipeline over the recorded video fixture
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sampled_video(face_video: Path):
    """The 30 fps fixture decimated to the shipped 8 fps analysis rate."""
    pytest.importorskip("cv2")
    return list(iter_video_frames(face_video, 8.0))


def test_video_is_decimated_onto_the_analysis_grid(sampled_video, cfg):
    """420 source frames at 30 fps -> 14 s -> 112 admitted at 8 fps."""
    assert cfg.preprocess.analysis_fps == 8.0
    assert len(sampled_video) == 112

    stamps = [t_ms for _, t_ms, _ in sampled_video]
    ids = [frame_id for frame_id, _, _ in sampled_video]

    assert stamps[0] == 0 and ids[0] == 0
    assert all(b > a for a, b in zip(stamps, stamps[1:])), "t_ms must be monotone"
    assert all(b > a for a, b in zip(ids, ids[1:])), "frame_id must be monotone"


def test_decimation_holds_the_ideal_grid_instead_of_drifting(sampled_video):
    """Gaps land within one source period of the 125 ms target, with no drift.

    ``FrameSampler`` advances the deadline on the ideal grid, so individual gaps
    alternate around 125 ms but the mean must not walk away from it.
    """
    stamps = [t_ms for _, t_ms, _ in sampled_video]
    gaps = np.diff(stamps)
    source_period_ms = 1000.0 / 30.0

    assert gaps.min() > 125.0 - source_period_ms - 1.0
    assert gaps.max() < 125.0 + source_period_ms + 1.0
    assert float(gaps.mean()) == pytest.approx(125.0, abs=1.0)


def test_video_frames_survive_the_pipeline_in_video_mode(
    mediapipe_ready, fresh_cfg, sampled_video
):
    """VIDEO mode keeps MediaPipe tracking across a decimated take."""
    fresh_cfg.preprocess.running_mode = "VIDEO"
    take = sampled_video[:8]

    with PreprocessPipeline(fresh_cfg) as pipeline:
        observations = [
            pipeline.process_bgr(frame, frame_id=frame_id, t_ms=t_ms)
            for frame_id, t_ms, frame in take
        ]

    assert [o.invalid_reason for o in observations] == [None] * len(take)
    assert all(o.face_valid for o in observations)
    assert [o.t_ms for o in observations] == [t for _, t, _ in take]
    assert [o.frame_id for o in observations] == [i for i, _, _ in take]
    assert all(o.image_size == (640, 480) for o in observations)


def test_missing_video_path_fails_before_opencv_guesses(tmp_path):
    with pytest.raises(FileNotFoundError, match="video not found"):
        list(iter_video_frames(tmp_path / "nope.mp4", 8.0))
