"""답 쓰기 맥락 시험 (기능 문서 6-1 3번): 이번 턴에 읽은 근거 값 표(`evidence_table`)와 질문별 근거 목록(`questions`).

- 표는 이번 턴의 최신 기록 · 화면 근거 한 줄씩(id · Take · 장 · 무엇 · 화면 표기 값)이고 계산 근거 · 사본은 넣지 않는다.
- 질문 목록은 기록 질문 · 주장 초안마다 그 대상(Take · 장 · 지표 · 차원)에 맞는 근거 id 다. 발표법 주장은 넣지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

from . import fakes as F

MSG = "Take 5 3장 청중 시선 몇 %였어? 발표는 빠를수록 좋다며?"


def research_block(req: Any) -> dict[str, Any]:
    for m in req.messages:
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, str) and c.startswith("<research>"):
            return json.loads(c[len("<research>") : c.rindex("</research>")])
    raise AssertionError("<research> 블록이 없다")


def test_screen_numbers_without_a_metric_keep_their_raw_value() -> None:
    # 화면 숫자(제한 시간 등)는 지표가 없어 표기를 만들 수 없다: 값이 비어 보이지 않고 저장 여부와 함께 실린다
    from .test_tools import pitch_screen

    req = F.request(user_message="지금 제한 시간이 몇 분이야?")
    req["page"]["screen"] = pitch_screen()
    d = F.draft("지금 제한 시간이 몇 분이야", type_="REQUEST", claim_op=None, polarity="NEUTRAL")
    llm = F.FakeLlm([F.plan(assessments=[d]), F.tools(("get_screen", {})), F.done(), F.answer([])])
    F.run_turn(req, llm)
    table = research_block(next(c for c in llm.calls if c.purpose == "ANSWER"))["evidence_table"]
    rows = [r for r in table if r["what"] == "time_limit_sec"]
    assert sorted((r["value"], r["saved"]) for r in rows) == [(240, True), (300, False)]


def test_answer_call_gets_value_table_and_per_question_evidence() -> None:
    drafts = [
        F.draft(
            "Take 5 3장 청중 시선 몇 %였어",
            type_="REQUEST",
            claim_op=None,
            dimension="audience_gaze",
            metric="audience_ratio",
            slide_number=3,
            take_numbers=[5],
            polarity="NEUTRAL",
        ),
        F.draft("발표는 빠를수록 좋다며", type_="KNOWLEDGE", claim_op=None, polarity="NEUTRAL"),
    ]
    read = ("get_take_report", {"take_id": "t-5", "section": "slides", "slide_number": 3})
    llm = F.FakeLlm([F.plan(assessments=drafts), F.tools(read), F.done(), F.answer([])])
    F.run_turn(F.request(user_message=MSG), llm)
    block = research_block(next(c for c in llm.calls if c.purpose == "ANSWER"))
    table = block["evidence_table"]
    row = next(r for r in table if r["what"] == "청중 시선")
    assert (row["take"], row["slide"], row["value"]) == (5, 3, "28%")
    assert all(r["id"].startswith("e-") for r in table)  # 계산 근거(c-)는 표에 없다
    questions = block["questions"]
    assert [q["quote"] for q in questions] == [
        "Take 5 3장 청중 시선 몇 %였어"
    ]  # 발표법 주장은 빠진다
    assert questions[0]["evidence_ids"] == [row["id"]]
