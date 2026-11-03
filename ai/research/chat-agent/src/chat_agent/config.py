"""대화 에이전트 설정 — 비교 방향 표 · 차원 표 · 목표 규칙 · 낱말 목록 · 한도 · 크기 (기능 문서 4-1 · 6 · 8 · 16절).

- 누가 쓰나: 코어 모든 모듈. 코어는 설정 파일을 읽지 않는다. 기본값은 이 파일에만 있고, 바꿀 값만
  `load_config(...)`로 덮어쓴다(서비스 계층 · 시험이 넘긴다).
- 깊게 불변: 표는 `MappingProxyType` · `tuple` 로 감싸 만든 뒤 바꿀 수 없다(6-4 '설정은 깊게 불변').
- 버전: `config_hash()`(12자리)가 `criteria_versions.chat_agent`(= `<feature_version>+<해시>`)와
  목표 판정 규칙 판(`goal_rule_version`)에 들어간다. 저장된 목표는 저장 때 고정한 판으로 판정한다.
- 고칠 때
  - 비교 방향 표 · 차원 표는 리뷰(`pitch_coach_ai.features.review.config` · `vocab`)와 **같은 표**다.
    기능 모듈끼리 import 하지 않으므로 사본을 두고, 계약 시험(tests/unit/test_contract_tables.py)이 같은지 본다.
  - 낱말 목록(측정 항목 · 동의 · 칭찬 · 완화 · 행동 · 부정 · 조건 · 인용)을 바꾸면 `version.FEATURE_VERSION` 을 올린다(6-2 · 9절).
  - `min_delta` 시작값(시선 비율 0.05 · 군더더기/분 0.5)은 측정 뒤 조정한다(15-3). 실제 발표 데이터로 검증하지 않았다.
  - 한도 · 크기 숫자는 BE와 맞춘 통신 · 운영 값이다(8-2 · 16절). 바꾸면 기능 문서와 시험을 같이 고친다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .version import FEATURE_VERSION

Direction = Literal["HIGHER", "LOWER", "RANGE"]


def _freeze(value: Any) -> Any:
    """dict → MappingProxyType, list → tuple 로 깊게 바꾼다."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """해시 · 덤프용으로 다시 dict · list 로."""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    if isinstance(value, BaseModel):
        return {name: _thaw(getattr(value, name)) for name in type(value).model_fields}
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    @field_validator("*", mode="after")
    @classmethod
    def _deep_freeze(cls, v: Any) -> Any:
        return _freeze(v)


class DimensionSpec(_Frozen):
    """리뷰 차원 하나의 영역 · 지표 · 문제 (리뷰 vocab.DIMENSIONS 와 같다)"""

    areas: tuple[str, ...]
    metrics: tuple[str, ...]
    issues: tuple[str, ...]


class GoalMetric(_Frozen):
    """대화 목표로 쓸 수 있는 지표 하나 (6-4 'P0 네 지표')"""

    #: 값을 읽는 Take 결과 영역 (`take_result.areas.<영역>`)
    area: str
    #: 허용 operator. 범위 지표(cpm · duration_ms)는 in_range 만
    operators: tuple[Literal["gt_base", "lt_base", "in_range"], ...]
    #: gt_base · lt_base 의 최소 개선폭(유한 양수, 저장 때 목표에 고정). in_range 는 None
    min_delta: float | None = None


def _default_dimensions() -> dict[str, DimensionSpec]:
    # 리뷰 vocab.DIMENSIONS 와 같은 표 (계약 시험으로 대조). 순서가 차원 표 순서다
    return {
        "audience_gaze": DimensionSpec(
            areas=("GAZE",),
            metrics=("audience_ratio",),
            issues=("GAZE_LOW_EYE_CONTACT", "GAZE_AWAY"),
        ),
        "script_independence": DimensionSpec(
            areas=("GAZE",),
            metrics=("script_ratio", "reading_ratio", "reading_runs"),
            issues=("GAZE_ON_SCRIPT",),
        ),
        "pacing_rhythm": DimensionSpec(
            areas=("SPEED", "PAUSE", "TIME"),
            metrics=(
                "cpm",
                "pause_count",
                "long_silence_count",
                "duration_ms",
                "slide_duration_ms",
            ),
            issues=(
                "PACE_FAST",
                "PACE_SLOW",
                "LONG_SILENCE",
                "BEHIND_SCHEDULE",
                "AHEAD_OF_SCHEDULE",
                "SLIDE_OVER",
                "TIME_OVER",
            ),
        ),
        "vocal_delivery": DimensionSpec(
            areas=("VOLUME",), metrics=("voice_diff_db", "low_ratio"), issues=("VOLUME_LOW",)
        ),
        "fluency": DimensionSpec(
            areas=("FILLER",),
            metrics=("filler_per_min", "filler_count"),
            issues=("FILLER_FREQUENT",),
        ),
        "content_delivery": DimensionSpec(
            areas=("CONTENT",), metrics=("content_coverage", "critical_fact_accuracy"), issues=()
        ),
    }


def _default_direction() -> dict[str, Direction | None]:
    # 리뷰 config.comparison.direction 과 같은 표 (계약 시험으로 대조). None: 방향을 정할 수 없음
    return {
        "audience_ratio": "HIGHER",
        "script_ratio": "LOWER",
        "reading_ratio": "LOWER",
        "reading_runs": "LOWER",
        "cpm": "RANGE",
        "pause_count": None,
        "long_silence_count": "LOWER",
        "duration_ms": "RANGE",
        "slide_duration_ms": "RANGE",
        "voice_diff_db": "HIGHER",
        "low_ratio": "LOWER",
        "filler_per_min": "LOWER",
        "filler_count": "LOWER",
        "content_coverage": "HIGHER",
        "critical_fact_accuracy": "HIGHER",
    }


class ChatAgentConfig(_Frozen):
    # ── 6-4 비교 · 목표 ─────────────────────────────────────────────
    #: 비교 지표와 좋은 방향 (리뷰와 같은 표)
    direction: Mapping[str, Direction | None] = Field(default_factory=_default_direction)
    #: 리뷰 차원 표 (리뷰와 같은 표)
    dimensions: Mapping[str, DimensionSpec] = Field(default_factory=_default_dimensions)
    #: 대본 전달도 Take 값을 쓰는 최소 채점 비율 (리뷰 evaluable.min_scored_ratio 와 같은 값)
    min_scored_ratio: float = 0.5
    #: 대화 목표 지표 (P0 네 개, 6-4)
    goal_metrics: Mapping[str, GoalMetric] = Field(
        default_factory=lambda: {
            "audience_ratio": GoalMetric(area="GAZE", operators=("gt_base",), min_delta=0.05),
            "filler_per_min": GoalMetric(area="FILLER", operators=("lt_base",), min_delta=0.5),
            "cpm": GoalMetric(area="SPEED", operators=("in_range",)),
            "duration_ms": GoalMetric(area="TIME", operators=("in_range",)),
        }
    )
    #: 지표의 기본 단위. 값 주장(VALUE)의 원문 단위를 이 단위로 바꿔 비교한다(6-3)
    metric_units: Mapping[str, str] = Field(
        default_factory=lambda: {
            "audience_ratio": "ratio",
            "script_ratio": "ratio",
            "reading_ratio": "ratio",
            "screen_ratio": "ratio",
            "away_ratio": "ratio",
            "low_ratio": "ratio",
            "content_coverage": "ratio",
            "critical_fact_accuracy": "ratio",
            "reading_runs": "count",
            "pause_count": "count",
            "long_silence_count": "count",
            "filler_count": "count",
            "cpm": "cpm",
            "filler_per_min": "per_min",
            "duration_ms": "ms",
            "slide_duration_ms": "ms",
            "voice_diff_db": "dB",
        }
    )
    #: 답 문장에서 지표를 가리키는 이름 → 지표. 답 검사가 문장 속 지표 이름이 이은 근거의 지표와 같은지 본다(6-9 대상 일치).
    #: 긴 이름부터 맞추고, 한 이름은 한 지표만 가리킨다
    metric_names: Mapping[str, str] = Field(
        default_factory=lambda: {
            "청중 시선 비율": "audience_ratio",
            "시선 비율": "audience_ratio",
            "청중 시선": "audience_ratio",
            "대본 응시 비율": "script_ratio",
            "대본을 본 비율": "script_ratio",
            "대본 읽기 비율": "reading_ratio",
            "대본 읽기 횟수": "reading_runs",
            "화면 응시 비율": "screen_ratio",
            "말 속도": "cpm",
            "분당 글자 수": "cpm",
            "멈춤 횟수": "pause_count",
            "긴 침묵": "long_silence_count",
            "발표 시간": "duration_ms",
            "장 시간": "slide_duration_ms",
            "소리 크기": "voice_diff_db",
            "작은 소리 비율": "low_ratio",
            "분당 군더더기": "filler_per_min",
            "군더더기 빈도": "filler_per_min",
            "군더더기 수": "filler_count",
            "내용 전달률": "content_coverage",
            "핵심 사실 정확도": "critical_fact_accuracy",
        }
    )
    #: 화면 표시 자리수: 값 주장을 이 자리로 반올림해 비교한다(6-3 VALUE). ratio 는 퍼센트 정수(소수 둘째 자리)
    display_decimals: Mapping[str, int] = Field(
        default_factory=lambda: {"ratio": 2, "count": 0, "cpm": 0, "per_min": 1, "ms": -3, "dB": 1}
    )

    # ── 6-3 주장 판정 낱말 ─────────────────────────────────────────
    #: 측정 항목 낱말 → 차원. 이 낱말이 든 절마다 그 절을 덮는 판단이 있어야 한다(6-3 먼저 검사 1).
    #: '음' · '어'처럼 다른 낱말 안에 흔히 든 글자는 넣지 않는다
    measure_words: Mapping[str, str] = Field(
        default_factory=lambda: {
            "시선": "audience_gaze",
            "청중": "audience_gaze",
            "눈맞춤": "audience_gaze",
            "아이컨택": "audience_gaze",
            "대본": "script_independence",
            "원고": "script_independence",
            "속도": "pacing_rhythm",
            "빠르": "pacing_rhythm",
            "빨라": "pacing_rhythm",
            "빨랐": "pacing_rhythm",
            "느리": "pacing_rhythm",
            "느려": "pacing_rhythm",
            "느렸": "pacing_rhythm",
            "쉼": "pacing_rhythm",
            "멈춤": "pacing_rhythm",
            "침묵": "pacing_rhythm",
            "시간": "pacing_rhythm",
            "군더더기": "fluency",
            "채움말": "fluency",
            "말버릇": "fluency",
            "목소리": "vocal_delivery",
            "소리": "vocal_delivery",
            "성량": "vocal_delivery",
            "내용": "content_delivery",
        }
    )
    #: 문제 주장(ISSUE)의 방향 낱말 → 문제 이름. 방향이 둘인 차원(빠름 · 느림)을 가른다
    issue_words: Mapping[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "PACE_FAST": ("빠르", "빨라", "빨랐", "급하", "서둘"),
            "PACE_SLOW": ("느리", "느려", "느렸", "늘어"),
            "LONG_SILENCE": ("침묵", "멈췄", "끊겼"),
            "TIME_OVER": ("시간 초과", "시간을 넘", "넘겼"),
            "VOLUME_LOW": ("작았", "작아", "안 들"),
            "FILLER_FREQUENT": ("군더더기", "채움말", "말버릇"),
            "GAZE_ON_SCRIPT": ("대본을 봤", "대본만", "원고를 봤", "읽었"),
            "GAZE_LOW_EYE_CONTACT": ("시선", "청중", "눈맞춤", "아이컨택"),
        }
    )
    #: 동의 · 칭찬 낱말 (6-9). 맞는 판단 · 강점 근거에 이어져야만 나간다
    agreement_words: tuple[str, ...] = (
        "맞아요",
        "맞습니다",
        "맞네요",
        "그렇죠",
        "좋아요",
        "좋았어요",
        "잘하셨",
        "잘했",
        "훌륭",
        "완벽",
        "문제없",
        "문제 없",
    )
    #: 완화 낱말 (6-9 ASSESSMENT). 판정의 방향 · 정도 · 범위를 흐린다
    hedge_words: tuple[str, ...] = ("충분히", "사실상", "거의", "괜찮", "나쁘지 않")
    #: 인과 단정 낱말 (6-9). 승인된 자료 근거 없이 원인을 단정하면 걸린다
    causal_words: tuple[str, ...] = ("때문에", "때문이", "해서 그래", "탓", "원인은")
    #: 금지 주장 낱말 (6-9 FORBIDDEN_CLAIM): 점수 · 미션을 바꿨다, 발표를 시작했다
    forbidden_claims: tuple[str, ...] = (
        "점수를 바꿨",
        "점수를 고쳤",
        "점수를 올렸",
        "미션을 바꿨",
        "미션을 바꿔",
        "미션을 지웠",
        "미션을 고쳤",
        "리뷰를 바꿨",
        "리뷰 결과를 바꿨",
        "발표를 시작했",
        "녹화를 시작했",
    )
    #: 측정 수치를 한글 수로 쓴 표현 (6-9 NUMBER_FORMAT). 앞에 붙는 한글 수 + 뒤 단위
    korean_numerals: tuple[str, ...] = (
        "한",
        "두",
        "세",
        "네",
        "다섯",
        "여섯",
        "일곱",
        "여덟",
        "아홉",
        "열",
        "스무",
        "서른",
        "마흔",
        "쉰",
    )
    korean_numeral_units: tuple[str, ...] = ("번", "개", "장", "분", "초", "퍼센트", "점")
    #: 상대 대상 표현 → 정규화한 값 (claim_link_key, 5-2). 길이가 긴 것부터 맞춘다
    relative_targets: Mapping[str, str] = Field(
        default_factory=lambda: {
            "최근 세 번": "RECENT_3",
            "최근 3번": "RECENT_3",
            "최근 두 번": "RECENT_2",
            "최근 2번": "RECENT_2",
            "요즘": "RECENT",
            "최근": "RECENT",
            "지난번": "PREVIOUS",
            "저번": "PREVIOUS",
            "전에": "PREVIOUS",
            "이번": "CURRENT",
            "방금": "CURRENT",
            "지금": "CURRENT",
        }
    )

    # ── 6-2 허용 근거 낱말 ─────────────────────────────────────────
    #: 행동마다 인용에 드러나야 하는 행동 낱말 (명사 하나만으로는 부족하고 요청형까지 필요)
    action_words: Mapping[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "SAVE_GOAL": ("목표", "잡", "정해", "저장"),
            "START_PRACTICE": ("연습", "발표", "시작", "가자"),
        }
    )
    #: 요청형 표현. 인용을 포함한 절에 있어야 한다. 행동마다 다르다: 연습 화면 이동은 이동 의도
    #: (가자 · 할래 · 시작)가 드러나야 하고 "준비해 줘"만으로는 모호해 버튼으로 낮춘다(3-1)
    request_forms: Mapping[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "SAVE_GOAL": (
                "줘",
                "주세요",
                "줄래",
                "하자",
                "할래",
                "할게",
                "잡자",
                "잡을래",
                "잡아",
                "정해",
                "저장해",
                "합시다",
                "부탁",
            ),
            "START_PRACTICE": (
                "가자",
                "갈래",
                "가 줘",
                "가줘",
                "하자",
                "할래",
                "시작해",
                "시작하",
                "하러 가",
            ),
        }
    )
    #: 부정 · 취소 표현. 인용을 포함한 절 전체에 있으면 버린다
    negation_words: tuple[str, ...] = (
        "하지 마",
        "하지마",
        "하지 말",
        "말고",
        "취소",
        "빼고",
        "안 해",
        "안해",
        "말아",
        "그만",
        "않",
        "싫",
    )
    #: 조건 표현(~면). 절의 낱말 끝이 이 꼴이면 조건으로 본다
    condition_suffixes: tuple[str, ...] = ("면", "다면", "으면", "라면", "거든")
    #: 남의 말 인용 표현
    quotation_markers: tuple[str, ...] = (
        '"',
        "“",
        "”",
        "'",
        "‘",
        "’",
        "라던데",
        "라고 했",
        "라고 하",
        "다던데",
        "하라던",
        "라더라",
        "랬어",
        "래요",
    )

    # ── 8-2 한도 · 8-1 시간 ───────────────────────────────────────
    max_model_calls: int = 8
    #: 답 쓰기 · 고치기 몫으로 남기는 호출 수와 그 시간(각 12초)
    reserved_answer_calls: int = 2
    reserved_answer_seconds: float = 24.0
    max_tool_calls: int = 12
    max_tools_per_response: int = 8
    max_turn_calls: int = 6
    max_actions: int = 5
    max_save_actions: int = 1
    max_reassess_per_turn: int = 1
    max_plan_edits: int = 3
    plan_steps_min: int = 1
    plan_steps_max: int = 8
    max_input_tokens: int = 64_000
    max_output_reserve: int = 16_000
    #: 호출 한 번의 출력 상한(추론 포함). 실행 출력 예약 16k 를 8번에 나눈 값 이하
    output_tokens_per_call: int = 2_000
    turn_seconds: float = 120.0
    call_seconds: float = 45.0
    #: 모델 재시도 대기(Retry-After 가 없을 때)
    retry_wait_seconds: float = 0.5
    line_write_seconds: float = 2.0
    state_ttl_seconds: int = 600
    state_ttl_ask_seconds: int = 2_100
    #: 같은 (도구, 인자, 범위, 자료 판)을 새 근거 없이 연속 이만큼 요청하면 조사를 끝낸다
    repeat_stop: int = 2
    #: 무효 도구 응답이 연속 이만큼이면 조사를 끝낸다
    invalid_stop: int = 2

    # ── 크기 (5-3 · 5-4 · 5-6 · 6-12 · 11-5) ────────────────────────
    max_request_bytes: int = 2 * 1024 * 1024
    max_json_depth: int = 32
    max_context_bytes: int = 524_288
    max_state_bytes: int = 1_048_576
    max_state_decoded_bytes: int = 768 * 1024
    max_line_bytes: int = 2 * 1024 * 1024
    max_response_bytes: int = 16 * 1024 * 1024
    tail_reserve_bytes: int = 4 * 1024
    max_diagnostics_bytes: int = 65_536
    min_secret_bytes: int = 32

    # ── 입력 길이 (5-2 · 5-3) ─────────────────────────────────────
    max_user_message_chars: int = 2_000
    max_history_messages: int = 20
    max_history_text_chars: int = 4_000
    max_prior_assessments: int = 50
    max_search_query_chars: int = 200
    max_search_results: int = 5
    max_criteria_targets: int = 20

    # ── 6-10 자료 묶음 ────────────────────────────────────────────
    #: P0 은 팀이 쓴 카드만(라이선스 위험 없음). 외부 자료(CC BY · CC BY-SA)는 P2
    allowed_licenses: tuple[str, ...] = ("team",)

    def config_hash(self) -> str:
        """재현성 키(12자리). 같은 해시면 같은 입력에 같은 판정 · 검사 결과가 나온다."""
        blob = json.dumps(_thaw(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def criteria_version(self) -> str:
        """`criteria_versions.chat_agent` = <feature_version>+<설정 해시 12자리>"""
        return f"{FEATURE_VERSION}+{self.config_hash()}"

    def goal_rule_version(self) -> str:
        """목표 판정 규칙 판 = goal-<비교 방향 · 목표 지표 · min_delta 해시 12자리>. 저장 때 목표에 고정한다(9절)."""
        blob = json.dumps(
            {
                "direction": _thaw(self.direction),
                "goal_metrics": _thaw(self.goal_metrics),
                "display_decimals": _thaw(self.display_decimals),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return "goal-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def dimension_of_metric(self, metric: str) -> str | None:
        for dim, spec in self.dimensions.items():
            if metric in spec.metrics:
                return dim
        return None

    def dimension_of_issue(self, issue: str) -> str | None:
        for dim, spec in self.dimensions.items():
            if issue in spec.issues:
                return dim
        return None


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(**override: Any) -> ChatAgentConfig:
    """기본값 위에 키워드 인자를 덮어쓴다. 예: ``load_config(max_tool_calls=6)``. 파일은 읽지 않는다."""
    merged = _thaw(ChatAgentConfig())
    if override:
        merged = _deep_merge(merged, override)
    return ChatAgentConfig.model_validate(merged)


DEFAULT_CONFIG = ChatAgentConfig()
