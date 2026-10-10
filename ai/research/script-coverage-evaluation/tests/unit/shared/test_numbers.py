import pytest

from script_coverage.shared.numbers import format_number, parse_korean_number

# 기대값은 노트북 원본 함수를 그대로 돌려 얻은 값이다.
CASES = [
    ("30", 30.0, "30"),
    ("1,240만", 12400000.0, "12,400,000"),
    ("1천만", 10000000.0, "10,000,000"),
    ("1만 9천", 19000.0, "19,000"),
    ("24억", 2400000000.0, "2,400,000,000"),
    ("4.6", 4.6, "4.6"),
    ("천이백사십만", 12400000.0, "12,400,000"),
    ("삼십", 30.0, "30"),
    ("사점육", 4.6, "4.6"),
    ("이공이육", 2026.0, "2,026"),
    ("구십구", 99.0, "99"),
    ("1.5억", 150000000.0, "150,000,000"),
    ("삼점일사", 3.14, "3.14"),
]


@pytest.mark.parametrize(("text", "value", "formatted"), CASES)
def test_parse_and_format(text, value, formatted):
    parsed = parse_korean_number(text)
    assert parsed == pytest.approx(value)
    assert format_number(parsed) == formatted


@pytest.mark.parametrize("text", ["abc", ""])
def test_unparsable_returns_none(text):
    assert parse_korean_number(text) is None
