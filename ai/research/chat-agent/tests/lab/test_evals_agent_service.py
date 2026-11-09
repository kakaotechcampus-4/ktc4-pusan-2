"""에이전트 서비스 세트(13-2) 평가 도구 시험 — 기억 fixtures · 스트림 끊김 장애 · 새 지표(14-1) · GENERAL 금지 위반 채점기.

실제 모델을 부르지 않는다(가짜 모델: 서비스 tests/unit/chat_agent/fakes.py). 시나리오 검사 경로가 실제 관찰값에 닿는지를 정해 둔 모델 응답으로 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent_lab import evals as ev
from chat_agent_lab import grading as gr
from chat_agent_lab.app import load_corpus
from chat_agent_lab.service_tests import fakes as F
from chat_agent_lab.service_tests import turn_tests

from .test_evals import BY_ID, _request, _save_goal_llm, _slide3_answer
from .test_fake_be import reassess_llm, script_drafts

COMPARE, roles = turn_tests.COMPARE, turn_tests.roles

SLIDE3_REPORT = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})


def _ctx(sid: str, llm: Any) -> ev._Ctx:
    return ev._Ctx(BY_ID[sid], llm, F.SECRET, ev.RunnerConfig(repeat=1))


def _record_llm(msg: str, *, answer: Any = None, fixes: list[Any] | None = None) -> F.FakeLlm:
    return F.FakeLlm(
        [F.plan(assessments=[_request(msg)]), F.tools(SLIDE3_REPORT), F.done()],
        answers=[answer or _slide3_answer],
        fixes=fixes or [F.fix({})],
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


def test_memory_off_drops_a_model_candidate_and_sends_no_profile() -> None:
    """설정이 꺼져 있으면 모델이 후보를 내도 끝 줄에 싣지 않고(코어) 저장도 하지 않는다(BE). 프로필도 보내지 않는다."""
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
    assert ctx.turns[0].cut_applied is True  # 실제로 끊었다(#920 리뷰)
    # 끊김은 그 차례에만: 다음 차례의 호출은 그대로다
    assert ctx.be.app.turn == ctx._app_turn


def test_cut_lines_closes_the_inner_response_even_if_never_read() -> None:
    closed: list[bool] = []

    class Inner:
        def __iter__(self) -> Any:
            return iter(['{"event": "claim"}', '{"event": "final"}'])

        def close(self) -> None:
            closed.append(True)

    state = {"done": False}
    ev._cut_lines(Inner(), 1, state).close()  # 읽지 않고 버려도 닫는다
    assert closed == [True] and state["done"] is False
    it = ev._cut_lines(Inner(), 1, state)
    assert next(it) == '{"event": "claim"}'
    with pytest.raises(ConnectionError):
        next(it)
    assert state["done"] is True and closed == [True, True]


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
    assert "3문장 넘음" in got[3] and "근거 · 자료 · 판단 · 행동에 이음" in got[3]
    assert gr.general_violations([_g("천천히 말해 보세요.", action_id="a1")], searched=True)
    # 단위로 시작하는 낱말 · 짧은 낱말이 든 일반 표현은 걸지 않는다
    ok = [
        _g("한 장면에 메시지 하나만 담아 보세요."),
        _g("발성 기관을 풀고 자신을 탓하지 마세요."),
        _g("한 개념씩 한 번에 하나만 말해 보세요."),
        _g("한 회사의 발표처럼 한 장소를 떠올려 보세요."),
    ]
    assert all(gr.general_violations([c], searched=True) == [] for c in ok)
    bad = [
        _g("한두 번 더 연습해 보세요."),
        _g("세 번쯤 쉬어 보세요."),
        _g("지난 기록을 떠올려 보세요."),
    ]
    assert len(gr.general_violations(bad, searched=True)) == 3
    # 자료를 찾지 않은 일반 조언은 문장마다 위반이다
    assert len(gr.general_violations([_g("천천히 말해 보세요.")], searched=False)) == 1


def test_general_violations_match_the_product_rule_for_idioms_and_causal_wording() -> None:
    # 제품 검사(checks.check_claims, kind GENERAL)도 같은 문장을 막는다(기능 문서 6-9: GENERAL 에는 한글 수사 · 숫자 표기와
    # 인과 표현이 없다). 채점기가 일부러 같게 센 것이므로 위반으로 고정한다(#920 리뷰)
    for text in (
        "한번 소리 내어 연습해 보세요.",
        "한 번 더 천천히 읽어 보세요.",
        "핵심 한 점에 집중해 보세요.",
        "긴장 때문에 말이 빨라질 수 있어요.",
    ):
        assert len(gr.general_violations([_g(text)], searched=True)) == 1, text


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


def _ev(req: Any, take_id: str) -> str:
    return F.find_evidence(
        req, lambda o: o.get("metric") == "audience_ratio" and o.get("take_id") == take_id
    )


def _record_then_save_llm() -> F.FakeLlm:
    """기록 질문 + 목표 저장: 승인 대기 전에 기록 문장을 내고, 승인 뒤 이어 부른 답은 비운다(행동 결과 문장은 코드)."""
    msg = BY_ID["svc-stream-resume"].turns[0].message or ""
    ask, save = msg.split(", ")
    args = {
        "metric": "audience_ratio",
        "slide_number": 3,
        "base_take_id": "t-5",
        "authorized_by": "목표로 잡아 줘",
        "reason": "3장 시선",
    }
    return F.FakeLlm(
        [
            F.plan(assessments=[_request(ask), _request(save)]),
            F.tools(SLIDE3_REPORT),
            F.tools(("save_goal", args), prefix="sg"),
            F.done(),
        ],
        answers=[_slide3_answer, F.answer([])],
        fixes=[F.fix({})],
    )


def _two_takes_llm() -> F.FakeLlm:
    """Take 4 · Take 5 두 문장. Take 4 는 읽기 행동(fetch_take)으로 가져와 비교한다. 장애가 앞 문장(Take 4)의 31% 를 35% 로 바꾸면
    그 문장과 뒤 문장을 보류했다가 고쳐 원래 순서로 낸다."""

    msg = BY_ID["svc-stream-held-sentence"].turns[0].message or ""

    def answer(r: Any) -> Any:
        ro = roles(r)
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "Take 4의 3장 청중 시선 비율은 31%예요.",
                    evidence_ids=[ro["previous"]],
                ),
                F.claim(
                    "RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[ro["current"]]
                ),
            ]
        )

    return F.FakeLlm(
        [
            F.plan(assessments=[_request(msg.rstrip("?"))]),
            F.tools(("fetch_take", {"take_id": "t-4"}), prefix="ft"),
            F.tools(COMPARE),
            F.done(),
        ],
        answers=[answer],
        fixes=[F.fix({0: "Take 4의 3장 청중 시선 비율은 31%예요."})],
    )


def _talk_llm(kind: str) -> F.FakeLlm:
    return F.FakeLlm([F.plan(route="SMALL_TALK", steps=[], talk_kind=kind)])


def _scope_llm(reason: str) -> F.FakeLlm:
    return F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위 확인"], scope_reason=reason)])


SCRIPTED = {
    "svc-reject-save": _save_goal_llm,
    "svc-stream-resume": lambda: _record_then_save_llm(),
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


def _msg(sid: str) -> str:
    return BY_ID[sid].turns[0].message or ""


def _guide_llm() -> F.FakeLlm:
    """'긴장' 자료(g-07)를 찾아 첫 문장을 원문 그대로 인용한다."""
    quote = load_corpus().text_of("g-07").split(". ")[0] + "."  # type: ignore[union-attr]
    return F.FakeLlm(
        [
            F.plan(assessments=[_request(_msg("svc-general-guide-found"))]),
            F.tools(("search_guides", {"query": "긴장"})),
            F.done(),
        ],
        answers=[
            F.answer(
                [F.claim("GUIDE", f'자료에 따르면 "{quote}"', source_ids=["g-07"], quote=quote)]
            )
        ],
        fixes=[F.fix({})],
    )


def _verdict_llm() -> F.FakeLlm:
    """'Take 5에서 3장 시선 잘 봤지?' 를 전체 평가 주장(MEASURED)으로 나눈다. 판정은 코드가 기록으로 한다."""
    d = F.draft(
        "Take 5에서 3장 시선 잘 봤지",
        claim_op="OVERALL",
        dimension="audience_gaze",
        polarity="POSITIVE",
        take_numbers=[5],
    )
    return F.FakeLlm(
        [F.plan(assessments=[d]), F.done()],
        answers=[F.answer([F.claim("ASSESSMENT", "", assessment_index=0)])],
        fixes=[F.fix({})],
    )


def _script_drafts() -> list[dict[str, Any]]:
    """시나리오 말('Take 5는 대본 보고 한 발표였어. 그 조건으로 다시 평가해 줘')의 절을 그대로 덮는 판단 초안."""
    d = script_drafts()
    d[0] = {**d[0], "quote": "Take 5는 대본 보고 한 발표였어"}
    d[0]["subject"] = {**d[0]["subject"], "take_numbers": [5]}
    return d


MORE = {
    "svc-approve-reassess": lambda: reassess_llm(
        {"script_used": True}, _script_drafts(), F.answer([])
    ),
    "svc-expire-reassess": lambda: reassess_llm(
        {"script_used": True}, _script_drafts(), F.answer([])
    ),
    "svc-memory-not-inferred": lambda: _record_llm(_msg("svc-memory-not-inferred")),
    "svc-memory-no-memory-chat": lambda: _record_llm(_msg("svc-memory-no-memory-chat")),
    "svc-general-record-bypass": lambda: _record_llm(_msg("svc-general-record-bypass")),
    "svc-stream-held-sentence": lambda: _two_takes_llm(),
    "svc-general-guide-found": _guide_llm,
    "svc-general-number-lure": lambda: _general_llm("svc-general-number-lure"),
    "svc-memory-same-verdict": _verdict_llm,
    # 가벼운 말(P-0008 5-1): 계획 한 번 + 코드 고정 문장
    "svc-talk-greeting": lambda: _talk_llm("GREETING"),
    "svc-talk-thanks": lambda: _talk_llm("THANKS"),
    "svc-talk-capability": lambda: _talk_llm("CAPABILITY"),
    "svc-talk-practice": lambda: _talk_llm("PRACTICE_INTENT"),
    "svc-talk-farewell": lambda: _talk_llm("FAREWELL"),
    "svc-talk-mixed-record": lambda: _record_llm(_msg("svc-talk-mixed-record")),
    "svc-talk-greeting-oos": lambda: _scope_llm("OTHER"),
}


@pytest.mark.parametrize("sid", sorted(MORE))
def test_more_agent_service_scenarios_pass_with_a_scripted_model(sid: str) -> None:
    sc = BY_ID[sid]
    ctx = _ctx(sid, MORE[sid]())
    for t in sc.turns:
        ctx.do_turn(t)
    ok, results, forb = ev.grade(ctx)
    assert ok and forb == [], _failed(results)


def test_every_agent_service_scenario_has_a_scripted_run() -> None:
    svc = {s.id for s in BY_ID.values() if s.set == "agent_service"}
    covered = (
        set(SCRIPTED)
        | set(MORE)
        | {
            "svc-memory-explicit",
            "svc-memory-off",
            "svc-stream-cut",
            "svc-new-message-cancels",
        }
    )
    assert svc == covered


def test_general_violation_from_a_real_run_reaches_the_metric() -> None:
    """제품 검사는 통과하지만 채점기(따로 짠 목록)가 잡는 일반 조언이 실행 결과 · 지표까지 이어진다."""
    sid = "svc-general-no-guide"
    llm = _general_llm(sid)
    llm.queues["ANSWER"] = [
        F.answer([F.claim("GENERAL", "천천히 시작했던 첫 문장을 떠올려 보세요.")], route="NO_GUIDE")
    ]
    r = ev.run_one(BY_ID[sid], 1, llm, F.SECRET, ev.RunnerConfig(repeat=1))
    assert r.general_violations and "행동 · 성공 주장" in r.general_violations[0]
    rep = ev.summarize([r], None, None, repeat=1)
    assert rep["units"]["all"]["general_violations"] == 1
    assert rep["targets"]["general_violations"]["met"] is False


def test_memory_leak_counts_a_run_whose_memory_checks_fail() -> None:
    (r,) = ev.run_suite(
        [BY_ID["svc-memory-same-verdict"]],
        llm_factory=lambda sc, rep: F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위"])]),
        secret=F.SECRET,
        cfg=ev.RunnerConfig(repeat=1),
    )
    assert r.metric_checks == {"memory": False}
    u = ev.summarize([r], None, None, repeat=1)["units"]["all"]
    assert (u["memory_leak"], u["memory_runs"]) == (1, 1)


def test_unapproved_prefixes_match_the_forbidden_event_texts() -> None:
    import inspect

    # 문구는 같은 상수로 만들고 세는 쪽도 같은 상수를 쓴다: 한쪽만 바꿔 지표가 0이 되지 않는다(#920 리뷰)
    consts = (
        ev.ASK_NO_DECISION,
        ev.ASK_NOT_APPROVED,
        ev.ASK_NO_STORED_HASH,
        ev.ASK_HASH_DIFFERS,
        ev.ASK_NO_RESULT_HASH,
    )
    assert consts == ev.UNAPPROVED_PREFIXES
    src = inspect.getsource(ev.forbidden_events)
    for name in (
        "ASK_NO_DECISION",
        "ASK_NOT_APPROVED",
        "ASK_NO_STORED_HASH",
        "ASK_HASH_DIFFERS",
        "ASK_NO_RESULT_HASH",
    ):
        assert "{" + name + "}" in src, name
    # 상수 밖에 문구를 따로 적지 않았다
    for p in ev.UNAPPROVED_PREFIXES:
        assert f'f"{p}' not in src and f'"{p}' not in src, p


def test_stream_mismatch_target_fails_when_a_turn_mismatches() -> None:
    (r,) = ev.run_suite(
        [BY_ID["refuse-out-of-scope"]],
        llm_factory=lambda sc, rep: F.FakeLlm([F.plan(route="OUT_OF_SCOPE", steps=["범위"])]),
        secret=F.SECRET,
        cfg=ev.RunnerConfig(repeat=1),
    )
    r.turns = [{**r.turns[0], "stream_ok": False}]
    assert ev.summarize([r], None, None, repeat=1)["targets"]["stream_mismatch"]["met"] is False


# ── 제공자 장애 멈춤 (4회차 교훈) ─────────────────────────────────────────


class _DownLlm:
    """제공자 장애 대역: 모든 호출이 사용량 없이 403 으로 실패한다."""

    model_name = None

    def invoke(self, req: Any, before_attempt: Any) -> Any:
        from pitch_coach_ai.features.chat_agent.llm_types import LlmCallFailed, Usage

        before_attempt(1)
        raise LlmCallFailed(attempts=1, usage=[Usage()], cause="PermissionDeniedError:403")


def test_provider_outage_stops_new_runs_and_warns_in_the_report(tmp_path: Any) -> None:
    sc = BY_ID["ana-slide3-gaze"]
    cfg = ev.RunnerConfig(repeat=6, stop_after_provider_failures=3)
    results = ev.run_suite([sc], llm_factory=lambda s, r: _DownLlm(), secret=F.SECRET, cfg=cfg)
    calls = [c for r in results for c in r.calls if not c["synthetic"]]
    assert calls and all(ev.provider_failure(c) for c in calls)
    assert {c["cause"] for c in calls} == {"PermissionDeniedError:403"}
    aborted = [r for r in results if (r.error or "").startswith(ev.PROVIDER_ABORT)]
    assert aborted and len(aborted) < len(results)  # 연속 3번 뒤 남은 실행은 돌리지 않는다
    u = ev.summarize(results, None, None, repeat=6)["units"]["all"]
    assert u["provider_failed_calls"] == len(calls) and u["provider_aborted_runs"] == len(aborted)
    assert u["provider_failure_causes"] == {"PermissionDeniedError:403": len(calls)}
    rep = {
        "info": {
            "started_at": "x",
            "baseline": False,
            "repeat": 6,
            "followups": False,
            "feature_version": "f",
            "prompt_version": "p",
            "corpus_version": "c",
            "model": None,
            "bundle": "OPENAI",
            "scenario_ids": [sc.id],
            "scenarios_sha256": "h",
        },
        **ev.summarize(results, None, None, repeat=6),
    }
    assert any("제공자 쪽 실패 호출" in ln for ln in ev._provider_warning(rep))


def test_successful_call_resets_the_provider_failure_streak() -> None:
    ok = {"ok": True, "synthetic": False, "error": None, "input_tokens": 10}
    bad = {"ok": False, "synthetic": False, "error": "LLM_ERROR", "input_tokens": None}
    timeout = {"ok": False, "synthetic": False, "error": "LLM_TIMEOUT", "input_tokens": None}
    injected = {"ok": False, "synthetic": True, "error": "LLM_ERROR", "input_tokens": None}
    assert ev.provider_failure(bad) and not ev.provider_failure(ok)
    assert not ev.provider_failure(timeout) and not ev.provider_failure(injected)


# ── 목적별 추론 강도 · 장애 실행만 다시 돌리기 ───────────────────────────


def test_reasoning_per_purpose_parses_and_falls_back_to_the_role_default() -> None:
    import dataclasses as dc

    from pitch_coach_ai.common.llm import tool_role

    efforts = ev.parse_reasoning(["answer=Medium", "FIX=high"])
    assert efforts == (("ANSWER", "medium"), ("FIX", "high"))
    role = dc.replace(tool_role(), purpose_efforts=efforts)
    assert (role.effort("ANSWER"), role.effort("FIX"), role.effort("RESEARCH")) == (
        "medium",
        "high",
        tool_role().reasoning_effort,
    )
    with pytest.raises(ValueError):
        ev.parse_reasoning(["TOOLS=high"])
    with pytest.raises(ValueError):
        ev.parse_reasoning(["ANSWER=max"])


def test_retry_from_keeps_clean_runs_and_redoes_only_outage_runs(tmp_path: Any) -> None:
    import argparse

    sc = BY_ID["ana-slide3-gaze"]
    down = ev.run_suite(
        [sc],
        llm_factory=lambda s, r: _DownLlm(),
        secret=F.SECRET,
        cfg=ev.RunnerConfig(repeat=2, stop_after_provider_failures=1),
    )
    from .test_evals import _slide3_llm

    (ok,) = ev.run_suite(
        [sc], llm_factory=lambda s, r: _slide3_llm(), secret=F.SECRET, cfg=ev.RunnerConfig(repeat=1)
    )
    ok = dataclasses_replace(ok, rep=1)
    runs = [down[0], ok]
    info = {
        **ev.freeze_info([sc], None, "OPENAI"),
        "started_at": "x",
        "set": "dev",
        "repeat": 2,
        "baseline": False,
        "followups": False,
        "fake": False,
        "reasoning": {"default": "low"},
    }

    def plan(rs: list[Any], inf: dict[str, Any], model: str | None = None, **kw: Any) -> Any:
        ev.write_report(tmp_path, rs, ev.summarize(rs, None, None, repeat=2), inf, None)
        a = argparse.Namespace(
            **{
                "set": "dev",
                "repeat": 2,
                "baseline": False,
                "followups": False,
                "bundle": "OPENAI",
                "fake": False,
                **kw,
            }
        )
        return ev.retry_plan(tmp_path, [sc], a, (), model=model)

    kept, redo = plan(runs, info)
    assert [r.rep for r in kept] == [1] and [(s.id, rep) for s, rep in redo] == [(sc.id, 0)]
    # 채점 코드(grading · scenario · judge)가 다르면 합치지 않고, 실행기(evals.py)만 다르면 합친다(#938 리뷰)
    gs = info["grader_sha256"]
    for name in ("grading.py", "scenario.py", "judge.py"):
        with pytest.raises(ValueError, match=name):
            plan(runs, {**info, "grader_sha256": {**gs, name: "다른 해시"}})
    kept, _ = plan(runs, {**info, "grader_sha256": {**gs, "evals.py": "다른 해시"}})
    assert [r.rep for r in kept] == [1]
    with pytest.raises(ValueError, match="grader_sha256"):
        plan(runs, {k: v for k, v in info.items() if k != "grader_sha256"})
    # 모델: 이번 모델을 알면 앞 결과의 모델과 같아야 합친다. 앞 결과에 모델이 없으면(옛 결과) 알 수 없어 합치지 않는다.
    # 이번 모델을 모르면(시험 호출) 비교하지 않는다
    with_model = {**info, "model": "m-a"}
    assert plan(runs, with_model, model="m-a")[0]
    assert plan(runs, with_model)[0]
    with pytest.raises(ValueError, match="모델"):
        plan(runs, {**info, "model": None}, model="m-b")
    with pytest.raises(ValueError, match="모델"):
        plan(runs, with_model, model="m-b")
    # 추론 강도가 없는 옛 결과는 그 결과의 역할 기본 강도로 읽는다(고정 low 가 아니다)
    old = {k: v for k, v in info.items() if k != "reasoning"}
    old_role = {**info["role"], "reasoning_effort": "high"}
    with pytest.raises(ValueError, match="reasoning"):
        plan(runs, {**old, "role": old_role})  # 지금 기본(low)과 다르다
    assert plan(runs, {**old, "role": {**info["role"], "reasoning_effort": "low"}})[0]
    assert plan(runs, {k: v for k, v in old.items() if k != "role"})[0]  # 역할도 없으면 low
    # 설정 · 판 · 묶음 · 가짜 여부가 다르면 합치지 않는다
    for kw, inf in (
        ({"repeat": 3}, info),
        ({"bundle": "OPENAI_SOL"}, info),
        ({"fake": True}, info),
        ({}, {**info, "corpus_version": "guides-0"}),
        ({}, {**info, "scenarios_sha256": "다른 해시"}),
    ):
        with pytest.raises(ValueError):
            plan(runs, inf, **kw)
    # 같은 (시나리오, 반복)이 두 번이거나 반복 범위 밖이면 합치지 않는다
    with pytest.raises(ValueError, match="두 번"):
        plan([down[0], ok, ok], info)
    with pytest.raises(ValueError, match="설정 밖"):
        plan([down[0], ok, dataclasses_replace(ok, rep=2)], info)
    # 앞 결과에서 빠진 실행은 다시 돌 목록에 든다
    kept, redo = plan([down[0]], info)
    assert kept == [] and [(s.id, rep) for s, rep in redo] == [(sc.id, 0), (sc.id, 1)]
    kept, redo = plan(runs, info)
    again = ev.run_suite(
        [sc],
        llm_factory=lambda s, r: _slide3_llm(),
        secret=F.SECRET,
        cfg=ev.RunnerConfig(repeat=2),
        only=redo,
    )
    assert [r.rep for r in again] == [0] and again[0].passed


def dataclasses_replace(r: Any, **kw: Any) -> Any:
    import dataclasses as dc

    return dc.replace(r, **kw)


def test_provider_streak_counts_calls_in_completion_order_across_runs() -> None:
    # 실행 A 의 실패 2번 → 실행 B 의 성공 1번 → 실행 A 의 실패 1번: 끝난 실행 단위가 아니라 호출이
    # 끝난 순서로 세므로 B 의 성공이 연속을 끊는다(실행이 끝나기를 기다렸다 세면 A 의 3번이 이어 보인다)
    import contextlib

    from pitch_coach_ai.features.chat_agent.llm_types import (
        LlmCallFailed,
        LlmRequest,
        LlmResult,
        Usage,
    )

    seen: list[Any] = []

    class _Down:
        model_name = None

        def invoke(self, req: Any, before_attempt: Any) -> Any:
            raise LlmCallFailed(attempts=1, usage=[Usage()], cause="connection")

    class _Up:
        model_name = None

        def invoke(self, req: Any, before_attempt: Any) -> Any:
            return LlmResult(message="ok", usage=Usage(input_tokens=5, output_tokens=1))

    a = ev.MeteredLlm(_Down(), ev.OffsetClock(), on_call=seen.append)
    b = ev.MeteredLlm(_Up(), ev.OffsetClock(), on_call=seen.append)
    req = LlmRequest(purpose="ANSWER", messages=[], deadline=1e12)
    for m in (a, a, b, a):
        with contextlib.suppress(LlmCallFailed):
            m.invoke(req, lambda _n: True)
    assert [ev.provider_failure(c) for c in seen] == [True, True, False, True]
    assert seen == [*a.records[:2], *b.records, a.records[2]]


# ── 자료 묶음 없이 일반 조언: '찾은 것' 기준이 코어와 같은가 (6-10 · L1-21, #977 리뷰) ─────────────


def test_search_without_a_corpus_counts_as_searched_so_general_advice_is_no_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자료 묶음 준비 실패(`search_guides` 의 `ERROR` · `CORPUS_UNAVAILABLE`)도 찾은 것이다. 그 뒤의 일반 조언
    문장을 'GENERAL 금지 위반'으로 세지 않는다(제품 규칙과 지표가 같아야 한다)."""
    import dataclasses

    real = ev.scn.build_world
    monkeypatch.setattr(
        ev.scn, "build_world", lambda sc, **kw: dataclasses.replace(real(sc, **kw), corpus=None)
    )
    llm = F.FakeLlm(
        [
            F.plan(assessments=[_request(_msg("svc-general-guide-found"))]),
            F.tools(("search_guides", {"query": "긴장"})),
            F.done(),
        ],
        answers=[F.answer([F.claim("GENERAL", "긴장될 때는 첫 문장을 천천히 말해 보세요.")])],
        fixes=[F.fix({})],
    )
    r = ev.run_one(BY_ID["svc-general-guide-found"], 0, llm, F.SECRET, ev.RunnerConfig(repeat=1))
    assert r.general_violations == []


def test_searched_reads_the_corpus_error_from_diagnostics_not_other_errors() -> None:
    """진행 줄 trace 에는 오류 코드가 없다: `ERROR` 는 진단 기록 `TOOL` 단계의 `error_code` 로 가린다."""
    from types import SimpleNamespace as NS

    def run(error_code: str | None) -> Any:
        progress = [
            {"event": "tool_done", "data": {"trace": {"tool": "search_guides", "outcome": "ERROR"}}}
        ]
        step = {"kind": "TOOL", "code": "search_guides", "detail": {"error_code": error_code}}
        return NS(progress=progress, calls=[NS(diagnostics={"steps": [step]})])

    assert ev._searched([run("CORPUS_UNAVAILABLE")]) is True
    assert ev._searched([run("TOOL_FAILED")]) is False
    assert ev._searched([run(None)]) is False
