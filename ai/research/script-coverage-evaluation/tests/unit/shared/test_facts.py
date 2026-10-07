import pytest

from script_coverage.shared.facts import CriticalFact, extract_critical_facts
from script_coverage.shared.text import normalize_script

from .slides import SLIDES

# 노트북 ① 3) Critical Fact Parser: 실제 대본에 나올 법한 표기
PARSER_CASES = [
    ("시드 투자로 2억 3천만 원을 유치했습니다.", {"money:230,000,000원"}),
    ("점유율이 5%p 상승했습니다.", {"percentage:5%p"}),
    ("응답 속도를 10~20% 개선했습니다.", {"percentage:10%", "percentage:20%"}),
    ("5~10억 원 규모입니다.", {"money:500,000,000원", "money:1,000,000,000원"}),
    ("주요 고객은 20대 30대 직장인입니다.", {"quantity:20대", "quantity:30대"}),
    ("2025년 3분기에 출시하고 2026학년도부터 도입합니다.", {"date:2025-Q3", "date:2026"}),
    ("오후 3시 30분에 시작합니다.", {"time:15:30"}),
    (
        "매출이 두 배 늘었고 세 가지 기능을 한 달 만에 만들었습니다.",
        {"quantity:2배", "quantity:3가지", "duration:1개월"},
    ),
    ("사용자는 삼십칠 퍼센트 증가했습니다.", {"percentage:37%"}),  # STT 식 한글 수사
    ("멘토와 멘티를 일대일로 연결합니다.", {"ratio:1:1"}),
    ("용량은 5,000 mAh 입니다.", {"quantity:5,000mAh"}),
    ("한 번 확인한 내용을 이대로 다시 보여 줍니다.", set()),  # 수사처럼 보이는 일반 표현
    # 단위 글자로 시작하는 일반 단어는 Kiwi 형태소 확인으로 걸러진다
    ("3원칙을 지키고 3프로젝트를 진행했습니다.", {"number:3"}),
    ("3시간 동안 5시리즈를 봤습니다.", {"duration:3시간", "number:5"}),
    ("두 배터리를 쓰고 한 대학에서 도입했습니다.", set()),
    ("둘 사이 명확한 차이가 있습니다.", set()),
]


@pytest.mark.parametrize(("text", "expected"), PARSER_CASES)
def test_parser_cases(text, expected):
    facts = extract_critical_facts(normalize_script(text))
    got = {f"{f.type}:{f.normalized}" for f in facts if f.type not in ("proper_noun", "term")}
    assert got == expected


def facts_of(script_name: str, slide_number: int) -> dict[str, CriticalFact]:
    norm = normalize_script(SLIDES[(script_name, slide_number)])
    return {f.normalized: f for f in extract_critical_facts(norm)}


def test_slide_1_4():
    f = facts_of("가상대본1", 4)
    assert f["12,400,000건"].qualifier == "약"
    assert "3:1" in f
    assert "xgboost" in f


def test_slide_1_6():
    f = facts_of("가상대본1", 6)
    assert f["2025-03"].type == "date"
    assert "8주" in f
    assert f["42%"].qualifier == "약"
    assert f["85%"].qualifier == "이상"


def test_slide_1_7():
    f = facts_of("가상대본1", 7)
    assert {"2,400,000,000원", "3,600,000,000원", "1,200곳", "430곳", "900곳"} <= f.keys()


def test_slide_1_9_ordinals_excluded():
    f = facts_of("가상대본1", 9)
    assert "2026-H1" in f
    assert not any(x.value.endswith("단계") for x in f.values())


def test_slide_2_1_range():
    assert {"15%", "20%"} <= facts_of("가상대본2", 1).keys()


def test_slide_2_8_times():
    assert {"18:00", "09:00"} <= facts_of("가상대본2", 8).keys()


def test_slide_2_9():
    f = facts_of("가상대본2", 9)
    assert "19,000원" in f
    assert "6배" in f
