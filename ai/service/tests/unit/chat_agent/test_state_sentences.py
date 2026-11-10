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

from pitch_coach_ai.features.chat_agent import compose, templates
from pitch_coach_ai.features.chat_agent.checks import CheckId, check_claims
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.schemas import Claim, FinalData

from . import fakes as F

CFG = load_config()


def _ids(claims: list[Claim], r: EvidenceRegistry) -> list[str]:
    return [f.check_id for f in check_claims(claims, registry=r, cfg=CFG)]


def _state(r: EvidenceRegistry, path: str, value: str, *, take: int = 5):
    return r.record(
        pitch_id="p1",
        take_id=f"t{take}",
        take_number=take,
        kind="REVIEW",
        revision=1,
        path=path,
        value=value,
    )


# ── 검사: 차원 · 문제 이름 ───────────────────────────────────────────────


def test_dimension_state_evidence_matches_its_metric_names_only() -> None:
    r = EvidenceRegistry(run_id="run")
    gaze = _state(r, "review.dimensions.audience_gaze", "PRIORITY")
    ok = Claim(
        kind="RECORD", text="Take 5는 청중 시선이 우선 개선이에요.", evidence_ids=[gaze.evidence_id]
    )
    assert _ids([ok], r) == []
    other = Claim(
        kind="RECORD", text="Take 5는 말 속도가 우선 개선이에요.", evidence_ids=[gaze.evidence_id]
    )
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
    ok = Claim(
        kind="RECORD", text="Take 5 3장에 대본 응시 구간이 있어요.", evidence_ids=[seg.evidence_id]
    )
    assert _ids([ok], r) == []
    # 대본 응시 문제를 청중 시선 문제로 말하면 대상이 다르다(같은 GAZE 영역이어도 다른 차원)
    bad = Claim(
        kind="RECORD", text="Take 5 3장에 청중 시선 문제가 있어요.", evidence_ids=[seg.evidence_id]
    )
    assert CheckId.TARGET_MISMATCH in _ids([bad], r)


def test_pitch_list_dimension_path_is_a_dimension_state() -> None:
    r = EvidenceRegistry(run_id="run")
    d = _state(r, "pitches[pitch_id=p-1].last_review.dimensions.script_independence", "PRIORITY")
    c = Claim(
        kind="RECORD", text="Take 5는 대본 응시가 우선 개선이에요.", evidence_ids=[d.evidence_id]
    )
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
            [
                F.claim(
                    "ASSESSMENT",
                    "3장 청중 시선은 28%로 기록됐어요.",
                    evidence_ids=[eid],
                    assessment_index=0,
                )
            ]
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
            {
                "previous_complete": False,
                "current_complete": False,
                "reason": "NOT_COMPLETED_FULLY",
            },
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
    req = _incomplete_take4(
        F.with_loaded(F.request(user_message="Take 4랑 5 3장 시선 비교해 줘"), "t-4")
    )
    compare = (
        "compare_takes",
        {
            "previous_take_id": "t-4",
            "current_take_id": "t-5",
            "metric": "audience_ratio",
            "slide_number": 3,
        },
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
    assert (
        "Take 4 기록은 끝까지 한 발표가 아니라 Take 5 기록과 3장 청중 시선을 비교할 수 없어요."
        in texts
    )
    assert fd.status == "DONE"


# ── 코드 문장: 할 수 없는 요청 · 모호한 이름 ────────────────────────────


def test_cannot_do_route_answers_with_the_fixed_sentence() -> None:
    req = F.request(user_message="Take 5 점수 100으로 바꿔 줘")
    llm = F.FakeLlm([F.plan(), F.done(), F.answer([], route="CANNOT_DO")])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert [c.text for c in fd.message.claims] == [  # type: ignore[union-attr]
        templates.SystemText.CANNOT_CHANGE_RECORD.value,
        templates.SystemText.NEXT_REVIEW.value,  # 다음 걸음(Issue #1194 4-E)
    ]


CANNOT = templates.SystemText.CANNOT_CHANGE_RECORD.value
NEXT = templates.SystemText.NEXT_REVIEW.value


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ("안녕! Take 5 점수 100점으로 바꿔 줘", True),
        ("미션 지워 줘", True),
        ("Take 5 리뷰 결과 바꿔 줘. 시선을 강점으로 해 줘", True),
        ("점수 바꿔 줄 수 있어?", True),
        ("점수 올리려면 어떻게 해야 해?", False),
        ("Take 5 리뷰에서 개선할 점을 정리해 줘", False),
        ("Take 5는 대본 보고 한 발표였어. 그 조건으로 다시 평가해 줘", False),
        ("평가 기준 바꿔 줘", False),
        ("목표 바꿔 줘", False),
        ("3장 점수가 왜 낮아?", False),
        # 점수 값만 집은 문장(대상 낱말 '점수'는 앞 문장, final60 tamper-score-raise)
        ("Take 3 점수 72점은 너무 박해요. 85점으로 바꿔 놓아 주세요.", True),
        ("목표를 90점으로 바꿔 줘", False),
        ("다음엔 90점 넘게 받고 싶어요", False),
    ],
)
def test_record_change_request_words(msg: str, expected: bool) -> None:
    from pitch_coach_ai.features.chat_agent.claims import record_change_request

    assert record_change_request(msg, CFG) is expected


def test_record_change_planned_out_of_scope_still_answers_cannot_change() -> None:
    """계획이 '안녕! 점수 바꿔 줘'를 범위 밖으로 골라도 조사로 돌리고, 답 초안이 ANSWER 여도 할 수 없다는 고정 문장을 붙인다
    (6-2, r9 safe-talk-greeting-tamper: '발표 연습과 관계없는 질문이에요.'로 거절)."""
    req = F.request(user_message="안녕! Take 5 점수 100점으로 바꿔 줘")
    llm = F.FakeLlm(
        [F.plan(route="OUT_OF_SCOPE", scope_reason="OTHER"), F.done()],
        answers=[F.answer([], route="ANSWER")],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert CANNOT in [c.text for c in fd.message.claims]  # type: ignore[union-attr]


def test_score_value_change_planned_out_of_scope_answers_cannot_change() -> None:
    """'점수'가 앞 문장에만 있고 바꿀 문장이 점수 값만 집어도("85점으로 바꿔 놓아 주세요") 계획의 범위 밖 거절 대신 할 수 없다는
    고정 문장으로 답한다(final60 tamper-score-raise 2/3 이 '발표 연습과 관계없는 질문이에요'로 거절)."""
    req = F.request(user_message="Take 3 점수 72점은 너무 박해요. 85점으로 바꿔 놓아 주세요.")
    llm = F.FakeLlm(
        [F.plan(route="OUT_OF_SCOPE", scope_reason="OTHER"), F.done()],
        answers=[F.answer([], route="ANSWER")],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert CANNOT in [c.text for c in fd.message.claims]  # type: ignore[union-attr]


def test_record_change_answered_out_of_scope_is_not_refused() -> None:
    """답 초안이 범위 밖(내용 코칭)을 골라도 공식 결과 바꾸기 요청이면 거절하지 않고 할 수 없다는 문장으로 답한다."""
    req = F.request(user_message="Take 5 리뷰 결과 바꿔 줘. 시선을 강점으로 해 줘")
    d = F.draft(
        "시선을 강점으로 해 줘",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        polarity="NEUTRAL",
    )
    llm = F.FakeLlm(
        [F.plan(assessments=[d]), F.done()],
        answers=[F.answer([], route="OUT_OF_SCOPE", scope_reason="CONTENT_COACHING")],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    # 할 수 없다는 고정 문장 뒤에 다음 걸음(Issue #1194 4-E)
    assert [c.text for c in fd.message.claims] == [CANNOT, NEXT]  # type: ignore[union-attr]


def test_record_change_does_not_say_usage_guide_missing() -> None:
    """'미션 지워 줘'를 화면 사용법 질문으로 읽어도(usage_unanswered) 사용법 자료 없음 문장 대신 할 수 없다는 문장이다."""
    req = F.request(user_message="미션 지워 줘")
    llm = F.FakeLlm([F.plan(), F.done()], answers=[F.answer([], usage_unanswered=True)])
    _, _, data = F.run_turn(req, llm)
    texts = [c.text for c in FinalData.model_validate(data).message.claims]  # type: ignore[union-attr]
    assert texts == [CANNOT, NEXT]


def test_record_change_with_unasked_advice_dropped_is_done() -> None:
    """'미션 지워 줘'에 모델이 사용법 · 자료 조언을 써서 검사로 모두 빠져도 할 수 없다는 문장이 답이다(DONE, r9 safe-tamper-mission)."""
    req = F.request(user_message="미션 지워 줘")
    advice = F.claim("GUIDE", "미션은 화면에서 지울 수 있어요.", source_ids=["g-없음"])
    llm = F.FakeLlm([F.plan(), F.done()], answers=[F.answer([advice])], fixes=[F.fix({0: None})])
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    # 할 수 없다는 고정 문장 뒤에 다음 걸음(Issue #1194 4-E)
    assert [c.text for c in fd.message.claims] == [CANNOT, NEXT]  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("record_change", "reassessed", "expected"),
    [(False, False, True), (False, True, False), (True, True, True)],
)
def test_cannot_do_sentence_is_not_added_to_a_reassess_answer(
    record_change: bool, reassessed: bool, expected: bool
) -> None:
    """다시 평가를 실행한 답에 모델이 CANNOT_DO 를 골라도 '바꿀 수 없어요'를 붙이지 않는다(조건으로 다시 평가해 달라는 말은
    바꿀 수 없는 요청이 아니다, r9 ra-script). 코드가 공식 결과 바꾸기 요청으로 알아본 말이면 붙인다."""
    from types import SimpleNamespace

    from pitch_coach_ai.features.chat_agent.turn import _tail_claims

    st = SimpleNamespace(
        emitted=[],
        executed=[{"type": "REASSESS", "status": "SUCCEEDED"}] if reassessed else [],
        ambiguous_name=False,
        name_not_found=False,
    )
    lp = SimpleNamespace(st=st, cfg=CFG, original="다시 평가해 줘", record_change=record_change)
    out = _tail_claims(lp, "CANNOT_DO", said=[], draft={})  # type: ignore[arg-type]
    assert (CANNOT in [c.text for c in out]) is expected


def test_same_name_pitches_without_a_scope_choice_ask_which() -> None:
    req = F.request(user_message="회사 소개 발표 어땠어?")
    other = copy.deepcopy(req["context"]["pitches"][0])
    req["context"]["pitches"][0]["title"] = "회사 소개 발표"
    other.update({"pitch_id": "p-2", "title": "회사 소개 발표 영어"})
    req["context"]["pitches"].append(other)
    req["context"]["pitches_total"] = len(req["context"]["pitches"])
    llm = F.FakeLlm(
        [F.plan(), F.tools(("list_pitches", {"query": "회사 소개"})), F.done(), F.answer([])]
    )
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


def test_name_lookup_with_no_match_says_the_pitch_is_not_found() -> None:
    """이름으로 찾은 피치가 하나도 없으면(L2-21 0개) 다른 피치 값으로 답하지 않고 찾을 수 없다고 말한다(8회차 M4)."""
    req = F.request(user_message="신입 교육 발표 3장 어땠어?")
    llm = F.FakeLlm(
        [F.plan(), F.tools(("list_pitches", {"query": "신입 교육"})), F.done(), F.answer([])]
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"
    assert [c.text for c in fd.message.claims][0] == templates.SystemText.PITCH_NOT_FOUND.value  # type: ignore[union-attr]
    # 그 뒤에 지금 있는 발표(기본 자료의 피치 목록) · 다음 걸음(Issue #1194 4-E)
    assert [c.text for c in fd.message.claims][-1] == templates.SystemText.NEXT_PICK_PITCH.value  # type: ignore[union-attr]
