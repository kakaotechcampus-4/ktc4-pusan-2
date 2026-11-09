"""PR 전 검증 지적 시험 — 고친 곳마다 가짜 모델(fakes.py)로 다시 생기지 않는지 본다(기능 문서 6-3 · 6-8 · 6-9 · 7-10 · 11-5).

- 문제 구간 0곳 근거: 장마다 근거 id 가 따로이고, 그 장의 값이 측정된 영역만 '없음'이다.
- Take 목록: 거른 조건에 맞는 Take 만 없으면 'Take 없음'(기록 없음 신호)이 아니다.
- 결정 로그: 사실 표시(FACT_SHOWN)는 그 사실이 화면 단계에 붙었을 때만 남긴다.
- 답 쓰기 실패의 대신 문장: 질문 대상의 근거로만 그린다(읽은 근거 전체를 나열하지 않는다).
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import compose
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.llm_types import LlmTimeout

from . import fakes as F
from .test_none_evidence import _read, no_take_request, with_take_result
from .test_plan_progress_turn import decisions
from .test_prefetch import slide_draft
from .test_signal_routes import research_blocks
from .test_tools import base_request, load, make_ctx, run

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
