"""계약 시험: 코어 `examples/`의 요청 · 스트림 · 오류 · 행동 결과 예시가 5절 모양과 맞는지 (기능 문서 10-2 '계약').

BE도 서비스 탑재 뒤 같은 예시 파일로 계약 시험을 한다. 예시를 바꾸면 이 시험이 먼저 깨진다.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from chat_agent import schemas as s
from chat_agent.config import DEFAULT_CONFIG

EXAMPLES = resources.files("chat_agent") / "examples"
STREAMS = [
    "stream_needs_action.ndjson",
    "stream_done.ndjson",
    "stream_partial.ndjson",
    "stream_refused.ndjson",
    "stream_error.ndjson",
]


def _read(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name,trigger",
    [
        ("request_user_message.json", "USER_MESSAGE"),
        ("request_action_result.json", "ACTION_RESULT"),
        ("request_take_reviewed.json", "TAKE_REVIEWED"),
    ],
)
def test_request_examples_match_contract(name: str, trigger: str) -> None:
    req = s.TurnRequest.model_validate_json(_read(name))
    assert req.run.trigger == trigger
    # 기본 자료 크기 상한(6-12)은 공백 없는 UTF-8 JSON 으로 잰다
    ctx = json.dumps(json.loads(_read(name))["context"], ensure_ascii=False, separators=(",", ":"))
    assert len(ctx.encode("utf-8")) <= DEFAULT_CONFIG.max_context_bytes


@pytest.mark.parametrize("name", STREAMS)
def test_fm_L1_07_stream_examples_have_exactly_one_terminal_line_last(name: str) -> None:
    """L1-07 · L1-40: 줄마다 JSON 하나, seq 1부터 1씩, 끝 줄(final · error)은 정확히 하나이고 마지막."""
    lines = _read(name).splitlines()
    events = []
    for i, line in enumerate(lines, start=1):
        assert len((line + "\n").encode("utf-8")) <= DEFAULT_CONFIG.max_line_bytes
        obj = json.loads(line)
        sl = s.StreamLine.model_validate(obj)
        assert sl.seq == i
        assert sl.run_id == "run-81"
        events.append(sl.event)
        if sl.event == "final":
            final = s.FinalData.model_validate(sl.data)
            if final.diagnostics is not None:
                size = len(final.diagnostics.model_dump_json().encode("utf-8"))
                assert size <= DEFAULT_CONFIG.max_diagnostics_bytes
        elif sl.event == "plan":
            for step in sl.data["plan"]:
                s.PlanStep.model_validate(step)
        elif sl.event == "tool_done":
            s.TraceItem.model_validate(sl.data["trace"])
        elif sl.event == "error":
            assert set(sl.data["error"]) == {"code", "message"}
    terminal = [e for e in events if e in ("final", "error")]
    assert terminal == [events[-1]]


def test_needs_action_example_has_one_action_and_state() -> None:
    final = json.loads(_read("stream_needs_action.ndjson").splitlines()[-1])["data"]
    data = s.FinalData.model_validate(final)
    assert data.status == "NEEDS_ACTION"
    assert len(data.actions) == 1 and data.state is not None
    action = data.actions[0]
    # 코드가 만드는 값(5-8): action_id · idempotency_key 는 실행 id 에 묶인다
    assert action.idempotency_key.startswith("run-81:") and action.action_id.startswith("run-81:")
    assert action.authorized_by and action.authorized_by in _user_message()


def _user_message() -> str:
    return json.loads(_read("request_user_message.json"))["user_message"]


def test_done_example_text_is_claims_joined_and_cards_valid() -> None:
    final = json.loads(_read("stream_done.ndjson").splitlines()[-1])["data"]
    data = s.FinalData.model_validate(final)
    assert data.message is not None
    assert data.message.text == "\n".join(c.text for c in data.message.claims)
    kinds = {c.type for c in data.message.cards}
    assert kinds == {"GOAL_SAVED", "START_PRACTICE"}
    # 판단은 사용자 말 원문의 부분 문자열이다(6-3)
    for a in data.assessments:
        assert a.quote in _user_message()


def test_take_reviewed_example_has_goal_result() -> None:
    data = s.FinalData.model_validate_json(_read("final_take_reviewed.json"))
    assert data.goal_result is not None and data.goal_result.terminal
    computed = [e for e in data.evidence if e.origin == "COMPUTED"]
    assert computed and all(
        set(e.inputs or []) <= {x.evidence_id for x in data.evidence} for e in computed
    )


def test_pre_stream_error_body_shape() -> None:
    body = s.ErrorBody.model_validate_json(_read("error_state_invalid.json"))
    assert body.error["code"] == "AGENT_STATE_INVALID"


def test_action_result_example_shape() -> None:
    r = s.ActionResult.model_validate_json(_read("action_result_fetch_pitch_ambiguous.json"))
    assert r.status == "AMBIGUOUS" and len(r.data["candidates"]) == 2
