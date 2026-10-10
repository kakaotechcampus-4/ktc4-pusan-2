"""코치가 쓰는 고정 어휘 — enum 과 문제 코드의 성질.

여기 값은 **BE · FE · 리뷰 에이전트와 공유하는 계약**입니다. 이름을 바꾸거나 값을 빼면
소비자가 깨지므로 모델 버전(v2)을 올려야 합니다. 값을 **추가**하는 것은 v1 안에서 가능합니다.
"""

from __future__ import annotations

from enum import StrEnum


class FeedbackType(StrEnum):
    """원인 영역. 리뷰의 ReviewPoint.type · Mission.type 과 같은 enum 입니다."""

    GAZE = "GAZE"
    SPEED = "SPEED"
    VOLUME = "VOLUME"
    PAUSE = "PAUSE"
    FILLER = "FILLER"
    CONTENT = "CONTENT"
    TIME = "TIME"


class Instruction(StrEnum):
    """발표자에게 하라는 행동. 같은 type 이라도 상황에 따라 다르다 (TIME → SPEED_UP · WRAP_UP)."""

    LOOK_AT_CAMERA = "LOOK_AT_CAMERA"
    SLOW_DOWN = "SLOW_DOWN"
    SPEED_UP = "SPEED_UP"
    SPEAK_LOUDER = "SPEAK_LOUDER"
    RESUME = "RESUME"
    REDUCE_FILLER = "REDUCE_FILLER"
    CONDENSE = "CONDENSE"
    MOVE_ON = "MOVE_ON"
    WRAP_UP = "WRAP_UP"
    CONTINUE = "CONTINUE"


class Action(StrEnum):
    WAIT = "WAIT"  # 지금은 말하지 않고 계속 본다 (문제가 없거나, 타이밍을 기다리는 중)
    IGNORE = "IGNORE"  # 문제는 있지만 지금 말할 가치가 낮아 이번엔 버린다
    INTERVENE = "INTERVENE"  # 지금 말한다


class Mode(StrEnum):
    COACHING = "COACHING"
    EXAM = "EXAM"


class Issue(StrEnum):
    """평가기가 찾는 문제. 후보 하나는 문제 하나에서 나온다."""

    GAZE_ON_SCRIPT = "GAZE_ON_SCRIPT"
    GAZE_AWAY = "GAZE_AWAY"
    GAZE_LOW_EYE_CONTACT = "GAZE_LOW_EYE_CONTACT"
    #: 아래 둘과 PACE_SLOW 는 말하지 않고 문제 구간에만 남긴다 (config 의 빈 사다리)
    GAZE_ON_SCREEN = "GAZE_ON_SCREEN"
    GAZE_UNMEASURABLE = "GAZE_UNMEASURABLE"
    PACE_FAST = "PACE_FAST"
    PACE_SLOW = "PACE_SLOW"
    VOLUME_LOW = "VOLUME_LOW"
    LONG_SILENCE = "LONG_SILENCE"
    FILLER_FREQUENT = "FILLER_FREQUENT"
    BEHIND_SCHEDULE = "BEHIND_SCHEDULE"
    AHEAD_OF_SCHEDULE = "AHEAD_OF_SCHEDULE"
    SLIDE_OVER = "SLIDE_OVER"
    FINAL_MINUTE = "FINAL_MINUTE"
    TIME_OVER = "TIME_OVER"
    IMPROVED_AFTER_FEEDBACK = "IMPROVED_AFTER_FEEDBACK"


class CandidateStatus(StrEnum):
    SELECTED = "SELECTED"  # 이번에 말한 후보
    OUTRANKED = "OUTRANKED"  # 말할 수 있었지만 점수에서 밀림 — 다음 기회에 다시 경쟁
    WAITING = "WAITING"  # 타이밍 때문에 보류 (지속시간 미달, 간격, 쿨다운, 문장 끝 대기)
    IGNORED = "IGNORED"  # 가치가 없어 버림 (실전 모드, 센서 불량, 낮은 신뢰도 …)


class Outcome(StrEnum):
    EFFECTIVE = "EFFECTIVE"
    INEFFECTIVE = "INEFFECTIVE"
    NOT_MEASURED = "NOT_MEASURED"


class Reason(StrEnum):
    # ── 개입한 이유 (INTERVENE) ──────────────────────────────────────────
    PERSISTENT = "PERSISTENT"
    WORSENING = "WORSENING"
    MISSION_RELEVANT = "MISSION_RELEVANT"
    MISSION_AT_RISK = "MISSION_AT_RISK"
    RECURRING = "RECURRING"
    PLAN_FOCUS = "PLAN_FOCUS"
    TIME_CRITICAL = "TIME_CRITICAL"
    SPEED_LIMIT_EXCEEDED = "SPEED_LIMIT_EXCEEDED"
    ESCALATED = "ESCALATED"
    AFTER_EFFECTIVE_FEEDBACK = "AFTER_EFFECTIVE_FEEDBACK"
    PAUSE_TIMEOUT = "PAUSE_TIMEOUT"
    # ── 기다린 이유 (WAIT) ───────────────────────────────────────────────
    NO_CANDIDATE = "NO_CANDIDATE"
    NOT_PERSISTENT = "NOT_PERSISTENT"
    MIN_GAP = "MIN_GAP"
    COOLDOWN = "COOLDOWN"
    WAITING_FOR_PAUSE = "WAITING_FOR_PAUSE"
    STALE_TICK = "STALE_TICK"
    # ── 버린 이유 (IGNORE) ───────────────────────────────────────────────
    EXAM_MODE = "EXAM_MODE"
    SENSOR_UNUSABLE = "SENSOR_UNUSABLE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    SCRIPT_ALLOWED = "SCRIPT_ALLOWED"
    TIME_PRESSURE = "TIME_PRESSURE"
    STRATEGY_EXHAUSTED = "STRATEGY_EXHAUSTED"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"
    PLAN_RELAXED = "PLAN_RELAXED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    LOW_PRIORITY = "LOW_PRIORITY"
    # ── 운영 ─────────────────────────────────────────────────────────────
    INTERNAL_ERROR = "INTERNAL_ERROR"
    STATE_RESET = "STATE_RESET"


#: 기다리면 다시 기회가 오는 이유. 하나라도 걸리면 후보는 WAITING 이다.
WAIT_REASONS: frozenset[Reason] = frozenset(
    {Reason.NOT_PERSISTENT, Reason.MIN_GAP, Reason.COOLDOWN, Reason.WAITING_FOR_PAUSE}
)

#: 기다려도 소용없는 이유. 하나라도 걸리면 후보는 IGNORED 이다 (WAITING 보다 우선).
IGNORE_REASONS: frozenset[Reason] = frozenset(
    {
        Reason.EXAM_MODE,
        Reason.SENSOR_UNUSABLE,
        Reason.LOW_CONFIDENCE,
        Reason.SCRIPT_ALLOWED,
        Reason.TIME_PRESSURE,
        Reason.STRATEGY_EXHAUSTED,
        Reason.ALREADY_DELIVERED,
        Reason.PLAN_RELAXED,
        Reason.BUDGET_EXHAUSTED,
        Reason.LOW_PRIORITY,
    }
)


class StrategyChange(StrEnum):
    ESCALATED = "ESCALATED"  # 같은 말이 안 통해서 다른 방법으로 바꿈
    GAVE_UP = "GAVE_UP"  # 방법을 다 써서 그 범위에서는 그만둠


#: 문제 → 원인 영역. IMPROVED_AFTER_FEEDBACK 는 교정했던 영역을 그대로 물려받는다.
ISSUE_TYPE: dict[Issue, FeedbackType] = {
    Issue.GAZE_ON_SCRIPT: FeedbackType.GAZE,
    Issue.GAZE_AWAY: FeedbackType.GAZE,
    Issue.GAZE_LOW_EYE_CONTACT: FeedbackType.GAZE,
    Issue.GAZE_ON_SCREEN: FeedbackType.GAZE,
    Issue.GAZE_UNMEASURABLE: FeedbackType.GAZE,
    Issue.PACE_FAST: FeedbackType.SPEED,
    Issue.PACE_SLOW: FeedbackType.SPEED,
    Issue.VOLUME_LOW: FeedbackType.VOLUME,
    Issue.LONG_SILENCE: FeedbackType.PAUSE,
    Issue.FILLER_FREQUENT: FeedbackType.FILLER,
    Issue.BEHIND_SCHEDULE: FeedbackType.TIME,
    Issue.AHEAD_OF_SCHEDULE: FeedbackType.TIME,
    Issue.SLIDE_OVER: FeedbackType.TIME,
    Issue.FINAL_MINUTE: FeedbackType.TIME,
    Issue.TIME_OVER: FeedbackType.TIME,
}

#: 슬라이드마다 따로 보는 문제. 전략(사다리 단계 · 포기)도 슬라이드마다 새로 시작한다.
#: 4번 장에서 시선 지적을 포기했어도 5번 장에서는 다시 시도한다.
SLIDE_SCOPED: frozenset[Issue] = frozenset(
    {
        Issue.GAZE_ON_SCRIPT,
        Issue.GAZE_AWAY,
        Issue.GAZE_LOW_EYE_CONTACT,
        Issue.GAZE_ON_SCREEN,
        Issue.GAZE_UNMEASURABLE,
        Issue.SLIDE_OVER,
    }
)

#: 사다리를 나눠 쓰는 문제 → 사다리 이름. 세 문제가 함께 걸려 같은 말을 두 갈래로 올리지 않게
#: 장마다 사다리 하나를 같이 쓴다. 문제 구간(episode)은 문제마다 따로 센다.
SHARED_LADDER: dict[Issue, str] = {
    Issue.GAZE_ON_SCRIPT: "GAZE",
    Issue.GAZE_AWAY: "GAZE",
    Issue.GAZE_LOW_EYE_CONTACT: "GAZE",
}

#: 점수가 같을 때의 순서. 앞일수록 먼저다 — 결과가 실행마다 달라지지 않게 하려는 것뿐이다.
ISSUE_ORDER: tuple[Issue, ...] = (
    Issue.TIME_OVER,
    Issue.FINAL_MINUTE,
    Issue.BEHIND_SCHEDULE,
    Issue.SLIDE_OVER,
    Issue.GAZE_ON_SCRIPT,
    Issue.GAZE_AWAY,
    Issue.GAZE_LOW_EYE_CONTACT,
    Issue.PACE_FAST,
    Issue.VOLUME_LOW,
    Issue.LONG_SILENCE,
    Issue.FILLER_FREQUENT,
    Issue.AHEAD_OF_SCHEDULE,
    Issue.GAZE_ON_SCREEN,
    Issue.GAZE_UNMEASURABLE,
    Issue.PACE_SLOW,
    Issue.IMPROVED_AFTER_FEEDBACK,
)
