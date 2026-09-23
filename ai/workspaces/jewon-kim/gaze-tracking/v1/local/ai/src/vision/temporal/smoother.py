"""Temporal smoothing of per-frame gaze decisions (doc 6).

The classifier (doc 5-4) decides one frame at a time and therefore flickers: a
blink, a single dark frame or a 51/49 probability is enough to flip its label.
Downstream consumers want a *state* ("looking at the script since 1.4 s"), not
a label stream, so this module turns decisions into ``GazeStateEvent``s through
three stages:

1. **EMA** over ``p_bottom`` (``ema_alpha``) removes per-frame jitter.  Its
   memory is exponential and unbounded, which is what makes it cheap.
2. **Recency-weighted vote** over the last ``window_frames`` values of
   ``p_bottom``, with linear weights running from ``1.0`` (oldest) to
   ``vote_recency_weight`` (newest).  This bounds how far back a stale
   observation can reach - after ``window_frames`` frames an old spike is gone
   entirely, which the EMA alone never guarantees.
3. **Asymmetric hysteresis** turns the score into a state: it must sit above
   the entry threshold for ``to_bottom_dwell_ms`` / ``to_camera_dwell_ms``
   before the state commits.  Entering BOTTOM is deliberately more expensive
   than leaving it, because a wrongly reported script-glance is worse feedback
   than a missed one.

The smoothed BOTTOM score is the mean of stages 1 and 2, not a cascade of one
into the other.  Both readings honour doc 6, but they cost very different
latency, and at ``analysis_fps=8`` both smoothers are frame-based while the
dwell is not, so the smoothing lag is the term that dominates.  Measured on a
clean step with the shipped config (threshold 0.60, 125 ms frames):

===========================  =========  =========  =========================
composition                  ->BOTTOM   ->CAMERA   peak score, 1/2/3/4-frame
                                                   spike (threshold 0.60)
===========================  =========  =========  =========================
vote over EMA (cascade)      1375 ms    1250 ms    0.13 / 0.25 / 0.36 / 0.47
mean(EMA, vote)  <- shipped  1000 ms     875 ms    0.27 / 0.46 / 0.60 / 0.71
vote alone                   1125 ms    1000 ms    0.18 / 0.33 / 0.47 / 0.60
===========================  =========  =========  =========================

The blend buys 375 ms in both directions and still cannot be flipped by a
burst: a 4-frame spike only *arms* the transition, and the dwell then needs
another 600 ms of the same evidence, so a flip takes >= 1 s of sustained
BOTTOM either way.  A cue block in the collection protocol is 5 s
(``collection.yaml``), so 1 s of lag is already 20 % of a block - paying 1.4 s
for spike headroom nothing produces was the worse trade.

Timestamps
----------
Every duration is measured from ``GazeDecision.t_ms``.  Frame counts times a
nominal interval would be wrong: the sampler decimates to ``analysis_fps`` by
timestamp, cameras drop frames, and a slow backbone stalls the stream.
``continuous_duration_ms`` is therefore ``t_ms`` minus the timestamp of the
frame that committed the current state.

UNCERTAIN
---------
An UNCERTAIN decision - a lost face, or a low-confidence / low-margin call on a
perfectly good face (doc 5-4) - is an *abstention*: it never votes for either
class.  With ``decay_on_invalid`` it pushes the EMA toward 0.5 and enters the
window as a neutral 0.5 sample, so evidence goes stale instead of freezing;
without it both smoothers are frozen and the pre-blackout state is simply held.
Abstentions never arm a transition, because they only move the score toward
0.5.  They do not reset an armed one either - dropped frames are normal and
must not restart a dwell - so a transition armed before a dropout can commit
*on* an abstaining frame, on the strength of the evidence that preceded it;
that event then carries ``face_valid=False`` like any other.  After
``uncertain_dwell_ms`` of *continuous* abstention the state itself becomes
UNCERTAIN, and every UNCERTAIN event reports ``face_valid=False`` whichever
doc 5-4 branch produced it.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Optional, Tuple

from vision.config import TemporalConfig
from vision.schemas import GazeDecision, GazeState, GazeStateEvent

#: Identifies this rule set in ``AiVersion.temporal_rule`` (doc 15).
TEMPORAL_RULE_VERSION = "gaze_temporal_v1.0"

_CAMERA = GazeState.CAMERA.value
_BOTTOM = GazeState.BOTTOM.value
_UNCERTAIN = GazeState.UNCERTAIN.value

#: Score an abstaining frame contributes: exactly no evidence for either class.
_NEUTRAL = 0.5


def _elapsed(now_ms: int, since_ms: Optional[int]) -> int:
    """Non-negative elapsed time, tolerant of a replayed or reordered stream."""
    if since_ms is None:
        return 0
    return max(0, int(now_ms) - int(since_ms))


def _recency_weights(window_frames: int, recency: float) -> List[float]:
    """Weights indexed by lag: ``[newest, ..., oldest]``.

    Indexing by lag rather than by deque slot keeps a frame's weight constant
    while the window is still filling, so the first second of a session behaves
    like the steady state instead of drifting as the deque grows.
    """
    if window_frames <= 1:
        return [float(recency)]
    step = (float(recency) - 1.0) / (window_frames - 1)
    return [float(recency) - step * i for i in range(window_frames)]


def _decision_label(decision: GazeDecision) -> str:
    """Canonical upper-case label string.

    ``GazeState`` is a ``str`` Enum, so ``==`` already works on members, but
    ``str()`` of a mixed-in Enum returns ``"GazeState.UNCERTAIN"`` on 3.11;
    take ``.value`` when it is there and normalise case for labels that were
    round-tripped through a CSV.
    """
    raw = getattr(decision.label, "value", decision.label)
    return str(raw).strip().upper()


def _normalised_p_bottom(decision: GazeDecision) -> float:
    """``p_bottom`` renormalised over the two decision classes.

    The LR head already sums to 1, but decisions also arrive rehydrated from
    feature tables (doc 20), where rounding or a hand-written fixture can break
    that; renormalising costs nothing and keeps the score a probability.

    Every guard is stated POSITIVELY -- "both inputs are real numbers", "the
    total is usable", "the ratio came out a real number" -- so anything we
    cannot read falls through to the neutral 0.5, which is exactly the evidence
    an unusable frame carries.

    Three things this must not be simplified back into:

    * ``if total <= 1e-9``. That is False for a NaN total, so a corrupt row
      reached the clamp, ``min(1.0, max(0.0, nan))`` returned 0.0, and the frame
      voted as *maximal CAMERA evidence* -- a confident class conjured out of a
      broken number.
    * Checking only the ratio. ``p_camera=inf`` with a finite ``p_bottom``
      divides to a perfectly finite 0.0, which passes a ratio-only test and
      lands as that same maximal CAMERA vote. The asymmetry is the trap: the
      mirrored ``p_bottom=inf`` gives ``inf/inf`` -> NaN and *is* caught, so the
      bug only ever shows up on one side.
    * Dropping the ratio check now that the inputs are screened. Two finite
      inputs can still divide to a non-finite ratio under subnormals, and the
      check costs nothing.
    """
    p_bottom = float(decision.p_bottom)
    p_camera = float(decision.p_camera)
    if not (math.isfinite(p_bottom) and math.isfinite(p_camera)):
        return _NEUTRAL
    total = p_bottom + p_camera
    if not (total > 1e-9):
        return _NEUTRAL
    ratio = p_bottom / total
    if not math.isfinite(ratio):
        return _NEUTRAL
    return min(1.0, max(0.0, ratio))


def _validate(cfg: TemporalConfig) -> None:
    """Fail at construction, not three hundred frames into a take."""
    if int(cfg.window_frames) < 1:
        raise ValueError(f"window_frames must be >= 1, got {cfg.window_frames}")
    if not 0.0 < float(cfg.ema_alpha) <= 1.0:
        raise ValueError(f"ema_alpha must be in (0, 1], got {cfg.ema_alpha}")
    if float(cfg.vote_recency_weight) <= 0.0:
        raise ValueError(f"vote_recency_weight must be > 0, got {cfg.vote_recency_weight}")
    for name in ("enter_bottom_threshold", "enter_camera_threshold"):
        value = float(getattr(cfg, name))
        if not 0.0 < value <= 1.0:
            raise ValueError(f"{name} must be in (0, 1], got {value}")
    for name in ("to_bottom_dwell_ms", "to_camera_dwell_ms", "uncertain_dwell_ms", "heartbeat_ms"):
        if int(getattr(cfg, name)) < 0:
            raise ValueError(f"{name} must be >= 0, got {getattr(cfg, name)}")


class TemporalSmoother:
    """Per-frame decision stream -> GAZE_STATE stream (doc 6).

    One instance owns one take.  It is not thread-safe and expects timestamps
    in order; ``reset`` returns it to the just-constructed state.
    """

    def __init__(self, cfg: TemporalConfig, model_version: str = "gaze_v1.0.0") -> None:
        _validate(cfg)
        self.cfg = cfg
        self.model_version = model_version
        self._weights = _recency_weights(int(cfg.window_frames), float(cfg.vote_recency_weight))
        # Snapshotted right next to the weights it has to agree with: the vote
        # reads ``_weights[lag]`` for every slot the window holds, so the two
        # lengths are one decision, not two.  ``reset`` used to re-read
        # ``cfg.window_frames``, which let a config mutated after construction
        # hand back a deque longer than the weight table -- an IndexError
        # several hundred frames into a take.  Deliberately the *only* field
        # snapshotted: the rest are read per frame so a live tweak of a
        # threshold or a dwell still takes effect.
        self._window_frames = int(cfg.window_frames)
        self.reset()

    # -- state ------------------------------------------------------------
    def reset(self) -> None:
        """Forget the stream.  Starts UNCERTAIN: no frame seen, no evidence."""
        self._state: str = _UNCERTAIN
        self._ema: float = _NEUTRAL
        self._vote: float = _NEUTRAL
        self._score_bottom: float = _NEUTRAL
        self._window: Deque[float] = deque(maxlen=self._window_frames)
        self._state_entry_ms: Optional[int] = None
        self._pending: Optional[str] = None
        self._pending_since_ms: Optional[int] = None
        self._uncertain_since_ms: Optional[int] = None
        self._last_emit_ms: Optional[int] = None
        self._last_emit_key: Optional[Tuple[int, str, bool]] = None
        self._last_event: Optional[GazeStateEvent] = None

    @property
    def state(self) -> str:
        """Current committed state, one of ``GazeState``."""
        return self._state

    @property
    def smoothed_p_bottom(self) -> float:
        """The score the hysteresis actually tests: ``mean(ema, vote)``."""
        return self._score_bottom

    @property
    def smoothed_p_camera(self) -> float:
        return 1.0 - self._score_bottom

    @property
    def ema_p_bottom(self) -> float:
        """Stage-1 output; exposed for the debug overlay and for tests."""
        return self._ema

    @property
    def vote_p_bottom(self) -> float:
        """Stage-2 output; exposed for the debug overlay and for tests."""
        return self._vote

    @property
    def pending_state(self) -> Optional[str]:
        """State whose dwell is currently running, or ``None`` when disarmed."""
        return self._pending

    @property
    def last_event(self) -> Optional[GazeStateEvent]:
        return self._last_event

    # -- main loop --------------------------------------------------------
    def update(self, decision: GazeDecision) -> GazeStateEvent:
        """Fold one decision in and return the state as of ``decision.t_ms``.

        Always returns an event (doc 6-2); ``should_emit`` decides what goes on
        the wire, so a UI can render every frame while the transport only sees
        transitions and heartbeats.
        """
        t_ms = int(decision.t_ms)
        if self._state_entry_ms is None:
            self._state_entry_ms = t_ms

        # UNCERTAIN by label *or* by a dead face: doc 5-4 lets a frame be
        # UNCERTAIN with a good face (LOW_MARGIN), and a frame with
        # face_valid=False can never carry a trustworthy probability.
        abstains = _decision_label(decision) == _UNCERTAIN or not decision.face_valid
        if abstains:
            if self.cfg.decay_on_invalid:
                self._ema += float(self.cfg.ema_alpha) * (_NEUTRAL - self._ema)
                self._window.append(_NEUTRAL)
            if self._uncertain_since_ms is None:
                self._uncertain_since_ms = t_ms
        else:
            p_bottom = _normalised_p_bottom(decision)
            self._ema += float(self.cfg.ema_alpha) * (p_bottom - self._ema)
            self._window.append(p_bottom)
            self._uncertain_since_ms = None

        self._vote = self._weighted_vote()
        self._score_bottom = 0.5 * (self._ema + self._vote)
        transitioned = self._advance_state(t_ms, abstains)

        event = self._build_event(t_ms, decision, transitioned)
        self._last_event = event
        return event

    def should_emit(self, event: GazeStateEvent) -> bool:
        """True when ``event`` must go on the wire: transition or heartbeat.

        Stateful - it records what it released, so the heartbeat clock restarts
        on every emitted event, transitions included.  Re-asking about the same
        event returns the same answer, so a caller may branch on it twice.
        """
        key = (int(event.t_ms), event.label, bool(event.is_transition))
        if key == self._last_emit_key:
            return True
        due = (
            bool(event.is_transition)
            or self._last_emit_ms is None
            or _elapsed(event.t_ms, self._last_emit_ms) >= int(self.cfg.heartbeat_ms)
        )
        if due:
            self._last_emit_ms = int(event.t_ms)
            self._last_emit_key = key
        return due

    # -- internals --------------------------------------------------------
    def _weighted_vote(self) -> float:
        """Recency-weighted mean of the window; the EMA covers the empty case.

        The window is empty only before the first frame, or while
        ``decay_on_invalid`` is off and the stream opened with abstentions;
        falling back to the EMA keeps the blend a plain average of two defined
        quantities instead of a special case.
        """
        if not self._window:
            return self._ema
        total = 0.0
        weight_sum = 0.0
        for lag, value in enumerate(reversed(self._window)):
            weight = self._weights[lag]
            total += weight * value
            weight_sum += weight
        if weight_sum <= 1e-12:
            return self._ema
        return total / weight_sum

    def _advance_state(self, t_ms: int, abstains: bool) -> bool:
        """Run the hysteresis; True when the state actually flipped."""
        # A long enough abstention run outranks the score: whatever the stale
        # evidence still claims, we are no longer observing the person.
        if abstains and _elapsed(t_ms, self._uncertain_since_ms) >= int(self.cfg.uncertain_dwell_ms):
            self._disarm()
            if self._state != _UNCERTAIN:
                self._commit(_UNCERTAIN, t_ms)
                return True
            return False

        candidate = self._candidate()
        if candidate is None or candidate == self._state:
            self._disarm()
            return False

        # Arm on the first frame that crossed, so the dwell is measured from an
        # observation we actually saw (arming one frame late is conservative).
        if candidate != self._pending:
            self._pending = candidate
            self._pending_since_ms = t_ms

        dwell = (
            int(self.cfg.to_bottom_dwell_ms)
            if candidate == _BOTTOM
            else int(self.cfg.to_camera_dwell_ms)
        )
        if _elapsed(t_ms, self._pending_since_ms) >= dwell:
            self._commit(candidate, t_ms)
            self._disarm()
            return True
        return False

    def _candidate(self) -> Optional[str]:
        """Which state the smoothed score argues for, if any.

        BOTTOM is tested first: with thresholds below 0.5 both tests can pass,
        and doc 7 gates on BOTTOM recall, so the tie goes to the class whose
        misses cost more.
        """
        if self._score_bottom >= float(self.cfg.enter_bottom_threshold):
            return _BOTTOM
        if (1.0 - self._score_bottom) >= float(self.cfg.enter_camera_threshold):
            return _CAMERA
        return None

    def _commit(self, state: str, t_ms: int) -> None:
        self._state = state
        self._state_entry_ms = t_ms

    def _disarm(self) -> None:
        self._pending = None
        self._pending_since_ms = None

    def _build_event(self, t_ms: int, decision: GazeDecision, transitioned: bool) -> GazeStateEvent:
        score_bottom = self._score_bottom
        score_camera = 1.0 - score_bottom
        if self._state == _BOTTOM:
            confidence = score_bottom
        elif self._state == _CAMERA:
            confidence = score_camera
        else:
            # While UNCERTAIN the leading score says *why*: ~0.5 means the
            # evidence is ambiguous or stale, a high value means a transition
            # is armed and only the dwell is still missing.
            confidence = max(score_bottom, score_camera)
        return GazeStateEvent(
            t_ms=t_ms,
            label=self._state,
            confidence=float(confidence),
            continuous_duration_ms=_elapsed(t_ms, self._state_entry_ms),
            # doc 6: the UNCERTAIN state is by definition not backed by a
            # usable observation, whichever branch of doc 5-4 produced it.
            face_valid=bool(decision.face_valid) and self._state != _UNCERTAIN,
            model_version=self.model_version,
            is_transition=transitioned,
            smoothed_p_camera=float(score_camera),
            smoothed_p_bottom=float(score_bottom),
        )
