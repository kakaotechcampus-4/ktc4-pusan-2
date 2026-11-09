"""PR 전 검증 지적 시험 — 고친 곳마다 가짜 모델(fakes.py)로 다시 생기지 않는지 본다(기능 문서 6-3 · 6-8 · 6-9 · 7-10 · 11-5).

- 문제 구간 0곳 근거: 장마다 근거 id 가 따로이고, 그 장의 값이 측정된 영역만 '없음'이다.
- Take 목록: 거른 조건에 맞는 Take 만 없으면 'Take 없음'(기록 없음 신호)이 아니다.
"""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.chat_agent import compose

from .test_none_evidence import _read, no_take_request, with_take_result
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
