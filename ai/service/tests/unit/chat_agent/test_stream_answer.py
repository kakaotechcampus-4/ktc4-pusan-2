"""문장 줄(`claim`) 시험 — 검사를 통과한 문장부터 내고, 낸 문장은 언제나 끝 줄 message 의 앞부분이다 (기능 문서 5-4 · 6-1 5번 · 6-9).

가짜 모델이 답 초안을 글자 단위로 흘리게 하고, 흘리는 중간에 답 쓰는 쪽(`text_sink`)이 낸 본문 문장 수를 본다.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.llm_types import LlmRefused, LlmTimeout, Usage
from pitch_coach_ai.features.chat_agent.schemas import ClaimLineData, FinalData

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


def _closure(ev: dict[str, Any], ids: list[str]) -> set[str]:
    """근거 id 와 계산 근거의 입력을 따라간 id. 끝 줄 근거에 없는 id 를 가리키면 실패다(사본이 빠진 것)."""
    out: set[str] = set()
    stack = list(ids)
    while stack:
        i = stack.pop()
        if i in out:
            continue
        assert i in ev, f"끝 줄 근거에 없는 id: {i}"
        out.add(i)
        stack.extend(ev[i].get("inputs") or [])
    return out


def assert_prefix_invariant(lines: list[dict[str, Any]], data: dict[str, Any]) -> None:
    """5-4: 문장 줄은 끝 줄 message 문장과 글자 · 종류 · 근거까지 같고 번호가 0부터 1씩, 끝 줄은 문장 줄 뒤 하나.

    근거 · 자료 · 판단 사본은 처음 쓰는 줄에만, 끝 줄의 사본과 같은 내용으로 싣는다. 줄마다 그때까지 받은 사본으로
    그 문장을 그릴 수 있고(근거와 계산 근거의 입력 · 자료 카드), 문장 줄을 받았으면 확정 판단이 모두 있다."""
    cl = claim_lines(lines)
    for c in cl:
        ClaimLineData.model_validate(c)
    claims = data["message"]["claims"]
    assert [c["index"] for c in cl] == list(range(len(claims)))
    assert [c["claim"] for c in cl] == claims
    assert lines[-1]["event"] == "final"
    final_ev = {e["evidence_id"]: e for e in data["evidence"]}
    final_src = {g["source_id"]: g for g in data["message"]["sources"]}
    final_as = {a["assessment_id"]: a for a in data["assessments"]}
    have_ev: set[str] = set()
    have_src: set[str] = set()
    have_as: set[str] = set()
    for c in cl:
        for e in c["evidence"]:
            assert e["evidence_id"] not in have_ev and e == final_ev[e["evidence_id"]]
            have_ev.add(e["evidence_id"])
        for g in c["sources"]:
            assert g["source_id"] not in have_src
            # 끝 줄 message.sources 는 답 문장이 쓴 자료다. 문장이 없는 판단의 자료 사본은 문장 줄에만 있을 수 있다
            assert g == final_src.get(g["source_id"], g)
            have_src.add(g["source_id"])
        for a in c["assessments"]:
            assert a["assessment_id"] not in have_as and a == final_as[a["assessment_id"]]
            have_as.add(a["assessment_id"])
        ids = [
            *c["claim"]["evidence_ids"],
            *(e for a in c["assessments"] for e in a["evidence_ids"]),
        ]
        assert _closure(final_ev, ids) <= have_ev
        srcs = [*c["claim"]["source_ids"], *(s for a in c["assessments"] for s in a["source_ids"])]
        assert set(srcs) <= have_src
        if c["claim"].get("assessment_id"):
            assert c["claim"]["assessment_id"] in have_as
    if cl:
        assert have_as == set(final_as)


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


def test_refusal_after_a_sentence_went_out_ends_partial_not_refused() -> None:
    def cut(r: Any) -> Any:
        res = compare_answer()(r)
        assert Feeder(r.text_sink, res.message).until('"kind": "RECORD"') == 2
        return LlmRefused(attempts=1, usage=[Usage()], cause="content_filter")

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(cut), stream_chunk=None))
    fd = FinalData.model_validate(data)
    assert fd.status == "PARTIAL"
    assert fd.message.claims[-1].text == templates.SystemText.PARTIAL_CHECKED.value  # type: ignore[union-attr]
    assert_prefix_invariant(lines, data)


def test_refusal_before_any_sentence_stays_refused() -> None:
    def refuse(r: Any) -> Any:
        return LlmRefused(attempts=1, usage=[Usage()], cause="content_filter")

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(refuse)))
    assert data["status"] == "REFUSED"
    assert_prefix_invariant(lines, data)


def test_refusal_after_only_code_sentences_went_out_ends_partial() -> None:
    """모델 문장은 아직 없어도 앞 문장(조사 중단 안내)을 냈으면 공급자 거절을 REFUSED 로 바꾸지 않는다(6-1 5번)."""

    def refuse(r: Any) -> Any:
        return LlmRefused(attempts=1, usage=[Usage()], cause="content_filter")

    llm = F.FakeLlm(
        [
            F.plan(assessments=[change_draft()]),
            F.tools(COMPARE, prefix="a"),
            F.tools(COMPARE, prefix="b"),
            refuse,
        ]
    )
    _, lines, data = F.run_turn(request(), llm)
    assert data["status"] == "PARTIAL"
    texts = [c["text"] for c in data["message"]["claims"]]
    assert texts[0] == templates.SystemText.NO_MORE_SOURCES.value
    assert texts[-1] == templates.SystemText.PARTIAL_CHECKED.value
    assert_prefix_invariant(lines, data)


def test_ask_pause_answer_failure_after_a_sentence_still_pauses_without_repeating_it() -> None:
    """승인 대기 전 답이 문장을 낸 뒤 실패해도 확인 카드와 함께 멈춘다(6-1 4번). 낸 판단 문장의 판단은 설명 없는 첫
    문장으로 다시 그리지 않는다."""
    expl = "3장에서 청중을 보는 시간을 늘려 보세요."

    def cut(r: Any) -> Any:
        res = F.answer(
            [
                F.claim("ASSESSMENT", expl, assessment_index=0),
                F.claim("RECORD", GOOD, evidence_ids=[roles(r)["current"]]),
            ]
        )
        assert Feeder(r.text_sink, res.message).until('"kind": "RECORD"') == 1
        return LlmTimeout(attempts=1, usage=[Usage()], cause="stream_timeout")

    llm = F.FakeLlm(save_script(), answers=[cut], fixes=[F.fix({})], stream_chunk=None)
    _, lines, data = F.run_turn(first_request(), llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION"
    assert [c.type for c in fd.message.cards] == ["ASK_CONFIRM"]  # type: ignore[union-attr]
    claims = fd.message.claims  # type: ignore[union-attr]
    assert expl in claims[0].text
    ids = [c.assessment_id for c in claims if c.kind == "ASSESSMENT"]
    assert len(ids) == len(set(ids))
    assert_prefix_invariant(lines, data)


def test_duplicate_top_level_key_stops_streaming() -> None:
    """같은 최상위 키가 두 번 오면(다 받은 파싱은 뒤 값을 씀) 그 뒤로는 흘려 내지 않는다."""

    def dup(r: Any) -> Any:
        res = compare_answer()(r)
        text = res.message.replace(
            '"route": "ANSWER"', '"route": "ANSWER", "route": "OUT_OF_SCOPE"', 1
        )
        assert Feeder(r.text_sink, text).until(None) == 0
        parsed = {**(res.parsed or {}), "route": "OUT_OF_SCOPE", "scope_reason": "OTHER"}
        return dataclasses.replace(res, parsed=parsed)

    _, lines, data = F.run_turn(request(), F.FakeLlm(script(dup), stream_chunk=None))
    assert data["status"] == "REFUSED"
    assert all(c["claim"]["kind"] == "SYSTEM" for c in claim_lines(lines))
    assert_prefix_invariant(lines, data)


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
    sink = kept["sink"]
    before = list(sink.sent)
    sink.feed(1, json.dumps(json.loads(F.answer([F.claim("RECORD", GOOD)]).message)))
    assert sink.closed and sink.sent == before
    assert_prefix_invariant(lines, data)


def test_stream_processing_error_does_not_break_the_answer(monkeypatch: Any) -> None:
    from pitch_coach_ai.features.chat_agent import stream_json

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
