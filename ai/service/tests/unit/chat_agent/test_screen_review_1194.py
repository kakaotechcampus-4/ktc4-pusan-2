"""실제 화면에서 본 헷갈리는 두 차례 대화의 고침 시험 (Issue #1194 화면 검토, 기능 문서 6-1 7번 · 6-3 · 7-10).

- 앞 답이 기록으로 확인해 준 것을 사용자가 되받아 말하면('작게 말했다고 하는데 …') 확인할 주장으로 보지 않는다: '확인 불가'
  판정 첫 문장을 그리지 않는다(앞 답과 어긋나 보이지 않게).
- 같은 도구 · 같은 대상을 여러 번 실행해도 할 일 단계는 하나로 잇는다(자료 찾기 여러 번 · 같은 Take 기록의 두 절).
- 끝난 답(DONE)에는 실행하지 않은 단계를 남기지 않는다.
- 내부 말('기본 자료에 없음')과 어색한 문제 이름('목소리가 작음 구간')을 쓰지 않는다. 범위를 바꾼 문장은 무엇을 가져왔는지로 말한다.
"""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.chat_agent import claims as clm
from pitch_coach_ai.features.chat_agent import progress, templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG

from . import fakes as F
from .test_progress import _meta, _read
from .test_tools import make_ctx

PREV = "'abc' 기록을 가져왔어요.\nTake 2에서는 점검 때보다 1.4dB 작게 말했어요."


def test_restating_the_previous_answer_is_recognised() -> None:
    assert clm.restates_previous_answer("목소리를 작게 말했다고 하는데", PREV)
    assert clm.restates_previous_answer("작게 말했다고 하셨는데", PREV)
    # 남의 말(코치 · 친구)이나 앞 답에 없는 내용, 앞 답이 없을 때는 그대로 확인할 주장이다
    assert not clm.restates_previous_answer("코치가 빠르다고 하는데", PREV)
    assert not clm.restates_previous_answer("목소리를 작게 말했다고 하는데", None)
    assert not clm.restates_previous_answer("목소리가 작았어", PREV)


def test_restated_claim_gets_no_unverifiable_sentence() -> None:
    """둘째 질문 '목소리를 작게 말했다고 하는데 어떻게 하면 크게 말할 수 있을까요?': 앞 답을 되받은 절은 '확인 불가'로 그리지 않는다."""
    msg = "목소리를 작게 말했다고 하는데 어떻게 하면 크게 말할 수 있을까요?"
    req = F.request(user_message=msg)
    req["history"]["messages"] = [
        {
            "role": "user",
            "text": "최근 발표에서 제 목소리 크기가 어땠나요?",
            "truncated": False,
            "basis": None,
            "stale_reasons": [],
        },
        {"role": "assistant", "text": PREV, "truncated": False, "basis": None, "stale_reasons": []},
    ]
    draft = F.draft(
        "목소리를 작게 말했다고 하는데",
        claim_op="ISSUE",
        dimension="vocal_delivery",
        metric="voice_diff_db",
        polarity="NEGATIVE",
    )
    llm = F.FakeLlm(
        [F.plan(assessments=[draft]), F.done()],
        answers=[F.answer([F.claim("GENERAL", "숨을 고르고 첫 문장을 또렷하게 시작해 보세요.")])],
    )
    _, _, data = F.run_turn(req, llm)
    texts = [c["text"] for c in (data.get("message") or {}).get("claims") or []]
    assert not any("이 기록으로는 확인할 수 없어요" in t for t in texts)
    assert not any(t.startswith("말씀하신 '목소리를 작게 말했다고 하는데'") for t in texts)


def test_repeated_runs_of_the_same_tool_and_target_share_one_step() -> None:
    ctx = make_ctx()
    meta = _meta(("답 쓰기", "answer"))
    q1 = _read(ctx, "search_guides", {"query": "목소리 크게"})
    q2 = _read(ctx, "search_guides", {"query": "발성"})
    a = progress.attach(meta, q1, 1, max_steps=8)
    b = progress.attach(meta, q2, 2, max_steps=8)
    assert a == b and len(meta) == 2  # 자료 찾기 두 번이 한 단계
    r1 = _read(ctx, "get_take_report", {"take_id": "t-5", "section": "slides"})
    r2 = _read(ctx, "get_take_report", {"take_id": "t-5", "section": "problem_segments"})
    c = progress.attach(meta, r1, 3, max_steps=8)
    d = progress.attach(meta, r2, 4, max_steps=8)
    assert c == d and len(meta) == 3  # 같은 Take 기록의 두 절이 한 단계
    assert meta[c]["result"] == progress.merge_result(templates.PLAN_RESULT["CHECKED"], r2.result)


def test_merge_result_keeps_the_more_specific_result() -> None:
    chk, seg = templates.PLAN_RESULT["CHECKED"], templates.PLAN_RESULT["SEGMENTS"]
    assert progress.merge_result(chk, seg) == seg
    assert progress.merge_result(seg, chk) == seg
    assert (
        progress.merge_result(templates.PLAN_RESULT["NO_GUIDES"], templates.PLAN_RESULT["GUIDES"])
        == (templates.PLAN_RESULT["GUIDES"])
    )
    assert progress.merge_result("A", "B") == "A · B"


def test_user_facing_words() -> None:
    assert templates.PLAN_RESULT["NOT_LOADED"] == "아직 불러오지 않음"
    assert clm.ISSUE_LABEL["VOLUME_LOW"] == "작은 목소리"
    assert templates.ACTION_SUCCESS["FETCH_PITCH"] == "'{title}' 기록을 가져왔어요."
    assert "범위" not in templates.ACTION_SUCCESS["FETCH_PITCH"]


def test_finished_plan_drops_steps_that_never_ran() -> None:
    ctx: Any = make_ctx()
    meta = _meta(("최근 발표 기록 확인", "list_pitches"), ("답 쓰기", "answer"))
    plan = progress.finish_plan(meta, ctx.registry, CFG, "DONE", answered=True)
    assert [p["step"] for p in plan] == ["답 쓰기"]


def test_metric_only_issue_claim_is_judged_by_the_problem_segment() -> None:
    """'대본 응시 비율이 너무 높은거같아'(문제 낱말 없음): 대본 의존 차원의 문제는 대본 응시 하나뿐이라 그 문제 구간으로 판정한다
    ('확인 불가'가 아니다). 방향이 둘인 말 속도는 낱말이 있어야 한다."""
    from .test_claims import NEG, mk, run, t5

    d = mk(
        "대본 응시 비율이 너무 높은거같아",
        op="ISSUE",
        dim="script_independence",
        metric="script_ratio",
        polarity=NEG,
    )
    with_seg = run(d, t5(issues=[("GAZE_ON_SCRIPT", 3, True)]), focus="take-5")
    assert with_seg.verdict == "SUPPORTED" and "대본 응시" in with_seg.evidence_phrase
    assert run(d, t5(), focus="take-5").verdict == "CONTRADICTED"  # 대본 응시 구간이 없으면 반박
    pace = mk(
        "말 속도가 너무 이상한 거 같아", op="ISSUE", dim="pacing_rhythm", metric="cpm", polarity=NEG
    )
    assert (
        run(pace, t5(issues=[("PACE_FAST", 2, True)]), focus="take-5").reason_code
        == "NO_ISSUE_WORD"
    )


# ── 읽듯 보는 검토(보정 전 보조 검토)로 찾은 것 ─────────────────────────────────


def test_question_form_is_not_answered_with_an_unverifiable_head() -> None:
    """물음 꼴의 말('나아졌는지' · '올랐어?')은 판정할 수 없어도 '이 기록으로는 확인할 수 없어요'로 되받지 않는다(답이 까닭을 말한다)."""
    assert clm._asks("Take 7이 그 전 Take 6보다 시선 처리가 나아졌는지")
    assert clm._asks("Take 4 점수랑 비교해서 이번 Take 점수가 올랐어?")
    assert clm._asks("대본을 쓴 조건으로 다시 평가해 볼 수 있을까요")
    assert not clm._asks("Take 5는 군더더기 말이 너무 많았어")
    assert not clm._asks("대본 응시 비율이 너무 높은거같아")
    # 확인을 구하는 꼴은 주장이다(판정 문장을 쓴다)
    assert not clm._asks("Take 5 목소리는 충분히 컸지?")
    assert not clm._asks("시선 잘 봤죠?")
    assert not clm._asks("목소리 크게 잘 냈잖아요")


def test_unknown_issue_type_shows_the_area_name_not_the_internal_code() -> None:
    from pitch_coach_ai.features.chat_agent.tools.impl import issue_label

    assert issue_label("FILLER_FREQUENT", "FILLER") == "잦은 군더더기"
    assert issue_label("FILLER_EXCESS", "FILLER") == "군더더기 문제"  # 계약 밖 종류
    assert issue_label("SOMETHING_NEW", None) == "문제 구간"
