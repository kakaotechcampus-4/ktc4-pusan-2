"""평가 기준 분석 Structured Output 스키마."""

from pydantic import BaseModel, Field


# 주의: json_schema strict 모드는 dict[str, ...]처럼 키가 고정되지 않은 필드를 지원하지 않으므로
# 평가 요소를 고정 필드로 갖는 모델로 표현
class Criteria(BaseModel):
    speed: str | None = Field(description="발표 속도")
    pause: str | None = Field(description="지나치게 긴 침묵 횟수")
    volume: str | None = Field(description="발표 볼륨")
    filler: str | None = Field(description="발표 중 filler 단어 사용 횟수")
    gaze: str | None = Field(description="발표 중 시선 처리")
    script_used: bool | None = Field(description="스크립트 사용 여부")
    script_dependency: str | None = Field(description="스크립트 의존도")
    script_similarity: str | None = Field(description="스크립트와 발표 내용의 유사도")


class EvaluationCriteriaAnalysis(BaseModel):
    display_criteria: list[str] = Field(description="사용자 입력 표현을 살려 짧게 다듬은 평가 기준 목록 (예: \"추임새 5번 이하\")")
    values: Criteria = Field(description="평가 요소별 기준 값 (기준이 없으면 null)")
    excluded: list[str] = Field(description="평가 기준으로 사용하지 않은 평가 항목 이름(명사구) 목록 (예: \"발표 시간\")")
