"""'가장 최근' 발표 시험 (Issue #1194 4-A, 기능 문서 5-7 `list_pitches` · 6-9 날짜 검사).

- 날짜 꼴("11월 10일")은 숫자 검사에서 따로 다룬다: 이은 근거 중 날짜 근거(단위 `date`)의 월 · 일과 같아야 통과하고,
  단위 없는 수 11 · 10 으로 대조하지 않는다.
"""

from __future__ import annotations

from pitch_coach_ai.features.chat_agent.checks import CheckId, check_claims, extract_numbers
from pitch_coach_ai.features.chat_agent.config import load_config
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.schemas import Claim

CFG = load_config()


def _date_ev(r: EvidenceRegistry, value: str = "2026-11-10"):
    return r.record(
        pitch_id="p-2",
        take_id=None,
        take_number=None,
        kind=None,
        revision=None,
        path="pitches[pitch_id=p-2].last_practiced_at",
        value=value,
        unit="date",
    )


def test_extract_dates_are_not_values() -> None:
    ex = extract_numbers("11월 10일에 시작한 발표는 Take 3개예요.")
    assert ex.dates == ((11, 10),)
    assert [t.text for t in ex.values] == []  # 'Take 3' 은 Take 번호, 날짜의 11 · 10 은 값이 아니다
    assert extract_numbers("11 월 9 일").dates == ((11, 9),)


def test_date_matches_date_evidence() -> None:
    r = EvidenceRegistry(run_id="run")
    ev = _date_ev(r)
    ok = Claim(
        kind="RECORD",
        text="가장 최근 발표는 11월 10일에 연습했어요.",
        evidence_ids=[ev.evidence_id],
    )
    assert check_claims([ok], registry=r, cfg=CFG) == []


def test_wrong_date_fails() -> None:
    r = EvidenceRegistry(run_id="run")
    ev = _date_ev(r)
    bad = Claim(
        kind="RECORD", text="가장 최근 발표는 11월 9일에 연습했어요.", evidence_ids=[ev.evidence_id]
    )
    fails = check_claims([bad], registry=r, cfg=CFG)
    assert [f.check_id for f in fails] == [CheckId.NUMBER_MISMATCH]


def test_date_without_date_evidence_fails() -> None:
    r = EvidenceRegistry(run_id="run")
    other = r.record(
        pitch_id="p-2",
        take_id=None,
        take_number=None,
        kind=None,
        revision=None,
        path="pitches[pitch_id=p-2].take_count",
        value=2,
        unit="count",
    )
    bad = Claim(
        kind="RECORD", text="11월 10일에 연습한 발표예요.", evidence_ids=[other.evidence_id]
    )
    assert CheckId.NUMBER_MISMATCH in [f.check_id for f in check_claims([bad], registry=r, cfg=CFG)]


# ── 한 턴: 범위 '전체'에서 "가장 최근에 한 발표" (dev 버그, 2026-11-10) ─────────────────────────────

from typing import Any  # noqa: E402

import pytest  # noqa: E402

from pitch_coach_ai.features.chat_agent import templates  # noqa: E402
from pitch_coach_ai.features.chat_agent.schemas import FinalData  # noqa: E402

from . import fakes as F  # noqa: E402

T = templates.SystemText
DEV_STEPS = ["가장 최근 발표 기록을 찾습니다", "최근 발표 기록을 엽니다", "기록을 안내합니다"]


def _global(message: str, pitches: list[dict[str, Any]]) -> dict[str, Any]:
    req = F.request(user_message=message)
    req["run"]["scope_pitch_id"] = None
    req["context"]["pitch"] = None
    req["context"]["loaded_takes"] = []
    req["context"]["pitches"] = pitches
    req["context"]["pitches_total"] = len(pitches)
    req["page"] = {"route": "/", "pitch_id": None, "take_id": None, "screen": None}
    return req


def _pitch(pid: str, title: str, count: int, at: str | None) -> dict[str, Any]:
    return {
        "pitch_id": pid,
        "title": title,
        "take_count": count,
        "last_practiced_at": at,
        "last_review": None,
    }


def _texts(data: dict[str, Any]) -> list[str]:
    return [c["text"] for c in (data.get("message") or {}).get("claims") or []]


def _run(
    req: dict[str, Any], *, answer_claims: list[Any] | None = None
) -> tuple[FinalData, dict[str, Any], Any]:
    # 모델은 근거 없는 문장을 쓰고(검사로 빠짐) 고치기도 못 한다 — 코드 문장이 답이어야 한다
    bad = F.claim("RECORD", "가장 최근 발표는 투자 설명회예요.")
    llm = F.FakeLlm(
        [F.plan(steps=DEV_STEPS), F.done()],
        answers=[F.answer(answer_claims if answer_claims is not None else [bad])],
        fixes=[F.fix({0: None})],
    )
    _, _, data = F.run_turn(req, llm)
    return FinalData.model_validate(data), data, llm


@pytest.mark.prefetch
def test_latest_pitch_without_finished_take_is_answered_from_the_list() -> None:
    """맨 앞 피치(마지막 연습 시작이 가장 늦음)에 분석이 끝난 Take 가 0개여도, 찾은 근거로 답하고 DONE 이다."""
    req = _global(
        "가장 최근에 한 발표를 보고 싶어",
        [
            _pitch("p-2", "투자 설명회", 0, "2026-11-10T09:30:00+09:00"),
            _pitch("p-1", "졸업 발표", 3, "2026-11-01T10:00:00+09:00"),
        ],
    )
    fd, data, _ = _run(req)
    assert fd.status == "DONE"
    assert _texts(data)[:2] == [
        "가장 최근에 연습을 시작한 발표는 '투자 설명회'이고, 마지막 연습 시작은 11월 10일, 아직 분석이 끝난 Take는 없어요.",
        "분석이 끝난 Take가 있는 가장 최근 발표는 '졸업 발표'이고, 마지막 연습 시작은 11월 1일이에요.",
    ]
    assert T.PARTIAL_CHECKED.value not in _texts(data)
    # 코드가 피치 목록을 먼저 읽었다(모델이 고르기 전에)
    assert [t["tool"] for t in data["trace"]][:1] == ["list_pitches"]
    assert {c["kind"] for c in data["message"]["claims"][:2]} == {"RECORD"}


@pytest.mark.prefetch
def test_latest_pitch_with_finished_takes() -> None:
    req = _global(
        "마지막으로 연습한 피치 뭐였지?",
        [
            _pitch("p-1", "졸업 발표", 3, "2026-11-01T10:00:00+09:00"),
            _pitch("p-2", "투자 설명회", 2, "2026-11-09T16:00:00Z"),  # UTC → 11월 10일(한국 시간)
        ],
    )
    fd, data, _ = _run(req)
    assert fd.status == "DONE"
    assert _texts(data)[0] == (
        "가장 최근에 연습을 시작한 발표는 '투자 설명회'이고, 마지막 연습 시작은 11월 10일, 분석이 끝난 Take는 2개예요."
    )
    assert not any(t.startswith("분석이 끝난 Take가 있는") for t in _texts(data))


@pytest.mark.prefetch
def test_latest_pitch_title_with_numbers_is_not_dropped() -> None:
    """제목의 숫자(1분기)는 기록 값이 아니다 — 코드 문장이 숫자 검사로 빠지지 않는다."""
    req = _global(
        "최근 발표 어땠어?", [_pitch("p-9", "1분기 실적 발표", 1, "2026-11-08T09:00:00+09:00")]
    )
    fd, data, _ = _run(req)
    assert fd.status == "DONE"
    assert _texts(data)[0].startswith("가장 최근에 연습을 시작한 발표는 '1분기 실적 발표'이고")


@pytest.mark.prefetch
def test_no_pitch_practiced_yet() -> None:
    req = _global("가장 최근 발표 보여 줘", [_pitch("p-1", "졸업 발표", 0, None)])
    fd, data, _ = _run(req)
    assert fd.status == "DONE"
    assert _texts(data)[0] == T.LATEST_NONE.value


@pytest.mark.prefetch
def test_no_pitch_at_all_says_none_started_and_is_done() -> None:
    """피치가 하나도 없으면 목록을 읽고도 근거가 없다: '아직 연습을 시작한 발표가 없어요.'로 DONE 이다(Issue #1194 독립 평가 —
    전에는 목록을 읽지 않은 것으로 보아 모델 문장이 빠지면 PARTIAL '답 일부를 확인하지 못해 …'로 끝났다)."""
    req = _global("마지막으로 연습한 발표 기록 좀 보여 줘", [])
    fd, data, _ = _run(req, answer_claims=[])
    assert fd.status == "DONE"
    assert T.LATEST_NONE.value in _texts(data)
    assert not any("답 일부를 확인하지 못해" in t for t in _texts(data))


@pytest.mark.prefetch
def test_practice_button_for_the_latest_pitch_in_global_scope() -> None:
    """범위 '전체'에서 "가장 최근에 하던 발표로 바로 연습하러 갈래요": 계획이 기록 없이 답하기를 골라도 조사로 돌려 피치 목록을 읽고,
    코드가 고른 가장 최근 피치로 [연습 준비로] 버튼을 낸다(Issue #1194 독립 평가 — 전에는 범위 피치가 없다고 버튼을 버렸다)."""
    msg = "가장 최근에 하던 발표로 바로 연습하러 갈래요."
    req = _global(msg, PITCHES_0)
    llm = F.FakeLlm(
        [F.plan(route="ANSWER_DIRECT", steps=DEV_STEPS), F.done()],
        answers=[F.answer([], start_practice={"authorized_by": "바로 연습하러 갈래요"})],
        fixes=[F.fix({})],
    )
    _, _, data = F.run_turn(req, llm)
    fd = FinalData.model_validate(data)
    starts = [a for a in fd.actions if a.type == "START_PRACTICE"]
    assert starts and starts[0].args == {"pitch_id": "p-2"}  # 마지막 연습 시작이 가장 늦은 피치
    assert HEAD_0 in _texts(data)


@pytest.mark.prefetch
def test_scoped_conversation_is_not_a_latest_pitch_question() -> None:
    """범위 피치가 있으면 '최근'은 그 피치의 Take 목록이 답이다(코드 문장 없음)."""
    req = F.request(user_message="가장 최근에 한 발표 어땠어?")
    fd, data, _ = _run(req, answer_claims=[])
    assert not any("가장 최근에 연습을 시작한 발표" in t for t in _texts(data))


# ── 검수 반영(Opus 5.5): 최근 발표를 찾지 않는 말 · 이어 부른 뒤 범위가 바뀐 경우 ─────────────────────────────


def test_general_talk_with_latest_words_is_not_a_latest_pitch_question() -> None:
    from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
    from pitch_coach_ai.features.chat_agent.loop import latest_pitch_question

    for msg in (
        "요즘 발표할 때 너무 떨려요, 어떻게 해야 해?",
        "최근 발표할 때 떨려",
        "최신 발표 트렌드가 뭐야?",
        "마지막 피치 부분에서 목소리가 작아져",
        "최근에 연습을 많이 해서 발표할 때 긴장이 덜 돼요",
        "마지막 장에서 발표 속도가 빨라",
        # 사이 낱말이 연습 이력이 아니다(코드 검수 반례)
        "최근 들어 발표가 너무 무서워요",
        "최근 친구 발표는 어땠어?",
    ):
        assert not latest_pitch_question(msg, DEFAULT_CONFIG), msg
    for msg in (
        "가장 최근 발표",
        "최근 발표는 언제야?",
        "마지막으로 한 피치 기록 보여 줘",
        # 꾸밈 낱말 여러 개 · 발표 뒤 '로 · 였 · 인지'(Issue #1194 독립 평가에서 놓친 말꼴)
        "제가 가장 최근에 연습을 시작한 발표가 무엇인지 확인 부탁드려요.",
        "요즘 마지막으로 연습했던 게 어떤 발표였더라?",
        "가장 최근에 하던 발표로 바로 연습하러 갈래요.",
    ):
        assert latest_pitch_question(msg, DEFAULT_CONFIG), msg


def test_first_call_decision_is_kept_after_the_scope_changes() -> None:
    """처음 호출에서 정한 판정을 서명 상태에 남겨, 이어 부를 때 범위가 바뀌어도(FETCH_PITCH) 그대로 쓴다. 옛 상태(None)는 지금 요청으로."""
    from pitch_coach_ai.features.chat_agent.loop import LoopState

    assert LoopState.load({}).latest_q is None
    assert LoopState.load({"latest_q": True}).latest_q is True
    assert LoopState.load(LoopState(latest_q=False).dump()).latest_q is False


# ── 검수 반영(Opus 5.5) ─────────────────────────────────────────────────────────────────

PITCHES_0 = [
    _pitch("p-2", "투자 설명회", 0, "2026-11-10T09:30:00+09:00"),
    _pitch("p-1", "졸업 발표", 3, "2026-11-01T10:00:00+09:00"),
]
HEAD_0 = "가장 최근에 연습을 시작한 발표는 '투자 설명회'이고, 마지막 연습 시작은 11월 10일, 아직 분석이 끝난 Take는 없어요."
EXTRA_0 = (
    "분석이 끝난 Take가 있는 가장 최근 발표는 '졸업 발표'이고, 마지막 연습 시작은 11월 1일이에요."
)


@pytest.mark.prefetch
def test_answer_failure_with_another_request_is_partial_with_the_latest_sentences_first() -> None:
    """답 쓰기가 실패했을 때 '최근 발표' 말고 다른 요청(조언)이 섞였으면 DONE 이 아니라 PARTIAL 이고, 찾은 근거 문장은 앞에 남는다."""
    from pitch_coach_ai.features.chat_agent.llm_types import LlmCallFailed

    req = _global("가장 최근에 한 발표 뭐야? 그리고 긴장 줄이는 법도 알려줘", PITCHES_0)
    llm = F.FakeLlm(
        [F.plan(steps=DEV_STEPS), F.done()], answers=[LlmCallFailed(attempts=1, usage=[])]
    )
    _, _, data = F.run_turn(req, llm)
    assert data["status"] == "PARTIAL"
    assert _texts(data)[:2] == [HEAD_0, EXTRA_0]


@pytest.mark.prefetch
def test_answer_failure_on_the_latest_question_alone_is_done() -> None:
    from pitch_coach_ai.features.chat_agent.llm_types import LlmCallFailed

    req = _global("가장 최근에 한 발표 뭐야?", PITCHES_0)
    llm = F.FakeLlm(
        [F.plan(steps=DEV_STEPS), F.done()], answers=[LlmCallFailed(attempts=1, usage=[])]
    )
    _, _, data = F.run_turn(req, llm)
    assert data["status"] == "DONE"
    assert _texts(data)[:2] == [HEAD_0, EXTRA_0]


@pytest.mark.prefetch
def test_model_sentence_calling_another_pitch_latest_is_dropped() -> None:
    """모델이 다른 피치(졸업 발표)의 목록 근거로 '가장 최근'이라 쓴 문장은 버리고, 코드가 정한 대상의 문장만 나간다."""

    def answer(r: Any) -> Any:
        eid = F.find_evidence(
            r, lambda o: o.get("value") == 3
        )  # 졸업 발표(p-1)의 분석이 끝난 Take 수 근거
        return F.answer(
            [F.claim("RECORD", "가장 최근에 연습한 발표는 '졸업 발표'예요.", evidence_ids=[eid])]
        )

    req = _global("가장 최근에 한 발표 뭐야?", PITCHES_0)
    llm = F.FakeLlm([F.plan(steps=DEV_STEPS), F.done()], answers=[answer], fixes=[F.fix({0: None})])
    _, _, data = F.run_turn(req, llm)
    texts = _texts(data)
    assert "가장 최근에 연습한 발표는 '졸업 발표'예요." not in texts
    assert texts[:2] == [HEAD_0, EXTRA_0]


@pytest.mark.prefetch
def test_model_citing_the_latest_date_still_gets_the_finished_pitch_sentence() -> None:
    """모델 문장이 최근 피치의 날짜 근거만 이었으면(분석이 끝난 Take 수는 말하지 않음) 첫 코드 문장을 그대로 내고(TARGET v2 1절 '답에
    이 문장이 나온다', Issue #1194 독립 평가 — 전에는 날짜만 이어도 뺐다), 결과를 볼 수 있는 최근 발표 안내도 붙인다."""

    def answer(r: Any) -> Any:
        eid = F.find_evidence(r, lambda o: o.get("value") == "11월 10일")
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "가장 최근 발표는 11월 10일에 시작한 '투자 설명회'예요.",
                    evidence_ids=[eid],
                )
            ]
        )

    req = _global("가장 최근에 한 발표 뭐야?", PITCHES_0)
    llm = F.FakeLlm([F.plan(steps=DEV_STEPS), F.done()], answers=[answer])
    _, _, data = F.run_turn(req, llm)
    texts = _texts(data)
    assert (
        HEAD_0 in texts
    )  # 첫 문장이 분석이 끝난 Take 가 없다는 사실까지 말한다(path-glob-latest-unfinished)
    assert EXTRA_0 in texts


@pytest.mark.prefetch
def test_model_citing_date_and_count_gets_no_extra_zero_sentence() -> None:
    """모델 문장이 날짜와 분석이 끝난 Take 수 근거를 모두 이었으면 '아직 없어요' 문장을 되풀이하지 않는다."""

    def answer(r: Any) -> Any:
        day = F.find_evidence(r, lambda o: o.get("value") == "11월 10일")
        cnt = F.find_evidence(
            r, lambda o: o.get("value") == 0
        )  # 첫 줄 '투자 설명회'의 분석이 끝난 Take 수
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "가장 최근 발표는 11월 10일에 시작한 '투자 설명회'이고 분석이 끝난 Take는 0개예요.",
                    evidence_ids=[day, cnt],
                )
            ]
        )

    req = _global("가장 최근에 한 발표 뭐야?", PITCHES_0)
    llm = F.FakeLlm([F.plan(steps=DEV_STEPS), F.done()], answers=[answer], fixes=[F.fix({})])
    _, _, data = F.run_turn(req, llm)
    texts = _texts(data)
    assert texts and texts[0].startswith("가장 최근 발표는 11월 10일")  # 모델 문장이 나갔다
    assert "'투자 설명회'는 아직 분석이 끝난 Take가 없어요." not in texts


@pytest.mark.prefetch
def test_model_citing_both_dates_still_gets_the_finished_pitch_sentence() -> None:
    """모델이 두 피치의 날짜만 이어 말하면('두 발표의 마지막 연습 시작 날짜는 …') 결과를 볼 수 있는 최근 발표 안내가 빠지지
    않는다 — 분석이 끝난 Take 수 근거를 잇지 않았다(실사용 흐름 점검 장면 1, Issue #1194)."""

    def answer(r: Any) -> Any:
        latest = F.find_evidence(r, lambda o: o.get("value") == "11월 10일")
        done = F.find_evidence(r, lambda o: o.get("value") == "11월 1일")
        return F.answer(
            [
                F.claim(
                    "RECORD",
                    "가장 최근 발표는 11월 10일에 시작했고, 다른 발표는 11월 1일에 시작했어요.",
                    evidence_ids=[latest, done],
                )
            ]
        )

    req = _global("가장 최근에 한 발표 뭐야?", PITCHES_0)
    llm = F.FakeLlm([F.plan(steps=DEV_STEPS), F.done()], answers=[answer], fixes=[F.fix({})])
    _, _, data = F.run_turn(req, llm)
    texts = _texts(data)
    assert texts and texts[0].startswith("가장 최근 발표는 11월 10일")  # 모델 문장이 나갔다
    assert EXTRA_0 in texts
