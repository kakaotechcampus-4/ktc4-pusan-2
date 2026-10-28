"""평가 기준 분석 Structured Output 스키마와 응답 모델."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from .config import DEFAULT

# 지표 이름은 architecture.md 6-3 그대로
Metric = Literal[
    "cpm",
    "pause_count",
    "long_silence_count",
    "voice_diff_db",
    "filler_count",
    "filler_per_min",
    "audience_ratio",
    "reading_ratio",
    "content_coverage",
]
Operator = Literal["LTE", "GTE", "LT", "GT"]


def _within_display_chars(text: str) -> str:
    # 자르면 숫자 · 조건이 잘리거나 targets[].display 짝이 깨지므로 스키마 위반으로 본다 (문서 6절 2번)
    if len(text) > DEFAULT.max_display_chars:
        raise ValueError(f"표시 기준이 {DEFAULT.max_display_chars}자를 넘는다 ({len(text)}자)")
    return text


# JSON 스키마에 maxLength 를 싣지 않고 응답을 검증할 때만 본다 (strict 모드 호환)
DisplayCriterion = Annotated[str, AfterValidator(_within_display_chars)]


class EvaluationCriteriaRequest(BaseModel):
    """POST /evaluation-criteria/parse 요청. 모르는 필드는 무시한다."""

    model_config = ConfigDict(extra="ignore")

    # 앞뒤 공백을 뺀 길이 1~2,000자 (문서 5-2)
    standard_text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


# 주의: json_schema strict 모드는 dict[str, ...]처럼 키가 고정되지 않은 필드를 지원하지 않으므로
# 평가 요소를 고정 필드로 갖는 모델로 표현
class Criteria(BaseModel):
    speed: str | None = Field(description="말 속도")
    pause: str | None = Field(description="말 사이 멈춤")
    silence: str | None = Field(description="지나치게 긴 침묵")
    volume: str | None = Field(description="목소리 크기")
    filler: str | None = Field(description="군더더기(추임새)")
    gaze: str | None = Field(description="청중을 보는 정도")
    script_dependency: str | None = Field(description="발표 중 대본을 보는 정도")
    script_similarity: str | None = Field(description="대본 내용을 얼마나 전달했나")
    script_used: bool | None = Field(description="대본을 봐도 되는가 (지표가 아닌 설정)")


class Target(BaseModel):
    display: str = Field(description="이 목표의 display_criteria 문장")
    metric: Metric = Field(description="지표 이름")
    operator: Operator = Field(description="LTE(이하) · GTE(이상) · LT(미만) · GT(초과)")
    value: float = Field(description="지표 단위의 숫자")


class EvaluationCriteriaAnalysis(BaseModel):
    display_criteria: list[DisplayCriterion] = Field(
        description='사용자 입력 표현을 살려 짧게 다듬은 평가 기준 목록 (예: "추임새 5번 이하")'
    )
    values: Criteria = Field(description="평가 요소별 기준 값 (기준이 없으면 null)")
    targets: list[Target] = Field(description="숫자로 읽히는 기준만 옮긴 숫자 목표 목록")
    excluded: list[str] = Field(
        description='평가 요소에 없는 기준, 사용자 표현 구절 그대로 (예: "발표는 5분 안에 끝내기")'
    )


class CriteriaMeta(BaseModel):
    schema_version: str
    feature_version: str
    model: str | None


class EvaluationCriteriaResponse(EvaluationCriteriaAnalysis):
    """POST /evaluation-criteria/parse 응답: LLM 출력 + meta."""

    meta: CriteriaMeta
