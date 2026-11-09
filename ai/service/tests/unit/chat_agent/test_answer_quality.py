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

from pitch_coach_ai.features.chat_agent import templates
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
    """닫힌 목표의 상태만 고정 SYSTEM 문장으로 말한다(Take · 값 없이). `goal_result` 는 싣지 않는다 — 계약상 자동 목표 확인
    (TAKE_REVIEWED)의 값이고 BE 가 그 값으로 목표 상태를 바꾸므로 사용자 말 응답에 실으면 호출 시점이 바뀐다(5-5 · 12-3)."""
    data = _goal_question(load_config())
    goal_line = next(
        t for k, t in _texts(data) if t == templates.SystemText.GOAL_CLOSED_ACHIEVED.value
    )
    assert "Take" not in goal_line and "%" not in goal_line
    assert "RECORD" not in [k for k, _ in _texts(data)]
    assert data.get("goal_result") is None


@pytest.mark.parametrize(
    ("status", "text"),
    [
        ("NOT_ACHIEVED", templates.SystemText.GOAL_CLOSED_NOT_ACHIEVED),
        ("BASELINE_MISMATCH", templates.SystemText.GOAL_CLOSED_MISMATCH),
    ],
)
def test_goal_status_sentence_follows_the_terminal_status_only(status: str, text: Any) -> None:
    req = _closed_goal_request()
    req["context"]["conversation_goal"]["status"] = status
    llm = F.FakeLlm(
        [
            F.plan(assessments=[]),
            F.tools(("get_goals", {}), NO_HIT, prefix="g"),
            F.done(),
            F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
        ]
    )
    _, _, data = F.run_turn(
        req, llm, corpus=F.load_corpus(), corpus_version="guides-4", cfg=load_config()
    )
    assert data is not None
    assert text.value in [t for _, t in _texts(data)]


def test_goal_status_sentence_is_added_even_when_the_model_body_is_empty() -> None:
    """모델 문장이 모두 빠져도 목표 상태 문장은 낸다(빈 답 분기보다 먼저 만든다)."""
    llm = F.FakeLlm(
        [
            F.plan(assessments=[]),
            F.tools(("get_goals", {}), NO_HIT, prefix="g"),
            F.done(),
            F.answer(
                [F.claim("GENERAL", "청중과 눈을 맞춰서 말하세요 100% 확실해요.")], route="NO_GUIDE"
            ),
            F.answer(
                [F.claim("GENERAL", "청중과 눈을 맞춰서 말하세요 100% 확실해요.")], route="NO_GUIDE"
            ),
        ]
    )
    _, _, data = F.run_turn(
        _closed_goal_request(), llm, corpus=F.load_corpus(), corpus_version="guides-4"
    )
    assert data is not None
    assert templates.SystemText.GOAL_CLOSED_ACHIEVED.value in [t for _, t in _texts(data)]
    assert data["status"] == "PARTIAL"


def test_goal_status_sentence_off_does_not_add_it() -> None:
    data = _goal_question(load_config(goal_status_sentence=False))
    assert "RECORD" not in [k for k, _ in _texts(data)]
    assert data.get("goal_result") is None


# ── 목표 질문: 조사 첫머리에 get_goals 를 먼저 읽는다(6-1 2번) ─────────────────────────────


def _open_goal_request(message: str) -> dict[str, Any]:
    req = _closed_goal_request()
    req["user_message"] = message
    req["context"]["conversation_goal"]["status"] = "OPEN"
    return req


@pytest.mark.parametrize("message", ["지금 대화 목표가 뭐야?", "대화 목표는 어떻게 됐어?"])
def test_goal_question_reads_goals_first_even_on_a_direct_answer_route(message: str) -> None:
    """목표를 묻는 말: 계획이 기록 없이 답하기(ANSWER_DIRECT)를 골라도 조사로 돌리고, 모델이 고르기 전에 코드가 get_goals 를 읽는다."""
    llm = F.FakeLlm([F.plan(route="ANSWER_DIRECT", assessments=[]), F.done(), F.answer([])])
    _, _, data = F.run_turn(_open_goal_request(message), llm)
    assert data is not None
    assert [c.purpose for c in llm.calls][:2] == ["RESEARCH", "RESEARCH"]
    assert ("get_goals", {}) in tool_calls_in(llm.calls[1])
    assert [t["tool"] for t in data["trace"]].count("get_goals") == 1


@pytest.mark.parametrize("message", ["3장 시선을 목표로 잡아 줄래?", "Take 5 3장 시선 어땠어?"])
def test_goal_prefetch_needs_a_goal_question(message: str) -> None:
    """목표를 정하는 말 · 목표 낱말이 없는 질문은 목표 질문이 아니다: 코드가 get_goals 를 먼저 읽지 않는다."""
    llm = F.FakeLlm([F.plan(assessments=[]), F.done(), F.answer([])])
    F.run_turn(_open_goal_request(message), llm)
    assert ("get_goals", {}) not in tool_calls_in(llm.calls[1])


# ── 닫힌 목표: 상태 문장 + 보고 있는 Take 의 지금 판정 문장 ───────────────────────────


def _closing_request(
    *loaded: str, page: str | None = "t-5", status: str = "ACHIEVED", base_value: float = 0.10
) -> dict[str, Any]:
    """기준 Take 3 · 목표 저장 11-01 10:30. 그 뒤 완료한 Take 는 4(11-02) · 5(11-03)다. `page` 는 보고 있는 Take."""
    req = _closed_goal_request()
    goal = req["context"]["conversation_goal"]
    goal["base"].update({"take_id": "t-3", "take_number": 3, "value": base_value})
    goal["created_at"] = "2026-11-01T10:30:00+09:00"
    goal["status"] = status
    req["page"]["take_id"] = page
    return F.with_loaded(req, *loaded)


def _ask_goal(req: dict[str, Any]) -> dict[str, Any]:
    llm = F.FakeLlm(
        [
            F.plan(assessments=[]),
            F.tools(("get_goals", {}), NO_HIT, prefix="g"),
            F.done(),
            F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
        ]
    )
    _, _, data = F.run_turn(
        req, llm, corpus=F.load_corpus(), corpus_version="guides-4", cfg=load_config()
    )
    assert data is not None
    assert data.get("goal_result") is None  # 사용자 말 응답에는 목표 결과 계약 값을 싣지 않는다
    return data


def _goal_records(data: dict[str, Any]) -> list[str]:
    return [t for k, t in _texts(data) if k == "RECORD" and "목표 기준" in t]


def test_goal_status_sentence_adds_the_page_take_judgement_with_its_value() -> None:
    """보고 있는 Take 5 의 지금 판정이 목표 상태와 같으면 상태 문장 뒤에 그 Take 의 값 문장을 잇는다."""
    data = _ask_goal(_closing_request("t-4", "t-5"))
    texts = [t for _, t in _texts(data)]
    rec = _goal_records(data)
    assert templates.SystemText.GOAL_CLOSED_ACHIEVED.value in texts
    assert len(rec) == 1
    assert "Take 5" in rec[0] and "%" in rec[0] and "목표 기준을 달성한 값이에요" in rec[0]
    assert "목표를 이뤘" not in rec[0]  # 이 Take 가 목표를 이뤘다 · 닫았다고는 말하지 않는다
    assert texts.index(templates.SystemText.GOAL_CLOSED_ACHIEVED.value) < texts.index(rec[0])


def test_goal_status_sentence_does_not_pick_a_closing_take_by_itself() -> None:
    """닫은 Take 는 요청 자료로 알 수 없다: 보고 있는 Take 가 없으면 앞 Take 가 실려 있어도 값 문장을 내지 않는다."""
    data = _ask_goal(_closing_request("t-4", "t-5", page=None))
    assert templates.SystemText.GOAL_CLOSED_ACHIEVED.value in [t for _, t in _texts(data)]
    assert _goal_records(data) == []


def test_goal_status_sentence_not_achieved_wording() -> None:
    """못 이룬 목표: 보고 있는 Take 도 못 미치면 못 이룬 말투의 값 문장(기준 값이 높아 기준을 못 넘는다)."""
    data = _ask_goal(_closing_request("t-5", status="NOT_ACHIEVED", base_value=0.90))
    rec = _goal_records(data)
    assert len(rec) == 1 and "Take 5" in rec[0] and "목표 기준에 못 미치는 값이에요" in rec[0]


@pytest.mark.parametrize(
    ("loaded", "page", "status", "why"),
    [
        (("t-3",), "t-3", "ACHIEVED", "기준 Take 는 목표 뒤 Take 가 아님"),
        (("t-5",), "t-5", "NOT_ACHIEVED", "지금 판정(이룸)이 목표 상태(못 이룸)와 다름"),
    ],
)
def test_goal_status_sentence_states_only_the_status_otherwise(
    loaded: tuple[str, ...], page: str, status: str, why: str
) -> None:
    data = _ask_goal(_closing_request(*loaded, page=page, status=status))
    assert templates.GOAL_CLOSED_TEXT[status].value in [t for _, t in _texts(data)], why
    assert _goal_records(data) == [], why


# ── 다른 발표의 Take 끼리는 비교하지 않는다는 끝 문장(1-3) ─────────────────────────


def _two_pitch_loop(
    message: str, pitches: tuple[str, ...], drafts: list[dict[str, Any]] | None = None
) -> tuple[Any, list[Any]]:
    """기록 근거를 가진 실행과, 그 근거를 하나씩 든 답 문장들. 비교 안내 규칙이 보는 것만 가진다."""
    from types import SimpleNamespace

    from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
    from pitch_coach_ai.features.chat_agent.schemas import Claim

    reg = EvidenceRegistry(run_id="run-x")
    said = []
    for i, pid in enumerate(pitches, start=1):
        ev = reg.record(
            pitch_id=pid,
            take_id=f"{pid}-t{i}",
            take_number=i,
            kind="TAKE_RESULT",
            revision=1,
            path="gaze.audience_ratio",
            value=0.3,
            metric="audience_ratio",
        )
        said.append(
            Claim(
                kind="RECORD",
                text=f"Take {i}의 청중 시선은 30%예요.",
                evidence_ids=[ev.evidence_id],
            )
        )
    lp = SimpleNamespace(
        original=message,
        cfg=load_config(),
        ctx=SimpleNamespace(registry=reg),
        st=SimpleNamespace(plan_drafts=drafts or []),
    )
    return lp, said


def _cross(lp: Any, said: list[Any]) -> bool:
    from pitch_coach_ai.features.chat_agent import turn

    return turn._cross_pitch_compare(lp, said)


@pytest.mark.parametrize(
    "message",
    [
        "졸업 발표 Take 5랑 회사 소개 발표 Take 2 시선 비교해 줘",
        "졸업 발표랑 회사 소개 발표 중에 어느 쪽이 더 잘했어?",
    ],
)
def test_cross_pitch_comparison_gets_the_no_compare_sentence(message: str) -> None:
    lp, said = _two_pitch_loop(message, ("p-1", "p-2"))
    assert _cross(lp, said)


def test_cross_pitch_comparison_is_found_from_the_plan_draft_without_a_comparison_word() -> None:
    drafts = [{"type": "MEASURED", "claim_op": "CHANGE", "subject": {"relation": "COMPARE"}}]
    lp, said = _two_pitch_loop("두 발표 중 뭐가 낫지?", ("p-1", "p-2"), drafts)
    assert _cross(lp, said)


def test_no_compare_sentence_needs_both_a_comparison_and_two_pitches_in_the_answer() -> None:
    same, said = _two_pitch_loop("Take 4랑 Take 5 시선 비교해 줘", ("p-1",))
    assert not _cross(same, said)
    plain, said = _two_pitch_loop(
        "졸업 발표랑 회사 소개 발표 기록을 살펴보다 궁금해졌어. 각각 시선 알려 줘", ("p-1", "p-2")
    )
    assert not _cross(plain, said)


def test_no_compare_sentence_counts_only_the_pitches_the_answer_cites() -> None:
    """다른 피치 기록을 읽었어도 답 문장이 한 피치의 기록만 들면 붙이지 않는다."""
    lp, said = _two_pitch_loop("졸업 발표 Take 1이랑 2 비교해 줘", ("p-1", "p-2"))
    assert not _cross(lp, said[:1])
