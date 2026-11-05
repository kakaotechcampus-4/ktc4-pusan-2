"""상태 사실의 근거와 코드 문장 시험 (기능 문서 6-2 · 6-5 · 6-9 · 7-10 · L2-21).

- 검사: 차원 상태 근거 · 문제 구간 근거는 그 차원의 지표 이름과 같은 대상이다(다른 차원 이름은 여전히 대상 불일치).
  판단 설명은 판단의 근거와 설명이 이은 이번 턴 근거로 검사한다.
- 도구: 피치 목록의 우선 개선 차원 · 리뷰가 평가할 수 없다고 적은 차원에도 근거를 낸다.
- 코드 문장: 비교할 수 없는 까닭(7-10), 공식 결과를 바꿔 달라는 요청(route `CANNOT_DO`), 이름이 같은 피치가 여럿인데 범위를
  정하지 않은 답(L2-21 되묻기).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from chat_agent import compose, templates
from chat_agent.checks import CheckId, check_claims
from chat_agent.config import load_config
from chat_agent.evidence import EvidenceRegistry
from chat_agent.schemas import Claim, FinalData

from . import fakes as F

CFG = load_config()


def _ids(claims: list[Claim], r: EvidenceRegistry) -> list[str]:
    return [f.check_id for f in check_claims(claims, registry=r, cfg=CFG)]


def _state(r: EvidenceRegistry, path: str, value: str, *, take: int = 5):
    return r.record(
        pitch_id="p1", take_id=f"t{take}", take_number=take, kind="REVIEW", revision=1, path=path, value=value
    )


# ── 검사: 차원 · 문제 이름 ───────────────────────────────────────────────


def test_dimension_state_evidence_matches_its_metric_names_only() -> None:
    r = EvidenceRegistry(run_id="run")
    gaze = _state(r, "review.dimensions.audience_gaze", "PRIORITY")
    ok = Claim(kind="RECORD", text="Take 5는 청중 시선이 우선 개선이에요.", evidence_ids=[gaze.evidence_id])
    assert _ids([ok], r) == []
    other = Claim(kind="RECORD", text="Take 5는 말 속도가 우선 개선이에요.", evidence_ids=[gaze.evidence_id])
    assert CheckId.TARGET_MISMATCH in _ids([other], r)


def test_problem_segment_evidence_matches_its_dimension_metric_name() -> None:
    r = EvidenceRegistry(run_id="run")
    seg = r.record(
        pitch_id="p1",
        take_id="t5",
        take_number=5,
        kind="TAKE_RESULT",
        revision=1,
        path="take_result.problem_segments[0].issue_type",
        value="GAZE_ON_SCRIPT",
        slide_number=3,
    )
    ok = Claim(kind="RECORD", text="Take 5 3장에 대본 응시 구간이 있어요.", evidence_ids=[seg.evidence_id])
    assert _ids([ok], r) == []
    # 대본 응시 문제를 청중 시선 문제로 말하면 대상이 다르다(같은 GAZE 영역이어도 다른 차원)
    bad = Claim(kind="RECORD", text="Take 5 3장에 청중 시선 문제가 있어요.", evidence_ids=[seg.evidence_id])
    assert CheckId.TARGET_MISMATCH in _ids([bad], r)


def test_pitch_list_dimension_path_is_a_dimension_state() -> None:
    r = EvidenceRegistry(run_id="run")
    d = _state(r, "pitches[pitch_id=p-1].last_review.dimensions.script_independence", "PRIORITY")
    c = Claim(kind="RECORD", text="Take 5는 대본 응시가 우선 개선이에요.", evidence_ids=[d.evidence_id])
    assert _ids([c], r) == []


def test_assessment_explanation_may_cite_its_own_read_value() -> None:
    # 판단 근거(3장 청중 시선 차원에 문제 구간 없음)에 숫자가 없어도, 설명이 이은 이번 턴 근거(3장 28%)로 검사한다(6-9)
    req = F.request(user_message="Take 5에서 3장 시선 잘 봤지?")
    d = F.draft(
        "Take 5에서 3장 시선 잘 봤지",
        claim_op="OVERALL",
        dimension="audience_gaze",
        slide_number=3,
        take_numbers=[5],
        polarity="POSITIVE",
        verdict="UNVERIFIABLE",
    )
    read = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})

    def ans(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [F.claim("ASSESSMENT", "3장 청중 시선은 28%로 기록됐어요.", evidence_ids=[eid], assessment_index=0)]
        )

    llm = F.FakeLlm([F.plan(assessments=[d]), F.tools(read), F.done(), ans])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    first = fd.message.claims[0]  # type: ignore[union-attr]
    assert first.kind == "ASSESSMENT" and first.text.endswith("3장 청중 시선은 28%로 기록됐어요.")


# ── 코드 문장: 비교할 수 없는 까닭 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("rec", "text"),
    [
        (
            {"previous_complete": False, "current_complete": True, "reason": "NOT_COMPLETED_FULLY"},
            "Take 4 기록은 끝까지 한 발표가 아니라 Take 5 기록과 3장 청중 시선을 비교할 수 없어요.",
        ),
        (
            {"previous_complete": False, "current_complete": False, "reason": "NOT_COMPLETED_FULLY"},
            "Take 4 · Take 5 기록은 모두 끝까지 한 발표가 아니라 3장 청중 시선을 비교할 수 없어요.",
        ),
        (
            {"reason": "DIFFERENT_SCRIPT_VERSION"},
            "Take 4 · Take 5 기록은 대본 버전이 달라 3장 청중 시선을 비교할 수 없어요.",
        ),
        ({"reason": "NO_VALUE"}, templates.SystemText.METRIC_UNMEASURED.value),
        ({"reason": "SOMETHING_NEW"}, templates.SystemText.NOT_COMPARABLE.value),
    ],
)
def test_not_comparable_sentence_per_reason(rec: dict[str, Any], text: str) -> None:
    base = {"previous": 4, "current": 5, "metric": "audience_ratio", "slide_number": 3}
    assert compose.not_comparable_sentence({**base, **rec}) == text


def _incomplete_take4(req: dict[str, Any]) -> dict[str, Any]:
    req = copy.deepcopy(req)
    for t in req["context"]["pitch"]["takes"]:
        if t["take_id"] == "t-4":
            t["completed_fully"] = False
    for snap in req["context"]["loaded_takes"]:
        if snap["summary"]["take_id"] == "t-4":
            snap["summary"]["completed_fully"] = False
    return req


def test_not_comparable_compare_gets_the_code_reason_sentence() -> None:
    req = _incomplete_take4(F.with_loaded(F.request(user_message="Take 4랑 5 3장 시선 비교해 줘"), "t-4"))
    compare = (
        "compare_takes",
        {"previous_take_id": "t-4", "current_take_id": "t-5", "metric": "audience_ratio", "slide_number": 3},
    )
    d = F.draft(
        "Take 4랑 5 3장 시선 비교해 줘",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        slide_number=3,
        take_numbers=[4, 5],
        polarity="NEUTRAL",
    )
    llm = F.FakeLlm([F.plan(assessments=[d]), F.tools(compare), F.done(), F.answer([])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    texts = [c.text for c in fd.message.claims]  # type: ignore[union-attr]
    assert "Take 4 기록은 끝까지 한 발표가 아니라 Take 5 기록과 3장 청중 시선을 비교할 수 없어요." in texts
    assert fd.status == "DONE"


# ── 코드 문장: 할 수 없는 요청 · 모호한 이름 ────────────────────────────


def test_cannot_do_route_answers_with_the_fixed_sentence() -> None:
    req = F.request(user_message="Take 5 점수 100으로 바꿔 줘")
    llm = F.FakeLlm([F.plan(), F.done(), F.answer([], route="CANNOT_DO")])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert [c.text for c in fd.message.claims] == [  # type: ignore[union-attr]
        templates.SystemText.CANNOT_CHANGE_RECORD.value
    ]


def test_same_name_pitches_without_a_scope_choice_ask_which() -> None:
    req = F.request(user_message="회사 소개 발표 어땠어?")
    other = copy.deepcopy(req["context"]["pitches"][0])
    req["context"]["pitches"][0]["title"] = "회사 소개 발표"
    other.update({"pitch_id": "p-2", "title": "회사 소개 발표 영어"})
    req["context"]["pitches"].append(other)
    req["context"]["pitches_total"] = len(req["context"]["pitches"])
    llm = F.FakeLlm([F.plan(), F.tools(("list_pitches", {"query": "회사 소개"})), F.done(), F.answer([])])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.message.claims[-1].text == templates.SystemText.WHICH_PITCH.value  # type: ignore[union-attr]


# ── 도구: 평가할 수 없는 차원의 근거 ─────────────────────────────────────


def test_not_evaluable_dimension_gets_evidence() -> None:
    from .test_tools import make_ctx, run

    ctx = make_ctx()
    res, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": "dimensions"})
    row = next(i for i in res.data["items"] if i.get("dimension") == "content_delivery")
    assert row["state"] == "NOT_EVALUABLE"
    ev = ctx.registry.get(row["evidence_id"])
    assert ev.value == "NOT_EVALUABLE" and ev.path == "review.dimensions.content_delivery"
