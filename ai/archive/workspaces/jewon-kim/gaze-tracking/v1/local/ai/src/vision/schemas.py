"""Core data contracts for the Pitch Coach vision pipeline.

This module is the single source of truth for every structure that crosses a
module boundary (preprocess -> backbone -> calibration -> temporal -> runtime).
Nothing here imports torch, mediapipe or cv2, so it stays cheap to import from
tests and tools.

Sign conventions (fixed once, enforced everywhere)
--------------------------------------------------
All angles are RADIANS unless the field name ends with ``_deg``.

Camera coordinate system follows OpenCV: ``+x`` right in the image, ``+y`` down
in the image, ``+z`` away from the camera into the scene.  A gaze direction is
the unit vector ``g`` pointing from the eye toward whatever the person looks
at, expressed in that frame.  We reduce it to two angles::

    gaze_pitch = -asin(g_y)         # > 0 looking UP,    < 0 looking DOWN
    gaze_yaw   =  atan2(g_x, g_z)   # > 0 gaze toward the RIGHT side of image

Consequence that every backbone adapter must respect: BOTTOM (reading a script
below the screen) has a MORE NEGATIVE ``gaze_pitch`` than CAMERA.
``evaluation.sanity`` (``ai/evaluation/sanity.py``, outside ``ai/src`` and so
not part of the installed ``vision`` package) checks this per backbone on real
calibration data; a violation means an adapter failed to convert its native
convention.

The check is ADVISORY, not enforced.  ``sanity.pitch_ordering_report`` is
reported next to the metrics but is not one of the six doc 7 release-gate rows;
``calibration.quality`` only prefixes a warning onto ``CalibrationQuality.hint``
and leaves ``status``/``reason`` untouched; and only ``exp1_backbone
--pitch-veto`` makes an INVERTED verdict binding.  A violation is meant to be
read, not to fail a run, because it can equally mean the user was cued the wrong
way round.

Head pose uses the same convention: ``head_pitch`` > 0 is chin up, < 0 is chin
down; ``head_yaw`` > 0 is the face turned toward the right of the image;
``head_roll`` > 0 is a clockwise in-image tilt.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


class GazeLabel(str, Enum):
    """Ground-truth frame/segment label (doc 4-2), and the calibration cue vocabulary.

    ``SCREEN`` (look at the centre of the screen) is a calibration cue: the
    reference-anchor classifier needs it to size the screen region and to read
    the camera placement off the same frames.  The recorded datasets and the
    release-gate metrics still only carry CAMERA / BOTTOM ground truth.
    """

    CAMERA = "CAMERA"
    BOTTOM = "BOTTOM"
    IGNORE = "IGNORE"
    SCREEN = "SCREEN"

    @classmethod
    def coerce(cls, value):
        # ``getattr(value, "value", value)`` first: a `str, Enum` member of the
        # SIBLING enum stringifies as "GazeState.CAMERA", not "CAMERA", so the
        # bare str() rejected a member that already *is* the right wire string.
        # Every dataclass here types these fields as plain ``str``, so such a
        # member type-checks at the call site and only exploded in here.
        # Unwrapping the wire value keeps the vocabularies apart -- IGNORE and
        # UNCERTAIN still fail, because it is the string that is looked up.
        if isinstance(value, cls):
            return value
        return cls(str(getattr(value, "value", value)).strip().upper())


class GazeState(str, Enum):
    """Predicted state emitted by the runtime (doc 5-4 / 6-2).

    ``SCREEN`` is the rest of the screen (slides, the middle of the display),
    ``OTHER`` is a confident "looking at none of the calibrated targets" --
    away from the screen, or the head turned far off the calibration pose.
    Neither is an abstention: ``UNCERTAIN`` alone means "could not tell".
    """

    CAMERA = "CAMERA"
    BOTTOM = "BOTTOM"
    UNCERTAIN = "UNCERTAIN"
    SCREEN = "SCREEN"
    OTHER = "OTHER"

    @classmethod
    def coerce(cls, value):
        # Same unwrap-then-str as :meth:`GazeLabel.coerce`; see the note there
        # for why the bare str() rejected a sibling member that already is the
        # right wire string.
        if isinstance(value, cls):
            return value
        return cls(str(getattr(value, "value", value)).strip().upper())


#: Ground-truth classes the release gate scores (doc 7), in fixed order.  Also
#: the class set of the logistic classifier and of a two-class smoother.  The
#: recorded datasets only carry these two labels, so the evaluation truth
#: vocabulary stays at two even though the runtime can decide more.
DECISION_CLASSES: Tuple[str, str] = (GazeLabel.CAMERA.value, GazeLabel.BOTTOM.value)

#: Every state the reference-anchor classifier can decide, in tie-break order:
#: when two classes score exactly the same, the earlier one wins (so CAMERA
#: still beats BOTTOM on a dead heat, as it always has).
STATE_CLASSES: Tuple[str, ...] = (
    GazeState.CAMERA.value,
    GazeState.SCREEN.value,
    GazeState.BOTTOM.value,
    GazeState.OTHER.value,
)

#: Decided states that are neither CAMERA nor BOTTOM.  Against CAMERA/BOTTOM
#: ground truth they are confident misses, not abstentions.
OFF_TARGET_STATES: Tuple[str, ...] = (GazeState.SCREEN.value, GazeState.OTHER.value)

#: The three calibration cues, in the order the runtime asks for them.
CALIBRATION_CUES: Tuple[str, ...] = (
    GazeLabel.CAMERA.value,
    GazeLabel.SCREEN.value,
    GazeLabel.BOTTOM.value,
)

#: Where an OTHER look went, from the PRESENTER's point of view (their own left
#: and right, not the image's), in 45-degree sectors counter-clockwise from
#: their right.  The order is also the tie-break order when slices are merged.
GAZE_DIRECTIONS: Tuple[str, ...] = (
    "RIGHT",
    "UP_RIGHT",
    "UP",
    "UP_LEFT",
    "LEFT",
    "DOWN_LEFT",
    "DOWN",
    "DOWN_RIGHT",
)


class InvalidReason(str, Enum):
    """Why a frame produced no usable gaze estimate."""

    NO_FACE = "NO_FACE"
    LOW_FACE_CONFIDENCE = "LOW_FACE_CONFIDENCE"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    OUT_OF_FRAME = "OUT_OF_FRAME"
    EYES_CLOSED = "EYES_CLOSED"
    CROP_FAILED = "CROP_FAILED"
    BACKBONE_FAILED = "BACKBONE_FAILED"


class CalibrationStatus(str, Enum):
    OK = "OK"
    RETRY_REQUIRED = "RETRY_REQUIRED"


class CalibrationFailReason(str, Enum):
    """Why doc 5-2 asked for a calibration retry.

    Every member except :attr:`INVERTED_PITCH` can be assigned to
    ``CalibrationQuality.reason``.  ``ANCHOR_AMBIGUOUS`` is only produced by
    the reference-anchor classifier (``calibration.references``); the logistic
    path (``calibration.quality._fail``) emits the first five.
    """

    NOT_ENOUGH_SAMPLES = "NOT_ENOUGH_SAMPLES"
    CLASS_NOT_SEPARABLE = "CLASS_NOT_SEPARABLE"
    LOW_LOO_ACCURACY = "LOW_LOO_ACCURACY"
    CENTROIDS_TOO_CLOSE = "CENTROIDS_TOO_CLOSE"
    DEGENERATE_FEATURES = "DEGENERATE_FEATURES"
    #: A calibration anchor does not even classify as itself: its region is
    #: so diluted (a very large screen box, a script box nested too loosely)
    #: that the decision rule could never pick that class.
    ANCHOR_AMBIGUOUS = "ANCHOR_AMBIGUOUS"
    #: ADVISORY ONLY -- never assigned to ``CalibrationQuality.reason``.  An
    #: inverted pitch ordering is a warning, not a failure (see the module
    #: docstring), so ``calibration.quality`` puts the literal string
    #: ``"INVERTED_PITCH: ..."`` into ``CalibrationQuality.hint`` and leaves the
    #: status OK.  Detect it with :data:`INVERTED_PITCH_HINT_PREFIX` rather than
    #: by comparing ``reason``, which will never hold this value.
    INVERTED_PITCH = "INVERTED_PITCH"


#: Marker ``calibration.quality`` puts into ``CalibrationQuality.hint`` when the
#: advisory pitch-ordering check fires.  This is the only runtime signal for
#: :attr:`CalibrationFailReason.INVERTED_PITCH`, so match on this constant rather
#: than hand-writing the literal.
#:
#: Test it with ``in``, NOT ``startswith``.  The marker only leads the string on
#: the passing path (``quality.hint = warning``); when the calibration ALSO fails
#: a doc 5-2 gate, ``_fail`` builds ``f"{hint} {warning}"``, so the primary
#: failure hint comes first and a prefix-anchored match silently misses the
#: inversion on exactly the runs that have two things wrong at once::
#:
#:     inverted = bool(quality.hint) and INVERTED_PITCH_HINT_PREFIX in quality.hint
INVERTED_PITCH_HINT_PREFIX: str = "INVERTED_PITCH:"


# --------------------------------------------------------------------------
# Preprocess output
# --------------------------------------------------------------------------


@dataclass
class HeadPose:
    """Head orientation in radians (see module docstring for signs)."""

    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    #: Reprojection error of the PnP solve, in pixels. Higher = less trustworthy.
    reprojection_error: float = 0.0
    #: Camera-to-face distance in focal-normalised units (larger = further away).
    depth_proxy: float = 0.0

    def as_array(self) -> np.ndarray:
        return np.asarray([self.yaw, self.pitch, self.roll], dtype=np.float32)

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class FrameQuality:
    """Cheap per-frame signals used for gating and for doc-19 failure buckets."""

    #: Face bbox area / full frame area. Small face is a known failure bucket.
    face_area_ratio: float = 0.0
    #: Mean luminance (0-255) inside the face crop.
    face_brightness: float = 0.0
    #: Mean luminance (0-255) outside the face bbox -- backlight detector.
    background_brightness: float = 0.0
    #: RMS contrast inside the face crop.
    face_contrast: float = 0.0
    #: Eye aspect ratio per eye; low values mean blink / closed eyes.
    left_eye_openness: float = 0.0
    right_eye_openness: float = 0.0
    #: Fraction of landmarks that stayed inside the image bounds.
    landmark_visibility: float = 1.0
    #: True when the face bbox touches the frame border.
    touches_border: bool = False

    @property
    def backlight_ratio(self) -> float:
        """Background over face luminance; > ~1.6 indicates strong backlight."""
        if self.face_brightness <= 1e-6:
            return 0.0
        return float(self.background_brightness / self.face_brightness)

    @property
    def min_eye_openness(self) -> float:
        return float(min(self.left_eye_openness, self.right_eye_openness))

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["backlight_ratio"] = self.backlight_ratio
        return out


@dataclass
class FaceScene:
    """What the frame looks like around the main face (runtime only).

    Kept apart from :class:`FrameQuality` on purpose: ``FrameQuality`` is
    flattened into the frozen feature-table schema (doc 20), and these signals
    exist for the precondition check and the live condition monitor, not for
    the recorded dataset.
    """

    #: Faces MediaPipe found in this frame (0 when none).
    n_faces: int = 0
    #: Largest *other* face's bbox area over the main face's (0 when alone).
    #: A person leaning in beside the presenter reads close to 1.
    second_face_area_ratio: float = 0.0
    #: Mean iris diameter of the main face in source-frame pixels (0 unknown).
    #: The direct precision proxy: an eye rotation of ~9 deg moves the iris by
    #: only ~16% of its diameter, so fewer iris pixels means coarser gaze.
    iris_diameter_px: float = 0.0
    #: Pixel bbox of every detected face, the main face first.
    face_bboxes: List[Tuple[int, int, int, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FrameObservation:
    """Everything preprocess produces for one analysed frame (doc 3-1).

    Image arrays are ``uint8`` RGB, ``HxWx3``.  They are ``None`` when the frame
    is invalid, or when the observation was rehydrated from a feature cache
    (offline evaluation keeps angles, not pixels -- doc 20).
    """

    frame_id: int
    t_ms: int
    face_confidence: float
    face_valid: bool
    head_pose: HeadPose = field(default_factory=HeadPose)
    quality: FrameQuality = field(default_factory=FrameQuality)
    face_crop: Optional[np.ndarray] = None
    left_eye_crop: Optional[np.ndarray] = None
    right_eye_crop: Optional[np.ndarray] = None
    #: (N, 3) landmark coordinates in normalised [0, 1] image space.
    #: MediaPipe returns 478 points; 468-477 are the two iris contours.
    landmarks: Optional[np.ndarray] = None
    #: MediaPipe blendshape scores, e.g. ``eyeLookDownLeft``.  The
    #: ``mediapipe_geom`` backbone consumes these directly.
    blendshapes: Optional[Dict[str, float]] = None
    #: (x, y, w, h) face bbox in pixels.
    face_bbox: Optional[Tuple[int, int, int, int]] = None
    #: (width, height) of the source frame in pixels.
    image_size: Optional[Tuple[int, int]] = None
    invalid_reason: Optional[str] = None
    #: Wall-clock cost of preprocessing this frame.
    preprocess_ms: float = 0.0
    #: Multi-face and framing signals (runtime only; not part of ``to_record``).
    scene: Optional[FaceScene] = None

    def to_record(self) -> Dict[str, Any]:
        """Serialisable view without pixels (for manifests and caches)."""
        return {
            "frame_id": self.frame_id,
            "t_ms": self.t_ms,
            "face_confidence": self.face_confidence,
            "face_valid": self.face_valid,
            "head_yaw": self.head_pose.yaw,
            "head_pitch": self.head_pose.pitch,
            "head_roll": self.head_pose.roll,
            "head_reprojection_error": self.head_pose.reprojection_error,
            "head_depth_proxy": self.head_pose.depth_proxy,
            "invalid_reason": self.invalid_reason,
            "preprocess_ms": self.preprocess_ms,
            **{"q_" + k: v for k, v in self.quality.to_dict().items()},
        }


# --------------------------------------------------------------------------
# Backbone output
# --------------------------------------------------------------------------


@dataclass
class GazeVector:
    """Backbone output (doc 3-2).  Angles in radians, see module docstring."""

    gaze_yaw: float
    gaze_pitch: float
    confidence: float
    backbone: str = "unknown"
    #: Wall-clock inference cost attributed to this single frame.
    inference_ms: float = 0.0

    @property
    def gaze_yaw_deg(self) -> float:
        return math.degrees(self.gaze_yaw)

    @property
    def gaze_pitch_deg(self) -> float:
        return math.degrees(self.gaze_pitch)

    def as_array(self) -> np.ndarray:
        return np.asarray([self.gaze_yaw, self.gaze_pitch], dtype=np.float32)

    def to_unit_vector(self) -> np.ndarray:
        """Inverse of the projection documented at module level."""
        cp = math.cos(self.gaze_pitch)
        return np.asarray(
            [cp * math.sin(self.gaze_yaw), -math.sin(self.gaze_pitch), cp * math.cos(self.gaze_yaw)],
            dtype=np.float32,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def clamp_unit(value: float) -> Optional[float]:
    """Clamp ``value`` into the ``asin``/``acos`` domain, or ``None`` if it is not a number.

    Exported (rather than inlined) because every site that turns a direction
    component into an angle needs exactly this guard -- ``backbones.mediapipe_geom``
    included -- and the obvious one-liner is silently wrong.  ``max(-1.0,
    min(1.0, value))`` maps NaN to ``1.0``: ``min`` keeps its first argument
    because ``nan < 1.0`` is False, and ``max`` then keeps that ``1.0``.  The
    asin of a NaN component therefore comes back as a confident ``pi/2``, which
    in this sign convention is a straight-down gaze -- the strongest possible
    false BOTTOM.  So the test here is POSITIVE: the value must prove it is
    finite before it is clipped.  The old negative guard let every non-finite
    value through as a pole.

    ``None``, not NaN, is the "no answer" reply, so a caller has to decide what
    an unusable component means for it; a NaN would keep flowing through the
    arithmetic unnoticed.  A finite value is returned bit-identically (``-0.0``
    stays ``-0.0``), so this is not a change to any well-formed input.
    """
    v = float(value)
    if not math.isfinite(v):
        return None
    if v < -1.0:
        return -1.0
    if v > 1.0:
        return 1.0
    return v


def unit_vector_to_angles(vec: Sequence[float]) -> Tuple[float, float]:
    """Convert a camera-frame gaze direction into ``(yaw, pitch)`` radians.

    Anything that is not a real direction -- zero length, or any NaN/inf
    component -- collapses to the ``(0.0, 0.0)`` "no direction" answer the length
    guard has always given.  Do not "simplify" the finiteness
    test back into ``max(-1.0, min(1.0, y))``: that clip silently turned NaN into
    a pitch of ``-pi/2``, i.e. a plausible, confident BOTTOM gaze out of a frame
    that produced no direction at all.  See :func:`clamp_unit`.
    """
    v = np.asarray(vec, dtype=np.float64)
    if not bool(np.all(np.isfinite(v))):
        # A NaN/inf component is not a direction at all, and rejecting it here
        # rather than after the division also stops numpy warning about nan/nan
        # and keeps a half-answer (finite pitch, NaN yaw) from escaping.
        return 0.0, 0.0
    norm = float(np.linalg.norm(v))
    if norm < 1e-9:
        return 0.0, 0.0
    x, y, z = (float(c) for c in v / norm)
    sin_pitch = clamp_unit(y)
    if sin_pitch is None:
        # Belt and braces: the check above already rejected every non-finite
        # component, so this can only fire if that guard is ever loosened.
        return 0.0, 0.0
    pitch = float(-math.asin(sin_pitch))
    yaw = float(math.atan2(x, z))
    return yaw, pitch


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


@dataclass
class CalibrationSample:
    """One usable calibration frame (doc 5-1)."""

    label: str  # CAMERA | SCREEN | BOTTOM
    gaze: GazeVector
    head_pose: HeadPose
    t_ms: int = 0
    frame_id: int = 0

    def raw_vector(self) -> np.ndarray:
        """The 5 pre-centroid features shared by every feature set."""
        return np.asarray(
            [
                self.gaze.gaze_yaw,
                self.gaze.gaze_pitch,
                self.head_pose.yaw,
                self.head_pose.pitch,
                self.head_pose.roll,
            ],
            dtype=np.float32,
        )


@dataclass
class CalibrationQuality:
    """Result of the pre-flight separability check (doc 5-2)."""

    status: str
    n_camera: int
    n_bottom: int
    camera_centroid: List[float] = field(default_factory=list)
    bottom_centroid: List[float] = field(default_factory=list)
    camera_variance: float = 0.0
    bottom_variance: float = 0.0
    centroid_distance: float = 0.0
    #: Centroid distance normalised by pooled within-class spread (Fisher-like).
    separability: float = 0.0
    loo_accuracy: float = 0.0
    reason: Optional[str] = None
    #: Human-readable hint shown on the retry screen.
    hint: Optional[str] = None
    #: Usable SCREEN (screen-centre) samples; 0 for a two-cue calibration.
    n_screen: int = 0
    #: Which classifier produced this report: "logistic" or "reference".
    method: str = "logistic"
    #: Reference method: median gaze per cue, ``{cue: [yaw_deg, pitch_deg]}``.
    anchors_deg: Dict[str, List[float]] = field(default_factory=dict)
    #: Reference method: pooled robust noise ``[yaw_sigma_deg, pitch_sigma_deg]``.
    sigma_deg: List[float] = field(default_factory=list)
    #: Reference method: anchor distance in noise units per pair, e.g.
    #: ``{"CAMERA-BOTTOM": 9.1}``.
    pair_separation: Dict[str, float] = field(default_factory=dict)
    #: Reference method: radius (deg) within which a gaze still reads as CAMERA
    #: over the screen interior -- the effective "eye contact" zone.
    camera_capture_radius_deg: float = 0.0
    #: Camera placement read off the CAMERA/SCREEN anchors (``to_dict`` of a
    #: ``PlacementCheckResult``), when a SCREEN cue was collected.
    placement: Optional[Dict[str, Any]] = None
    #: Advisory notes that did not fail the calibration.
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == CalibrationStatus.OK.value

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["calibration_status"] = self.status
        return out


@dataclass
class GazeDecision:
    """Per-frame classifier decision before temporal smoothing (doc 5-4)."""

    t_ms: int
    frame_id: int
    label: str  # CAMERA | SCREEN | BOTTOM | OTHER | UNCERTAIN
    p_camera: float
    p_bottom: float
    face_valid: bool
    uncertain_reason: Optional[str] = None
    gaze: Optional[GazeVector] = None
    #: End-to-end cost for this frame (preprocess + backbone + classifier).
    latency_ms: float = 0.0
    #: Posterior over every class the classifier decides between, keyed by
    #: ``GazeState`` value (``None`` for the two-class logistic classifier,
    #: whose ``p_camera``/``p_bottom`` already are the whole distribution).
    #: ``p_camera``/``p_bottom`` always mirror the matching entries.
    probs: Optional[Dict[str, float]] = None
    #: For an OTHER decision: which way outside the calibrated screen area the
    #: gaze went (one of ``GAZE_DIRECTIONS``, presenter-centric); ``None``
    #: otherwise, or when an OTHER look stayed inside that area.
    direction: Optional[str] = None
    #: ``(right_deg, up_deg)`` the gaze lies outside the calibrated screen area,
    #: presenter-centric (``(0, 0)`` inside it).  Reference classifier only.
    offset_deg: Optional[Tuple[float, float]] = None
    #: ``(right_deg, up_deg)`` of the gaze from the screen-centre look,
    #: presenter-centric, inside the screen area too.  Reference classifier only.
    aim_deg: Optional[Tuple[float, float]] = None

    def class_probs(self) -> Dict[str, float]:
        """The full class distribution, whichever classifier produced it."""
        if self.probs is not None:
            return dict(self.probs)
        return {GazeState.CAMERA.value: self.p_camera, GazeState.BOTTOM.value: self.p_bottom}

    def _ranked(self) -> List[float]:
        return sorted((float(v) for v in (self.probs or {}).values()), reverse=True)

    @property
    def p_max(self) -> float:
        if self.probs is None:
            return max(self.p_camera, self.p_bottom)
        ranked = self._ranked()
        return ranked[0] if ranked else 0.0

    @property
    def margin(self) -> float:
        """Top-1 minus top-2 probability (``|p_camera - p_bottom|`` for two classes)."""
        if self.probs is None:
            return abs(self.p_camera - self.p_bottom)
        ranked = self._ranked()
        if len(ranked) < 2:
            return ranked[0] if ranked else 0.0
        return ranked[0] - ranked[1]

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "t_ms": self.t_ms,
            "frame_id": self.frame_id,
            "label": self.label,
            "p_camera": self.p_camera,
            "p_bottom": self.p_bottom,
            "face_valid": self.face_valid,
            "uncertain_reason": self.uncertain_reason,
            "latency_ms": self.latency_ms,
        }
        if self.probs is not None:
            out["probs"] = {k: float(v) for k, v in self.probs.items()}
        if self.direction is not None:
            out["direction"] = self.direction
        if self.offset_deg is not None:
            out["offset_deg"] = [round(float(v), 4) for v in self.offset_deg]
        if self.aim_deg is not None:
            out["aim_deg"] = [round(float(v), 4) for v in self.aim_deg]
        if self.gaze is not None:
            out["gaze"] = self.gaze.to_dict()
        return out


# --------------------------------------------------------------------------
# Temporal output
# --------------------------------------------------------------------------


@dataclass
class GazeStateEvent:
    """Final vision event handed to downstream consumers (doc 6-2).

    ``to_dict`` reproduces the exact JSON shape fixed in the design document.
    """

    t_ms: int
    label: str
    confidence: float
    continuous_duration_ms: int
    face_valid: bool
    model_version: str = "gaze_v1.1.0"
    type: str = "GAZE_STATE"
    #: True only on the frame where the state actually flipped.
    is_transition: bool = False
    #: Smoothed probabilities behind the decision (debug only, not the contract).
    smoothed_p_camera: float = 0.0
    smoothed_p_bottom: float = 0.0
    #: Every smoothed class score when the smoother tracks more than two
    #: classes (debug only); ``None`` keeps a two-class debug dump unchanged.
    smoothed_probs: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "model_version": self.model_version,
            "t_ms": int(self.t_ms),
            "label": self.label,
            "confidence": round(float(self.confidence), 4),
            "continuous_duration_ms": int(self.continuous_duration_ms),
            "face_valid": bool(self.face_valid),
        }

    def to_debug_dict(self) -> Dict[str, Any]:
        out = self.to_dict()
        out.update(
            {
                "is_transition": self.is_transition,
                "smoothed_p_camera": round(float(self.smoothed_p_camera), 4),
                "smoothed_p_bottom": round(float(self.smoothed_p_bottom), 4),
            }
        )
        if self.smoothed_probs is not None:
            out["smoothed_probs"] = {k: round(float(v), 4) for k, v in self.smoothed_probs.items()}
        return out


@dataclass
class SessionConditionEvent:
    """How trustworthy the live measurement currently is (runtime only).

    A separate event, not extra keys on ``GAZE_STATE``: the 7-key gaze contract
    stays exactly as it is, and a consumer that only wants gaze states ignores
    this type.  ``reliability`` is in ``[0, 1]`` and is the *minimum* over the
    monitored signals, so ``issues`` always names what pulled it down.
    """

    t_ms: int
    reliability: float
    issues: List[str] = field(default_factory=list)
    type: str = "SESSION_CONDITION"
    #: True when the measurement is not usable at all (the main face was lost
    #: or replaced); gaze decisions are forced to UNCERTAIN meanwhile.
    severe: bool = False
    #: Per-signal reliability behind the minimum (debug only).
    components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "t_ms": int(self.t_ms),
            "reliability": round(float(self.reliability), 4),
            "issues": list(self.issues),
        }

    def to_debug_dict(self) -> Dict[str, Any]:
        out = self.to_dict()
        out["severe"] = bool(self.severe)
        out["components"] = {k: round(float(v), 4) for k, v in self.components.items()}
        return out


# --------------------------------------------------------------------------
# Dataset side
# --------------------------------------------------------------------------


@dataclass
class SegmentLabel:
    """Ground-truth interval label (doc 4-2)."""

    participant_id: str
    session_id: str
    start_ms: int
    end_ms: int
    label: str
    condition: str = "unknown"
    glasses: bool = False
    lighting: str = "normal"
    device_group: str = "laptop_internal_cam"
    label_version: str = "gaze_label_v1"
    source: str = "protocol"  # protocol | manual | corrected

    @property
    def duration_ms(self) -> int:
        return int(self.end_ms - self.start_ms)

    def contains(self, t_ms: int) -> bool:
        return self.start_ms <= t_ms < self.end_ms

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SegmentLabel":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ManifestRecord:
    """One row of the dataset manifest (doc 17)."""

    sample_id: str
    participant_id: str
    session_id: str
    timestamp_ms: int
    frame_path: str
    gaze_label: str
    condition: str = "unknown"
    glasses: bool = False
    lighting: str = "normal"
    device_group: str = "laptop_internal_cam"
    label_version: str = "gaze_label_v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ManifestRecord":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ParticipantMeta:
    """Static facts about one recorded person (doc 4-1; pseudonymous per doc 20)."""

    participant_id: str
    glasses: bool = False
    device_group: str = "laptop_internal_cam"
    camera_position: str = "top_center"  # top_center | bottom | side_left | side_right
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AiVersion:
    """Version snapshot stored with every take (doc 15, vision slice)."""

    face_landmarker: str = "mediapipe_face_landmarker_v2"
    gaze_backbone: str = "unset"
    gaze_classifier: str = "reference_anchor_v1"
    temporal_rule: str = "gaze_temporal_v1.1"
    feature_set: str = "C"
    config_hash: str = ""
    code_commit: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {"ai_version": asdict(self)}
