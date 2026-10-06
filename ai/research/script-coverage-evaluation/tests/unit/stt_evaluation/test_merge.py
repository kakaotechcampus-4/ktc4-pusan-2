"""전달 단위 → 문장 → Key Point 판정 규칙. 기대값은 노트북 원본 코드(셀 21)를 돌려 얻은 값이다."""

import pytest

from script_coverage.stt_evaluation.merge import aggregate_status, status_from_units


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "missing"),
        (["said"], "said"),
        (["said", "said"], "said"),
        (["missing", "missing"], "missing"),
        (["said", "vague"], "partial"),
        (["said", "missing"], "partial"),
        (["vague"], "partial"),
        (["vague", "missing"], "partial"),
        (["contradicted", "said"], "contradicted"),
    ],
)
def test_status_from_units(statuses, expected):
    assert status_from_units(statuses) == expected


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "missing"),
        (["said"], "covered"),
        (["missing"], "missing"),
        (["said", "partial"], "partial"),
        (["said", "missing"], "partial"),
        (["partial", "partial"], "partial"),
        (["contradicted", "said"], "contradicted"),
    ],
)
def test_aggregate_status(statuses, expected):
    assert aggregate_status(statuses) == expected
