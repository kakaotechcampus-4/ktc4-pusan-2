"""화면 사용법 문장 시험 — 사용법 자료 없이 일반 조언으로 짐작하지 않는다 (기능 문서 6-10 · 7-10, L4-28).

검사(`check_claims`)는 문장 하나씩, 흐름은 가짜 모델로 본다.
"""

from __future__ import annotations

from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.checks import CheckId, check_claims
from pitch_coach_ai.features.chat_agent.compose import DraftClaim
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry

from . import fakes as F
from .test_general import G1, NO_HIT, _run, _script, _texts

USAGE = templates.SystemText.USAGE_NO_GUIDE.value
USAGE_TEXT = "평가 기준은 연습 준비 화면의 입력 칸에 적고 저장할 수 있어요."
METHOD_TEXT = "중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉽다."


def _failures(c: DraftClaim, **kw: object) -> list[tuple[CheckId, bool]]:
    got = check_claims([c], registry=EvidenceRegistry("run-1"), **kw)  # type: ignore[arg-type]
    return [(f.check_id, f.unfixable) for f in got]


def test_general_app_usage_is_dropped_without_a_fix() -> None:
    c = DraftClaim(kind="GENERAL", text=G1, about="APP_USAGE")
    assert _failures(c, guide_searched=True) == [(CheckId.GENERAL_RULE, True)]


def test_general_delivery_passes_as_before() -> None:
    for about in ("DELIVERY", None):
        c = DraftClaim(kind="GENERAL", text=G1, about=about)
        assert _failures(c, guide_searched=True) == []


def test_guide_app_usage_must_cite_a_usage_card() -> None:
    guide_texts = {"g-01": METHOD_TEXT, "u-01": USAGE_TEXT}
    bad = DraftClaim(
        kind="GUIDE",
        text="중요한 문장 앞에서 잠깐 멈추면 청중이 따라오기 쉽다.",
        source_ids=["g-01"],
        quote=METHOD_TEXT,
        about="APP_USAGE",
    )
    assert _failures(bad, guide_texts=guide_texts) == [(CheckId.GENERAL_RULE, True)]
    ok = DraftClaim(
        kind="GUIDE",
        text=USAGE_TEXT,
        source_ids=["u-01"],
        quote=USAGE_TEXT,
        about="APP_USAGE",
    )
    assert _failures(ok, guide_texts=guide_texts) == []
    # 발표법 자료를 발표 전달 조언으로 인용하는 문장은 그대로 통과한다
    plain = DraftClaim(
        kind="GUIDE", text=METHOD_TEXT, source_ids=["g-01"], quote=METHOD_TEXT, about="DELIVERY"
    )
    assert _failures(plain, guide_texts=guide_texts) == []


def test_usage_unanswered_alone_is_done_with_the_fixed_sentence() -> None:
    """사용법 일반 조언은 고치기 없이 빠지고, 고정 문장만 남아도 DONE 이다."""
    answer = F.answer(
        [F.claim("GENERAL", G1, about="APP_USAGE")], route="NO_GUIDE", usage_unanswered=True
    )
    _, data, llm = _run(answer, fixes=[])
    assert data["status"] == "DONE"
    # 사용법 자료 없음 문장 뒤에 기록 질문으로 이어 가는 다음 걸음(Issue #1194 4-E)
    assert _texts(data) == [("SYSTEM", USAGE), ("SYSTEM", templates.SystemText.NEXT_REVIEW.value)]
    assert [c for c in llm.calls if c.purpose == "FIX"] == []


def test_usage_unanswered_is_appended_after_the_other_sentences() -> None:
    answer = F.answer(
        [F.claim("GENERAL", G1, about="DELIVERY")], route="NO_GUIDE", usage_unanswered=True
    )
    _, data, _ = _run(answer)
    kinds = _texts(data)
    assert kinds[-1] == ("SYSTEM", USAGE)
    assert ("GENERAL", G1) in kinds
    assert data["status"] == "DONE"


def test_without_the_flag_no_usage_sentence() -> None:
    _, data, _ = _run(F.answer([F.claim("GENERAL", G1, about="DELIVERY")], route="NO_GUIDE"))
    assert ("SYSTEM", USAGE) not in _texts(data)


def test_out_of_scope_refusal_is_unaffected() -> None:
    llm = F.FakeLlm(
        [
            F.plan(steps=["답 쓰기"]),
            F.done(),
            F.answer([], route="OUT_OF_SCOPE", scope_reason="CONTENT_COACHING"),
        ]
    )
    status, _, data = F.run_turn(F.request(user_message="평가 기준 괜찮은지 봐 줘"), llm)
    assert status == 200 and data is not None
    assert data["status"] == "REFUSED"
    assert _texts(data) == [
        ("SYSTEM", templates.scope_text("CONTENT_COACHING").value),
        ("SYSTEM", templates.SystemText.NEXT_REVIEW.value),
    ]


def test_script_helpers_import() -> None:
    assert _script(F.answer([]))[0] is not None and NO_HIT
