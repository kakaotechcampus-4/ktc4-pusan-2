"""요청 · 응답 · LLM 출력 스키마 (= 계약, docs/ai/features/script-parser.md 5절)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 요청: 모르는 필드는 무시한다
class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    script_text: str = Field(max_length=50_000, description="대본 원문. 50,000자 이하")
    target_ms: int | None = Field(default=None, ge=1, description="발표 제한 시간(ms). 없으면 null")


# 응답: 정해진 필드만 낸다
class Highlight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0, description="키워드 시작 위치(코드 포인트)")
    end: int = Field(ge=0, description="키워드 끝 위치(코드 포인트, 미포함)")


class Slide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_number: int = Field(ge=1, description="구분자에 적힌 번호. fail이면 1")
    script: str = Field(description="정제한 장 대본")
    script_chars: int = Field(ge=0, description="공백 · 문장부호를 뺀 글자 수(코드 포인트)")
    keywords: list[str] = Field(description="대본 표현 그대로의 중요 단어, 대본 순서, 중복 없음")
    highlights: list[Highlight] = Field(
        description="키워드마다 장 대본에서 처음 나오는 위치, 키워드 순서"
    )
    expected_ms: int = Field(ge=0, description="예상 발표 시간(ms)")


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    feature_version: str
    model: str | None = Field(
        default=None, description="실제로 응답한 LLM 모델. 서비스 라우터가 넣는다"
    )


class ParseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "fail"] = Field(description="구분자로 대본 전체가 나뉘었는가")
    slides: list[Slide] = Field(description="장별 결과. fail이면 정확히 1개")
    terms: list[str] = Field(
        max_length=50, description="STT가 틀리기 쉬운 고유명사 · 용어, 중요한 순서"
    )
    meta: Meta


# LLM 출력: 제목 줄 · 키워드 · 용어만 고른다
class LLMSlide(BaseModel):
    slide_number: int = Field(description="장 후보의 번호")
    title_lines: list[str] = Field(
        description="이 장 안에서 발표할 말이 아닌 제목 줄. 대본 줄 그대로"
    )
    keywords: list[str] = Field(description="이 장 대본 표현 그대로의 중요 단어 3–7개")


class LLMOutput(BaseModel):
    preface_title_lines: list[str] = Field(
        description="첫 구분자 앞 글에서 제목인 줄. 대본 줄 그대로"
    )
    slides: list[LLMSlide] = Field(description="장 후보마다 제목 줄 · 키워드")
    terms: list[str] = Field(
        description="STT가 틀리기 쉬운 고유명사 · 약어 · 전문 용어, 틀릴 가능성이 높은 순서"
    )
