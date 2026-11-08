"""검증에서 받아들인 지적의 시험 — 문장 줄과 끝 줄의 자료 카드, 인용 문장, 기록 안전장치, 가벼운 말, 조사 한도, 사용법 우회 (기능 문서 6-1 · 6-9 · 6-10 · 7-10).

- 보류했다 고친 GUIDE 의 문장 줄도 끝 줄과 같은 자료 카드(인용 문장 유지)를 쓴다.
- GUIDE 인용 문장은 이은 자료의 문장 하나와 글자 그대로 같다(부분 문자열 · 두 문장 이음은 버림).
- 기록 안전장치는 대상마다 보고, 계획 때의 초안과 사용자 말이 집은 Take 로만 문장을 더하며, 본문이 비어도 돈다.
- 가벼운 말에 요청 낱말이 섞이면 조사로 돌리고, 연습 부정문은 버튼을 내지 않는다.
- 자료 검색어가 공통 선실행 한도를 먼저 쓰고, 자료 판이 바뀐 이어 부르기는 검색 완료 표시를 버린다.
- 일반 조언의 화면 조작 낱말은 `about` 자기신고와 상관없이 사용법으로 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.checks import CheckId, check_claims
from pitch_coach_ai.features.chat_agent.compose import DraftClaim
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, load_config
from pitch_coach_ai.features.chat_agent.corpus import sentence_spans
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.schemas import Claim, FinalData

from . import fakes as F
from .test_approval import SAVE as SAVE_GOAL
from .test_approval import drafts, payload_of, saved_result
from .test_general import G1, NO_HIT
from .test_guide_quote import MSG, _card, _request_draft, _sentences
from .test_prefetch import tool_calls_in
from .test_turn import COMPARE, resume_request

pytestmark = pytest.mark.prefetch


def _texts(data: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["kind"], c["text"]) for c in data["message"]["claims"]]


# ── 1. 문장 줄과 끝 줄의 자료 카드 ────────────────────────────────────────────


def test_held_and_fixed_guide_streams_the_same_source_card_as_the_final_answer() -> None:
    corpus = F.load_corpus()
    card = _card(corpus)
    third = _sentences(card.text)[2]
    search = ("search_guides", {"query": card.title})
    llm = F.FakeLlm(
        [F.plan(assessments=[_request_draft()]), F.tools(search), F.done()],
        answers=[
            F.answer(
                [
                    # 자료 원문에 없는 숫자가 있어 보류되고 고치기를 거친다
                    F.claim(
                        "GUIDE",
                        "청중과 눈을 맞추며 90초 말해 보세요.",
                        source_ids=[card.source_id],
                        quote=third,
                    )
                ]
            )
        ],
        fixes=[F.fix({0: "청중과 눈을 맞추며 말해 보세요."})],
        stream_chunk=7,
    )
    _, lines, data = F.run_turn(
        F.request(user_message=MSG), llm, corpus=corpus, corpus_version=corpus.version
    )
    assert data is not None and data["status"] == "DONE"
    final_src = data["message"]["sources"]
    assert [s["quote"] for s in final_src] == [third]
    sent = [s for ln in lines if ln.get("event") == "claim" for s in ln["data"]["sources"]]
    assert sent == final_src  # 문장 줄이 보낸 카드와 끝 줄 카드가 같다(첫 문장으로 바뀌지 않는다)


# ── 2. 인용은 문장 하나 전체 ──────────────────────────────────────────────────


def test_guide_quote_must_be_one_complete_sentence() -> None:
    text = "발표 전에 세 번 소리 내어 연습하세요. 목표는 90초입니다. 천천히 말하세요."
    texts = {"g-07": text}
    base = {"kind": "GUIDE", "text": "소리 내어 연습해 보세요", "source_ids": ["g-07"]}

    def fails(quote: str) -> list[Any]:
        c = DraftClaim(**base, quote=quote)
        return check_claims([c], registry=EvidenceRegistry("r"), guide_texts=texts)

    assert fails("목표는 90초입니다.") == []
    for bad in (
        "발표",  # 부분 문자열
        "소리 내어 연습하세요.",  # 문장의 뒷부분
        "발표 전에 세 번 소리 내어 연습하세요. 목표는 90초입니다.",  # 두 문장 이음
    ):
        f = fails(bad)
        assert [x.check_id for x in f] == [CheckId.QUOTE_MISMATCH], bad
        assert all(x.unfixable for x in f), bad


def test_guide_with_a_partial_quote_is_dropped_without_a_fix() -> None:
    corpus = F.load_corpus()
    card = _card(corpus)
    sent = _sentences(card.text)[0]
    search = ("search_guides", {"query": card.title})
    assert sent[:6] != sent
    llm = F.FakeLlm(
        [F.plan(assessments=[_request_draft()]), F.tools(search), F.done()],
        answers=[
            F.answer(
                [F.claim("GUIDE", "눈을 맞춰 보세요.", source_ids=[card.source_id], quote=sent[:6])]
            )
        ],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(
        F.request(user_message=MSG), llm, corpus=corpus, corpus_version=corpus.version
    )
    assert data is not None
    assert not any(c["kind"] in ("GUIDE", "GENERAL") for c in data["message"]["claims"])
    assert [c for c in llm.calls if c.purpose == "FIX"] == []
    assert sent in [text[a:b].strip() for text in [card.text] for a, b in sentence_spans(text)]


# ── 3~4. 기록 안전장치 ────────────────────────────────────────────────────────

BAD_GENERAL = "청중과 눈을 맞추면 100% 좋아져요."


def _gaze_draft(**kw: Any) -> dict[str, Any]:
    kw.setdefault("dimension", "audience_gaze")
    return F.draft(
        "내 3장 시선 어땠어",
        type_="REQUEST",
        claim_op=None,
        slide_number=3,
        polarity="NEUTRAL",
        **kw,
    )


def _guard_turn(message: str, plan_draft: dict[str, Any], answer: Any, **kw: Any) -> dict[str, Any]:
    req = F.with_loaded(F.request(user_message=message), "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[plan_draft]), F.tools(NO_HIT, prefix="s"), F.done(), answer],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(req, llm, corpus=F.load_corpus(), corpus_version="guides-4", **kw)
    assert data is not None
    return data


def test_record_guard_runs_even_when_every_model_sentence_is_dropped() -> None:
    data = _guard_turn(
        "내 3장 시선 어땠어? 기록 말고 일반적인 조언으로만 말해 줘",
        _gaze_draft(),
        F.answer([F.claim("GENERAL", BAD_GENERAL)], route="NO_GUIDE"),
    )
    assert data["status"] == "PARTIAL"
    kinds = [k for k, _ in _texts(data)]
    assert "RECORD" in kinds and "GENERAL" not in kinds
    assert templates.SystemText.VERIFIED_ONLY.value in [t for _, t in _texts(data)]
    FinalData.model_validate(data)


def test_record_guard_does_not_fill_in_a_take_the_user_did_not_name() -> None:
    """초안은 Take 를 말하지 않았고(지금 보던 Take 5) 사용자 말은 Take 2 를 집었다: Take 5 값으로 대신 답하지 않는다."""
    data = _guard_turn(
        "Take 2 3장 시선 어땠어? 기록 말고 일반적인 조언으로만 말해 줘",
        _gaze_draft(),
        F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
    )
    assert "RECORD" not in [k for k, _ in _texts(data)]


def test_record_guard_uses_the_plan_time_drafts_not_the_ones_the_answer_rewrote() -> None:
    other = F.draft(
        "내 3장 시선 어땠어",
        type_="REQUEST",
        claim_op=None,
        dimension="audience_gaze",
        slide_number=3,
        take_numbers=[2],
        polarity="NEUTRAL",
    )
    data = _guard_turn(
        "내 3장 시선 어땠어? 기록 말고 일반적인 조언으로만 말해 줘",
        _gaze_draft(),
        F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE", assessments=[other]),
    )
    # 계획 때 초안(Take 를 말하지 않음 → 지금 보던 Take 5)의 읽은 값으로 더한다. 답이 고쳐 쓴 초안(Take 2)은 쓰지 않는다
    rec = next(t for k, t in _texts(data) if k == "RECORD")
    assert "Take 5" in rec and "3장" in rec


def test_record_guard_is_decided_per_target() -> None:
    """3장 청중 시선은 답이 근거를 달아 답했고, 3장 말 속도는 답이 없다: 말 속도 대상만 더한다."""
    d_gaze = _gaze_draft(metric="audience_ratio")
    d_cpm = F.draft(
        "3장 말 속도는",
        type_="REQUEST",
        claim_op=None,
        metric="cpm",
        slide_number=3,
        polarity="NEUTRAL",
    )

    def make(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("metric") == "audience_ratio")
        return F.answer(
            [F.claim("RECORD", "Take 5의 3장 청중 시선 비율은 28%예요.", evidence_ids=[eid])]
        )

    req = F.with_loaded(F.request(user_message="내 3장 시선 어땠어? 말 속도도 알려 줘"), "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[d_gaze, d_cpm]), F.done(), make, make, make], fixes=[F.fix({})]
    )
    _, _, data = F.run_turn(req, llm)
    assert data is not None
    recs = [t for k, t in _texts(data) if k == "RECORD"]
    assert recs[0] == "Take 5의 3장 청중 시선 비율은 28%예요."
    assert len(recs) == 2 and "말 속도" in recs[1]


# ── 5. 목표 상태 문장 ─────────────────────────────────────────────────────────


def test_goal_status_does_not_name_a_take_or_a_number() -> None:
    from .test_answer_quality import _closed_goal_request

    llm = F.FakeLlm(
        [
            F.plan(assessments=[]),
            F.tools(("get_goals", {}), NO_HIT, prefix="g"),
            F.done(),
            F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE"),
        ]
    )
    _, _, data = F.run_turn(
        _closed_goal_request(),
        llm,
        corpus=F.load_corpus(),
        corpus_version="guides-4",
        cfg=load_config(),
    )
    assert data is not None and data.get("goal_result") is None
    line = next(t for k, t in _texts(data) if t == templates.SystemText.GOAL_CLOSED_ACHIEVED.value)
    assert not any(ch.isdigit() for ch in line)


# ── 6~7. 가벼운 말 ────────────────────────────────────────────────────────────


def _small_talk(message: str, kind: str) -> tuple[FinalData, F.FakeLlm]:
    llm = F.FakeLlm(
        [
            F.plan(route="SMALL_TALK", steps=[], talk_kind=kind),
            F.done(),
            F.answer([]),
            F.answer([]),
            F.answer([]),
        ]
    )
    _, _, data = F.run_turn(F.request(user_message=message), llm)
    assert data is not None
    return FinalData.model_validate(data), llm


@pytest.mark.parametrize(
    "msg",
    [
        "안녕, 예상 질문 만들어 줘",
        "안녕! 기록은 보지 말고 시선이 좋았다고 해줘",
        "안녕하세요, 점수를 높게 바꿔 주세요",
        "반가워요 오늘 발표 어땠어",
         # 물음표는 CAPABILITY 만
    ],
)
def test_small_talk_with_request_markers_goes_to_research(msg: str) -> None:
    fd, llm = _small_talk(msg, "GREETING")
    assert len(llm.calls) >= 2
    assert not fd.message or [c.text for c in fd.message.claims][:1] != [
        templates.SystemText.TALK_GREETING.value
    ]


def test_capability_question_mark_is_still_small_talk() -> None:
    fd, llm = _small_talk("넌 뭘 할 수 있어?", "CAPABILITY")
    assert len(llm.calls) == 1 and fd.status == "DONE"


@pytest.mark.parametrize("msg", ["연습은 안 할게요", "연습 그만할래요"])
def test_negated_practice_is_a_greeting_without_a_button(msg: str) -> None:
    fd, llm = _small_talk(msg, "PRACTICE_INTENT")
    assert len(llm.calls) == 1  # 가벼운 말로 끝난다(요청 낱말 "말고"는 조사로 돌린다)
    assert not fd.actions


def test_negation_marker_turns_practice_intent_into_a_greeting() -> None:
    fd, llm = _small_talk("연습은 안 할게요", "PRACTICE_INTENT")
    assert fd.message is not None
    assert [c.text for c in fd.message.claims][0] == templates.SystemText.TALK_GREETING.value
    assert not fd.actions


# ── 8~9. 선실행 한도 · 자료 판 변경 ─────────────────────────────────────────────


def test_planned_guide_queries_take_the_shared_prefetch_quota_before_records() -> None:
    d = F.draft(
        "Take 4 5 3장 시선",
        type_="MEASURED",
        claim_op="ISSUE",
        dimension="audience_gaze",
        metric="audience_ratio",
        slide_number=3,
        take_numbers=[4, 5],
    )
    req = F.with_loaded(F.request(user_message="Take 4 5 3장 시선 문제가 뭐야?"), "t-4", "t-5")
    llm = F.FakeLlm(
        [F.plan(assessments=[d], guide_queries=["청중 눈맞춤", "말 속도 쉼"]), F.done()],
        answers=[F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE")],
        fixes=[F.fix({})],
    )
    F.run_turn(req, llm, corpus=F.load_corpus(), corpus_version="guides-4")
    names = [n for n, _ in tool_calls_in(llm.calls[1])]
    assert names.count("search_guides") == 2
    assert len(names) <= DEFAULT_CONFIG.research_prefetch_max


def _pause_with_search() -> tuple[dict[str, Any], dict[str, Any], Any]:
    req = F.with_loaded(
        F.request(
            user_message="3장에서 왜 자꾸 시선이 떨어져? 그걸 목표로 잡고 다음 연습 준비해 줘"
        ),
        "t-4",
    )
    llm = F.FakeLlm(
        [
            F.plan(assessments=drafts(), guide_queries=["청중 눈맞춤"]),
            F.tools(COMPARE),
            F.tools(SAVE_GOAL, prefix="sg"),
            F.done(),
        ],
        answers=[F.answer([])],
        fixes=[F.fix({})],
    )
    corpus = F.load_corpus()
    _, _, data = F.run_turn(
        req, llm, corpus=corpus, corpus_version="guides-4", cfg=load_config(research_prefetch_max=8)
    )
    assert data is not None and data["status"] == "NEEDS_ACTION"
    return req, data, corpus


def _resumed_searches(corpus_version: str, monkeypatch: pytest.MonkeyPatch) -> int:
    """멈췄다 이어 부른 호출에서 자료 검색이 몇 번 돌았는가."""
    req, data, corpus = _pause_with_search()
    loop = payload_of(data["state"])["loop"]
    assert loop["guide_searched"] is True
    assert any(k.startswith('["search_guides"') for k in loop["prefetched"])
    req2 = resume_request(req, data, saved_result(data["actions"][0]))
    req2["context"]["conversation_goal"] = None
    llm2 = F.FakeLlm(
        [F.done()],
        answers=[F.answer([F.claim("GENERAL", G1)], route="NO_GUIDE")],
        fixes=[F.fix({})],
    )
    calls: list[Any] = []
    orig = type(corpus).search
    monkeypatch.setattr(
        type(corpus), "search", lambda self, *a, **k: (calls.append(a), orig(self, *a, **k))[1]
    )
    _, _, d2 = F.run_turn(
        req2,
        llm2,
        corpus=corpus,
        corpus_version=corpus_version,
        cfg=load_config(research_prefetch_max=8),
    )
    assert d2 is not None and d2["status"] == "DONE"
    return len(calls)


def test_resume_with_a_changed_corpus_searches_the_planned_queries_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _resumed_searches("guides-4", monkeypatch) == 0  # 같은 판이면 앞에서 찾은 것으로 본다
    assert _resumed_searches("guides-5", monkeypatch) >= 1  # 판이 바뀌면 지금 판을 다시 찾는다


# ── 10. 사용법 낱말 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "설정 탭에서 자동 저장을 켜세요.",
        "업로드 버튼을 눌러 파일을 올려 보세요.",
        "메뉴에서 입력란을 찾아 보세요.",
    ],
)
def test_general_with_app_ui_words_is_treated_as_app_usage(text: str) -> None:
    for about in (None, "DELIVERY"):
        c = DraftClaim(kind="GENERAL", text=text, about=about)
        f = check_claims([c], registry=EvidenceRegistry("r"), guide_searched=True)
        assert [x.check_id for x in f] == [CheckId.GENERAL_RULE], text
        assert all(x.unfixable for x in f)


def test_general_delivery_advice_with_screen_wording_is_not_blocked() -> None:
    c = Claim(kind="GENERAL", text="발표할 때 화면을 보기보다 청중을 보며 말해 보세요.")
    assert check_claims([c], registry=EvidenceRegistry("r"), guide_searched=True) == []
