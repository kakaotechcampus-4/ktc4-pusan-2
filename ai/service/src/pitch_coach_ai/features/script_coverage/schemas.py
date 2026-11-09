"""API 요청 · 응답 모델 (계약). 서비스가 이 모양 그대로 받고 낸다.

입력 모델은 모르는 필드를 무시하고 출력 모델은 정해진 필드만 낸다 (pydantic 기본).
LLM 출력 스키마(`script_analysis/schemas.py` · `stt_evaluation/schemas.py`)와는 다르다: 이 파일은
BE 와 주고받는 값이고, LLM 출력 스키마는 바꾸면 설정 해시가 달라져 캐시를 쓸 수 없다.

- `/scripts/rubric`: `RubricRequest` → `RubricResponse`
- `/takes/analyze`: `AnalyzeRequest` → `EvaluationResponse`
- `/takes/rescore`: `RescoreRequest` → `EvaluationResponse` (analyze 와 같은 모양)
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .shared.rubric import EvaluationRubric
from .stt_evaluation.schemas import (
    CriticalGap,
    Expression,
    FactSummary,
    Fallback,
    Fidelity,
    Metric,
    MixItem,
    SlideEvaluation,
    SpeechMix,
    SttIntegrity,
    ZeroKind,
)


class ErrorBody(BaseModel):
    code: str
    message: str


class SlideFailure(BaseModel):
    """처리하지 못한 장 하나. 나머지 장의 결과는 그대로 돌려준다."""

    slide_number: int = Field(ge=1)
    error: ErrorBody
    fallback: Fallback | None = Field(
        default=None,
        description="간이 값 (선택 필드). 간이 값을 낼 수 없거나 예전 결과면 null",
    )


class ScriptSlide(BaseModel):
    slide_number: int = Field(ge=1)
    script: str = Field(description="대본 파싱이 낸 장별 대본 글. 빈 장도 싣는다")


class RubricRequest(BaseModel):
    """`/scripts/rubric` 요청: 대본의 모든 장과, 평가 기준을 만들 장 번호(선택)."""

    pitch_id: str
    script_version_id: str
    slides: list[ScriptSlide] = Field(min_length=1)
    slide_numbers: list[int] | None = Field(
        default=None,
        description="평가 기준을 만들 장 번호. 없으면 모든 장. 다른 장은 키워드 · 이름 판정의 맥락으로만 쓴다",
    )

    @model_validator(mode="after")
    def _check_slide_numbers(self):
        numbers = [s.slide_number for s in self.slides]
        if len(numbers) != len(set(numbers)):
            raise ValueError("slides 에 같은 slide_number 가 둘 이상 있다")
        unknown = sorted(set(self.slide_numbers or []) - set(numbers))
        if unknown:
            raise ValueError(f"slide_numbers 에 slides 에 없는 장이 있다: {unknown}")
        return self


class ResponseMeta(BaseModel):
    """`/scripts/rubric` 응답의 meta (architecture.md 5절)."""

    schema_version: str
    feature_version: str
    model: str | None


class RubricResponse(BaseModel):
    rubrics: list[EvaluationRubric]
    failed: list[SlideFailure]
    meta: ResponseMeta


class TakeSlide(BaseModel):
    slide_number: int = Field(ge=1)
    stt: str = Field(
        description="장 변경 기록으로 나눈 그 장의 STT 텍스트. 발표에서 넘어간 장은 빈 문자열"
    )
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="그 장 구간 길이의 합 (방문마다 더함). 없으면 STT 완전성 모름",
    )
    stt_gap_ms: int | None = Field(
        default=None, ge=0, description="그 장 구간 중 1초 기록의 STT 상태가 ok 가 아니던 시간"
    )
    stt_unobserved_ms: int | None = Field(
        default=None, ge=0, description="그 장 구간 중 1초 기록이 없던 시간"
    )
    script: str | None = Field(
        default=None,
        description="그 Take 의 대본 버전의 장 대본 글. 평가 기준이 없는 장의 간이 값에만 쓴다",
    )


class AnalyzeRequest(BaseModel):
    """`/takes/analyze` 요청: 장별 STT, 장별 평가 기준, 채점할 장 번호(선택)."""

    take_id: str
    slides: list[TakeSlide] = Field(min_length=1)
    rubrics: list[EvaluationRubric]
    slide_numbers: list[int] | None = Field(
        default=None, description="채점할 장 번호. 없으면 slides 의 장 전부"
    )

    @model_validator(mode="after")
    def _check_slide_numbers(self):
        numbers = [s.slide_number for s in self.slides]
        if len(numbers) != len(set(numbers)):
            raise ValueError("slides 에 같은 slide_number 가 둘 이상 있다")
        unknown = sorted(set(self.slide_numbers or []) - set(numbers))
        if unknown:
            raise ValueError(f"slide_numbers 에 slides 에 없는 장이 있다: {unknown}")
        numbers = [r.meta.slide_number for r in self.rubrics]
        if len(numbers) != len(set(numbers)):
            raise ValueError("rubrics 에 같은 slide_number 가 둘 이상 있다")
        return self


class Answer(BaseModel):
    """비슷한 말 하나에 대한 사용자 확인 답."""

    slide_number: int = Field(ge=1)
    item_id: str
    answer: Literal["as_script", "as_stt"]


class RescoreRequest(BaseModel):
    """`/takes/rescore` 요청: 저장해 둔 채점 전체 + 평가 기준 + 이번에 받은 확인 답 + 저장된 실패 장."""

    take_id: str
    evaluations: list[SlideEvaluation]
    rubrics: list[EvaluationRubric]
    answers: list[Answer] = Field(default_factory=list)
    failed: list[SlideFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_evaluations(self):
        numbers = [e.slide_number for e in self.evaluations]
        if len(numbers) != len(set(numbers)):
            raise ValueError("evaluations 에 같은 slide_number 가 둘 이상 있다")
        numbers = [r.meta.slide_number for r in self.rubrics]
        if len(numbers) != len(set(numbers)):
            raise ValueError("rubrics 에 같은 slide_number 가 둘 이상 있다")
        return self


class TakeScores(BaseModel):
    """채점된 장들의 점수로 낸 Take 값 (기능 문서 6-4)."""

    content_coverage: float | None
    critical_fact_accuracy: float | None
    script_fidelity: float | None
    similar_words: int
    similar_numbers: int
    scored_ratio: float = Field(description="채점된 장 수 ÷ 대상 장 수")


class Confirmation(BaseModel):
    """아직 답을 받지 않은 비슷한 말 하나."""

    slide_number: int
    item_id: str
    script_text: str
    stt_text: str


class ScoringMeta(BaseModel):
    """`/takes/analyze` · `/takes/rescore` 응답의 meta (architecture.md 5절)."""

    schema_version: str
    feature_version: str
    criteria_versions: dict[str, str]
    model: str | None


class SpeechMixBrief(BaseModel):
    """장 요약용 말의 구성 (상태 · 이유 · 화면 목록만)."""

    state: str
    reason_codes: list[str] = Field(default_factory=list)
    display: list[MixItem] = Field(default_factory=list)


class SlideSummary(BaseModel):
    """Take 상세 안의 장 요약 (`detail.slides[]`). 채점된 장 · 실패 장을 모두 담는다."""

    slide_number: int
    state: Literal["scored", "estimated", "failed"]
    reason_codes: list[str] = Field(default_factory=list)
    stt_integrity: SttIntegrity
    rubric_quality: Literal["full", "sentence_fallback"] | None
    delivery: Metric | None
    zero_kind: ZeroKind | None
    contradiction: Metric | None
    status_shares: dict[str, float] | None
    critical_gap_count: int
    delivery_band: Literal["mostly", "about_half", "little"] | None
    speech_mix: SpeechMixBrief | None


class ExcludedSlide(BaseModel):
    slide_number: int
    reason_code: str


class Scope(BaseModel):
    """Take 값의 평가 범위. 간이 값(`estimated`) 장은 Take 값에 넣지 않는다."""

    target_slides: int
    scored_slides: int
    estimated_slides: int
    provisional_slides: int
    excluded: list[ExcludedSlide] = Field(default_factory=list)


class TakeLegacy(BaseModel):
    content_coverage_raw: float | None = Field(
        description="장마다 0 으로 자르기 전 값의 가중 평균 (기존 Take 값은 자른 뒤 모은 값)"
    )


class TakeDetail(BaseModel):
    """Take 상세 (응답 맨 위 `detail`). `/takes/rescore` 는 받은 evaluations · failed 로 다시 만든다."""

    version: int = 1
    delivery: Metric
    zero_kind: ZeroKind | None
    contradiction: Metric
    status_shares: dict[str, float] | None
    key_points: dict[str, dict[str, int]]
    critical_gaps: list[CriticalGap] = Field(default_factory=list)
    legacy: TakeLegacy
    facts: FactSummary
    expression: Expression
    fidelity: Fidelity
    speech_mix: SpeechMix
    scope: Scope
    slides: list[SlideSummary] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    take_id: str
    evaluations: list[SlideEvaluation]
    take_scores: TakeScores
    confirmations: list[Confirmation]
    failed: list[SlideFailure]
    detail: TakeDetail | None = Field(default=None, description="Take 상세 (선택 필드)")
    meta: ScoringMeta
