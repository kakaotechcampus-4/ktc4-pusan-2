"""턴 계약 묶음 시험 — 새 줄 · 행동 종류 없음, 이어 부를 때 판이 다르면 409 (기능 문서 5-4 · 5-6 · 5-8 · L1-33, 목표 동작 1 · 5절).

- 줄 · 행동 종류: 턴 시험은 모두 `fakes.run_turn`으로 스트림을 받고, 그 안의 `fakes.check_contract`가 응답마다 줄 종류 ⊆
  {plan, tool_done, claim, final, error} · 끝 줄 행동 종류 ⊆ 다섯 행동을 본다(어기면 그 시험이 실패). 여기서는 (1) 그 집합이 응답
  스키마의 닫힌 값과 같고 (2) 대표 턴(답 · 가져오기 · 범위 바꾸기 · 저장 · 다시 평가 · 연습 버튼 · 오류)에서 다섯 줄 종류 · 다섯 행동
  종류가 모두 나오되 그 밖은 없고 (3) 검사가 실제로 걸리는지 본다. BE 쪽 거절(모르는 줄 · 행동 → AGENT_UPSTREAM_ERROR, 행동 실행 0번)은
  연구 tests/lab/test_fake_be.py `test_contract_new_line_kind_or_action_type_is_upstream_error_and_runs_no_action`.
- 판 불일치: 진행 중 실행은 판을 고정한다. 서명 형식(`v`) · 지원하지 않는 실행 판(feature · tool_schema) · 프롬프트 판이 다르면 이어
  부르기가 스트림 전 409 `AGENT_STATE_INVALID`이고 모델 호출 0번이다(상태 검증 단위 시험은 test_state.py).
"""

from __future__ import annotations

import copy
from typing import Any, get_args

import pytest

from pitch_coach_ai.features.chat_agent import prompts, state
from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout
from pitch_coach_ai.features.chat_agent.schemas import ActionType, StreamLine
from pitch_coach_ai.features.chat_agent.turn import TurnResponse
from pitch_coach_ai.features.chat_agent.version import STATE_V

from . import fakes as F
from .test_approval import REASSESS, first_request, record_answer, save_script
from .test_plan_progress_turn import compare_answer, compare_plan
from .test_turn import COMPARE, MSG, fetch_take_script, fetched_take, resume_request

# ── 줄 · 행동 종류 ───────────────────────────────────────────────────────────


def test_contract_sets_are_the_closed_values_of_the_response_schema() -> None:
    assert set(get_args(StreamLine.model_fields["event"].annotation)) == F.STREAM_EVENTS
    assert set(get_args(ActionType)) == F.ACTION_TYPES


def _answer_turn() -> list[dict[str, Any]]:
    llm = F.FakeLlm(
        [
            compare_plan(("반복 확인", "compare_takes"), ("답 쓰기", "answer")),
            F.tools(COMPARE),
            F.done(),
            compare_answer(),
        ]
    )
    return F.run_turn(F.with_loaded(F.request(user_message=MSG), "t-4"), llm)[1]


def _fetch_take_turn() -> list[dict[str, Any]]:
    return F.run_turn(F.request(user_message=MSG), F.FakeLlm(fetch_take_script()))[1]


def _fetch_pitch_turn() -> list[dict[str, Any]]:
    req = F.request(user_message="회사 소개 발표에서 Take 1 기록 알려 줘")
    other = {**copy.deepcopy(req["context"]["pitches"][0]), "pitch_id": "p-2"}
    other["title"] = "회사 소개 발표"
    req["context"]["pitches"].append(other)
    req["context"]["pitches_total"] = len(req["context"]["pitches"])
    llm = F.FakeLlm(
        [
            F.plan(),
            F.tools(
                ("fetch_pitch", {"name": "회사 소개 발표", "reason": "다른 발표"}), prefix="fp"
            ),
        ]
    )
    return F.run_turn(req, llm)[1]


def _save_goal_turn() -> list[dict[str, Any]]:
    llm = F.FakeLlm(save_script(), answers=[record_answer, F.answer([])], fixes=[F.fix({})])
    return F.run_turn(first_request(), llm)[1]


def _reassess_turn() -> list[dict[str, Any]]:
    d = [
        F.draft(
            "대본 보고 한 발표였어",
            claim_op="VALUE",
            dimension="script_independence",
            metric="script_mode",
            polarity="POSITIVE",
        ),
        F.draft("다시 평가해 줘", type_="REQUEST", claim_op=None, polarity="NEUTRAL"),
    ]
    llm = F.FakeLlm(
        [F.plan(assessments=d), F.tools(REASSESS, prefix="ra")],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )
    return F.run_turn(F.request(user_message="대본 보고 한 발표였어. 다시 평가해 줘"), llm)[1]


def _practice_turn() -> list[dict[str, Any]]:
    msg = "바로 발표할래"
    req_draft = F.draft(msg, type_="REQUEST", claim_op=None, polarity="NEUTRAL")
    llm = F.FakeLlm(
        [F.plan(route="ANSWER_DIRECT", steps=[], assessments=[req_draft])],
        answers=[F.answer([], assessments=[req_draft], start_practice={"authorized_by": msg})],
    )
    return F.run_turn(F.request(user_message=msg), llm)[1]


def _error_turn() -> list[dict[str, Any]]:
    llm = F.FakeLlm([F.plan(), LlmTimeout()], answers=[LlmTimeout()])
    return F.run_turn(F.request(user_message="발표 기록 좀 봐 줘"), llm)[1]


TURNS = {
    "answer": _answer_turn,
    "fetch_take": _fetch_take_turn,
    "fetch_pitch": _fetch_pitch_turn,
    "save_goal": _save_goal_turn,
    "reassess": _reassess_turn,
    "practice": _practice_turn,
    "error": _error_turn,
}


def test_turn_streams_use_every_line_kind_and_action_type_and_nothing_else() -> None:
    """대표 턴들의 줄을 모아 보면 다섯 줄 종류 · 다섯 행동 종류가 모두 나오고 그 밖은 없다(검사가 빈 집합으로 통과하지 않는다).
    각 턴의 줄은 `run_turn` 안에서 이미 `check_contract`를 받았다."""
    events: set[str] = set()
    actions: set[str] = set()
    for name, turn in TURNS.items():
        lines = turn()
        assert lines, name
        F.check_contract(lines)
        events |= {ln["event"] for ln in lines}
        if lines[-1]["event"] == "final":
            actions |= {a["type"] for a in lines[-1]["data"]["actions"]}
    assert events == F.STREAM_EVENTS
    assert actions == F.ACTION_TYPES


@pytest.mark.parametrize(
    "bad",
    [
        [{"event": "thought", "run_id": "r", "seq": 1, "data": {}}],
        [
            {
                "event": "final",
                "run_id": "r",
                "seq": 1,
                "data": {"actions": [{"type": "DELETE_TAKE"}]},
            }
        ],
    ],
    ids=["new_line_kind", "new_action_type"],
)
def test_run_turn_fails_a_turn_test_whose_stream_leaves_the_contract(
    bad: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_turn`의 검사가 실제로 걸린다: 계약에 없는 줄 종류 · 행동 종류를 받으면 그 턴 시험이 AssertionError 로 실패한다."""
    import json

    def fake_handle_turn(*_a: Any, **_kw: Any) -> TurnResponse:
        return TurnResponse(lines=iter(json.dumps(x, ensure_ascii=False) + "\n" for x in bad))

    monkeypatch.setattr(F, "handle_turn", fake_handle_turn)
    with pytest.raises(AssertionError, match="계약에 없는"):
        F.run_turn(F.request(user_message=MSG), F.FakeLlm([]))


# ── 이어 부를 때 판 불일치 → 409 ──────────────────────────────────────────────


@pytest.mark.parametrize("change", ["none", "state_format_v", "prompt_version", "run_version"])
def test_fm_L1_33_resume_after_a_version_change_is_409_before_any_model_call(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """가져오기로 멈춘 실행을 이어 부르는데 그 사이 판이 바뀌었다: 서명 형식(`v`)이 다르거나, 배포한 서비스가 그 실행 판(feature ·
    tool_schema)을 더는 지원하지 않거나, 프롬프트 판이 다르면 스트림 전 409 `AGENT_STATE_INVALID`이고 모델 호출 0번 · 줄 0개다.
    바뀐 것이 없으면(`none`) 이어 부르기가 그대로 돈다(409 가 판 때문임을 보인다)."""
    req = F.request(user_message=MSG)
    _, _, data = F.run_turn(req, F.FakeLlm(fetch_take_script()))
    action = data["actions"][0]  # type: ignore[index]
    result = {
        "action_id": action["action_id"],
        "type": "FETCH_TAKE",
        "status": "SUCCEEDED",
        "data": {"take": fetched_take("t-4")},
        "error": None,
    }
    req2 = resume_request(req, data, [result])  # type: ignore[arg-type]
    if change == "state_format_v":
        req2["resume"]["state"]["v"] = STATE_V + 1
    elif change == "prompt_version":
        monkeypatch.setattr(prompts, "PROMPT_VERSION", "prompt-next")
    elif change == "run_version":
        monkeypatch.setattr(
            state, "SUPPORTED_RUN_VERSIONS", frozenset({("chat_agent-next", "tools-next")})
        )
    llm2 = F.FakeLlm([F.tools(COMPARE, prefix="c2"), F.done(), compare_answer()])
    status, lines, body = F.run_turn(req2, llm2)
    if change == "none":
        assert status == 200 and lines[-1]["event"] == "final" and llm2.calls
        return
    assert (status, body["error"]["code"]) == (409, "AGENT_STATE_INVALID")  # type: ignore[index]
    assert lines == [] and llm2.calls == []
