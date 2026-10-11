"""LLM Structured Output 스키마."""

from typing import Literal
from pydantic import BaseModel, Field


# Slide 모델 정의
class Slide(BaseModel):
    slide_number: int | None = Field(description="슬라이드 번호. 1부터 시작하는 정수. 구분 불가능할 경우 None")
    script: str = Field(description="해당 슬라이드에 포함된 발표 대본")
    keywords: list[str] = Field(description="해당 슬라이드 대본에 포함된 키워드 목록")
    highlights: list[str] = Field(description="해당 슬라이드 대본에서 강조할 부분 인덱스 목록")


# 전체 결과 모델 정의
class SeparatedSlides(BaseModel):
    status: Literal["success", "fail"] = Field(description="슬라이드 구분 성공 여부")
    slides: list[Slide] = Field(description="슬라이드별 발표 대본 목록")
    terms: list[str] = Field(description="발표 대본에서 추출한 용어 목록")
