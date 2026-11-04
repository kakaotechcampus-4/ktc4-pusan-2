"""에이전트 서비스 세트(13-2) 평가 도구 시험 — 기억 fixtures · 스트림 끊김 장애 · 새 지표(14-1) · GENERAL 금지 위반 채점기.

실제 모델을 부르지 않는다(가짜 모델 tests/unit/fakes.py). 시나리오 검사 경로가 실제 관찰값에 닿는지를 정해 둔 모델 응답으로 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from chat_agent_lab import evals as ev
from chat_agent_lab import grading as gr
from chat_agent_lab.app import load_corpus
from tests.unit import fakes as F
from tests.unit.test_turn import COMPARE, roles

from .test_evals import BY_ID, _request, _save_goal_llm, _slide3_answer
from .test_fake_be import reassess_llm, script_drafts

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
        answers=[F.answer([F.claim("GUIDE", f'자료에 따르면 "{quote}"', source_ids=["g-07"])])],
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

    src = inspect.getsource(ev.forbidden_events)
    for p in ev.UNAPPROVED_PREFIXES:
        assert p in src, p


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
        from chat_agent.llm_types import LlmCallFailed, Usage

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
