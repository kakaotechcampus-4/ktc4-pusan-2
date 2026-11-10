"""늘 같은 대응이 정답인 신호의 코드 대응 시험 (Issue #1194 4-G · 4-F, 기능 문서 6-1 7번 '상황 신호' · 11-5 결정 로그).

- 근거: 고치기 전 회귀 실행의 신호별 대응 분포(연구 모듈 reports/failure-analysis-1194.md 3절) — 리뷰 없음 · 기본 자료에 없음 ·
  기록 없음은 모델에게 준 선택지가 하나였고 모델의 대응도 거의 늘 같았다. 그래서 코드가 바로 처리하고 모델에게 묻지 않는다.
- 비교 불가 · 도구 실패는 질문에 따라 대응이 갈려 모델 몫이다(test_signal_routes.py · test_plan_progress_turn.py).
"""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.chat_agent import templates

from . import fakes as F
from .test_none_evidence import no_take_request, none_ev
from .test_plan_progress_turn import check_stream, decisions
from .test_signal_routes import research_blocks, tools_of


def test_review_missing_reads_the_coach_record_by_code() -> None:
    """리뷰가 없는 Take 의 리뷰 절을 읽으면 코드가 그 Take 의 코치 기록(문제 구간 · 장별 값)을 바로 읽고(ALT_TOOL:CODE),
    그 실행은 `리뷰 없어 코치 기록` 단계로 보인다. 모델에게는 신호를 묻지 않는다. 답에는 리뷰 없음 문장 · 다음 걸음이 붙는다."""
    req = F.request(user_message="Take 5 리뷰에서 제일 큰 문제가 뭐였어?")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    review = ("get_take_report", {"take_id": "t-5", "section": "dimensions"})
    llm = F.FakeLlm(
        [
            F.plan(steps=["리뷰 보기", "답 쓰기"], step_tools=["get_take_report", "answer"]),
            F.tools(review, prefix="a"),
            F.done(),
        ],
        answers=[F.answer([F.claim("RECORD", "Take 5 점수는 70점이에요.")])],
        fixes=[F.fix({0: None})],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    fd = check_stream(lines, data)
    assert tools_of(data) == [
        ("get_take_report", "EMPTY"),
        ("get_take_report", "OK"),
        ("get_take_report", "OK"),
    ]
    assert decisions(log) == [
        ("REVIEW_MISSING", "FACT_SHOWN", "CODE", 1),
        ("REVIEW_MISSING", "ALT_TOOL", "CODE", 1),
    ]
    assert all("signals" not in b for b in research_blocks(llm))
    steps = [p["step"] for p in data["plan"]]
    assert steps[0] == "리뷰 보기: Take 5 기록 → 리뷰 분석 전"
    assert steps[1].startswith("리뷰 없어 코치 기록: Take 5 기록 → ")
    assert fd.status == "DONE"
    texts = [c.text for c in fd.message.claims]  # type: ignore[union-attr]
    assert templates.SystemText.NO_REVIEW.value in texts
    assert texts[-1] == templates.SystemText.NEXT_AFTER_REVIEW.value


def test_no_record_is_closed_by_the_shown_fact() -> None:
    """범위 피치의 Take 가 0개(`Take 없음`)는 사실 표시로 닫는다: 모델에게 신호를 묻지 않고 대응 없음(NONE:LLM)도 남지 않는다."""

    def answer(r: Any) -> Any:
        return F.answer(
            [F.claim("RECORD", "'졸업 발표'에는 아직 연습한 Take가 없어요.", evidence_ids=[none_ev(r)])]
        )

    llm = F.FakeLlm(
        [
            F.plan(steps=["최근 발표 찾기", "답 쓰기"], step_tools=["list_pitches", "answer"]),
            F.tools(("list_pitches", {})),
            F.done(),
        ],
        answers=[answer],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(no_take_request(), llm, log=log)
    fd = check_stream(lines, data)
    assert decisions(log) == [("NO_RECORD", "FACT_SHOWN", "CODE", 1)]
    assert all("signals" not in b for b in research_blocks(llm))
    assert fd.status == "DONE"


def test_not_loaded_take_is_fetched_by_code() -> None:
    """모델이 기본 자료에 없는 Take 를 읽으려 하면(NOT_LOADED) 코드가 바로 가져오기를 낸다(NOT_LOADED:ACTION:CODE)."""
    req = F.request(user_message="Take 3 기록 보여 줘")
    req["context"]["loaded_takes"] = []
    read3 = ("get_take_report", {"take_id": "t-3", "section": "slides", "slide_number": 3})
    llm = F.FakeLlm([F.plan(), F.tools(read3, prefix="a")])
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log)
    fd = check_stream(lines, data)
    assert fd.status == "NEEDS_ACTION"
    assert [a.type for a in fd.actions] == ["FETCH_TAKE"]
    assert fd.actions[0].args["take_id"] == "t-3"
    assert ("NOT_LOADED", "ACTION", "CODE", None) in decisions(log)
    assert not any(d[2] == "LLM" for d in decisions(log))


# ── 검수 반영(Opus 5.5): 코드 대응은 덧붙이는 읽기라 한도 · 이미 읽은 기록을 넘지 않는다 ─────────────────────────


def _review_missing_run(cfg: Any = None, reads: list[Any] | None = None) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    req = F.request(user_message="Take 5 리뷰에서 제일 큰 문제가 뭐였어?")
    req["context"]["pitch"]["focus_take"]["results"]["review"] = None
    review = ("get_take_report", {"take_id": "t-5", "section": "dimensions"})
    llm = F.FakeLlm(
        [
            F.plan(steps=["리뷰 보기", "답 쓰기"], step_tools=["get_take_report", "answer"]),
            *(reads or []),
            F.tools(review, prefix="a"),
            F.done(),
        ],
        answers=[F.answer([F.claim("RECORD", "Take 5 점수는 70점이에요.")])],
        fixes=[F.fix({0: None})],
    )
    log: list[dict[str, Any]] = []
    _, lines, data = F.run_turn(req, llm, log=log, **({"cfg": cfg} if cfg is not None else {}))
    check_stream(lines, data)
    return llm, log, data


def test_code_reads_stay_inside_the_tool_limit() -> None:
    """도구 한도가 남지 않았으면 코드 읽기를 내지 않는다: 덧붙이는 읽기 때문에 도구 한도로 멈춰 PARTIAL 이 되지 않는다."""
    from pitch_coach_ai.features.chat_agent.config import load_config

    _, log, data = _review_missing_run(cfg=load_config(max_tool_calls=1))
    assert data["status"] == "DONE"
    assert ("REVIEW_MISSING", "NONE", "CODE", 1) in decisions(log)
    assert data["diagnostics"]["end"]["reason"] != "TOOL_CALLS"


def test_coach_record_already_read_is_not_queued_again() -> None:
    """모델이 이미 코치 기록(문제 구간 · 장별 값)을 읽었으면 리뷰 없음 대응으로 다시 내지 않는다(대응 없음 NONE:CODE)."""
    coach = F.tools(
        ("get_take_report", {"take_id": "t-5", "section": "problem_segments"}),
        ("get_take_report", {"take_id": "t-5", "section": "slides"}),
        prefix="c",
    )
    _, log, data = _review_missing_run(reads=[coach])
    effects = [d[:3] for d in decisions(log)]
    assert ("REVIEW_MISSING", "NONE", "CODE") in effects
    assert ("REVIEW_MISSING", "ALT_TOOL", "CODE") not in effects
    assert not any(p["step"].startswith("리뷰 없어 코치 기록") for p in data["plan"])
