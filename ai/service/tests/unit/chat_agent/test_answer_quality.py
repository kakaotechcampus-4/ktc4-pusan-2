"""답 품질 보강 시험 — 설정으로 따로 켜고 끄는 세 가지(`record_answer_guard` · `count_metric_hint` · `goal_status_sentence`).

- 기록 답 보장: '기록 말고 일반 조언으로'라고 해도 읽은 기록의 코드 문장이 답에 들어간다(6-1 · 13-2).
- 횟수 질문: 몇 번 · 몇 회는 횟수 지표(`filler_count`)를 읽는다. 계획 지침에 안내가 실리고, Take 전체 값에 횟수 지표가 실린다.
- 목표 결과 문장: 이미 닫힌 대화 목표의 결과를 사용자 말로 물으면 코드 템플릿 문장과 `goal_result`를 낸다(7-10).
각 항목은 켠 때와 끈 때를 함께 본다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.schemas import FinalData

from . import fakes as F
from .test_general import G1, NO_HIT
from .test_prefetch import tool_calls_in

pytestmark = pytest.mark.prefetch


def _texts(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["kind"], c["text"]) for c in data["message"]["claims"]]


# ── record_answer_guard ───────────────────────────────────────────────────────


def _bypass(cfg: Any) -> tuple[dict[str, Any], Any]:
    d = F.draft(
        "내 3장 시선 어땠어",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        slide_number=3,
        polarity="NEUTRAL",
    )
    req = F.with_loaded(
        F.request(user_message="내 3장 시선 어땠어? 기록 말고 일반적인 조언으로만 말해 줘"), "t-5"
    )
    llm = F.FakeLlm(
        [
            F.plan(assessments=[d]),
            F.tools(NO_HIT, prefix="s"),
            F.done(),
            F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
        ]
    )
    _, _, data = F.run_turn(req, llm, cfg=cfg, corpus=F.load_corpus(), corpus_version="guides-4")
    assert data is not None and data["status"] == "DONE"
    return data, llm


def test_record_guard_adds_the_record_sentence_after_the_streamed_advice() -> None:
    data, _ = _bypass(load_config())
    kinds = [k for k, _ in _texts(data)]
    texts = [t for _, t in _texts(data)]
    assert "RECORD" in kinds
    rec = next(t for k, t in _texts(data) if k == "RECORD")
    assert "3장" in rec and "%" in rec
    assert texts.index(rec) > texts.index(
        G1
    )  # 모델 문장은 이미 문장 줄로 나가 순서를 바꾸지 않고 뒤에 더한다
    fd = FinalData.model_validate(data)
    assert fd.status == "DONE"


def test_record_guard_off_keeps_the_model_answer_only() -> None:
    data, _ = _bypass(load_config(record_answer_guard=False))
    assert "RECORD" not in [k for k, _ in _texts(data)]


# ── count_metric_hint ─────────────────────────────────────────────────────────


def _count_question(cfg: Any) -> tuple[dict[str, Any], Any]:
    d = F.draft(
        "군더더기는 몇 번 나왔어",
        type_="REQUEST",
        claim_op=None,
        metric="filler_count",
        take_numbers=[5],
        polarity="NEUTRAL",
    )
    req = F.with_loaded(F.request(user_message="Take 5에서 군더더기는 몇 번 나왔어?"), "t-5")
    llm = F.FakeLlm([F.plan(assessments=[d]), F.done(), F.answer([])])
    F.run_turn(req, llm, cfg=cfg)
    return req, llm


def _take_values(llm: Any) -> list[str]:
    out: list[str] = []
    for m in llm.calls[1].messages:
        if m.get("role") == "tool":
            out += [
                v.get("metric")
                for it in json.loads(m["content"]).get("data", {}).get("items", [])
                for v in it.get("values", [])
            ]
    return [x for x in out if x]


def test_count_metric_hint_reads_the_count_value_and_tells_the_plan() -> None:
    _, llm = _count_question(load_config())
    assert any("filler_count" in (m.get("content") or "") for m in llm.calls[0].messages)
    assert "filler_count" in _take_values(llm)
    assert (
        "get_take_report",
        {"take_id": "t-5", "section": "slides", "slide_number": None},
    ) in tool_calls_in(llm.calls[1])


def test_count_metric_hint_off_leaves_prompt_and_values_unchanged() -> None:
    _, llm = _count_question(load_config(count_metric_hint=False))
    assert not any(
        "'몇 번 · 몇 회 · 횟수'" in (m.get("content") or "") for m in llm.calls[0].messages
    )
    assert "filler_count" not in _take_values(llm)


# ── goal_status_sentence ──────────────────────────────────────────────────────


def _closed_goal_request() -> dict[str, Any]:
    ex = json.loads((F.EXAMPLES / "request_take_reviewed.json").read_text(encoding="utf-8"))
    goal = ex["context"]["conversation_goal"]
    goal["base"].update({"take_id": "t-4", "take_number": 4, "value": 0.10})
    goal["status"] = "ACHIEVED"
    req = F.with_loaded(F.request(user_message="대화 목표는 어떻게 됐어?"), "t-5")
    req["context"]["conversation_goal"] = goal
    req["context"]["goal_version"] = goal["version"]
    return req


def _goal_question(cfg: Any) -> dict[str, Any]:
    llm = F.FakeLlm(
        [
            F.plan(assessments=[]),
            F.tools(("get_goals", {}), NO_HIT, prefix="g"),
            F.done(),
            F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
        ]
    )
    _, _, data = F.run_turn(
        _closed_goal_request(), llm, cfg=cfg, corpus=F.load_corpus(), corpus_version="guides-4"
    )
    assert data is not None and data["status"] == "DONE"
    return data


def test_goal_status_sentence_states_the_result_without_goal_result() -> None:
    """목표 결과 문장은 내지만 `goal_result` 는 싣지 않는다 — 계약상 자동 목표 확인(TAKE_REVIEWED)의 값이고 BE 가 그 값으로
    목표 상태를 바꾸므로 사용자 말 응답에 실으면 호출 시점이 바뀐다(5-5 · 12-3)."""
    data = _goal_question(load_config())
    goal_line = next(t for k, t in _texts(data) if k == "RECORD")
    assert "이뤘" in goal_line and "28%" in goal_line
    assert data.get("goal_result") is None


def test_goal_status_sentence_off_does_not_add_it() -> None:
    data = _goal_question(load_config(goal_status_sentence=False))
    assert "RECORD" not in [k for k, _ in _texts(data)]
    assert data.get("goal_result") is None
