"""대본 → 장별 결과: 구분자 찾기(코드) → 제목 줄 · 키워드 · 용어 고르기(LLM) → 판정 · 정제 · 계산(코드)."""

from typing import Any

from .cleaner import clean_script, count_chars, find_highlights
from .config import BASE_CPM, MAX_KEYWORDS, MAX_KEYWORDS_FAIL, MAX_TERMS
from .prompts.parse import SYSTEM_PROMPT
from .schemas import LLMOutput, Meta, ParseRequest, ParseResponse, Slide
from .splitter import SplitResult, find_slides
from .version import FEATURE_VERSION, SCHEMA_VERSION


class EmptyScriptError(ValueError):
    """나눌 글이 없는 대본. 서비스가 422 EMPTY_SCRIPT로 바꿉니다."""


def parse_script(request: ParseRequest, llm: Any) -> ParseResponse:
    """대본 하나를 장별 결과로 만듭니다.

    llm은 LLMOutput 스키마가 걸린 Structured Output LLM입니다(run.py의 build_llm()).
    LLM 예외는 잡지 않고 올립니다. meta.model은 서비스 라우터가 넣습니다.
    """
    # 1. 구분자 찾기
    split = find_slides(request.script_text)
    # 2. 입력 검사: 구분자와 공백을 빼면 남는 글자가 없음 → LLM을 부르지 않는다
    if not split.preface.strip() and not any(s.text.strip() for s in split.slides):
        raise EmptyScriptError("대본에 글이 없습니다.")

    # 3. LLM 한 번
    picked = LLMOutput.model_validate(
        llm.invoke([("system", SYSTEM_PROMPT), ("user", _render(split))])
    )
    by_number = {}
    for s in picked.slides:
        by_number.setdefault(s.slide_number, s)
    titles = {n: {t.strip() for t in s.title_lines if t.strip()} for n, s in by_number.items()}
    keywords = {n: s.keywords for n, s in by_number.items()}

    # 4. 판정: 머리글에서 빈 줄 · 제목 줄을 빼고도 글이 남으면 fail
    status = split.status
    preface_titles = {t.strip() for t in picked.preface_title_lines if t.strip()}
    if status == "success" and any(
        line.strip() and line.strip() not in preface_titles for line in split.preface.splitlines()
    ):
        status = "fail"
        whole = "\n".join([split.preface] + [s.text for s in split.slides])
        # 대본 전체를 장 1개로: 제목 줄은 모두 걷어 내고 키워드는 장별 키워드를 합친다
        parts = [(1, whole, set().union(preface_titles, *titles.values()),
                  [k for s in split.slides for k in keywords.get(s.slide_number, [])])]
    elif status == "success":
        parts = [
            (s.slide_number, s.text, titles.get(s.slide_number, set()), keywords.get(s.slide_number, []))
            for s in split.slides
        ]
    else:
        only = split.slides[0]
        parts = [(1, only.text, titles.get(1, set()), keywords.get(1, []))]

    # 5. 정제 · 6. 계산
    limit = MAX_KEYWORDS if status == "success" else MAX_KEYWORDS_FAIL
    slides = []
    for number, text, title_set, picked_keywords in parts:
        body = "\n".join(line for line in text.splitlines() if line.strip() not in title_set)
        script = clean_script(body)
        kept = _keep(picked_keywords, script, limit)
        slides.append(
            {"slide_number": number, "script": script, "script_chars": count_chars(script),
             "keywords": kept, "highlights": find_highlights(script, kept)}
        )

    total = sum(s["script_chars"] for s in slides)
    if total == 0:
        raise EmptyScriptError("제목 줄을 빼면 대본에 글이 없습니다.")
    for s, ms in zip(slides, _expected_ms([s["script_chars"] for s in slides], request.target_ms)):
        s["expected_ms"] = ms

    return ParseResponse(
        status=status,
        slides=[Slide(**s) for s in slides],
        terms=_keep(picked.terms, request.script_text, MAX_TERMS),
        meta=Meta(schema_version=SCHEMA_VERSION, feature_version=FEATURE_VERSION),
    )


def _render(split: SplitResult) -> str:
    """LLM에게 줄 대본: 머리글과 장 후보를 블록으로 표시한다."""
    blocks = []
    if split.status == "fail":
        blocks.append("장을 나누지 못한 대본입니다. <장 1>에 대본 전체가 있습니다.")
    if split.preface.strip():
        blocks.append(f"<머리글>\n{split.preface}\n</머리글>")
    for s in split.slides:
        blocks.append(f"<장 {s.slide_number}>\n{s.text}\n</장 {s.slide_number}>")
    return "\n\n".join(blocks)


def _keep(words: list[str], text: str, limit: int) -> list[str]:
    """text에 그대로 있는 것만, 중복 없이, 순서를 지켜 limit개까지."""
    kept = []
    for word in words:
        if word.strip() and word in text and word not in kept:
            kept.append(word)
    return kept[:limit]


def _expected_ms(chars: list[int], target_ms: int | None) -> list[int]:
    """목표가 있으면 글자 수 비례 누적 반올림(합 = 목표), 없으면 기준 말 속도로 장마다 반올림."""
    if target_ms is None:
        return [(2 * c * 60_000 + BASE_CPM) // (2 * BASE_CPM) for c in chars]
    total = sum(chars)
    result, cum, prev = [], 0, 0
    for c in chars:
        cum += c
        now = (2 * target_ms * cum + total) // (2 * total)  # 반올림(target_ms × cum ÷ total)
        result.append(now - prev)
        prev = now
    return result
