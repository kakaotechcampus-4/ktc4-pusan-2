"""조사 흐름 · 상태 문장 변경의 경계 시험 (병렬 검증 지적 반영).

- 조사 흐름(6-1 2번): 먼저 읽기 상한(가져오기 포함), 가져오기 실패 뒤 다시 내지 않음, 차원만 · 문제 주장의 section.
- 대상 일치(6-9): 문제 구간은 그 문제가 직접 뒷받침하는 지표만(`issue_metrics`), 차원 상태 근거는 칭찬 대상도 그 차원뿐.
- 비교할 수 없는 까닭(7-10): 끝까지 했는지 모름 · 범위 없음 · 지워진 Take.
- 끝에 붙는 고정 문장: 여러 발표를 함께 물으면 되묻지 않음, CANNOT_DO 가 다른 문장이 모두 빠진 것을 가리지 않음.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from chat_agent import compose, templates
from chat_agent.checks import CheckId, check_claims
from chat_agent.config import load_config
from chat_agent.evidence import EvidenceRegistry
from chat_agent.schemas import Claim, FinalData

from . import fakes as F
from .test_state_sentences import _incomplete_take4
from .test_turn import resume_request

CFG = load_config()


def tool_calls_in(req: Any) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for m in req.messages:
        for tc in m.get("tool_calls") or []:
            out.append((tc["function"]["name"], json.loads(tc["function"]["arguments"])))
    return out


def slide(n: int, takes: list[int] | None = None, **kw: Any) -> dict[str, Any]:
    return F.draft(
        f"{n}장 시선",
        type_=kw.pop("type_", "REQUEST"),
        claim_op=kw.pop("claim_op", None),
        dimension=kw.pop("dimension", "audience_gaze"),
        metric=kw.pop("metric", "audience_ratio"),
        slide_number=n,
        take_numbers=takes or [5],
        polarity="NEUTRAL",
        **kw,
    )


# ── 조사 흐름 ─────────────────────────────────────────────────────────────


@pytest.mark.prefetch
def test_prefetch_cap_counts_the_fetch_too() -> None:
    drafts = [slide(1), slide(2), slide(3), slide(4), {**slide(3, takes=[4]), "quote": "3장 시선"}]
    llm = F.FakeLlm([F.plan(assessments=drafts), F.done(), F.answer([])])
    msg = "1장 시선, 2장 시선, 3장 시선, 4장 시선"
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    fd = FinalData.model_validate(data)
    # 읽기 4개로 상한(4)이 찼으니 Take 4 가져오기는 내지 않는다
    assert fd.status != "NEEDS_ACTION"
    assert len(tool_calls_in(llm.calls[1])) == 4


@pytest.mark.prefetch
def test_failed_prefetch_fetch_is_not_sent_again_nor_replaced() -> None:
    req = F.request(user_message="Take 4 3장 시선 몇 %였어?")
    llm = F.FakeLlm([F.plan(assessments=[slide(3, takes=[4])])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    res = [
        {
            "action_id": fd.actions[0].action_id,
            "type": "FETCH_TAKE",
            "status": "FAILED",
            "data": None,
            "error": {"code": "NOT_FOUND", "message": "없음"},
        }
    ]
    llm2 = F.FakeLlm([F.done(), F.answer([])])
    _, _, data2 = F.run_turn(resume_request(req, data, res), llm2)
    fd2 = FinalData.model_validate(data2)
    assert fd2.status != "NEEDS_ACTION"  # 같은 가져오기를 다시 내지 않는다
    assert [n for n, _ in tool_calls_in(llm2.calls[0]) if n != "fetch_take"] == []  # 다른 Take 를 읽지 않는다


@pytest.mark.prefetch
@pytest.mark.parametrize(
    ("draft", "section"),
    [
        (F.draft("시선 어땠어", type_="REQUEST", claim_op=None, dimension="audience_gaze", polarity="NEUTRAL"), "dimensions"),
        (F.draft("말이 빨랐어", claim_op="ISSUE", dimension="pacing_rhythm"), "problem_segments"),
    ],
)
def test_prefetch_section_follows_the_draft(draft: dict[str, Any], section: str) -> None:
    llm = F.FakeLlm([F.plan(assessments=[draft]), F.done(), F.answer([])])
    F.run_turn(F.request(user_message=draft["quote"]), llm)
    got = [a.get("section") for n, a in tool_calls_in(llm.calls[1]) if n == "get_take_report"]
    assert section in got and all(a["take_id"] == "t-5" for n, a in tool_calls_in(llm.calls[1]))


# ── 대상 일치 ─────────────────────────────────────────────────────────────


def _seg(r: EvidenceRegistry, issue: str) -> str:
    return r.record(
        pitch_id="p1",
        take_id="t5",
        take_number=5,
        kind="TAKE_RESULT",
        revision=1,
        path="take_result.problem_segments[0].issue_type",
        value=issue,
        slide_number=3,
    ).evidence_id


@pytest.mark.parametrize(
    ("issue", "ok", "bad"),
    [
        ("PACE_FAST", "Take 5 3장에 말 속도 문제 구간이 있어요.", "Take 5 3장에 긴 침묵 문제 구간이 있어요."),
        ("GAZE_ON_SCRIPT", "Take 5 3장에 대본 응시 구간이 있어요.", "Take 5 3장에 대본 읽기 비율 문제가 있어요."),
    ],
)
def test_problem_segment_supports_only_its_own_metric(issue: str, ok: str, bad: str) -> None:
    r = EvidenceRegistry(run_id="run")
    eid = _seg(r, issue)
    assert [f.check_id for f in check_claims([Claim(kind="RECORD", text=ok, evidence_ids=[eid])], registry=r, cfg=CFG)] == []
    got = [f.check_id for f in check_claims([Claim(kind="RECORD", text=bad, evidence_ids=[eid])], registry=r, cfg=CFG)]
    assert CheckId.TARGET_MISMATCH in got


def test_dimension_strength_does_not_back_praise_of_another_dimension() -> None:
    r = EvidenceRegistry(run_id="run")
    strength = r.record(
        pitch_id="p1", take_id="t5", take_number=5, kind="REVIEW", revision=1,
        path="review.dimensions.script_independence", value="STRENGTH",
    )  # fmt: skip
    gaze = r.record(
        pitch_id="p1", take_id="t5", take_number=5, kind="TAKE_RESULT", revision=1,
        path="take_result.gaze", value=0.28, metric="audience_ratio",
    )  # fmt: skip
    c = Claim(
        kind="RECORD",
        text="Take 5 청중 시선 28%, 잘하셨어요.",
        evidence_ids=[strength.evidence_id, gaze.evidence_id],
    )
    assert CheckId.AGREEMENT_WORD in [f.check_id for f in check_claims([c], registry=r, cfg=CFG)]


# ── 비교할 수 없는 까닭 ───────────────────────────────────────────────────


def test_unknown_completion_and_missing_range_are_not_overstated() -> None:
    base = {"previous": 4, "current": 5, "metric": "cpm", "slide_number": None}
    unknown = {**base, "reason": "NOT_COMPLETED_FULLY", "previous_complete": None, "current_complete": True}
    assert compose.not_comparable_sentence(unknown) == templates.SystemText.COMPLETION_UNKNOWN.value
    assert (
        compose.not_comparable_sentence({**base, "reason": "NO_RANGE"})
        == templates.SystemText.NOT_COMPARABLE.value
    )


def test_not_comparable_sentence_is_dropped_when_its_take_is_deleted() -> None:
    req = _incomplete_take4(F.with_loaded(F.request(user_message="Take 4랑 5 3장 시선 비교해 줘"), "t-4"))
    d = F.draft(
        "Take 4랑 5 3장 시선 비교해 줘",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        slide_number=3,
        take_numbers=[4, 5],
        polarity="NEUTRAL",
    )
    compare = (
        "compare_takes",
        {"previous_take_id": "t-4", "current_take_id": "t-5", "metric": "audience_ratio", "slide_number": 3},
    )
    fetch3 = ("fetch_take", {"take_id": "t-3", "reason": "다른 Take"})
    llm = F.FakeLlm([F.plan(assessments=[d]), F.tools(compare), F.tools(fetch3, prefix="act")])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "NEEDS_ACTION"
    from .test_turn import fetched_take

    res = [
        {
            "action_id": fd.actions[0].action_id,
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    req2 = resume_request(req, data, res)
    req2["context"]["removed_sources"] = ["t-4"]
    req2["context"]["pitch"]["takes"] = [t for t in req2["context"]["pitch"]["takes"] if t["take_id"] != "t-4"]
    req2["context"]["loaded_takes"] = [
        s for s in req2["context"]["loaded_takes"] if s["summary"]["take_id"] != "t-4"
    ]
    llm2 = F.FakeLlm([F.done(), F.answer([])])
    _, _, data2 = F.run_turn(req2, llm2)
    texts = [c["text"] for c in (data2.get("message") or {}).get("claims") or []]
    assert not any("끝까지 한 발표가 아니라" in t for t in texts)


# ── 끝에 붙는 고정 문장 ───────────────────────────────────────────────────


def _two_same_name(message: str) -> dict[str, Any]:
    req = F.request(user_message=message)
    other = copy.deepcopy(req["context"]["pitches"][0])
    req["context"]["pitches"][0]["title"] = "회사 소개 발표"
    other.update({"pitch_id": "p-2", "title": "회사 소개 발표 영어"})
    req["context"]["pitches"].append(other)
    req["context"]["pitches_total"] = len(req["context"]["pitches"])
    return req


def test_plural_request_is_not_asked_which_pitch() -> None:
    llm = F.FakeLlm([F.plan(), F.tools(("list_pitches", {"query": "회사 소개"})), F.done(), F.answer([])])
    _, _, data = F.run_turn(_two_same_name("회사 소개 발표들 점수 각각 알려 줘"), llm)
    texts = [c["text"] for c in (data.get("message") or {}).get("claims") or []]
    assert templates.SystemText.WHICH_PITCH.value not in texts


def test_cannot_do_sentence_survives_a_no_guide_refusal() -> None:
    req = F.request(user_message="점수 100으로 바꾸고 긴장 푸는 법도 알려 줘")
    advice = F.claim("GUIDE", "긴장을 풀려면 숨을 고르세요.", source_ids=["g-없음"])  # 자료 검증 실패 → 빠짐
    llm = F.FakeLlm([F.plan(), F.done(), F.answer([advice], route="CANNOT_DO")], fixes=[F.fix({0: None})])
    _, _, data = F.run_turn(req, llm)
    texts = [c["text"] for c in (data.get("message") or {}).get("claims") or []]
    assert templates.SystemText.CANNOT_CHANGE_RECORD.value in texts


def test_cannot_do_does_not_hide_dropped_record_answer() -> None:
    req = F.request(user_message="점수 100으로 바꾸고 3장 시선도 알려 줘")
    d = {**slide(3), "quote": "3장 시선도 알려 줘"}
    bad = F.claim("RECORD", "Take 5 3장 청중 시선은 99%예요.")  # 근거 없음 → 고쳐도 빠짐
    llm = F.FakeLlm(
        [F.plan(assessments=[d]), F.done(), F.answer([bad], route="CANNOT_DO")],
        fixes=[F.fix({0: None})],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "PARTIAL"  # 기록 질문의 답이 빠졌다는 것이 드러난다
    assert templates.SystemText.CANNOT_CHANGE_RECORD.value in fd.message.text  # type: ignore[union-attr]
