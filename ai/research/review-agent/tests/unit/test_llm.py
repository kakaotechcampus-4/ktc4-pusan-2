"""LLM 단계 (명세 6-2 · 7절) — 가짜 LLM 으로 review_take 의 LLM 경로를 시험한다.

가짜 LLM 은 받은 입력 메시지(후보 목록)를 읽어 답을 만든다. 후보 id 는 강점 s1… · 개선점 i1… ·
다음 미션 n1… 이고, 예시 요청에서 n1 은 사용자 기준 FAILED 미션(fixed)이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from review import review_take
from review.prompts.review import REVIEW_SYSTEM_PROMPT
from review.schemas import MAX_DESCRIPTION_CHARS, ReviewDraft

from .conftest import sample_coverage


class FakeLLM:
    """출력 스키마가 걸린 LLM 대역. make(입력 payload) 의 답을 돌려주거나 예외를 던진다."""

    def __init__(self, make: Callable[[dict[str, Any]], Any] | BaseException) -> None:
        self.make = make
        self.messages: list[tuple[str, str]] | None = None

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.messages = messages
        if isinstance(self.make, BaseException):
            raise self.make
        return self.make(json.loads(messages[-1][1]))


def ids(payload: dict[str, Any], key: str) -> list[str]:
    return [c["candidate_id"] for c in payload[key]]


def full_draft(payload: dict[str, Any]) -> ReviewDraft:
    """모든 후보에 숫자 없는 문장을 쓰고, 다음 미션은 후보 순서 그대로 고른 답"""
    return ReviewDraft.model_validate(
        {
            "overall_summary": "LLM 요약",
            "strengths": [
                {"candidate_id": c, "text": f"LLM 강점 {'가나다라마바사'[i]}"}
                for i, c in enumerate(ids(payload, "strength_candidates"))
            ],
            "improvements": [
                {"candidate_id": c, "text": "LLM 개선점"}
                for c in ids(payload, "improvement_candidates")
            ],
            "next_missions": [
                {"candidate_id": c, "description": "LLM 설명", "reason": "LLM 이유"}
                for c in ids(payload, "next_mission_candidates")
            ],
        }
    )


def without_text(resp: Any) -> dict[str, Any]:
    """문장 · fallback_reason · meta.model 을 뺀 응답 (규칙 단계가 정한 것만)"""
    data = resp.model_dump(mode="json")
    for key in ("overall_summary", "fallback_reason"):
        data.pop(key)
    data["meta"].pop("model")
    for key in ("strengths", "improvements"):
        for item in data[key]:
            item.pop("text")
    for m in data["next_missions"]:
        m.pop("description")
        m.pop("reason")
    return data


def test_success_uses_llm_text_and_keeps_rule_results(req):
    base = review_take(req)
    resp = review_take(req, FakeLLM(full_draft), model="fake-model")
    assert resp.fallback_reason is None
    assert resp.meta.model == "fake-model"
    assert resp.overall_summary == "LLM 요약"
    assert [s.text for s in resp.strengths] == [
        "LLM 강점 가",
        "LLM 강점 나",
        "LLM 강점 다",
    ]
    assert {i.text for i in resp.improvements} == {"LLM 개선점"}
    # 숫자 · 상태 · 점수 · 판정 · 영역 · 근거는 LLM 이 바꾸지 못한다
    assert without_text(resp) == without_text(base)


def test_llm_gets_system_prompt_and_candidates_without_script(req):
    # 대본 · STT 원문이 요청에 섞여 와도 LLM 입력에 넣지 않는다
    coverage = sample_coverage()
    coverage["evaluations"][0]["key_points"][0]["text"] = "대본원문표식"
    req["script_coverage"] = coverage
    req["take_result"]["stt_words"] = [{"w": "STT원문표식"}]
    llm = FakeLLM(full_draft)
    review_take(req, llm)
    (role0, system), (role1, message) = llm.messages
    assert (role0, system, role1) == ("system", REVIEW_SYSTEM_PROMPT, "user")
    payload = json.loads(message)
    assert ids(payload, "strength_candidates")[0] == "s1"
    assert [c["fixed"] for c in payload["next_mission_candidates"]][:1] == [True]
    assert "대본원문표식" not in message and "STT원문표식" not in message


def test_dict_output_is_accepted(req):
    resp = review_take(req, FakeLLM(lambda p: full_draft(p).model_dump()), model="m")
    assert resp.fallback_reason is None


def test_unknown_duplicate_missing_and_empty_ids(req):
    def make(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "overall_summary": "  ",  # 빈 요약 → 기본 요약
            "strengths": [
                {"candidate_id": "s2", "text": "첫 답"},
                {"candidate_id": "s2", "text": "두 번째 답은 버린다"},
                {"candidate_id": "zz", "text": "후보에 없는 id"},
                {"candidate_id": "s3", "text": ""},  # 빈 문장 → 기본 문장
            ],
            "improvements": [],  # 빠뜨린 후보 → 기본 문장
            "next_missions": [],
        }

    base = review_take(req)
    resp = review_take(req, FakeLLM(make), model="m")
    assert resp.fallback_reason is None
    assert resp.overall_summary == base.overall_summary
    # 후보는 모두 후보 순서로 나온다 (s1 은 LLM 이 빠뜨려 기본 문장)
    assert [s.text for s in resp.strengths] == [
        base.strengths[0].text,
        "첫 답",
        base.strengths[2].text,
    ]
    assert [i.text for i in resp.improvements] == [i.text for i in base.improvements]


def test_next_missions_fixed_first_then_llm_order(req):
    def make(p: dict[str, Any]) -> dict[str, Any]:
        return {
            **full_draft(p).model_dump(),
            "next_missions": [
                {"candidate_id": "n2", "description": "x" * 600, "reason": "LLM 이유 긴 설명"},
                {
                    "candidate_id": "n1",
                    "description": "LLM 이 바꾼 설명",
                    "reason": "LLM 이유 고정",
                },
            ],
        }

    base = review_take(req)
    resp = review_take(req, FakeLLM(make), model="m")
    fixed, picked = resp.next_missions
    assert [m.priority for m in resp.next_missions] == [1, 2]
    # 사용자 기준 FAILED 는 맨 앞, 설명은 사용자 표현 그대로, 이유는 LLM 문장
    assert fixed.target == base.next_missions[0].target
    assert (fixed.description, fixed.reason) == ("추임새 5번 이하", "LLM 이유 고정")
    # 설명은 500자에서 자른다
    assert picked.target == base.next_missions[1].target
    assert picked.description == "x" * MAX_DESCRIPTION_CHARS


def test_llm_may_pick_no_priority_mission(req):
    def make(p: dict[str, Any]) -> dict[str, Any]:
        return {**full_draft(p).model_dump(), "next_missions": []}

    resp = review_take(req, FakeLLM(make), model="m")
    # 고르지 않아도 사용자 기준 FAILED 미션은 남는다
    assert [(m.priority, m.description) for m in resp.next_missions] == [(1, "추임새 5번 이하")]


def test_number_check_replaces_only_bad_sentences(req):
    def make(p: dict[str, Any]) -> dict[str, Any]:
        draft = full_draft(p).model_dump()
        draft["overall_summary"] = "점수는 99점입니다."  # 입력에 없는 숫자
        draft["strengths"][0]["text"] = "청중 응시 비율이 58%였습니다."  # 0.58 은 입력에 있다
        draft["strengths"][1]["text"] = "말 속도가 분당 400자였습니다."  # 없는 숫자
        draft["next_missions"][1]["reason"] = "이번 Take 70%였습니다"  # 없는 숫자
        return draft

    base = review_take(req)
    resp = review_take(req, FakeLLM(make), model="m")
    assert resp.fallback_reason is None  # 문장 단위로 바꾸므로 달지 않는다
    assert resp.overall_summary == base.overall_summary
    assert resp.strengths[0].text == "청중 응시 비율이 58%였습니다."
    assert resp.strengths[1].text == base.strengths[1].text
    assert resp.next_missions[1].description == "LLM 설명"
    assert resp.next_missions[1].reason == base.next_missions[1].reason


class CodedError(Exception):
    """서비스 common/llm.py 처럼 code 를 단 예외"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@pytest.mark.parametrize(
    ("llm", "reason"),
    [
        (None, "LLM_UNAVAILABLE"),
        (FakeLLM(CodedError("LLM_TIMEOUT")), "LLM_TIMEOUT"),
        (FakeLLM(CodedError("LLM_UNAVAILABLE")), "LLM_UNAVAILABLE"),
        (FakeLLM(TimeoutError()), "LLM_TIMEOUT"),
        (FakeLLM(RuntimeError("boom")), "LLM_ERROR"),
        (FakeLLM(lambda p: {"overall_summary": 1}), "LLM_INVALID_OUTPUT"),
    ],
)
def test_failure_returns_default_result(req, llm, reason):
    base = review_take(req)
    resp = review_take(req, llm, model="m")
    assert resp.fallback_reason == reason
    assert resp.meta.model is None
    # 기본 결과: 기본 문장 · 후보 순서 다음 미션 (fallback_reason 만 다르다)
    assert resp.model_dump(exclude={"fallback_reason"}) == base.model_dump(
        exclude={"fallback_reason"}
    )


def test_invalid_output_raises_validation_error_inside():
    # LLM_INVALID_OUTPUT 은 pydantic 검증 실패에서 온다
    with pytest.raises(ValidationError):
        ReviewDraft.model_validate({"overall_summary": 1})
