"""요청 해소 검사 시험 (Issue #1194 4-B, 기능 문서 6-1 7번 '끝 상태').

- 계획 때 판단 초안이 집은 측정 항목(측정 주장 · 정보 질문)마다 답에 해소(그 항목 근거를 이은 기록 · 판단 문장, 확인한 '없음',
  비교할 수 없는 까닭, 측정 안 됨)가 있어야 `DONE`이다. 정상으로 끝났다고 늘 `DONE`이 아니다(부분 답의 완료 오표시를 막는다).
- 모델이 읽은 항목을 빠뜨리면 코드가 그 근거로 기록 문장을 채워 `DONE`. 읽지도 못한 항목이 남으면 남은 일 단계를 더해 `PARTIAL`.
"""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.config import load_config

from . import fakes as F

MSG = "Take 5 3장 청중 시선은 몇 %였고 군더더기는 몇 번이었어?"
GAZE = F.draft(
    "Take 5 3장 청중 시선은 몇 %였고",
    type_="REQUEST",
    claim_op=None,
    metric="audience_ratio",
    slide_number=3,
    take_numbers=[5],
    polarity="NEUTRAL",
)
FILLER = F.draft(
    "군더더기는 몇 번이었어?",
    type_="REQUEST",
    claim_op=None,
    metric="filler_count",
    take_numbers=[5],
    polarity="NEUTRAL",
)
SLIDE3 = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})


def _gaze_only(r: Any) -> Any:
    eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio" and o.get("display") == "28%")
    return F.answer([F.claim("RECORD", "Take 5 3장 청중 시선은 28%예요.", evidence_ids=[eid])])


def _run(research: list[Any], cfg: Any = None) -> dict[str, Any]:
    req = F.with_loaded(F.request(user_message=MSG), "t-5")
    llm = F.FakeLlm([F.plan(assessments=[GAZE, FILLER]), *research, F.done()], answers=[_gaze_only])
    _, _, data = F.run_turn(req, llm, **({"cfg": cfg} if cfg is not None else {}))
    return data


def test_unread_item_leaves_partial_with_a_todo_step() -> None:
    """군더더기는 읽지도 않았다: 끊기지 않았어도 DONE 이 아니라 PARTIAL 이고, 남은 일 단계가 보인다."""
    data = _run([F.tools(SLIDE3, prefix="a")])
    assert data["status"] == "PARTIAL"
    steps = data["plan"]
    assert any(
        p["step"] == templates.PLAN_UNRESOLVED and p["status"] == "TODO" for p in steps
    )
    assert data["diagnostics"]["end"]["reason"] == "OTHER"


def test_read_but_unsaid_item_is_filled_by_code_and_done() -> None:
    """군더더기를 읽었는데 모델이 빠뜨렸다: 그 근거로 코드 기록 문장을 채우고 DONE 이다."""
    totals = ("get_take_report", {"take_id": "t-5", "section": "slides"})
    data = _run([F.tools(SLIDE3, totals, prefix="a")])
    assert data["status"] == "DONE"
    texts = [c["text"] for c in data["message"]["claims"]]
    assert any("군더더기" in t for t in texts)


def test_switch_off_keeps_the_previous_end_status() -> None:
    data = _run([F.tools(SLIDE3, prefix="a")], cfg=load_config(request_resolution=False))
    assert data["status"] == "DONE"


def test_unresolved_step_is_not_added_past_the_step_limit() -> None:
    """할 일 목록이 상한(8단계)으로 차 있으면 '물은 항목 확인' 단계를 더하지 않는다(끝 상태 판단과는 따로)."""
    from pitch_coach_ai.features.chat_agent import progress

    meta = [progress._meta(f"단계 {i}", None) for i in range(7)] + [progress._meta("답 쓰기", "answer")]
    for m in meta[:7]:
        m["done"] = True
    assert progress.add_unresolved(meta, 8) is None and len(meta) == 8
    assert progress.add_unresolved(meta[:4] + meta[7:], 8) == 4


def test_unrelated_none_evidence_does_not_resolve_an_item() -> None:
    """다른 항목의 '없음' 근거(대화 목표 없음)를 이은 문장이 있어도, 읽지 못한 군더더기 항목은 해소되지 않는다(PARTIAL)."""
    msg = MSG + " 대화 목표는 뭐야?"
    req = F.with_loaded(F.request(user_message=msg), "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[GAZE, FILLER]), F.tools(SLIDE3, ("get_goals", {}), prefix="a"), F.done()],
        answers=[_gaze_only],
    )
    _, _, data = F.run_turn(req, llm)
    assert data["status"] == "PARTIAL"


def test_advice_question_is_not_counted_as_a_value_question() -> None:
    """'어떻게 하면 좋아?'는 조언을 묻는 말이라, 측정 지표를 집었어도 값을 읽지 않았다고 PARTIAL 이 되지 않는다."""
    advice = F.draft(
        "긴장하면 말이 빨라져. 어떻게 하면 좋아?",
        type_="REQUEST",
        claim_op=None,
        metric="cpm",
        polarity="NEUTRAL",
    )
    req = F.with_loaded(F.request(user_message="긴장하면 말이 빨라져. 어떻게 하면 좋아?"), "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[advice]), F.done()],
        answers=[F.answer([F.claim("GENERAL", "숨을 한 번 고르고 첫 문장을 천천히 시작해 보세요.")])],
    )
    _, _, data = F.run_turn(req, llm)
    assert not any(p["step"] == templates.PLAN_UNRESOLVED for p in data["plan"])  # 남은 일로 세지 않았다


def test_dimension_state_answers_a_dimension_question() -> None:
    """'대본 전달은 어땠어?'(내용 전달 차원)에 그 차원 상태 근거로 답했으면 해소다(지표 칸이 없는 근거)."""
    ask = F.draft(
        "Take 5 대본 전달은 어땠어?",
        type_="REQUEST",
        claim_op=None,
        dimension="content_delivery",
        take_numbers=[5],
        polarity="NEUTRAL",
    )

    def answer(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("dimension") == "content_delivery")
        return F.answer([F.claim("RECORD", "Take 5의 내용 전달은 유지 상태예요.", evidence_ids=[eid])])

    req = F.with_loaded(F.request(user_message="Take 5 대본 전달은 어땠어?"), "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[ask]), F.tools(("get_take_report", {"take_id": "t-5", "section": "dimensions"})), F.done()],
        answers=[answer],
    )
    _, _, data = F.run_turn(req, llm)
    assert data["status"] == "DONE"
