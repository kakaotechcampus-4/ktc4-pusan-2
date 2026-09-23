"""Contracts of the pure-geometry half of preprocess (doc 3-1).

Covers ``vision.preprocess.crops``, ``vision.preprocess.headpose`` and
``vision.preprocess.sampler`` -- everything that turns landmarks and timestamps
into pixels, angles and an analysis stream without touching the MediaPipe graph.

What this file locks in:

* landmarks are normalised by width and height *independently*, so every
  geometric quantity must be taken in PIXELS.  ``eye_aspect_ratio`` is the one
  function that can skip that step, and on the non-square fixture it must give a
  measurably different (skewed) answer;
* ``side="left"`` is the SUBJECT's left eye = the IMAGE RIGHT half, the
  convention ``crops`` and ``landmarker`` both warn against "fixing";
* the head-pose signs of ``schemas.py``: pitch > 0 chin up, yaw > 0 face toward
  the image right, roll > 0 clockwise in-image tilt -- for both estimators, and
  across MediaPipe's Y-up / Z-toward-viewer basis change;
* the refusal paths: a bbox clamped to the frame, a face too small to crop, an
  eye below the 4 px floor or off-frame, a truncated landmark array;
* the crop bbox is the TIGHT visible-face rectangle, not the margined crop
  window, because ``face_area_ratio`` and the border test are read off it;
* decimation is driven by timestamps, tolerant of half a millisecond, and
  resynchronises after a stall instead of firing a catch-up burst.

No fabricated dataset: pixels come from ``tests/fixtures``, and every hand-built
array exercises one specific code path.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from vision.preprocess import crops, headpose
from vision.preprocess.landmarker import NUM_LANDMARKS
from vision.preprocess.sampler import FrameSampler, iter_video_frames

# --------------------------------------------------------------------------
# Local fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pre(cfg):
    """The shipped ``PreprocessConfig`` (read-only)."""
    return cfg.preprocess


@pytest.fixture
def fresh_pre(fresh_cfg):
    """A per-test ``PreprocessConfig``, safe to mutate."""
    return fresh_cfg.preprocess


def _blank_landmarks(x: float = 0.5, y: float = 0.5) -> np.ndarray:
    """A full-size landmark array collapsed onto one normalised point."""
    lm = np.zeros((NUM_LANDMARKS, 3), dtype=np.float64)
    lm[:, 0] = x
    lm[:, 1] = y
    return lm


def _with_eyes(
    lm: np.ndarray,
    left_center: float,
    right_center: float,
    y: float = 0.4,
    half_width: float = 0.05,
) -> np.ndarray:
    """Put horizontal eye corners on both sides, in normalised coordinates."""
    lm[crops.LEFT_EYE_OUTER, :2] = (left_center + half_width, y)
    lm[crops.LEFT_EYE_INNER, :2] = (left_center - half_width, y)
    lm[crops.RIGHT_EYE_OUTER, :2] = (right_center - half_width, y)
    lm[crops.RIGHT_EYE_INNER, :2] = (right_center + half_width, y)
    return lm


def _tilted_face(theta_deg: float, size: int = 400):
    """A synthetic face rolled clockwise by ``theta_deg``, plus its image.

    The landmark cloud, the eye line and a bright bar drawn into the image all
    share the same tilt, so a roll-aligned crop must show the bar horizontal.
    The frame is square on purpose: the tilt is then the same in normalised and
    in pixel space, which keeps the expected angle exactly ``theta_deg``.
    """
    centre = np.asarray([size / 2.0, size / 2.0])
    t = math.radians(theta_deg)
    along = np.asarray([math.cos(t), math.sin(t)])
    across = np.asarray([-math.sin(t), math.cos(t)])

    grid = [
        centre + u * along + v * across
        for u in np.linspace(-80.0, 80.0, 20)
        for v in np.linspace(-80.0, 80.0, 20)
    ]
    points = np.asarray(grid)[:NUM_LANDMARKS]

    lm = np.zeros((NUM_LANDMARKS, 3), dtype=np.float64)
    lm[: len(points), :2] = points
    lm[len(points) :, :2] = centre
    lm[crops.RIGHT_EYE_OUTER, :2] = centre - 68.0 * along
    lm[crops.RIGHT_EYE_INNER, :2] = centre - 52.0 * along
    lm[crops.LEFT_EYE_INNER, :2] = centre + 52.0 * along
    lm[crops.LEFT_EYE_OUTER, :2] = centre + 68.0 * along
    lm[:, :2] /= float(size)

    image = np.zeros((size, size, 3), dtype=np.uint8)
    p0 = tuple(np.round(centre - 70.0 * along).astype(int))
    p1 = tuple(np.round(centre + 70.0 * along).astype(int))
    cv2.line(image, p0, p1, (255, 255, 255), 5)
    return lm, image


def _principal_angle_deg(crop: np.ndarray) -> float:
    """Orientation of the bright bar inside a crop, wrapped to (-90, 90]."""
    ys, xs = np.nonzero(crop[:, :, 0] > 128)
    assert xs.size > 50, "the bar did not survive the crop"
    centred = np.vstack([xs - xs.mean(), ys - ys.mean()])
    values, vectors = np.linalg.eigh(np.cov(centred))
    dx, dy = vectors[:, int(np.argmax(values))]
    return (math.degrees(math.atan2(float(dy), float(dx))) + 90.0) % 180.0 - 90.0


def _rx(a: float) -> np.ndarray:
    return np.asarray(
        [[1.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a)], [0.0, math.sin(a), math.cos(a)]]
    )


def _ry(b: float) -> np.ndarray:
    return np.asarray(
        [[math.cos(b), 0.0, math.sin(b)], [0.0, 1.0, 0.0], [-math.sin(b), 0.0, math.cos(b)]]
    )


def _rz(c: float) -> np.ndarray:
    return np.asarray(
        [[math.cos(c), -math.sin(c), 0.0], [math.sin(c), math.cos(c), 0.0], [0.0, 0.0, 1.0]]
    )


def _matrix_4x4(rotation: np.ndarray, t_z: float = -60.0) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    m[:3, :3] = rotation
    m[2, 3] = t_z
    return m


# ==========================================================================
# crops: normalised -> pixel conversion
# ==========================================================================


@pytest.mark.parametrize(
    "image_size, point, expected",
    [
        ((820, 1024), (0.5, 0.25), (410.0, 256.0)),
        ((100, 200), (0.5, 0.5), (50.0, 100.0)),  # a transposed scale would give (100, 50)
        ((640, 480), (0.0, 1.0), (0.0, 480.0)),
        ((640, 480), (1.25, -0.5), (800.0, -240.0)),  # out of frame is not clamped here
    ],
)
def test_to_pixels_scales_x_by_width_and_y_by_height(image_size, point, expected):
    lm = _blank_landmarks(*point)

    assert crops.to_pixels(lm, image_size)[0] == pytest.approx(expected)


def test_to_pixels_drops_depth_and_leaves_the_caller_array_untouched():
    lm = _blank_landmarks(0.3, 0.7)
    lm[:, 2] = 99.0
    before = lm.copy()

    pixels = crops.to_pixels(lm, (200, 400))

    assert pixels.shape == (NUM_LANDMARKS, 2)
    assert np.array_equal(pixels, crops.to_pixels(lm[:, :2], (200, 400)))
    assert np.array_equal(lm, before)


# ==========================================================================
# crops: the subject-left / image-right convention
# ==========================================================================


def test_subject_left_eye_lands_on_the_image_right_half_of_the_fixture(
    face_landmarks, face_image_size
):
    # Verified in landmarker.py's docstring and deliberately not "fixed": the
    # anatomical naming is what keeps left_eye_crop and eyeLook*Left one eye.
    left = crops.eye_center(face_landmarks, "left", face_image_size)
    right = crops.eye_center(face_landmarks, "right", face_image_size)

    assert left[0] > right[0]
    assert crops.iris_center(face_landmarks, "left", face_image_size)[0] > face_image_size[0] / 2
    assert crops.iris_center(face_landmarks, "right", face_image_size)[0] < face_image_size[0] / 2


@pytest.mark.parametrize("spelling", ["left", "LEFT", " Left ", "right", "RIGHT"])
def test_side_names_are_case_and_whitespace_insensitive(face_landmarks, face_image_size, spelling):
    assert np.isfinite(crops.eye_aspect_ratio(face_landmarks, spelling, face_image_size))
    assert np.all(np.isfinite(crops.eye_center(face_landmarks, spelling, face_image_size)))
    assert np.all(np.isfinite(crops.iris_center(face_landmarks, spelling, face_image_size)))


@pytest.mark.parametrize("side", ["middle", "", "l", None, 3])
@pytest.mark.parametrize(
    "call",
    [crops.eye_corners, crops.eye_center, crops.iris_center, crops.eye_aspect_ratio],
    ids=["eye_corners", "eye_center", "iris_center", "eye_aspect_ratio"],
)
def test_an_unknown_side_is_rejected_by_every_per_eye_function(call, side):
    lm = _blank_landmarks()

    with pytest.raises(ValueError, match="side must be"):
        call(lm, side, (100, 100))


def test_eye_center_ignores_the_iris_while_iris_center_follows_it():
    # The corner midpoint is what the eye crop is built on, precisely so the
    # crop does not chase the gaze it is meant to measure.
    lm = _with_eyes(_blank_landmarks(), left_center=0.6, right_center=0.4)
    lm[list(crops.LEFT_IRIS_RING), :2] = (0.60, 0.40)
    looking_away = lm.copy()
    looking_away[list(crops.LEFT_IRIS_RING), :2] = (0.63, 0.43)

    assert crops.eye_center(looking_away, "left", (400, 400)) == pytest.approx(
        crops.eye_center(lm, "left", (400, 400))
    )
    assert crops.iris_center(looking_away, "left", (400, 400))[0] > (
        crops.iris_center(lm, "left", (400, 400))[0]
    )


# ==========================================================================
# crops: roll angle
# ==========================================================================


@pytest.mark.parametrize("theta_deg", [0.0, 12.0, -12.0, 25.0, -25.0, 45.0])
def test_roll_is_positive_when_the_image_right_eye_sits_lower(theta_deg):
    lm, _ = _tilted_face(theta_deg)

    assert crops.roll_angle_deg(lm, (400, 400)) == pytest.approx(theta_deg, abs=1e-9)


def test_roll_is_measured_in_pixels_so_the_frame_aspect_changes_it():
    lm = _with_eyes(_blank_landmarks(), left_center=0.6, right_center=0.4)
    lm[crops.LEFT_EYE_OUTER, 1] = 0.55
    lm[crops.LEFT_EYE_INNER, 1] = 0.55

    square = crops.roll_angle_deg(lm, (400, 400))
    tall = crops.roll_angle_deg(lm, (400, 800))

    # Same normalised landmarks, twice the pixel height -> twice the rise.
    assert square == pytest.approx(math.degrees(math.atan2(0.15 * 400, 0.2 * 400)))
    assert tall == pytest.approx(math.degrees(math.atan2(0.15 * 800, 0.2 * 400)))
    assert tall > square


def test_roll_from_the_eye_line_agrees_with_the_head_pose_roll(
    face_landmarks, face_image_size, landmark_result
):
    if landmark_result.transform_matrix is None:
        pytest.skip("no facial transformation matrix in this MediaPipe build")
    pose = headpose.head_pose_from_matrix(landmark_result.transform_matrix, face_image_size)

    eye_line = crops.roll_angle_deg(face_landmarks, face_image_size)

    # Two independent estimators (two landmarks vs the whole mesh fit) must at
    # least agree on which way the head is tilted.
    assert eye_line == pytest.approx(math.degrees(pose.roll), abs=1.0)
    assert np.sign(eye_line) == np.sign(math.degrees(pose.roll))


# ==========================================================================
# crops: face bbox
# ==========================================================================


def test_face_bbox_is_the_floor_ceil_hull_of_every_landmark(face_landmarks, face_image_size):
    pixels = crops.to_pixels(face_landmarks, face_image_size)
    x, y, w, h = crops.face_bbox_from_landmarks(face_landmarks, face_image_size)

    assert x <= pixels[:, 0].min() < x + 1
    assert y <= pixels[:, 1].min() < y + 1
    assert x + w - 1 < pixels[:, 0].max() <= x + w
    assert y + h - 1 < pixels[:, 1].max() <= y + h
    assert 0 <= x and 0 <= y
    assert x + w <= face_image_size[0] and y + h <= face_image_size[1]


@pytest.mark.parametrize("margin", [0.0, 0.25, 1.0, 10.0])
def test_face_bbox_never_leaves_the_frame_however_far_the_landmarks_stray(margin):
    lm = _blank_landmarks()
    lm[0, :2] = (-0.5, -0.25)
    lm[1, :2] = (1.5, 1.25)

    assert crops.face_bbox_from_landmarks(lm, (820, 1024), margin) == (0, 0, 820, 1024)


def test_face_bbox_margin_inflates_the_rectangle_about_its_centre():
    lm = _blank_landmarks()
    lm[0, :2] = (0.25, 0.25)
    lm[1, :2] = (0.75, 0.75)

    x0, y0, w0, h0 = crops.face_bbox_from_landmarks(lm, (400, 400), 0.0)
    x1, y1, w1, h1 = crops.face_bbox_from_landmarks(lm, (400, 400), 0.25)

    assert (w0, h0) == (200, 200)
    assert w1 == pytest.approx(1.5 * w0, abs=1)
    assert h1 == pytest.approx(1.5 * h0, abs=1)
    assert x1 + w1 / 2 == pytest.approx(x0 + w0 / 2, abs=1)
    assert y1 + h1 / 2 == pytest.approx(y0 + h0 / 2, abs=1)


def test_a_collapsed_landmark_cloud_has_a_zero_area_bbox_not_a_negative_one():
    x, y, w, h = crops.face_bbox_from_landmarks(_blank_landmarks(0.5, 0.5), (400, 400))

    assert (x, y) == (200, 200)
    assert (w, h) == (0, 0)


# ==========================================================================
# crops: face crop
# ==========================================================================


def test_face_crop_is_square_at_the_configured_size(face_rgb, face_landmarks, pre):
    crop, _ = crops.crop_face(face_rgb, face_landmarks, pre)

    assert crop is not None
    assert crop.shape == (pre.face_crop_size, pre.face_crop_size, 3)
    assert crop.dtype == np.uint8


def test_face_crop_size_follows_the_config(face_rgb, face_landmarks, fresh_pre):
    fresh_pre.face_crop_size = 96

    crop, _ = crops.crop_face(face_rgb, face_landmarks, fresh_pre)

    assert crop.shape == (96, 96, 3)


def test_crop_face_returns_the_tight_bbox_not_the_margined_crop_window(
    face_rgb, face_landmarks, pre
):
    # Reporting the padded window would inflate face_area_ratio by (1+2m)^2 and
    # flag every close-up frame as touching the border.
    _, bbox = crops.crop_face(face_rgb, face_landmarks, pre)
    image_size = (face_rgb.shape[1], face_rgb.shape[0])
    tight = crops.face_bbox_from_landmarks(face_landmarks, image_size, 0.0)
    padded = crops.face_bbox_from_landmarks(face_landmarks, image_size, pre.face_crop_margin)

    assert pre.face_crop_margin > 0.0
    assert bbox == tight
    assert bbox[2] * bbox[3] < padded[2] * padded[3]


def test_a_face_too_small_to_crop_yields_no_crop_but_still_reports_its_bbox(pre):
    image = np.full((400, 400, 3), 200, dtype=np.uint8)

    crop, bbox = crops.crop_face(image, _blank_landmarks(0.5, 0.5), pre)

    assert crop is None
    assert bbox == (200, 200, 0, 0)


def test_face_crop_replicates_the_border_instead_of_padding_black(pre):
    # A face at the frame edge: the crop window is built UNCLAMPED so the face
    # stays centred, and the overhang must not become a fake luminance step.
    lm = _blank_landmarks()
    lm[:, 0] = np.linspace(-0.05, 0.05, NUM_LANDMARKS)
    lm[:, 1] = np.linspace(0.20, 0.60, NUM_LANDMARKS)
    _with_eyes(lm, left_center=0.03, right_center=-0.01, y=0.3, half_width=0.01)
    image = np.full((400, 400, 3), 200, dtype=np.uint8)

    crop, bbox = crops.crop_face(image, lm, pre)

    assert crop.shape == (pre.face_crop_size, pre.face_crop_size, 3)
    assert bbox[0] == 0  # clamped at the edge
    assert int(crop.min()) == 200 and int(crop.max()) == 200


@pytest.mark.parametrize("theta_deg", [12.0, -12.0, 25.0])
def test_roll_alignment_levels_the_eye_line_in_the_face_crop(theta_deg, fresh_pre):
    lm, image = _tilted_face(theta_deg)

    fresh_pre.align_face_roll = True
    aligned, _ = crops.crop_face(image, lm, fresh_pre)
    fresh_pre.align_face_roll = False
    raw, _ = crops.crop_face(image, lm, fresh_pre)

    # De-rotated, not double-rotated: a flipped sign would give 2*theta.
    assert _principal_angle_deg(aligned) == pytest.approx(0.0, abs=1.0)
    assert _principal_angle_deg(raw) == pytest.approx(theta_deg, abs=1.5)


def test_a_non_finite_landmark_degrades_to_no_crop_instead_of_raising(pre):
    """One NaN landmark is a CROP_FAILED frame, not a dead take.

    ``_landmark_extent`` is the single finiteness guard: it refuses the cloud
    before ``face_bbox_from_landmarks`` can reach ``math.floor(nan)``, which is
    what used to raise and make ``crop_face``'s own guard unreachable.
    """
    lm = _blank_landmarks(0.3, 0.3)
    lm[5, :2] = (np.nan, 0.5)

    crop, bbox = crops.crop_face(np.zeros((400, 400, 3), np.uint8), lm, pre)

    assert crop is None
    # The refusal still reports a bbox, and it is the empty one: no finite
    # extent means no visible face, which is face_area_ratio == 0.0.
    assert bbox == (0, 0, 0, 0)
    assert crops.face_bbox_from_landmarks(lm, (400, 400)) == (0, 0, 0, 0)


# ==========================================================================
# crops: eye crops
# ==========================================================================


def test_eye_crops_are_width_by_height_as_configured(face_rgb, face_landmarks, pre):
    left, right = crops.crop_eyes(face_rgb, face_landmarks, pre)
    width, height = int(pre.eye_crop_size[0]), int(pre.eye_crop_size[1])

    assert width != height, "a square eye crop would not catch an axis swap"
    for crop in (left, right):
        assert crop is not None
        assert crop.shape == (height, width, 3)
        assert crop.dtype == np.uint8


@pytest.mark.parametrize("broken", ["left", "right"])
@pytest.mark.parametrize(
    "kind, outer, inner",
    [
        ("too narrow", (0.5000, 0.4), (0.5075, 0.4)),  # 3 px apart on a 400 px frame
        ("centre off frame", (1.5000, 0.4), (1.4000, 0.4)),
        ("non finite", (np.nan, 0.4), (0.4000, 0.4)),
    ],
)
def test_a_degenerate_eye_drops_only_its_own_side(broken, kind, outer, inner):
    from vision.config import PreprocessConfig

    lm = _with_eyes(_blank_landmarks(), left_center=0.6, right_center=0.4)
    lm[crops.LEFT_EYE_OUTER if broken == "left" else crops.RIGHT_EYE_OUTER, :2] = outer
    lm[crops.LEFT_EYE_INNER if broken == "left" else crops.RIGHT_EYE_INNER, :2] = inner

    left, right = crops.crop_eyes(
        np.full((400, 400, 3), 200, np.uint8), lm, PreprocessConfig()
    )
    dropped, kept = (left, right) if broken == "left" else (right, left)

    assert dropped is None, kind
    assert kept is not None, "the healthy eye must survive its neighbour"


@pytest.mark.parametrize("width_px, expected_crop", [(3.99, False), (4.01, True)])
def test_the_eye_width_floor_is_four_pixels(width_px, expected_crop):
    from vision.config import PreprocessConfig

    assert crops._MIN_EYE_WIDTH_PX == 4.0
    lm = _with_eyes(_blank_landmarks(), left_center=0.6, right_center=0.4)
    lm[crops.LEFT_EYE_OUTER, :2] = (0.6 + width_px / 800.0, 0.4)
    lm[crops.LEFT_EYE_INNER, :2] = (0.6 - width_px / 800.0, 0.4)

    left, _ = crops.crop_eyes(np.full((400, 400, 3), 200, np.uint8), lm, PreprocessConfig())

    assert (left is not None) is expected_crop


# ==========================================================================
# crops: eye aspect ratio
# ==========================================================================


@pytest.mark.parametrize("side", ["left", "right"])
def test_eye_aspect_ratio_is_skewed_when_the_image_size_is_omitted(
    face_landmarks, face_image_size, side
):
    width, height = face_image_size
    assert width != height, "the fixture must be non-square for this to bite"

    in_pixels = crops.eye_aspect_ratio(face_landmarks, side, face_image_size)
    normalised = crops.eye_aspect_ratio(face_landmarks, side)

    # Dividing the lid opening by the height and the eye span by the width
    # scales the ratio by roughly width/height -- a ~18% error on this fixture.
    assert normalised / in_pixels == pytest.approx(width / height, rel=0.05)
    assert abs(normalised - in_pixels) / in_pixels > 0.15


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("square", [(64, 64), (1024, 1024)])
def test_eye_aspect_ratio_is_scale_invariant_on_a_square_frame(face_landmarks, side, square):
    assert crops.eye_aspect_ratio(face_landmarks, side, square) == pytest.approx(
        crops.eye_aspect_ratio(face_landmarks, side), rel=1e-12
    )


@pytest.mark.parametrize("side", ["left", "right"])
def test_open_eyes_on_the_fixture_clear_the_closed_eye_gate(
    face_landmarks, face_image_size, pre, side
):
    ear = crops.eye_aspect_ratio(face_landmarks, side, face_image_size)

    assert pre.min_eye_openness < ear < 0.45


@pytest.mark.parametrize("side", ["left", "right"])
def test_a_closed_eye_scores_far_below_the_openness_gate(pre, side):
    lm = _blank_landmarks()
    outer, upper_a, upper_b, inner, lower_a, lower_b = crops._EAR_IDS[side]
    lm[outer, :2] = (0.40, 0.50)
    lm[inner, :2] = (0.50, 0.50)
    for lid in (upper_a, upper_b, lower_a, lower_b):
        lm[lid, :2] = (0.45, 0.50)  # lids collapsed onto the corner line

    ear = crops.eye_aspect_ratio(lm, side, (400, 400))

    assert ear == pytest.approx(0.0, abs=1e-12)
    assert ear < pre.min_eye_openness


def test_a_zero_width_eye_returns_zero_instead_of_dividing_by_zero():
    assert crops.eye_aspect_ratio(_blank_landmarks(), "left", (400, 400)) == 0.0
    assert crops.eye_aspect_ratio(_blank_landmarks(), "right") == 0.0


def test_eye_aspect_ratio_does_not_mutate_the_caller_landmarks(face_landmarks, face_image_size):
    before = face_landmarks.copy()

    crops.eye_aspect_ratio(face_landmarks, "left", face_image_size)

    assert np.array_equal(face_landmarks, before)


# ==========================================================================
# crops: frame quality
# ==========================================================================


def test_frame_quality_without_a_bbox_still_reports_the_landmark_signals():
    lm = _with_eyes(_blank_landmarks(), left_center=0.6, right_center=0.4)
    lm[crops._EAR_IDS["left"][1], :2] = (0.60, 0.36)

    quality = crops.frame_quality(np.zeros((400, 400, 3), np.uint8), lm, None, (400, 400))

    assert quality.face_area_ratio == 0.0
    assert quality.face_brightness == 0.0
    assert quality.touches_border is False
    assert quality.landmark_visibility == 1.0
    assert quality.left_eye_openness > 0.0


@pytest.mark.parametrize("bbox", [(10, 10, 0, 50), (10, 10, 50, 0)])
def test_a_zero_area_bbox_is_treated_as_no_bbox(bbox):
    quality = crops.frame_quality(
        np.full((400, 400, 3), 200, np.uint8), _blank_landmarks(), bbox, (400, 400)
    )

    assert quality.face_area_ratio == 0.0
    assert quality.face_brightness == 0.0


def test_face_and_background_brightness_split_the_frame_at_the_bbox():
    image = np.full((400, 400, 3), 50, np.uint8)
    image[100:200, 100:200] = 200

    quality = crops.frame_quality(image, _blank_landmarks(), (100, 100, 100, 100), (400, 400))

    assert quality.face_area_ratio == pytest.approx(100 * 100 / (400 * 400))
    assert quality.face_brightness == pytest.approx(200.0, abs=0.05)
    assert quality.background_brightness == pytest.approx(50.0, abs=0.05)
    assert quality.backlight_ratio == pytest.approx(0.25, abs=0.001)
    assert quality.face_contrast == pytest.approx(0.0, abs=0.05)


def test_face_contrast_is_the_luma_spread_inside_the_bbox():
    image = np.full((400, 400, 3), 100, np.uint8)
    image[100:150, 100:200] = 200  # half the face rectangle is bright

    quality = crops.frame_quality(image, _blank_landmarks(), (100, 100, 100, 100), (400, 400))

    assert quality.face_brightness == pytest.approx(150.0, abs=0.05)
    assert quality.face_contrast == pytest.approx(50.0, abs=0.05)


def test_background_brightness_stays_zero_when_the_face_fills_the_frame():
    image = np.full((400, 400, 3), 90, np.uint8)

    quality = crops.frame_quality(image, _blank_landmarks(), (0, 0, 400, 400), (400, 400))

    assert quality.face_brightness == pytest.approx(90.0, abs=0.05)
    assert quality.background_brightness == 0.0
    assert quality.backlight_ratio == 0.0


@pytest.mark.parametrize("keep_rows", [1, 2, 3])
def test_a_nearly_full_frame_face_cannot_report_a_negative_background(keep_rows):
    """The background is a difference of two near-equal million-term sums.

    Accumulated in float32 that difference lost enough low bits to come out
    NEGATIVE (-0.024 on this frame), and ``backlight_ratio`` inherited the sign
    -- a backlight reading no doc 19 bucket can interpret.  float64 plus a
    clamp at zero: a black sliver of background reads ~0, never below it.
    """
    height, width = 958, 1155
    image = np.zeros((height, width, 3), np.uint8)
    image[: height - keep_rows] = 238  # bright face, genuinely black remainder

    quality = crops.frame_quality(
        image,
        _blank_landmarks(),
        (0, 0, width, height - keep_rows),
        (width, height),
    )

    assert quality.face_brightness == pytest.approx(238.0, abs=0.05)
    assert quality.background_brightness >= 0.0
    assert quality.background_brightness == pytest.approx(0.0, abs=0.05)
    assert quality.backlight_ratio >= 0.0


@pytest.mark.parametrize(
    "bbox, touches",
    [
        ((100, 100, 100, 100), False),
        ((2, 100, 100, 100), True),  # left edge, exactly at the tolerance
        ((3, 100, 100, 100), False),  # one pixel clear of it
        ((100, 2, 100, 100), True),
        ((198, 100, 200, 100), True),  # right edge: x + w == width - 2
        ((100, 198, 100, 200), True),
        ((0, 0, 400, 400), True),
    ],
)
def test_border_contact_uses_an_inclusive_pixel_tolerance(bbox, touches):
    quality = crops.frame_quality(
        np.full((400, 400, 3), 120, np.uint8), _blank_landmarks(), bbox, (400, 400), 2
    )

    assert quality.touches_border is touches


@pytest.mark.parametrize(
    "bbox", [(0, 0, 400, 400), (0, 100, 100, 100), (100, 300, 300, 100), (100, 100, 100, 100)]
)
def test_a_negative_border_tolerance_disables_the_out_of_frame_rule(bbox):
    quality = crops.frame_quality(
        np.full((400, 400, 3), 120, np.uint8), _blank_landmarks(), bbox, (400, 400), -1
    )

    assert quality.touches_border is False


# ==========================================================================
# headpose: euler extraction
# ==========================================================================


@pytest.mark.parametrize("angle", [0.0, 0.2, -0.2, 0.9, -0.9])
@pytest.mark.parametrize(
    "axis, expected",
    [
        ("x", lambda t: (0.0, -t, 0.0)),  # pitch = -a
        ("y", lambda t: (-t, 0.0, 0.0)),  # yaw   = -b
        ("z", lambda t: (0.0, 0.0, t)),  # roll  = +c
    ],
)
def test_single_axis_rotations_follow_the_documented_sign_mapping(axis, expected, angle):
    rotation = {"x": _rx, "y": _ry, "z": _rz}[axis](angle)

    result = headpose.euler_from_camera_rotation(rotation)

    assert result == pytest.approx(expected(angle), abs=1e-12)


def test_a_chin_up_pose_is_a_positive_pitch():
    rotation = _rx(-0.2)
    forward = -rotation[:, 2]  # the nose direction in camera coordinates

    yaw, pitch, roll = headpose.euler_from_camera_rotation(rotation)

    assert forward[1] < 0  # image y grows downward, so the nose points up
    assert pitch == pytest.approx(0.2, abs=1e-12)
    assert (yaw, roll) == pytest.approx((0.0, 0.0), abs=1e-12)


def test_a_face_turned_toward_the_image_right_is_a_positive_yaw():
    rotation = _ry(-0.2)
    forward = -rotation[:, 2]

    yaw, pitch, roll = headpose.euler_from_camera_rotation(rotation)

    assert forward[0] > 0
    assert yaw == pytest.approx(0.2, abs=1e-12)
    assert (pitch, roll) == pytest.approx((0.0, 0.0), abs=1e-12)


def test_a_clockwise_in_image_tilt_is_a_positive_roll():
    rotation = _rz(0.2)
    up = -rotation[:, 1]  # the head's up direction in camera coordinates

    yaw, pitch, roll = headpose.euler_from_camera_rotation(rotation)

    assert up[0] > 0  # the crown leans toward the image right
    assert roll == pytest.approx(0.2, abs=1e-12)


@pytest.mark.parametrize(
    "a, b, c",
    [(0.3, 0.2, 0.1), (-0.4, 0.5, -0.2), (0.0, -0.7, 0.35), (1.2, -1.1, 0.9)],
)
def test_a_composed_rotation_round_trips_through_the_extractor(a, b, c):
    rotation = _rz(c) @ _ry(b) @ _rx(a)

    yaw, pitch, roll = headpose.euler_from_camera_rotation(rotation)

    assert (yaw, pitch, roll) == pytest.approx((-b, -a, c), abs=1e-9)


@pytest.mark.parametrize("a, c", [(0.4, 0.25), (0.0, 0.0), (-0.6, 0.3)])
@pytest.mark.parametrize("b, sign", [(math.pi / 2, -1.0), (-math.pi / 2, 1.0)])
def test_gimbal_lock_puts_the_whole_observable_angle_into_pitch(a, c, b, sign):
    # At b = +-90 deg only one combination of a and c survives -- (a - c) for
    # b = +90, (a + c) for b = -90 -- and it is attributed entirely to pitch
    # rather than split arbitrarily, with roll pinned to zero.
    yaw, pitch, roll = headpose.euler_from_camera_rotation(_rz(c) @ _ry(b) @ _rx(a))

    assert roll == 0.0
    assert yaw == pytest.approx(sign * math.pi / 2, abs=1e-9)
    assert pitch == pytest.approx(-(a + sign * c), abs=1e-9)


def test_the_extractor_accepts_a_flat_nine_element_rotation():
    rotation = _rz(0.1) @ _ry(0.2) @ _rx(0.3)

    assert headpose.euler_from_camera_rotation(rotation.reshape(9)) == pytest.approx(
        headpose.euler_from_camera_rotation(rotation)
    )


# ==========================================================================
# headpose: intrinsics
# ==========================================================================


@pytest.mark.parametrize("width", [320, 640, 1920])
def test_the_focal_length_depends_only_on_the_frame_height(width):
    expected = (480 / 2.0) / math.tan(math.radians(headpose.DEFAULT_VERTICAL_FOV_DEG) / 2.0)

    assert headpose.focal_length_px((width, 480)) == pytest.approx(expected)


@pytest.mark.parametrize("image_size", [(820, 1024), (640, 480)])
def test_without_an_image_size_the_focal_is_expressed_in_image_heights(image_size):
    unitless = headpose.focal_length_px(None)

    assert unitless * image_size[1] == pytest.approx(headpose.focal_length_px(image_size))
    assert 0.0 < unitless < 1.0


def test_the_camera_matrix_puts_the_principal_point_at_the_image_centre():
    f = headpose.focal_length_px((820, 1024))

    matrix = headpose.camera_matrix((820, 1024))

    assert matrix == pytest.approx(
        np.asarray([[f, 0.0, 410.0], [0.0, f, 512.0], [0.0, 0.0, 1.0]])
    )


# ==========================================================================
# headpose: the MediaPipe matrix path
# ==========================================================================


def test_the_matrix_path_reports_no_reprojection_error_and_a_frontal_pose():
    pose = headpose.head_pose_from_matrix(_matrix_4x4(np.eye(3), t_z=-65.0), (820, 1024))

    assert (pose.yaw, pose.pitch, pose.roll) == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
    assert pose.reprojection_error == 0.0
    assert pose.depth_proxy > 0.0  # a face in front of the camera


@pytest.mark.parametrize(
    "rotation_gl, expected",
    [
        (_rx(0.25), (0.0, -0.25, 0.0)),  # GL +y is up: rotating about +x drops the nose
        (_ry(0.25), (0.25, 0.0, 0.0)),  # a missing basis change would flip this sign
        (_rz(0.25), (0.0, 0.0, -0.25)),  # and this one
    ],
    ids=["gl_x", "gl_y", "gl_z"],
)
def test_the_matrix_path_changes_basis_out_of_mediapipes_y_up_frame(rotation_gl, expected):
    pose = headpose.head_pose_from_matrix(_matrix_4x4(rotation_gl), (640, 480))

    assert (pose.yaw, pose.pitch, pose.roll) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("a, b, c", [(0.1, -0.2, 0.05), (-0.35, 0.4, -0.15)])
def test_a_camera_frame_rotation_survives_the_round_trip_through_the_matrix(a, b, c):
    rotation_cv = _rz(c) @ _ry(b) @ _rx(a)
    gl = headpose.GL_TO_CV @ rotation_cv @ headpose.GL_TO_CV  # what MediaPipe would emit

    pose = headpose.head_pose_from_matrix(_matrix_4x4(gl), (640, 480))

    assert (pose.yaw, pose.pitch, pose.roll) == pytest.approx((-b, -a, c), abs=1e-9)


def test_the_depth_proxy_is_the_distance_divided_by_the_focal_length():
    matrix = _matrix_4x4(np.eye(3), t_z=-65.0)

    with_size = headpose.head_pose_from_matrix(matrix, (820, 1024)).depth_proxy
    unitless = headpose.head_pose_from_matrix(matrix, None).depth_proxy

    assert with_size == pytest.approx(65.0 / headpose.focal_length_px((820, 1024)))
    # focal_length_px(None) is `height` times smaller, so this proxy is that
    # much larger -- the two are only comparable when both carry an image size.
    assert unitless == pytest.approx(with_size * 1024, rel=1e-9)


# ==========================================================================
# headpose: the solvePnP fallback
# ==========================================================================


def test_the_pnp_model_has_one_point_per_landmark_id():
    assert headpose.PNP_MODEL_POINTS.shape == (len(headpose.PNP_LANDMARK_IDS), 3)
    assert len(set(headpose.PNP_LANDMARK_IDS)) == len(headpose.PNP_LANDMARK_IDS)


@pytest.mark.parametrize("n_rows", [0, 1, 100, 454])
def test_pnp_rejects_a_landmark_array_missing_one_of_its_model_points(n_rows):
    points = np.zeros((n_rows, 2), dtype=np.float64)

    with pytest.raises(ValueError, match="expected at least 455 landmarks"):
        headpose.head_pose_from_landmarks(points, (820, 1024))


@pytest.mark.parametrize("shape", [(478,), (478, 3, 1)])
def test_pnp_rejects_a_landmark_array_of_the_wrong_rank(shape):
    with pytest.raises(ValueError, match="expected at least 455 landmarks"):
        headpose.head_pose_from_landmarks(np.zeros(shape, dtype=np.float64), (820, 1024))


def test_pnp_needs_exactly_the_rows_up_to_its_highest_model_index(face_landmarks, face_image_size):
    pixels = crops.to_pixels(face_landmarks, face_image_size)
    assert max(headpose.PNP_LANDMARK_IDS) == 454

    full = headpose.head_pose_from_landmarks(pixels, face_image_size)
    truncated = headpose.head_pose_from_landmarks(pixels[:455], face_image_size)

    assert truncated.as_array() == pytest.approx(full.as_array())


def test_pnp_ignores_the_landmark_depth_channel(face_landmarks, face_image_size):
    pixels = crops.to_pixels(face_landmarks, face_image_size)
    with_depth = np.hstack([pixels, np.full((pixels.shape[0], 1), 7.0)])

    assert headpose.head_pose_from_landmarks(with_depth, face_image_size).as_array() == (
        pytest.approx(headpose.head_pose_from_landmarks(pixels, face_image_size).as_array())
    )


def test_the_two_head_pose_estimators_agree_on_the_real_face(
    face_landmarks, face_image_size, landmark_result
):
    if landmark_result.transform_matrix is None:
        pytest.skip("no facial transformation matrix in this MediaPipe build")

    from_matrix = headpose.head_pose_from_matrix(landmark_result.transform_matrix, face_image_size)
    from_pnp = headpose.head_pose_from_landmarks(
        crops.to_pixels(face_landmarks, face_image_size), face_image_size
    )

    # PNP_MODEL_POINTS was back-projected from this very fixture, so the two
    # paths must not step the head-pose features when one takes over.
    assert from_pnp.as_array() == pytest.approx(from_matrix.as_array(), abs=math.radians(0.5))
    assert from_pnp.depth_proxy == pytest.approx(from_matrix.depth_proxy, rel=0.01)
    assert from_pnp.reprojection_error < 2.0
    assert from_matrix.reprojection_error == 0.0


def test_the_pnp_solution_is_a_near_frontal_pose_on_the_fixture(face_landmarks, face_image_size):
    pose = headpose.head_pose_from_landmarks(
        crops.to_pixels(face_landmarks, face_image_size), face_image_size
    )

    # The fixture is a face looking into the lens; the mirrored PnP minimum the
    # SQPNP flag exists to avoid would show up here as a large yaw.
    assert abs(math.degrees(pose.yaw)) < 10.0
    assert abs(math.degrees(pose.pitch)) < 10.0
    assert abs(math.degrees(pose.roll)) < 10.0
    assert 0.0 < pose.depth_proxy < 1.0


def test_a_failed_pnp_solve_is_distinguishable_from_a_frontal_face(
    monkeypatch, face_landmarks, face_image_size
):
    """A failed solve must not look like a measurement.

    ``HeadPose()`` is byte-identical to an ideal frontal face at zero distance,
    so the old zeroed return landed in the feature table as if the solver had
    succeeded.  ``inf`` / ``nan`` in the two columns that carry the solve's own
    quality say "no estimate" instead, and both survive the trip: doc 19's
    lean-back bucket keeps only ``depth_proxy > 0`` and the evaluation reader
    substitutes its default for a non-finite cell.
    """
    monkeypatch.setattr(
        headpose.cv2, "solvePnP", lambda *args, **kwargs: (False, None, None)
    )

    pose = headpose.head_pose_from_landmarks(
        crops.to_pixels(face_landmarks, face_image_size), face_image_size
    )

    assert pose != headpose.HeadPose()
    assert math.isinf(pose.reprojection_error) and pose.reprojection_error > 0.0
    assert math.isnan(pose.depth_proxy)
    # No orientation was recovered, so the angles stay at their neutral 0.0.
    assert (pose.yaw, pose.pitch, pose.roll) == (0.0, 0.0, 0.0)


def test_pnp_does_not_mutate_the_caller_landmarks(face_landmarks, face_image_size):
    pixels = crops.to_pixels(face_landmarks, face_image_size)
    before = pixels.copy()

    headpose.head_pose_from_landmarks(pixels, face_image_size)

    assert np.array_equal(pixels, before)


# ==========================================================================
# sampler: decimation
# ==========================================================================


@pytest.mark.parametrize("source_fps", [24.0, 30.0, 60.0, 120.0])
def test_eight_fps_admits_one_frame_per_125_ms_of_stream_time(source_fps):
    sampler = FrameSampler(8.0)
    assert sampler.interval_ms == pytest.approx(125.0)

    accepted = [
        t
        for t in (i * 1000.0 / source_fps for i in range(int(source_fps) * 4))
        if sampler.should_process(t)
    ]

    assert len(accepted) == 32  # 4 seconds at 8 fps
    gaps = np.diff(accepted)
    assert gaps.min() > 125.0 - 1000.0 / source_fps
    assert gaps.max() <= 125.0 + 1000.0 / source_fps
    assert float(gaps.mean()) == pytest.approx(125.0, abs=1000.0 / source_fps)


def test_a_stream_already_at_the_target_rate_is_admitted_whole():
    # The ideal-grid advance plus the tolerance must not drop every other frame
    # of a stream that is already at 8 fps, and must not drift over minutes.
    sampler = FrameSampler(8.0)

    accepted = [i for i in range(400) if sampler.should_process(i * 125.0)]

    assert len(accepted) == 400


@pytest.mark.parametrize(
    "t_ms, accepted",
    [(124.4, False), (124.49, False), (124.5, True), (125.0, True), (200.0, True)],
)
def test_a_frame_up_to_half_a_millisecond_early_still_counts(t_ms, accepted):
    sampler = FrameSampler(8.0)
    sampler.should_process(0.0)

    assert sampler.should_process(t_ms) is accepted


def test_a_backwards_timestamp_restarts_the_schedule():
    sampler = FrameSampler(8.0)
    assert sampler.should_process(1000.0) is True
    assert sampler.should_process(1010.0) is False

    # A seek or a new take, not a late frame: the next frame opens a new stream.
    assert sampler.should_process(0.0) is True
    assert sampler.last_accepted_ms == 0.0
    assert sampler.should_process(10.0) is False


def test_a_stall_resynchronises_instead_of_firing_a_catch_up_burst():
    sampler = FrameSampler(8.0)
    sampler.should_process(0.0)

    assert sampler.should_process(5000.0) is True  # the stream comes back
    # Five seconds of missed deadlines must not admit 40 frames back to back.
    assert [sampler.should_process(t) for t in (5010.0, 5040.0, 5100.0)] == [False] * 3
    assert sampler.should_process(5130.0) is True


@pytest.mark.parametrize("target_fps", [0.0, -1.0, -30.0])
def test_a_non_positive_target_fps_disables_decimation(target_fps):
    sampler = FrameSampler(target_fps)

    assert sampler.interval_ms == 0.0
    assert all(sampler.should_process(t) for t in (0.0, 0.0, 1.0, 0.5, -5.0, 1e6))
    assert sampler.last_accepted_ms == 1e6


def test_only_admitted_frames_move_the_last_accepted_timestamp():
    sampler = FrameSampler(8.0)

    sampler.should_process(1000.0)
    sampler.should_process(1050.0)  # rejected

    assert sampler.last_accepted_ms == 1000.0


def test_reset_forgets_the_schedule_entirely():
    sampler = FrameSampler(8.0)
    sampler.should_process(1000.0)

    sampler.reset()

    assert sampler.last_accepted_ms is None
    assert sampler.should_process(1001.0) is True


# ==========================================================================
# sampler: video iteration
# ==========================================================================


def test_iter_video_frames_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        next(iter_video_frames(tmp_path / "no_such_take.mp4", 8.0))


def test_iter_video_frames_decimates_the_recorded_take_to_the_analysis_rate(face_video):
    frames = list(iter_video_frames(face_video, 8.0))

    assert len(frames) == pytest.approx(14 * 8, abs=2)  # 14 s at 8 fps
    ids = [f[0] for f in frames]
    stamps = [f[1] for f in frames]
    assert ids == sorted(set(ids))
    assert ids[0] == 0 and max(ids) < 420  # source indices, not output indices
    assert all(isinstance(t, int) for t in stamps)
    assert stamps == sorted(set(stamps))
    assert frames[0][2].shape == (480, 640, 3)


def test_iter_video_frames_keeps_every_frame_when_decimation_is_off(face_video):
    kept = sum(1 for _ in iter_video_frames(face_video, 0.0))

    assert kept == 420
