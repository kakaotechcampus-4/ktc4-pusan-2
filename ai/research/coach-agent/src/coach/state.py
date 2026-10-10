"""coach_state — 코치의 기억.

코치는 아무것도 저장하지 않습니다. 기억은 매 응답에 담겨 나가고, BE 가 다음 요청에 그대로
붙여 돌려보냅니다. 그래서 AI 가 재시작되거나 다른 worker 가 요청을 받아도 판단이 같습니다.

BE 는 이 모양을 몰라도 됩니다. 모양이 바뀌면 STATE_VERSION 을 올리고, 다른 버전이 오면
버리고 새로 시작합니다 (쿨다운이 한 번 풀리는 정도의 손해).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .version import STATE_VERSION
from .vocab import SHARED_LADDER, SLIDE_SCOPED, FeedbackType, Issue


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistorySample(_S):
    """최근 60초를 1초 단위로 남긴 것. 지속 · 악화 · 개입 전후 비교에 쓴다."""

    t_ms: int
    script_ratio: float | None = None
    script_ratio_short: float | None = None
    away_ratio_short: float | None = None
    audience_ratio_short: float | None = None
    cpm: float | None = None
    cpm_short: float | None = None
    voice_diff_db: float | None = None
    speaking: bool | None = None
    required_ratio: float | None = None
    recent_filler_count: float | None = None


class EpisodeState(_S):
    candidate_id: str
    issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    start_ms: int
    last_seen_ms: int
    peak_severity: float = 0.0
    intervention_ids: list[str] = Field(default_factory=list)
    suppressed_reasons: list[str] = Field(default_factory=list)
    #: 센서를 믿을 수 있던 / 없던 시간. 리뷰가 근거 없는 지적을 하지 않게 한다
    reliable_ms: int = 0
    unreliable_ms: int = 0
    #: 믿을 수 있는 상태가 이어지기 시작한 시각. 지속시간은 여기서부터 잰다
    reliable_since_ms: int | None = None
    #: 심각도 × 시간의 합. 평균 심각도 = severity_ms / (reliable_ms + unreliable_ms)
    severity_ms: float = 0.0


class SlideVisit(_S):
    """장 방문 하나. 방문이 끝나고 그 장의 단어가 다 확정되면 SLIDE 이벤트가 되고 여기서 빠진다.

    tally 는 그 방문에 속한 조각의 합이다(영역 → 이름 → 합). 단어 조각은 말한 시각의 방문에
    들어가므로, 확정이 늦은 단어도 말한 장 방문에 붙는다.
    """

    slide_number: int
    start_ms: int
    #: 지금 방문 중이면 null (null 은 보내지 않으므로 기본값이 있어야 한다)
    end_ms: int | None = None
    #: 대본 분석이 준 이 장의 목표 시간. 계획에 없으면 null
    target_ms: int | None = None
    tally: dict[str, dict[str, float]] = Field(default_factory=dict)


class StrategyState(_S):
    #: 사다리에서 지금 쓰는 칸
    step: int = 0
    failures: int = 0
    exhausted: bool = False
    fires: int = 0


class PendingOutcome(_S):
    """효과를 재기로 한 개입."""

    intervention_id: str
    issue_type: Issue
    area: FeedbackType
    step: int
    strategy_key: str
    slide_number: int | None = None
    t_ms: int
    check_at_ms: int
    metric: str | None = None
    before: float | None = None


class Praise(_S):
    """효과가 있던 개입 — 유지 격려(CONTINUE) 후보가 된다."""

    intervention_id: str
    source_issue_type: Issue
    area: FeedbackType
    slide_number: int | None = None
    intervention_t_ms: int
    expires_ms: int
    metric: str | None = None
    before: float | None = None
    after: float | None = None


class Hold(_S):
    """문장 끝을 기다리는 중인 후보."""

    candidate_id: str
    since_ms: int


class Cursor(_S):
    """판정 모듈 하나의 커서. 여기까지는 이미 셌다."""

    since_ms: int = 0
    words_since_ms: int = -1


class CoachState(_S):
    v: int = STATE_VERSION
    last_t_ms: int | None = None

    history: list[HistorySample] = Field(default_factory=list)
    #: 열린 문제 구간. 키 = episode_key (문제코드, 슬라이드 단위면 "문제코드:장번호")
    episodes: dict[str, EpisodeState] = Field(default_factory=dict)

    last_fired_ms: int | None = None
    last_by_instruction: dict[str, int] = Field(default_factory=dict)
    fires_by_issue: dict[str, int] = Field(default_factory=dict)
    interventions: int = 0

    pending: list[PendingOutcome] = Field(default_factory=list)
    strategy: dict[str, StrategyState] = Field(default_factory=dict)
    praise: list[Praise] = Field(default_factory=list)
    hold: Hold | None = None

    slide_number: int | None = None
    #: 최근 장 전환 [(장, 시작 시각)]. 확정이 늦게 온 단어를 말한 시각의 장에 붙인다
    slide_log: list[tuple[int, int]] = Field(default_factory=list)
    #: STT 가 불량(상태 ≠ ok 또는 오디오 정지)인 중인가, 그리고 불량에서 마지막으로 돌아온 시각.
    #: 돌아오기 전 단어는 속도 · 군더더기 계산에 쓰지 않는다. STT 입력이 아예 없던 것은 불량이
    #: 아니다
    stt_gap: bool = False
    stt_ok_since_ms: int | None = None
    #: 군더더기 효과를 재려고 남기는 최근 군더더기 수 [[말한 시각, 수]]. 단어는 늦게 확정되므로
    #: 효과를 잴 때 구간별로 다시 센다. 효과 판정에 필요한 만큼만 남긴다
    filler_times: list[list[int]] = Field(default_factory=list)
    #: 군더더기 수를 믿을 수 있던 시간 [[시작, 끝]] (합쳐 둠). STT 를 믿을 수 있고 군더더기 판정이
    #: 성공해 잴 수 있던 1초들이다. 효과를 재는 구간이 이 안에 다 들지 않으면 재지 못한 것으로 둔다
    filler_ok: list[list[int]] = Field(default_factory=list)

    #: 문제별 마지막 '참은 기록' 시각
    suppress_log: dict[str, int] = Field(default_factory=dict)
    #: 모듈 이름(gaze · pace · volume · filler · timing) → 커서
    cursors: dict[str, Cursor] = Field(default_factory=dict)
    #: 판정 결과 tally 의 누적: 영역 → 이름 → 합. slide_totals 는 장 번호(문자열)별 같은 모양
    totals: dict[str, dict[str, float]] = Field(default_factory=dict)
    slide_totals: dict[str, dict[str, dict[str, float]]] = Field(default_factory=dict)
    #: 장 번호(문자열) → [첫 방문 시작, 마지막 방문 끝]. 다시 온 장은 하나로 합친다
    slide_spans: dict[str, list[int]] = Field(default_factory=dict)
    #: 장 번호별로 STT 를 믿을 수 있던 시간 (timing 이 글자 수로 진행도를 쟤도 되는지 정한다)
    slide_stt_ok_ms: dict[str, int] = Field(default_factory=dict)
    #: 기준 음량(dBFS)과 출처(CALIBRATION · TAKE), 잡기 전까지 모은 말한 1초의 레벨
    base_level_db: float | None = None
    base_level_source: str | None = None
    baseline_samples: list[float] = Field(default_factory=list)
    #: 모듈별 criteria_version. 처음 본 것과 가장 최근 것
    criteria_versions: dict[str, str] = Field(default_factory=dict)
    latest_criteria_versions: dict[str, str] = Field(default_factory=dict)
    #: 코치가 센 시간 구간 [시작, 끝] (합쳐 둠). 비면 Take 복구(#151)가 그 시간을 다시 요청한다
    covered: list[list[int]] = Field(default_factory=list)
    #: 아직 SLIDE 이벤트를 내지 않은 장 방문(시간순). 마지막이 지금 방문 중인 장이다
    visits: list[SlideVisit] = Field(default_factory=list)


def initial_state() -> CoachState:
    """Take 의 첫 기억. 코칭 계획은 state 에 두지 않고 요청의 coaching_plan 으로 받는다."""
    return CoachState()


def load_state(raw: dict[str, Any] | None) -> tuple[CoachState, bool]:
    """(state, reset). 버전이 다르거나 모양이 깨졌으면 새로 시작하고 reset=True."""
    if raw is None:
        return initial_state(), False
    if raw.get("v") != STATE_VERSION:
        return initial_state(), True
    try:
        return CoachState.model_validate(raw), False
    except ValidationError:
        return initial_state(), True


def dump_state(state: CoachState) -> dict[str, Any]:
    # null 은 빼고 보낸다 — 모든 null 필드의 기본값이 null 이라 되읽을 때 그대로 복원된다.
    # exclude_defaults 는 쓰지 않는다: 기본값인 v 까지 빠져 버전 확인이 깨진다
    return state.model_dump(mode="json", exclude_none=True)


def _scoped(name: str, issue: Issue, slide_number: int | None) -> str:
    if issue in SLIDE_SCOPED and slide_number is not None:
        return f"{name}:{slide_number}"
    return name


def episode_key(issue: Issue, slide_number: int | None) -> str:
    """문제 구간 · 참은 기록의 키. 문제마다 따로다 (슬라이드 단위 문제면 "문제코드:장번호")."""
    return _scoped(issue.value, issue, slide_number)


def ladder_name(issue: Issue) -> str:
    """문제가 쓰는 사다리 이름. 사다리를 나눠 쓰는 문제는 같은 이름이다."""
    return SHARED_LADDER.get(issue, issue.value)


def strategy_key(issue: Issue, slide_number: int | None) -> str:
    """사다리 단계 · 포기 · 개입 횟수의 키. 사다리를 나눠 쓰는 문제는 같은 키를 쓴다."""
    return _scoped(ladder_name(issue), issue, slide_number)
