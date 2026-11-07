import json
from pathlib import Path

import pytest

from pitch_coach_ai.features.script_parser.splitter import (
    SlideCandidate,
    find_slides,
    match_delimiter,
)

# FE · AI 공통 입력 목록 (#819 · #1023). FE slideMarkers.test.ts도 같은 결과를 내야 한다
MARKERS = json.loads(
    (Path(__file__).resolve().parents[5] / "docs/ai/features/script-parser-markers.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", MARKERS["lines"], ids=lambda c: repr(c["line"]))
def test_shared_line_cases(case):
    delimiter = match_delimiter(case["line"])
    assert (None if delimiter is None else delimiter[0]) == case["number"]


@pytest.mark.parametrize("case", MARKERS["scripts"], ids=lambda c: c["name"])
def test_shared_script_cases(case):
    numbers = [d[0] for line in case["script"].splitlines() if (d := match_delimiter(line))]
    assert numbers == case["numbers"]
    assert find_slides(case["script"]).status == case["status"]


@pytest.mark.parametrize(
    "text",
    [
        "[1] 첫 장\n[2] 둘째 장",
        "[슬라이드 1] 첫 장\n[슬라이드 2] 둘째 장",
        "[Slide 1] 첫 장\n[slide 2] 둘째 장",  # 영문은 대소문자 무관
        "[슬라이드1] 첫 장\n[Slide2] 둘째 장",  # 번호 앞 공백 없음
        "슬라이드 1: 첫 장\n슬라이드 2: 둘째 장",
        "Slide 1: 첫 장\nSLIDE 2: 둘째 장",
        "슬라이드1 : 첫 장\n슬라이드 2： 둘째 장",  # 콜론 앞 공백 · 전각 콜론
        "슬라이드 1\n첫 장\n슬라이드 2\n둘째 장",  # 줄 전체 (FE가 다시 조립한 대본의 형식)
        "Slide 1\n첫 장\nslide 2  \n둘째 장",
        "## Slide 1 — 첫 장\n## Slide 2 - 둘째 장",  # 제목 표시 뒤: 구분 기호는 걷는다
        "# 슬라이드 1 첫 장\n### 슬라이드 2: 둘째 장",
        "## [1] 첫 장\n## [Slide 2] 둘째 장",
        "  [1] 첫 장\n\t[2] 둘째 장",  # 앞 공백 허용
    ],
)
def test_delimiter_formats(text):
    result = find_slides(text)
    assert result.status == "success"
    assert result.slides == [SlideCandidate(1, "첫 장"), SlideCandidate(2, "둘째 장")]


def test_whole_line_delimiters_from_fe_rebuild():
    # FE가 매핑에서 장 대본을 고치면 원문을 "슬라이드 N\n<장 대본>"으로 다시 조립한다
    blocks = ["1장입니다", "2번입니다", "일반물리2 수업은 3:00 PM 입니다."]
    text = "\n\n".join(f"슬라이드 {i}\n{block}" for i, block in enumerate(blocks, 1))
    result = find_slides(text)
    assert result.status == "success"
    assert [s.slide_number for s in result.slides] == [1, 2, 3]
    assert [s.text.strip() for s in result.slides] == blocks


def test_body_on_following_lines():
    result = find_slides("[1]\n안녕하세요.\n반갑습니다.\n[2] 시장 현황\n지난 분기")
    assert result.slides == [
        SlideCandidate(1, "안녕하세요.\n반갑습니다."),
        SlideCandidate(2, "시장 현황\n지난 분기"),
    ]


def test_preface():
    result = find_slides("# 성과 보고\n\n[1] 안녕하세요.")
    assert result.status == "success"
    assert result.preface == "# 성과 보고\n"
    assert result.slides == [SlideCandidate(1, "안녕하세요.")]


def test_numbers_kept_as_written():
    result = find_slides("[1] a\n[3] b\n[5] c")
    assert [s.slide_number for s in result.slides] == [1, 3, 5]


# 형식마다 번호를 읽는 갈래가 달라 형식마다 본다 (한 갈래만 고쳐지는 일을 막는다)
FORMATS = [
    "[{n}]",
    "[슬라이드 {n}]",
    "[Slide {n}]",
    "슬라이드 {n}:",
    "Slide {n}:",
    "## Slide {n} —",
]


@pytest.mark.parametrize("fmt", FORMATS)
def test_four_digit_number_is_delimiter(fmt):
    result = find_slides(f"{fmt.format(n=1)} a\n{fmt.format(n=9999)} b")
    assert result.status == "success"
    assert result.slides == [SlideCandidate(1, "a"), SlideCandidate(9999, "b")]


@pytest.mark.parametrize("fmt", ["슬라이드 {n}", "Slide {n}"])
def test_four_digit_number_whole_line(fmt):
    result = find_slides(f"{fmt.format(n=1)}\na\n{fmt.format(n=9999)}\nb")
    assert result.slides == [SlideCandidate(1, "a"), SlideCandidate(9999, "b")]


@pytest.mark.parametrize("fmt", FORMATS)
def test_long_number_line_stays_in_slide(fmt):
    long_line = f"{fmt.format(n=12345)} b"
    result = find_slides(f"{fmt.format(n=1)} a\n{long_line}")
    assert result.status == "success"
    assert result.slides == [SlideCandidate(1, f"a\n{long_line}")]


@pytest.mark.parametrize(
    "text",
    [
        "첫째, 시장입니다.\n다음으로 성과입니다.",  # 서수 · 전환 표현
        "본문 중간의 [1] 표시",  # 줄 맨 앞이 아님
        "1. 시장\n2. 성과",  # 번호 목록
        "1) 시장\n2) 성과",
        "## 01. 문제 상황\n본문",  # 제목 표시가 있어도 번호 목록은 아님
        "슬라이드 3 보시면 매출이 늘었습니다.",  # 제목 표시 없이 뒤에 글이 붙음: 문장일 수 있다
        "Slide 2 shows the result.",
        "## Slide 1a\n본문",  # 번호 뒤에 바로 글자
        "슬라이드 제작 팁\n본문",  # 번호 없음
        "#Slide 1\n본문",  # '#' 뒤 공백 없음은 마크다운 제목이 아님
        "[12345] 본문",  # 5자리 번호
        "[슬라이드 12345] 본문",
        "슬라이드 12345: 본문",
        "슬라이드 12345\n본문",
        "## Slide 12345 — 본문",
        f"[{'1' * 4301}] 본문",  # int() 자릿수 한도를 넘는 번호도 오류 없이 본문
    ],
)
def test_not_delimiter(text):
    result = find_slides(text)
    assert result.status == "fail"
    assert result.preface == ""
    assert result.slides == [SlideCandidate(1, text)]


@pytest.mark.parametrize(
    "text",
    [
        "[0] a\n[1] b",  # 0
        "[1] a\n[1] b",  # 겹침
        "[2] a\n[1] b",  # 거꾸로
    ],
)
def test_bad_numbers_fail(text):
    result = find_slides(text)
    assert result.status == "fail"
    assert result.slides == [SlideCandidate(1, "a\nb")]


def test_bad_numbers_keep_preface_in_whole():
    result = find_slides("제목\n[2] a\n[1] b")
    assert result.slides == [SlideCandidate(1, "제목\na\nb")]
