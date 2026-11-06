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
        elif sl.event == "claim":
            s.ClaimLineData.model_validate(sl.data)
        elif sl.event == "error":
            assert set(sl.data["error"]) == {"code", "message"}
    terminal = [e for e in events if e in ("final", "error")]
    assert terminal == [events[-1]]


@pytest.mark.parametrize("name", STREAMS)
def test_claim_lines_are_the_tail_of_final_message_in_order(name: str) -> None:
    """5-4: 문장 줄은 끝 줄 message 문장과 글자 · 종류 · 근거까지 같고, 번호는 실행 전체 답의 자리로 1씩 이어진다.
    문장 줄을 하나라도 받았으면 끝 줄의 판단이 모두 그 앞 줄들에 실려 있다(이어 부른 응답은 앞 응답에서 보낸 것 제외)."""
    objs = [json.loads(x) for x in _read(name).splitlines()]
    claim_lines = [o["data"] for o in objs if o["event"] == "claim"]
    if objs[-1]["event"] != "final" or not claim_lines:
        return
    claims = objs[-1]["data"]["message"]["claims"]
    base = claim_lines[0]["index"]
    assert [c["index"] for c in claim_lines] == list(range(base, base + len(claim_lines)))
    assert [c["claim"] for c in claim_lines] == claims[base:]
    sent = {a["assessment_id"] for c in claim_lines for a in c["assessments"]}
    if base == 0:
        assert sent == {a["assessment_id"] for a in objs[-1]["data"]["assessments"]}


def test_needs_action_example_has_one_action_and_state() -> None:
    final = json.loads(_read("stream_needs_action.ndjson").splitlines()[-1])["data"]
    data = s.FinalData.model_validate(final)
    assert data.status == "NEEDS_ACTION"
    assert len(data.actions) == 1 and data.state is not None
    action = data.actions[0]
    # 코드가 만드는 값(5-8): action_id · idempotency_key 는 실행 id 에 묶인다
    assert action.idempotency_key.startswith("run-81:") and action.action_id.startswith("run-81:")
    assert action.authorized_by and action.authorized_by in _user_message()
    # ASK 행동(6-2): args_hash 가 있고, 멈추기 전에 낸 문장과 같은 해시의 확인 카드가 message 에 있다
    assert action.autonomy == "ASK" and action.args_hash and action.preapproved is None
    assert data.message is not None and data.message.claims
    (card,) = [c for c in data.message.cards if c.type == "ASK_CONFIRM"]
    assert (card.action_id, card.args_hash) == (action.action_id, action.args_hash)
    assert card.before is None and card.after == action.args["goal"]


def test_ask_args_hash_is_sha256_of_type_and_args() -> None:
    from chat_agent.actions import action_args_hash

    final = json.loads(_read("stream_needs_action.ndjson").splitlines()[-1])["data"]
    action = final["actions"][0]
    assert action["args_hash"] == action_args_hash(action["type"], action["args"])
    res = json.loads(_read("request_action_result.json"))["resume"]["action_results"][0]
    assert res["executed_args_hash"] == action["args_hash"]


def _user_message() -> str:
    return json.loads(_read("request_user_message.json"))["user_message"]


def test_done_example_text_is_claims_joined_and_cards_valid() -> None:
    final = json.loads(_read("stream_done.ndjson").splitlines()[-1])["data"]
    data = s.FinalData.model_validate(final)
    assert data.message is not None
    assert data.message.text == "\n".join(c.text for c in data.message.claims)
    kinds = {c.type for c in data.message.cards}
    assert kinds == {"GOAL_SAVED", "START_PRACTICE"}
    # 연습 이동은 버튼 등급 행동 + 버튼 카드(자동 이동 없음)
    assert [(a.type, a.autonomy) for a in data.actions] == [("START_PRACTICE", "BUTTON")]
    # 앞부분은 승인 대기로 멈추기 전에 낸 문장 그대로이고 그 뒤에 행동 결과 문장이 이어진다(5-5)
    paused = json.loads(_read("stream_needs_action.ndjson").splitlines()[-1])["data"]
    said = paused["message"]["claims"]
    assert [c.model_dump(mode="json") for c in data.message.claims[: len(said)]] == said
    assert data.message.claims[len(said)].kind == "ACTION_RESULT"
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


def test_example_files_match_builder() -> None:
    """예시 파일이 만들기 코드(chat_agent_lab.examples)의 결과와 같다. 프롬프트 · 설정 · 모양을 바꾸면 다시 만든다."""
    from chat_agent_lab import examples as ex

    for name, content in ex.build().items():
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False)
        )
        on_disk = _read(name).replace("\r\n", "\n")  # 줄 끝 자동 변환(CRLF)은 무시한다
        assert on_disk == text + "\n", f"{name} 이 오래됐다: python -m chat_agent_lab.examples"


def test_resume_example_passes_state_and_pairing_checks() -> None:
    """이어 부르기 예시는 예시 키로 서명 · 판 · 식별자 · 행동 결과 짝 · 누적량 검사를 통과하고, 저장 성공 문장으로 끝난다."""
    from chat_agent.turn import handle_turn
    from chat_agent_lab import examples as ex

    from . import fakes as F

    clock = F.Clock()
    clock.ms = ex.EXAMPLE_ISSUED_MS + 60_000
    llm = F.FakeLlm([F.done()], answers=[F.answer([])])
    resp = handle_turn(
        _read("request_action_result.json").encode("utf-8"),
        F.deps(llm, clock=clock),
        secret=ex.EXAMPLE_STATE_KEY,
    )
    assert resp.error is None
    assert resp.lines is not None
    lines = [json.loads(x) for x in resp.lines]
    final = s.FinalData.model_validate(lines[-1]["data"])
    assert final.message is not None and "목표를 저장했어요." in final.message.text
    assert any(
        c.kind == "ACTION_RESULT" and c.action_id == "run-81:a1" for c in final.message.claims
    )
    # 멈추기 전에 낸 문장이 최종 답의 앞부분에 그대로 있다(5-5)
    paused = json.loads(_read("stream_needs_action.ndjson").splitlines()[-1])["data"]
    said = paused["message"]["claims"]
    assert [c.model_dump(mode="json") for c in final.message.claims[: len(said)]] == said
    assert {a.assessment_id for a in final.assessments} == {
        a["assessment_id"] for a in paused["assessments"]
    }
    # 다른 키 · 만료 뒤에는 거절한다
    assert (
        handle_turn(
            _read("request_action_result.json").encode("utf-8"),
            F.deps(F.FakeLlm([]), clock=clock),
            secret=b"x" * 32,
        ).error
        is not None
    )
    # 승인 대기로 멈춘 상태는 발급 + 35분까지 받는다(보통 10분보다 길다, 5-6)
    wait = F.Clock()
    wait.ms = ex.EXAMPLE_ISSUED_MS + 34 * 60_000
    assert (
        handle_turn(
            _read("request_action_result.json").encode("utf-8"),
            F.deps(F.FakeLlm([F.done()], answers=[F.answer([])]), clock=wait),
            secret=ex.EXAMPLE_STATE_KEY,
        ).error
        is None
    )
    late = F.Clock()
    late.ms = ex.EXAMPLE_ISSUED_MS + 36 * 60_000
    assert (
        handle_turn(
            _read("request_action_result.json").encode("utf-8"),
            F.deps(F.FakeLlm([]), clock=late),
            secret=ex.EXAMPLE_STATE_KEY,
        ).error
        is not None
    )
