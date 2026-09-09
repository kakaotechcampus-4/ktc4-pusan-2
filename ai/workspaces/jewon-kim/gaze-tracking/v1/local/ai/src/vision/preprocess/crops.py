"""Landmark geometry: bboxes, roll-aligned crops, eye openness, frame quality.

Implements the cropping half of doc 3-1.

Left / right
------------
``side="left"`` always means the SUBJECT's left eye, which in a non-mirrored
frame appears on the IMAGE RIGHT.  That is MediaPipe's own convention: the
wheel's ``FACE_LANDMARKS_LEFT_EYE`` is built from 362..263 and
``FACE_LANDMARKS_LEFT_IRIS`` from 474..477, and on ``tests/fixtures/face.jpg``
those points sit at x ~ 0.55 while the "right" sets (33..133, 469..472) sit at
x ~ 0.44.  Keeping the same meaning here means ``left_eye_crop`` and the
``...Left`` blendshapes describe one eye, not two different ones.

Aspect ratio
------------
Landmarks are normalised by width and height independently, so any distance or
angle taken from them is skewed on a non-square frame.  Every function here
converts to pixels first; ``eye_aspect_ratio`` takes ``image_size`` for exactly
that reason (on the 820x1024 fixture, skipping it changes the EAR by 20%).
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import cv2
import numpy as np

from ..config import PreprocessConfig
from ..schemas import FrameQuality
from .landmarker import in_bounds_fraction

#: The verified landmark index map, kept in one place because getting a side
#: wrong here is invisible until the gaze signal is mirrored.
NOSE_TIP = 1
NOSE_BRIDGE = 168
FOREHEAD = 10
CHIN = 152
#: Subject's left = image right.
LEFT_EYE_OUTER = 263
LEFT_EYE_INNER = 362
MOUTH_LEFT = 291
FACE_SIDE_LEFT = 454
#: Subject's right = image left.
RIGHT_EYE_OUTER = 33
RIGHT_EYE_INNER = 133
MOUTH_RIGHT = 61
FACE_SIDE_RIGHT = 234

#: Iris ring (the centre point 473 / 468 is excluded; averaging the ring is less
#: jittery than trusting the single centre landmark).
LEFT_IRIS_RING: Tuple[int, ...] = (474, 475, 476, 477)
RIGHT_IRIS_RING: Tuple[int, ...] = (469, 470, 471, 472)

#: Classic 6-point eye-aspect-ratio layout (Soukupova & Cech):
#: outer corner, two upper-lid points, inner corner, two lower-lid points.
#: Taken from MediaPipe's own eye contours, mirrored index for index.
_EAR_IDS = {
    "left": (LEFT_EYE_OUTER, 387, 385, LEFT_EYE_INNER, 380, 373),
    "right": (RIGHT_EYE_OUTER, 160, 158, RIGHT_EYE_INNER, 153, 144),
}

_EYE_CORNERS = {
    "left": (LEFT_EYE_OUTER, LEFT_EYE_INNER),
    "right": (RIGHT_EYE_OUTER, RIGHT_EYE_INNER),
}

_IRIS_RINGS = {"left": LEFT_IRIS_RING, "right": RIGHT_IRIS_RING}

#: Smaller than this (in pixels) the eye is a few pixels wide and any crop of it
#: is interpolation noise.
_MIN_EYE_WIDTH_PX = 4.0

#: Rec.601 luma weights, applied to RGB.  float64 rather than float32 on
#: purpose: ``frame_quality`` recovers the background by subtracting the face's
#: luma sum from the whole frame's, and a float32 accumulation over ~1e6 pixels
#: drops enough low bits that a near-full-frame face made the difference come
#: out NEGATIVE (measured -0.024 on a 958x1155 frame), which flipped the sign of
#: backlight_ratio.  Do not "optimise" this back to float32.
_LUMA = np.asarray([0.299, 0.587, 0.114], dtype=np.float64)


def _check_side(side: str) -> str:
    key = str(side).strip().lower()
    if key not in _EAR_IDS:
        raise ValueError("side must be 'left' or 'right', got {!r}".format(side))
    return key


def to_pixels(landmarks: np.ndarray, image_size: Tuple[int, int]) -> np.ndarray:
    """``(N, 2)`` pixel coordinates from normalised landmarks."""
    scale = np.asarray([float(image_size[0]), float(image_size[1])], dtype=np.float64)
    return np.asarray(landmarks, dtype=np.float64)[:, :2] * scale


def eye_corners(
    landmarks: np.ndarray, side: str, image_size: Tuple[int, int]
) -> Tuple[np.ndarray, np.ndarray]:
    """``(outer, inner)`` eye-corner pixel coordinates for one side."""
    outer, inner = _EYE_CORNERS[_check_side(side)]
    pixels = to_pixels(landmarks, image_size)
    return pixels[outer], pixels[inner]


def eye_center(
    landmarks: np.ndarray, side: str, image_size: Tuple[int, int]
) -> np.ndarray:
    """Midpoint of the eye corners, in pixels.

    Deliberately not the iris centre: the corners stay put while the eye moves,
    so an eye crop built on them does not chase the gaze it is meant to measure.
    """
    outer, inner = eye_corners(landmarks, side, image_size)
    return (outer + inner) / 2.0


def iris_center(
    landmarks: np.ndarray, side: str, image_size: Tuple[int, int]
) -> np.ndarray:
    """Iris centre in pixels, averaged over the four ring landmarks."""
    ring = _IRIS_RINGS[_check_side(side)]
    pixels = to_pixels(landmarks, image_size)
    return pixels[list(ring)].mean(axis=0)


def roll_angle_deg(landmarks: np.ndarray, image_size: Tuple[int, int]) -> float:
    """In-image tilt of the eye line, degrees, positive = clockwise.

    Matches ``HeadPose.roll``'s sign: the eye line runs from the image-left eye
    to the image-right eye, so a positive angle means the image-right eye sits
    lower, which is a clockwise tilt of the head.
    """
    left = eye_center(landmarks, "left", image_size)  # image right
    right = eye_center(landmarks, "right", image_size)  # image left
    delta = left - right
    return math.degrees(math.atan2(float(delta[1]), float(delta[0])))


def _landmark_extent(
    landmarks: np.ndarray,
    image_size: Tuple[int, int],
    margin: float = 0.0,
) -> Optional[Tuple[float, float, float, float]]:
    """Margined ``(x0, y0, x1, y1)`` pixel extent of the landmark cloud.

    UNCLAMPED, because the two callers want opposite halves of it:
    ``face_bbox_from_landmarks`` clamps this to the frame to get the VISIBLE
    face, while ``crop_face`` needs the raw rectangle so its square stays
    centred on a face that hangs off an edge.

    Returns ``None`` when the extent is not finite, and this is the ONLY place
    finiteness is checked.  MediaPipe does emit the occasional NaN landmark, and
    the check used to live in ``crop_face`` alone, downstream of
    ``face_bbox_from_landmarks`` -- which had already reached
    ``math.floor(nan)`` and raised ``ValueError``.  So the documented
    ``(None, bbox)`` refusal was unreachable and one bad landmark killed the
    whole take instead of being reported as CROP_FAILED.
    """
    pixels = to_pixels(landmarks, image_size)
    x0, y0 = pixels.min(axis=0)
    x1, y1 = pixels.max(axis=0)

    pad_x = (x1 - x0) * float(margin)
    pad_y = (y1 - y0) * float(margin)
    extent = (
        float(x0 - pad_x),
        float(y0 - pad_y),
        float(x1 + pad_x),
        float(y1 + pad_y),
    )
    if not all(math.isfinite(value) for value in extent):
        return None
    return extent


def face_bbox_from_landmarks(
    landmarks: np.ndarray,
    image_size: Tuple[int, int],
    margin: float = 0.0,
) -> Tuple[int, int, int, int]:
    """``(x, y, w, h)`` pixel bbox around all landmarks, margined and clamped.

    Clamping to the frame is what makes ``face_area_ratio`` mean "visible face",
    which is the quantity the doc 19 small-face and out-of-frame buckets want.

    A non-finite landmark has no rectangle, so the answer is the empty one at
    the origin: ``face_area_ratio`` then reads 0.0, which is the "nothing
    visible" the buckets already understand.  Previously this raised.
    """
    width, height = int(image_size[0]), int(image_size[1])
    extent = _landmark_extent(landmarks, image_size, margin)
    if extent is None:
        return 0, 0, 0, 0
    x0, y0, x1, y1 = extent

    cx0 = int(max(0, math.floor(x0)))
    cy0 = int(max(0, math.floor(y0)))
    cx1 = int(min(width, math.ceil(x1)))
    cy1 = int(min(height, math.ceil(y1)))
    return cx0, cy0, max(0, cx1 - cx0), max(0, cy1 - cy0)


def _aligned_crop(
    image: np.ndarray,
    center: np.ndarray,
    source_width: float,
    out_size: Tuple[int, int],
    angle_deg: float,
) -> np.ndarray:
    """Rotate about ``center`` by ``angle_deg`` CCW, scale, and centre the result.

    The source region keeps the output's aspect ratio, so nothing is stretched.
    Borders replicate instead of filling black: a synthetic black edge would
    show up as a fake luminance step to any CNN backbone and would bias the
    brightness statistics of a face that is partly out of frame.
    """
    out_w, out_h = int(out_size[0]), int(out_size[1])
    scale = out_w / float(source_width)
    cx, cy = float(center[0]), float(center[1])
    matrix = cv2.getRotationMatrix2D((cx, cy), angle_deg, scale)
    matrix[0, 2] += out_w / 2.0 - cx
    matrix[1, 2] += out_h / 2.0 - cy
    return cv2.warpAffine(
        image,
        matrix,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def crop_face(
    rgb: np.ndarray,
    landmarks: np.ndarray,
    cfg: PreprocessConfig,
) -> Tuple[Optional[np.ndarray], Optional[Tuple[int, int, int, int]]]:
    """Square, roll-aligned face crop plus the axis-aligned bbox (doc 3-1).

    The returned bbox is the TIGHT clamped landmark rectangle -- the visible
    face -- not the margined crop window.  ``face_crop_margin`` exists to give a
    CNN some context around the face; treating that padding as part of the face
    would inflate ``face_area_ratio`` by (1 + 2*margin)^2 and would flag every
    close-up webcam frame as touching the border.  The crop square itself is
    built UNCLAMPED so the face stays centred even at a frame edge.

    Returns ``(None, bbox)`` when the visible face is too small to crop, or
    when a landmark is non-finite and there is no extent to crop around.
    """
    height, width = rgb.shape[:2]
    image_size = (width, height)
    # Both rectangles come from _landmark_extent, which is where the single
    # finiteness guard lives: a NaN landmark makes `extent` None AND makes the
    # bbox empty, so this branch is the documented (None, bbox) refusal.  The
    # old code guarded `not np.isfinite(side)` further down, which no frame
    # could ever reach -- face_bbox_from_landmarks raised out of
    # math.floor(nan) first, turning one bad landmark into a dead take.
    extent = _landmark_extent(landmarks, image_size, cfg.face_crop_margin)
    bbox = face_bbox_from_landmarks(landmarks, image_size)
    if extent is None or bbox[2] < 2 or bbox[3] < 2:
        return None, bbox

    x0, y0, x1, y1 = extent
    # `extent` already carries the margin on both sides, and padding is
    # symmetric, so its centre is the unpadded centre and its longer side is
    # the same square the old `(x1 - x0) + 2 * pad_x` expression produced.
    center = np.asarray([(x0 + x1) / 2.0, (y0 + y1) / 2.0])
    side = max(x1 - x0, y1 - y0)
    if side < 2.0:
        return None, bbox

    angle = roll_angle_deg(landmarks, image_size) if cfg.align_face_roll else 0.0
    size = int(cfg.face_crop_size)
    crop = _aligned_crop(rgb, center, side, (size, size), angle)
    return crop, bbox


def crop_eyes(
    rgb: np.ndarray,
    landmarks: np.ndarray,
    cfg: PreprocessConfig,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """``(left_eye, right_eye)`` roll-aligned crops, subject-anatomical sides.

    Each crop covers ``eye_crop_scale`` times the eye's corner-to-corner width,
    so it scales with the face instead of with the frame.  A side is ``None``
    when its eye is degenerate (too small, off-frame, or non-finite).
    """
    height, width = rgb.shape[:2]
    image_size = (width, height)
    angle = roll_angle_deg(landmarks, image_size) if cfg.align_face_roll else 0.0
    out_size = (int(cfg.eye_crop_size[0]), int(cfg.eye_crop_size[1]))

    crops = []
    for side in ("left", "right"):
        outer, inner = eye_corners(landmarks, side, image_size)
        eye_width = float(np.linalg.norm(outer - inner))
        center = (outer + inner) / 2.0
        inside = 0.0 <= center[0] < width and 0.0 <= center[1] < height
        if not np.isfinite(eye_width) or eye_width < _MIN_EYE_WIDTH_PX or not inside:
            crops.append(None)
            continue
        crops.append(
            _aligned_crop(rgb, center, eye_width * float(cfg.eye_crop_scale), out_size, angle)
        )
    return crops[0], crops[1]


def eye_aspect_ratio(
    landmarks: np.ndarray,
    side: str,
    image_size: Optional[Tuple[int, int]] = None,
) -> float:
    """Eye aspect ratio: lid opening over corner-to-corner width (doc 3-1).

    Pass ``image_size`` on any non-square frame -- without it the normalised
    coordinates skew the ratio by width/height.  A wide-open eye is around 0.3,
    a blink well under 0.1; ``PreprocessConfig.min_eye_openness`` sits at 0.12.
    """
    ids = _EAR_IDS[_check_side(side)]
    points = np.asarray(landmarks, dtype=np.float64)[list(ids), :2]
    if image_size is not None:
        points = points * np.asarray(
            [float(image_size[0]), float(image_size[1])], dtype=np.float64
        )
    p1, p2, p3, p4, p5, p6 = points
    span = float(np.linalg.norm(p1 - p4))
    if span < 1e-9:
        return 0.0
    opening = float(np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5))
    return opening / (2.0 * span)


def frame_quality(
    rgb: np.ndarray,
    landmarks: np.ndarray,
    bbox: Optional[Tuple[int, int, int, int]],
    image_size: Tuple[int, int],
    border_tolerance_px: int = 2,
) -> FrameQuality:
    """Cheap per-frame signals for gating and for the doc 19 failure buckets.

    ``bbox`` is the tight face rectangle from ``crop_face``: measuring
    brightness on the frame through that rectangle keeps the crop's replicated
    border and its 25% context margin out of the face/background comparison the
    backlight bucket depends on.  ``border_tolerance_px`` below 0 disables the
    border test (and with it the pipeline's OUT_OF_FRAME rule).
    """
    width, height = int(image_size[0]), int(image_size[1])
    quality = FrameQuality(
        left_eye_openness=eye_aspect_ratio(landmarks, "left", image_size),
        right_eye_openness=eye_aspect_ratio(landmarks, "right", image_size),
        landmark_visibility=in_bounds_fraction(np.asarray(landmarks)),
    )
    if bbox is None or bbox[2] < 1 or bbox[3] < 1:
        return quality

    x, y, w, h = bbox
    quality.face_area_ratio = float(w * h) / float(max(1, width * height))
    quality.touches_border = bool(
        x <= border_tolerance_px
        or y <= border_tolerance_px
        or x + w >= width - border_tolerance_px
        or y + h >= height - border_tolerance_px
    )

    # Luma is materialised for the face rectangle only and the background is
    # recovered by subtraction: converting the whole frame costs 6 ms on a
    # 1 MPix image, which is 5% of the 125 ms per-frame budget for nothing.
    # The accumulation is float64 (see _LUMA) because that subtraction is a
    # difference of two nearly equal million-term sums whenever the face fills
    # the frame, and float32 there is short by more than the answer.
    face = np.asarray(rgb[y : y + h, x : x + w], dtype=np.float64) @ _LUMA
    if face.size:
        quality.face_brightness = float(face.mean())
        quality.face_contrast = float(face.std())
        n_total = width * height
        n_background = n_total - face.size
        if n_background > 0:
            frame_luma = float(np.dot(cv2.mean(rgb)[:3], _LUMA))
            # Clamped at 0: a sliver of genuinely black background can only
            # round to a small residual either way, and a negative luminance
            # is not something backlight_ratio can mean.  Without the clamp a
            # near-full-frame bbox reported background_brightness < 0 and a
            # NEGATIVE backlight_ratio, which no doc 19 bucket expects.
            quality.background_brightness = max(
                0.0,
                float((frame_luma * n_total - float(face.sum())) / n_background),
            )
    return quality
