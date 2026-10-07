from script_coverage.script_analysis.draft import new_rubric
from script_coverage.script_analysis.schemas import (
    SentenceUnitsDraft,
    SlideScript,
    SlideUnits,
    UnitDraft,
)
from script_coverage.script_analysis.units import (
    _find_in_sentence,
    attach_units,
    build_content_units,
    unit_user_message,
)
from script_coverage.shared.facts import extract_critical_facts
from script_coverage.shared.text import normalize_script
from tests.unit.shared.slides import SLIDES

# 기대값은 원본 노트북 셀 코드를 같은 입력으로 돌려 얻은 것이다
SLIDE = SlideScript(slide_number=7, script=SLIDES[("가상대본1", 7)])
NORM = normalize_script(SLIDE.script)


def _rubric():
    facts = extract_critical_facts(NORM)
    for i, fact in enumerate(facts, 1):
        fact.id = f"CF{i}"
    return new_rubric(
        "t", SLIDE, NORM, [], ["claim", "evidence", "skip"], "c", [], facts, [], "test-model"
    )


SLIDE_UNITS = SlideUnits(
    sentences=[
        SentenceUnitsDraft(
            sentence_id=0,
            units=[
                UnitDraft(text="공공도서관 약 1,200곳", quote="공공도서관은 약 1,200곳"),
                UnitDraft(text="공공도서관 약 1,200곳", quote="공공도서관은 약 1,200곳"),
                UnitDraft(text="지어냄", quote="전혀 없는 표현"),
            ],
        ),
        SentenceUnitsDraft(
            sentence_id=1,
            units=[UnitDraft(text="좌석 예약 시스템 사용", quote="열람실 좌석 예약 시스템")],
        ),
    ]
)


def test_find_in_sentence():
    s0 = NORM.sentences[0]
    assert _find_in_sentence("공공도서관은 약 1,200곳", s0, NORM.text) == ((3, 18), True)
    assert _find_in_sentence("공공도서관은  약1200 곳", s0, NORM.text) == ((3, 18), True)
    assert _find_in_sentence("국내 대학 도서관은 약 430곳이", s0, NORM.text) == ((0, 35), False)
    assert _find_in_sentence("완전히 다른 내용", s0, NORM.text) == (None, False)
    assert _find_in_sentence("", s0, NORM.text) == (None, False)


def test_unit_user_message():
    assert unit_user_message(SLIDE, _rubric()) == (
        "[슬라이드 7]\n"
        "[S0] 국내 공공도서관은 약 1,200곳이고, 대학 도서관은 약 430곳입니다. (핵심 수치·이름: 약 1,200곳, 약 430곳)\n"
        "[S1] 이 중 열람실 좌석 예약 시스템을 이미 쓰는 곳을 초기 대상으로 보면 약 900곳이며, 연간 시장 규모는 약 24억 원에서 36억 원으로 추산했습니다. (핵심 수치·이름: 약 900곳, 약 24억 원, 36억 원)\n"
        "[S2] (나누지 않음) 처음에는 이용자가 가장 많은 서울과 대전의 대학 도서관부터 시작하겠습니다."
    )


def test_build_content_units_validates_and_adds_missing_facts():
    warnings: list[str] = []
    units = build_content_units(_rubric(), SLIDE_UNITS, warnings)
    assert [(u.id, u.text, u.quote, u.span, u.fact_ids, u.source) for u in units] == [
        ("S0-U1", "공공도서관 약 1,200곳", "공공도서관은 약 1,200곳", (3, 18), ["CF1"], "llm"),
        ("S0-U2", "약 430곳", "약 430곳", (30, 36), ["CF2"], "fact"),
        ("S1-U1", "좌석 예약 시스템 사용", "열람실 좌석 예약 시스템", (45, 58), [], "llm"),
        ("S1-U2", "약 900곳", "약 900곳", (80, 86), ["CF3"], "fact"),
        ("S1-U3", "약 24억 원", "약 24억 원", (100, 107), ["CF4"], "fact"),
        ("S1-U4", "36억 원", "36억 원", (110, 115), ["CF5"], "fact"),
    ]
    assert warnings == [
        "unit_quote_not_found:S0:전혀 없는 표현",
        "unit_missing_fact:S0:약 430곳",
        "unit_missing_fact:S1:약 900곳",
        "unit_missing_fact:S1:약 24억 원",
        "unit_missing_fact:S1:36억 원",
    ]


def test_build_content_units_without_llm_units_uses_whole_sentences():
    warnings: list[str] = []
    units = build_content_units(_rubric(), None, warnings)
    assert [(u.id, u.span, u.fact_ids, u.source) for u in units] == [
        ("S0-U1", (0, 40), ["CF1", "CF2"], "sentence"),
        ("S1-U1", (41, 125), ["CF3", "CF4", "CF5"], "sentence"),
    ]
    assert warnings == ["units_not_split:S0", "units_not_split:S1"]


def test_attach_units_updates_meta():
    with_units = attach_units(_rubric(), SLIDE_UNITS, "test-model")
    assert with_units.meta.rubric_id == "dd78eb47f964443f"
    assert with_units.meta.unit_config_hash == "150d0924a7b6"
    without = attach_units(_rubric(), None, "test-model")
    assert without.meta.rubric_id == "425ea6231a264b4e"
    assert without.meta.unit_config_hash == ""
    assert without.warnings == ["units_not_split:S0", "units_not_split:S1"]
