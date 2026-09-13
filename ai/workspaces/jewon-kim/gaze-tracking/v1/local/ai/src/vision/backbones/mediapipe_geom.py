"""Geometric gaze from MediaPipe iris landmarks + eyeLook* blendshapes (doc 3-2).

This is the default backbone: no download, no torch, and it is what the user
runs on their own webcam before any checkpoint exists.

Geometry
--------
The eyeball is modelled as a sphere of radius ``R`` whose centre ``C`` is fixed
in the head.  The iris centre ``I`` sits on that sphere, so the eye-in-head gaze
direction is ``d = (I - C) / R``.  Under weak perspective (the face is small
next to the camera distance) the image displacement of the iris is
``proj(I) - proj(C) = s * R * (d_x, d_y)`` with ``s`` the pixels-per-millimetre
scale, so two of the three components of ``d`` are directly measurable::

    d_x = offset_x / R_px          d_y = offset_y / R_px
    d_z = sqrt(1 - d_x^2 - d_y^2)  (the eye always points out of the face)

Three quantities have to be pinned down for that to be usable.

**Scale.**  ``R_px`` comes from the iris itself.  The horizontal iris diameter is
one of the most constant dimensions in the human body -- 11.7 +/- 0.5 mm, which
is exactly why MediaPipe's iris model can report metric depth -- so
``R_px = (12.0 / 5.85) * r_iris_px``.  The *horizontal* semi-axis of the iris
ring is used because the upper and lower lids clip the vertical one, badly so
for a squinting or smiling subject.

**Reference point.**  ``proj(C)`` is approximated by the midpoint of the two
canthi (landmarks 33/133 and 362/263).  They are bony, blink-invariant points,
unlike the eye-opening centroid which follows the lids downward and would cancel
a large part of the very downward excursion that separates BOTTOM from CAMERA.
The canthal midpoint carries two constant offsets from the true ``proj(C)``:

* it sits *nasal* to the eyeball centre, because the palpebral fissure reaches
  further temporally than nasally.  This one is self-evident from any frontal
  frame: the bias is mirror-antisymmetric between the eyes while true conjugate
  gaze is common-mode, so on ``tests/fixtures/face.jpg`` (subject looking into
  the lens) the two eyes measure -2.29 px and +2.60 px horizontally -- a
  +/-2.45 px = 0.29 iris-radii temporal bias around a mean of ~0.
* it sits slightly *below* the eyeball centre: the same fixture measures the
  iris 2.87 px / 2.53 px above the canthal midpoint, i.e. ~0.32 iris radii.

Both defaults are rounded to 0.30 iris radii and are config params.  Be honest
about their provenance: the horizontal one is derivable from mirror antisymmetry
on any frame, the vertical one is a *single-sample* calibration against one
fixture whose subject is looking at the lens.  Neither affects the CAMERA/BOTTOM
decision, because a constant offset is absorbed by the per-user logistic
regression of doc 5-3; they only make the raw angles interpretable.

**Head composition.**  The measurement above is eye-in-head.  doc 3-2 wants a
gaze direction in camera coordinates, so the landmark differences are first
de-rotated into the head frame (removing the foreshortening of a turned head)
and the resulting ``d`` is rotated back by the head rotation.  With a zero
eye-in-head offset this returns exactly the head angles, which is the correct
degenerate behaviour and is covered by a unit test.

Blendshapes
-----------
The ARKit-compatible ``eyeLookIn/Out/Up/Down`` scores are a second, independent
eye-in-head estimate: learned, smooth, and far less noisy than a ~5 px iris
displacement, but only piecewise linear in the true angle.  The two estimates
are fused with fixed weights and their disagreement feeds confidence.

Side naming (verified on ``tests/fixtures/face.jpg``, a non-mirrored image)
--------------------------------------------------------------------------
* Landmarks 33/133 lie at x = 348/383 px and 362/263 at x = 430/465 px, so the
  ``33..133`` ring is the eye on the **image left** = the subject's **right**
  eye, and ``362..263`` is the image-right = subject's left eye.
* Iris ring 468-472 has mean x = 363 px and 473-477 has mean x = 450 px, so
  468-472 belongs to the **image-left** eye.  MediaPipe documents that block as
  the "left iris"; that name is mirror-centric and it is physically the
  subject's right eye.  Indices, not names, are used below.
* The blendshape suffix is subject-centric (ARKit convention).  Confirmed
  numerically on the fixture, whose subject is smiling hard enough for the two
  eyes to differ: ``_eye_aspect_ratio`` gives 0.212 for the image-left eye and
  0.197 for the image-right one, so the image-right eye is the more closed of
  the two -- and it is ``eyeSquintLeft`` (0.741 > ``eyeSquintRight`` 0.658) and
  ``eyeBlinkLeft`` (0.270 > 0.257) that are the higher scores.  So suffix
  ``Left`` = subject's left eye = **image right**.

Measured on that fixture (subject looking into the lens, truth ~(0, 0) deg):
head pose (yaw +1.85, pitch -3.86, roll -0.89) deg, output gaze
(yaw +2.74, pitch -2.77) deg at confidence 0.67 in 0.21 ms.  L2CS-Net with the
published Gaze360 weights independently reports (+2.20, -0.23) deg on the same
image, which is the strongest available check on both the geometry and the sign
conversion.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, fields
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from vision.backbones.base import GazeBackbone
from vision.backbones.registry import register
from vision.config import BackboneConfig
from vision.preprocess.landmarker import in_bounds_fraction
from vision.schemas import (
    FrameObservation,
    GazeVector,
    HeadPose,
    clamp_unit,
    unit_vector_to_angles,
)


@dataclass(frozen=True)
class _Eye:
    """Landmark indices for one eye, named by the side of the *image* it lands on."""

    key: str
    #: Suffix of the ARKit blendshape names for this eye (subject-centric).
    blendshape_suffix: str
    #: Corner away from the nose / toward the nose.
    lateral: int
    medial: int
    #: Sign of the temporal (away-from-nose) direction along head-frame +x.
    temporal_sign: float
    #: Iris centre landmark and its four ring points.
    iris_centre: int
    iris_ring: Tuple[int, int, int, int]
    #: Six points of the standard eye-aspect ratio, (h1, h2, v1a, v1b, v2a, v2b).
    ear_points: Tuple[int, int, int, int, int, int]


#: Head-frame +x is image-right when the head is neutral, so the image-left eye
#: has its temporal side toward -x.
_IMG_LEFT = _Eye(
    key="image_left",
    blendshape_suffix="Right",
    lateral=33,
    medial=133,
    temporal_sign=-1.0,
    iris_centre=468,
    iris_ring=(469, 470, 471, 472),
    ear_points=(33, 133, 160, 144, 158, 153),
)
_IMG_RIGHT = _Eye(
    key="image_right",
    blendshape_suffix="Left",
    lateral=263,
    medial=362,
    temporal_sign=1.0,
    iris_centre=473,
    iris_ring=(474, 475, 476, 477),
    ear_points=(362, 263, 385, 380, 387, 373),
)
_EYES: Tuple[_Eye, _Eye] = (_IMG_LEFT, _IMG_RIGHT)

#: Landmark count MediaPipe returns only when the iris refinement ran.
_N_WITH_IRIS = 478

#: Smallest measured iris ring, as a fraction of the interocular-derived radius,
#: that is still believed rather than replaced by that fallback.  A fraction, not
#: a pixel count, because the working frame is in pixels only when the caller
#: supplied an ``image_size`` -- see :func:`_iris_eye_angles`.  Deliberately not a
#: ``_GeomParams`` field: it is a degeneracy test, not a tunable, and every new
#: params key moves ``VisionConfig.hash()`` and invalidates recorded provenance.
_MIN_IRIS_RADIUS_FRACTION = 0.05


@dataclass(frozen=True)
class _GeomParams:
    """Everything tunable, so a run is reproducible from the config hash (doc 18)."""

    #: Bekerman et al. 2014: adult axial length ~24 mm, so a ~12 mm radius.
    eyeball_radius_mm: float = 12.0
    #: Horizontal iris radius; diameter 11.7 +/- 0.5 mm is age- and sex-stable.
    iris_radius_mm: float = 5.85
    #: Canthal-midpoint offsets, in iris radii.  See the module docstring.
    iris_temporal_bias_r: float = 0.30
    iris_superior_bias_r: float = 0.30
    #: Eye rotation that an ``eyeLook*`` score of 1.0 stands for.  ARKit does not
    #: define this in degrees; 30/25 is the usual retargeting-rig assumption and
    #: only sets the gain of the blendshape branch.
    blendshape_yaw_deg: float = 30.0
    blendshape_pitch_deg: float = 25.0
    #: Fusion weights, renormalised when only one source is available.
    iris_weight: float = 0.60
    blendshape_weight: float = 0.40
    #: Physiological ceiling on eye-in-head rotation; clips landmark noise.
    max_eye_angle_deg: float = 45.0
    #: Eye-aspect-ratio band mapped to a 0..1 openness gate.  ``ear_closed``
    #: matches PreprocessConfig.min_eye_openness so the two agree on "closed".
    ear_closed: float = 0.12
    ear_open: float = 0.25
    #: Head yaw at which the far eye is treated as fully occluded.
    occlusion_yaw_deg: float = 70.0
    #: Disagreement between the two estimates that drives confidence to its floor.
    agreement_tolerance_deg: float = 25.0
    #: Confidence multiplier when only one of the two estimates was available.
    single_source_confidence: float = 0.70

    @classmethod
    def from_params(cls, params: Optional[Dict[str, object]]) -> "_GeomParams":
        if not params:
            return cls()
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(params) - known)
        if unknown:
            # A silently ignored typo in gaze_backbone.yaml would make an
            # experiment unreproducible for the worst possible reason.
            raise ValueError(
                f"mediapipe_geom: unknown backbone params {unknown}; valid: {sorted(known)}"
            )
        return cls(**{k: float(v) for k, v in params.items()})


@dataclass
class _EyeMeasurement:
    """Per-eye intermediate result; kept explicit so the fusion stays readable."""

    eye: _Eye
    ear: float
    weight: float
    iris_yaw: Optional[float] = None
    iris_pitch: Optional[float] = None
    bs_yaw: Optional[float] = None
    bs_pitch: Optional[float] = None


def _rotation_head_to_camera(head: HeadPose) -> np.ndarray:
    """Rotation taking head-frame vectors to the camera-aligned working frame.

    The working frame is ``schemas`` with the y axis flipped: ``+x`` image right,
    ``+y`` image **up**, ``+z`` toward the camera.  That makes it right-handed
    and makes ``(0, 0, 1)`` mean "looking straight at the lens", matching the
    zero of the ``schemas`` angle convention (``to_unit_vector(0, 0) == +z``).

    Signs follow the ``schemas`` module docstring: yaw > 0 turns the face toward
    image right (a rotation about +y), pitch > 0 lifts the chin (about -x), roll
    > 0 tilts the head clockwise in the image (about -z).  Applied roll-first,
    then pitch, then yaw, i.e. roll is about the face's own axis.
    """
    cy, sy = math.cos(head.yaw), math.sin(head.yaw)
    cp, sp = math.cos(-head.pitch), math.sin(-head.pitch)
    cr, sr = math.cos(-head.roll), math.sin(-head.roll)
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=np.float64)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]], dtype=np.float64)
    rz = np.array([[cr, -sr, 0.0], [sr, cr, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    return ry @ rx @ rz


def _landmarks_to_working_frame(
    landmarks: np.ndarray, image_size: Optional[Tuple[int, int]]
) -> np.ndarray:
    """Normalised MediaPipe landmarks -> pixel-isotropic points in the working frame.

    MediaPipe normalises x by width and y by height, and documents z as "roughly
    the same scale as x" with the head centre as origin and smaller = closer to
    the camera.  Multiplying x and z by width and y by height therefore restores
    a single isotropic unit, after which the y and z axes are flipped to reach
    the working frame (y up, z toward the camera).

    ``image_size`` is only omitted by callers that never had a frame; the
    fallback treats the normalised coordinates as already isotropic, which is
    exact for a square frame and rescales the vertical angle otherwise.
    """
    if image_size is None:
        width = height = 1.0
    else:
        width, height = float(image_size[0]), float(image_size[1])
    pts = np.asarray(landmarks, dtype=np.float64)
    return np.stack(
        [pts[:, 0] * width, -(pts[:, 1] * height), -(pts[:, 2] * width)], axis=-1
    )


def _eye_aspect_ratio(pts: np.ndarray, eye: _Eye) -> float:
    """Soukupova-Cech eye-aspect ratio in the working frame (blink / squint gate)."""
    h1, h2, v1a, v1b, v2a, v2b = eye.ear_points
    width = float(np.linalg.norm(pts[h1, :2] - pts[h2, :2]))
    if width < 1e-9:
        return 0.0
    lid = float(np.linalg.norm(pts[v1a, :2] - pts[v1b, :2])) + float(
        np.linalg.norm(pts[v2a, :2] - pts[v2b, :2])
    )
    return lid / (2.0 * width)


def _blendshape_eye_angles(
    blendshapes: Dict[str, float], eye: _Eye, params: _GeomParams
) -> Optional[Tuple[float, float]]:
    """Eye-in-head angles from the ARKit ``eyeLook*`` scores, in the schemas signs.

    "Out" is away from the nose, so for the subject's left eye (image right) it
    points toward image right = +yaw, and the sense flips for the other eye.
    "Up" is +pitch in both, matching the schemas convention directly.
    """
    suffix = eye.blendshape_suffix
    try:
        look_in = float(blendshapes[f"eyeLookIn{suffix}"])
        look_out = float(blendshapes[f"eyeLookOut{suffix}"])
        look_up = float(blendshapes[f"eyeLookUp{suffix}"])
        look_down = float(blendshapes[f"eyeLookDown{suffix}"])
    except (KeyError, TypeError, ValueError):
        return None
    outward = look_out - look_in
    horizontal = outward if suffix == "Left" else -outward
    return (
        horizontal * math.radians(params.blendshape_yaw_deg),
        (look_up - look_down) * math.radians(params.blendshape_pitch_deg),
    )


def _iris_eye_angles(
    pts_work: np.ndarray,
    rotation: np.ndarray,
    eye: _Eye,
    params: _GeomParams,
    fallback_radius_px: float,
) -> Optional[Tuple[float, float]]:
    """Eye-in-head angles from the iris sphere model, or ``None`` to abstain.

    ``None`` means "this eye measured nothing usable", which the caller turns
    into a blendshape-only estimate -- or, when there is no blendshape either,
    into a zero-confidence frame.  It never means "straight ahead".
    """
    centre = 0.5 * (pts_work[eye.lateral] + pts_work[eye.medial])
    iris = pts_work[eye.iris_centre]
    ring = pts_work[list(eye.iris_ring)]

    # Row-vector convention: ``v @ R`` is ``R.T @ v``, i.e. camera -> head.
    offset = (iris - centre) @ rotation
    ring_head = (ring - iris) @ rotation

    # Horizontal semi-axis: the nasal and temporal iris edges are the only two
    # that no eyelid can cover, so they survive a squint that ruins the mean.
    #
    # The floor is a fraction of the interocular-derived radius rather than a
    # literal 0.5.  0.5 was a *pixel* constant, but the working frame is only in
    # pixels when the caller passed an ``image_size``; with ``image_size=None``
    # the frame is one unit wide and a real iris ring measures ~0.010, so every
    # ring fell under the old floor, the fallback fell under it too, and the
    # whole iris branch was dropped -- the backbone then returned the head
    # direction dressed up as a gaze estimate.  ``fallback_radius_px`` comes from
    # the same landmarks in the same working frame, so the ratio means the same
    # thing in both unit systems; 0.05 of it is 0.38 px on tests/fixtures/face.jpg,
    # i.e. the half-pixel the literal used to mean, while a healthy ring measures
    # ~1.1x the fallback (a 22x margin) in either unit system.
    floor = _MIN_IRIS_RADIUS_FRACTION * fallback_radius_px
    radius = float(np.max(np.abs(ring_head[:, 0])))
    if not math.isfinite(radius) or radius < floor:
        radius = fallback_radius_px
    if not math.isfinite(radius) or radius <= 0.0:
        # Both the ring and the interocular fallback are degenerate: there is no
        # scale in this frame at all.  (The old absolute test also rejected any
        # face smaller than ~10 px across; ``eyeball_px`` below still guards the
        # division, and a face that small has no iris to localise anyway.)
        return None

    eyeball_px = radius * (params.eyeball_radius_mm / params.iris_radius_mm)
    if eyeball_px < 1e-6:
        return None

    dx = offset[0] - eye.temporal_sign * params.iris_temporal_bias_r * radius
    dy = offset[1] - params.iris_superior_bias_r * radius

    limit = math.sin(math.radians(params.max_eye_angle_deg))
    # ``clamp_unit`` (schemas), not ``max(-1.0, min(1.0, ...))``: that clip maps
    # NaN to 1.0, so a non-finite iris landmark used to come out of ``asin`` as a
    # confident pi/2 -- after the head rotation, a full -90 deg gaze_pitch at
    # ~0.65 confidence, the strongest possible false BOTTOM.  The old finiteness
    # test covered only the *radius*, which a NaN iris centre walks straight past
    # because the ring can be perfectly measurable around an unmeasurable centre.
    sx = clamp_unit(float(np.clip(dx / eyeball_px, -limit, limit)))
    sy = clamp_unit(float(np.clip(dy / eyeball_px, -limit, limit)))
    if sx is None or sy is None:
        return None
    # sx, sy are proven finite above, so this floor is no longer load-bearing for
    # NaN (``max(1e-9, 1.0 - nan)`` used to return 1e-9 and hide it).  It still
    # matters for real values: at the default 45 deg limit both components can
    # sit at sin(45 deg) at once, which is exactly a unit vector and leaves 0.
    dz = math.sqrt(max(1e-9, 1.0 - sx * sx - sy * sy))
    return math.atan2(sx, dz), math.asin(sy)


def _weighted_mean(
    values: Sequence[Optional[float]], weights: Sequence[float]
) -> Optional[float]:
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    if total <= 1e-6:
        # Both eyes gated to zero (deep blink): keep the measurement rather than
        # dropping it, and let confidence -- not the angle -- carry the doubt.
        return float(np.mean([v for v, _ in pairs]))
    return float(sum(v * w for v, w in pairs) / total)


def _synthetic_landmarks() -> np.ndarray:
    """A minimal frontal-face landmark array for warmup and unit tests.

    Only the indices this backbone reads are meaningful; everything else sits at
    the face centre.  The irises are nudged slightly out and up so the array is
    not accidentally the exact zero of the estimator.
    """
    pts = np.zeros((_N_WITH_IRIS, 3), dtype=np.float64)
    pts[:, 0] = 0.5
    pts[:, 1] = 0.5
    half_width, lid, iris_r = 0.035, 0.011, 0.012
    for eye, cx in ((_IMG_LEFT, 0.42), (_IMG_RIGHT, 0.58)):
        cy = 0.42
        outward = -1.0 if eye is _IMG_LEFT else 1.0
        pts[eye.lateral] = (cx + outward * half_width, cy, 0.0)
        pts[eye.medial] = (cx - outward * half_width, cy, 0.0)
        centre = np.array([cx + outward * 0.30 * iris_r, cy - 0.30 * iris_r, 0.0])
        pts[eye.iris_centre] = centre
        for point, (dx, dy) in zip(
            eye.iris_ring, ((iris_r, 0.0), (0.0, -iris_r), (-iris_r, 0.0), (0.0, iris_r))
        ):
            pts[point] = centre + np.array([dx, dy, 0.0])
        _, _, v1a, v1b, v2a, v2b = eye.ear_points
        for upper, lower, offset in ((v1a, v1b, -0.4), (v2a, v2b, 0.4)):
            pts[upper] = (cx + offset * half_width, cy - lid, 0.0)
            pts[lower] = (cx + offset * half_width, cy + lid, 0.0)
    return pts


@register("mediapipe_geom")
class MediaPipeGeomBackbone(GazeBackbone):
    """Iris-geometry + blendshape gaze estimator (doc 3-2, the doc 3-3 default)."""

    version = "1.0.0"

    def __init__(self, cfg: Optional[BackboneConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else BackboneConfig(name="mediapipe_geom")
        self.params = _GeomParams.from_params(getattr(self.cfg, "params", None))

    def predict(
        self,
        face_crop: Optional[np.ndarray],
        left_eye_crop: Optional[np.ndarray],
        right_eye_crop: Optional[np.ndarray],
        head_pose: Optional[HeadPose],
        *,
        landmarks: Optional[np.ndarray] = None,
        blendshapes: Optional[Dict[str, float]] = None,
        image_size: Optional[Tuple[int, int]] = None,
    ) -> GazeVector:
        """Gaze in camera coordinates from landmarks and/or blendshapes (doc 3-2).

        The crops are ignored: this backbone reads geometry, not pixels.  Raises
        :class:`ValueError` when neither landmarks nor blendshapes are supplied,
        because the only alternative would be to return the head direction
        dressed up as a gaze estimate.
        """
        t0 = time.perf_counter()
        params = self.params
        head = head_pose if head_pose is not None else HeadPose()
        rotation = _rotation_head_to_camera(head)

        pts_work: Optional[np.ndarray] = None
        if landmarks is not None:
            arr = np.asarray(landmarks, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[0] >= _N_WITH_IRIS and arr.shape[1] >= 3:
                pts_work = _landmarks_to_working_frame(arr[:, :3], image_size)
        if pts_work is None and not blendshapes:
            raise ValueError(
                "mediapipe_geom needs either (478, 3) landmarks with the iris "
                "refinement or MediaPipe blendshapes; got neither"
            )

        # One backup scale shared by both eyes, so a lid covering one iris cannot
        # silently rescale just that eye.
        fallback = self._interocular_radius(pts_work, rotation) if pts_work is not None else 0.0

        measurements: List[_EyeMeasurement] = []
        for eye in _EYES:
            ear = _eye_aspect_ratio(pts_work, eye) if pts_work is not None else params.ear_open
            measurement = _EyeMeasurement(eye=eye, ear=ear, weight=self._eye_weight(ear, eye, head))
            if pts_work is not None:
                iris = _iris_eye_angles(pts_work, rotation, eye, params, fallback)
                if iris is not None:
                    measurement.iris_yaw, measurement.iris_pitch = iris
            if blendshapes:
                blend = _blendshape_eye_angles(blendshapes, eye, params)
                if blend is not None:
                    measurement.bs_yaw, measurement.bs_pitch = blend
            measurements.append(measurement)

        weights = [m.weight for m in measurements]
        iris_yaw = _weighted_mean([m.iris_yaw for m in measurements], weights)
        iris_pitch = _weighted_mean([m.iris_pitch for m in measurements], weights)
        bs_yaw = _weighted_mean([m.bs_yaw for m in measurements], weights)
        bs_pitch = _weighted_mean([m.bs_pitch for m in measurements], weights)

        eye_yaw, eye_pitch, n_sources = self._fuse(iris_yaw, iris_pitch, bs_yaw, bs_pitch)

        # Eye-in-head unit vector in the working frame (y up, z out of the face),
        # rotated by the head so the result is a camera-frame direction.
        cos_p = math.cos(eye_pitch)
        d_head = np.array(
            [cos_p * math.sin(eye_yaw), math.sin(eye_pitch), cos_p * math.cos(eye_yaw)]
        )
        d_cam = rotation @ d_head
        # Back to the schemas frame, which is this one with y pointing down.
        gaze_yaw, gaze_pitch = unit_vector_to_angles((d_cam[0], -d_cam[1], d_cam[2]))

        confidence = self._confidence(
            measurements=measurements,
            head=head,
            landmarks=landmarks,
            n_sources=n_sources,
            iris=(iris_yaw, iris_pitch),
            blend=(bs_yaw, bs_pitch),
        )
        return GazeVector(
            gaze_yaw=float(gaze_yaw),
            gaze_pitch=float(gaze_pitch),
            confidence=float(confidence),
            backbone=self.name,
            inference_ms=self._elapsed_ms(t0),
        )

    # -- internals --------------------------------------------------------

    def _interocular_radius(self, pts_work: np.ndarray, rotation: np.ndarray) -> float:
        """Backup iris radius derived from the distance between the eyeball centres.

        Used only when an iris ring is missing or degenerate.  Mean adult
        interpupillary distance is ~63 mm (Dodgson 2004), so the eyeball radius
        is ~12/63 of it; the result is converted back into iris radii so the
        caller can keep applying the same bias constants.
        """
        left = 0.5 * (pts_work[_IMG_LEFT.lateral] + pts_work[_IMG_LEFT.medial])
        right = 0.5 * (pts_work[_IMG_RIGHT.lateral] + pts_work[_IMG_RIGHT.medial])
        iod = float(np.linalg.norm((right - left) @ rotation))
        eyeball_px = iod * (self.params.eyeball_radius_mm / 63.0)
        return eyeball_px * (self.params.iris_radius_mm / self.params.eyeball_radius_mm)

    def _eye_weight(self, ear: float, eye: _Eye, head: HeadPose) -> float:
        """How much this eye contributes: openness x self-occlusion."""
        params = self.params
        span = max(1e-6, params.ear_open - params.ear_closed)
        openness = float(np.clip((ear - params.ear_closed) / span, 0.0, 1.0))
        # A head turned toward image right (+yaw) rotates the image-right eye
        # away from the camera; that is the eye whose iris localisation decays.
        away = head.yaw if eye is _IMG_RIGHT else -head.yaw
        limit = math.radians(max(1.0, params.occlusion_yaw_deg))
        occlusion = float(np.clip(1.0 - max(0.0, away) / limit, 0.0, 1.0))
        return openness * occlusion

    def _fuse(
        self,
        iris_yaw: Optional[float],
        iris_pitch: Optional[float],
        bs_yaw: Optional[float],
        bs_pitch: Optional[float],
    ) -> Tuple[float, float, int]:
        params = self.params
        w_iris = params.iris_weight if iris_yaw is not None else 0.0
        w_blend = params.blendshape_weight if bs_yaw is not None else 0.0
        total = w_iris + w_blend
        if total <= 0.0:
            return 0.0, 0.0, 0
        yaw = (w_iris * (iris_yaw or 0.0) + w_blend * (bs_yaw or 0.0)) / total
        pitch = (w_iris * (iris_pitch or 0.0) + w_blend * (bs_pitch or 0.0)) / total
        return yaw, pitch, int(w_iris > 0.0) + int(w_blend > 0.0)

    def _confidence(
        self,
        *,
        measurements: Sequence[_EyeMeasurement],
        head: HeadPose,
        landmarks: Optional[np.ndarray],
        n_sources: int,
        iris: Tuple[Optional[float], Optional[float]],
        blend: Tuple[Optional[float], Optional[float]],
    ) -> float:
        """Confidence in [0, 1] from eye openness and landmark visibility (doc 3-2).

        Openness is a hard multiplier -- a closed eye has no gaze to report -- and
        the remaining terms only modulate, so a wide-open eye in an awkward pose
        still scores usefully above zero instead of collapsing through a product
        of four sub-unit factors.  A frame in which *neither* branch measured
        anything is not scored on those terms at all; see below.
        """
        if n_sources <= 0:
            # No branch produced an estimate, so ``_fuse`` returned (0, 0) and the
            # reported angle is the head direction, not a measurement of the eyes.
            # Scoring that on openness/pose/visibility -- which is what happened
            # before -- is how a frame whose iris landmarks were all NaN came back
            # at 0.65 confidence.  Wide-open eyes say nothing about a gaze nobody
            # managed to measure.
            return 0.0

        params = self.params
        span = max(1e-6, params.ear_open - params.ear_closed)
        # max, not mean: one clearly open eye is enough to localise an iris.
        openness = max(
            float(np.clip((m.ear - params.ear_closed) / span, 0.0, 1.0)) for m in measurements
        )

        if n_sources >= 2 and iris[0] is not None and blend[0] is not None:
            error = math.hypot(
                iris[0] - (blend[0] or 0.0), (iris[1] or 0.0) - (blend[1] or 0.0)
            )
            tolerance = math.radians(max(1.0, params.agreement_tolerance_deg))
            agreement = float(np.clip(1.0 - error / tolerance, 0.0, 1.0))
        else:
            agreement = params.single_source_confidence

        pose = float(np.clip(math.cos(head.yaw) * math.cos(head.pitch), 0.0, 1.0))
        # ``in_bounds_fraction`` is the shared definition of this metric (it also
        # fills ``FrameQuality.landmark_visibility``), so the two can never drift.
        # "No landmarks means do not penalise" is *this backbone's* policy and
        # stays here at the call site: a blendshape-only frame simply was not
        # measured for out-of-frame points, whereas the shared metric answers 0.0
        # for an empty array because that is the right answer for a quality field.
        # The shape test is the same guard the private copy carried.
        if landmarks is None:
            visibility = 1.0
        else:
            arr = np.asarray(landmarks, dtype=np.float64)
            measurable = arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 2
            visibility = in_bounds_fraction(arr) if measurable else 1.0

        modulation = 0.40 * agreement + 0.35 * pose + 0.25 * visibility
        return float(np.clip(openness * (0.30 + 0.70 * modulation), 0.0, 1.0))

    def _warmup_observation(self) -> FrameObservation:
        """Synthetic frontal face, so warmup exercises the real geometry path."""
        obs = super()._warmup_observation()
        obs.landmarks = _synthetic_landmarks()
        obs.blendshapes = {
            f"eyeLook{direction}{side}": 0.1
            for direction in ("In", "Out", "Up", "Down")
            for side in ("Left", "Right")
        }
        return obs
