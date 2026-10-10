"""시선 근거(1초 기록 · 이슈 · 요약)의 임계값.

research 에서는 configs/evidence.yaml 이 이 값을 덮어쓰고(gaze_lab.config.load_config),
서버(service)는 파일을 읽지 않고 이 기본값을 그대로 쓴다. 그래서 두 값이 같아야 하고,
tests/lab/test_core_sync.py 가 YAML 과 이 기본값이 같은지 검사한다.
임계값을 바꿀 때는 YAML 과 이 파일을 같이 고친다.

원본: ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/ai/src/vision/config.py (EvidenceConfig)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvidenceConfig:
    """Gaze evidence for the coach and review agents (``gaze.core``).

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
