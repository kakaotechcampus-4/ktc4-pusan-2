"""코치 설정 — 기준값 · 가중치 · 문구 템플릿 · 전략 사다리.

**모든 숫자는 잠정입니다.** 실제 발표 데이터로 검증하지 않았습니다. 출처는 셋입니다.

- FE 1단 코치(`frontend/.../useCoach.ts`)의 값 — 침묵 5초, 대본 응시 70%, 15초 간격, 60초 쿨다운
- stt-live v1 노트북 — CPM 275 / 350
- 그 밖의 값 — 손으로 고른 시작점. 재생 평가(coach_lab.replay)로 조정합니다

기본값은 이 파일에만 있습니다. 바꿀 때는 코드를 고치지 말고 바꿀 값만 덮어쓰세요 (load_config).
코어는 설정 파일을 읽지 않습니다 — research 에서는 coach_lab.replay --config 가 JSON 을 읽습니다.
응답의 config_hash 가 어떤 설정으로 판단했는지를 기록합니다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from .vocab import Instruction, Issue


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GazeConfig(_Section):
    #: 대본을 본다고 볼 라벨. 시선 모듈이 방향을 세분화하면 여기에 이름만 더한다
    script_labels: list[str] = ["BOTTOM", "SCRIPT"]
    uncertain_label: str = "UNCERTAIN"
    #: 얼굴이 없거나 기록이 빈 시간. 판정을 보류한 UNCERTAIN 과 같이 '측정하지 못함'으로 센다
    unmeasured_label: str = "UNMEASURED"
    #: 1초 기록 입력에서 마지막 기록이 지금보다 이만큼 넘게 오래됐으면 지금 라벨을 측정 불가로 본다
    record_stale_ms: int = 2_000
    #: 창이 이보다 짧으면(Take 시작 직후) 비율로 지적하지 않는다. 1~3초의 표본으로는 두 번만
    #: 대본을 봐도 70% 를 넘었다 (1초 기록 입력 실험, harsh). FE 코치도 표본 5개 미만이면
    #: 비율을 내지 않는다
    min_window_ms: int = 5_000
    #: 최근 창(기본 10초)에서 대본 응시 비율. FE 코치의 BOTTOM_RATIO 와 같은 값
    script_ratio: float = 0.7
    script_ratio_bad: float = 0.95
    #: 대본을 연속으로 본 시간
    continuous_ms: int = 5_000
    continuous_bad_ms: int = 15_000
    #: UNCERTAIN 이 이보다 많으면 시선 판단을 버린다 (SENSOR_UNUSABLE)
    max_uncertain_ratio: float = 0.5
    #: 센서 판단은 지금 UNCERTAIN 비율과 최근 이만큼의 평균 중 나쁜 쪽으로 한다. 10초 창 하나는
    #: 잡음으로 잠깐 기준 아래로 내려가, 얼굴이 대부분 안 잡히는데도 '믿을 만한 1초'가 생긴다 (실험
    #: 04 · 16)
    sensor_smoothing_ms: int = 10_000
    #: 연속 응시만으로 잡을 때도 창 비율이 이 이상이어야 한다. 라벨이 15% 흔들리면 5초 연속이
    #: 우연히 생겨 발표 끝 무렵 근거 없는 시선 지적이 났다 (실험 06 · 08 · 09, harsh)
    streak_min_ratio: float = 0.6
    #: indicators.gaze 를 SCRIPT 로 보여줄 비율
    indicator_script_ratio: float = 0.5


class SpeechConfig(_Section):
    #: CPM 을 재는 창. stt-live v1 과 같은 15초
    window_ms: int = 15_000
    slow_cpm: float = 275.0
    fast_cpm: float = 350.0
    fast_cpm_bad: float = 450.0
    #: 창 안에서 실제로 말한 시간이 이보다 짧으면 CPM 을 내지 않는다
    min_speak_ms: int = 4_000
    min_words: int = 5
    filler_window_ms: int = 60_000
    filler_threshold: int = 6
    filler_bad: int = 15
    #: 개입 효과를 잴 때 쓰는 짧은 CPM 창. 15초 창에는 개입 전 단어가 남아 효과를 못 본다 (실험 06)
    recent_window_ms: int = 6_000
    recent_min_speak_ms: int = 2_500
    #: 이 장을 이만큼 말했는데 필수 키워드가 없으면 KEYWORD_MISSING
    keyword_progress: float = 0.8
    #: STT 가 고유명사를 잘못 적는 일이 잦아 키워드 판단 신뢰도를 낮춰 둔다
    keyword_confidence: float = 0.7
    #: 키워드가 단어 경계를 걸쳐도 찾도록 남겨 두는 최근 글자 수
    keyword_tail_chars: int = 40


class VoiceConfig(_Section):
    #: 캘리브레이션(평소 목소리) 대비 dB. 음수가 작은 소리. FE 와 단위 합의 전 임시값
    low_relative_db: float = -6.0
    low_relative_db_bad: float = -15.0
    #: 음량은 순간값이 크게 흔들려서 말하는 동안의 최근 평균으로 본다
    smoothing_ms: int = 5_000
    #: 평균에 들어갈 '말하는 중' 표본이 이보다 적으면 음량을 판단하지 않는다 (표본 1~2개 평균은
    #: 잡음)
    min_samples: int = 3
    long_silence_ms: int = 5_000
    long_silence_bad_ms: int = 15_000


class TimingConfig(_Section):
    #: r = 남은 내용 시간 / 남은 시간. 1 에 딱 붙어 깜빡이지 않게 약간 띄운다
    behind_ratio: float = 1.05
    behind_ratio_bad: float = 1.5
    #: 속도를 몰라 SPEED_UP / CONDENSE 를 못 고를 때, 이보다 늦으면 CONDENSE
    condense_ratio_without_pace: float = 1.3
    #: 허용 최소 시간(min_ms)이 없을 때 '너무 일찍 끝남'의 기준 = 목표 × 이 값
    early_end_ratio: float = 0.85
    #: 예상 종료가 기준보다 '목표 × 이 값'만큼 이르면 심각도 1.0
    early_end_bad_ratio: float = 0.15
    #: 초반에는 진행도 추정이 흔들려서 '빠르다' 판단을 미룬다
    ahead_min_elapsed_ratio: float = 0.2
    slide_over_factor: float = 1.5
    slide_over_bad_factor: float = 2.5
    final_minute_ms: int = 60_000
    #: 진행도가 이보다 작으면 '이 장이 몇 초 걸릴지' 추정하지 않는다
    projection_min_progress: float = 0.1
    #: 진행도를 말한 글자 수로 쟀을 때 / 시간으로 대신 쟀을 때의 신뢰도
    progress_confidence_chars: float = 0.9
    progress_confidence_time: float = 0.6


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
            "relative_db": -3.0,
            "required_ratio": 0.1,
            "filler_count_60s": 3.0,
        }
    )
    #: 계획(v1.1 LLM)이 주는 가중치를 이 범위로 자른다
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
    cpm_back_margin: float = 20.0
    #: 효과 전후 값은 이 시간의 평균으로 잰다
    average_ms: int = 3_000
    cpm_drop_ratio: float = 0.1
    volume_gain_db: float = 3.0
    filler_drop_ratio: float = 0.5
    schedule_delta: float = 0.05


class ReviewConfig(_Section):
    """Take 종료 뒤 리뷰 에이전트 근거를 만드는 규칙 (review.py).

    기본값은 리뷰 근거 실험(research 의 coach_lab.evaluate)으로 고른 것입니다.
    결과는 ai/research/coach-agent/reports/results/review_evidence.json 에 있습니다.
    """

    #: 센서를 믿을 수 있던 시간이 이 비율보다 적은 구간은 문제로 보지 않는다 (UNRELIABLE)
    min_reliability: float = 0.5
    exclude_unreliable: bool = True
    #: 평가기의 창 때문에 탐지가 실제보다 늦게 시작 · 끝나는 만큼을 되돌린다
    lag_compensation: bool = True
    #: 되돌리는 정도 (1.0 = 아래 lag_ms 그대로). 실험으로 고른다
    lag_scale: float = 1.0
    #: (시작 지연, 끝 지연) ms. GAZE_SCRIPT 는 창 길이 × 기준으로 따로 계산한다
    lag_ms: dict[Issue, tuple[int, int]] = Field(
        default_factory=lambda: {
            Issue.PACE_FAST: (7_500, 7_500),  # 15초 CPM 창의 절반
            Issue.VOLUME_LOW: (2_500, 2_500),  # 5초 평균의 절반
            Issue.FILLER_FREQUENT: (30_000, 30_000),  # 60초 개수 창의 절반
            Issue.LONG_SILENCE: (5_000, 0),  # 5초 침묵 뒤에야 잡힌다
        }
    )
    #: 같은 영역 · 같은 장의 구간이 이 간격 안에서 다시 시작되면 한 구간으로 합친다
    merge_gap_ms: int = 10_000
    #: 실제로 잡힌 시간(지연 보정 전)이 이보다 짧고 개입도 없던 구간은 리뷰에 넘기지 않는다 (잡음
    #: 깜빡임).
    #: 3초: 깨끗한 데이터의 짧은 실제 문제를 놓치지 않는 가장 큰 값. 6초면 harsh 잡음의 근거 없는
    #: 지적이 0.11 → 0.06 으로 줄지만 깨끗한 데이터에서 실제 문제 하나를 놓친다 (실험 결과의 sweep)
    min_segment_ms: int = 3_000
    #: 이보다 작은 부담(심각도 × 초)은 문제로 보지 않는다
    min_burden_s: float = 3.0
    #: 상위 몇 개 영역을 PRIORITY 로 볼지
    priority_types: int = 2
    recurring_weight: float = 1.5
    gave_up_weight: float = 1.3
    mission_failed_weight: float = 1.2
    #: 데이터가 이 비율보다 적으면 미션 · 영역을 NOT_EVALUABLE 로 둔다
    min_coverage: float = 0.5
    #: 목표를 이만큼 이내로 놓치면 FAILED 가 아니라 PARTIAL
    partial_tolerance: dict[str, float] = Field(
        default_factory=lambda: {
            "script_ratio": 0.05,
            "cpm": 20.0,
            "relative_db": 2.0,
            "filler_per_min": 1.0,
            "slide_duration_ms": 5_000.0,
            "duration_ms": 5_000.0,
            "long_silence_count": 0.0,
            "keyword_coverage": 0.0,
        }
    )
    #: 장 시간이 목표의 이 배를 넘으면 TIME 문제, 이 배에서 심각도 1.0
    time_over_ratio: float = 1.1
    time_bad_ratio: float = 2.0
    #: 빠뜨린 키워드 하나의 부담 (초)
    keyword_burden_s: float = 10.0
    max_next_missions: int = 3
    #: 한 장에 그 영역 부담의 이 비율 이상이 몰려 있으면 장 단위 미션, 아니면 Take 단위
    slide_mission_share: float = 0.6
    #: 다음 미션 목표 — 한 번에 도달할 만큼만 낮춘다
    gaze_goal: float = 0.3
    gaze_step: float = 0.2
    filler_goal: float = 2.0
    slide_time_margin: float = 1.1


class Features(_Section):
    #: STT 가 고유명사를 자주 잘못 적어 '말했는데 언급하라'는 오탐이 난다. 기본은 끔
    keyword_missing: bool = False
    praise: bool = True
    pause_wait: bool = True


class Step(_Section):
    instruction: Instruction
    variant: str = "default"


class IssueRule(_Section):
    #: 효과가 없을 때마다 한 칸씩 내려간다
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


def _ladder(*steps: tuple[Instruction, str]) -> list[Step]:
    return [Step(instruction=i, variant=v) for i, v in steps]


def _default_issue_rules() -> dict[Issue, IssueRule]:
    I = Instruction  # noqa: E741 — 표가 한눈에 들어오게
    return {
        # 시선 비율은 FE 의 10초 창이라 반응(약 2초 뒤)이 창을 다 채우는 12초 뒤에 잰다 (실험 03 ·
        # 14)
        Issue.GAZE_SCRIPT: IssueRule(
            ladder=_ladder((I.LOOK_AT_CAMERA, "default"), (I.LOOK_AT_CAMERA, "sentence_start")),
            persistence_ms=3_000,
            outcome_delay_ms=12_000,
            praise=True,
        ),
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
        Issue.KEYWORD_MISSING: IssueRule(
            ladder=_ladder((I.MENTION_KEYWORD, "default")),
            outcome_delay_ms=15_000,
            max_fires=1,
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
    "MENTION_KEYWORD.default": "이 슬라이드의 핵심인 '{keyword}'를 언급해 보세요",
    "SPEED_UP.default": "조금만 빠르게 — 남은 {remaining_slides}장, {remaining_time}",
    "CONDENSE.default": "핵심만 말하고 넘어가세요 — 남은 {remaining_slides}장, {remaining_time}",
    "MOVE_ON.default": "이 장은 목표보다 {over_time} 넘었어요 — 정리하고 다음 장으로",
    "MOVE_ON.firm": "지금 다음 장으로 넘어가세요",
    "WRAP_UP.default": "결론으로 넘어가 마무리하세요",
    "WRAP_UP.final_minute": "남은 시간이 짧으니 결론으로 넘어가세요",
    "WRAP_UP.time_over": "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요",
    "CONTINUE.default": "좋아요, 지금처럼 이어가세요",
}


class CoachConfig(_Section):
    gaze: GazeConfig = Field(default_factory=GazeConfig)
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)
    features: Features = Field(default_factory=Features)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
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
