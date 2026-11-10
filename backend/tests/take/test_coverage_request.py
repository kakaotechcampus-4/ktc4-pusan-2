"""`/takes/analyze` 요청의 장별 입력: 전사 · 장 길이 · STT 끊김 · 기록 없음 시간 · 대본 글."""

import asyncio
import copy

import pytest

from pitch_coach_backend.module.pitch.entity import ScriptSlide
from pitch_coach_backend.module.take.entity import TakeSlideEvent, TakeTranscriptSegment
from pitch_coach_backend.module.take.service import coverage as coverage_service
from tests.test_analysis_review_ownership import in_current_session, make_ready
from tests.test_take_ai_validation import coverage

GAPS = {
    "version": 1,
    "duration_ms": 9_000,
    "not_ok": [[2_000, 4_000]],
    "unobserved": [[7_000, 8_000]],
}


def _word(text: str, start_ms: int) -> dict:
    return {
        "word": text,
        "punctuated_word": text,
        "start_ms": start_ms,
        "end_ms": start_ms + 300,
        "confidence": 0.9,
    }


def _scoring(db, user_id, stt_gap_ranges):
    take = make_ready(db, user_id, "SCORING", stt_gap_ranges=stt_gap_ranges)
    for number in (1, 2, 3):
        db.add(
            ScriptSlide(
                script_version_id=take.script_version_id,
                slide_number=number,
                full_content=f"{number}장 대본",
                # 3장은 평가 기준이 없다. 대본 글은 그래도 싣는다
                rubric={"rubric_id": f"rubric-{number}"} if number < 3 else None,
            )
        )
    db.add_all(
        TakeSlideEvent(take_id=take.id, slide_number=number, started_ms=started_ms)
        for number, started_ms in [(1, 0), (2, 3_000), (1, 6_000)]
    )
    db.add(
        TakeTranscriptSegment(
            take_id=take.id,
            seq=0,
            stt_session_no=0,
            start_ms=0,
            end_ms=7_300,
            transcript="안녕 둘째 다시",
            words=[_word("안녕", 500), _word("둘째", 3_500), _word("다시", 7_000)],
            confidence=0.9,
            speech_final=True,
        )
    )
    db.commit()
    return take


def _analyze(monkeypatch, db, take, reply: dict) -> list[dict]:
    in_current_session(monkeypatch, db)
    bodies = []

    async def respond(body, request_id):
        bodies.append(copy.deepcopy(body))
        return reply

    monkeypatch.setattr(coverage_service, "request_analyze", respond)
    asyncio.run(coverage_service.analyze_script_coverage(take.id))
    return bodies


def test_analyze_and_partial_retry_send_slide_time_gaps_and_script(
    db_session, user_id, monkeypatch
) -> None:
    take = _scoring(db_session, user_id, GAPS)
    reply = coverage(take.id)
    reply["failed"] = [
        {"slide_number": 2, "error": {"code": "LLM_TIMEOUT", "message": "시간 초과"}}
    ]

    first, retry = _analyze(monkeypatch, db_session, take, reply)

    assert first["slides"] == [
        # 1장은 [0, 3000) + [6000, 9000). 끊김 [2000, 3000), 기록 없음 [7000, 8000)
        {
            "slide_number": 1,
            "stt": "안녕 다시",
            "duration_ms": 6_000,
            "stt_gap_ms": 1_000,
            "stt_unobserved_ms": 1_000,
            "script": "1장 대본",
        },
        {
            "slide_number": 2,
            "stt": "둘째",
            "duration_ms": 3_000,
            "stt_gap_ms": 1_000,
            "stt_unobserved_ms": 0,
            "script": "2장 대본",
        },
        # 한 번도 보이지 않은 장
        {
            "slide_number": 3,
            "stt": "",
            "duration_ms": 0,
            "stt_gap_ms": 0,
            "stt_unobserved_ms": 0,
            "script": "3장 대본",
        },
    ]
    assert first["rubrics"] == [{"rubric_id": "rubric-1"}, {"rubric_id": "rubric-2"}]
    assert first["slide_numbers"] is None
    # 부분 재시도도 같은 값을 싣는다
    assert retry == {**first, "slide_numbers": [2]}


@pytest.mark.parametrize("gaps", [None, {"version": 2}], ids=["before-this-column", "unknown"])
def test_unknown_stt_gaps_send_null_times(db_session, user_id, monkeypatch, gaps) -> None:
    take = _scoring(db_session, user_id, gaps)

    [body] = _analyze(monkeypatch, db_session, take, coverage(take.id))

    assert [
        (slide["duration_ms"], slide["stt_gap_ms"], slide["stt_unobserved_ms"], slide["script"])
        for slide in body["slides"]
    ] == [
        (None, None, None, "1장 대본"),
        (None, None, None, "2장 대본"),
        (None, None, None, "3장 대본"),
    ]
