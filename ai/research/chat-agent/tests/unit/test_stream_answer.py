"""문장 줄(`claim`) 시험 — 검사를 통과한 문장부터 내고, 낸 문장은 언제나 끝 줄 message 의 앞부분이다 (기능 문서 5-4 · 6-1 5번 · 6-9).

가짜 모델이 답 초안을 글자 단위로 흘리게 하고, 흘리는 중간에 답 쓰는 쪽(`text_sink`)이 낸 본문 문장 수를 본다.
"""

from __future__ import annotations

import json
from typing import Any

from chat_agent import templates
from chat_agent.llm_types import LlmTimeout, Usage
from chat_agent.schemas import ClaimLineData, FinalData

from . import fakes as F
from .test_approval import first_request, record_answer, save_script, saved_result
from .test_turn import COMPARE, MSG, change_draft, compare_answer, resume_request, roles

GOOD = "Take 5의 3장 청중 시선 비율은 28%예요."
BAD = "Take 5의 3장 청중 시선 비율은 99%예요."
PREV = "Take 4의 3장 청중 시선 비율은 31%예요."


def script(answer: Any) -> list[Any]:
    return [F.plan(assessments=[change_draft()]), F.tools(COMPARE), F.done(), answer]


def request() -> dict[str, Any]:
    return F.with_loaded(F.request(user_message=MSG), "t-4")


def claim_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ln["data"] for ln in lines if ln["event"] == "claim"]


class Feeder:
    """답 초안 글을 한 글자씩, 표시한 자리까지 이어서 흘린다."""

    def __init__(self, sink: Any, text: str) -> None:
        self.sink, self.text, self.pos = sink, text, 0
        sink.start(1)

    def until(self, marker: str | None) -> int:
        """`marker` 앞까지(없으면 끝까지) 흘리고, 그때까지 낸 본문 문장 수를 돌려준다."""
        end = self.text.index(marker) if marker is not None else len(self.text)
        for ch in self.text[self.pos : end]:
            self.sink.feed(1, ch)
        self.pos = max(self.pos, end)
        return len(self.sink.sent)


def assert_prefix_invariant(lines: list[dict[str, Any]], data: dict[str, Any]) -> None:
    """5-4: 문장 줄은 끝 줄 message 문장과 글자 · 종류 · 근거까지 같고 번호가 0부터 1씩, 끝 줄은 문장 줄 뒤 하나."""
    cl = claim_lines(lines)
    for c in cl:
        ClaimLineData.model_validate(c)
    claims = data["message"]["claims"]
    assert [c["index"] for c in cl] == list(range(len(claims)))
    assert [c["claim"] for c in cl] == claims
    assert lines[-1]["event"] == "final"
    # 근거 · 자료 사본은 처음 쓰는 줄에만 싣는다
    ev_ids = [e["evidence_id"] for c in cl for e in c["evidence"]]
    assert len(ev_ids) == len(set(ev_ids))
    assert set(ev_ids) <= {e["evidence_id"] for e in data["evidence"]}


# ── 통과한 문장부터 낸다 ─────────────────────────────────────────────────────


def test_sentences_go_out_as_draft_elements_close_before_the_draft_ends() -> None:
    probes: list[int] = []

    def streamed(r: Any) -> Any:
        res = compare_answer()(r)
        f = Feeder(r.text_sink, res.message)
        probes.append(f.until('"kind": "ASSESSMENT"'))  # 판단 목록만 닫혔다: 설명을 기다린다
        probes.append(f.until('"kind": "CONCLUSION"'))  # 판단 0의 설명이 닫혔다: 판단 문장을 낸다
        probes.append(f.until('"kind": "RECORD"'))  # 결론이 닫혔다: 결론 문장까지
        probes.append(f.until('"start_practice"'))  # 기록 문장까지
        f.until(None)
        return res

    llm = F.FakeLlm(script(streamed), stream_chunk=None)
    status, lines, data = F.run_turn(request(), llm)
    assert status == 200
    assert probes == [0, 1, 2, 3]
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert_prefix_invariant(lines, data)
    cl = claim_lines(lines)
    # 판단 목록이 닫힌 뒤 처음 낸 줄에 확정 판단이 모두 실린다
    assert [a["assessment_id"] for a in cl[0]["assessments"]] == [
        a.assessment_id for a in fd.assessments
    ]
    assert all(c["assessments"] == [] for c in cl[1:])


def test_code_sentences_decided_before_the_model_call_go_out_first() -> None:
    """조사가 반복으로 끝났으면 중단 안내(코드 고정문)를 답 쓰기 모델을 부르기 전에 낸다. 모델에게 알리는
    '이미 보인 문장'에는 넣지 않는다(답을 비우지 않게)."""
    probes: list[list[str]] = []

    def streamed(r: Any) -> Any:
        probes.append([c.text for c in r.text_sink.pre])
        return compare_answer()(r)

    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(COMPARE, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            streamed,
        ]
    )
    _, lines, data = F.run_turn(request(), llm)
    assert probes == [[templates.SystemText.NO_MORE_SOURCES.value]]
    assert claim_lines(lines)[0]["claim"]["text"] == templates.SystemText.NO_MORE_SOURCES.value
    assert data["status"] == "PARTIAL"
    assert_prefix_invariant(lines, data)
    research = next(c for c in llm.calls if c.purpose == "ANSWER").messages[-1]["content"]
    assert '"already_said"' not in research


def test_failed_sentence_holds_itself_and_later_ones_then_goes_out_after_the_fix() -> None:
    probes: list[int] = []

    def streamed(r: Any) -> Any:
        ro = roles(r)
        res = F.answer(
            [
                F.claim("ASSESSMENT", "", assessment_index=0),
                F.claim("RECORD", BAD, evidence_ids=[ro["current"]]),
                F.claim("RECORD", PREV, evidence_ids=[ro["previous"]]),
            ]
        )
        probes.append(Feeder(r.text_sink, res.message).until(None))
        return res

    llm = F.FakeLlm(script(streamed), stream_chunk=None, fixes=[F.fix({1: GOOD})])
    _, lines, data = F.run_turn(request(), llm)
    # 판단 문장만 나가고, 걸린 기록 문장과 그 뒤(통과하는 문장 포함)는 보류했다
    assert probes == [1]
    fd = FinalData.model_validate(data)
    assert [c.text for c in fd.message.claims][1:] == [GOOD, PREV]  # type: ignore[union-attr]
    assert_prefix_invariant(lines, data)
    # 고치기 호출의 문장 번호는 이 답의 본문 전체 기준(이미 낸 판단 문장이 0번)
    fix_req = next(c for c in llm.calls if c.purpose == "FIX")
    items = json.loads(fix_req.messages[-1]["content"].split("\n")[1])
    assert [(i["index"], i["text"]) for i in items] == [(1, BAD)]


def test_model_failure_after_a_sentence_went_out_keeps_it_and_ends_partial() -> None:
    def cut(r: Any) -> Any:
        res = compare_answer()(r)
        assert Feeder(r.text_sink, res.message).until('"kind": "RECORD"') == 2
        return LlmTimeout(attempts=1, usage=[Usage()], cause="stream_timeout")

    llm = F.FakeLlm(script(cut), stream_chunk=None)
    _, lines, data = F.run_turn(request(), llm)
    assert lines[-1]["event"] == "final"
    fd = FinalData.model_validate(data)
    assert fd.status == "PARTIAL"
    claims = [c.model_dump(mode="json") for c in fd.message.claims]  # type: ignore[union-attr]
    sent = [c["claim"] for c in claim_lines(lines)]
    assert claims[:2] == sent[:2]
    assert claims[2]["text"] == templates.SystemText.PARTIAL_CHECKED.value
    assert_prefix_invariant(lines, data)
    # 줄로 보낸 판단은 끝 줄에도 있다
    assert {a.assessment_id for a in fd.assessments} == {
        a["assessment_id"] for c in claim_lines(lines) for a in c["assessments"]
    }


def test_out_of_scope_draft_streams_no_model_sentence() -> None:
    def oos(r: Any) -> Any:
        ro = roles(r)
        return F.answer(
            [F.claim("RECORD", GOOD, evidence_ids=[ro["current"]])],
            route="OUT_OF_SCOPE",
            scope_reason="OTHER",
        )

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(oos)))
    fd = FinalData.model_validate(data)
    assert fd.status == "REFUSED"
    assert all(c["claim"]["kind"] == "SYSTEM" for c in claim_lines(lines))
    assert_prefix_invariant(lines, data)


def test_assessment_explanation_after_another_kind_is_dropped() -> None:
    def late(r: Any) -> Any:
        ro = roles(r)
        return F.answer(
            [
                F.claim("CONCLUSION", "", comparison_evidence_id=ro["outcome"]),
                F.claim(
                    "ASSESSMENT", "3장에서 청중을 보는 시간을 늘려 보세요.", assessment_index=0
                ),
            ]
        )

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(late)))
    fd = FinalData.model_validate(data)
    head = fd.message.claims[0]  # type: ignore[union-attr]
    assert head.kind == "ASSESSMENT" and "늘려 보세요" not in head.text
    assert_prefix_invariant(lines, data)


def test_chunks_of_another_attempt_or_after_close_are_ignored() -> None:
    kept: dict[str, Any] = {}

    def streamed(r: Any) -> Any:
        res = compare_answer()(r)
        sink = kept["sink"] = r.text_sink
        sink.start(1)
        sink.feed(2, res.message)  # 지난 · 다른 시도의 조각
        assert sink.sent == []
        sink.feed(1, res.message)
        return res

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(streamed), stream_chunk=None))
    n = len(claim_lines(lines))
    kept["sink"].feed(1, '{"route":"ANSWER","assessments":[],"claims":[{"kind":"RECORD"}]}')
    assert len(kept["sink"].sent) <= n and kept["sink"].closed
    assert_prefix_invariant(lines, data)


def test_stream_processing_error_does_not_break_the_answer(monkeypatch: Any) -> None:
    from chat_agent import stream_json

    def boom(self: Any, text: str) -> list[Any]:
        raise RuntimeError("추적기 오류")

    monkeypatch.setattr(stream_json.StreamTracker, "feed", boom)
    log: list[dict[str, Any]] = [{"event": "test_start"}]  # 빈 목록이면 run_turn 이 새 목록을 쓴다
    _, lines, data = F.run_turn(request(), F.FakeLlm(script(compare_answer())), log=log)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE" and len(fd.message.claims) == 3  # type: ignore[union-attr]
    assert any(e.get("event") == "agent_stream_error" for e in log)
    assert_prefix_invariant(lines, data)


# ── 이어 부르기에서도 번호가 이어진다 ────────────────────────────────────────


def test_claim_index_continues_across_the_approval_pause() -> None:
    req = first_request()
    llm = F.FakeLlm(save_script(), answers=[record_answer], fixes=[F.fix({})])
    _, lines1, d1 = F.run_turn(req, llm)
    assert d1["status"] == "NEEDS_ACTION"
    said = d1["message"]["claims"]
    assert [c["index"] for c in claim_lines(lines1)] == list(range(len(said)))
    assert [c["claim"] for c in claim_lines(lines1)] == said
    llm2 = F.FakeLlm([F.done()], answers=[F.answer([])], fixes=[F.fix({})])
    req2 = resume_request(req, d1, saved_result(d1["actions"][0]))
    req2["context"]["conversation_goal"] = None
    _, lines2, d2 = F.run_turn(req2, llm2)
    claims = d2["message"]["claims"]
    cl2 = claim_lines(lines2)
    assert claims[: len(said)] == said
    assert [c["index"] for c in cl2] == list(range(len(said), len(claims)))
    assert [c["claim"] for c in cl2] == claims[len(said) :]
    # 행동 결과 문장(코드)은 이어 부른 응답의 첫 문장 줄이다
    assert cl2[0]["claim"]["kind"] == "ACTION_RESULT"
    # 앞 응답에서 보낸 판단 · 근거는 다시 싣지 않는다
    assert all(c["assessments"] == [] for c in cl2)
    sent1 = {e["evidence_id"] for c in claim_lines(lines1) for e in c["evidence"]}
    assert not sent1 & {e["evidence_id"] for c in cl2 for e in c["evidence"]}
