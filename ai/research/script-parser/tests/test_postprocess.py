import pytest

from script_parser.postprocess import clean_script, find_highlights


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 헤더: "# " 형태만 제거
        ("# 제목\n본문", "제목\n본문"),
        ("### 소제목", "소제목"),
        ("#1 순위", "#1 순위"),
        ("#해시태그", "#해시태그"),
        # 굵게 / 기울임
        ("**핵심**을 보면", "핵심을 보면"),
        ("*기울임* 표현", "기울임 표현"),
        ("4 * 6 * 2", "4 * 6 * 2"),
        ("4*6", "4*6"),
        ("2**10", "2**10"),
        # 백틱
        ("`print()` 함수", "print() 함수"),
        # 링크
        ("[문서](https://example.com) 참고", "문서 참고"),
        ("[슬라이드 1] 본문", "[슬라이드 1] 본문"),
        # 공백 정리
        ("a  \t b", "a b"),
        ("a\n\n\n\nb", "a\n\nb"),
        ("  앞뒤 공백  \n", "앞뒤 공백"),
    ],
)
def test_clean_script(text, expected):
    assert clean_script(text) == expected


def test_find_highlights_offsets():
    script = "발표 시간과 말하기 속도"
    assert find_highlights(script, ["발표", "말하기 속도"]) == ["0:2", "7:13"]


def test_find_highlights_first_occurrence():
    assert find_highlights("AI와 AI", ["AI"]) == ["0:2"]


def test_find_highlights_skips_missing(caplog):
    assert find_highlights("발표 시간", ["발표", "없는말"]) == ["0:2"]
    assert "없는말" in caplog.text
