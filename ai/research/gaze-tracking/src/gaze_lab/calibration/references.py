"""Reference-anchor gaze classifier: calibrated regions in gaze space.

The user looks at three targets in turn -- the webcam lens (CAMERA), the centre
of the screen (SCREEN) and the script (BOTTOM) -- naturally with the default
head-pose backbone, head held still with an eye backbone.  Each target leaves an
*anchor*: the median gaze angle the backbone reports while this person looks
there.  Live frames are classified by comparing
their gaze with those anchors, not by a fitted decision boundary.

Why anchors and not a fitted classifier
---------------------------------------
* **One quantity, compared like with like.**  Only the backbone's gaze is
  compared -- the head direction for the default head-pose backbone, head plus
  eye rotation for an eye backbone -- in the same units the calibration
  measured it in.  A logistic model given separate head and gaze features can
  instead learn whichever happened to move between the cues.
* **Few samples favour few parameters.**  A dozen frames per cue pin a median
  well; they do not pin a flexible boundary.
* **Systematic error cancels.**  The backbone's geometric constants (eyeball
  radius, iris size, blendshape gain) and the user's own optical/visual-axis
  offset bias every reading the same way during calibration and live use, so
  comparing like with like removes them.  What matters is consistency, not
  absolute angles.
* **"None of the above" exists.**  A two-class boundary has to put every gaze
  somewhere; here a gaze far from every region becomes OTHER.

The likelihood model
--------------------
Each class is a region in (yaw, pitch) degrees blurred by the measurement
noise, a *soft box*: per axis the density of ``Uniform[lo, hi]`` convolved with
``N(0, s^2)``,

    f(x) = [Phi((hi - x) / s) - Phi((lo - x) / s)] / (hi - lo)

which tends to the Gaussian pdf as the box shrinks to a point.  It is a proper
density, so regions of different size compete fairly: a point anchor is sharp
and wins near itself, a large box is diluted and wins only where nothing more
specific claims the gaze.

* CAMERA -- a point at the CAMERA anchor (optionally a disc of
  ``camera_halfwidth_deg``).
* SCREEN -- the screen rectangle.  The lens sits at the top edge of the screen,
  so the CAMERA->SCREEN offset is half the screen height; the width follows
  from ``screen_aspect``, capped at ``screen_max_halfwidth_deg`` each side.
* BOTTOM -- a box around the BOTTOM anchor, a fraction of the screen box.
* OTHER -- a constant density everywhere: the "looking elsewhere" floor.

``s`` is one robust noise level per axis pooled over every cue (MAD of each
sample's deviation from its own cue median), widened by ``sigma_scale``
because a still calibration fixation is quieter than live speech, and floored
at ``sigma_min_deg``.  Posteriors come from Bayes with configurable priors,
computed in log space so far-away gazes do not underflow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.special import log_ndtr, logsumexp

from gaze_lab.config import CalibrationConfig
from gaze_lab.schemas import (
    DECISION_CLASSES,
    GAZE_DIRECTIONS,
    INVERTED_PITCH_HINT_PREFIX,
    STATE_CLASSES,
    CalibrationFailReason,
    CalibrationQuality,
    CalibrationSample,
    CalibrationStatus,
    FrameObservation,
    GazeDecision,
    GazeLabel,
    GazeState,
    GazeVector,
    HeadPose,
    InvalidReason,
)

#: Same reason strings as the logistic classifier (``classifier.py``).
UNCERTAIN_LOW_CONFIDENCE = "LOW_CONFIDENCE"
UNCERTAIN_LOW_MARGIN = "LOW_MARGIN"
#: The head is turned so far from the calibration pose that the decision is
#: OTHER; used as the reason only if OTHER is switched off (``prior_other: 0``).
UNCERTAIN_HEAD_AWAY = "HEAD_AWAY"

_CAMERA = GazeState.CAMERA.value
_SCREEN = GazeState.SCREEN.value
_BOTTOM = GazeState.BOTTOM.value
_OTHER = GazeState.OTHER.value

_CUES: Tuple[str, ...] = (_CAMERA, _SCREEN, _BOTTOM)

#: Consistency factor turning a MAD into a Gaussian standard deviation.
_MAD_TO_SIGMA = 1.4826
#: Below this many samples per class a median and LOO mean nothing.
_MIN_SAMPLES_FOR_STATS = 2
#: An anchor must classify as itself this far above the decision threshold.
_SELF_POSTERIOR_MARGIN = 0.05
_LOG_SQRT_2PI = 0.5 * math.log(2.0 * math.pi)

_HINTS: Dict[str, str] = {
    CalibrationFailReason.NOT_ENOUGH_SAMPLES.value: (
        "Not enough usable frames. Keep your whole face in view and hold each look until "
        "the gauge fills, then try again."
    ),
    CalibrationFailReason.DEGENERATE_FEATURES.value: (
        "The gaze never moved between the targets. Check the camera and the light, then try "
        "again looking clearly at each target."
    ),
    CalibrationFailReason.CLASS_NOT_SEPARABLE.value: (
        "Looking at the lens and looking at the script measured almost the same. Look straight "
        "into the lens first, then clearly down at the script."
    ),
    CalibrationFailReason.CENTROIDS_TOO_CLOSE.value: (
        "Two of the targets measured too close together. Look clearly at each one in turn."
    ),
    CalibrationFailReason.ANCHOR_AMBIGUOUS.value: (
        "One target could not be told apart from the region around it. Sit a little closer "
        "and look clearly at each target."
    ),
    CalibrationFailReason.LOW_LOO_ACCURACY.value: (
        "The samples of different targets overlap. Look clearly at each target and hold "
        "each look steady."
    ),
}


#: Grades a full (with SCREEN) calibration can fail that dropping SCREEN can fix:
#: CAMERA-BOTTOM failing is CLASS_NOT_SEPARABLE, so a pair too close here
#: involves SCREEN; an ambiguous anchor or overlapping samples usually means
#: the screen box swallowed the lens point.
_SCREEN_FIXABLE = frozenset({
    CalibrationFailReason.CENTROIDS_TOO_CLOSE.value,
    CalibrationFailReason.ANCHOR_AMBIGUOUS.value,
    CalibrationFailReason.LOW_LOO_ACCURACY.value,
})

#: Added to ``quality.warnings`` (and the hint) when SCREEN was folded into CAMERA.
SCREEN_MERGED_WARNING = (
    "SCREEN_MERGED: looking at the lens and at the screen centre measured too close to tell "
    "apart, so a look at the screen counts as CAMERA (facing front) for this calibration."
)


# --------------------------------------------------------------------------
# Densities
# --------------------------------------------------------------------------


def _log1mexp(d: float) -> float:
    """``log(1 - exp(d))`` for ``d <= 0``, accurate at both ends."""
    if d >= 0.0:
        return -math.inf
    if d > -0.6931471805599453:  # -log 2
        return math.log(-math.expm1(d))
    return math.log1p(-math.exp(d))


def _log_ndtr_diff(a: float, b: float) -> float:
    """``log(Phi(b) - Phi(a))`` for ``a < b`` without cancellation.

    In the upper tail both CDFs are ~1 and their difference cancels to 0 long
    before the true value underflows, so that case is flipped to the lower tail
    with ``Phi(b) - Phi(a) = Phi(-a) - Phi(-b)``.
    """
    if a > 0.0:
        a, b = -b, -a
    lb = float(log_ndtr(b))
    la = float(log_ndtr(a))
    return lb + _log1mexp(la - lb)


def soft_box_log_density(x: float, lo: float, hi: float, s: float) -> float:
    """Log density at ``x`` of ``Uniform[lo, hi]`` convolved with ``N(0, s^2)``."""
    s = max(float(s), 1e-9)
    lo, hi = (float(lo), float(hi)) if lo <= hi else (float(hi), float(lo))
    width = hi - lo
    if width < 1e-3 * s:
        z = (float(x) - 0.5 * (lo + hi)) / s
        return -0.5 * z * z - math.log(s) - _LOG_SQRT_2PI
    return _log_ndtr_diff((lo - float(x)) / s, (hi - float(x)) / s) - math.log(width)


def robust_sigma(residuals: np.ndarray, cfg: CalibrationConfig) -> float:
    """Noise level of one axis from residuals about each cue's own median."""
    r = np.asarray(residuals, dtype=np.float64)
    r = r[np.isfinite(r)]
    mad = float(np.median(np.abs(r))) if r.size else 0.0
    return max(float(cfg.sigma_min_deg), float(cfg.sigma_scale) * _MAD_TO_SIGMA * mad)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SoftBox:
    """An axis-aligned region in gaze degrees: ``[yaw_lo, yaw_hi] x [pitch_lo, pitch_hi]``."""

    yaw_lo: float
    yaw_hi: float
    pitch_lo: float
    pitch_hi: float

    def shifted(self, dyaw: float, dpitch: float) -> "SoftBox":
        return SoftBox(self.yaw_lo + dyaw, self.yaw_hi + dyaw, self.pitch_lo + dpitch, self.pitch_hi + dpitch)

    @property
    def area(self) -> float:
        return (self.yaw_hi - self.yaw_lo) * (self.pitch_hi - self.pitch_lo)


@dataclass(frozen=True)
class ReferenceModel:
    """Everything one calibration produced; immutable, so a re-anchor copies it."""

    #: Median gaze per cue, ``(yaw_deg, pitch_deg)``.
    anchors: Dict[str, Tuple[float, float]]
    #: Usable samples per cue.
    counts: Dict[str, int]
    #: Pooled noise ``(yaw_sigma_deg, pitch_sigma_deg)`` after scale and floor.
    sigma: Tuple[float, float]
    #: Region of every anchored class (CAMERA, BOTTOM and, if calibrated, SCREEN).
    boxes: Dict[str, SoftBox]
    #: Log prior of every active class.
    log_priors: Dict[str, float]
    #: Log of OTHER's constant density (only used when OTHER is active).
    log_other_density: float
    #: Median head ``(yaw_deg, pitch_deg)`` during calibration.
    head_baseline: Tuple[float, float]
    #: Active classes in ``STATE_CLASSES`` order.
    classes: Tuple[str, ...]
    #: Per-cue spread ``(yaw, pitch)`` in degrees, before pooling (diagnostics).
    spreads: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def log_likelihood(self, cls: str, yaw_deg: float, pitch_deg: float) -> float:
        if cls == _OTHER:
            return self.log_other_density
        box = self.boxes[cls]
        return soft_box_log_density(yaw_deg, box.yaw_lo, box.yaw_hi, self.sigma[0]) + soft_box_log_density(
            pitch_deg, box.pitch_lo, box.pitch_hi, self.sigma[1]
        )

    def posterior(
        self, yaw_deg: float, pitch_deg: float, other_margin_deg: float = 0.0, min_halfwidth_deg: float = 0.0,
    ) -> Dict[str, float]:
        """Class posterior at one gaze, keyed by state, in ``classes`` order.

        OTHER takes part only once the gaze is at least ``other_margin_deg``
        outside the calibrated screen area (at least ``min_halfwidth_deg`` wide
        each side); closer in it is 0 and the nearest screen class wins (see
        ``CalibrationConfig.other_margin_deg``).
        """
        active = self.classes
        if other_margin_deg > 0.0 and _OTHER in active:
            right, up = gaze_offset(self, yaw_deg, pitch_deg, min_halfwidth_deg)
            if math.hypot(right, up) < other_margin_deg:
                active = tuple(c for c in active if c != _OTHER)
        logs = [self.log_priors[c] + self.log_likelihood(c, yaw_deg, pitch_deg) for c in active]
        norm = float(logsumexp(logs))
        probs = {c: float(math.exp(v - norm)) for c, v in zip(active, logs)}
        return {c: probs.get(c, 0.0) for c in self.classes}

    def shifted(self, dyaw_deg: float, dpitch_deg: float) -> "ReferenceModel":
        """Every anchor and region moved by the same gaze offset (re-anchor)."""
        return replace(
            self,
            anchors={c: (y + dyaw_deg, p + dpitch_deg) for c, (y, p) in self.anchors.items()},
            boxes={c: b.shifted(dyaw_deg, dpitch_deg) for c, b in self.boxes.items()},
        )

    def camera_capture_radius_deg(self) -> float:
        """Radius within which a gaze still reads as CAMERA over its neighbour.

        The neighbour is the screen interior when SCREEN is calibrated, else the
        OTHER floor.  It is the effective eye-contact zone, and it grows with the
        noise level -- reported so a noisy calibration is visible as a wide zone.
        """
        s = math.sqrt(self.sigma[0] * self.sigma[1])
        log_peak = self.log_priors[_CAMERA] - math.log(2.0 * math.pi * self.sigma[0] * self.sigma[1])
        if _SCREEN in self.boxes and _SCREEN in self.log_priors and self.boxes[_SCREEN].area > 0:
            log_rival = self.log_priors[_SCREEN] - math.log(self.boxes[_SCREEN].area)
        elif _OTHER in self.log_priors:
            log_rival = self.log_priors[_OTHER] + self.log_other_density
        else:
            return math.inf
        ratio = log_peak - log_rival
        return s * math.sqrt(2.0 * ratio) if ratio > 0.0 else 0.0


def _gaze_deg(gaze: GazeVector) -> Tuple[float, float]:
    return math.degrees(float(gaze.gaze_yaw)), math.degrees(float(gaze.gaze_pitch))


def _cue_values(samples: Sequence[CalibrationSample]) -> Dict[str, np.ndarray]:
    """Finite ``(yaw_deg, pitch_deg)`` rows per cue; other labels are dropped."""
    rows: Dict[str, List[Tuple[float, float]]] = {c: [] for c in _CUES}
    for sample in samples:
        try:
            label = GazeLabel.coerce(sample.label).value
        except ValueError:
            continue
        if label not in rows:
            continue
        yaw, pitch = _gaze_deg(sample.gaze)
        if math.isfinite(yaw) and math.isfinite(pitch):
            rows[label].append((yaw, pitch))
    return {c: np.asarray(v, dtype=np.float64).reshape(-1, 2) for c, v in rows.items()}


def _head_baseline(samples: Sequence[CalibrationSample]) -> Tuple[float, float]:
    heads = np.asarray(
        [(math.degrees(s.head_pose.yaw), math.degrees(s.head_pose.pitch)) for s in samples],
        dtype=np.float64,
    ).reshape(-1, 2)
    heads = heads[np.all(np.isfinite(heads), axis=1)]
    if heads.shape[0] == 0:
        return 0.0, 0.0
    med = np.median(heads, axis=0)
    return float(med[0]), float(med[1])


def build_reference_model(
    samples: Sequence[CalibrationSample], cfg: CalibrationConfig
) -> Optional[ReferenceModel]:
    """The model from one calibration, or ``None`` without CAMERA and BOTTOM."""
    values = _cue_values(samples)
    if values[_CAMERA].shape[0] < 1 or values[_BOTTOM].shape[0] < 1:
        return None
    present = [c for c in _CUES if values[c].shape[0] >= 1]

    anchors: Dict[str, Tuple[float, float]] = {}
    spreads: Dict[str, Tuple[float, float]] = {}
    residuals = []
    for cue in present:
        med = np.median(values[cue], axis=0)
        anchors[cue] = (float(med[0]), float(med[1]))
        dev = values[cue] - med
        residuals.append(dev)
        spreads[cue] = tuple(float(_MAD_TO_SIGMA * np.median(np.abs(dev[:, i]))) for i in range(2))
    pooled = np.vstack(residuals)
    sigma = (robust_sigma(pooled[:, 0], cfg), robust_sigma(pooled[:, 1], cfg))

    boxes: Dict[str, SoftBox] = {}
    cy, cp = anchors[_CAMERA]
    r = max(0.0, float(cfg.camera_halfwidth_deg))
    boxes[_CAMERA] = SoftBox(cy - r, cy + r, cp - r, cp + r)
    by, bp = anchors[_BOTTOM]
    if _SCREEN in anchors:
        sy, sp = anchors[_SCREEN]
        aspect = max(float(cfg.screen_aspect), 1e-6)
        d_yaw, d_pitch = abs(sy - cy), abs(sp - cp)
        # The lens sits on the screen's edge: the CAMERA->SCREEN offset is half
        # the screen extent along that axis, the other axis follows the aspect.
        half_h = max(d_pitch, d_yaw / aspect)
        half_w = max(d_yaw, min(d_pitch * aspect, float(cfg.screen_max_halfwidth_deg)))
        boxes[_SCREEN] = SoftBox(sy - half_w, sy + half_w, sp - half_h, sp + half_h)
        sw = float(cfg.script_width_fraction) * half_w
        sh = float(cfg.script_height_fraction) * half_h
        boxes[_BOTTOM] = SoftBox(by - sw, by + sw, bp - sh, bp + sh)
    else:
        boxes[_BOTTOM] = SoftBox(by, by, bp, bp)

    priors = {
        _CAMERA: float(cfg.prior_camera),
        _SCREEN: float(cfg.prior_screen) if _SCREEN in anchors else 0.0,
        _BOTTOM: float(cfg.prior_bottom),
        _OTHER: float(cfg.prior_other),
    }
    if priors[_CAMERA] <= 0.0 or priors[_BOTTOM] <= 0.0:
        raise ValueError("prior_camera and prior_bottom must be > 0")
    classes = tuple(c for c in STATE_CLASSES if priors.get(c, 0.0) > 0.0)
    total = sum(priors[c] for c in classes)
    log_priors = {c: math.log(priors[c] / total) for c in classes}
    field_area = max(float(cfg.other_field_yaw_deg) * float(cfg.other_field_pitch_deg), 1e-6)

    return ReferenceModel(
        anchors=anchors,
        counts={c: int(values[c].shape[0]) for c in _CUES},
        sigma=(float(sigma[0]), float(sigma[1])),
        boxes=boxes,
        log_priors=log_priors,
        log_other_density=-math.log(field_area),
        head_baseline=_head_baseline(samples),
        classes=classes,
        spreads=spreads,
    )


# --------------------------------------------------------------------------
# Decision rule (shared by live frames and the leave-one-out check)
# --------------------------------------------------------------------------


def decide_label(probs: Dict[str, float], cfg: CalibrationConfig) -> Tuple[str, Optional[str]]:
    """doc 5-4 rule on a K-class posterior: ``(label, uncertain_reason)``.

    The confidence floor first, then the margin floor, then the most probable
    class -- the first one in ``STATE_CLASSES`` order on an exact tie.
    """
    ranked = sorted(probs.values(), reverse=True)
    p_max = ranked[0] if ranked else 0.0
    margin = (ranked[0] - ranked[1]) if len(ranked) > 1 else p_max
    if p_max < float(cfg.p_max_threshold):
        return GazeState.UNCERTAIN.value, UNCERTAIN_LOW_CONFIDENCE
    if margin < float(cfg.margin_threshold):
        return GazeState.UNCERTAIN.value, UNCERTAIN_LOW_MARGIN
    best = max(STATE_CLASSES, key=lambda c: (probs.get(c, -1.0), -STATE_CLASSES.index(c)))
    return best, None


def _other_rule(cfg: CalibrationConfig) -> Tuple[float, float]:
    """``(other_margin_deg, screen_min_halfwidth_deg)`` for ``ReferenceModel.posterior``."""
    return float(cfg.other_margin_deg), float(cfg.screen_min_halfwidth_deg)


def screen_region(model: ReferenceModel, min_halfwidth_deg: float = 0.0) -> SoftBox:
    """The calibrated screen area: every class region together, widened by one noise unit.

    The lens sits on the screen's top edge, the script box at its bottom, and the
    screen box (when calibrated) spans the rest; one sigma of margin keeps edge
    jitter from reading as a look "outside".  It is at least
    ``min_halfwidth_deg`` wide on each side of its middle: head-pose looks at
    the lens, the screen centre and the script often line up within a degree or
    two sideways, which says nothing about how wide the screen is.
    """
    boxes = list(model.boxes.values())
    sy, sp = model.sigma
    lo = min(b.yaw_lo for b in boxes) - sy
    hi = max(b.yaw_hi for b in boxes) + sy
    if (hi - lo) / 2.0 < min_halfwidth_deg:
        mid = (lo + hi) / 2.0
        lo, hi = mid - min_halfwidth_deg, mid + min_halfwidth_deg
    return SoftBox(lo, hi, min(b.pitch_lo for b in boxes) - sp, max(b.pitch_hi for b in boxes) + sp)


def gaze_offset(
    model: ReferenceModel, yaw_deg: float, pitch_deg: float, min_halfwidth_deg: float = 0.0
) -> Tuple[float, float]:
    """``(right_deg, up_deg)`` the gaze lies outside the screen area; ``(0, 0)`` inside.

    Presenter-centric: in the raw (unmirrored) frame a gaze toward the image
    right (yaw > 0) is toward the presenter's LEFT, so the horizontal sign flips.
    """
    region = screen_region(model, min_halfwidth_deg)
    if yaw_deg > region.yaw_hi:
        ex = yaw_deg - region.yaw_hi
    elif yaw_deg < region.yaw_lo:
        ex = yaw_deg - region.yaw_lo
    else:
        ex = 0.0
    if pitch_deg > region.pitch_hi:
        ey = pitch_deg - region.pitch_hi
    elif pitch_deg < region.pitch_lo:
        ey = pitch_deg - region.pitch_lo
    else:
        ey = 0.0
    return (-ex if ex else 0.0, ey)


def aim_offset(model: ReferenceModel, yaw_deg: float, pitch_deg: float) -> Tuple[float, float]:
    """``(right_deg, up_deg)`` of the gaze from the screen-centre look, presenter-centric.

    Always defined (unlike :func:`gaze_offset`, which is 0 inside the screen
    area): it is where the head points, read against the calibration, for a
    display of "how far left/right and up/down".  The reference is the SCREEN
    anchor, or halfway between the lens and the script without one.
    """
    if _SCREEN in model.anchors:
        ry, rp = model.anchors[_SCREEN]
    else:
        (cy, cp), (by, bp) = model.anchors[_CAMERA], model.anchors[_BOTTOM]
        ry, rp = (cy + by) / 2.0, (cp + bp) / 2.0
    return (-(float(yaw_deg) - ry), float(pitch_deg) - rp)


def direction_of(offset: Tuple[float, float]) -> Optional[str]:
    """The 45-degree sector of an outside offset, or ``None`` inside the screen area."""
    right, up = offset
    if right == 0.0 and up == 0.0:
        return None
    angle = math.degrees(math.atan2(up, right))
    return GAZE_DIRECTIONS[int(math.floor((angle + 22.5) / 45.0)) % 8]


def head_is_away(model: ReferenceModel, head: HeadPose, cfg: CalibrationConfig) -> bool:
    yaw, pitch = math.degrees(float(head.yaw)), math.degrees(float(head.pitch))
    if not (math.isfinite(yaw) and math.isfinite(pitch)):
        return False
    return (
        abs(yaw - model.head_baseline[0]) > float(cfg.head_away_yaw_deg)
        or abs(pitch - model.head_baseline[1]) > float(cfg.head_away_pitch_deg)
    )


# --------------------------------------------------------------------------
# Quality (doc 5-2 for this method)
# --------------------------------------------------------------------------


def _pair_name(a: str, b: str) -> str:
    return f"{a}-{b}"


def _separation(model: ReferenceModel, a: str, b: str) -> float:
    (ay, ap), (by_, bp) = model.anchors[a], model.anchors[b]
    return math.hypot((ay - by_) / model.sigma[0], (ap - bp) / model.sigma[1])


def _fail(quality: CalibrationQuality, reason: CalibrationFailReason, detail: str = "",
          warning: Optional[str] = None) -> CalibrationQuality:
    quality.status = CalibrationStatus.RETRY_REQUIRED.value
    quality.reason = reason.value
    hint = _HINTS[reason.value] + (f" ({detail})" if detail else "")
    quality.hint = f"{hint} {warning}" if warning else hint
    return quality


def leave_one_out_accuracy(samples: Sequence[CalibrationSample], cfg: CalibrationConfig) -> float:
    """Each cue sample classified by a model rebuilt without it.

    UNCERTAIN counts as wrong: a calibration whose own samples the rule refuses
    to decide is not one the live take can rely on.
    """
    usable = [
        (i, s) for i, s in enumerate(samples)
        if _label_of(s) in _CUES and all(map(math.isfinite, _gaze_deg(s.gaze)))
    ]
    if len(usable) < 2 * _MIN_SAMPLES_FOR_STATS:
        return 0.0
    correct = 0
    for i, sample in usable:
        rest = [s for j, s in usable if j != i]
        model = build_reference_model(rest, cfg)
        if model is None:
            continue
        label, _ = decide_label(model.posterior(*_gaze_deg(sample.gaze), *_other_rule(cfg)), cfg)
        correct += int(label == _label_of(sample))
    return correct / float(len(usable))


def _label_of(sample: CalibrationSample) -> Optional[str]:
    try:
        return GazeLabel.coerce(sample.label).value
    except ValueError:
        return None


def assess_reference_calibration(
    samples: Sequence[CalibrationSample],
    cfg: CalibrationConfig,
    model: Optional[ReferenceModel],
) -> CalibrationQuality:
    """Score a calibration and apply the pass rule, most specific reason first.

    Order: NOT_ENOUGH_SAMPLES (nothing else is measurable) -> DEGENERATE_FEATURES
    (the gaze never moved) -> CLASS_NOT_SEPARABLE (CAMERA vs BOTTOM, the pair the
    product exists for) -> CENTROIDS_TOO_CLOSE (any other anchor pair) ->
    ANCHOR_AMBIGUOUS (a region too diluted to ever be decided) ->
    LOW_LOO_ACCURACY (geometry looked fine, the rule still fails).  The pitch
    ordering CAMERA > SCREEN > BOTTOM is advisory, as in the logistic path.
    """
    values = _cue_values(samples)
    n = {c: int(values[c].shape[0]) for c in _CUES}
    quality = CalibrationQuality(
        status=CalibrationStatus.OK.value,
        n_camera=n[_CAMERA],
        n_bottom=n[_BOTTOM],
        n_screen=n[_SCREEN],
        method="reference",
    )
    floor = int(cfg.min_samples_per_class)
    if model is None or min(n[_CAMERA], n[_BOTTOM]) < _MIN_SAMPLES_FOR_STATS:
        return _fail(quality, CalibrationFailReason.NOT_ENOUGH_SAMPLES)

    quality.anchors_deg = {c: [round(y, 4), round(p, 4)] for c, (y, p) in model.anchors.items()}
    quality.sigma_deg = [round(model.sigma[0], 4), round(model.sigma[1], 4)]
    quality.camera_centroid = [math.radians(v) for v in model.anchors[_CAMERA]]
    quality.bottom_centroid = [math.radians(v) for v in model.anchors[_BOTTOM]]
    pairs = [(_CAMERA, _BOTTOM)] + [
        (a, b) for a, b in ((_CAMERA, _SCREEN), (_SCREEN, _BOTTOM)) if a in model.anchors and b in model.anchors
    ]
    quality.pair_separation = {_pair_name(a, b): round(_separation(model, a, b), 4) for a, b in pairs}
    quality.separability = quality.pair_separation[_pair_name(_CAMERA, _BOTTOM)]
    cam, bot = model.anchors[_CAMERA], model.anchors[_BOTTOM]
    quality.centroid_distance = math.hypot(cam[0] - bot[0], cam[1] - bot[1])
    quality.camera_variance = float(np.mean(np.sum((values[_CAMERA] - np.asarray(cam)) ** 2, axis=1)))
    quality.bottom_variance = float(np.mean(np.sum((values[_BOTTOM] - np.asarray(bot)) ** 2, axis=1)))
    radius = model.camera_capture_radius_deg()
    quality.camera_capture_radius_deg = round(radius, 4) if math.isfinite(radius) else 0.0

    warning: Optional[str] = None
    if cfg.check_pitch_ordering:
        order = [c for c in _CUES if c in model.anchors]
        pitches = [model.anchors[c][1] for c in order]
        if any(hi <= lo for hi, lo in zip(pitches, pitches[1:])):
            warning = (
                f"{INVERTED_PITCH_HINT_PREFIX} the targets are not ordered top to bottom "
                f"({' > '.join(order)} expected). Check whether the targets were followed in "
                "reverse or the camera is not above the screen."
            )
            quality.warnings.append(warning)
    for cue, (sy, sp) in model.spreads.items():
        if sy > 2.0 * model.sigma[0] or sp > 2.0 * model.sigma[1]:
            quality.warnings.append(f"{cue} spread is more than twice the pooled noise")

    if min(n[_CAMERA], n[_BOTTOM]) < floor:
        return _fail(quality, CalibrationFailReason.NOT_ENOUGH_SAMPLES, warning=warning)
    if 0 < n[_SCREEN] < floor:
        return _fail(quality, CalibrationFailReason.NOT_ENOUGH_SAMPLES, "SCREEN", warning)

    everything = np.vstack([values[c] for c in _CUES if values[c].size])
    if bool(np.all(everything.std(axis=0) <= 1e-8)):
        return _fail(quality, CalibrationFailReason.DEGENERATE_FEATURES, warning=warning)

    min_sep = float(cfg.min_anchor_separation)
    if quality.separability < min_sep:
        return _fail(quality, CalibrationFailReason.CLASS_NOT_SEPARABLE, warning=warning)
    for name, sep in quality.pair_separation.items():
        if sep < min_sep:
            return _fail(quality, CalibrationFailReason.CENTROIDS_TOO_CLOSE, name, warning)

    floor_p = float(cfg.p_max_threshold) + _SELF_POSTERIOR_MARGIN
    for cue, (ay, ap) in model.anchors.items():
        if model.posterior(ay, ap, *_other_rule(cfg)).get(cue, 0.0) < floor_p:
            return _fail(quality, CalibrationFailReason.ANCHOR_AMBIGUOUS, cue, warning)

    quality.loo_accuracy = leave_one_out_accuracy(samples, cfg)
    if quality.loo_accuracy < float(cfg.min_loo_accuracy):
        return _fail(quality, CalibrationFailReason.LOW_LOO_ACCURACY, warning=warning)

    quality.hint = warning
    return quality


# --------------------------------------------------------------------------
# Classifier
# --------------------------------------------------------------------------


def _finite_gaze(gaze: Optional[GazeVector]) -> bool:
    return gaze is not None and math.isfinite(gaze.gaze_yaw) and math.isfinite(gaze.gaze_pitch)


class ReferenceAnchorClassifier:
    """CAMERA / SCREEN / BOTTOM / OTHER for one user, from that user's anchors.

    Same surface as ``PerUserGazeClassifier`` (``fit``, ``is_fitted``,
    ``quality``, ``decide``) so the runtime can hold either.  Nothing is
    persisted: the model lives and dies with the session.
    """

    method = "reference"

    def __init__(
        self,
        model: Optional[ReferenceModel],
        cfg: CalibrationConfig,
        quality: Optional[CalibrationQuality] = None,
    ) -> None:
        self._model = model
        self._cfg = cfg
        self._quality = quality

    @classmethod
    def fit(
        cls, samples: Sequence[CalibrationSample], cfg: CalibrationConfig
    ) -> Tuple["ReferenceAnchorClassifier", CalibrationQuality]:
        """Build the anchors and grade them.

        Like the logistic path, a model is built whenever CAMERA and BOTTOM are
        both present, even when the grade is RETRY_REQUIRED: the retry policy
        belongs to the caller.
        """
        model = build_reference_model(samples, cfg)
        quality = assess_reference_calibration(samples, cfg, model)
        if (
            not quality.ok
            and bool(cfg.merge_inseparable_screen)
            and model is not None
            and _SCREEN in model.anchors
            and quality.reason in _SCREEN_FIXABLE
        ):
            merged = cls._fit_without_screen(samples, cfg, quality)
            if merged is not None:
                return merged
        return cls(model, cfg, quality), quality

    @classmethod
    def _fit_without_screen(
        cls, samples: Sequence[CalibrationSample], cfg: CalibrationConfig,
        full: CalibrationQuality,
    ) -> Optional[Tuple["ReferenceAnchorClassifier", CalibrationQuality]]:
        """CAMERA / BOTTOM / OTHER only, when that grade passes.

        With the head-pose backbone a user may look from the lens to the screen
        centre without moving their head; those two anchors then coincide and
        the full model fails.  The lens-versus-script distinction the product
        exists for is still measurable, so rather than refusing the take, a look
        at the screen is reported as CAMERA (facing front).  The full grade's
        pair separations are kept so the report shows why.
        """
        reduced = [s for s in samples if _label_of(s) != _SCREEN]
        model = build_reference_model(reduced, cfg)
        quality = assess_reference_calibration(reduced, cfg, model)
        if model is None or not quality.ok:
            return None
        quality.n_screen = full.n_screen
        quality.pair_separation = dict(full.pair_separation or {})
        quality.warnings.append(SCREEN_MERGED_WARNING)
        quality.hint = f"{quality.hint} {SCREEN_MERGED_WARNING}" if quality.hint else SCREEN_MERGED_WARNING
        return cls(model, cfg, quality), quality

    # -- introspection ----------------------------------------------------
    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    @property
    def config(self) -> CalibrationConfig:
        return self._cfg

    @property
    def quality(self) -> Optional[CalibrationQuality]:
        return self._quality

    @property
    def model(self) -> Optional[ReferenceModel]:
        return self._model

    @property
    def classes(self) -> Tuple[str, ...]:
        return self._model.classes if self._model is not None else tuple(DECISION_CLASSES)

    # -- inference --------------------------------------------------------
    def predict_probs(self, gaze: GazeVector, head: Optional[HeadPose] = None) -> Dict[str, float]:
        if self._model is None:
            raise RuntimeError(
                "ReferenceAnchorClassifier is not fitted; calibration produced "
                f"reason={None if self._quality is None else self._quality.reason}"
            )
        if head is not None and _OTHER in self._model.classes and head_is_away(self._model, head, self._cfg):
            return {c: (1.0 if c == _OTHER else 0.0) for c in self._model.classes}
        return self._model.posterior(*_gaze_deg(gaze), *_other_rule(self._cfg))

    def decide(
        self,
        obs: FrameObservation,
        gaze: Optional[GazeVector],
        latency_ms: float = 0.0,
    ) -> GazeDecision:
        """The doc 5-4 rule over the anchor posterior.

        An unusable frame is UNCERTAIN with the preprocess reason, exactly as in
        the logistic path.  A head turned far from the calibration pose is
        OTHER whatever the eyes read: the eye estimate is unreliable there and
        the person is, by any reading, not looking at the screen.
        """
        if not obs.face_valid or not _finite_gaze(gaze):
            reason = obs.invalid_reason
            if reason is None:
                reason = InvalidReason.BACKBONE_FAILED.value if obs.face_valid else InvalidReason.NO_FACE.value
            return GazeDecision(
                t_ms=obs.t_ms, frame_id=obs.frame_id, label=GazeState.UNCERTAIN.value,
                p_camera=0.5, p_bottom=0.5, face_valid=False, uncertain_reason=reason,
                gaze=gaze, latency_ms=latency_ms,
            )
        if self._model is None:
            raise RuntimeError("ReferenceAnchorClassifier is not fitted")

        away = head_is_away(self._model, obs.head_pose, self._cfg)
        if away and _OTHER not in self._model.classes:
            probs = {c: 1.0 / len(self._model.classes) for c in self._model.classes}
            label, reason = GazeState.UNCERTAIN.value, UNCERTAIN_HEAD_AWAY
        else:
            probs = self.predict_probs(gaze, obs.head_pose)
            label, reason = decide_label(probs, self._cfg)
        offset = gaze_offset(self._model, *_gaze_deg(gaze), float(self._cfg.screen_min_halfwidth_deg))
        return GazeDecision(
            t_ms=obs.t_ms, frame_id=obs.frame_id, label=label,
            p_camera=float(probs.get(_CAMERA, 0.0)), p_bottom=float(probs.get(_BOTTOM, 0.0)),
            face_valid=True, uncertain_reason=reason, gaze=gaze, latency_ms=latency_ms,
            probs=probs, offset_deg=offset, aim_deg=aim_offset(self._model, *_gaze_deg(gaze)),
            direction=direction_of(offset) if label == _OTHER else None,
        )

    # -- re-anchor ------------------------------------------------------------
    def with_offset(self, dyaw_deg: float, dpitch_deg: float) -> "ReferenceAnchorClassifier":
        """A copy with every anchor moved by one gaze offset (offset-only update)."""
        if self._model is None:
            raise RuntimeError("cannot shift an unfitted ReferenceAnchorClassifier")
        return ReferenceAnchorClassifier(self._model.shifted(dyaw_deg, dpitch_deg), self._cfg, self._quality)

    def with_head_baseline(self, head: HeadPose) -> "ReferenceAnchorClassifier":
        """A copy whose head-away rule measures from a new pose."""
        if self._model is None:
            raise RuntimeError("cannot rebase an unfitted ReferenceAnchorClassifier")
        baseline = (math.degrees(float(head.yaw)), math.degrees(float(head.pitch)))
        return ReferenceAnchorClassifier(replace(self._model, head_baseline=baseline), self._cfg, self._quality)

    def __repr__(self) -> str:
        return (
            f"ReferenceAnchorClassifier(classes={self.classes}, fitted={self.is_fitted}, "
            f"status={None if self._quality is None else self._quality.status})"
        )
