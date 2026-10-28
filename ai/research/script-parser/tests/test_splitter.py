import pytest

from script_parser.splitter import SlideCandidate, find_slides


@pytest.mark.parametrize(
    "text",
    [
        "[1] 첫 장\n[2] 둘째 장",
        "[슬라이드 1] 첫 장\n[슬라이드 2] 둘째 장",
        "슬라이드 1: 첫 장\n슬라이드 2: 둘째 장",
        "  [1] 첫 장\n\t[2] 둘째 장",  # 앞 공백 허용
    ],
)
def test_three_formats(text):
    result = find_slides(text)
    assert result.status == "success"
    assert result.slides == [SlideCandidate(1, "첫 장"), SlideCandidate(2, "둘째 장")]


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


@pytest.mark.parametrize(
    "text",
    [
        "첫째, 시장입니다.\n다음으로 성과입니다.",  # 서수 · 전환 표현
        "본문 중간의 [1] 표시",  # 줄 맨 앞이 아님
        "1. 시장\n2. 성과",  # 번호 목록
        "슬라이드 1\n본문",  # 콜론 없음
        "[Slide 1] 본문",  # 영문 형식
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
