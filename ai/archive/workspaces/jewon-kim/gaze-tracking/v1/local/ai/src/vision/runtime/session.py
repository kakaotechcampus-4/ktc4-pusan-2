"""One take, wired end to end: frames in, GAZE_STATE out (doc 3-1 -> doc 6).

``VisionSession`` owns the four stages that only make sense together -- the
preprocess pipeline (doc 3-1), the gaze backbone (doc 3-2), the per-user
classifier fitted from this take's own calibration (doc 5) and the temporal
smoother (doc 6) -- plus the state none of them can hold alone: the
session-wide frame counter, the calibration sample buffer, the face being
followed, and the calibration scene the live frames are compared with.

A take runs in three steps:

1. ``check_preconditions`` on the preview: one presenter, centred, at a
   usable distance and in usable light (``runtime.preconditions``).
2. The head circle (``start_head_sweep`` / ``offer_sweep_frame``) measures
   the baseline: its centre is the presenter looking at the screen.
   Calibration then confirms and completes it, one gauge-driven cue at a time
   (``start_calibration_cue`` / ``offer_calibration_frame``): the screen-centre
   look confirms the circle's centre (``baseline_check``) and the lens and
   script looks are read from the confirmed posture -- the lens, the
   screen centre, the script -- looked at naturally with the default head-pose
   backbone, head held still with an eye backbone (:attr:`eye_based`).  ``estimate_placement`` reads the camera
   position off the first two; ``finish_calibration`` fits the classifier
   ``calibration.method`` names and arms the live monitors.
3. ``process_frame``: a GAZE_STATE stream, plus a SESSION_CONDITION stream
   that lowers ``reliability`` as the scene drifts from the calibration scene
   (``runtime.condition``) and forces decisions to UNCERTAIN when the
   calibrated face is gone or replaced.  ``begin_reanchor`` re-centres the
   anchors on the lens mid-take.

Nothing here is persisted: samples, anchors and baselines die with the session.

What this class deliberately does not do
----------------------------------------
*Decimate.*  The caller decides which frames are worth analysing; doc 3-1's
``FrameSampler`` lives on the capture side, where the source timestamps are.
Every frame handed to :meth:`process_frame` is analysed.

*Enforce calibration quality.*  :meth:`finish_calibration` returns the doc 5-2
report and keeps whatever model could be trained.  The retry decision belongs
to the caller: the demo shows ``quality.hint`` and asks for another attempt,
while doc 23's ablation wants the numbers from a poor calibration too, and
``PerUserGazeClassifier.fit`` is explicit that it never decides this for you.
So :attr:`is_calibrated` answers "can this session produce a decision at all",
and callers that need the stronger statement read
``calibration_quality.ok``.

Latency
-------
``GazeDecision.latency_ms`` is measured around the *whole* per-frame path --
BGR->RGB, landmarker, crops, head pose, backbone, classifier -- because that is
the quantity doc 7's release gate compares against 125 ms.  It is written after
``decide`` returns rather than passed into it, so the classifier's own cost
falls inside the measurement instead of just outside it.

Failure policy
--------------
A backbone raising mid-take must not end the take.  The frame becomes an
UNCERTAIN decision carrying ``BACKBONE_FAILED`` (doc 5-4), which the smoother
treats as an abstention (doc 6), and the exception text is kept on
:attr:`last_backbone_error` so a dead backbone is diagnosable rather than merely
survivable -- a run where every frame abstains looks the same from the outside
as a user who left the room.
"""

from __future__ import annotations

import copy
import json
import math
import time
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from vision.backbones.base import GazeBackbone
from vision.backbones.registry import build_backbone
from vision.calibration.factory import GazeClassifier, fit_gaze_classifier
from vision.calibration.gauge import NOT_ACTIVE, SETTLING, CalibrationGauge, GaugeState, GaugeStatus
from vision.calibration.references import robust_sigma
from vision.config import VisionConfig
from vision.eye.blink import AdaptiveBlinkGate
from vision.preprocess.pipeline import FaceHint, PreprocessPipeline, face_centre
from vision.evidence.gaze import GazeEvidenceRecorder
from vision.runtime.condition import (
    ConditionMonitor,
    ConditionState,
    SceneBaselineAccumulator,
    baseline_from_observations,
)
from vision.runtime.preconditions import PreconditionChecker, PreconditionReport
from vision.runtime.placement import (
    TARGET_CAMERA,
    TARGET_SCREEN,
    CameraPlacementCheck,
    PlacementCheckResult,
    placement_from_anchors,
)
from vision.runtime.sweep import CENTERING, HeadSweep, SweepStatus
from vision.runtime.version import MODEL_VERSION, current_ai_version
from vision.schemas import (
    AiVersion,
    CalibrationQuality,
    CalibrationSample,
    FrameObservation,
    GazeDecision,
    GazeLabel,
    GazeState,
    GazeStateEvent,
    GazeVector,
    HeadPose,
    InvalidReason,
    SessionConditionEvent,
)
from vision.temporal.smoother import TemporalSmoother

#: How many recent end-to-end latencies to keep for :meth:`latency_percentile`.
#: At ``analysis_fps=8`` this is the last ~2 minutes, which is the window a live
#: overlay should report; the full history belongs in the feature table, not in
#: a process that runs for an hour.
_LATENCY_HISTORY = 1024

#: Preprocess reasons that concern only the eyes: a backbone that reads the
#: head (``uses_eyes = False``) keeps these frames and judges the head direction.
_EYE_ONLY_REASONS = frozenset({InvalidReason.EYES_CLOSED.value, InvalidReason.CROP_FAILED.value})


def _finite(gaze: Optional[GazeVector]) -> bool:
    return (
        gaze is not None
        and math.isfinite(gaze.gaze_yaw)
        and math.isfinite(gaze.gaze_pitch)
    )


def _calibration_label(cue: Union[str, GazeLabel]) -> str:
    """Canonical cue label (CAMERA, SCREEN or BOTTOM), rejecting anything else.

    IGNORE is a dataset label (doc 4-2 guard bands), never a calibration cue; a
    caller that passes it has a bug in its cue timeline, and dropping the frame
    silently would surface later as NOT_ENOUGH_SAMPLES with no explanation.
    """
    label = GazeLabel.coerce(cue)
    if label is GazeLabel.IGNORE:
        raise ValueError(
            f"calibration cue must be {GazeLabel.CAMERA.value}, {GazeLabel.SCREEN.value} "
            f"or {GazeLabel.BOTTOM.value}, got {cue!r}"
        )
    return label.value


@dataclass
class ReanchorStatus:
    """Progress of a live "look at the lens again" offset update."""

    #: IDLE, COLLECTING, DONE, REJECTED, TIMED_OUT or UNSUPPORTED.
    state: str = "IDLE"
    collected: int = 0
    target: int = 0
    #: Applied (DONE) or measured-and-refused (REJECTED) offset, degrees.
    shift_deg: Optional[Tuple[float, float]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class BaselineCheck:
    """How the calibration's screen-centre look compared with the head circle's centre."""

    #: The look landed within ``cue_confirm_deg`` of the circle's centre.
    confirmed: bool
    #: Distance between the two postures, degrees.
    shift_deg: float
    #: Circle-centre frames folded into the screen-centre samples (0 when remeasured).
    seeded: int

    def to_dict(self) -> Dict[str, object]:
        return {"confirmed": self.confirmed, "shift_deg": self.shift_deg, "seeded": self.seeded}


class VisionSession:
    """Calibration and live inference for one recording take.

    Not thread-safe, and it expects timestamps in order: the smoother measures
    every duration from ``t_ms`` (doc 6).  One instance per take; ``close`` is
    idempotent and releases only what the session built itself.
    """

    def __init__(
        self,
        cfg: VisionConfig,
        backbone: Optional[GazeBackbone] = None,
        pipeline: Optional[PreprocessPipeline] = None,
        *,
        model_version: str = MODEL_VERSION,
    ) -> None:
        self.cfg = cfg
        # Injected collaborators stay the caller's property: doc 23's Experiment
        # 1 runs several sessions over one landmarker graph, and closing a
        # borrowed MediaPipe graph would kill the sessions still using it.
        self._owns_pipeline = pipeline is None
        self._owns_backbone = backbone is None
        self.pipeline = pipeline if pipeline is not None else PreprocessPipeline(cfg)
        try:
            self.backbone = backbone if backbone is not None else build_backbone(cfg.backbone)
            self.smoother = TemporalSmoother(cfg.temporal, model_version=model_version)
        except BaseException:
            # A build failure here is a *documented* path, not an exotic one:
            # build_backbone raises CheckpointMissingError whenever the weights
            # are not installed.  __init__ then never returns, so the caller has
            # no object to close() -- previously the MediaPipe graph opened one
            # line above simply leaked for the life of the process.  Release
            # only what this session built; an injected collaborator is the
            # caller's property whether or not we got as far as using it.
            self._close_owned()
            raise
        self.model_version = model_version

        self._placement = CameraPlacementCheck(cfg.placement)
        self._placement_result: Optional[PlacementCheckResult] = None
        self._samples: List[CalibrationSample] = []
        self._classifier: Optional[GazeClassifier] = None
        self._gauge = self._new_gauge()
        self._cue: Optional[str] = None
        self._quality: Optional[CalibrationQuality] = None
        self._frame_id = 0
        self._events: List[GazeStateEvent] = []
        self._latencies: Deque[float] = deque(maxlen=_LATENCY_HISTORY)
        self._rejected: Counter = Counter()
        self._last_decision: Optional[GazeDecision] = None
        self._last_observation: Optional[FrameObservation] = None
        self._last_gaze: Optional[GazeVector] = None
        self._last_event_emitted = False
        self.last_backbone_error: Optional[str] = None
        self._closed = False

        # Set-up check, main-face following, and the live condition monitor.
        self._preconditions = PreconditionChecker(cfg.preconditions, eye_based=self.eye_based)
        #: Head circle check between the set-up check and calibration (``runtime.sweep``).
        self._sweep = HeadSweep(cfg.sweep)
        #: The frames that made the circle's centre, kept to fold into the screen-centre look.
        self._neutral_frames: List[Tuple[FrameObservation, Optional[GazeVector]]] = []
        #: The screen-centre look is confirming the circle's centre (short target).
        self._confirming = False
        self._baseline_check: Optional[BaselineCheck] = None
        self._face_hint: Optional[FaceHint] = None
        self._scene_acc = SceneBaselineAccumulator()
        self._cue_ear: Dict[str, List[float]] = {}
        self._blink = AdaptiveBlinkGate(cfg.preprocess)
        self._monitor: Optional[ConditionMonitor] = None
        self._last_condition: Optional[ConditionState] = None
        self._condition_events: List[SessionConditionEvent] = []
        #: 1 s gaze slices for the coach / review agents (``vision.evidence.gaze``).
        self._evidence = GazeEvidenceRecorder(cfg.evidence)
        self._reanchor = ReanchorStatus()
        self._reanchor_started_ms = 0
        self._reanchor_obs: List[FrameObservation] = []
        self._reanchor_gaze: List[Tuple[float, float]] = []

    # -- introspection ----------------------------------------------------
    @property
    def is_calibrated(self) -> bool:
        """True once a model exists that can score a frame (see module docstring)."""
        return self._classifier is not None

    @property
    def classifier(self) -> Optional[GazeClassifier]:
        """The fitted per-user classifier (``calibration.method`` picks the family)."""
        return self._classifier

    @property
    def calibration_quality(self) -> Optional[CalibrationQuality]:
        """doc 5-2 report from the last :meth:`finish_calibration`, if any."""
        return self._quality

    @property
    def calibration_samples(self) -> List[CalibrationSample]:
        """Copy of the buffer, so a caller cannot rewrite what was fitted.

        Deep, not shallow.  ``CalibrationSample`` holds mutable ``GazeVector``
        and ``HeadPose`` dataclasses, so a ``list(...)`` handed the caller the
        very objects a second :meth:`finish_calibration` would fit on: an
        overlay that "normalised" a returned angle silently moved the anchors of
        a doc 5-2 retry.  A few dozen scalar dataclasses per take is not a cost
        worth trading that for.
        """
        return [copy.deepcopy(sample) for sample in self._samples]

    @property
    def calibration_counts(self) -> Dict[str, int]:
        """Accepted samples per cue -- what a progress bar counts down (doc 5-1).

        Always all three cues, so a cue with no samples yet reads 0 instead of
        being absent.
        """
        counts = Counter(s.label for s in self._samples)
        return {
            GazeLabel.CAMERA.value: int(counts.get(GazeLabel.CAMERA.value, 0)),
            GazeLabel.BOTTOM.value: int(counts.get(GazeLabel.BOTTOM.value, 0)),
            GazeLabel.SCREEN.value: int(counts.get(GazeLabel.SCREEN.value, 0)),
        }

    @property
    def gauge_status(self) -> GaugeStatus:
        """The good-frame gauge of the cue being collected (or the last one)."""
        return self._gauge.status

    @property
    def current_cue(self) -> Optional[str]:
        return self._cue

    @property
    def rejected_frames(self) -> Dict[str, int]:
        """Rejected calibration frames by reason (doc 3-1 codes).

        The retry screen of doc 5-2 gets its concrete advice from here: "17
        frames were EYES_CLOSED" is actionable, "calibration failed" is not.
        """
        return dict(self._rejected)

    @property
    def events(self) -> List[GazeStateEvent]:
        """Events that passed ``should_emit`` -- transitions and heartbeats only."""
        return list(self._events)

    @property
    def last_gaze(self) -> Optional[GazeVector]:
        """Gaze for the most recent frame, from any stage (overlay/debug use).

        ``last_decision`` only exists once a classifier does, so calibration UI
        and diagnostics need this separate value.  ``None`` means the last
        frame produced no estimate.
        """
        return self._last_gaze

    @property
    def last_observation(self) -> Optional[FrameObservation]:
        """The most recent preprocess result, from any stage.

        Exposed for overlays and debugging: a UI wants to draw the landmarks and
        the reject reason for the frame it is showing, and re-running the
        landmarker on the display path would double the per-frame cost that
        doc 7's latency gate measures.
        """
        return self._last_observation

    @property
    def last_decision(self) -> Optional[GazeDecision]:
        return self._last_decision

    @property
    def last_event(self) -> Optional[GazeStateEvent]:
        return self.smoother.last_event

    @property
    def last_event_emitted(self) -> bool:
        """Whether the most recent frame's event went on the wire (doc 6-2)."""
        return self._last_event_emitted

    @property
    def frames_processed(self) -> int:
        """Frames handed to preprocess in this session, calibration included."""
        return self._frame_id

    def latency_percentile(self, percentile: float = 95.0) -> float:
        """Percentile of recent end-to-end latencies, 0.0 before any frame.

        Nearest-rank on the raw samples rather than an interpolated quantile:
        doc 7 gates on p95 against a 125 ms budget, and the honest reading of
        "p95" for a few hundred frames is a latency that was actually measured.
        """
        if not self._latencies:
            return 0.0
        values = sorted(self._latencies)
        rank = max(1, math.ceil(float(percentile) / 100.0 * len(values)))
        return float(values[min(rank, len(values)) - 1])

    def ai_version(self) -> AiVersion:
        """Version stamp for this take (doc 15); names the backbone in use."""
        return current_ai_version(self.cfg, self.backbone.name)

    # -- calibration ------------------------------------------------------
    def add_calibration_frame(
        self, bgr: np.ndarray, cue: Union[str, GazeLabel], t_ms: int
    ) -> bool:
        """Offer one cued frame to the calibration buffer (doc 5-1).

        Returns True when the frame became a usable sample.  A False is normal
        and not an error -- a blink or a moment out of frame during the two
        seconds of CAMERA -- so the caller keeps feeding frames and checks
        :attr:`calibration_counts` against
        ``cfg.calibration.min_samples_per_class``.

        Refuses to run once a model is fitted: silently mixing new cues into a
        session that is already scoring frames would leave the anchors frozen at
        :meth:`finish_calibration` describing a set that no longer exists.
        Call :meth:`reset_calibration` for a doc 5-2 retry.
        """
        self._assert_open()
        label = _calibration_label(cue)
        if self._classifier is not None:
            raise RuntimeError(
                "VisionSession is already calibrated; call reset_calibration() "
                "before collecting new calibration frames (doc 5-2 retry)"
            )

        obs = self._observe(bgr, t_ms)
        gaze = self._estimate_gaze(obs)
        if not _finite(gaze):
            self._rejected[self._reject_reason(obs, gaze)] += 1
            return False

        self._samples.append(
            CalibrationSample(
                label=label,
                gaze=gaze,
                head_pose=obs.head_pose,
                t_ms=int(t_ms),
                frame_id=obs.frame_id,
            )
        )
        self._record_calibration_scene(label, obs)
        return True

    def start_calibration_cue(self, cue: Union[str, GazeLabel], t_ms: int) -> GaugeStatus:
        """Begin one gauge-driven cue: CAMERA, SCREEN or BOTTOM.

        The head is held still and only the eyes move between the cues, so the
        head pose of the first settled frame becomes the reference every later
        cue is checked against.  Restarting a cue (a retry of just that look)
        replaces its earlier samples instead of mixing two attempts.
        """
        self._assert_open()
        label = _calibration_label(cue)
        self._refuse_when_calibrated()
        self._samples = [s for s in self._samples if s.label != label]
        self._cue_ear.pop(label, None)
        self._cue = label
        self._gauge.set_direction_reference(self._cue_direction_reference(label))
        target = None
        if label == GazeLabel.SCREEN.value:
            self._baseline_check = None
            self._confirming = self._can_confirm_baseline()
            if self._confirming:
                target = int(self.cfg.calibration.cue_confirm_frames)
        self._gauge.start(label, int(t_ms), target)
        return self._gauge.status

    def _can_confirm_baseline(self) -> bool:
        """A head circle centre exists for the screen-centre look to confirm."""
        return (
            not self.eye_based
            and bool(self.cfg.calibration.cue_direction_gate)
            and self._sweep.status.neutral_deg is not None
            and len(self._neutral_frames) >= int(self.cfg.sweep.neutral_frames)
        )

    def _cue_direction_reference(self, label: str) -> Optional[Tuple[float, float]]:
        """Where the cue directions are read from.

        The screen-centre look is checked against the head circle's centre.  The
        lens and script looks are read from the screen-centre posture once it is
        measured -- the circle's centre when the look confirmed it, the new
        posture when the presenter had moved -- else from the circle's centre.
        """
        neutral = self._sweep.status.neutral_deg
        if label == GazeLabel.SCREEN.value:
            return neutral
        screen = self._screen_posture()
        return screen if screen is not None else neutral

    def _screen_posture(self) -> Optional[Tuple[float, float]]:
        """Median head ``(yaw, pitch)`` of the screen-centre samples, once there are enough."""
        heads = [(math.degrees(s.head_pose.yaw), math.degrees(s.head_pose.pitch))
                 for s in self._samples if s.label == GazeLabel.SCREEN.value and s.head_pose is not None]
        if len(heads) < int(self.cfg.calibration.min_samples_per_class):
            return None
        arr = np.asarray(heads, dtype=float)
        return float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))

    def _settle_baseline(self) -> None:
        """Once the screen-centre look is done: confirm the circle's centre, or measure anew.

        The look's own frames are compared with the circle's centre.  Close
        enough, the circle's frames join the screen-centre samples and the look
        ends; else the look goes on to a full measurement of the new posture.
        """
        neutral = self._sweep.status.neutral_deg
        if (self._cue != GazeLabel.SCREEN.value or self._baseline_check is not None or neutral is None
                or self._gauge.status.state != GaugeState.DONE.value):
            return
        heads = np.asarray([(math.degrees(s.head_pose.yaw), math.degrees(s.head_pose.pitch))
                            for s in self._samples if s.label == GazeLabel.SCREEN.value], dtype=float)
        if heads.size == 0:
            return
        med = np.median(heads, axis=0)
        shift = float(math.hypot(med[0] - neutral[0], med[1] - neutral[1]))
        close = shift <= float(self.cfg.calibration.cue_confirm_deg)
        if self._confirming and close:
            seeded = 0
            for obs, gaze in self._neutral_frames:
                if not _finite(gaze) or float(gaze.confidence) < float(self.cfg.calibration.min_sample_confidence):
                    continue
                self._samples.append(CalibrationSample(
                    label=GazeLabel.SCREEN.value, gaze=gaze, head_pose=obs.head_pose,
                    t_ms=int(obs.t_ms), frame_id=obs.frame_id,
                ))
                self._record_calibration_scene(GazeLabel.SCREEN.value, obs)
                seeded += 1
            self._baseline_check = BaselineCheck(confirmed=True, shift_deg=shift, seeded=seeded)
        elif self._confirming:
            # The presenter moved since the circle: measure this posture in full.
            self._gauge.extend(int(self.cfg.calibration.target_good_frames))
            self._baseline_check = BaselineCheck(confirmed=False, shift_deg=shift, seeded=0)
        else:
            self._baseline_check = BaselineCheck(confirmed=close, shift_deg=shift, seeded=0)
        self._confirming = False

    @property
    def baseline_check(self) -> Optional[BaselineCheck]:
        """How the screen-centre look compared with the head circle's centre (None before it ends)."""
        return self._baseline_check

    def offer_calibration_frame(self, bgr: np.ndarray, t_ms: int) -> GaugeStatus:
        """Analyse one frame for the current cue and report the gauge.

        A frame becomes a calibration sample only when the gauge accepts it
        (see ``calibration.gauge``); the returned status says how full the cue
        is, whether it finished, and what is wrong while it is not filling.
        """
        self._assert_open()
        if self._cue is None:
            raise RuntimeError("offer_calibration_frame() before start_calibration_cue()")
        self._refuse_when_calibrated()
        obs = self._observe(bgr, t_ms)
        gaze = self._estimate_gaze(obs)
        accepted, reason = self._gauge.offer(obs, gaze, int(t_ms))
        if accepted:
            self._samples.append(
                CalibrationSample(
                    label=self._cue, gaze=gaze, head_pose=obs.head_pose,
                    t_ms=int(t_ms), frame_id=obs.frame_id,
                )
            )
            self._record_calibration_scene(self._cue, obs)
        elif reason is not None and reason not in (SETTLING, NOT_ACTIVE):
            self._rejected[reason] += 1
        self._settle_baseline()
        return self._gauge.status

    def estimate_placement(self) -> Optional[PlacementCheckResult]:
        """Camera placement from the CAMERA and SCREEN samples collected so far.

        ``None`` until both cues have samples.  Runs between the SCREEN and the
        BOTTOM cue so an unsupported camera position is caught before the user
        is asked for the last look, and again inside ``finish_calibration``.
        """
        cam = _cue_gaze_deg(self._samples, GazeLabel.CAMERA.value)
        scr = _cue_gaze_deg(self._samples, GazeLabel.SCREEN.value)
        if cam.shape[0] == 0 or scr.shape[0] == 0:
            return None
        cam_med, scr_med = np.median(cam, axis=0), np.median(scr, axis=0)
        residuals = np.vstack([cam - cam_med, scr - scr_med])
        sigma = (
            robust_sigma(residuals[:, 0], self.cfg.calibration),
            robust_sigma(residuals[:, 1], self.cfg.calibration),
        )
        result = placement_from_anchors(
            (float(cam_med[0]), float(cam_med[1])),
            (float(scr_med[0]), float(scr_med[1])),
            cam.shape[0], scr.shape[0], sigma, self.cfg.placement,
        )
        self._placement_result = result
        return result

    def finish_calibration(self) -> CalibrationQuality:
        """Fit the per-user model and report doc 5-2 quality.

        Always returns a report, including for a set too small to train on
        (``NOT_ENOUGH_SAMPLES``), in which case no model is kept and
        :attr:`is_calibrated` stays False.  The smoother is reset either way:
        evidence gathered while the user was staring at a calibration cue must
        not leak into the first live state.  With a model, the smoother is
        switched to that model's class set, so a multi-class decision is never
        squeezed through a two-class smoother.  When a SCREEN cue was
        collected, the camera placement read off the anchors rides along in
        ``quality.placement``.
        """
        self._assert_open()
        classifier, quality = fit_gaze_classifier(self._samples, self.cfg.calibration)
        self._quality = quality
        self._classifier = classifier if classifier.is_fitted else None
        placement = self.estimate_placement()
        if placement is not None:
            quality.placement = placement.to_dict()
        if self._classifier is not None:
            self.smoother.set_classes(self._classifier.classes)
            self._arm_live_monitors()
        else:
            self.smoother.reset()
        self._cue = None
        return quality

    def _record_calibration_scene(self, cue: str, obs: FrameObservation) -> None:
        """Remember what an accepted calibration frame looked like.

        Only the scene numbers are kept (face position and size, iris size,
        brightness, head pose, eye openness) -- they become the reference the
        live condition monitor and the blink gate compare against.
        """
        self._scene_acc.add(obs, cue=cue)
        self._cue_ear.setdefault(cue, []).append(float(obs.quality.min_eye_openness))

    def _arm_live_monitors(self) -> None:
        baseline = self._scene_acc.build()
        # With the head-pose backbone the head direction IS the gaze: turning away is
        # a direction (OTHER), not a worse measurement, so it does not lower reliability.
        self._monitor = (
            ConditionMonitor(self.cfg.condition, baseline, head_is_gaze=not self.eye_based)
            if baseline is not None else None
        )
        self._last_condition = None
        self._condition_events = []
        self._evidence.reset()
        if self.eye_based:
            # Only an eye-reading backbone loses a frame to a half-closed lid;
            # without a reference the gate stays inactive.
            open_eye = [v for cue, values in self._cue_ear.items() for v in values]
            self._blink.calibrate(open_eye, self._cue_ear.get(GazeLabel.BOTTOM.value, []))
        self._reanchor = ReanchorStatus()

    def reset_calibration(self) -> None:
        """Drop the model, the samples and the smoothed state for a retry (doc 5-2).

        The frame counter keeps running: frame ids stay unique within a take, so
        a log or a feature table written across a retry cannot collide.
        """
        self._samples.clear()
        self._classifier = None
        self._quality = None
        self._rejected.clear()
        self._events.clear()
        self._latencies.clear()
        self._last_decision = None
        self._last_event_emitted = False
        self._gauge = self._new_gauge()
        self._cue = None
        self._scene_acc.reset()
        self._cue_ear = {}
        self._blink.reset()
        self._monitor = None
        self._last_condition = None
        self._condition_events = []
        self._evidence.reset()
        self._reanchor = ReanchorStatus()
        self._confirming = False
        self._baseline_check = None
        self.smoother.reset()

    @property
    def eye_based(self) -> bool:
        """Does the backbone read the eyes?  ``False`` for the default ``head_pose``.

        Decides whether calibration holds the head still (eyes move, head does
        not) or lets it move (the head movement is the signal), and whether the
        adaptive blink gate runs.
        """
        return bool(getattr(self.backbone, "uses_eyes", True))

    def _new_gauge(self) -> CalibrationGauge:
        return CalibrationGauge(
            self.cfg.calibration, blink_ratio=self.cfg.preprocess.blink_ratio,
            eye_based=self.eye_based,
        )

    def _refuse_when_calibrated(self) -> None:
        if self._classifier is not None:
            raise RuntimeError(
                "VisionSession is already calibrated; call reset_calibration() "
                "before collecting new calibration frames (doc 5-2 retry)"
            )

    # -- preview ----------------------------------------------------------
    def preview(self, bgr: np.ndarray, t_ms: int) -> Tuple[FrameObservation, Optional[GazeVector]]:
        """Analyse a frame for display only, changing no session state.

        A UI that stops tracking between stages keeps showing the last face it
        saw, which is worse than showing nothing: a covered lens then looks
        exactly like a tracked face.  This runs the same preprocess and backbone
        the real stages run, so :attr:`last_observation` and :attr:`last_gaze`
        stay honest on every displayed frame, but it touches neither the
        calibration buffer, the placement buffer nor the smoother.
        """
        self._assert_open()
        obs = self._observe(bgr, t_ms)
        return obs, self._estimate_gaze(obs)

    # -- placement check (before calibration) -----------------------------
    def add_placement_frame(self, bgr: np.ndarray, target: str, t_ms: int) -> bool:
        """Offer one frame to the camera-placement check.

        ``target`` is ``CAMERA`` (look at the lens) or ``SCREEN`` (look at the
        middle of the display).  Same contract as
        :meth:`add_calibration_frame`: False means the frame was unusable, which
        is routine rather than an error.

        This runs before calibration on purpose.  Calibration happily fits a
        boundary for a camera mounted under the screen -- it just means every
        later CAMERA/BOTTOM label is inverted, which no downstream metric can
        detect (doc 19's webcam-position bucket, caught up front).
        """
        self._assert_open()
        obs = self._observe(bgr, t_ms)
        gaze = self._estimate_gaze(obs)
        if not _finite(gaze):
            self._rejected[self._reject_reason(obs, gaze)] += 1
            return False
        return self._placement.add_sample(target, gaze, obs.head_pose)

    def finish_placement(self) -> PlacementCheckResult:
        """Return the placement verdict; see :mod:`vision.runtime.placement`.

        Like :meth:`finish_calibration` this reports rather than enforces: an
        unsupported placement is the caller's decision to act on, because a
        research run may deliberately want the off-nominal geometry recorded.
        """
        self._assert_open()
        self._placement_result = self._placement.evaluate()
        return self._placement_result

    def reset_placement(self) -> None:
        """Drop placement samples for a retry, leaving calibration untouched."""
        self._placement.reset()
        self._placement_result = None

    @property
    def placement_result(self) -> Optional[PlacementCheckResult]:
        return self._placement_result

    @property
    def placement_counts(self) -> Dict[str, int]:
        return {
            TARGET_CAMERA: self._placement.count(TARGET_CAMERA),
            TARGET_SCREEN: self._placement.count(TARGET_SCREEN),
        }

    # -- live loop --------------------------------------------------------
    def process_frame(self, bgr: np.ndarray, t_ms: int) -> Tuple[GazeStateEvent, GazeDecision]:
        """Analyse one live frame, returning ``(state event, frame decision)``.

        The event is the smoothed state as of ``t_ms`` and is returned on every
        frame (doc 6-2) so a UI can redraw continuously; only the ones for which
        :attr:`last_event_emitted` is True belong on the wire, and those are the
        ones collected in :attr:`events`.
        """
        self._assert_open()
        if self._classifier is None:
            raise RuntimeError(
                "VisionSession.process_frame() before calibration: collect "
                "CAMERA and BOTTOM frames with add_calibration_frame() and call "
                "finish_calibration() first (doc 5-1)"
            )

        started = time.perf_counter()
        obs = self._observe(bgr, t_ms)
        self._blink.apply(obs)
        gaze = self._estimate_gaze(obs)
        condition = self._monitor.update(obs) if self._monitor is not None else None
        decision = self._classifier.decide(obs, gaze)
        if condition is not None and condition.severe:
            # The calibrated person is not the one being measured (gone, or
            # replaced): report "could not tell", never someone else's gaze.
            decision = replace(
                decision, label=GazeState.UNCERTAIN.value, face_valid=False,
                uncertain_reason=condition.issues[0], direction=None,
            )
        self._feed_reanchor(obs, gaze)
        self._evidence.record(decision, condition)
        decision.latency_ms = (time.perf_counter() - started) * 1000.0

        event = self.smoother.update(decision)
        emitted = self.smoother.should_emit(event)
        if emitted:
            self._events.append(event)
        if condition is not None:
            self._last_condition = condition
            if self._monitor.should_emit(condition):
                self._condition_events.append(condition.to_event())

        self._latencies.append(decision.latency_ms)
        self._last_decision = decision
        self._last_event_emitted = bool(emitted)
        return event, decision

    # -- set-up check (before calibration) ----------------------------------
    def check_preconditions(self, bgr: np.ndarray, t_ms: int) -> PreconditionReport:
        """Analyse a preview frame against the set-up the model assumes.

        Reports PASS / RETRY / REJECT (see ``runtime.preconditions``); acting on
        a REJECT is the caller's decision (``runtime.policy``).  Touches neither
        the calibration buffers nor the smoother.
        """
        self._assert_open()
        obs = self._observe(bgr, t_ms)
        return self._preconditions.update(obs)

    @property
    def precondition_report(self) -> Optional[PreconditionReport]:
        return self._preconditions.last_report

    def reset_preconditions(self) -> None:
        self._preconditions.reset()

    # -- head circle check (between the set-up check and calibration) --------
    def start_head_sweep(self, t_ms: int) -> SweepStatus:
        """Begin the ring: the next frames set its centre, then the head goes round."""
        self._assert_open()
        self._neutral_frames = []
        return self._sweep.start(t_ms)

    def offer_sweep_frame(self, bgr: np.ndarray, t_ms: int) -> SweepStatus:
        """Analyse a frame for the ring (see ``runtime.sweep``).

        Like the set-up check it touches neither the calibration buffers nor the
        smoother, and it never blocks: what to do with a ring that did not fill
        is the caller's decision.
        """
        self._assert_open()
        obs = self._observe(bgr, t_ms)
        centering = self._sweep.status.state == CENTERING
        status = self._sweep.offer(obs, t_ms)
        if centering and status.last_reason is None:
            # One of the frames that make the circle's centre: keep it for the calibration.
            self._neutral_frames.append((obs, self._estimate_gaze(obs)))
        return status

    @property
    def head_sweep(self) -> SweepStatus:
        return self._sweep.status

    # -- live condition -------------------------------------------------------
    @property
    def last_condition(self) -> Optional[ConditionState]:
        """Measurement condition of the latest live frame (``None`` before one)."""
        return self._last_condition

    @property
    def gaze_evidence(self) -> GazeEvidenceRecorder:
        """Gaze slices and their readings for the coach / review agents.

        ``gaze_evidence.issues()`` -- issues now, in the agents' evaluator format;
        ``gaze_evidence.summary()`` -- the take's statistics and problem segments;
        ``gaze_evidence.timeline.samples`` -- the 1 s records themselves.
        """
        return self._evidence

    @property
    def condition_events(self) -> List[SessionConditionEvent]:
        """SESSION_CONDITION events that passed ``should_emit`` (changes and heartbeats)."""
        return list(self._condition_events)

    def dump_condition_events(self, path: Union[str, Path], *, debug: bool = False) -> Path:
        """Write the emitted SESSION_CONDITION events as JSONL."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for event in self._condition_events:
                payload = event.to_debug_dict() if debug else event.to_dict()
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return target

    # -- re-anchor ------------------------------------------------------------
    @property
    def reanchor_status(self) -> ReanchorStatus:
        return replace(self._reanchor)

    def begin_reanchor(self, t_ms: int) -> ReanchorStatus:
        """Start a live offset update: the user looks at the lens for a moment.

        Over the next ``reanchor_frames`` usable frames the median gaze is
        taken as the new CAMERA anchor, and every anchor moves by the same
        offset (the regions keep their shape).  A shift larger than
        ``reanchor_max_shift_deg`` is refused -- the user was most likely not
        looking at the lens.  The condition monitor's reference scene and the
        head-away reference move to the current posture with it.  Only the
        reference-anchor classifier supports this.
        """
        self._assert_open()
        if self._classifier is None:
            raise RuntimeError("begin_reanchor() before calibration")
        target = int(self.cfg.calibration.reanchor_frames)
        if not hasattr(self._classifier, "with_offset"):
            self._reanchor = ReanchorStatus(state="UNSUPPORTED", target=target, reason="METHOD")
            return replace(self._reanchor)
        self._reanchor = ReanchorStatus(state="COLLECTING", target=target)
        self._reanchor_started_ms = int(t_ms)
        self._reanchor_obs = []
        self._reanchor_gaze = []
        return replace(self._reanchor)

    def _feed_reanchor(self, obs: FrameObservation, gaze: Optional[GazeVector]) -> None:
        status = self._reanchor
        if status.state != "COLLECTING":
            return
        cfg = self.cfg.calibration
        if int(obs.t_ms) - self._reanchor_started_ms > int(cfg.reanchor_timeout_ms):
            self._reanchor = replace(status, state="TIMED_OUT", reason="NOT_ENOUGH_FRAMES")
            return
        if not (obs.face_valid and _finite(gaze) and gaze.confidence >= float(cfg.min_sample_confidence)):
            return
        self._reanchor_obs.append(obs)
        self._reanchor_gaze.append((math.degrees(gaze.gaze_yaw), math.degrees(gaze.gaze_pitch)))
        collected = len(self._reanchor_gaze)
        if collected < status.target:
            self._reanchor = replace(status, collected=collected)
            return

        median = np.median(np.asarray(self._reanchor_gaze, dtype=np.float64), axis=0)
        anchor = self._classifier.model.anchors[GazeLabel.CAMERA.value]
        shift = (float(median[0] - anchor[0]), float(median[1] - anchor[1]))
        if max(abs(shift[0]), abs(shift[1])) > float(cfg.reanchor_max_shift_deg):
            self._reanchor = replace(status, state="REJECTED", collected=collected, shift_deg=shift,
                                     reason="SHIFT_TOO_LARGE")
            return
        heads = np.asarray(
            [(o.head_pose.yaw, o.head_pose.pitch) for o in self._reanchor_obs], dtype=np.float64
        )
        head = np.median(heads, axis=0)
        self._classifier = self._classifier.with_offset(*shift).with_head_baseline(
            HeadPose(yaw=float(head[0]), pitch=float(head[1]))
        )
        baseline = baseline_from_observations(self._reanchor_obs)
        if self._monitor is not None and baseline is not None:
            # The re-anchor frames are all "lens" frames: the other calibrated
            # postures (script, screen) move with the lens one.
            old = self._monitor.baseline
            baseline = replace(baseline, head_poses=old.shifted_head_poses(baseline.head))
            self._monitor.rebaseline(baseline)
        self._reanchor = replace(status, state="DONE", collected=collected, shift_deg=shift)

    def warmup(self, iterations: int = 2) -> None:
        """Pay the backbone's lazy init before the first timed frame (doc 3-2).

        Optional for a live take -- the calibration frames already warm every
        stage, and they are not latency-measured -- but an offline run that
        starts straight at :meth:`process_frame` would otherwise put a cold
        first inference into the doc 7 p95.
        """
        self._assert_open()
        self.backbone.warmup(iterations)

    # -- output -----------------------------------------------------------
    def dump_events(self, path: Union[str, Path], *, debug: bool = False) -> Path:
        """Write the emitted events as JSONL (doc 6-2 shape, one event per line).

        ``debug=True`` adds the smoothed probabilities and the transition flag,
        which are explicitly not part of the contract -- use it for a failure
        case attached to an experiment record (doc 18), not for a consumer.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for event in self._events:
                payload = event.to_debug_dict() if debug else event.to_dict()
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return target

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        """Release the pipeline and backbone this session created.  Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._close_owned()

    def _close_owned(self) -> None:
        """Release what this session built, tolerating a half-built session.

        ``__init__`` calls this on a failed build, when ``self.backbone`` may
        not exist yet, so both attributes are read defensively rather than
        assumed.  Injected collaborators are never touched: doc 23's Experiment
        1 runs several sessions over one landmarker graph.
        """
        if self._owns_backbone and getattr(self, "backbone", None) is not None:
            self.backbone.close()
        if self._owns_pipeline and getattr(self, "pipeline", None) is not None:
            self.pipeline.close()

    def __enter__(self) -> "VisionSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:
        counts = self.calibration_counts
        return (
            f"VisionSession(backbone={self.backbone.name!r}, "
            f"calibrated={self.is_calibrated}, "
            f"samples={counts[GazeLabel.CAMERA.value]}C/{counts[GazeLabel.BOTTOM.value]}B"
            f"/{counts[GazeLabel.SCREEN.value]}S, "
            f"state={self.smoother.state})"
        )

    # -- internals --------------------------------------------------------
    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("VisionSession is closed")

    def _observe(self, bgr: np.ndarray, t_ms: int) -> FrameObservation:
        """Preprocess one BGR frame under the next session-wide frame id.

        Calibration and live frames share one id space so that anything joined
        on ``frame_id`` later -- manifest rows, dumped events, a debug video --
        refers to exactly one frame of this take.
        """
        frame_id = self._frame_id
        self._frame_id += 1
        obs = self.pipeline.process_bgr(bgr, frame_id, int(t_ms), main_face_hint=self._face_hint)
        if not self.eye_based and obs.invalid_reason in _EYE_ONLY_REASONS:
            # The head-pose backbone never reads the eyes: lids lowered to read
            # the script, a blink, or eyes too small or turned away to crop
            # leave the head direction it measures intact.
            obs.face_valid = True
            obs.invalid_reason = None
        self._last_observation = obs
        # Follow the face we are measuring: the next frame analyses the face
        # nearest this one, so a second person cannot take over by being bigger.
        centre = face_centre(obs) if obs.invalid_reason != InvalidReason.NO_FACE.value else None
        if centre is not None:
            self._face_hint = centre
        return obs

    def _estimate_gaze(self, obs: FrameObservation) -> Optional[GazeVector]:
        """Run the backbone on a usable frame; ``None`` when there is no estimate.

        An invalid frame is not offered to the backbone at all: doc 3-1 already
        decided the crops or landmarks are not trustworthy, and a gaze computed
        from them would be indistinguishable downstream from a real one.
        """
        self._last_gaze = None
        if not obs.face_valid:
            return None
        try:
            gaze = self.backbone.predict_observation(obs)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            self.last_backbone_error = f"{type(exc).__name__}: {exc}"
            return None
        if not _finite(gaze):
            # A backbone that returns NaN is just as dead as one that raises,
            # and every consumer of the estimate already drops it -- but only
            # the raising branch used to leave a trace, so an all-abstaining run
            # from a NaN-returning backbone read exactly like a user who left
            # the room (module docstring).  Record it and still hand the vector
            # back unchanged: the rejection itself belongs to the callers, which
            # each bucket it under their own doc 5-4 reason.
            self.last_backbone_error = (
                f"non-finite gaze from backbone {self.backbone.name!r}: {gaze!r}"
            )
        self._last_gaze = gaze
        return self._last_gaze

    def _reject_reason(self, obs: FrameObservation, gaze: Optional[GazeVector]) -> str:
        """Why a calibration frame was dropped, in doc 3-1 / doc 5-4 vocabulary."""
        if obs.invalid_reason:
            return str(obs.invalid_reason)
        return InvalidReason.BACKBONE_FAILED.value


def _cue_gaze_deg(samples: Sequence[CalibrationSample], cue: str) -> np.ndarray:
    """Finite ``(yaw_deg, pitch_deg)`` rows of one cue's samples."""
    rows = [
        (math.degrees(s.gaze.gaze_yaw), math.degrees(s.gaze.gaze_pitch))
        for s in samples
        if s.label == cue and _finite(s.gaze)
    ]
    return np.asarray(rows, dtype=np.float64).reshape(-1, 2)


def collect_calibration(
    session: VisionSession,
    frames: Sequence[Tuple[np.ndarray, str, int]],
) -> CalibrationQuality:
    """Feed a whole cue timeline through a session and finish (doc 5-1).

    Convenience for offline replay and tests, where the frames already exist as
    a list; a live take drives :meth:`VisionSession.add_calibration_frame` one
    frame at a time because it has to draw the cue between them.
    """
    for bgr, cue, t_ms in frames:
        session.add_calibration_frame(bgr, cue, t_ms)
    return session.finish_calibration()
