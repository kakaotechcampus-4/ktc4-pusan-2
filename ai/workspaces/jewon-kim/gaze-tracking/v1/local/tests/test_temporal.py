"""Contract tests for the doc 6 temporal smoother.

``vision.temporal.smoother`` turns a flickering per-frame ``GazeDecision``
stream into a ``GazeStateEvent`` state machine.  Everything downstream -- the
runtime session, the offline evaluator, the demo overlay -- leans on properties
that are invisible from any single frame:

* the score is ``mean(EMA, recency-weighted vote)`` over *renormalised*
  ``p_bottom``, and the vote weights are indexed by LAG, so a frame's weight
  stays constant while the window is still filling;
* a transition is *armed* by the score and *committed* by a dwell, and the two
  dwells are deliberately asymmetric (entering BOTTOM costs more than leaving
  it), so a burst of BOTTOM frames cannot flip the state on its own;
* the two "nothing happened" cases differ.  A score that falls back into the
  dead band disarms a running dwell; an abstention (lost face, or a low-margin
  call on a perfectly good face) does not -- a transition armed before a
  dropout still commits, *on* an abstaining frame, carrying ``face_valid=False``;
* only *continuous* abstention reaches the UNCERTAIN state, and that branch
  outranks whatever the (possibly frozen) score still claims; ambiguity alone
  holds the last committed state instead;
* every UNCERTAIN event reports ``face_valid=False`` whatever the face was
  doing, and ``continuous_duration_ms`` restarts at the committing frame and
  never goes negative on a reordered stream;
* ``should_emit`` is idempotent for one event but stateful across events, and a
  transition restarts the heartbeat clock.

Timelines here are explicit and hand-built: a state machine is exactly the thing
you test by handing it a known sequence on a known clock.  No accuracy or gate
number is measured off these decisions -- the probabilities are inputs to a code
path, not a stand-in dataset.
"""

from __future__ import annotations

import dataclasses
import math
from typing import List, Optional

import pytest

from vision.config import TemporalConfig
from vision.schemas import AiVersion, GazeDecision, GazeState, GazeStateEvent
from vision.temporal.smoother import (
    TEMPORAL_RULE_VERSION,
    TemporalSmoother,
    _elapsed,
    _normalised_p_bottom,
    _recency_weights,
)

CAMERA = GazeState.CAMERA.value
BOTTOM = GazeState.BOTTOM.value
UNCERTAIN = GazeState.UNCERTAIN.value

#: ai/configs/preprocess.yaml ships analysis_fps = 8, i.e. one frame per 125 ms.
#: Dwells are in milliseconds, so every commit lands on this grid.
FRAME_MS = 125


# --------------------------------------------------------------------------
# Timeline helpers
# --------------------------------------------------------------------------


def decision(
    t_ms: int,
    p_bottom: float,
    *,
    face_valid: bool = True,
    label: Optional[str] = None,
) -> GazeDecision:
    """A valid decision voting ``p_bottom`` for the BOTTOM class."""
    if label is None:
        label = BOTTOM if p_bottom >= 0.5 else CAMERA
    return GazeDecision(
        t_ms=t_ms,
        frame_id=t_ms // FRAME_MS,
        label=label,
        p_camera=1.0 - p_bottom,
        p_bottom=p_bottom,
        face_valid=face_valid,
    )


def abstention(t_ms: int, *, face_valid: bool = False, label=UNCERTAIN) -> GazeDecision:
    """A frame that votes for nothing: lost face, or a low-margin good face."""
    return GazeDecision(
        t_ms=t_ms,
        frame_id=t_ms // FRAME_MS,
        label=label,
        p_camera=0.5,
        p_bottom=0.5,
        face_valid=face_valid,
        uncertain_reason="NO_FACE" if not face_valid else "LOW_MARGIN",
    )


def steady(smoother: TemporalSmoother, p_bottom: float, start_ms: int, n_frames: int) -> int:
    """Feed ``n_frames`` identical valid decisions; return the next timestamp."""
    t_ms = start_ms
    for _ in range(n_frames):
        smoother.update(decision(t_ms, p_bottom))
        t_ms += FRAME_MS
    return t_ms


def settle(smoother: TemporalSmoother, p_bottom: float, start_ms: int = 0) -> int:
    """Drive the smoother to the committed steady state; return the next timestamp."""
    end_ms = steady(smoother, p_bottom, start_ms, 16)
    assert smoother.state == (BOTTOM if p_bottom >= 0.5 else CAMERA)
    return end_ms


def arm_bottom(smoother: TemporalSmoother) -> int:
    """Four confident BOTTOM frames: arms at t=0, dwell still running.

    Returns the timestamp of the next frame.
    """
    next_ms = steady(smoother, 1.0, 0, 4)  # t = 0, 125, 250, 375
    assert smoother.state == UNCERTAIN, "the dwell must not have expired yet"
    assert smoother.pending_state == BOTTOM, "four confident frames must arm BOTTOM"
    return next_ms


def transitions(events: List[GazeStateEvent]) -> List[GazeStateEvent]:
    return [event for event in events if event.is_transition]


def event_at(t_ms: int, *, label: str = CAMERA, transition: bool = False) -> GazeStateEvent:
    """A hand-built event, for exercising ``should_emit`` on its own."""
    return GazeStateEvent(
        t_ms=t_ms,
        label=label,
        confidence=0.9,
        continuous_duration_ms=0,
        face_valid=True,
        is_transition=transition,
    )


@pytest.fixture
def temporal(cfg) -> TemporalConfig:
    """The shipped ai/configs/temporal.yaml section (never mutated in place)."""
    return cfg.temporal


# --------------------------------------------------------------------------
# Construction, validation, reset
# --------------------------------------------------------------------------


def test_a_fresh_smoother_is_uncertain_with_no_evidence(temporal):
    smoother = TemporalSmoother(temporal)

    assert smoother.state == UNCERTAIN
    assert smoother.smoothed_p_bottom == 0.5
    assert smoother.smoothed_p_camera == 0.5
    assert smoother.ema_p_bottom == 0.5
    assert smoother.vote_p_bottom == 0.5
    assert smoother.pending_state is None
    assert smoother.last_event is None


def test_reset_returns_a_driven_smoother_to_the_constructed_state(temporal):
    smoother = TemporalSmoother(temporal)
    settle(smoother, 1.0)
    smoother.should_emit(smoother.last_event)

    smoother.reset()

    assert smoother.state == UNCERTAIN
    assert smoother.smoothed_p_bottom == 0.5
    assert smoother.ema_p_bottom == 0.5
    assert smoother.vote_p_bottom == 0.5
    assert smoother.pending_state is None
    assert smoother.last_event is None
    # The emit clock is part of "the just-constructed state": the first event
    # after a reset is due, exactly as it is on a brand new instance.
    assert smoother.should_emit(event_at(0)) is True


def test_reset_keeps_the_window_in_step_with_the_weights(temporal):
    """The vote indexes ``_weights[lag]``, so the two lengths must not drift.

    ``reset`` used to rebuild the deque from ``cfg.window_frames`` rather than
    from the value the weights were built from, so a config object mutated
    after construction -- a sweep reusing one config, a demo tweaking it live --
    gave a window longer than the weight table and blew up with an IndexError
    several hundred frames into a take, nowhere near the mutation.
    """
    cfg = dataclasses.replace(temporal, window_frames=4)
    smoother = TemporalSmoother(cfg)
    cfg.window_frames = 32  # the mutation reset() must not pick up

    smoother.reset()
    steady(smoother, 1.0, 0, 40)

    assert smoother.state == BOTTOM
    assert math.isfinite(smoother.vote_p_bottom)


@pytest.mark.parametrize(
    "field, value",
    [
        ("window_frames", 0),
        ("window_frames", -4),
        ("ema_alpha", 0.0),
        ("ema_alpha", -0.1),
        ("ema_alpha", 1.5),
        ("vote_recency_weight", 0.0),
        ("vote_recency_weight", -2.0),
        ("enter_bottom_threshold", 0.0),
        ("enter_bottom_threshold", 1.01),
        ("enter_camera_threshold", -0.1),
        ("enter_camera_threshold", 2.0),
        ("to_bottom_dwell_ms", -1),
        ("to_camera_dwell_ms", -1),
        ("uncertain_dwell_ms", -1),
        ("heartbeat_ms", -1),
    ],
)
def test_out_of_range_config_is_rejected_at_construction(temporal, field, value):
    bad = dataclasses.replace(temporal, **{field: value})

    with pytest.raises(ValueError, match=field):
        TemporalSmoother(bad)


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_frames": 1},
        {"ema_alpha": 1.0},
        {"vote_recency_weight": 1.0},
        {"enter_bottom_threshold": 1.0, "enter_camera_threshold": 1.0},
        {"to_bottom_dwell_ms": 0, "to_camera_dwell_ms": 0},
        {"uncertain_dwell_ms": 0, "heartbeat_ms": 0},
    ],
)
def test_boundary_config_values_are_accepted_and_run(temporal, overrides):
    smoother = TemporalSmoother(dataclasses.replace(temporal, **overrides))

    event = smoother.update(decision(0, 1.0))

    assert event.label in {CAMERA, BOTTOM, UNCERTAIN}


def test_rule_version_matches_the_stamp_written_into_every_take():
    # doc 15: AiVersion.temporal_rule names the rule set a take was produced by
    # and runtime.version copies this constant into it. Drift would stamp takes
    # with a rule that did not produce them.
    assert TEMPORAL_RULE_VERSION == AiVersion().temporal_rule


# --------------------------------------------------------------------------
# Scoring: renormalisation, EMA, recency-weighted vote
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now_ms, since_ms, expected",
    [
        (0, None, 0),
        (500, None, 0),
        (500, 100, 400),
        (100, 100, 0),
        (100, 500, 0),  # reordered stream: clamped, never negative
    ],
)
def test_elapsed_clamps_unset_and_reordered_timestamps_to_zero(now_ms, since_ms, expected):
    assert _elapsed(now_ms, since_ms) == expected


@pytest.mark.parametrize(
    "window_frames, recency", [(1, 2.0), (2, 2.0), (8, 2.0), (8, 1.0), (5, 3.5)]
)
def test_recency_weights_run_from_newest_to_oldest_indexed_by_lag(window_frames, recency):
    weights = _recency_weights(window_frames, recency)

    assert len(weights) == window_frames
    assert weights[0] == pytest.approx(recency)  # lag 0 == newest frame
    assert all(w > 0.0 for w in weights)
    assert all(a >= b for a, b in zip(weights, weights[1:]))
    if window_frames > 1:
        assert weights[-1] == pytest.approx(1.0)  # oldest carries the unit weight


def test_newest_frame_keeps_full_recency_weight_while_the_window_fills(temporal):
    weights = _recency_weights(int(temporal.window_frames), float(temporal.vote_recency_weight))
    assert weights[0] == max(weights)

    # Two frames in an eight-slot window. Indexed by lag the newest carries
    # weights[0]; indexed by deque slot it would carry weights[1] instead, so
    # this number is what distinguishes the two implementations.
    partial = TemporalSmoother(temporal)
    partial.update(decision(0, 0.0))
    partial.update(decision(FRAME_MS, 1.0))
    expected_partial = (weights[0] * 1.0 + weights[1] * 0.0) / (weights[0] + weights[1])
    assert partial.vote_p_bottom == pytest.approx(expected_partial)

    # Same newest-frame weight once the window is full, i.e. the weighting the
    # first second of a session sees is the steady-state weighting.
    full = TemporalSmoother(temporal)
    t_ms = steady(full, 0.0, 0, int(temporal.window_frames) - 1)
    full.update(decision(t_ms, 1.0))
    assert full.vote_p_bottom == pytest.approx(weights[0] / sum(weights))


def test_an_old_spike_leaves_the_window_after_window_frames(temporal):
    """The bound the vote buys over the EMA: memory is finite, not exponential."""
    smoother = TemporalSmoother(temporal)
    smoother.update(decision(0, 1.0))
    assert smoother.vote_p_bottom == pytest.approx(1.0)

    t_ms = steady(smoother, 0.0, FRAME_MS, int(temporal.window_frames))

    assert smoother.vote_p_bottom == pytest.approx(0.0)  # spike fully evicted
    assert smoother.ema_p_bottom > 0.0  # ...while the EMA still remembers it
    assert t_ms == FRAME_MS * (int(temporal.window_frames) + 1)


@pytest.mark.parametrize(
    "p_bottom, p_camera, expected",
    [
        (0.6, 0.2, 0.75),  # rehydrated row that no longer sums to 1
        (0.5, 0.5, 0.5),
        (1.0, 0.0, 1.0),
        (0.0, 0.0, 0.5),  # degenerate pair carries no evidence
        (2.0, -1.0, 1.0),  # clamped into the unit interval
        (-0.5, 1.0, 0.0),
    ],
)
def test_probabilities_are_renormalised_over_the_two_decision_classes(
    p_bottom, p_camera, expected
):
    parsed = _normalised_p_bottom(
        GazeDecision(
            t_ms=0,
            frame_id=0,
            label=BOTTOM,
            p_camera=p_camera,
            p_bottom=p_bottom,
            face_valid=True,
        )
    )

    assert parsed == pytest.approx(expected)


def test_a_renormalised_probability_is_what_reaches_the_score(temporal):
    # alpha=1 and a one-frame window make both stages the identity, so the
    # score is exactly the renormalised probability and nothing else.
    passthrough = dataclasses.replace(temporal, window_frames=1, ema_alpha=1.0)
    smoother = TemporalSmoother(passthrough)

    smoother.update(
        GazeDecision(
            t_ms=0, frame_id=0, label=BOTTOM, p_camera=0.2, p_bottom=0.6, face_valid=True
        )
    )

    assert smoother.smoothed_p_bottom == pytest.approx(0.75)


def test_nan_probabilities_never_poison_the_smoothed_score(temporal):
    """A corrupt row must not put NaN into the state machine's comparisons."""
    smoother = TemporalSmoother(temporal)
    nan = float("nan")

    smoother.update(
        GazeDecision(t_ms=0, frame_id=0, label=BOTTOM, p_camera=nan, p_bottom=nan, face_valid=True)
    )
    event = smoother.update(decision(FRAME_MS, 1.0))

    assert math.isfinite(smoother.smoothed_p_bottom)
    assert 0.0 <= smoother.smoothed_p_bottom <= 1.0
    assert math.isfinite(event.confidence)
    assert event.label in {CAMERA, BOTTOM, UNCERTAIN}


def test_a_non_finite_probability_should_count_as_no_evidence():
    """A corrupt row abstains; it must never read as maximal CAMERA evidence.

    The old ``if total <= 1e-9`` guard was false for a NaN total, so the clamp
    ``min(1.0, max(0.0, nan))`` handed back 0.0 -- p_bottom = 0 is the most
    confident CAMERA vote there is, conjured out of an unreadable number.
    """
    nan = float("nan")
    parsed = _normalised_p_bottom(
        GazeDecision(t_ms=0, frame_id=0, label=BOTTOM, p_camera=nan, p_bottom=nan, face_valid=True)
    )

    assert parsed == 0.5


@pytest.mark.parametrize(
    "p_camera, p_bottom",
    [
        (1.0, float("nan")),           # only the BOTTOM side is corrupt
        (float("nan"), 0.0),           # only the CAMERA side is corrupt
        (float("inf"), float("inf")),  # a total that divides out to NaN
        (-1.0, 1.0),                   # a total of exactly zero
        (-0.6, -0.4),                  # a negative total
        # The asymmetric infinities are the subtle pair. p_camera=inf divides to
        # a perfectly FINITE 0.0, so a ratio-only guard waves it through as the
        # most confident CAMERA vote there is; the mirrored p_bottom=inf gives
        # inf/inf -> NaN and is caught. Screening the inputs is what makes the
        # two sides behave the same.
        (float("inf"), 0.5),           # finite ratio 0.0 -- reads as pure CAMERA
        (0.5, float("inf")),           # the mirror, caught by the NaN ratio
        (float("-inf"), 0.5),
        (0.5, float("-inf")),
    ],
)
def test_no_unreadable_probability_pair_votes_for_a_class(p_camera, p_bottom):
    """Whatever is wrong with the pair, the frame abstains rather than guessing.

    The three NaN-producing rows are the ones the old negative guard let
    through to the clamp, where they came back as a maximally confident CAMERA
    vote; the zero and negative totals were already caught and are here so the
    two families stay one rule -- "unreadable means 0.5" -- rather than two.
    """
    parsed = _normalised_p_bottom(
        GazeDecision(
            t_ms=0,
            frame_id=0,
            label=BOTTOM,
            p_camera=p_camera,
            p_bottom=p_bottom,
            face_valid=True,
        )
    )

    assert parsed == 0.5


def test_the_smoothed_score_is_the_mean_of_the_two_stages(temporal):
    smoother = TemporalSmoother(temporal)
    stream = [0.9, 0.1, 0.55, 0.4, 0.95, 0.5, 0.2, 0.8, 0.99, 0.05, 1.0, 0.0]

    t_ms = 0
    for index, p_bottom in enumerate(stream):
        # One dropout in the middle, so the invariant covers the decay branch.
        event = (
            smoother.update(abstention(t_ms))
            if index == 5
            else smoother.update(decision(t_ms, p_bottom))
        )

        blended = 0.5 * (smoother.ema_p_bottom + smoother.vote_p_bottom)
        assert smoother.smoothed_p_bottom == pytest.approx(blended)
        assert smoother.smoothed_p_camera == pytest.approx(1.0 - smoother.smoothed_p_bottom)
        assert event.smoothed_p_bottom == pytest.approx(smoother.smoothed_p_bottom)
        assert event.smoothed_p_camera == pytest.approx(1.0 - smoother.smoothed_p_bottom)
        assert event.smoothed_p_bottom + event.smoothed_p_camera == pytest.approx(1.0)
        t_ms += FRAME_MS


def test_a_valid_decisions_label_is_ignored_and_only_its_probabilities_vote(temporal):
    """Only UNCERTAIN is read off the label; CAMERA/BOTTOM carry no extra vote."""
    honest = TemporalSmoother(temporal)
    mislabelled = TemporalSmoother(temporal)

    for t_ms in range(0, 8 * FRAME_MS, FRAME_MS):
        honest.update(decision(t_ms, 1.0, label=BOTTOM))
        mislabelled.update(decision(t_ms, 1.0, label=CAMERA))

    assert mislabelled.state == honest.state == BOTTOM
    assert mislabelled.smoothed_p_bottom == pytest.approx(honest.smoothed_p_bottom)


# --------------------------------------------------------------------------
# Hysteresis: arming, dwell, dead band
# --------------------------------------------------------------------------


def test_the_two_dwells_are_asymmetric_on_identical_evidence(temporal):
    """Same step size, same threshold: only the dwell differs, and BOTTOM costs more."""
    assert temporal.enter_bottom_threshold == temporal.enter_camera_threshold
    assert temporal.to_bottom_dwell_ms > temporal.to_camera_dwell_ms

    commits = {}
    for name, p_bottom in ((BOTTOM, 1.0), (CAMERA, 0.0)):
        smoother = TemporalSmoother(temporal)
        for t_ms in range(0, 20 * FRAME_MS, FRAME_MS):
            event = smoother.update(decision(t_ms, p_bottom))
            if event.is_transition:
                commits[name] = event
                break

    assert commits[BOTTOM].label == BOTTOM
    assert commits[CAMERA].label == CAMERA
    # Both stream cross on their very first frame (score 0.84 / 0.16), so the
    # commit lands on the first frame at or after the dwell.
    assert commits[BOTTOM].t_ms == 625
    assert commits[CAMERA].t_ms == 500
    assert commits[BOTTOM].t_ms > commits[CAMERA].t_ms
    for name, dwell in ((BOTTOM, temporal.to_bottom_dwell_ms), (CAMERA, temporal.to_camera_dwell_ms)):
        assert dwell <= commits[name].t_ms < dwell + FRAME_MS


@pytest.mark.parametrize(
    "settled_p, step_p, expected_state, expected_latency_ms",
    [(0.0, 1.0, BOTTOM, 1000), (1.0, 0.0, CAMERA, 875)],
)
def test_clean_step_latency_matches_the_documented_blend(
    temporal, settled_p, step_p, expected_state, expected_latency_ms
):
    """The mean(EMA, vote) row of the module docstring's latency table.

    The numbers only mean anything for the shipped constants, so assert those
    first: if the config is retuned, the table in the docstring is stale too.
    """
    assert (temporal.window_frames, temporal.ema_alpha, temporal.vote_recency_weight) == (8, 0.35, 2.0)
    assert temporal.enter_bottom_threshold == temporal.enter_camera_threshold == 0.60
    assert (temporal.to_bottom_dwell_ms, temporal.to_camera_dwell_ms) == (600, 400)

    smoother = TemporalSmoother(temporal)
    step_ms = settle(smoother, settled_p)

    transition = None
    for t_ms in range(step_ms, step_ms + 20 * FRAME_MS, FRAME_MS):
        event = smoother.update(decision(t_ms, step_p))
        if event.is_transition:
            transition = event
            break

    assert transition is not None
    assert transition.label == expected_state
    assert transition.t_ms - step_ms == expected_latency_ms


def test_a_four_frame_spike_arms_the_transition_but_never_commits(temporal):
    """Arming is not committing: the burst has to survive the dwell as well."""
    smoother = TemporalSmoother(temporal)
    spike_ms = settle(smoother, 0.0)

    peak = 0.0
    armed = False
    events = []
    t_ms = spike_ms
    for _ in range(4):  # the longest spike the docstring's table covers
        events.append(smoother.update(decision(t_ms, 1.0)))
        peak = max(peak, smoother.smoothed_p_bottom)
        armed = armed or smoother.pending_state == BOTTOM
        t_ms += FRAME_MS
    for _ in range(20):  # ...and the evidence goes away again
        events.append(smoother.update(decision(t_ms, 0.0)))
        t_ms += FRAME_MS

    assert peak >= temporal.enter_bottom_threshold, "the spike must really cross"
    assert armed, "crossing the threshold must arm the transition"
    assert transitions(events) == []
    assert {event.label for event in events} == {CAMERA}
    assert smoother.state == CAMERA


def test_a_zero_dwell_commits_on_the_arming_frame(temporal):
    instant = dataclasses.replace(temporal, to_bottom_dwell_ms=0)
    smoother = TemporalSmoother(instant)

    event = smoother.update(decision(0, 1.0))

    assert event.is_transition is True
    assert event.label == BOTTOM
    assert event.continuous_duration_ms == 0
    assert smoother.pending_state is None  # committing disarms


def test_contradicting_evidence_in_the_dead_band_disarms_a_running_dwell(temporal):
    smoother = TemporalSmoother(temporal)
    next_ms = arm_bottom(smoother)
    assert next_ms < temporal.to_bottom_dwell_ms <= next_ms + FRAME_MS

    still_armed = smoother.update(decision(next_ms, 0.0))
    disarmed = smoother.update(decision(next_ms + FRAME_MS, 0.0))

    assert still_armed.is_transition is False
    assert smoother.pending_state is None
    dead_band = 1.0 - temporal.enter_camera_threshold
    assert dead_band < smoother.smoothed_p_bottom < temporal.enter_bottom_threshold
    assert disarmed.is_transition is False
    assert smoother.state == UNCERTAIN


def test_a_dropout_does_not_disarm_and_the_transition_commits_on_an_abstaining_frame(temporal):
    """Dropped frames are normal; they must not restart a dwell (doc 6)."""
    smoother = TemporalSmoother(temporal)
    next_ms = arm_bottom(smoother)

    during = smoother.update(abstention(next_ms))
    committed = smoother.update(abstention(next_ms + FRAME_MS))

    # Mid-dropout the dwell is still running on pre-dropout evidence.
    assert during.is_transition is False
    assert during.label == UNCERTAIN
    assert smoother.smoothed_p_bottom >= temporal.enter_bottom_threshold
    # ...and it commits on a frame that itself voted for nothing.
    assert committed.is_transition is True
    assert committed.label == BOTTOM
    assert committed.t_ms == next_ms + FRAME_MS
    assert temporal.to_bottom_dwell_ms <= committed.t_ms < temporal.to_bottom_dwell_ms + FRAME_MS
    assert committed.face_valid is False
    assert smoother.state == BOTTOM


def test_an_ambiguous_stream_holds_the_committed_state_instead_of_going_uncertain(temporal):
    """Only abstention reaches UNCERTAIN; a 50/50 score just parks the state."""
    smoother = TemporalSmoother(temporal)
    t_ms = settle(smoother, 0.0)

    events = []
    for _ in range(40):  # far longer than uncertain_dwell_ms
        events.append(smoother.update(decision(t_ms, 0.5)))
        t_ms += FRAME_MS

    assert {event.label for event in events} == {CAMERA}
    assert transitions(events) == []
    assert smoother.pending_state is None
    assert smoother.smoothed_p_bottom == pytest.approx(0.5, abs=1e-3)


@pytest.mark.parametrize("score", [0.0, 0.05, 0.39, 0.4, 0.5, 0.6, 0.61, 0.95, 1.0])
def test_the_candidate_never_proposes_uncertain(temporal, score):
    """UNCERTAIN is not a thing the score can argue for -- only abstention is."""
    smoother = TemporalSmoother(temporal)
    smoother._score_bottom = score  # white box: the hysteresis input

    candidate = smoother._candidate()

    assert candidate in {None, CAMERA, BOTTOM}
    assert candidate != UNCERTAIN


@pytest.mark.parametrize(
    "score, expected",
    [
        (0.60, BOTTOM),  # entry threshold is inclusive
        (0.5999, None),
        (0.40, CAMERA),
        (0.4001, None),
    ],
)
def test_the_entry_thresholds_are_inclusive_and_bound_the_dead_band(temporal, score, expected):
    smoother = TemporalSmoother(temporal)
    smoother._score_bottom = score

    assert smoother._candidate() == expected


def test_thresholds_below_one_half_break_the_tie_toward_bottom(temporal):
    """doc 7 gates on BOTTOM recall, so an overlapping pair of tests favours BOTTOM."""
    overlapping = dataclasses.replace(
        temporal, enter_bottom_threshold=0.4, enter_camera_threshold=0.4
    )
    smoother = TemporalSmoother(overlapping)
    smoother._score_bottom = 0.5  # both tests pass

    assert smoother._candidate() == BOTTOM


def test_a_new_candidate_restarts_the_dwell_clock(temporal):
    smoother = TemporalSmoother(temporal)
    arm_bottom(smoother)

    # Flip the evidence hard: BOTTOM disarms, CAMERA arms with a fresh clock.
    t_ms = 4 * FRAME_MS
    for _ in range(4):
        smoother.update(decision(t_ms, 0.0))
        t_ms += FRAME_MS
    assert smoother.pending_state == CAMERA

    armed_at_ms = smoother._pending_since_ms
    assert armed_at_ms is not None and armed_at_ms >= 4 * FRAME_MS
    while smoother.state == UNCERTAIN and t_ms < 40 * FRAME_MS:
        event = smoother.update(decision(t_ms, 0.0))
        t_ms += FRAME_MS

    assert smoother.state == CAMERA
    assert event.t_ms - armed_at_ms >= temporal.to_camera_dwell_ms


# --------------------------------------------------------------------------
# Abstention and the UNCERTAIN state
# --------------------------------------------------------------------------


def test_continuous_abstention_falls_back_to_uncertain_after_its_dwell(temporal):
    smoother = TemporalSmoother(temporal)
    base_ms = settle(smoother, 1.0)

    events = [smoother.update(abstention(base_ms + i * FRAME_MS)) for i in range(10)]

    committed = [index for index, event in enumerate(events) if event.is_transition]
    assert committed == [7]
    # The boundary is the frame grid crossing uncertain_dwell_ms, inclusive.
    assert 6 * FRAME_MS < temporal.uncertain_dwell_ms <= 7 * FRAME_MS
    assert events[6].label == BOTTOM
    assert events[7].label == UNCERTAIN
    assert events[9].label == UNCERTAIN
    assert events[9].is_transition is False  # the fallback fires once


def test_the_uncertain_fallback_outranks_a_frozen_high_score(temporal):
    """With decay off the score still screams BOTTOM; the abstention run wins."""
    frozen = dataclasses.replace(temporal, decay_on_invalid=False)
    smoother = TemporalSmoother(frozen)
    base_ms = settle(smoother, 1.0)
    score_before = smoother.smoothed_p_bottom
    assert score_before > temporal.enter_bottom_threshold

    events = [smoother.update(abstention(base_ms + i * FRAME_MS)) for i in range(9)]

    assert smoother.smoothed_p_bottom == score_before  # frozen, bit for bit
    assert smoother.state == UNCERTAIN
    assert [event.label for event in events][-1] == UNCERTAIN
    assert len(transitions(events)) == 1


def test_a_single_valid_frame_restarts_the_abstention_clock(temporal):
    """"Continuous" means continuous: one good frame resets the fallback timer."""
    interrupted = TemporalSmoother(temporal)
    base_ms = settle(interrupted, 1.0)
    events = []
    for index in range(13):
        t_ms = base_ms + index * FRAME_MS
        events.append(
            interrupted.update(decision(t_ms, 1.0) if index == 6 else abstention(t_ms))
        )

    assert interrupted.state == BOTTOM
    assert UNCERTAIN not in {event.label for event in events}

    # Control: the same 13 frames without the interruption do fall back.
    uninterrupted = TemporalSmoother(temporal)
    base_ms = settle(uninterrupted, 1.0)
    for index in range(13):
        uninterrupted.update(abstention(base_ms + index * FRAME_MS))

    assert uninterrupted.state == UNCERTAIN


def test_a_long_dropout_cannot_arm_anything(temporal):
    """Past the fallback the abstention branch returns before the score is read."""
    frozen = dataclasses.replace(temporal, decay_on_invalid=False)
    smoother = TemporalSmoother(frozen)
    base_ms = settle(smoother, 1.0)

    pendings = []
    for index in range(20):
        smoother.update(abstention(base_ms + index * FRAME_MS))
        pendings.append(smoother.pending_state)

    assert smoother.state == UNCERTAIN
    assert set(pendings) == {None}


@pytest.mark.parametrize("label", [GazeState.UNCERTAIN, "UNCERTAIN", "uncertain", "  Uncertain "])
def test_the_uncertain_label_is_recognised_however_it_was_spelled(temporal, label):
    """Decisions round-tripped through an enum or a CSV must still abstain."""
    smoother = TemporalSmoother(temporal)
    base_ms = settle(smoother, 1.0)

    for index in range(10):
        # face_valid stays True, so only the label can make these abstain; read
        # as ordinary 0.5 votes they would merely park the state at BOTTOM.
        smoother.update(abstention(base_ms + index * FRAME_MS, face_valid=True, label=label))

    assert smoother.state == UNCERTAIN


def test_an_invalid_face_abstains_however_confident_its_label_claims_to_be(temporal):
    """A dead face cannot carry a trustworthy probability, so it never votes."""
    smoother = TemporalSmoother(temporal)
    base_ms = settle(smoother, 1.0)

    events = [
        smoother.update(decision(base_ms + index * FRAME_MS, 0.0, face_valid=False))
        for index in range(10)
    ]

    assert CAMERA not in {event.label for event in events}
    assert smoother.state == UNCERTAIN
    assert len(transitions(events)) == 1


@pytest.mark.parametrize("decay_on_invalid", [True, False])
def test_decay_on_invalid_chooses_between_going_stale_and_freezing(temporal, decay_on_invalid):
    smoother = TemporalSmoother(dataclasses.replace(temporal, decay_on_invalid=decay_on_invalid))
    base_ms = settle(smoother, 1.0)
    before = smoother.smoothed_p_bottom

    for index in range(3):  # short of uncertain_dwell_ms
        smoother.update(abstention(base_ms + index * FRAME_MS))
    after = smoother.smoothed_p_bottom

    assert smoother.state == BOTTOM
    if decay_on_invalid:
        assert after < before
        assert abs(after - 0.5) < abs(before - 0.5)  # toward neutral, never past it
        assert after > 0.5
    else:
        assert after == before  # nothing was folded in at all


# --------------------------------------------------------------------------
# Event payload
# --------------------------------------------------------------------------


def test_event_face_valid_needs_both_a_good_face_and_a_committed_state(temporal):
    smoother = TemporalSmoother(temporal)

    # A perfectly good face, but the state has not committed yet.
    first = smoother.update(decision(0, 1.0))
    assert first.face_valid is False
    assert first.label == UNCERTAIN

    t_ms = steady(smoother, 1.0, FRAME_MS, 8)
    assert smoother.state == BOTTOM
    assert smoother.last_event.face_valid is True

    # A low-margin abstention on a good face: the state is still backed by one.
    low_margin = smoother.update(abstention(t_ms, face_valid=True))
    assert low_margin.label == BOTTOM
    assert low_margin.face_valid is True

    # A lost face while the state is still BOTTOM reports the lost face.
    lost = smoother.update(decision(t_ms + FRAME_MS, 1.0, face_valid=False))
    assert lost.label == BOTTOM
    assert lost.face_valid is False


def test_every_uncertain_event_reports_face_valid_false(temporal):
    """doc 6: the UNCERTAIN state is by definition not backed by an observation."""
    immediate = dataclasses.replace(temporal, uncertain_dwell_ms=0)
    smoother = TemporalSmoother(immediate)
    t_ms = steady(smoother, 1.0, 0, 8)
    assert smoother.state == BOTTOM

    event = smoother.update(abstention(t_ms, face_valid=True))

    assert event.label == UNCERTAIN
    assert event.is_transition is True
    assert event.face_valid is False


def test_uncertain_confidence_reports_the_leading_score(temporal):
    """While UNCERTAIN the confidence says *why*: ~0.5 stale, high means armed."""
    idle = TemporalSmoother(temporal)
    idle.update(abstention(0))
    assert idle.last_event.label == UNCERTAIN
    assert idle.last_event.confidence == pytest.approx(0.5)

    armed = TemporalSmoother(temporal)
    arm_bottom(armed)
    event = armed.last_event
    assert event.label == UNCERTAIN
    assert event.confidence == pytest.approx(max(event.smoothed_p_bottom, event.smoothed_p_camera))
    assert event.confidence > temporal.enter_bottom_threshold


@pytest.mark.parametrize("p_bottom, state", [(1.0, BOTTOM), (0.0, CAMERA)])
def test_a_committed_state_reports_its_own_class_as_the_confidence(temporal, p_bottom, state):
    smoother = TemporalSmoother(temporal)
    settle(smoother, p_bottom)
    event = smoother.last_event

    assert event.label == state
    expected = event.smoothed_p_bottom if state == BOTTOM else event.smoothed_p_camera
    assert event.confidence == pytest.approx(expected)
    assert event.confidence > 0.5


def test_continuous_duration_restarts_at_the_committing_frame(temporal):
    smoother = TemporalSmoother(temporal)

    durations = []
    transition_index = None
    for index, t_ms in enumerate(range(0, 12 * FRAME_MS, FRAME_MS)):
        event = smoother.update(decision(t_ms, 1.0))
        durations.append(event.continuous_duration_ms)
        if event.is_transition:
            transition_index = index

    assert transition_index is not None
    assert durations[transition_index] == 0
    # Before the commit the duration measures the UNCERTAIN run from frame 0.
    assert durations[:transition_index] == [i * FRAME_MS for i in range(transition_index)]
    # After it, the BOTTOM run.
    assert durations[transition_index:] == [
        i * FRAME_MS for i in range(len(durations) - transition_index)
    ]


def test_a_reordered_stream_never_reports_a_negative_duration(temporal):
    smoother = TemporalSmoother(temporal)
    settle(smoother, 1.0)
    assert smoother.state == BOTTOM

    replayed = smoother.update(decision(0, 1.0))

    assert replayed.continuous_duration_ms == 0
    assert replayed.continuous_duration_ms >= 0


def test_update_does_not_mutate_the_caller_decision(temporal):
    smoother = TemporalSmoother(temporal)
    given = decision(0, 0.8)
    before = given.to_dict()

    smoother.update(given)

    assert given.to_dict() == before


def test_events_carry_the_transport_contract_fields(temporal):
    smoother = TemporalSmoother(temporal, model_version="gaze_v9.9.9")

    event = smoother.update(decision(0, 1.0))
    payload = event.to_dict()

    assert event.type == "GAZE_STATE"
    assert event.model_version == "gaze_v9.9.9"
    assert payload["model_version"] == "gaze_v9.9.9"
    assert payload["t_ms"] == 0
    assert set(payload) == {
        "type",
        "model_version",
        "t_ms",
        "label",
        "confidence",
        "continuous_duration_ms",
        "face_valid",
    }


# --------------------------------------------------------------------------
# should_emit: transitions, heartbeats, idempotency
# --------------------------------------------------------------------------


def test_the_first_event_of_a_take_always_goes_on_the_wire(temporal):
    smoother = TemporalSmoother(temporal)

    assert smoother.should_emit(event_at(50_000)) is True


def test_a_heartbeat_is_due_exactly_at_the_gap(temporal):
    smoother = TemporalSmoother(temporal)
    heartbeat = int(temporal.heartbeat_ms)
    smoother.should_emit(event_at(0))

    assert smoother.should_emit(event_at(heartbeat - 1)) is False
    assert smoother.should_emit(event_at(heartbeat)) is True
    assert smoother.should_emit(event_at(2 * heartbeat - 1)) is False
    assert smoother.should_emit(event_at(2 * heartbeat)) is True


def test_a_transition_emits_immediately_and_restarts_the_heartbeat_clock(temporal):
    smoother = TemporalSmoother(temporal)
    heartbeat = int(temporal.heartbeat_ms)
    smoother.should_emit(event_at(0))

    assert smoother.should_emit(event_at(200, label=BOTTOM, transition=True)) is True
    # The clock now runs from 200, not from 0.
    assert smoother.should_emit(event_at(200 + heartbeat - 1)) is False
    assert smoother.should_emit(event_at(200 + heartbeat)) is True


@pytest.mark.parametrize("t_ms, expected", [(500, False), (1000, True)])
def test_should_emit_is_idempotent_for_one_event(temporal, t_ms, expected):
    """A caller may branch on the answer twice without changing it."""
    smoother = TemporalSmoother(temporal)
    smoother.should_emit(event_at(0))
    event = event_at(t_ms)

    answers = [smoother.should_emit(event) for _ in range(3)]

    assert answers == [expected, expected, expected]


def test_should_emit_is_stateful_across_different_events(temporal):
    smoother = TemporalSmoother(temporal)
    stream = [event_at(t_ms) for t_ms in (0, 500, 1000, 1500, 2000, 2600)]

    emitted = [event.t_ms for event in stream if smoother.should_emit(event)]

    assert emitted == [0, 1000, 2000]


def test_a_stream_emits_transitions_and_heartbeats_only(temporal):
    """End-to-end: what the transport sees out of a full CAMERA -> BOTTOM take."""
    smoother = TemporalSmoother(temporal)
    on_the_wire = []

    t_ms = 0
    for _ in range(16):
        event = smoother.update(decision(t_ms, 0.0))
        if smoother.should_emit(event):
            on_the_wire.append(event)
        t_ms += FRAME_MS
    for _ in range(16):
        event = smoother.update(decision(t_ms, 1.0))
        if smoother.should_emit(event):
            on_the_wire.append(event)
        t_ms += FRAME_MS

    assert [event.label for event in on_the_wire][0] == UNCERTAIN
    assert on_the_wire[-1].label == BOTTOM
    assert len(transitions(on_the_wire)) == 2  # -> CAMERA, then -> BOTTOM
    gaps = [b.t_ms - a.t_ms for a, b in zip(on_the_wire, on_the_wire[1:])]
    assert all(gap > 0 for gap in gaps)
    assert max(gaps) <= temporal.heartbeat_ms + FRAME_MS
