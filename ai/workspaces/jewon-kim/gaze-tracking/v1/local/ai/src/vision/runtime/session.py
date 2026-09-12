"""One take, wired end to end: frames in, GAZE_STATE out (doc 3-1 -> doc 6).

``VisionSession`` owns the four stages that only make sense together -- the
preprocess pipeline (doc 3-1), the gaze backbone (doc 3-2), the per-user
classifier fitted from this take's own two-point calibration (doc 5) and the
temporal smoother (doc 6) -- plus the two pieces of state none of them can hold
alone: the session-wide frame counter and the calibration sample buffer.

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
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from vision.backbones.base import GazeBackbone
from vision.backbones.registry import build_backbone
from vision.calibration.classifier import PerUserGazeClassifier
from vision.config import VisionConfig
from vision.preprocess.pipeline import PreprocessPipeline
from vision.runtime.placement import (
    TARGET_CAMERA,
    TARGET_SCREEN,
    CameraPlacementCheck,
    PlacementCheckResult,
)
from vision.runtime.version import MODEL_VERSION, current_ai_version
from vision.schemas import (
    AiVersion,
    CalibrationQuality,
    CalibrationSample,
    FrameObservation,
    GazeDecision,
    GazeLabel,
    GazeStateEvent,
    GazeVector,
    InvalidReason,
)
from vision.temporal.smoother import TemporalSmoother

#: How many recent end-to-end latencies to keep for :meth:`latency_percentile`.
#: At ``analysis_fps=8`` this is the last ~2 minutes, which is the window a live
#: overlay should report; the full history belongs in the feature table, not in
#: a process that runs for an hour.
_LATENCY_HISTORY = 1024


def _finite(gaze: Optional[GazeVector]) -> bool:
    return (
        gaze is not None
        and math.isfinite(gaze.gaze_yaw)
        and math.isfinite(gaze.gaze_pitch)
    )


def _calibration_label(cue: Union[str, GazeLabel]) -> str:
    """Canonical cue label, rejecting anything the classifier cannot train on.

    IGNORE is a dataset label (doc 4-2 guard bands), never a calibration cue; a
    caller that passes it has a bug in its cue timeline, and dropping the frame
    silently would surface later as NOT_ENOUGH_SAMPLES with no explanation.
    """
    label = GazeLabel.coerce(cue)
    if label is GazeLabel.IGNORE:
        raise ValueError(
            f"calibration cue must be {GazeLabel.CAMERA.value} or "
            f"{GazeLabel.BOTTOM.value}, got {cue!r}"
        )
    return label.value


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
        self._classifier: Optional[PerUserGazeClassifier] = None
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

    # -- introspection ----------------------------------------------------
    @property
    def is_calibrated(self) -> bool:
        """True once a model exists that can score a frame (see module docstring)."""
        return self._classifier is not None

    @property
    def classifier(self) -> Optional[PerUserGazeClassifier]:
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
        """Accepted samples per cue -- what a progress bar counts down (doc 5-1)."""
        counts = Counter(s.label for s in self._samples)
        return {
            GazeLabel.CAMERA.value: int(counts.get(GazeLabel.CAMERA.value, 0)),
            GazeLabel.BOTTOM.value: int(counts.get(GazeLabel.BOTTOM.value, 0)),
        }

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
        return True

    def finish_calibration(self) -> CalibrationQuality:
        """Fit the per-user model and report doc 5-2 quality.

        Always returns a report, including for a set too small to train on
        (``NOT_ENOUGH_SAMPLES``), in which case no model is kept and
        :attr:`is_calibrated` stays False.  The smoother is reset either way:
        evidence gathered while the user was staring at a calibration cue must
        not leak into the first live state.
        """
        self._assert_open()
        classifier, quality = PerUserGazeClassifier.fit(self._samples, self.cfg.calibration)
        self._quality = quality
        self._classifier = classifier if classifier.is_fitted else None
        self.smoother.reset()
        return quality

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
        self.smoother.reset()

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
        gaze = self._estimate_gaze(obs)
        decision = self._classifier.decide(obs, gaze)
        decision.latency_ms = (time.perf_counter() - started) * 1000.0

        event = self.smoother.update(decision)
        emitted = self.smoother.should_emit(event)
        if emitted:
            self._events.append(event)

        self._latencies.append(decision.latency_ms)
        self._last_decision = decision
        self._last_event_emitted = bool(emitted)
        return event, decision

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
            f"samples={counts[GazeLabel.CAMERA.value]}C/{counts[GazeLabel.BOTTOM.value]}B, "
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
        obs = self.pipeline.process_bgr(bgr, frame_id, int(t_ms))
        self._last_observation = obs
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
