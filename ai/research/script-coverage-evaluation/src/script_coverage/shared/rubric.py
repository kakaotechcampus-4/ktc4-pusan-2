"""평가 기준(Evaluation Rubric) 모델과 상수. 대본 분석과 STT 평가 사이의 약속이다."""

from typing import Literal

from pydantic import BaseModel, Field

from .facts import CriticalFact, Importance
from .text import Sentence


class Keyword(BaseModel):
    term: str
    score: float = Field(description="TF-IDF 점수 (슬라이드 안에서 L2 정규화)")
    tf: int = Field(description="슬라이드 안 등장 횟수")


class KeyPoint(BaseModel):
    id: str
    content: str
    importance: Importance = Field(description="근거 문장의 역할로 코드가 정한다 (ROLE_IMPORTANCE)")
    sentence_indices: list[int] = Field(description="근거 문장 번호 (LLM 이 번호로 지정)")
    source_quote: str = Field(description="근거 문장들의 원문")
    source_span: tuple[int, int] | None = Field(
        description="근거 문장들의 위치. 유효한 번호가 없으면 None"
    )
    key_terms: list[str]
    fact_ids: list[str] = Field(description="이 Key Point 가 직접 말하는 Critical Fact")


class RubricMeta(BaseModel):
    rubric_id: str = Field(
        description="대본·LLM 설정·스키마가 같으면 같은 값. 평가 결과가 어떤 기준으로 채점됐는지 가리킨다"
    )
    script_name: str = Field(description="입력 JSON 파일 이름 (확장자 제외)")
    slide_number: int
    content_hash: str
    rubric_schema_version: str
    llm_model: str
    llm_config_hash: str = Field(description="1차 분석 LLM 설정 해시")
    final_config_hash: str = Field(
        default="", description="최종 결론 LLM 설정 해시. 1차 결과(초안)면 빈 값"
    )
    unit_config_hash: str = Field(
        default="", description="전달 단위 LLM 설정 해시. 단위를 나누기 전이면 빈 값"
    )
    created_at: str


class NameDecision(BaseModel):
    value: str
    source: Literal["rule", "llm"]
    keep: bool = Field(description="발표자가 이 말을 그대로 해야 하는가 (True 면 Critical Fact)")
    reason: str


class ContentUnit(BaseModel):
    """전달 단위: 대본 문장 속 정보 하나 (주장·사실·수치·나열 항목 하나). STT 평가는 단위마다 말했는지 판정하고, 문장 판정은 코드가 모은다."""

    id: str = Field(description="S{문장 번호}-U{순번}")
    sentence_index: int
    text: str = Field(description="단위의 내용을 짧게 쓴 것")
    quote: str = Field(description="대본 문장 속 해당 표현 (대본 표기 그대로)")
    span: tuple[int, int] = Field(description="quote 의 정규화 대본 기준 위치")
    fact_ids: list[str] = Field(default_factory=list, description="이 단위 안에 있는 Critical Fact")
    source: Literal["llm", "fact", "sentence"] = Field(
        description="llm: LLM 이 나눔 / fact: 어느 단위에도 안 들어간 수치·이름을 코드가 보탬 / sentence: 나누지 못해 문장 전체"
    )


class EvaluationRubric(BaseModel):
    meta: RubricMeta
    normalized_script: str
    sentences: list[Sentence]
    sentence_roles: list[str] = Field(
        description="문장별 역할 (claim / evidence / detail / skip). sentences 와 순서가 같다"
    )
    core_claim: str
    key_points: list[KeyPoint]
    critical_facts: list[CriticalFact]
    keywords: list[Keyword]
    warnings: list[str]
    draft_warnings: list[str] = Field(
        default_factory=list, description="1차 결과에 대한 코드 검증 경고 (최종 결론의 입력)"
    )
    review_changes: list[str] = Field(
        default_factory=list, description="최종 결론이 1차 결과에서 바꾼 점"
    )
    name_decisions: list[NameDecision] = Field(
        default_factory=list, description="이름·용어 후보별 최종 결정"
    )
    content_units: list[ContentUnit] = Field(
        default_factory=list, description="문장별 전달 단위 (6-2장). skip 문장은 나누지 않는다"
    )


IMPORTANCE_RANK = {"normal": 0, "high": 1, "critical": 2}
# Key Point 의 중요도 = 근거 문장 역할 중 가장 높은 것
ROLE_IMPORTANCE = {"claim": "critical", "evidence": "high", "detail": "normal", "skip": "normal"}
# 채점할 때 Key Point·사실의 가중치 (STT 평가 노트북의 점수 계산과 12장 일관성 측정에서 쓴다)
SCORE_WEIGHT = {"critical": 3, "high": 2, "normal": 1}


NAME_TYPES = ("proper_noun", "term")
