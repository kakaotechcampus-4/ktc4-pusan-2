"""예시 파일 계약 — 문서(INTERFACE.md)의 예시와 구현이 같은 값을 말하는지 확인한다.

규칙을 바꿔 이 테스트가 실패하면 response.json 을 새 결과로 바꾸고,
바뀐 값이 의도한 것인지 확인한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from review import ReviewRequest, ReviewResponse, review_take

from .conftest import EXAMPLES


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def assert_same(actual: Any, expected: Any) -> None:
    """모양은 같고 실수는 근사로 비교한다."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for k in expected:
            assert_same(actual[k], expected[k])
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected, strict=True):
            assert_same(a, e)
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9)
    else:
        assert actual == expected


def test_example_files_parse():
    ReviewRequest.model_validate(load("request.json"))
    ReviewResponse.model_validate(load("response.json"))


def test_example_response_matches():
    actual = review_take(load("request.json")).model_dump(mode="json")
    assert_same(actual, load("response.json"))
