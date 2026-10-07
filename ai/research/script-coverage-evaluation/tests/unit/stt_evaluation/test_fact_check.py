"""수 읽기 · 발음 차이 · 불일치 원인 신호. 기대값은 노트북 원본 코드(셀 15)를 돌려 얻은 값이다."""

import pytest

from script_coverage.shared.facts import extract_critical_facts
from script_coverage.shared.rubric import NAME_TYPES
from script_coverage.shared.text import normalize_script
from script_coverage.stt_evaluation.fact_check import (
    jamo_diff,
    mismatch_signals,
    read_number,
    reading_of,
)
from script_coverage.stt_evaluation.normalize import normalize_stt
from script_coverage.stt_evaluation.schemas import FactCheck


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("삼", "사", 1),
        ("육", "팔", 3),
        ("가", "가", 0),
        ("여", "다", 2),
        ("칠", "육", 3),
        ("다", "섯", 3),
        ("a", "a", 0),
        ("a", "b", 3),
    ],
)
def test_jamo_diff(a, b, expected):
    assert jamo_diff(a, b) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", "영"),
        ("10", "십"),
        ("12", "십이"),
        ("42", "사십이"),
        ("1000", "천"),
        ("1001", "천일"),
        ("2.4", "이점사"),
        ("12400000", "천이백사십만"),
        ("100000000", "일억"),
        ("1234567890123", "일조이천삼백사십오억육천칠백팔십구만백이십삼"),
    ],
)
def test_read_number(text, expected):
    assert read_number(text) == expected


@pytest.mark.parametrize(
    ("normalized", "kind", "expected"),
    [
        ("12,400,000건", "", "천이백사십만"),
        ("18:30", "time", "여섯시 삼십분"),
        ("12:00", "time", "열두시"),
        ("00:05", "time", "열두시 오분"),
        ("9:30", "time", "구 삼십"),
        ("42%", "", "사십이"),
        ("3.5%", "", "삼점오"),
        ("2025년 3월", "", "이천이십오 삼"),
        ("", "", ""),
    ],
)
def test_reading_of(normalized, kind, expected):
    assert reading_of(normalized, kind) == expected


# (대본 표기, STT 표기, 규칙 추정 원인, 발음 관계, 마지막 신호)
MISMATCH_DEMOS = [
    (
        "312명",
        "사백십이 명",
        "asr_error",
        "similar",
        "발음이 비슷한 음절 하나 차이 (삼 ↔ 사) → 음성 인식 오류일 수 있음",
    ),
    ("42%", "40%", "asr_error", "similar", "음절 하나('이')가 빠짐 → 음성 인식 오류일 수 있음"),
    (
        "76%",
        "67퍼센트",
        "speaker_error",
        "swap",
        "음절 순서만 바뀜 (칠십육 ↔ 육십칠) → 사람이 숫자를 바꿔 말하는 실수에 가까움",
    ),
    (
        "600만 원",
        "팔백만 원",
        "speaker_error",
        "different",
        "발음이 비슷하지 않은 다른 수 → 발표자가 다른 값을 말했을 가능성이 큼",
    ),
    (
        "약 18%",
        "약 이십팔 퍼센트",
        "asr_error",
        "similar",
        "음절 하나('이')가 더해짐 → 음성 인식 오류일 수 있음",
    ),
    (
        "오후 6시",
        "오후 다섯 시",
        "speaker_error",
        "near",
        "발음이 조금 비슷한 음절 하나 차이 (여 ↔ 다) → 인식 오류인지 발표자 실수인지 애매함",
    ),
    (
        "약 1,240만 건",
        "천만 건이 넘는",
        "approximation",
        "approx",
        "어림 표현이 붙어 있고 방향이 맞음 (차이 19%): '천만 건이 넘는'",
    ),
    (
        "18명",
        "스무 명 가까이",
        "approximation",
        "approx",
        "어림 표현이 붙어 있고 방향이 맞음 (차이 11%): '스무 명 가까이'",
    ),
    (
        "11.4%",
        "십일 퍼센트 정도",
        "approximation",
        "approx",
        "어림 표현이 붙어 있고 방향이 맞음 (차이 4%): '십일 퍼센트 정도'",
    ),
    (
        "42%",
        "오십 퍼센트 넘게",
        "speaker_error",
        "different",
        "발음이 비슷하지 않은 다른 수 → 발표자가 다른 값을 말했을 가능성이 큼",
    ),
]


@pytest.mark.parametrize(
    ("script_surface", "stt_surface", "cause", "sound", "last"), MISMATCH_DEMOS
)
def test_mismatch_signals_demo_pairs(script_surface, stt_surface, cause, sound, last):
    script_fact = [
        f
        for f in extract_critical_facts(normalize_script(script_surface))
        if f.type not in NAME_TYPES
    ][0]
    stt = normalize_stt(stt_surface)
    found = [f for f in extract_critical_facts(stt) if f.type not in NAME_TYPES][0]
    check = FactCheck(
        fact_id="-",
        type=found.type,
        value=script_surface,
        normalized=script_fact.normalized,
        importance="normal",
        key_point_ids=[],
        status="mismatched",
        stt_value=found.value,
        stt_normalized=found.normalized,
        stt_numeric_value=found.numeric_value,
        stt_qualifier=found.qualifier,
        stt_span=tuple(found.spans[0]),
    )
    signals, guess, relation = mismatch_signals(check, script_fact, stt.text)
    assert (guess, relation, signals[-1]) == (cause, sound, last)
