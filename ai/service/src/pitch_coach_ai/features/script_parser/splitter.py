"""구분자로 장 후보를 나눕니다. LLM 없이 도는 순수 함수만 둡니다."""

import re
from dataclasses import dataclass
from typing import Literal

# 구분자는 줄 맨 앞(앞 공백 허용, 그 뒤 마크다운 제목 표시 "#"~"######" 허용)의
#   ① 대괄호: [N] · [슬라이드 N] · [Slide N]                  → 뒤 같은 줄 글은 본문
#   ② 콜론:   슬라이드 N: · Slide N:                          → 뒤 같은 줄 글은 본문
#   ③ 줄 전체: 슬라이드 N · Slide N (뒤에 글이 없음)
#   ④ 제목 표시 뒤: ## Slide N — 제목 · # 슬라이드 N 제목     → 구분 기호를 걷은 뒤 글은 본문
# 이다. "슬라이드"와 N 사이 공백은 없어도 되고, "Slide"는 대소문자를 가리지 않는다.
# 제목 표시가 없는 "슬라이드 3 보시면 …"은 문장일 수 있어 구분자가 아니다.
# 번호 목록(1. · 1))은 본문 나열과 헷갈려 구분자가 아니다.
# 대괄호 바로 뒤가 "("면 마크다운 링크([3](https://…))라 구분자가 아니다.
# 제목 표시 뒤 구분 기호 바로 다음이 숫자면 범위 · 소수(## Slide 1-2 · 3.14)라 구분자가 아니다.
# N은 1~4자리이고, 더 길면 본문 줄로 본다(아주 긴 번호에서 int()가 ValueError를 내지 않게)
_HEADING_RE = re.compile(r"#{1,6}[ \t]+")
_WORD = r"(?:슬라이드|slide)[ \t]*"
_BRACKET_RE = re.compile(rf"\[(?:{_WORD})?(\d{{1,4}})\](?!\()", re.IGNORECASE)
_WORD_RE = re.compile(rf"{_WORD}(\d{{1,4}})(?!\d)", re.IGNORECASE)
_COLON_RE = re.compile(r"[ \t]*[:：]")
# 제목 표시 뒤 번호와 제목 사이는 공백이나 구분 기호(— – - | : .)로 띄운다 ("## Slide 1a"는 아님)
_HEADING_SEP_RE = re.compile(r"[ \t]*[—–\-|:：.]?[ \t]+|[ \t]*[—–\-|:：.]")
# 구분 기호 바로 다음 숫자: 범위(1-2) · 소수(3.14)
_HEADING_RANGE_RE = re.compile(r"[ \t]*[—–\-|.]\d")


def match_delimiter(line: str) -> tuple[int, str] | None:
    """구분자 줄이면 (장 번호, 구분자 뒤 같은 줄의 글)을, 아니면 None을 돌려줍니다."""
    rest = line.lstrip(" \t")
    heading = _HEADING_RE.match(rest)
    if heading:
        rest = rest[heading.end() :]

    if match := _BRACKET_RE.match(rest):
        return int(match[1]), rest[match.end() :].strip()

    match = _WORD_RE.match(rest)
    if match is None:
        return None
    number, rest = int(match[1]), rest[match.end() :]
    if colon := _COLON_RE.match(rest):
        return number, rest[colon.end() :].strip()
    if not rest.strip():
        return number, ""
    if heading and not _HEADING_RANGE_RE.match(rest) and (sep := _HEADING_SEP_RE.match(rest)):
        return number, rest[sep.end() :].strip()
    return None


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
        delimiter = match_delimiter(line)
        if delimiter is None:
            (slides[-1][1] if slides else preface).append(line)
            stripped.append(line)
            continue

        number, rest = delimiter
        if number == 0 or (slides and number <= slides[-1][0]):
            valid = False
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
