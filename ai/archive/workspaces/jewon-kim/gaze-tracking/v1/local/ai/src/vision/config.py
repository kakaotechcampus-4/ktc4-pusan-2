"""Typed configuration loaded from ``ai/configs/*.yaml``.

Every tunable number in the pipeline lives here so that an experiment can be
reproduced from a config hash alone (doc 18).  Defaults match the values fixed
in the design document; the YAML files only need to carry overrides.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

#: Repository root = three levels above this file (ai/src/vision/config.py).
REPO_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = REPO_ROOT / "ai"
CONFIG_DIR = AI_ROOT / "configs"
MODELS_DIR = AI_ROOT / "models"
DATASETS_DIR = AI_ROOT / "datasets"
REPORTS_DIR = AI_ROOT / "reports"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


@dataclass
class PreprocessConfig:
    """doc 3-1: input standardisation."""

    analysis_fps: float = 8.0
    face_crop_size: int = 224
    eye_crop_size: List[int] = field(default_factory=lambda: [60, 36])  # (w, h)
    #: Extra margin around the landmark bbox, as a fraction of bbox size.
    face_crop_margin: float = 0.25
    #: Eye crop width as a multiple of the inter-corner eye distance.
    eye_crop_scale: float = 2.2
    #: Below this MediaPipe face score the frame is UNCERTAIN, no inference.
    min_face_confidence: float = 0.50
    #: Face bbox area / frame area below which the face is "too small".
    min_face_area_ratio: float = 0.010
    #: Eye aspect ratio below which we treat the eyes as closed.
    min_eye_openness: float = 0.12
    #: Fraction of the frame border a bbox may touch before OUT_OF_FRAME.
    border_tolerance_px: int = 2
    #: Rotate the face crop so the eye line is horizontal.
    align_face_roll: bool = True
    #: Faces MediaPipe may return.  Only one is analysed (the main face, see
    #: ``preprocess.pipeline.select_main_face``); the others are what lets the
    #: runtime notice a second person instead of silently measuring them.
    num_faces: int = 3
    landmarker_model_path: str = "ai/models/face_landmarker.task"
    #: MediaPipe running mode: IMAGE keeps offline runs deterministic.
    running_mode: str = "IMAGE"
    #: After calibration, treat a frame as a blink when its eye aspect ratio
    #: drops below ``blink_ratio`` x this user's own open-eye reference (never
    #: below ``min_eye_openness``).  The reference is the smaller of the recent
    #: median and the BOTTOM-cue median, because the eyelid lowers when the
    #: user reads the script -- a CAMERA-only reference would call that a blink.
    adaptive_blink: bool = True
    blink_ratio: float = 0.70
    #: Recent valid frames the running median is taken over.
    blink_window_frames: int = 24
    #: The adaptive gate stays off until it has seen this many frames.
    blink_min_reference_frames: int = 8


@dataclass
class BackboneConfig:
    """doc 3-2 / 3-3: which gaze backbone to run and where its weights live."""

    name: str = "head_pose"
    checkpoint: Optional[str] = None
    device: str = "cpu"
    #: torch threads; 0 leaves the torch default alone.
    num_threads: int = 0
    batch_size: int = 1
    #: Per-backbone extras (bins, input size, ...).
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CalibrationConfig:
    """doc 5-1 .. 5-4."""

    #: Seconds of CAMERA / BOTTOM / SCREEN staring at take start.  The runtime
    #: ends a cue on its good-frame gauge instead; these size the recording
    #: blocks (collector) and the offline calibration windows (data.splits).
    camera_seconds: float = 2.0
    bottom_seconds: float = 2.0
    screen_seconds: float = 2.0
    #: Hard floor on usable samples per class before we even try to fit.
    min_samples_per_class: int = 10
    #: doc 5-2 pass rule.
    min_loo_accuracy: float = 0.85
    #: Centroid distance floor, in standardised feature units.
    min_centroid_distance: float = 0.60
    #: Fisher-like separability floor (centroid distance / pooled spread).
    min_separability: float = 1.00
    #: Feature set: A = gaze only, B = + head pose, C = + relative-to-centroid.
    feature_set: str = "C"
    #: sklearn LogisticRegression settings (doc 5-3).
    C: float = 1.0
    max_iter: int = 1000
    class_weight: str = "balanced"
    random_seed: int = 42
    #: doc 5-4 UNCERTAIN rule; swept on validation.
    p_max_threshold: float = 0.70
    margin_threshold: float = 0.20
    #: Warn (do not fail) when BOTTOM pitch is not below CAMERA pitch.
    check_pitch_ordering: bool = True

    #: Which per-user classifier to fit: "reference" (anchor regions in gaze
    #: space, see ``calibration.references``) or "logistic" (the doc 5-3 LR,
    #: kept for ablation).  The feature_set / C / max_iter / class_weight keys
    #: above only apply to "logistic".
    method: str = "reference"

    # -- good-frame gauge (one cue at a time; see calibration.gauge) -------
    #: Accepted frames each cue needs before it is complete.
    target_good_frames: int = 16
    #: Frames this soon after a cue appears are still the saccade to it.
    cue_settle_ms: int = 500
    #: A cue that has not filled by now ends; it passes only if it reached
    #: ``min_samples_per_class``.
    cue_timeout_ms: int = 8000
    #: Gaze estimates below this backbone confidence never count.
    min_sample_confidence: float = 0.30
    #: The head must stay within this many degrees (yaw and pitch) of the
    #: pose it had when the first cue settled: the eyes move, the head does not.
    max_head_deviation_deg: float = 6.0
    #: A frame further than this many noise units from its cue's running
    #: median is a glance elsewhere, not a sample of the cue.
    outlier_k: float = 4.0
    #: Head-pose engine: with a reference pose (the head circle's centre, else
    #: the screen-centre look) a frame counts only when the head points the
    #: cue's way from it -- up toward the lens, down toward the script, near
    #: the centre for the screen -- so looking elsewhere never fills the gauge.
    cue_direction_gate: bool = True
    cue_min_up_deg: float = 2.0
    cue_min_down_deg: float = 3.0
    cue_screen_radius_deg: float = 7.0
    #: Turned further sideways than this from the reference: not at the target.
    cue_max_side_deg: float = 10.0
    #: With a head circle measured, the screen-centre look *confirms* its centre
    #: instead of measuring from scratch: after ``cue_confirm_frames`` good frames
    #: their median is compared with the circle's centre.  Within
    #: ``cue_confirm_deg`` the circle's centre frames join the screen-centre
    #: samples and the look ends; further, the presenter moved since the circle,
    #: so the look goes on to a full ``target_good_frames`` measurement and the
    #: lens and script looks are read from that new posture.
    cue_confirm_frames: int = 8
    cue_confirm_deg: float = 4.0

    # -- reference-anchor classifier ---------------------------------------
    #: Floor on the pooled per-axis gaze noise, degrees.
    sigma_min_deg: float = 1.5
    #: Calibration is a still fixation; live gaze is noisier.  The pooled
    #: robust sigma is widened by this factor before use.
    sigma_scale: float = 1.25
    #: Effective width/height ratio of the screen region in GAZE space.  The
    #: screen box is sized from the CAMERA->SCREEN offset (half its height) and
    #: this ratio; geometric backbones compress vertical gaze, so this is the
    #: first knob to tune when the screen sides read as OTHER.
    screen_aspect: float = 1.7778
    #: Script region around the BOTTOM anchor, as fractions of the screen box.
    script_width_fraction: float = 0.60
    script_height_fraction: float = 0.25
    #: > 0 pins the CAMERA region to a disc of this radius instead of a point
    #: (0 lets the noise level alone set the eye-contact zone).
    camera_halfwidth_deg: float = 0.0
    #: OTHER is a constant density over this gaze field (degrees).
    other_field_yaw_deg: float = 120.0
    other_field_pitch_deg: float = 90.0
    #: OTHER takes part only once the gaze is this far outside the calibrated
    #: screen area (``screen_region``).  Closer in -- a head raised a little
    #: above the lens, turned a little past the screen's edge -- the nearest
    #: target wins: presenters move their heads, and with the head-pose backbone
    #: the calibrated regions are only a few degrees wide.  A clear look away
    #: (beyond this, or past ``head_away_*``) is still OTHER.
    other_margin_deg: float = 6.0
    #: The calibrated screen area is at least this wide on each side (yaw).
    #: With the head-pose backbone the lens, screen-centre and script looks often
    #: sit within a degree or two sideways (and the screen folds into the lens),
    #: which would leave no room to turn the head toward the screen's sides.
    screen_min_halfwidth_deg: float = 10.0
    #: ... and at most this wide (yaw), however far the head moved from the lens
    #: to the screen centre.  The width is read from that vertical move times
    #: ``screen_aspect``; a presenter who lifts the head a lot for the lens would
    #: otherwise get a screen 20+ deg wide each side, and a clear turn to the
    #: side would never read as OTHER.  The head turns ~10 deg at most to look
    #: at a laptop screen's edge.
    screen_max_halfwidth_deg: float = 12.0
    #: Class priors (renormalised; 0 switches a class off).
    prior_camera: float = 0.25
    prior_screen: float = 0.25
    prior_bottom: float = 0.25
    prior_other: float = 0.25
    #: A head turned this far from the calibration pose is looking away: the
    #: decision is OTHER whatever the eyes read (they are unreliable there).
    head_away_yaw_deg: float = 35.0
    head_away_pitch_deg: float = 30.0
    #: Anchors closer than this (in pooled noise units) cannot be told apart.
    min_anchor_separation: float = 4.0
    #: When only the SCREEN anchor is the problem (too close to the lens or the
    #: script), keep a CAMERA / BOTTOM / OTHER model and report screen looks as
    #: CAMERA instead of failing the calibration.
    merge_inseparable_screen: bool = True

    # -- re-anchor ("look at the lens again") during a live take -------------
    reanchor_frames: int = 8
    #: A shift larger than this means the user was not looking at the lens.
    reanchor_max_shift_deg: float = 8.0
    reanchor_timeout_ms: int = 3000


@dataclass
class PlacementConfig:
    """Camera-placement check that runs before calibration.

    Not in the design document: it turns doc 19's ``webcam이 화면 아래/옆에 위치``
    failure bucket into an up-front check, because that geometry silently
    inverts the meaning of CAMERA vs BOTTOM rather than degrading it.
    """

    #: Seconds spent on each of the two cues (look at lens / look at screen centre).
    camera_seconds: float = 2.0
    screen_seconds: float = 2.0
    min_samples_per_target: int = 8
    #: Gaze samples below this backbone confidence are dropped.
    min_sample_confidence: float = 0.30
    #: "learned" fits the same LR family as doc 5-3 and reads the placement off
    #: the decision boundary; "geometric" applies fixed angular thresholds.
    mode: str = "learned"
    #: learned mode: the two cues must be separable this well to be trusted,
    #: mirroring the doc 5-2 calibration gate.
    min_loo_accuracy: float = 0.85
    C: float = 1.0
    max_iter: int = 1000
    random_seed: int = 42
    #: How dominant one axis must be before we call the placement vertical or
    #: horizontal; below this the two axes are treated as ambiguous.
    min_axis_dominance: float = 1.30
    #: geometric mode (and the sanity floor for learned mode).
    min_delta_deg: float = 4.0
    min_separation: float = 1.00
    #: Placement read off the calibration anchors: the dominant axis must be
    #: this many times the other one.  2.5 means a top-centre verdict needs
    #: ``|delta_yaw| <= 0.4 * |delta_pitch|``, i.e. the lens within ~22 deg
    #: of straight above the screen centre.
    anchor_min_axis_dominance: float = 2.5


@dataclass
class TemporalConfig:
    """doc 6: EMA + weighted voting + hysteresis."""

    window_frames: int = 8
    #: EMA smoothing factor on p_bottom; higher = more reactive.
    ema_alpha: float = 0.35
    #: Newest frame weight relative to oldest in the voting window.
    vote_recency_weight: float = 2.0
    #: Probability a smoothed state must exceed to arm a transition.
    enter_bottom_threshold: float = 0.60
    enter_camera_threshold: float = 0.60
    #: Dwell time before the armed transition is committed (doc 6-1).
    to_bottom_dwell_ms: int = 600
    to_camera_dwell_ms: int = 400
    #: Consecutive invalid-face time before the state falls back to UNCERTAIN.
    uncertain_dwell_ms: int = 800
    #: Emit a heartbeat event every N ms even without a transition.
    heartbeat_ms: int = 1000
    #: Invalid frames decay the EMA toward 0.5 instead of freezing it.
    decay_on_invalid: bool = True
    #: Entry thresholds and dwells for the states only a multi-class
    #: classifier produces.  Looking away (OTHER) needs the longest evidence:
    #: a short glance off-screen while thinking is normal and not feedback.
    enter_screen_threshold: float = 0.60
    enter_other_threshold: float = 0.60
    to_screen_dwell_ms: int = 400
    to_other_dwell_ms: int = 800


@dataclass
class PreconditionConfig:
    """Set-up check that runs before calibration (runtime only).

    The whole model assumes one presenter facing a webcam mounted at the top
    centre of the screen.  Every bound here is the MINIMUM the measurement needs
    -- can the face be found and its landmarks tracked -- not an ideal posture:
    a set-up that can be measured passes.  When it clearly cannot, the take is
    refused with an actionable hint (``strict``); research tools run advisory
    and only record it.
    """

    #: True: a failing check blocks calibration.  False: report only.
    strict: bool = True
    #: Every check must hold this long, continuously, to pass.
    hold_ms: int = 1000
    #: One check failing this long, continuously, is a rejection (before that
    #: the answer is "retry": the user is probably still settling).
    reject_after_ms: int = 3000
    #: Another face larger than this fraction of the main face's area means
    #: a second person is in the shot -- once it has been seen for
    #: ``second_face_confirm_ms`` (a one-frame false find is not a person).
    #: Only a distinct face counts (``preprocess.pipeline.second_face_ratio``).
    max_second_face_area_ratio: float = 0.25
    second_face_confirm_ms: int = 500
    #: Face-centre offset from the frame centre, as a fraction of frame width
    #: and height: the face only has to stay well inside the picture.
    max_center_offset_x: float = 0.35
    max_center_offset_y: float = 0.35
    #: Too far = the face nears the size below which it cannot be found
    #: reliably: ``preprocess.min_face_area_ratio`` (0.010, the face detector's
    #: floor) with a little headroom, so leaning back does not drop frames.
    min_face_area_ratio: float = 0.012
    #: Eye-reading backbones only: iris diameter floor in source-frame pixels;
    #: below it the eye is too few pixels to resolve a ~9 deg eye rotation.  The
    #: head-pose backbone does not read the iris and ignores this.
    min_iris_px: float = 7.0
    #: Too close = the face fills so much of the picture that a small head
    #: movement takes it out of frame.
    max_face_height_ratio: float = 0.70
    #: The user should roughly face the camera while the check runs (landmarks
    #: stay reliable well within this).  Pitch is wider: a laptop webcam sits
    #: below eye level, so a presenter looking at the lens already reads
    #: 16-24 deg chin-up (measured on a laptop webcam).
    max_head_yaw_deg: float = 30.0
    max_head_pitch_deg: float = 40.0
    #: Mean face luminance floor (0-255) and the backlight ceiling: dim light
    #: still tracks; only a face too dark or too backlit to find fails.
    min_face_brightness: float = 40.0
    max_backlight_ratio: float = 2.5
    #: Analysed frames per second the stream must sustain: the 1 s vote needs 4
    #: frames, so 5 FPS leaves a little room (8 FPS is the target).
    min_analysis_fps: float = 5.0


@dataclass
class SweepConfig:
    """Head circle check between the set-up check and calibration (``vision.runtime.sweep``).

    The presenter looks straight ahead for a moment, then turns the head
    slowly around in a circle; a ring of ticks around the face fills in the
    directions the head reached while the face stayed tracked.  It checks the
    tracking in every direction and never blocks on its own.  Every number here
    is an initial value, not a measured one.
    """

    #: Ticks around the ring; a multiple of 8 so each direction owns whole ticks.
    ticks: int = 32
    #: Valid frames whose median head pose is the centre of the circle (1 s at
    #: 8 FPS).  That pose is the presenter looking at the screen -- their own
    #: face in the picture -- so it is also the screen-centre baseline the
    #: calibration confirms and folds in (``CalibrationConfig.cue_confirm_*``).
    neutral_frames: int = 8
    #: How far the head must turn from that centre to light a tick: an ellipse
    #: with these half-axes (heads turn further sideways than up and down).
    reach_yaw_deg: float = 14.0
    reach_pitch_deg: float = 9.0
    #: Turning faster than this between two frames lights nothing ("slower").
    max_speed_deg_s: float = 150.0
    #: Two turned frames in a row fill the ticks between them when they are at
    #: most this far apart in time and angle (at 8 FPS a circle skips ticks).
    max_gap_ms: int = 400
    max_fill_arc_deg: float = 90.0
    #: No new tick for this long: point at the largest gap still to fill.
    hint_after_ms: int = 3000
    #: Stop after this long; the result names the directions still missing.
    timeout_ms: int = 25000


@dataclass
class ConditionConfig:
    """Live measurement-condition monitor (runtime only).

    Compares every live frame with the scene recorded during calibration.  A
    deviation does not stop the take; it lowers ``reliability`` linearly from
    1.0 at the ``*_warn`` bound to ``fail_reliability`` at the ``*_fail`` bound.
    Reliability is the minimum over the signals, so the issue list always names
    what lowered it.
    """

    #: Window the valid-frame ratio is measured over.
    window_ms: int = 3000
    #: Re-emit an unchanged condition this often.
    heartbeat_ms: int = 2000
    #: A reliability change of at least this much is emitted immediately.
    emit_delta: float = 0.10
    #: Reliability a signal contributes at (and beyond) its fail bound.
    fail_reliability: float = 0.30
    #: Head pose drift from the calibration pose, degrees (max of yaw, pitch).
    head_warn_deg: float = 12.0
    head_fail_deg: float = 30.0
    #: Position drift: how far the head moved from where it was calibrated,
    #: read as the head-angle error it causes at a target -- atan(move / distance)
    #: for a move sideways or up/down, and the change of the farthest cue's angle
    #: for a move closer or further.  It fails at ``drift_fail_share`` x the
    #: smallest separation between the calibrated cue postures (a move that can
    #: carry a look all the way to the neighbouring target: the measurement no
    #: longer means what it meant), clamped to [``drift_fail_min_deg``,
    #: ``drift_fail_max_deg``], and warns at ``drift_warn_share`` x that bound.
    #: Lenient on purpose: presenters shift in their seat, and the classifier
    #: tolerates a few degrees (``calibration.other_margin_deg``).
    drift_fail_share: float = 1.0
    drift_fail_min_deg: float = 6.0
    drift_fail_max_deg: float = 12.0
    drift_warn_share: float = 0.6
    #: Beyond the fail bound for this long: the measurement is unusable
    #: (MOVED_TOO_FAR, severe) until the presenter returns or re-anchors.
    drift_confirm_ms: int = 2000
    #: The farthest cue's angle from the screen centre when the cues are unknown.
    drift_default_span_deg: float = 7.0
    #: Head rotation centre behind the face, cm.  Turning the head moves the face
    #: in the picture by about this x sin(angle) without the head moving; that
    #: part is taken out before the move is measured.
    head_radius_cm: float = 8.0
    #: A second face at least this large (vs the main face), seen for
    #: ``second_face_confirm_ms``, is a *notice* (``ConditionState.notices``):
    #: the presenter's face is still the one measured (FACE_REPLACED catches a
    #: switch), so it does not lower reliability.  Only a distinct face counts
    #: (``preprocess.pipeline.second_face_ratio``).
    second_face_area_ratio: float = 0.35
    second_face_confirm_ms: int = 1000
    #: Head-direction noise: the frame-to-frame scatter of the measured head
    #: angles over the last ``jitter_window_ms`` (robust second difference, so a
    #: smooth head movement is not noise).  Shaky landmarks -- poor light, a face
    #: near the recognition floor, motion blur -- show up here as they affect the
    #: measurement, whatever caused them.  It fails at ``jitter_fail_share`` x
    #: the smallest separation between the calibrated cue postures (noise that
    #: carries a frame half-way to the neighbouring target), clamped to
    #: [``jitter_fail_min_deg``, ``jitter_fail_max_deg``], and warns at
    #: ``jitter_warn_share`` x that.  Needs ``jitter_min_samples`` differences
    #: over frames at most ``jitter_max_gap_ms`` apart.
    jitter_window_ms: int = 2000
    jitter_max_gap_ms: int = 300
    jitter_min_samples: int = 6
    jitter_fail_share: float = 0.5
    jitter_fail_min_deg: float = 3.0
    jitter_fail_max_deg: float = 6.0
    jitter_warn_share: float = 0.5
    #: Valid-frame ratio over ``window_ms``.
    valid_warn_ratio: float = 0.90
    valid_fail_ratio: float = 0.50
    #: Face size against the recognition floor (``preprocess.min_face_area_ratio``
    #: 0.010): reliability falls as the face shrinks from ``small_face_warn_area``
    #: to ``small_face_fail_area`` (moving away) or grows from
    #: ``large_face_warn_height`` to ``large_face_fail_height`` of the frame
    #: (moving so close a small movement leaves the frame).
    small_face_warn_area: float = 0.016
    small_face_fail_area: float = 0.011
    large_face_warn_height: float = 0.70
    large_face_fail_height: float = 0.85
    #: The main face JUMPED this far (fraction of the frame) or changed area by
    #: this factor between two sightings (at most ``face_lost_ms`` apart), and
    #: did not come back to the calibrated place for ``replace_confirm_ms``: a
    #: different person is now being measured.  Moving away, closer or aside
    #: gradually is never a replacement -- that is the position drift.
    replace_center_offset: float = 0.30
    replace_area_ratio: float = 2.0
    replace_confirm_ms: int = 500
    #: No face for this long is a lost measurement.
    face_lost_ms: int = 1500


@dataclass
class EvidenceConfig:
    """Gaze evidence for the coach and review agents (``vision.evidence.gaze``).

    Frame decisions are cut into fixed slices (one record per second by
    default), the slices form a timeline, and the timeline is read as issues in
    the agents' common evaluator format, take summaries and intervention
    outcomes.  Every number here is an initial value, not a measured one.
    """

    #: Slice length and how a slice is decided from its frames.
    slice_ms: int = 1000
    min_frames_per_slice: int = 4
    slice_vote_threshold: float = 0.6
    #: Windows the realtime stats are read over (the coach's "recent 5-30 s").
    short_window_ms: int = 5000
    long_window_ms: int = 30000
    #: A continuous run must last ``*_min_ms`` to be an issue; severity reaches
    #: 1.0 at ``*_full_ms``.  Script reading is the most expected, so the most
    #: tolerated; looking away the least.
    script_min_ms: int = 3000
    script_full_ms: int = 10000
    screen_min_ms: int = 5000
    screen_full_ms: int = 15000
    away_min_ms: int = 2000
    away_full_ms: int = 8000
    #: Eye contact below this share of the measured time over the long window.
    low_eye_contact_ratio: float = 0.30
    low_eye_contact_min_measured_ms: int = 15000
    #: The gaze is not usable when, over the short window, the measured share
    #: or the mean condition reliability falls below these.
    unmeasurable_coverage: float = 0.5
    unmeasurable_reliability: float = 0.5
    #: Runs shorter than this are not reported as segments.
    segment_min_ms: int = 1000
    #: Intervention outcome: the target ratio over ``before_ms`` before the
    #: feedback against ``after_ms`` starting ``delay_ms`` after it.
    outcome_before_ms: int = 5000
    outcome_delay_ms: int = 5000
    outcome_after_ms: int = 5000
    #: The ratio must move the right way by this much to call it effective.
    outcome_min_change: float = 0.2


@dataclass
class ReleaseGateConfig:
    """doc 7: PoC pass line."""

    min_macro_f1: float = 0.85
    min_bottom_recall: float = 0.90
    min_per_user_f1: float = 0.75
    max_uncertain_ratio: float = 0.20
    max_calibration_failure_rate: float = 0.10
    #: 8 FPS leaves a 125 ms budget per frame.
    max_p95_latency_ms: float = 125.0


@dataclass
class ProtocolCondition:
    """One recorded block in the collection protocol (doc 4-1)."""

    key: str
    seconds: float
    #: Fixed cue for a static block, or empty when the cue alternates.
    cue: str = ""
    #: For alternating blocks: seconds each cue is held.
    alternate_period_s: float = 0.0
    speaking: bool = False
    head_motion: bool = False
    prompt: str = ""


@dataclass
class CollectionConfig:
    """doc 4-1: the guided recording protocol."""

    record_fps: int = 30
    frame_width: int = 1280
    frame_height: int = 720
    #: Frames within this window of a cue change are labelled IGNORE.
    transition_guard_ms: int = 500
    countdown_s: float = 3.0
    conditions: List[ProtocolCondition] = field(default_factory=list)


@dataclass
class VisionConfig:
    """The whole vision configuration, as one hashable object."""

    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    backbone: BackboneConfig = field(default_factory=BackboneConfig)
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    release_gate: ReleaseGateConfig = field(default_factory=ReleaseGateConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    preconditions: PreconditionConfig = field(default_factory=PreconditionConfig)
    condition: ConditionConfig = field(default_factory=ConditionConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    sweep: SweepConfig = field(default_factory=SweepConfig)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def hash(self) -> str:
        """Stable short hash over the full config (doc 18 reproducibility)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:12]

    def frame_interval_ms(self) -> float:
        return 1000.0 / float(self.preprocess.analysis_fps)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _require_mapping(section: str, data: Any) -> Dict[str, Any]:
    """Return one raw config section as a mapping, or raise naming the section.

    A missing/empty section is legitimate (it means "all defaults"), so ``None``
    becomes ``{}``.  Anything else that is not a mapping is a malformed file --
    a stray ``- `` turning ``preprocess.yaml`` into a list, a scalar where a
    block was meant.  Previously such a section was passed through unchanged and
    REPLACED the dataclass with the str/list, so the config object looked fine
    until something far away did ``cfg.preprocess.analysis_fps`` and raised an
    AttributeError that named neither the file nor the section.  Failing here
    costs the caller a traceback at load time and names both.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError(
            f"config section {section!r} must be a mapping, got "
            f"{type(data).__name__}"
        )
    return data


def _build(cls, data: Any, section: str = ""):
    """Construct a flat dataclass from a plain dict, ignoring unknown keys.

    Unknown keys are dropped rather than raising: a YAML file left over from an
    older config revision should degrade to defaults, not break every tool.  A
    section that is not a mapping at all is a different story and raises -- see
    :func:`_require_mapping`.  All sections here are flat; nested structures
    (``VisionConfig``, ``CollectionConfig.conditions``) are assembled explicitly
    in ``load_config``.

    ``section`` only names the offending section in that error; it defaults to
    the dataclass name so an ad-hoc call still says something useful.
    """
    known = {f.name for f in fields(cls)}
    raw = _require_mapping(section or cls.__name__, data)
    return cls(**{k: v for k, v in raw.items() if k in known})


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``override`` into ``base``, recursing only where BOTH sides are mappings.

    Everything else is assigned as-is, so the result can still alias values owned
    by the caller -- ``load_config`` copies the merged tree rather than making
    this function copy every leaf it touches.
    """
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(
    config_dir: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> VisionConfig:
    """Load every ``ai/configs/*.yaml`` section and merge optional overrides.

    File-to-section mapping::

        preprocess.yaml     -> preprocess
        gaze_backbone.yaml  -> backbone          (the one name that differs)
        placement.yaml      -> placement
        calibration.yaml    -> calibration
        temporal.yaml       -> temporal
        release_gate.yaml   -> release_gate
        collection.yaml     -> collection
        preconditions.yaml  -> preconditions
        condition.yaml      -> condition
        evidence.yaml       -> evidence
        sweep.yaml          -> sweep

    An ``overrides`` dict is keyed by the SECTION name, not the file name, so
    the backbone section must be given as ``{"backbone": {...}}``; a key that is
    not one of the eleven above is merged into the raw mapping and then silently
    ignored.  A missing file yields ``{}`` and ``_build`` drops unknown keys, so
    a wrong ``config_dir`` or a typo'd key produces an all-defaults config with
    a valid-looking hash rather than an error.  A file that parses to something
    other than a mapping is the one case that is NOT degraded that way: it
    raises ``TypeError`` naming the section, because it used to replace the
    whole dataclass (see :func:`_require_mapping`).

    The returned config owns its values: the merged mapping is deep-copied
    before the dataclasses are built, so nothing in it aliases ``overrides`` and
    two configs loaded from the same ``overrides`` cannot move each other's
    :meth:`VisionConfig.hash`.
    """
    directory = Path(config_dir) if config_dir else CONFIG_DIR
    raw: Dict[str, Any] = {
        "preprocess": _read_yaml(directory / "preprocess.yaml"),
        "backbone": _read_yaml(directory / "gaze_backbone.yaml"),
        "placement": _read_yaml(directory / "placement.yaml"),
        "calibration": _read_yaml(directory / "calibration.yaml"),
        "temporal": _read_yaml(directory / "temporal.yaml"),
        "release_gate": _read_yaml(directory / "release_gate.yaml"),
        "collection": _read_yaml(directory / "collection.yaml"),
        "preconditions": _read_yaml(directory / "preconditions.yaml"),
        "condition": _read_yaml(directory / "condition.yaml"),
        "evidence": _read_yaml(directory / "evidence.yaml"),
        "sweep": _read_yaml(directory / "sweep.yaml"),
    }
    if overrides:
        raw = _deep_merge(raw, overrides)

    # _deep_merge recurses only when BOTH sides are mappings, so a list -- or a
    # dict the YAML has no counterpart for -- came out of the merge as the very
    # object the caller passed in.  Two configs loaded from one ``overrides``
    # then shared it, and appending to one config's ``eye_crop_size`` silently
    # moved the OTHER config's ``hash()``, the doc 18 provenance key, without
    # that config ever being touched.  One deep copy of the merged tree here
    # gives every config its own values; ``raw`` is plain YAML data.
    raw = copy.deepcopy(raw)

    collection_raw = _require_mapping("collection", raw.get("collection"))
    conditions = [
        _build(ProtocolCondition, c, f"collection.conditions[{i}]")
        for i, c in enumerate(collection_raw.get("conditions") or [])
    ]
    collection = _build(
        CollectionConfig,
        {k: v for k, v in collection_raw.items() if k != "conditions"},
        "collection",
    )
    collection.conditions = conditions

    return VisionConfig(
        preprocess=_build(PreprocessConfig, raw.get("preprocess"), "preprocess"),
        backbone=_build(BackboneConfig, raw.get("backbone"), "backbone"),
        placement=_build(PlacementConfig, raw.get("placement"), "placement"),
        calibration=_build(CalibrationConfig, raw.get("calibration"), "calibration"),
        temporal=_build(TemporalConfig, raw.get("temporal"), "temporal"),
        release_gate=_build(ReleaseGateConfig, raw.get("release_gate"), "release_gate"),
        collection=collection,
        preconditions=_build(PreconditionConfig, raw.get("preconditions"), "preconditions"),
        condition=_build(ConditionConfig, raw.get("condition"), "condition"),
        evidence=_build(EvidenceConfig, raw.get("evidence"), "evidence"),
        sweep=_build(SweepConfig, raw.get("sweep"), "sweep"),
    )


def resolve_path(path_like: str) -> Path:
    """Resolve a config path that may be repo-relative or absolute."""
    p = Path(path_like)
    return p if p.is_absolute() else (REPO_ROOT / p)
