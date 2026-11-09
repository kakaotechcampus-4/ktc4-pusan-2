"""PR 전 검증 지적 시험 — 고친 곳마다 가짜 모델(fakes.py)로 다시 생기지 않는지 본다(기능 문서 6-3 · 6-8 · 6-9 · 7-10 · 11-5).

- 문제 구간 0곳 근거: 장마다 근거 id 가 따로이고, 그 장의 값이 측정된 영역만 '없음'이다.
- Take 목록: 거른 조건에 맞는 Take 만 없으면 'Take 없음'(기록 없음 신호)이 아니다.
- 결정 로그: 사실 표시(FACT_SHOWN)는 그 사실이 화면 단계에 붙었을 때만 남긴다.
- 답 쓰기 실패의 대신 문장: 질문 대상의 근거로만 그린다(읽은 근거 전체를 나열하지 않는다).
- 코드 대신 답의 끝난 까닭: 반복으로 멈춘 조사면 ANSWERED 가 아니라 REPEAT 다.
- '같은 조건으로 다시 평가' 되살리기: 부정 · 인용 · 새 대상 · 조건이면 하지 않는다(확실하지 않으면 모델이 고른다).
- 허용 근거: 행동 동사 자체에 붙은 조건("목표 저장하면 알려줘")은 저장 요청이 아니다.
- 변화 주장: 부정한 주장("좋아지지 않았어")은 말한 방향의 여집합으로 판정한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import actions as act
from pitch_coach_ai.features.chat_agent import claims as clm
from pitch_coach_ai.features.chat_agent import compose
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout
from pitch_coach_ai.features.chat_agent.schemas import Subject

from . import fakes as F
from .test_approval import SAVE, drafts, first_request
from .test_claims import change_draft, make_take, t5
from .test_claims import run as judge
from .test_none_evidence import (
    NO_TAKE_PLAN,
    NO_TAKE_SENTENCE,
    _read,
    no_take_request,
    with_take_result,
)
from .test_plan_progress_turn import decisions
from .test_prefetch import slide_draft
from .test_signal_routes import research_blocks
from .test_tools import base_request, load, make_ctx, run
from .test_turn import COMPARE

SEG = "problem_segments"
GAZE = frozenset({"GAZE"})


def _gaze_none(res: Any) -> str:
    return next(r for r in res.data["no_segment_areas"] if r["area"] == "GAZE")["count"][
        "evidence_id"
    ]


# ── 1. 문제 구간 0곳 근거: 장 번호 · 장의 측정 여부 ─────────────────────────────


def test_slide_none_evidence_does_not_collide_between_two_slides() -> None:
    """한 실행에서 2장 · 3장을 읽어도 '없음' 근거 id 가 겹치지 않는다(뒤 장이 앞 장 근거를 덮어 장 번호가 바뀌지 않음)."""
    ctx = make_ctx(with_take_result(load(base_request(), "t-4"), issues=[]))
    r2, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": SEG, "slide_number": 2})
    r3, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": SEG, "slide_number": 3})
    e2, e3 = _gaze_none(r2), _gaze_none(r3)
    assert e2 != e3
    assert (ctx.registry.get(e2).slide_number, ctx.registry.get(e3).slide_number) == (2, 3)
    assert ctx.registry.get(e3).path == "problem_segments[area=GAZE,slide_number=3].count"
    assert [c.text for c in compose.none_partial(ctx, [e2, e3], areas=GAZE)] == [
        "Take 5 2장의 시선에서 찾은 문제 구간이 없어요.",
        "Take 5 3장의 시선에서 찾은 문제 구간이 없어요.",
    ]
    # Take 전체를 읽으면 경로에 장이 없다(장 근거와도 겹치지 않는다)
    rt, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": SEG})
    et = _gaze_none(rt)
    assert ctx.registry.get(et).path == "problem_segments[area=GAZE].count"
    assert et not in (e2, e3) and compose.segment_area(ctx.registry.get(et)) == "GAZE"


def test_slide_none_evidence_only_when_that_slide_is_measured() -> None:
    """Take 값은 측정됐어도 그 장의 값이 측정 안 됨이면 그 장 · 영역의 '없음' 근거를 내지 않고 측정 안 됨으로 알린다(L1-17)."""
    req = with_take_result(load(base_request(), "t-4"), issues=[])
    gaze = req["context"]["pitch"]["focus_take"]["results"]["take_result"]["areas"]["GAZE"]
    s2 = gaze["slides"][1]
    s2["measured_ratio"] = 0.0
    s2["metrics"] = {k: None for k in s2["metrics"]}
    ctx = make_ctx(req)
    args = {"take_id": "t-5", "section": SEG, "slide_number": 2}
    res, _ = run(ctx, "get_take_report", args)
    assert "GAZE" not in {r["area"] for r in res.data["no_segment_areas"]}
    assert res.data["unmeasured_areas"] == [{"area": "GAZE", "label": "시선"}]
    assert compose.none_partial(ctx, res.evidence_ids, areas=GAZE) == []
    assert compose.none_partial(ctx, res.evidence_ids) == []  # 모든 영역이 0곳이 아니다
    step = _read(ctx, "get_take_report", args, areas=GAZE)
    assert (step.result, step.signal) == ("측정 안 됨", None)
    # 측정된 다른 장 · Take 전체는 그대로 '없음'이다
    r3, _ = run(ctx, "get_take_report", {**args, "slide_number": 3})
    assert "GAZE" in {r["area"] for r in r3.data["no_segment_areas"]}
    rt, _ = run(ctx, "get_take_report", {"take_id": "t-5", "section": SEG})
    assert "GAZE" in {r["area"] for r in rt.data["no_segment_areas"]}


# ── 2. 거른 조건의 Take 0개는 'Take 없음'이 아니다 ──────────────────────────────


def test_filtered_empty_take_list_is_not_take_none() -> None:
    """범위 피치에 Take 가 있는데 거른 조건에 맞는 Take 만 없으면 `조건에 맞는 Take 없음`이고 기록 없음 신호(NO_RECORD)가 없다.
    범위 피치에 Take 가 정말 0개일 때만 `Take 없음` · NO_RECORD(TAKES)다(조건을 걸어 읽어도 같다)."""
    ctx = make_ctx()
    args = {"mode": "PRACTICE", "completed_fully": False}
    step = _read(ctx, "list_takes", args)
    assert (step.result, step.signal, step.facts) == ("조건에 맞는 Take 없음", None, [])
    assert step.done
    # 범위 피치의 Take 가 0개: 조건이 있어도 없어도 'Take 없음'
    for a in ({}, args):
        ctx0 = make_ctx(no_take_request())
        s0 = _read(ctx0, "list_takes", a)
        assert (s0.result, s0.signal, s0.facts) == ("Take 없음", "NO_RECORD", ["TAKES"])


# ── 3. 사실 표시(FACT_SHOWN)는 단계에 실제로 붙었을 때만 ───────────────────────


def _review_missing_turn(cfg: Any) -> tuple[list[dict[str, Any]], F.FakeLlm]:
    req = F.request(user_message="Take 5 리뷰에서 제일 큰 문제가 뭐였어?")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    review = ("get_take_report", {"take_id": "t-5", "section": "dimensions"})
    segments = ("get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    llm = F.FakeLlm(
        [
            F.plan(steps=["리뷰 보기", "답 쓰기"], step_tools=["get_take_report", "answer"]),
            F.tools(review, prefix="a"),
            F.tools(segments, prefix="b"),
            F.done(),
        ],
        answers=[F.answer([F.claim("RECORD", "Take 5 점수는 70점이에요.")])],
        fixes=[F.fix({0: None})],
    )
    log: list[dict[str, Any]] = []
    F.run_turn(req, llm, cfg=cfg, log=log)
    return log, llm


def test_fact_shown_only_when_the_fact_is_on_a_displayed_step() -> None:
    """`plan_progress`가 꺼져 단계에 사실을 붙이지 못하면 FACT_SHOWN 을 남기지 않는다. 신호는 그대로 열려 모델에게 가고,
    모델의 대응(ALT_TOOL)은 남는다. 켜져 있으면 전과 같이 FACT_SHOWN 이 먼저다."""
    log, llm = _review_missing_turn(load_config(plan_progress=False))
    assert decisions(log) == [("REVIEW_MISSING", "ALT_TOOL", "LLM", 2)]
    assert research_blocks(llm)[1]["signals"][0]["signal"] == "REVIEW_MISSING"
    log_on, _ = _review_missing_turn(load_config())
    assert decisions(log_on) == [
        ("REVIEW_MISSING", "FACT_SHOWN", "CODE", 1),
        ("REVIEW_MISSING", "ALT_TOOL", "LLM", 2),
    ]


# ── 4. 답 쓰기 모델이 실패하면 질문 대상의 근거로만 대신 문장 ─────────────────────


@pytest.mark.prefetch
def test_answer_failure_fallback_uses_only_question_target_evidence() -> None:
    """답 쓰기 호출이 실패해 코드가 확인한 값으로 부분 답을 그릴 때, 이번 실행이 읽은 근거를 모두 나열하지 않고 질문 대상(판단
    초안의 Take 4 · 3장 · 청중 시선)에 맞는 근거만 쓴다. 같은 장의 다른 지표 · 모델이 더 읽은 다른 Take 값은 넣지 않는다."""
    req = F.with_loaded(F.request(user_message="Take 4 3장 청중 시선 몇 %였어?"), "t-4")
    other = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 1})
    llm = F.FakeLlm(
        [F.plan(assessments=[slide_draft([4])]), F.tools(other), F.done()],
        answers=[LlmTimeout()],
    )
    _, _, data = F.run_turn(req, llm)
    assert data is not None and data["status"] == "PARTIAL"
    ev = {e["evidence_id"]: e for e in data["evidence"]}
    claims = data["message"]["claims"]
    assert [c["text"] for c in claims if c["kind"] == "RECORD"] == [
        "Take 4의 3장 청중 시선은 31%예요."
    ]
    cited = {
        (ev[e]["take_number"], ev[e]["slide_number"], ev[e]["metric"])
        for c in claims
        for e in c["evidence_ids"]
    }
    assert cited == {(4, 3, "audience_ratio")}


def test_answer_failure_without_question_targets_still_ends_partial_with_checked_values() -> None:
    """질문이 대상을 집지 않았으면(판단 초안 없음) 거를 기준이 없으므로, 답 쓰기가 실패해도 오류로 끝내지 않고 이번 실행이 확인한
    값으로 부분 답을 그린다(13-2 '앞서 확인한 것이 있으면 PARTIAL + 확인한 것만')."""
    req = F.request(user_message="요즘 연습 어때?")
    read = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})
    llm = F.FakeLlm([F.plan(), F.tools(read), F.done()], answers=[LlmTimeout()])
    _, _, data = F.run_turn(req, llm)
    assert data is not None and data["status"] == "PARTIAL"
    assert any(c["kind"] == "RECORD" for c in data["message"]["claims"])


def test_answer_failure_with_targets_but_no_matching_evidence_does_not_list_others() -> None:
    """질문이 대상(Take 4 · 3장 · 청중 시선)을 집었는데 조사가 다른 Take 의 값만 읽었으면, 답 쓰기가 실패해도 이번 실행이 읽은
    근거 전체(Take 5 1장 값)로 넓혀 그리지 않는다. 맞는 '없음' 근거도 없으므로 그린 문장 없이 기존 길(오류 줄)로 끝난다(13-2)."""
    req = F.request(user_message="Take 4 3장 청중 시선 몇 %였어?")
    other = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 1})
    llm = F.FakeLlm(
        [F.plan(assessments=[slide_draft([4])]), F.tools(other), F.done()],
        answers=[LlmTimeout()],
    )
    _, lines, data = F.run_turn(req, llm)
    assert lines[-1]["event"] == "error"
    assert not any(x.get("event") == "claim" for x in lines)
    assert data is not None and data["error"]["code"] == "INTERNAL_ERROR"


# ── 5. 반복으로 멈춘 조사의 코드 대신 답도 끝난 까닭을 남긴다 ──────────────────────


def test_code_fallback_after_repeat_stop_records_repeat_not_answered() -> None:
    """같은 요청 되풀이로 조사가 멈췄고(NO_PROGRESS) 모델 문장이 검사로 모두 빠져 코드가 '없음' 근거 문장을 그렸다. 요청은
    해소돼 DONE 이지만 진단의 끝난 까닭은 ANSWERED 가 아니라 REPEAT 다(모델 문장이 남은 답과 같은 규칙, 11-5 · L1-16)."""
    llm = F.FakeLlm(
        [
            NO_TAKE_PLAN,
            F.tools(("list_pitches", {}), prefix="a"),
            F.tools(("list_pitches", {}), prefix="b"),
        ],
        answers=[F.answer([F.claim("RECORD", "최근 발표는 없어요.")])],
        fixes=[F.fix({0: None})],
    )
    _, _, data = F.run_turn(no_take_request(), llm)
    assert data is not None
    assert [(t["tool"], t["outcome"]) for t in data["trace"]] == [
        ("list_pitches", "OK"),
        ("list_pitches", "CACHED"),
    ]
    assert data["status"] == "DONE"
    assert data["diagnostics"]["end"]["reason"] == "REPEAT"
    assert [c["text"] for c in data["message"]["claims"]][0] == NO_TAKE_SENTENCE


# ── 6. '같은 조건으로 다시 평가' 되살리기는 확실할 때만 ──────────────────────────

_FIRST = "Take 5는 대본 보고 한 발표였어. 그 조건으로 다시 평가해 줘"
_AGAIN = "같은 조건으로 한 번 더 다시 평가해 줘"
_PRIOR_NEGATED = "Take 5는 대본 들고 읽지 않았어. 그 조건으로 다시 평가해 줘"
_PRIOR_QUOTED = "팀장이 Take 5는 대본 보고 했다던데. 그 조건으로 다시 평가해 줘"


def _twice(first: str, now: str) -> dict[str, Any]:
    req = F.with_loaded(F.request(user_message=now), "t-5")
    req["history"]["messages"] = [
        {"role": "user", "text": first},
        {"role": "assistant", "text": "참고 결과를 만들었어요. 공식 리뷰는 그대로예요."},
    ]
    return req


@pytest.mark.prefetch
@pytest.mark.parametrize(
    ("first", "now"),
    [
        (_FIRST, "같은 조건으로 다시 평가하지 마"),  # 이번 말이 다시 평가를 부정
        (_FIRST, "팀장이 같은 조건으로 다시 평가하라던데"),  # 이번 말이 남의 말 인용
        (_PRIOR_NEGATED, _AGAIN),  # 앞 말의 대본 사용이 부정
        (_PRIOR_QUOTED, _AGAIN),  # 앞 말의 대본 사용이 인용
        (_FIRST, "같은 조건으로 Take 4도 다시 평가해 줘"),  # 새 대상(다른 Take)
        (_FIRST, "같은 조건에 시선 기준 30%로 바꿔서 다시 평가해 줘"),  # 새 조건
    ],
    ids=["now-negated", "now-quoted", "prior-negated", "prior-quoted", "new-take", "new-criteria"],
)
def test_same_condition_reassess_is_not_restored_when_unsure(first: str, now: str) -> None:
    """이번 말이 다시 평가를 부정 · 인용하거나 새 대상 · 조건을 말했거나, 앞 말의 대본 사용이 부정 · 인용이면 코드가 앞 조건으로
    다시 평가를 되살리지 않는다(확실하지 않으면 모델이 고른다)."""
    llm = F.FakeLlm([F.plan(assessments=[]), F.done(), F.done()], answers=[F.answer([])])
    _, _, data = F.run_turn(_twice(first, now), llm)
    assert data is not None
    assert all(a["type"] != "REASSESS" for a in data.get("actions") or [])
    assert not any(t["tool"] == "reassess" for t in data["trace"])
    assert [c.purpose for c in llm.calls].count("RESEARCH") >= 2  # 계획 뒤 모델이 고른다


@pytest.mark.prefetch
@pytest.mark.parametrize("now", [_AGAIN, "같은 조건으로 Take 5 한 번 더 다시 평가해 줘"])
def test_same_condition_reassess_still_restored_for_the_plain_repeat(now: str) -> None:
    """조건 · 대상을 새로 말하지 않은 되풀이 요청(앞 말과 같은 Take 를 집은 말 포함)은 그대로 되살린다."""
    llm = F.FakeLlm([F.plan(assessments=[])], answers=[F.answer([])])
    _, _, data = F.run_turn(_twice(_FIRST, now), llm)
    assert data is not None
    assert [(a["type"], a["args"]) for a in data["actions"]] == [
        ("REASSESS", {"take_id": "t-5", "change": {"script_used": True}})
    ]


# ── 7. 행동 동사 자체에 붙은 조건은 저장 요청이 아니다 ─────────────────────────────


@pytest.mark.parametrize(
    ("quote", "message", "want"),
    [
        ("목표 저장", "목표 저장하면 알려줘", "CONDITION"),
        ("목표 저장하면 알려줘", "목표 저장하면 알려줘", "CONDITION"),
        ("목표로 잡으면", "시선 30%를 목표로 잡으면 연습 시작해 줘", "CONDITION"),
        # 그대로인 것: 요청 · 동사 뒤의 '~면'(다음 요청의 순서) · 바람('~면 좋겠어'는 요청형 검사로)
        ("목표로 저장해 줘", "목표로 저장해 줘", None),
        (
            "목표로 저장해 주고",
            "목표로 저장해 주고, 저장되면 바로 연습하러 갈 수 있게 해 주세요",
            None,
        ),
        ("목표로 저장하면 좋겠어", "시선 30%를 목표로 저장하면 좋겠어", "NO_REQUEST_FORM"),
    ],
)
def test_condition_on_the_action_verb_is_not_a_save_request(
    quote: str, message: str, want: str | None
) -> None:
    assert act.authorize("SAVE_GOAL", quote, message, CFG) == want


@pytest.mark.parametrize(
    ("tail", "quote", "asks"),
    [
        ("그걸 목표로 저장하면 알려줘", "목표로 저장하면 알려줘", False),
        ("그걸 목표로 저장해 줘", "목표로 저장해 줘", True),
    ],
)
def test_condition_on_the_save_verb_does_not_become_a_save_action(
    tail: str, quote: str, asks: bool
) -> None:
    """모델이 "…목표로 저장하면 알려줘"에서 저장을 고르고 그 말을 허용 근거로 넣어도 저장 행동(승인 대기)을 내지 않는다.
    같은 자리의 요청("저장해 줘")은 그대로 확인 카드와 함께 저장 행동을 낸다."""
    msg = "Take 4보다 Take 5 3장 시선이 좋아졌어? " + tail
    save = (SAVE[0], {**SAVE[1], "authorized_by": quote})
    llm = F.FakeLlm(
        [F.plan(assessments=drafts()), F.tools(COMPARE), F.tools(save, prefix="sg"), F.done()],
        answers=[F.answer([]), F.answer([])],
    )
    _, _, data = F.run_turn(first_request(msg), llm)
    assert data is not None
    saved = [(a["type"], a["autonomy"]) for a in data.get("actions") or []]
    assert saved == ([("SAVE_GOAL", "ASK")] if asks else [])
    assert (data["status"] == "NEEDS_ACTION") is asks


# ── 8. 부정한 변화 주장은 말한 방향의 여집합 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("quote", "prev", "cur", "expected"),
    [
        ("지난번보다 3장 시선이 좋아지지 않았어", 0.28, 0.28, "SUPPORTED"),  # 같음
        ("지난번보다 3장 시선이 좋아지지 않았어", 0.33, 0.28, "SUPPORTED"),  # 반대(나빠짐)
        ("지난번보다 3장 시선이 좋아지지 않았어", 0.28, 0.33, "CONTRADICTED"),  # 말한 방향
        ("지난번보다 시선 비율이 안 늘었어", 0.28, 0.28, "SUPPORTED"),
        ("지난번보다 시선 비율이 안 늘었어", 0.28, 0.33, "CONTRADICTED"),
        ("지난번보다 시선이 나빠지지는 않았어", 0.28, 0.28, "SUPPORTED"),
        ("지난번보다 시선이 나빠지지는 않았어", 0.33, 0.28, "CONTRADICTED"),
        # 부정과 긍정이 섞이면 긍정한 방향(나빠짐)의 주장: 같음이면 틀림
        ("지난번보다 시선이 좋아지지 않고 나빠졌어", 0.28, 0.28, "CONTRADICTED"),
        ("지난번보다 시선이 좋아지지 않고 나빠졌어", 0.33, 0.28, "SUPPORTED"),
    ],
)
def test_negated_change_claim_is_the_complement_of_the_stated_direction(
    quote: str, prev: float, cur: float, expected: str
) -> None:
    for polarity in ("POSITIVE", "NEGATIVE"):  # 모델 극성과 상관없이 말 속 낱말로 판정한다
        cv = judge(
            change_draft(polarity, quote=quote),
            make_take(4, audience_ratio=prev),
            t5(audience_ratio=cur),
            focus="take-5",
        )
        assert cv.verdict == expected, (quote, polarity)


def test_negated_change_claim_key_differs_from_the_opposite_claim() -> None:
    """'좋아지지 않았어'와 '나빠졌어'는 판정이 다르므로(같음이면 맞음 · 틀림) 같은 주장 키가 아니다. 부정이 아닌 변화 주장 키는 그대로다."""
    sub = Subject(
        dimension="audience_gaze",
        metric="audience_ratio",
        take_ids=["take-4", "take-5"],
        relation="COMPARE",
    )

    def key(quote: str) -> str:
        return clm.claim_key(
            type="MEASURED",
            subject=sub,
            claim_op="CHANGE",
            polarity="NEGATIVE",
            value=None,
            value_unit=None,
            quote=quote,
            cfg=CFG,
        )

    assert key("지난번보다 시선이 좋아지지 않았어") != key("지난번보다 시선이 나빠졌어")
    assert key("지난번보다 시선이 나빠졌어") == key("지난번보다 시선이 떨어졌잖아")
    assert clm.negated_change("시선이 좋아지지 않았어", "audience_ratio", CFG) == "IMPROVED"
    assert clm.negated_change("시선이 나빠졌어", "audience_ratio", CFG) is None
