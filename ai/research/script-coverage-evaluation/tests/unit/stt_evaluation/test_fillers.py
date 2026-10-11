"""간투사 제거와 STT 정규화. 기대값은 노트북 원본 코드(셀 7)를 돌려 얻은 값이다."""

import pytest

from script_coverage.stt_evaluation.fillers import find_fillers, remove_fillers
from script_coverage.stt_evaluation.normalize import locate_raw, normalize_stt


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("음 오늘은 어 매출을 말씀드리겠습니다", "오늘은 매출을 말씀드리겠습니다"),
        ("그 그 결과 매출이 올랐습니다", "그 결과 매출이 올랐습니다"),
        ("어디에서 아침에 만났습니다", "어디에서 아침에 만났습니다"),
        ("그러니까 이제 시작하겠습니다", "그러니까 이제 시작하겠습니다"),
        ("엄 흠 으응 아아 에 네", "으응 네"),
        ("음... 사십이 퍼센트 사십이 퍼센트 증가", "... 사십이 퍼센트 사십이 퍼센트 증가"),
    ],
)
def test_normalize_stt_text(raw, expected):
    assert normalize_stt(raw).text == expected


def test_normalize_stt_sentences():
    norm = normalize_stt("음 매출이 올랐습니다 어 비용은 줄었습니다")
    assert [(s.index, s.text, s.start, s.end) for s in norm.sentences] == [
        (0, "매출이 올랐습니다", 0, 9),
        (1, "비용은 줄었습니다", 10, 19),
    ]


def test_remove_fillers_keeps_words_that_only_contain_filler_letters():
    assert remove_fillers("어디 아침") == "어디 아침"
    assert remove_fillers("그 그 결과") == "그 결과"


def test_remove_fillers_replaces_filler_with_space():
    assert remove_fillers("음 매출") == "  매출"


def test_find_fillers_spans():
    text = "음 오늘은 어어 매출 어디"
    spans = find_fillers(text)
    assert [text[a:b] for a, b in spans] == ["음", "어어"]
    assert spans == [(0, 1), (6, 8)]


def test_find_fillers_none():
    assert find_fillers("어디 아침 그러니까") == []


def test_locate_raw():
    raw = "음 노조 비율은 십팔 퍼센트에서 칠 퍼센트로 낮아졌습니다 어 시험 운영은 사십 분 단위로 했습니다"
    assert locate_raw(raw, "노조", 0, 60) == (2, 4)
    assert locate_raw("a b a b", "ab", 5, 7) == (4, 7)
    assert locate_raw("abc", "", 0, 3) is None
