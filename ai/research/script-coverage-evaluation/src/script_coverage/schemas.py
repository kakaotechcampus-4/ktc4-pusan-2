"""API 요청 · 응답 모델 (계약). 서비스가 이 모양 그대로 받고 낸다.

입력 모델은 모르는 필드를 무시하고 출력 모델은 정해진 필드만 낸다 (pydantic 기본).
LLM 출력 스키마(`script_analysis/schemas.py` · `stt_evaluation/schemas.py`)와는 다르다: 이 파일은
BE 와 주고받는 값이고, LLM 출력 스키마는 바꾸면 설정 해시가 달라져 캐시를 쓸 수 없다.

- `/scripts/rubric`: `RubricRequest` → `RubricResponse`
"""

from pydantic import BaseModel, Field, model_validator

from .shared.rubric import EvaluationRubric


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
