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
from .stt_evaluation.schemas import SlideEvaluation


class ErrorBody(BaseModel):
    code: str
    message: str


class SlideFailure(BaseModel):
    """처리하지 못한 장 하나. 나머지 장의 결과는 그대로 돌려준다."""

    slide_number: int
    error: ErrorBody


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

    slide_number: int
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


class EvaluationResponse(BaseModel):
    take_id: str
    evaluations: list[SlideEvaluation]
    take_scores: TakeScores
    confirmations: list[Confirmation]
    failed: list[SlideFailure]
    meta: ScoringMeta
