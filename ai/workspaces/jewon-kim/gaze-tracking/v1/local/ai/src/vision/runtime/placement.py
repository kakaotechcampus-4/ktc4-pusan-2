"""Camera placement check -- run once, before the 2-point calibration.

The whole CAMERA / BOTTOM formulation in the design document assumes a laptop
webcam sitting *above* the screen, with the script somewhere below it.  When
that assumption is broken the pipeline does not fail loudly -- it quietly
mislabels, because "looking at the script" stops being "looking down".
doc 19 only catches this afterwards, as the ``webcam이 화면 아래/옆에 위치``
failure bucket.  This module catches it up front instead.

The measurement
---------------
We ask for two short gaze targets whose *screen-relative* geometry we know:

    1. look at the CAMERA lens
    2. look at the CENTRE of the screen

The camera is the origin of our angles, so target 1 pins the zero point and
target 2 says where the screen sits relative to it.  With the sign convention
from ``schemas.py`` (pitch > 0 is up)::

    pitch(screen) < pitch(camera)   screen below lens  -> camera ABOVE screen (expected)
    pitch(screen) > pitch(camera)   screen above lens  -> camera BELOW screen
    yaw dominates                   screen beside lens -> camera at the SIDE

The magnitude is comfortably measurable: a 13-15" laptop screen is roughly
19 cm tall viewed from ~55 cm, so the centre of the screen sits about
``atan(9.5 / 55) ~= 10 degrees`` below a top-mounted lens -- far above gaze noise.

Why this is learned, not thresholded
------------------------------------
Deciding "is the screen below the lens" is the same *kind* of question as
doc 5's "is the presenter looking at the camera or the script": a two-class
separation in the same (yaw, pitch) space, from a couple of seconds of frames.
So it uses the same machinery -- standardise, fit the doc 5-3 Logistic
Regression, gate on leave-one-out accuracy exactly like doc 5-2 does.

That is not ceremony, it changes the answer.  Picking the axis by raw angular
displacement lets a noisy axis win just by wobbling more.  The fitted boundary
lives in standardised space, so its normal ``w`` ranks axes by
signal-to-noise, and ``|w_pitch|`` vs ``|w_yaw|`` is the honest question
"which axis actually carries the separation".  Learning decides *whether* the
two cues are distinguishable at all and *which axis* carries it; the centroid
displacement still supplies the physical direction and the sanity check that
the movement is screen-sized rather than a still-sitter's micro-drift.

Yaw signs are read on the RAW (un-mirrored) frame, where the subject's right
hand side appears on the image left.  So a camera mounted to the left of the
screen (from the presenter's point of view) makes the presenter rotate to
their right to reach screen centre, which is a NEGATIVE delta_yaw.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from vision.config import PlacementConfig
from vision.schemas import GazeVector, HeadPose

#: The two gaze targets this check asks for.
TARGET_CAMERA = "CAMERA"
TARGET_SCREEN = "SCREEN"

#: Index of each axis in the (yaw, pitch) feature vector.
_YAW, _PITCH = 0, 1


class CameraPlacement(str, Enum):
    """Where the lens sits relative to the screen, from the presenter's view."""

    TOP = "TOP"
    BOTTOM = "BOTTOM"
    SIDE_LEFT = "SIDE_LEFT"
    SIDE_RIGHT = "SIDE_RIGHT"
    INCONCLUSIVE = "INCONCLUSIVE"


class PlacementReason(str, Enum):
    OK = "OK"
    NOT_ENOUGH_SAMPLES = "NOT_ENOUGH_SAMPLES"
    #: The fitted boundary cannot separate the two cues -- the person probably
    #: did not actually move their eyes between them.
    TARGETS_NOT_SEPARATED = "TARGETS_NOT_SEPARATED"
    #: Separable, but by less than a screen half-height would explain.
    DISPLACEMENT_TOO_SMALL = "DISPLACEMENT_TOO_SMALL"
    #: Neither axis dominates, so "below" and "beside" are equally good reads.
    AMBIGUOUS_AXIS = "AMBIGUOUS_AXIS"


@dataclass
class PlacementCheckResult:
    """Verdict of the placement check."""

    placement: str
    #: True only for TOP -- the geometry the CAMERA/BOTTOM model is built for.
    supported: bool
    delta_pitch_deg: float
    delta_yaw_deg: float
    n_camera: int
    n_screen: int
    reason: str
    hint: str
    mode: str = "learned"
    #: Leave-one-out accuracy of the fitted CAMERA-vs-SCREEN boundary (doc 5-2
    #: gate, reused).  0.0 in geometric mode.
    loo_accuracy: float = 0.0
    #: |w| ratio between the winning and losing axis in standardised space.
    #: > 1 means the winning axis genuinely carries the separation.
    axis_dominance: float = 0.0
    #: "vertical" or "horizontal" -- which axis the boundary actually used.
    axis: str = "unknown"
    #: Centroid distance over pooled spread; kept for geometric mode and debug.
    separation: float = 0.0
    #: Implied screen-centre offset angle, for the on-screen explanation.
    offset_deg: float = 0.0
    camera_centroid_deg: List[float] = field(default_factory=list)
    screen_centroid_deg: List[float] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.supported

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """One-line human summary for the terminal / HUD."""
        return (
            f"{self.placement} (dpitch={self.delta_pitch_deg:+.1f}deg, "
            f"dyaw={self.delta_yaw_deg:+.1f}deg, loo={self.loo_accuracy:.2f}, "
            f"axis={self.axis}) - {self.hint}"
        )


#: Hints are deliberately actionable: each names the fix, not the symptom.
_HINTS: Dict[str, str] = {
    CameraPlacement.TOP.value: (
        "The webcam is estimated to be above the screen, like a laptop camera."
    ),
    CameraPlacement.BOTTOM.value: (
        "The webcam is estimated to be below the screen. This is advisory; continue to "
        "calibration to learn the user's actual CAMERA and BOTTOM looks."
    ),
    CameraPlacement.SIDE_LEFT.value: (
        "The webcam is estimated to be left of the screen. Centre it above the display "
        "when practical, then use calibration to learn the actual looks."
    ),
    CameraPlacement.SIDE_RIGHT.value: (
        "The webcam is estimated to be right of the screen. Centre it above the display "
        "when practical, then use calibration to learn the actual looks."
    ),
}

_INCONCLUSIVE_HINTS: Dict[str, str] = {
    PlacementReason.NOT_ENOUGH_SAMPLES.value: (
        "Not enough usable frames. Keep your face in view and well lit, then retry."
    ),
    PlacementReason.TARGETS_NOT_SEPARATED.value: (
        "The two cues looked the same to the model. Look right into the lens for the "
        "first cue, then at the middle of the screen for the second - move your eyes, "
        "not just your head."
    ),
    PlacementReason.DISPLACEMENT_TOO_SMALL.value: (
        "Camera and screen centre are almost the same direction. Either the camera sits "
        "in the middle of the display, or you are sitting very far away."
    ),
    PlacementReason.AMBIGUOUS_AXIS.value: (
        "The camera looks diagonally offset from the screen centre, so 'below' and "
        "'beside' fit equally well. Centre the webcam above the screen and retry."
    ),
}


class CameraPlacementCheck:
    """Collects gaze samples for the two cues and returns a placement verdict.

    Usage mirrors the calibration flow: feed frames while a cue is on screen,
    then call :meth:`evaluate`.
    """

    def __init__(self, cfg: PlacementConfig) -> None:
        self.cfg = cfg
        self._samples: Dict[str, List[Tuple[float, float]]] = {
            TARGET_CAMERA: [],
            TARGET_SCREEN: [],
        }

    # -- collection -------------------------------------------------------
    def add_sample(
        self,
        target: str,
        gaze: Optional[GazeVector],
        head: Optional[HeadPose] = None,
    ) -> bool:
        """Record one frame for ``target``.  Returns True when it was usable.

        ``head`` is accepted for symmetry with the calibration API and to keep
        the door open for a head-pose cross-check; the verdict is driven by gaze
        alone, because head pose barely moves for a screen-sized shift.
        """
        key = str(target).strip().upper()
        if key not in self._samples:
            raise ValueError(f"unknown placement target {target!r}")
        if gaze is None or not math.isfinite(gaze.gaze_yaw) or not math.isfinite(gaze.gaze_pitch):
            return False
        if gaze.confidence < self.cfg.min_sample_confidence:
            return False
        self._samples[key].append((float(gaze.gaze_yaw), float(gaze.gaze_pitch)))
        return True

    def count(self, target: str) -> int:
        return len(self._samples[str(target).strip().upper()])

    def reset(self) -> None:
        for key in self._samples:
            self._samples[key].clear()

    # -- verdict ----------------------------------------------------------
    def evaluate(self) -> PlacementCheckResult:
        """Decide where the lens sits (doc 19 webcam-position bucket, up front)."""
        cam = np.asarray(self._samples[TARGET_CAMERA], dtype=np.float64).reshape(-1, 2)
        scr = np.asarray(self._samples[TARGET_SCREEN], dtype=np.float64).reshape(-1, 2)

        if len(cam) < self.cfg.min_samples_per_target or len(scr) < self.cfg.min_samples_per_target:
            return self._inconclusive(cam, scr, PlacementReason.NOT_ENOUGH_SAMPLES)

        if str(self.cfg.mode).lower() == "geometric":
            return self._evaluate_geometric(cam, scr)
        return self._evaluate_learned(cam, scr)

    # -- learned ----------------------------------------------------------
    def _evaluate_learned(self, cam: np.ndarray, scr: np.ndarray) -> PlacementCheckResult:
        """Fit the doc 5-3 classifier and read the placement off its boundary."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        X = np.vstack([cam, scr])
        y = np.concatenate([np.zeros(len(cam), dtype=int), np.ones(len(scr), dtype=int)])

        def _make() -> Pipeline:
            return Pipeline(
                [
                    ("scale", StandardScaler()),
                    (
                        "lr",
                        LogisticRegression(
                            C=self.cfg.C,
                            max_iter=self.cfg.max_iter,
                            class_weight="balanced",
                            random_state=self.cfg.random_seed,
                        ),
                    ),
                ]
            )

        # Leave-one-out is the doc 5-2 gate: with ~16 samples per cue it is both
        # cheap and the only honest estimate available.
        correct = 0
        for i in range(len(X)):
            mask = np.ones(len(X), dtype=bool)
            mask[i] = False
            if len(np.unique(y[mask])) < 2:
                continue
            correct += int(_make().fit(X[mask], y[mask]).predict(X[i : i + 1])[0] == y[i])
        loo = correct / float(len(X))

        model = _make().fit(X, y)
        scaler: Any = model.named_steps["scale"]
        weights = np.asarray(model.named_steps["lr"].coef_, dtype=np.float64).ravel()

        base, delta_pitch_deg, delta_yaw_deg = _geometry(cam, scr, "learned")
        base["loo_accuracy"] = round(loo, 3)
        base["separation"] = round(_separation(cam, scr), 3)

        if loo < self.cfg.min_loo_accuracy:
            return self._fail(PlacementReason.TARGETS_NOT_SEPARATED, **base)

        # The boundary normal lives in standardised space, so comparing its two
        # components ranks the axes by signal-to-noise rather than raw degrees.
        w_yaw, w_pitch = abs(float(weights[_YAW])), abs(float(weights[_PITCH]))
        vertical = w_pitch >= w_yaw
        dominance = (w_pitch / w_yaw) if vertical and w_yaw > 1e-12 else (
            (w_yaw / w_pitch) if not vertical and w_pitch > 1e-12 else float("inf")
        )
        base["axis_dominance"] = round(min(dominance, 999.0), 3)
        # Named here as well as inside _verdict, because an AMBIGUOUS_AXIS
        # report still has to say which axis narrowly won and never gets there.
        base["axis"] = "vertical" if vertical else "horizontal"

        if dominance < self.cfg.min_axis_dominance:
            return self._fail(PlacementReason.AMBIGUOUS_AXIS, **base)

        # Learning picked the axis; the centroids supply the physical direction.
        return self._verdict(base, vertical, delta_pitch_deg, delta_yaw_deg)

    # -- geometric --------------------------------------------------------
    def _evaluate_geometric(self, cam: np.ndarray, scr: np.ndarray) -> PlacementCheckResult:
        """Fixed-threshold fallback, kept so the check works without sklearn."""
        separation = _separation(cam, scr)
        base, delta_pitch_deg, delta_yaw_deg = _geometry(cam, scr, "geometric")
        base["separation"] = round(separation, 3)

        if separation < self.cfg.min_separation:
            return self._fail(PlacementReason.TARGETS_NOT_SEPARATED, **base)

        # No fitted boundary to read the axis off, so raw angular displacement
        # picks it -- the noisy-axis-wins problem the module docstring
        # describes, and the reason this mode is the fallback, not the default.
        vertical = abs(delta_pitch_deg) >= abs(delta_yaw_deg)
        return self._verdict(base, vertical, delta_pitch_deg, delta_yaw_deg)

    # -- helpers ----------------------------------------------------------
    def _verdict(
        self,
        base: Dict[str, Any],
        vertical: bool,
        delta_pitch_deg: float,
        delta_yaw_deg: float,
    ) -> PlacementCheckResult:
        """Turn "which axis" plus the centroid displacement into the verdict.

        The sign convention lives here and nowhere else.  The two modes differ
        only in how they *choose* the axis -- the learned one off the boundary
        normal, the geometric one off raw degrees -- so spelling the signs out
        in each of them was two chances to invert one and no test that compares
        the two against each other.

        ``delta_*_deg`` are the UNROUNDED degrees on purpose: ``base`` carries
        them rounded to 2 dp for the report, and thresholding on the rounded
        value would let a 3.996 deg displacement clear a 4.0 deg floor.
        """
        base["axis"] = "vertical" if vertical else "horizontal"
        axis_delta_deg = delta_pitch_deg if vertical else delta_yaw_deg

        # Separable is not enough: the movement still has to be screen-sized.
        # A perfectly still sitter can be separable at half a degree, which is
        # not evidence about where the lens is.
        if abs(axis_delta_deg) < self.cfg.min_delta_deg:
            return self._fail(PlacementReason.DISPLACEMENT_TOO_SMALL, **base)

        if vertical:
            # schemas.py: pitch > 0 is UP, so screen centre *below* the lens (a
            # negative delta) is the expected top-mounted laptop camera.
            placement = CameraPlacement.TOP if axis_delta_deg < 0 else CameraPlacement.BOTTOM
        else:
            # Raw frame: presenter's right appears on the image left (module docstring).
            placement = (
                CameraPlacement.SIDE_RIGHT if axis_delta_deg > 0 else CameraPlacement.SIDE_LEFT
            )

        return PlacementCheckResult(
            placement=placement.value,
            supported=placement is CameraPlacement.TOP,
            reason=PlacementReason.OK.value,
            hint=_HINTS[placement.value],
            **base,
        )

    @staticmethod
    def _fail(reason: PlacementReason, **base: Any) -> PlacementCheckResult:
        return PlacementCheckResult(
            placement=CameraPlacement.INCONCLUSIVE.value,
            supported=False,
            reason=reason.value,
            hint=_INCONCLUSIVE_HINTS[reason.value],
            **base,
        )

    def _inconclusive(
        self, cam: np.ndarray, scr: np.ndarray, reason: PlacementReason
    ) -> PlacementCheckResult:
        """A verdict we could not reach, still carrying whatever geometry exists.

        ``separation`` keeps its 0.0 default: with one cue possibly empty there
        is no pooled spread to divide by, and a 0.0 that means "not computed"
        beats inventing a number for the report.
        """
        base, _pitch_deg, _yaw_deg = _geometry(cam, scr, str(self.cfg.mode).lower())
        return self._fail(reason, **base)


def _geometry(
    cam: np.ndarray, scr: np.ndarray, mode: str
) -> Tuple[Dict[str, Any], float, float]:
    """Report fields every verdict carries, plus the two raw pitch/yaw deltas.

    Medians, not means: a couple of frames where the eyes flicked off the cue
    must not drag its centre.  Every branch -- learned, geometric and the
    not-enough-samples path -- reads its numbers from here, so the degrees a
    user is shown never depend on which branch produced them.

    The raw (unrounded) deltas come back alongside ``base`` because the
    thresholds are compared against those, while ``base`` carries the 2 dp
    values the report and the on-screen hint quote.  An empty cue centres on
    zero rather than letting ``np.median`` return NaN with a warning.
    """
    cam_c = np.median(cam, axis=0) if len(cam) else np.zeros(2)
    scr_c = np.median(scr, axis=0) if len(scr) else np.zeros(2)
    delta = scr_c - cam_c
    delta_pitch_deg = math.degrees(float(delta[_PITCH]))
    delta_yaw_deg = math.degrees(float(delta[_YAW]))
    base: Dict[str, Any] = dict(
        delta_pitch_deg=round(delta_pitch_deg, 2),
        delta_yaw_deg=round(delta_yaw_deg, 2),
        n_camera=int(len(cam)),
        n_screen=int(len(scr)),
        mode=mode,
        offset_deg=round(math.degrees(float(np.linalg.norm(delta))), 2),
        camera_centroid_deg=[round(math.degrees(v), 2) for v in cam_c],
        screen_centroid_deg=[round(math.degrees(v), 2) for v in scr_c],
    )
    return base, delta_pitch_deg, delta_yaw_deg


def _separation(a: np.ndarray, b: np.ndarray) -> float:
    """Centroid distance over pooled within-cluster spread (Fisher-like).

    Mirrors ``calibration.quality`` so both checks report on the same scale:
    below ~1.0 the clouds overlap enough that the verdict would be a coin flip.
    """
    centroid_gap = float(np.linalg.norm(np.median(a, axis=0) - np.median(b, axis=0)))
    spread_a = float(np.sqrt(np.mean(np.sum((a - np.median(a, axis=0)) ** 2, axis=1))))
    spread_b = float(np.sqrt(np.mean(np.sum((b - np.median(b, axis=0)) ** 2, axis=1))))
    pooled = math.sqrt(0.5 * (spread_a**2 + spread_b**2))
    if pooled < 1e-9:
        # Perfectly still gaze: any gap is infinitely separable, but cap it so
        # downstream comparisons stay finite.
        return 1e3 if centroid_gap > 1e-9 else 0.0
    return centroid_gap / pooled
