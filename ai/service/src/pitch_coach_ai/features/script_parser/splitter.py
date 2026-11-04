"""구분자로 장 후보를 나눕니다. LLM 없이 도는 순수 함수만 둡니다."""

import re
from dataclasses import dataclass
from typing import Literal

# 줄 맨 앞(앞 공백 허용)의 [N] · [슬라이드 N] · 슬라이드 N: 만 구분자.
# N은 1~4자리이고, 더 길면 본문 줄로 본다(아주 긴 번호에서 int()가 ValueError를 내지 않게)
DELIMITER_RE = re.compile(
    r"^[ \t]*(?:\[(\d{1,4})\]|\[슬라이드[ \t]+(\d{1,4})\]|슬라이드[ \t]+(\d{1,4}):)"
)


@dataclass(frozen=True)
class SlideCandidate:
    slide_number: int
    text: str  # 구분자를 뺀 그 장의 글. 구분자 뒤 같은 줄의 글도 본문이다


@dataclass(frozen=True)
class SplitResult:
    status: Literal["success", "fail"]
    preface: str  # 첫 구분자 앞 글(머리글). fail이면 빈 문자열
    slides: list[SlideCandidate]  # fail이면 대본 전체가 장 1개(slide_number 1)


def find_slides(script_text: str) -> SplitResult:
    """줄 맨 앞 구분자로 머리글과 장 후보를 나눕니다.

    구분자가 없거나 번호가 0 · 겹침 · 앞 장보다 작으면 fail이고,
    구분자를 뺀 대본 전체를 장 1개(slide_number 1)로 둡니다.
    머리글에 본문이 있는지(fail 전환)는 LLM이 제목 줄을 고른 뒤 판정합니다.
    """
    preface: list[str] = []
    slides: list[tuple[int, list[str]]] = []
    stripped: list[str] = []  # fail일 때 쓸, 구분자를 뺀 대본 전체
    valid = True

    for line in script_text.splitlines():
        match = DELIMITER_RE.match(line)
        if match is None:
            (slides[-1][1] if slides else preface).append(line)
            stripped.append(line)
            continue

        number = int(next(g for g in match.groups() if g is not None))
        if number == 0 or (slides and number <= slides[-1][0]):
            valid = False
        rest = line[match.end() :].strip()
        slides.append((number, [rest] if rest else []))
        if rest:
            stripped.append(rest)

    if not slides or not valid:
        whole = "\n".join(stripped) if slides else script_text
        return SplitResult(status="fail", preface="", slides=[SlideCandidate(1, whole)])

    return SplitResult(
        status="success",
        preface="\n".join(preface),
        slides=[SlideCandidate(number, "\n".join(lines)) for number, lines in slides],
    )
