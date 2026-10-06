"""Deepgram keyterm 조립. 한도를 넘으면 Deepgram 이 연결을 거절하므로 경계를 본다."""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service as pitch_service
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.realtime.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.keyterms import (
    MAX_KEYTERM_TOKENS,
    MAX_KEYTERMS,
    TOKEN_HEADROOM,
    build_keyterms,
    estimate_tokens,
)


def _tokens(terms: tuple[str, ...]) -> int:
    return sum(estimate_tokens(t) for t in terms)


# ── build_keyterms ────────────────────────────────────────────────────


def test_fillers_come_first_and_script_terms_follow():
    keyterms = build_keyterms(KEYTERM_FILLERS, ["SeatFlow", "XGBoost"])
    assert keyterms == (*KEYTERM_FILLERS, "SeatFlow", "XGBoost")


def test_fillers_alone_leave_room_for_script_terms():
    # filler 가 예산을 거의 다 쓰면 대본 용어가 들어갈 자리가 없다
    assert _tokens(build_keyterms(KEYTERM_FILLERS)) < MAX_KEYTERM_TOKENS // 4


def test_blank_duplicate_and_filler_overlap_are_dropped():
    keyterms = build_keyterms(("음",), ["", "  ", "음", "피치  코치", "피치 코치\n", "A"])
    assert keyterms == ("음", "피치 코치", "A")


def test_token_budget_is_never_exceeded_and_small_terms_still_fit():
    long_term = "가" * 500  # 502 토큰 — 혼자서 예산을 넘는다
    many = [f"용어{i:02d}" for i in range(100)]  # 하나에 6 토큰
    keyterms = build_keyterms(KEYTERM_FILLERS, [long_term, *many])

    assert long_term not in keyterms
    # 긴 용어 하나 때문에 뒤의 짧은 용어까지 버리지 않는다
    assert "용어00" in keyterms
    assert _tokens(keyterms) <= MAX_KEYTERM_TOKENS - TOKEN_HEADROOM
    # 예산이 모자라 뒤쪽은 빠진다
    assert "용어99" not in keyterms


def test_estimate_follows_the_measured_worst_case():
    """실측(§5-1): 가장 비싼 모양이 "한글·ASCII 글자 수 + 2" 였다."""
    assert estimate_tokens("가나다라") == 6
    assert estimate_tokens("abcdefgh") == 10
    assert estimate_tokens("피치 코치") == 7  # 공백도 한 글자로 센다
    # 측정하지 못한 문자는 바이트로 쪼개진다고 본다
    assert estimate_tokens("漢字") == 6 + 2


def test_korean_terms_get_about_twice_the_room_of_byte_counting():
    # 예전(UTF-8 바이트 + 1) 에는 4글자 한글 용어가 filler 뒤에 32개 들어갔다
    terms = [f"한글용{chr(0xAC00 + i)}" for i in range(85)]
    keyterms = build_keyterms(KEYTERM_FILLERS, terms)

    assert len(keyterms) - len(KEYTERM_FILLERS) >= 60


def test_count_limit():
    keyterms = build_keyterms([chr(ord("a") + i % 26) * (i // 26 + 1) for i in range(150)])
    assert len(keyterms) == MAX_KEYTERMS


# ── pitch_service.stt_keyterm_candidates ──────────────────────────────


def _script(db: Session, user_id: uuid.UUID, **fields) -> ScriptVersion:
    pitch = Pitch(user_id=user_id, title="발표", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본", **fields)
    db.add(script)
    db.flush()
    return script


def _slide(db: Session, script: ScriptVersion, number: int, keywords: list[str] | None) -> None:
    db.add(
        ScriptSlide(
            script_version_id=script.id,
            slide_number=number,
            full_content="본문",
            keywords=keywords,
        )
    )
    db.flush()


def test_candidates_are_terms_then_keywords_round_robin(db_session: Session, user_id: uuid.UUID):
    script = _script(
        db_session, user_id, parse_status=ScriptParseStatus.DONE, terms=["SeatFlow", "XGBoost"]
    )
    # 넣는 순서와 상관없이 slide_number 순으로 본다
    _slide(db_session, script, 2, ["둘-1"])
    _slide(db_session, script, 1, ["하나-1", "하나-2", "하나-3"])
    _slide(db_session, script, 3, None)

    assert pitch_service.stt_keyterm_candidates(db_session, script.pitch_id, script.id) == [
        "SeatFlow",
        "XGBoost",
        "하나-1",
        "둘-1",
        "하나-2",
        "하나-3",
    ]


def test_candidates_empty_until_parse_is_done(db_session: Session, user_id: uuid.UUID):
    script = _script(db_session, user_id, terms=["SeatFlow"])  # PENDING
    _slide(db_session, script, 1, ["키워드"])

    assert pitch_service.stt_keyterm_candidates(db_session, script.pitch_id, script.id) == []


def test_candidates_ignore_script_of_another_pitch(db_session: Session, user_id: uuid.UUID):
    script = _script(db_session, user_id, parse_status=ScriptParseStatus.DONE, terms=["SeatFlow"])

    assert pitch_service.stt_keyterm_candidates(db_session, uuid.uuid4(), script.id) == []
