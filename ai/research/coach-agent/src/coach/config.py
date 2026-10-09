"""코치 설정 — 기준값 · 가중치 · 문구 템플릿 · 전략 사다리.

**모든 숫자는 잠정입니다.** 실제 발표 데이터로 검증하지 않았습니다. 출처는 셋입니다.

- FE 1단 코치(`frontend/.../useCoach.ts`)의 값 — 침묵 5초, 대본 응시 70%, 15초 간격, 60초 쿨다운
- stt-live v1 노트북 — CPM 275 / 350
- 그 밖의 값 — 손으로 고른 시작점. 재생 평가(coach_lab.replay)로 조정합니다

기본값은 이 파일에만 있습니다. 바꿀 때는 코드를 고치지 말고 바꿀 값만 덮어쓰세요 (load_config).
코어는 설정 파일을 읽지 않습니다 — research 에서는 coach_lab.replay --config 가 JSON 을 읽습니다.
응답 meta 의 criteria_versions.coach 가 어떤 설정으로 판단했는지를 기록합니다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .vocab import FeedbackType, Instruction, Issue


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimingConfig(_Section):
    #: r = 남은 내용 시간 / 남은 시간. 1 에 딱 붙어 깜빡이지 않게 약간 띄운다
    behind_ratio: float = 1.05
    behind_ratio_bad: float = 1.5
    #: 속도를 몰라 SPEED_UP / CONDENSE 를 못 고를 때, 이보다 늦으면 CONDENSE
    condense_ratio_without_pace: float = 1.3
    #: 허용 최소 시간(min_ms)이 없을 때 '너무 일찍 끝남'의 기준 = 목표 × 이 값
    early_end_ratio: float = 0.85
    #: 초반에는 진행도 추정이 흔들려서 '빠르다' 판단을 미룬다
    ahead_min_elapsed_ratio: float = 0.2
    slide_over_factor: float = 1.5
    slide_over_bad_factor: float = 2.5
    final_minute_ms: int = 60_000
    #: 진행도를 말한 글자 수로 쟀을 때 / 시간으로 대신 쟀을 때의 신뢰도
    progress_confidence_chars: float = 0.9
    progress_confidence_time: float = 0.6
    #: projected_end_ratio(예상 종료 ÷ 이르다고 볼 시각)가 이 값이면 심각도 1.0
    ahead_bad_ratio: float = 0.82
    #: 남은 시간이 이 값이면 심각도 1.0
    final_minute_bad_ms: int = 0
    #: 경과 ÷ 허용 최대가 이 값이면 심각도 1.0
    time_over_bad_ratio: float = 1.05
    #: 남은 장이 이만큼 이상일 때만 마무리 안내한다 (모르면 통과)
    final_minute_min_slides: int = 2


def criteria_version(feature_version: str, section: BaseModel) -> str:
    """<feature_version>+<설정 해시 12자리>. 기준값을 바꾸면 해시가 바뀐다."""
    blob = json.dumps(section.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{feature_version}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"


class PolicyConfig(_Section):
    min_gap_ms: int = 15_000
    cooldown_ms: int = 60_000
    min_confidence: float = 0.6
    #: 1등 후보가 이 점수(0~100) 미만이면 IGNORE
    intervene_threshold: int = 40
    #: 문장 끝을 기다리는 최대 시간
    pause_wait_max_ms: int = 3_000
    #: 침묵이 이만큼이면 '말이 끊긴 틈'으로 본다
    pause_silence_ms: int = 300
    #: Deepgram 문장 끝 신호가 이 안에 있었으면 틈으로 본다
    utterance_end_recent_ms: int = 1_000
    #: 문제가 이만큼 안 보여야 구간을 닫는다. 1~2초 깜빡임으로 지속시간이 끊기지 않게
    episode_gap_ms: int = 3_000
    #: 이보다 짧고 개입도 없던 구간은 리뷰로 넘기지 않는다
    min_episode_ms: int = 2_000
    history_ms: int = 60_000
    #: 같은 문제의 '참은 기록'을 남기는 최소 간격. FE 의 SUPPRESS_LOG_GAP_MS 와 같은 값
    suppress_log_gap_ms: int = 10_000

    persistence_bonus_max: float = 0.3
    persistence_bonus_full_ms: int = 15_000
    mission_weight: float = 1.3
    mission_at_risk_weight: float = 1.5
    recurrence_weight: float = 1.2
    novelty_decay: float = 0.15
    novelty_floor: float = 0.55
    time_weight_max: float = 1.5
    worsening_weight: float = 1.1
    worsening_window_ms: int = 30_000
    #: 지표가 이만큼 나빠지면 WORSENING. 부호가 '나빠지는 방향'이다
    worsening_delta: dict[str, float] = Field(
        default_factory=lambda: {
            "script_ratio": 0.15,
            "cpm": 30.0,
            "voice_diff_db": -3.0,
            "required_ratio": 0.1,
            "recent_filler_count": 3.0,
        }
    )
    #: 계획(v1.2 LLM)이 주는 가중치를 이 범위로 자른다
    plan_weight_min: float = 0.5
    plan_weight_max: float = 2.0
    #: 유지 격려(CONTINUE)는 다른 어떤 지적보다 낮게
    praise_severity: float = 0.45
    praise_ttl_ms: int = 20_000
    #: 첫 요청의 시간 가중치, 그리고 재연결처럼 요청이 끊겼을 때 한 틱이 차지할 수 있는 최대 시간
    default_tick_ms: int = 1_000
    max_tick_gap_ms: int = 2_000


class ReflectionConfig(_Section):
    """개입 효과를 '효과 있음'으로 볼 기준. 기준선으로 돌아왔거나, 이만큼 좋아졌으면 효과."""

    #: 시선은 '기준선으로 돌아옴'만 효과로 인정하고, 기준보다 이만큼 더 내려와야 한다.
    #: '얼마나 줄었나'는 쓰지 않는다 — 개입은 측정값이 잡음으로 튄 순간에 일어나기 쉬워
    #: 그 뒤엔 저절로 내려오기 때문이다(평균으로의 회귀, 실험 03 · 14)
    gaze_back_ratio: float = 0.6
    #: 다른 곳 보기(GAZE_AWAY)는 이 비율 아래로 내려와야 효과로 본다. 판정 모듈이 생기면 실험으로
    #: 정한다
    gaze_away_back_ratio: float = 0.2
    #: 청중 응시(GAZE_LOW_EYE_CONTACT)는 탐지 기준보다 이만큼 더 올라와야 효과로 본다
    gaze_contact_margin: float = 0.1
    #: 효과 전후 값은 이 시간의 평균으로 잰다
    average_ms: int = 3_000
    cpm_drop_ratio: float = 0.1
    filler_drop_ratio: float = 0.5
    schedule_delta: float = 0.05


class Features(_Section):
    praise: bool = True
    pause_wait: bool = True


class Step(_Section):
    instruction: Instruction
    variant: str = "default"


class IssueRule(_Section):
    #: 효과가 없을 때마다 한 칸씩 내려간다. 비어 있으면 말하지 않고 문제 구간에만 남긴다
    ladder: list[Step]
    #: 사다리 끝에서도 효과가 없으면 그 범위에서 그만둔다. False 면 마지막 칸에 머문다
    exhaustible: bool = True
    #: 문제가 이만큼 이어져야 말한다
    persistence_ms: int = 0
    #: 개입 뒤 효과를 재는 시점. None 이면 재지 않는다
    outcome_delay_ms: int | None = None
    #: 범위(전략 키)마다 최대 개입 횟수
    max_fires: int | None = None
    #: 효과가 있으면 CONTINUE 로 격려한다
    praise: bool = False

    @property
    def record_only(self) -> bool:
        """사다리가 비어 있다 — 후보를 만들지 않고 기록만 한다."""
        return not self.ladder


def _ladder(*steps: tuple[Instruction, str]) -> list[Step]:
    return [Step(instruction=i, variant=v) for i, v in steps]


def _default_issue_rules() -> dict[Issue, IssueRule]:
    I = Instruction  # noqa: E741 — 표가 한눈에 들어오게

    def gaze() -> IssueRule:
        # 시선 비율은 FE 의 10초 창이라 반응(약 2초 뒤)이 창을 다 채우는 12초 뒤에 잰다 (실험 03 ·
        # 14). 코치가 말하는 세 시선 문제는 같은 정의에서 만들어 서로 어긋나지 않게 한다
        return IssueRule(
            ladder=_ladder((I.LOOK_AT_CAMERA, "default"), (I.LOOK_AT_CAMERA, "sentence_start")),
            persistence_ms=3_000,
            outcome_delay_ms=12_000,
            praise=True,
        )

    return {
        Issue.GAZE_ON_SCRIPT: gaze(),
        Issue.GAZE_AWAY: gaze(),
        Issue.GAZE_LOW_EYE_CONTACT: gaze(),
        # 아래 셋은 말하지 않고 문제 구간에만 남긴다. GAZE_ON_SCREEN 은 슬라이드를 가리키며 설명하는
        # 정상 행동과 가릴 근거가 없고, GAZE_UNMEASURABLE 은 측정 불가 신호다
        Issue.GAZE_ON_SCREEN: IssueRule(ladder=[]),
        Issue.GAZE_UNMEASURABLE: IssueRule(ladder=[]),
        Issue.PACE_SLOW: IssueRule(ladder=[]),
        Issue.PACE_FAST: IssueRule(
            ladder=_ladder((I.SLOW_DOWN, "default"), (I.SLOW_DOWN, "pause_at_end")),
            persistence_ms=5_000,
            outcome_delay_ms=10_000,
            praise=True,
        ),
        Issue.VOLUME_LOW: IssueRule(
            ladder=_ladder((I.SPEAK_LOUDER, "default"), (I.SPEAK_LOUDER, "project")),
            persistence_ms=5_000,
            outcome_delay_ms=8_000,
            praise=True,
        ),
        Issue.LONG_SILENCE: IssueRule(
            ladder=_ladder((I.RESUME, "default")),
            exhaustible=False,
            outcome_delay_ms=5_000,
        ),
        Issue.FILLER_FREQUENT: IssueRule(
            ladder=_ladder((I.REDUCE_FILLER, "default"), (I.REDUCE_FILLER, "breathe")),
            outcome_delay_ms=30_000,
            praise=True,
        ),
        Issue.BEHIND_SCHEDULE: IssueRule(
            ladder=_ladder(
                (I.SPEED_UP, "default"), (I.CONDENSE, "default"), (I.WRAP_UP, "default")
            ),
            exhaustible=False,
            outcome_delay_ms=15_000,
        ),
        # 예상 종료는 천천히 움직이는 신호라 몇 초짜리 급가속에 반응하지 않게 10초를 본다
        Issue.AHEAD_OF_SCHEDULE: IssueRule(
            ladder=_ladder((I.SLOW_DOWN, "time_ahead")),
            persistence_ms=10_000,
            outcome_delay_ms=10_000,
        ),
        Issue.SLIDE_OVER: IssueRule(
            ladder=_ladder((I.MOVE_ON, "default"), (I.MOVE_ON, "firm")),
            outcome_delay_ms=15_000,
        ),
        Issue.FINAL_MINUTE: IssueRule(ladder=_ladder((I.WRAP_UP, "final_minute")), max_fires=1),
        Issue.TIME_OVER: IssueRule(ladder=_ladder((I.WRAP_UP, "time_over")), max_fires=1),
        Issue.IMPROVED_AFTER_FEEDBACK: IssueRule(ladder=_ladder((I.CONTINUE, "default"))),
    }


#: 화면 문구. 키는 "INSTRUCTION.variant". {이름} 자리는 렌더러가 숫자로 채운다.
#: LLM 이 문장을 만들지 않는 이유: 발표 중 화면 문장이 매번 달라지거나 엉뚱한 말이 뜨면 안 된다.
_TEMPLATES: dict[str, str] = {
    "LOOK_AT_CAMERA.default": "대본보다 청중을 조금 더 바라보세요",
    "LOOK_AT_CAMERA.sentence_start": "문장을 시작할 때만이라도 고개를 들어 청중을 보세요",
    "SLOW_DOWN.default": "조금 천천히 말해 보세요",
    "SLOW_DOWN.pause_at_end": "문장 끝에서 한 박자 쉬고 이어가 보세요",
    "SLOW_DOWN.time_ahead": "시간 여유가 있어요. 천천히 말해도 돼요",
    "SPEAK_LOUDER.default": "목소리를 조금 더 크게 내 보세요",
    "SPEAK_LOUDER.project": "뒷자리 청중에게 말한다고 생각하고 소리를 키워 보세요",
    "RESUME.default": "다음 문장으로 이어가 보세요",
    "REDUCE_FILLER.default": "'음' 대신 잠시 호흡하고 이어가세요",
    "REDUCE_FILLER.breathe": "말을 고를 땐 소리 내지 말고 잠깐 멈춰 보세요",
    "SPEED_UP.default": "조금만 빠르게 — 남은 {remaining_slides}장, {remaining_time}",
    "CONDENSE.default": "핵심만 말하고 넘어가세요 — 남은 {remaining_slides}장, {remaining_time}",
    "MOVE_ON.default": "이 장은 목표보다 {over_time} 넘었어요 — 정리하고 다음 장으로",
    "MOVE_ON.firm": "지금 다음 장으로 넘어가세요",
    "WRAP_UP.default": "결론으로 넘어가 마무리하세요",
    "WRAP_UP.final_minute": "남은 시간이 짧으니 결론으로 넘어가세요",
    "WRAP_UP.time_over": "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요",
    "CONTINUE.default": "좋아요, 지금처럼 이어가세요",
}


class PlannerConfig(_Section):
    """Take 시작 전 코칭 계획(planner.py). LLM 이 낸 계획은 이 범위로 자른다."""

    #: 장 단위로 봐줄 수 있는 영역. 시간 · 속도 · 음량 · 침묵은 늘 챙긴다
    relax_types: list[FeedbackType] = Field(
        default_factory=lambda: [FeedbackType.GAZE, FeedbackType.FILLER, FeedbackType.CONTENT]
    )
    max_focus: int = 3
    max_relax: int = 3
    #: 개입 상한을 이보다 작게 잡지 못한다 — 코칭을 지나치게 아끼지 않게
    min_interventions: int = 5
    #: LLM 에 넘길 장 대본 글자 수 상한 (장마다)
    max_script_chars: int = 1_500
    #: 직전 리뷰 근거에서 넘길 문제 수
    max_previous_issues: int = 5


class TakeResultConfig(_Section):
    """Take 결과(finalize)를 만드는 규칙."""

    #: 영역의 Take 측정 비율이 이보다 낮으면 그 영역의 값을 비우고 이유를 단다. 장도 같은 기준으로
    #: 그 장의 값만 비운다
    min_measured_ratio: float = 0.5
    #: 센서를 믿을 수 있던 시간 비율이 이보다 낮은 문제 구간은 reliable=false 로 표시한다
    min_reliability: float = 0.5
    #: 같은 문제 · 같은 장 · 같은 신뢰도의 구간이 이 간격 안에서 다시 시작되면 한 구간으로 합친다
    merge_gap_ms: int = 10_000
    #: 합친 구간이 이보다 짧고 개입도 없었으면 잡음 깜빡임으로 보고 뺀다 (판정 기준 시각 보정 전
    #: 길이로 잰다)
    min_segment_ms: int = 3_000


class CoachConfig(_Section):
    timing: TimingConfig = Field(default_factory=TimingConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)
    features: Features = Field(default_factory=Features)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    take_result: TakeResultConfig = Field(default_factory=TakeResultConfig)
    issues: dict[Issue, IssueRule] = Field(default_factory=_default_issue_rules)
    templates: dict[str, str] = Field(default_factory=lambda: dict(_TEMPLATES))

    _hash: str | None = PrivateAttr(default=None)

    def config_hash(self) -> str:
        """재현성 키. 같은 hash 면 같은 입력에 같은 판단이 나온다.

        설정은 만든 뒤 바꾸지 않는다는 전제로 한 번만 계산한다 (매초 응답마다 부른다).
        """
        if self._hash is None:
            blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            self._hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        return self._hash


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(**override: Any) -> CoachConfig:
    """기본값 위에 키워드 인자를 덮어쓴다.

    덮어쓰지 않은 값은 기본값이 남는다 — 바꾸고 싶은 값만 적으면 된다.
    파일은 읽지 않는다. 설정 JSON 은 부르는 쪽이 dict 로 읽어 ``load_config(**data)`` 로 넘긴다.
    """
    merged = CoachConfig().model_dump(mode="json")
    if override:
        merged = _deep_merge(merged, override)
    return CoachConfig.model_validate(merged)


DEFAULT_CONFIG = CoachConfig()
