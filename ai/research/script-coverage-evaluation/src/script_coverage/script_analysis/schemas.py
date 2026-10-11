"""대본 분석의 입력 모델과 LLM 출력 스키마.

LLM 출력 스키마(클래스 이름 · docstring · 필드 순서 · 설명)는 `model_json_schema()` 로 설정 해시에 들어간다.
고치면 캐시가 모두 무효가 되므로 평가 기준을 바꿀 때만 고친다.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SlideScript(BaseModel):
    """입력 JSON 의 한 항목: 슬라이드 한 장의 대본."""

    slide_number: int = Field(description="슬라이드 번호")
    script: str = Field(description="슬라이드 대본 원문")


SentenceRole = Literal["claim", "evidence", "detail", "skip"]


class SentenceLabel(BaseModel):
    id: int = Field(description="문장 번호. 입력의 [S번호]")
    role: SentenceRole = Field(description="claim | evidence | detail | skip")


class KeyPointDraft(BaseModel):
    content: str = Field(
        description="핵심 내용 한 문장. '~이다/~한다'로 끝나는 평서문. 대본의 수치·고유명사는 그대로 유지"
    )
    sentence_ids: list[int] = Field(description="근거 문장 번호. 이어지는 문장 1개 이상")
    key_terms: list[str] = Field(
        description="이 핵심 내용을 전달하려면 반드시 말해야 하는 이름·용어. 대본 표기 그대로. 0~3개"
    )


class SlideSemanticAnalysis(BaseModel):
    # 필드 순서가 생성 순서다: 문장 역할을 먼저 모두 정한 뒤 그것을 바탕으로 Key Point 를 만든다
    sentence_roles: list[SentenceLabel] = Field(
        description="모든 문장의 역할. 입력 문장마다 하나씩"
    )
    core_claim: str = Field(description="이 슬라이드가 청중에게 남기려는 핵심 주장 한 문장")
    key_points: list[KeyPointDraft] = Field(
        description="발표자가 반드시 전달해야 하는 핵심 내용 목록"
    )


class FinalKeyPoint(BaseModel):
    content: str = Field(
        description="핵심 내용 한 문장. '~이다/~한다'로 끝나는 평서문. 대본의 수치·고유명사는 그대로 유지"
    )
    sentence_ids: list[int] = Field(description="근거 문장 번호. 이어지는 문장 1개 이상")


class NameVerdict(BaseModel):
    candidate_id: str = Field(description="이름·용어 후보 번호 (N1, N2 …)")
    keep: bool = Field(description="발표자가 이 말을 그대로 해야 전달된 것으로 볼 수 있으면 true")
    reason: str = Field(description="짧은 이유")


class FinalReview(BaseModel):
    # 필드 순서가 생성 순서다: 문장 역할을 먼저 확정한 뒤 Key Point 와 이름·용어를 정한다
    sentence_roles: list[SentenceLabel] = Field(description="최종 문장 역할. 모든 문장에 하나씩")
    core_claim: str = Field(description="최종 핵심 주장 한 문장")
    key_points: list[FinalKeyPoint] = Field(description="최종 Key Point")
    name_verdicts: list[NameVerdict] = Field(description="이름·용어 후보마다 하나씩")
    changes: list[str] = Field(description="1차 분석에서 바꾼 점과 이유. 바꾼 게 없으면 빈 목록")


class UnitDraft(BaseModel):
    text: str = Field(
        description="전달 단위 하나를 짧은 서술로 ('~함' 꼴). 대본에 없는 내용은 넣지 않는다"
    )
    quote: str = Field(
        description="이 단위의 핵심 표현을 대본 문장에서 그대로 복사한 것 (띄어쓰기·표기 그대로, 몇 어절)"
    )


class SentenceUnitsDraft(BaseModel):
    sentence_id: int = Field(description="문장 번호. 입력의 [S번호]")
    units: list[UnitDraft] = Field(description="이 문장의 전달 단위")


class SlideUnits(BaseModel):
    sentences: list[SentenceUnitsDraft] = Field(description="나눌 대상 문장마다 하나씩")
