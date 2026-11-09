"""First Take 튜토리얼 샘플 자료. DB 를 모른다 (storage.py 와 같은 자리).

`sample/` 의 세 파일을 읽는다. 모두 dev/build-first-take-sample.py 가 만든다.
  first_take.pdf   FE `firstTakePdf.ts` 가 만든 5장짜리 발표자료
  first_take.webp  그 PDF 의 첫 장 썸네일
  first_take.json  발표 값과, 실제 파이프라인으로 한 번 만들어 둔 대본 파싱 · 장별 평가 기준 ·
                   첫 Take 코칭 계획

샘플 발표를 만들 때 이 결과를 복사해서 AI 를 부르지 않는다. 프로세스에서 한 번만 읽는다.
"""

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample"


@dataclass(frozen=True)
class SampleSlide:
    slide_number: int
    full_content: str
    highlights: list[list[int]]
    keywords: list[str]
    script_chars: int | None
    expected_ms: int | None
    rubric: dict[str, Any]
    rubric_meta: dict[str, Any]


@dataclass(frozen=True)
class SamplePlan:
    """첫 Take 의 코칭 계획 (`coaching_plans` 한 행). 바탕 Take 는 없다."""

    target_ms: int | None
    min_ms: int | None
    max_ms: int | None
    payload: dict[str, Any]
    meta: dict[str, Any]


@dataclass(frozen=True)
class FirstTakeSample:
    title: str
    time_limit_sec: int
    lower_deviation: int
    upper_deviation: int
    page_count: int
    # 대본 원문과 파싱 결과
    content: str
    segmented: bool
    terms: list[str]
    parse_meta: dict[str, Any] | None
    slides: tuple[SampleSlide, ...]
    plan: SamplePlan
    pdf: bytes
    thumbnail: bytes


@functools.cache
def first_take() -> FirstTakeSample:
    data = json.loads((SAMPLE_DIR / "first_take.json").read_text(encoding="utf-8"))
    script = data["script"]
    return FirstTakeSample(
        title=data["title"],
        time_limit_sec=data["time_limit_sec"],
        lower_deviation=data["lower_deviation"],
        upper_deviation=data["upper_deviation"],
        page_count=data["page_count"],
        content=script["content"],
        segmented=script["segmented"],
        terms=script["terms"],
        parse_meta=script["parse_meta"],
        slides=tuple(SampleSlide(**slide) for slide in data["slides"]),
        plan=SamplePlan(**data["coaching_plan"]),
        pdf=(SAMPLE_DIR / "first_take.pdf").read_bytes(),
        thumbnail=(SAMPLE_DIR / "first_take.webp").read_bytes(),
    )
