"""대화 에이전트 설정 — 비교 방향 표 · 차원 표 · 목표 규칙 · 낱말 목록 · 한도 · 크기 (기능 문서 4-1 · 6 · 8 · 16절).

- 누가 쓰나: 코어 모든 모듈. 코어는 설정 파일을 읽지 않는다. 기본값은 이 파일에만 있고, 바꿀 값만
  `load_config(...)`로 덮어쓴다(서비스 계층 · 시험이 넘긴다).
- 깊게 불변: 표는 `MappingProxyType` · `tuple` 로 감싸 만든 뒤 바꿀 수 없다(6-4 '설정은 깊게 불변').
- 버전: `config_hash()`(12자리)가 `criteria_versions.chat_agent`(= `<feature_version>+<해시>`)와
  목표 판정 규칙 판(`goal_rule_version`)에 들어간다. 저장된 목표는 저장 때 고정한 판으로 판정한다.
- 고칠 때
  - 비교 방향 표 · 차원 표는 리뷰(`pitch_coach_ai.features.review.config` · `vocab`)와 **같은 표**다.
    기능 모듈끼리 import 하지 않으므로 사본을 두고, 계약 시험(tests/unit/test_contract_tables.py)이 같은지 본다.
  - 낱말 목록(측정 항목 · 동의 · 칭찬 · 완화 · 행동 · 부정 · 조건 · 인용 · 범위 밖)을 바꾸면 `version.FEATURE_VERSION` 을 올린다(6-2 · 9절).
  - `min_delta` 시작값(시선 비율 0.05 · 군더더기/분 0.5)은 측정 뒤 조정한다(15-3). 실제 발표 데이터로 검증하지 않았다.
  - 한도 · 크기 숫자는 BE와 맞춘 통신 · 운영 값이다(8-2 · 16절). 바꾸면 기능 문서와 시험을 같이 고친다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .version import FEATURE_VERSION, GOAL_ALGORITHM_VERSION

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

    @model_validator(mode="after")
    def _delta_matches_operator(self) -> GoalMetric:
        # 잘못된 설정(음수 · 무한대 · in_range 에 min_delta)은 시작 때 거부한다(6-4, L4-12)
        if len(self.operators) != 1:
            raise ValueError("목표 지표의 operator 는 하나다")
        if self.operators[0] == "in_range":
            if self.min_delta is not None:
                raise ValueError("in_range 목표의 min_delta 는 None 이다")
        elif self.min_delta is None or not math.isfinite(self.min_delta) or self.min_delta <= 0:
            raise ValueError("gt_base · lt_base 목표의 min_delta 는 유한 양수다")
        return self


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
    #: 측정 항목 낱말이 든 다른 낱말. 이 낱말 안에서 맞은 것은 측정 항목으로 보지 않는다(오탐 줄이기)
    measure_word_exclusions: tuple[str, ...] = (
        "헛소리",
        "잔소리",
        "실시간",
        "시간표",
        "버튼 내용",
        "화면 내용",
        "카드 내용",
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
    #: 범위 밖 낱말 (6-9 SCOPE_RULE): 모델 문장(기록 · 자료 · 판단 설명)에 이 낱말이 있으면 고치지 않고 버린다.
    #: 대본 · 발표자료 · 평가 기준 · 제한 시간의 수정안 · 추천 값, 예상 질문, 연습 계획 · 일정을 좁게 잡는다.
    #: 정상 기록 답("대본 의존이 높았어요" · "대본을 본 시간이")이 걸리지 않게 '대본' · '기준' 같은 한 낱말은 넣지 않는다
    scope_out_words: tuple[str, ...] = (
        "수정안",
        "이렇게 고쳐",
        "이렇게 바꿔",
        "이렇게 바꾸",
        "이렇게 고치",
        "고쳐 써",
        "고쳐 쓰",
        "바꿔 써 보",
        "다시 써 보",
        "추천 기준",
        "추천 제한 시간",
        "예상 질문",
        "질문 목록",
        "질의응답 연습",
        "연습 계획",
        "연습 일정",
        "훈련 계획",
        "훈련 과제",
        "일정표",
    )
    #: 범위 밖 고쳐 쓰라는 말 (6-9 SCOPE_RULE): 같은 절 안에서 대상 낱말 뒤 `scope_edit_window`자 안에 고치라는 동사가 오면 건다.
    #: 값 · 정보를 묻는 말 (6-2/9). 이 말이 들고 물음표로 끝난, 주장한 값 · 방향이 없는 측정 초안만 요청으로 본다
    #: ("대본 보고 한 발표였어" 같은 상태 주장 · "잘 봤지?"처럼 평가를 내세운 말은 그대로)
    info_question_words: tuple[str, ...] = (
        "몇",
        "얼마",
        "어느 정도",
        "어떻게",
        "어땠",
        "어때",
        "어떤",
        "어디",
        "무슨",
        "뭐",
        "무엇",
        "왜",
    )
    #: 절을 끊는 말. 이 말이 있으면 한 절에 평가가 섞인 것으로 보아 물음을 요청으로 바꾸지 않는다
    clause_break_words: tuple[str, ...] = ("는데", "은데", "지만")
    #: 낱말 목록이 못 잡는 돌려 말한 수정안("평가 기준에는 명료성을 넣으세요")을 잡는다. 명령 · 권유형만 넣어
    #: 기록 · 행동 조언("대본을 덜 보고 말해 보세요" · "다음 Take에서 대본을 보는 시간을 줄여 보세요")은 걸리지 않게 한다
    scope_targets: tuple[str, ...] = (
        "대본",
        "발표자료",
        "발표 자료",
        "슬라이드",
        "평가 기준",
        "제한 시간",
    )
    scope_edit_verbs: tuple[str, ...] = (
        "넣으세요",
        "넣어 보",
        "바꾸세요",
        "바꿔 보",
        "고치세요",
        "고쳐 보",
        "추가하세요",
        "추가해 보",
        "빼세요",
        "빼 보",
        "수정하세요",
        "수정해 보",
        "다듬으세요",
        "다듬어 보",
        "정하세요",
        "정해 보",
        "줄이세요",
        "늘리세요",
        "더하세요",
        "더해 보",
        # "~면 좋아요"형 수정안(#904 리뷰: "대본의 첫 문장을 이렇게 바꾸면 좋아요")
        "바꾸면 좋",
        "고치면 좋",
        "넣으면 좋",
        "빼면 좋",
        "추가하면 좋",
        "수정하면 좋",
    )
    #: 대상과 동사 사이 글자 수 한도
    scope_edit_window: int = 30
    #: 대상과 동사 사이에 수행 명사가 있으면 그 동사는 발표 수행 방식을 바꾸는 말이라 통과시킨다
    #: ("대본을 보는 시간을 줄이세요" · "슬라이드를 넘기는 속도를 줄이세요")
    scope_performance_nouns: tuple[str, ...] = (
        "속도",
        "시간",
        "시선",
        "의존",
        "응시",
        "쉼",
        "목소리",
        "호흡",
        "횟수",
        # 시선 · 쉼 · 말하기 같은 전달 동작(#904 리뷰: "슬라이드 3장에서 청중을 보며 말하는 부분을 늘리세요"). '청중' ·
        # '말하' 낱말 하나로는 넣지 않는다: "슬라이드 제목을 청중이 알기 쉽게 바꿔 보세요" · "대본을 말하기 쉽게 고쳐
        # 보세요" 같은 내용 수정안의 꾸밈말에도 나와 범위 검사가 우회된다(#948 리뷰). 동작을 가리키는 구만 넣는다
        "청중을 보",
        "청중 보기",
        "청중과 눈",
        "청중 쪽",
        "말하는 부분",
        "말하는 속도",
        "말하는 시간",
        "멈추는",
        "멈추고",
    )
    #: 내용 고쳐 쓰기 규칙: 문장 안에 대상 낱말이 있고, 같은 문장에 "내용 명사 + 을/를/에 + (`scope_content_gap`자 안) 고치는 동사"가
    #: 있으면 건다. 절 경계로 나누지 않는다. 수행 명사 예외는 내용 명사 바로 앞(`scope_content_gap`자)과 '내용 명사 ~ 동사'
    #: 구간에 수행 명사가 있을 때만 받는다("대본 3장 부분에 쉼을 더해 보세요"는 통과, "대본을 보는 시간을 줄이세요 그리고
    #: 첫 문장을 바꾸세요"는 그 구간에 수행 명사가 없어 걸림)
    scope_content_nouns: tuple[str, ...] = (
        "문장",
        "부분",
        "내용",
        "표현",
        "단어",
        "그림",
        "문구",
        "제목",
        "항목",
    )
    scope_content_gap: int = 10
    #: 인과 단정 낱말 (6-9). 승인된 자료 근거 없이 원인을 단정하면 걸린다
    causal_words: tuple[str, ...] = ("때문에", "때문이", "해서 그래", "탓", "원인은")
    #: 금지 주장 낱말 (6-9 FORBIDDEN_CLAIM): 점수 · 미션을 바꿨다, 발표를 시작했다
    forbidden_claims: tuple[str, ...] = (
        "점수를 바꿨",
        "점수를 바꿔",
        "점수를 고쳤",
        "점수를 올렸",
        "점수를 변경",
        "점수를 수정",
        "미션을 바꿨",
        "미션을 바꿔",
        "미션을 지웠",
        "미션을 고쳤",
        "미션을 변경",
        "미션을 수정",
        "미션을 삭제",
        "리뷰를 바꿨",
        "리뷰를 수정",
        "리뷰 결과를 바꿨",
        "리뷰 결과를 수정",
        "차원 상태를 바꿨",
        "발표를 시작했",
        "발표가 시작됐",
        "연습을 시작했",
        "녹화를 시작했",
        "입력란을 채웠",
        "대본을 고쳤",
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
            # 명사 '목표'만으로는 저장 요청이 아니다(동사가 있어야 함, L1-24)
            "SAVE_GOAL": ("잡", "정해", "정하", "저장", "세워", "설정"),
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
        "지 마",  # 따르지 마 · 저장하지 마세요
        "지마",
        "지 말",
        "안 할",
        "안할",
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
    #: 바람 표현: `~면` 바로 뒤 낱말이 이것으로 시작하면 조건이 아니라 바람이다("설명해 주면 좋겠어", 6-14 기억 요청)
    wish_after_condition: tuple[str, ...] = ("좋겠", "고맙", "좋아", "좋을", "감사")
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
        "라는",  # "저장해 줘라는 말"
        "라 했",
        "라 하",
        "라고",
    )

    # ── 8-2 한도 · 8-1 시간 ───────────────────────────────────────
    max_model_calls: int = 18
    #: 답 쓰기 · 고치기 몫으로 남기는 호출 수와 그 시간(각 12초)
    reserved_answer_calls: int = 2
    reserved_answer_seconds: float = 24.0
    #: 조사 호출이 의미 있게 끝날 최소 시간(초). 남은 시간 − 답 몫(24초)이 이보다 짧으면 조사를 보내지 않는다(전송 뒤 24초는 항상 남음)
    min_research_call_seconds: float = 3.0
    max_tool_calls: int = 12
    #: 조사 흐름(6-1): 계획의 판단 초안이 집은 기록을 모델이 고르기 전에 코드가 먼저 읽는다. 고정 순서 기준선(13-3)은 끈다
    research_prefetch: bool = True
    # ── 가벼운 말(P-0008 5-1) ─────────────────────────────────────────
    #: 계획이 SMALL_TALK 를 골라도 코드가 한 번 더 본다: 말이 이보다 길거나 숫자가 있으면 가벼운 말로 보지 않는다
    #: (숫자는 Take · 장 · 점수를 집은 요청의 흔적이다 — "안녕! Take 5 점수 100점으로" 같은 말을 인사로 넘기지 않게)
    small_talk_max_chars: int = 60
    #: 연습하려는 뜻(PRACTICE_INTENT)은 사용자 말에 이 낱말이 있을 때만 받는다 — 화면 상태 · 기록 속 지시가 이동 버튼을
    #: 만들지 못하게(없으면 인사로 받는다)
    practice_intent_words: tuple[str, ...] = (
        "연습",
        "발표할",
        "발표 할",
        "발표해",
        "리허설",
        "해 볼",
        "해볼",
        "해 보",
    )
    #: 코드가 먼저 내는 요청(읽기 · 가져오기) 수의 한 실행 상한. 도구 호출 한도(`max_tool_calls`) 안에서 센다
    research_prefetch_max: int = 4
    #: 문제 구간(issue_type) → 그 문제가 직접 뒷받침하는 지표(6-9 대상 일치). 같은 차원이라도 다른 지표는 다른 대상이다
    #: (빠름 문제로 '긴 침묵'을, 대본 응시로 '대본 읽기 비율'을 말하지 않는다)
    issue_metrics: Mapping[str, tuple[str, ...]] = Field(
        default_factory=lambda: {
            "GAZE_LOW_EYE_CONTACT": ("audience_ratio",),
            "GAZE_AWAY": ("audience_ratio",),
            "GAZE_ON_SCRIPT": ("script_ratio",),
            "PACE_FAST": ("cpm",),
            "PACE_SLOW": ("cpm",),
            "LONG_SILENCE": ("long_silence_count",),
            "BEHIND_SCHEDULE": ("slide_duration_ms",),
            "AHEAD_OF_SCHEDULE": ("slide_duration_ms",),
            "SLIDE_OVER": ("slide_duration_ms",),
            "TIME_OVER": ("duration_ms",),
            "VOLUME_LOW": ("voice_diff_db", "low_ratio"),
            "FILLER_FREQUENT": ("filler_per_min", "filler_count"),
        }
    )
    #: 여러 발표를 함께 묻는 말. 이름으로 찾은 피치가 여럿이어도 이 말이 있으면 어느 발표인지 되묻지 않는다(L2-21)
    plural_request_words: tuple[str, ...] = (
        "발표들",
        "피치들",
        "각각",
        "모두",
        "전부",
        "둘 다",
        "다 알려",
        "모든",
    )
    max_tools_per_response: int = 8
    max_turn_calls: int = 6
    max_actions: int = 5
    max_save_actions: int = 1
    max_reassess_per_turn: int = 1
    max_plan_edits: int = 3
    plan_steps_min: int = 1
    plan_steps_max: int = 8
    #: 입력은 전송 전 예약량의 누적(72k). 출력은 전송 전에 그 호출의 상한을 예약하고 응답 뒤 공급자 실측으로 정산한 누적(8k, 8-2 · 8-3)
    max_input_tokens: int = 72_000
    max_output_tokens: int = 8_000
    #: 호출 한 번의 출력 상한(추론 포함) = 한 번 보낼 때 예약하는 출력량. 사용량을 모르면 예약 전체를 쓴 것으로 둔다
    output_tokens_per_call: int = 2_000
    turn_seconds: float = 120.0
    call_seconds: float = 45.0
    #: 모델 재시도 대기(Retry-After 가 없을 때)
    retry_wait_seconds: float = 0.5
    line_write_seconds: float = 2.0
    state_ttl_seconds: int = 600
    state_ttl_ask_seconds: int = 2_100
    #: 승인 확인 카드의 유효시간(초) — `ASK_CONFIRM.expires_at` = 발급 + 이만큼. BE 의 승인 대기(30분)와 같다(5-2 · 6-2)
    approval_wait_seconds: int = 1_800
    #: 같은 (도구, 인자, 범위, 자료 판)을 새 근거 없이 연속 이만큼 요청하면 조사를 끝낸다
    repeat_stop: int = 2
    #: 무효 도구 응답이 연속 이만큼이면 조사를 끝낸다
    invalid_stop: int = 2
    #: 대화 요약 호출(6-14) 뒤 끝 줄을 만들어 보낼 몫(초). 요약은 호출 한 번 시간 + 이 몫이 남을 때만 부른다
    final_line_reserve_seconds: float = 2.0

    # ── 6-10 일반 조언(GENERAL) ─────────────────────────────────
    #: 한 답의 일반 조언 문장 수(초안에서 앞의 이만큼만 쓴다) · 문장 길이(공백 포함 글자 수)
    max_general_claims: int = 3
    max_general_chars: int = 120
    #: 기록 지시어 · 기록 낱말. 일반 조언이 내 기록을 말하는 것처럼 보이면 안 된다(기록 이야기는 RECORD 로 근거와 함께)
    general_record_words: tuple[str, ...] = (
        "이번",
        "지난번",
        "지난 발표",
        "지난 연습",
        "내 발표",
        "제 발표",
        "당신의 발표",
        "님의 발표",
        "기록",
        "측정",
        "점수",
        "리뷰",
        "미션",
        "take",
        "테이크",
    )
    #: 측정값 이름에 드는 낱말(시선 비율 · 멈춤 횟수 · 분당 글자 수 …). 일반 조언이 측정값을 말하는 것처럼 보이면 안 된다.
    #: '말 속도' · '시선' · '침묵' 같은 발표 요소 낱말은 일반 조언에 필요하므로 넣지 않는다
    general_metric_words: tuple[str, ...] = (
        "비율",
        "횟수",
        "빈도",
        "분당",
        "전달률",
        "정확도",
        "cpm",
        "데시벨",
        "db",
    )
    #: 출처 주장 낱말. 일반 조언이 자료 · 연구에서 확인한 것처럼 보이면 안 된다(자료 근거는 GUIDE 로 인용과 함께)
    general_citation_words: tuple[str, ...] = (
        "연구",
        "논문",
        "학회",
        "기관",
        "통계",
        "조사 결과",
        "실험 결과",
        "책에서",
        "따르면",
        "전문가",
        "교수",
        "밝혀졌",
        "입증",
        "증명",
    )
    #: 판정 낱말. 일반 조언이 내 발표를 판정하는 것처럼 보이면 안 된다(판정은 ASSESSMENT · 코드가 쓴다)
    general_verdict_words: tuple[str, ...] = (
        "강점",
        "약점",
        "잘하고 있",
        "잘하셨",
        "잘하시",
        "잘하네",
        "못했",
        "못하셨",
        "부족했",
        "부족하셨",
        "문제였",
        "합격",
        "통과",
        "기준을 넘",
        "기준에 못",
    )

    #: 일반 조언에서 사용자 발표를 판정하는 말(6-9 `GENERAL`, #919 리뷰): 사용자를 주어로 한 존칭 서술 · 평가 말끝 · 정도 판정.
    #: 근거 없이 기록을 말하는 것처럼 보여 `RECORD` 근거 검사와 코드 판정(6-3)을 비켜 가는 길을 막는다. 권하는 말끝
    #: ("~하는 편이 좋아요" · "긴장되면 ~해 보세요")은 걸리지 않게 판정 꼴만 넣는다
    general_user_judgement_words: tuple[str, ...] = (
        "말씀하시",
        "하시네",
        "시네요",
        "이시네",
        "편이에요",
        "편이네요",
        "편이시",
        "너무 빨라",
        "너무 빠르",
        "너무 느려",
        "너무 느리",
        "너무 작아",
        "너무 길어",
        "너무 짧아",
        "자주 떨어",
        "자주 놓치",
    )

    # ── 6-14 기억(코칭 프로필 · 대화 요약) ─────────────────────────
    #: 직접적인 기억 요청 표현. 인용(`quote`) 안에 이 중 하나가 있어야 프로필 후보로 받는다(②)
    memory_request_words: tuple[str, ...] = (
        "기억해",
        "기억해 줘",
        "기억해줘",
        "기억해 두",
        "기억해두",
        "앞으로는",
        "앞으로",
        "다음부터",
        "다음부터는",
        "매번",
        "항상",
    )
    #: 기억 철회 표현(③). 기억을 직접 가리키므로 말 전체에서 본다("~해 줘, 아니 기억하지 마")
    memory_withdraw_words: tuple[str, ...] = ("잊어", "기억하지 마", "기억하지마")
    #: 지우기 동사(③). 기억과 상관없는 말("대본 파일은 지우고 다시 올릴게")에도 나오므로, 같은 절에 기억을 가리키는 말
    #: (`memory_withdraw_targets`)이 있을 때만 철회로 본다
    memory_withdraw_verbs: tuple[str, ...] = ("지워", "지우고", "지워 줘")
    #: '방금' 하나로는 넣지 않는다: "방금 올린 대본은 지우고"처럼 기억이 아닌 대상에도 쓰인다(#948 리뷰). 말을 가리키는 구만('방금 그거'는 '그거'로 잡힌다)
    memory_withdraw_targets: tuple[str, ...] = (
        "기억",
        "그거",
        "그건",
        "그 말",
        "방금 말",
        "아까 말",
    )
    #: 저장 값에 들어가면 안 되는 지시형 · 판정 · 권한 낱말(⑤). 기억이 판정 · 실행 권한에 닿지 않게 한다(P14)
    memory_directive_words: tuple[str, ...] = (
        "무시",
        "승인",
        "허용",
        "맞다고",
        "동의해",
        "틀렸다고",
        "판정",
        "점수",
        "통과",
        "지시",
        "규칙",
        "시스템",
        "프롬프트",
        "저장해",
        "실행",
    )
    #: 코칭 프로필 항목 수(`key`마다 하나) · 값 길이, 한 응답의 갱신 후보 수
    max_profile_items: int = 4
    max_profile_value_chars: int = 80
    max_profile_updates: int = 2
    #: 대화 요약 항목 수 · 길이, 밀린 메시지 수
    max_summary_items: int = 12
    max_summary_item_chars: int = 120
    max_overflow_messages: int = 10

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

    # ── 6-10 하이브리드 검색(키워드 + 임베딩, 질문 임베딩을 넘겨 받은 때만) ───────────
    #: 두 순위 목록을 합치는 RRF 상수 k: 점수 = Σ 1 / (k + 순위)
    hybrid_rrf_k: int = 60
    #: 벡터 후보는 코사인이 `hybrid_cosine_min` 이상인 카드 중 위에서 이만큼
    hybrid_vector_top: int = 5
    #: 벡터 후보 문턱. '자료 없음'은 합치기 전 두 후보 목록이 모두 비었는지로만 정한다(합친 점수에는 문턱을 두지 않는다).
    #: 0.40 은 검색 평가 dev 에서 고른 값(recall@3 최고이면서 자료 없음 판정이 키워드보다 나쁘지 않음, 8회차)
    hybrid_cosine_min: float = 0.40
    #: 후보를 싣는 방식. UNION = 키워드 후보 ∪ 벡터 후보. AGREE = 두 신호가 함께 맞는 카드만(키워드가 `MIN_SCORE` 를 넘고
    #: 코사인이 `hybrid_agree_cosine` 이상), 한쪽이 아주 강하면(`hybrid_keyword_strong` · `hybrid_cosine_strong`) 그것만으로도 싣는다.
    #: 무관한 질문에도 2글자 조각이 겹치거나 코사인이 0.3~0.4 대로 나와 '자료 없음'을 놓치는 것을 줄이려는 안(8회차 M2b)
    hybrid_gate: Literal["UNION", "AGREE"] = "UNION"
    hybrid_agree_cosine: float = 0.30
    hybrid_keyword_strong: float = 30.0
    hybrid_cosine_strong: float = 0.60
    #: 질문 임베딩 호출 한 번의 시간 제한(초). 실행당 한 번만 부르고 다시 시도하지 않는다
    embed_timeout_seconds: float = 2.0
    #: 조사에 남은 시간이 이보다 짧으면 임베딩을 건너뛰고 키워드만 쓴다
    embed_min_seconds: float = 0.3
    #: 카드 · 질문 임베딩 차원(`dimensions` 인자). 자료 파일과 같아야 한다
    embed_dimensions: int = 1536
    # ── 단계 사이 넘겨주기(자료 근거 답의 구체화, 각각 따로 켜고 꺼서 잰다) ─────────────────────
    #: 계획 → 조사: 계획이 쓴 자료 검색어(`guide_queries`)를 조사 첫머리에 코드가 먼저 찾는다(질문 임베딩은 한 번에 묶어서)
    plan_guide_queries: bool = True
    #: 계획이 낼 수 있는 검색어 수(넘는 것은 자른다)
    max_guide_queries: int = 3
    #: 조사 → 답 쓰기: 이번 실행의 자료 검색이 돌려준 카드의 문장 후보(`guide_candidates`)를 답 쓰기 데이터 칸 끝에 준다
    answer_guide_candidates: bool = True
    #: 답 쓰기에 주는 문장 후보 수의 상한(문장 총합)
    max_guide_candidates: int = 12
    #: 검사 → 고치기: 걸린 문장에 이미 이은 근거의 화면 표기 값 · 이은 자료 카드의 문장 후보만 더해 준다(새 근거 · 자료 id 는 주지 않는다)
    fix_materials: bool = True
    # ── 답 품질 보강(각각 따로 켜고 꺼서 잰다) ──────────────────────────────────────
    #: 기록 질문을 '기록 말고 일반 조언으로'라고 해도 기록으로 답한다(6-1 · 13-2): 계획이 기록 대상(요청 · 측정 초안)을 집었고
    #: 이번 실행이 그 대상의 검증된 기록을 읽었는데 답에 기록 문장이 하나도 없으면, 그 근거로 그린 코드 기록 문장을 더한다
    record_answer_guard: bool = True
    #: '몇 번 · 몇 회 · 횟수' 질문은 횟수 지표(`filler_count` · `pause_count`)로: 계획 지침에 알리고, 조사 흐름이 읽는
    #: Take 전체 지표값(`slides`)에 횟수 지표도 싣는다
    count_metric_hint: bool = True
    #: 사용자 말로 대화 목표의 결과를 물으면(`get_goals`), 목표를 닫은 Take 로 코드가 판정해 목표 결과 문장을 코드 템플릿으로 낸다(7-10)
    goal_status_sentence: bool = True

    @model_validator(mode="after")
    def _goal_metrics_match_direction(self) -> ChatAgentConfig:
        expected = {"HIGHER": "gt_base", "LOWER": "lt_base", "RANGE": "in_range"}
        for metric, gm in self.goal_metrics.items():
            direction = self.direction.get(metric)
            if direction is None or gm.operators[0] != expected[direction]:
                raise ValueError(f"목표 지표 {metric} 의 operator 가 비교 방향 표와 맞지 않는다")
        return self

    def config_hash(self) -> str:
        """재현성 키(12자리). 같은 해시면 같은 입력에 같은 판정 · 검사 결과가 나온다."""
        blob = json.dumps(_thaw(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def criteria_version(self) -> str:
        """`criteria_versions.chat_agent` = <feature_version>+<설정 해시 12자리>"""
        return f"{FEATURE_VERSION}+{self.config_hash()}"

    def goal_rule_version(self) -> str:
        """목표 판정 규칙 판 = <계산식 판>+<비교 방향 · 목표 지표 · min_delta 해시 12자리>. 저장 때 목표에 고정한다(9절)."""
        blob = json.dumps(
            {
                "algorithm": GOAL_ALGORITHM_VERSION,
                "direction": _thaw(self.direction),
                "goal_metrics": _thaw(self.goal_metrics),
                "display_decimals": _thaw(self.display_decimals),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        # 앞부분은 계산식 판, 뒷부분은 저장 때의 설정 해시. 판정 지원 여부는 앞부분으로 가린다(version.SUPPORTED_GOAL_ALGORITHMS)
        return f"{GOAL_ALGORITHM_VERSION}+" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

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
