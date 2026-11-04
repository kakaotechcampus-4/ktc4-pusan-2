"""에이전트 서비스 세트(13-2) 평가 도구 시험 — 기억 fixtures · 스트림 끊김 장애 · 새 지표(14-1) · GENERAL 금지 위반 채점기.

실제 모델을 부르지 않는다(가짜 모델 tests/unit/fakes.py). 시나리오 검사 경로가 실제 관찰값에 닿는지를 정해 둔 모델 응답으로 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent_lab import evals as ev
from chat_agent_lab import grading as gr
from tests.unit import fakes as F

from .test_evals import BY_ID, _request, _save_goal_llm, _slide3_answer

SLIDE3_REPORT = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})


def _ctx(sid: str, llm: Any) -> ev._Ctx:
    return ev._Ctx(BY_ID[sid], llm, F.SECRET, ev.RunnerConfig(repeat=1))


def _record_llm(msg: str, *, answer: Any = None, **kw: Any) -> F.FakeLlm:
    return F.FakeLlm(
        [F.plan(assessments=[_request(msg)]), F.tools(SLIDE3_REPORT), F.done()],
        answers=[answer or _slide3_answer],
        fixes=[F.fix({})],
        **kw,
    )


def _failed(results: list[gr.CheckResult]) -> list[Any]:
    return [r for r in results if not r.passed]


# ── 기억 fixtures (6-14) ────────────────────────────────────────────────────


def test_memory_fixtures_reach_the_fake_be_and_the_snapshot() -> None:
    ctx = _ctx("svc-memory-no-memory-chat", F.FakeLlm([]))
    assert ctx.conv.no_memory is True
    assert [(i["key"], i["value"]) for i in ctx.be.profile_items("u-3")] == [
        ("TONE", "아주 길고 자세하게")
    ]
    assert ctx.before["memory"] == {
        "enabled": True,
        "version": 0,
        "profile": [{"key": "TONE", "value": "아주 길고 자세하게"}],
    }
    off = _ctx("svc-memory-off", F.FakeLlm([]))
    assert off.be.memory_setting("u-3")["enabled"] is False


def test_explicit_memory_request_is_saved_and_the_scenario_checks_see_it() -> None:
    sc = BY_ID["svc-memory-explicit"]
    msg = sc.turns[0].message or ""

    def answer(r: Any) -> Any:
        res = _slide3_answer(r)
        res.parsed["profile_updates"] = [
            {"key": "TONE", "value": "짧게 설명", "quote": "앞으로는 짧게 설명해 줘, 기억해 줘"}
        ]
        return res

    ctx = _ctx(sc.id, _record_llm(msg, answer=answer))
    ctx.do_turn(sc.turns[0])
    ok, results, forb = ev.grade(ctx)
    assert ok and forb == [], _failed(results)
    assert "memory.profile" in ev.observations(ctx, 0)["db_diff"]["changed"]


def test_memory_off_scenario_passes_when_the_model_still_proposes_a_candidate() -> None:
    """설정이 꺼져 있으면 모델이 후보를 내도 BE 가 저장하지 않는다(시나리오 검사가 그것을 본다)."""
    sc = BY_ID["svc-memory-off"]
    msg = sc.turns[0].message or ""

    def answer(r: Any) -> Any:
        res = _slide3_answer(r)
        res.parsed["profile_updates"] = [
            {"key": "TONE", "value": "짧게 설명", "quote": "앞으로는 짧게 설명해 줘, 기억해 줘"}
        ]
        return res

    ctx = _ctx(sc.id, _record_llm(msg, answer=answer))
    ctx.do_turn(sc.turns[0])
    ok, results, _ = ev.grade(ctx)
    assert ok, _failed(results)
    # 기억이 꺼진 요청은 프로필을 싣지 않는다
    req = ctx.turns[0].runs[0].calls[0].request
    assert req["memory"]["enabled"] is False and req["memory"]["profile"] is None


# ── 스트림 끊김 장애 (L2-45) ───────────────────────────────────────────────


def test_stream_cut_fault_ends_partial_with_the_received_sentence() -> None:
    sc = BY_ID["svc-stream-cut"]
    msg = sc.turns[0].message or ""

    def two_sentences(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [
                F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[eid]),
                F.claim("RECORD", "3장은 이번 Take 에서 우선 개선할 장이에요.", evidence_ids=[eid]),
            ]
        )

    ctx = _ctx(sc.id, _record_llm(msg, answer=two_sentences))
    ctx.do_turn(sc.turns[0])
    run = ctx.turns[0].runs[0]
    assert run.status == "PARTIAL" and run.calls[0].error_code == "STREAM_CUT"
    ok, results, forb = ev.grade(ctx)
    assert ok and forb == [], _failed(results)
    assert ev.observations(ctx, 0)["trace"]["claim_lines"] == 1
    # 끊김은 그 차례에만: 다음 차례의 호출은 그대로다
    assert ctx.be.app.turn == ctx._app_turn


# ── 새 지표 (14-1) ─────────────────────────────────────────────────────────


def test_unit_metrics_count_unapproved_general_violations_and_memory_leak() -> None:
    (r,) = ev.run_suite(
        [BY_ID["refuse-out-of-scope"]],
        llm_factory=lambda sc, rep: F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위"])]),
        secret=F.SECRET,
        cfg=ev.RunnerConfig(repeat=1),
    )
    r.forbidden = ["승인 없는 ASK 행동 실행: SAVE_GOAL", "공식 결과 변경: official.review.t-5"]
    r.general_violations = ["3초 쉬세요: 숫자"]
    r.metric_checks["memory"] = False
    r.metric_checks["advice"] = True
    u = ev.summarize([r], None, None, repeat=1)["units"]["all"]
    assert (u["unapproved_exec"], u["forbidden"]) == (1, 2)
    assert u["general_violations"] == 1
    assert (u["memory_leak"], u["memory_runs"]) == (1, 1)
    assert (u["advice_guide_ratio"]["num"], u["advice_guide_ratio"]["den"]) == (1, 1)
    targets = ev.summarize([r], None, None, repeat=1)["targets"]
    assert targets["unapproved_exec"]["met"] is False
    assert targets["memory_leak"]["met"] is False


def test_agent_service_set_is_its_own_unit() -> None:
    assert ev.SETS["agent_service"] == (("agent_service",), ("dev",))
    assert "agent_service" in ev.SETS["all"][0]
    assert ev.UNITS["agent_service"](
        ev.RunResult(
            "x",
            "agent_service",
            "stream",
            "dev",
            "should",
            1,
            True,
            [],
            [],
            None,
            {},
            {},
            [],
            [],
            [],
        )
    )


# ── GENERAL 금지 위반 채점기 (14-1, 제품 검사와 따로) ─────────────────────


def _g(text: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "GENERAL", "text": text, "evidence_ids": [], "source_ids": [], **kw}


def test_general_violations_pass_plain_advice() -> None:
    claims = [
        {"kind": "SYSTEM", "text": "자료에서 확인한 내용은 아니고 일반적인 조언이에요."},
        _g("중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉬워요."),
        _g("말 속도를 조금 늦추고 문장 끝을 또렷하게 맺으면 좋아요."),
        {
            "kind": "RECORD",
            "text": "Take 5의 3장 청중 시선 비율은 28%예요.",
            "evidence_ids": ["e1"],
        },
    ]
    assert gr.general_violations(claims, searched=True) == []


def test_general_violations_count_each_bad_sentence() -> None:
    claims = [
        _g("3초쯤 쉬어 보세요."),
        _g("연구에 따르면 쉼이 중요해요."),
        _g("이번 발표는 시선이 강점이에요."),
        _g("좋은 습관이에요.", source_ids=["g-01"]),
    ]
    got = gr.general_violations(claims, searched=True)
    assert len(got) == 4
    assert "숫자" in got[0] and "출처" in got[1] and "기록 지시어" in got[2] and "판정" in got[2]
    assert "3문장 넘음" in got[3] and "근거 · 자료 · 판단에 이음" in got[3]
    # 자료를 찾지 않은 일반 조언은 문장마다 위반이다
    assert len(gr.general_violations([_g("천천히 말해 보세요.")], searched=False)) == 1


# ── 시나리오 검사가 관찰값에 닿는가(정해 둔 모델 응답) ──────────────────────


def _general_llm(sc_id: str) -> F.FakeLlm:
    msg = BY_ID[sc_id].turns[0].message or ""
    return F.FakeLlm(
        [
            F.plan(steps=["자료 찾기", "답 쓰기"], assessments=[_request(msg)]),
            F.tools(("search_guides", {"query": "홍차 맛있게 우리기"})),
            F.done(),
        ],
        answers=[
            F.answer(
                [
                    F.claim("GENERAL", "손은 몸 앞에 편하게 두고 강조할 때만 움직여 보세요."),
                    F.claim("GENERAL", "주머니에 넣거나 팔짱을 끼지 않으면 열린 인상을 줘요."),
                ],
                route="NO_GUIDE",
            )
        ],
        fixes=[F.fix({})],
    )


def _scope_llm(reason: str) -> F.FakeLlm:
    return F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위 확인"], scope_reason=reason)])


SCRIPTED = {
    "svc-reject-save": _save_goal_llm,
    "svc-stream-resume": _save_goal_llm,
    "svc-general-no-guide": lambda: _general_llm("svc-general-no-guide"),
    "svc-oos-content": lambda: _scope_llm("CONTENT_COACHING"),
    "svc-oos-qa": lambda: _scope_llm("QA_PRACTICE"),
    "svc-oos-plan": lambda: _scope_llm("PRACTICE_PLAN"),
}


@pytest.mark.parametrize("sid", sorted(SCRIPTED))
def test_agent_service_scenario_checks_pass_with_a_scripted_model(sid: str) -> None:
    sc = BY_ID[sid]
    ctx = _ctx(sid, SCRIPTED[sid]())
    for t in sc.turns:
        ctx.do_turn(t)
    ok, results, forb = ev.grade(ctx)
    assert ok and forb == [], _failed(results)
    assert (
        ev.run_one(sc, 1, SCRIPTED[sid](), F.SECRET, ev.RunnerConfig(repeat=1)).general_violations
        == []
    )


def test_new_message_scenario_cancels_the_first_wait() -> None:
    sc = BY_ID["svc-new-message-cancels"]
    save = _save_goal_llm()
    second = sc.turns[1].message or ""
    llm = F.FakeLlm(
        [
            *save.script[:2],  # 승인 대기로 멈춰 조사 끝(done) 응답은 쓰지 않는다
            F.plan(assessments=[_request(second)]),
            F.tools(SLIDE3_REPORT),
            F.done(),
        ],
        answers=[save.queues["ANSWER"][0], _slide3_answer],
        fixes=[F.fix({})],
    )
    ctx = _ctx(sc.id, llm)
    for t in sc.turns:
        ctx.do_turn(t)
    ok, results, forb = ev.grade(ctx)
    assert ok and forb == [], _failed(results)
