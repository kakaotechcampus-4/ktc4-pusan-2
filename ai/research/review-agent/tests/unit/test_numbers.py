"""숫자 검사 (명세 6-2) — architecture.md 0-2 의 숫자 검사 예시 표를 계약 테스트한다.

표의 줄을 바꾸면 architecture.md 0-2 도 같이 바뀌어야 한다 (공용 규칙 · 공용 시험).
"""

from __future__ import annotations

import json

import pytest

from review.core import check_numbers, message_numbers

#: architecture.md 0-2 숫자 검사 예시 표 (입력 값, 문장 속 숫자, 같은가)
CONTRACT_TABLE = [
    ([0.6248], "62%", True),
    ([0.6248], "0.62", True),
    ([0.6248], "62퍼센트", True),
    ([0.6248], "62", False),  # 단위 없음
    ([0.6248], "63%", False),
    ([88000], "88초", True),
    ([88000], "1분 28초", True),
    ([5], "5번", True),
    ([5], "4번", False),
    ([5], "6번", False),
    ([-1.5], "평소보다 1.5dB 작게", True),  # 음수는 크기
    ([-1.5], "3dB", False),
    ([3], "3장", True),
    ([], "4장", False),  # 입력에 없는 장
    ([], "세 번", True),  # 한글 수는 검사하지 않음
]


@pytest.mark.parametrize(("allowed", "sentence", "same"), CONTRACT_TABLE)
def test_architecture_contract_table(allowed, sentence, same):
    assert check_numbers(sentence, allowed) is same


@pytest.mark.parametrize(
    ("allowed", "sentence", "same"),
    [
        ([], "숫자가 없는 문장입니다", True),
        ([0.625], "63%", True),  # 보인 자리까지 반올림은 5에서 올림
        ([0.285], "29%", True),  # 부동소수 28.4999… 도 29
        ([0.6248], "62.5%", True),  # 보인 자리가 소수 첫째
        ([1240], "1,240자", True),  # 세 자리 쉼표는 한 값
        ([2], "분당 군더더기 2 초과", True),  # '초과' 의 초 · '분당' 의 분은 단위가 아님
        ([2], "분당 군더더기 2초과", True),
        ([88000], "88000ms", True),
        ([88000], "1분 29초", False),
        ([88000, 3], "3번 장에서 1분 28초 머물렀습니다", True),
        ([3, 0.62], "3번 장 0.62, 이전 0.70", False),  # 하나라도 없으면 False
        ([None, True], "1번", False),  # None · bool 은 입력 값이 아니다
        # 개수 · 점수 · 단위 없는 정수는 반올림하지 않는다 (비율 0.62 가 "1번" 으로 통과하지 않게)
        ([0.62], "1번", False),
        ([0.58], "1점", False),
        ([0.52], "1개", False),
        ([0.62], "1", False),
        ([0.4], "0번", False),
        ([2.4], "2번", False),
        ([5.0], "5번", True),  # 문자열에서 되돌린 5.0 도 같은 개수
        ([318.5], "319자", True),  # 자는 보인 자리까지 반올림
    ],
)
def test_check_numbers_edges(allowed, sentence, same):
    assert check_numbers(sentence, allowed) is same


def test_message_numbers_reads_values_and_sentences():
    message = json.dumps(
        {
            "score": 76,
            "strength_candidates": [{"candidate_id": "s1", "evidence": {"cpm": 318.5}}],
            "next_mission_candidates": [
                {"candidate_id": "n1", "description": "3번 장 대본 응시 비율 52% 이하"},
                {"candidate_id": "n2", "description": "발표 시간 3분 2초 이하", "fixed": True},
            ],
        },
        ensure_ascii=False,
    )
    numbers = message_numbers(message)
    # JSON 숫자 값과 문자열 속 숫자를 입력 단위로 (52% → 0.52, 3분 2초 → 182000 ms)
    assert set(numbers) >= {76, 318.5, 3, 0.52, 182000}
    # candidate_id 의 번호 · bool 은 넣지 않는다
    assert 1 not in numbers and 2 not in numbers
