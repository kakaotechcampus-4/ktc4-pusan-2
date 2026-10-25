"""대본 전달도 분석 (`SCORING`).

/takes/analyze · 장별 재시도 · /takes/rescore 병합 · 다음 분석 단계 정하기.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service import script as script_service
from pitch_coach_backend.module.take.ai_response import CoverageCallError, validate_coverage_reply
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.repository import TakeRepository

logger = logging.getLogger(__name__)


COVERAGE_KIND = "SCRIPT_COVERAGE"

COVERAGE_CANDIDATE_KIND = "SCRIPT_COVERAGE_PENDING"

COVERAGE_RETRYABLE_FAILED_CODES = frozenset(
    {"LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE"}
)

RUBRIC_WAIT = timedelta(minutes=5)


async def _post_ai(path: str, body: dict, request_id: str, timeout_sec: float) -> dict:
    try:
        async with asyncio.timeout(timeout_sec):
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(
                    f"{settings.ai_base_url}{path}", json=body, headers={"X-Request-ID": request_id}
                )
    except (httpx.HTTPError, TimeoutError) as e:
        raise CoverageCallError(type(e).__name__, retryable=True) from e
    if response.status_code != 200:
        raise CoverageCallError(
            f"status={response.status_code}",
            retryable=response.status_code >= 500 or response.status_code in (408, 429),
        )
    try:
        reply = response.json()
    except ValueError as exc:
        raise CoverageCallError("invalid coverage JSON", retryable=False) from exc
    return validate_coverage_reply(reply, body["take_id"])


async def request_analyze(body: dict, request_id: str) -> dict:
    return await _post_ai("/takes/analyze", body, request_id, 130.0)


async def request_rescore(body: dict, request_id: str) -> dict:
    return await _post_ai("/takes/rescore", body, request_id, 10.0)


def slide_stt(
    slide_numbers: Iterable[int],
    slide_events: Iterable[tuple[int, int]],
    words: Iterable[tuple[str, int]]) -> list[dict]:
    events = sorted(slide_events, key=lambda event: event[1])
    spoken: dict[int, list[str]] = {number: [] for number in sorted(slide_numbers)}
    for text, start_ms in sorted(words, key=lambda word: word[1]):
        current = None
        for slide_number, started_ms in events:
            if started_ms > start_ms:
                break
            current = slide_number
        if current in spoken and text:
            spoken[current].append(text)
    return [{"slide_number": number, "stt": " ".join(texts)} for number, texts in spoken.items()]


def retry_targets(failed: Iterable[Mapping[str, Any]]) -> list[int]:
    return sorted({
        item["slide_number"]
        for item in failed
        if (item.get("error") or {}).get("code") in COVERAGE_RETRYABLE_FAILED_CODES
    })


def merge_retry(
    base: Mapping[str, Any], retry: Mapping[str, Any], targets: Iterable[int]
) -> tuple[list[dict], list[dict]] | None:
    retried = set(targets)
    new_evaluations = [e for e in retry["evaluations"] if e["slide_number"] in retried]
    if not new_evaluations:
        return None
    succeeded = {e["slide_number"] for e in new_evaluations}
    evaluations = [e for e in base["evaluations"] if e["slide_number"] not in succeeded]
    failed = [f for f in base["failed"] if f["slide_number"] not in retried]
    failed += [f for f in retry["failed"] if f["slide_number"] in retried]
    return (
        sorted(evaluations + new_evaluations, key=lambda e: e["slide_number"]),
        sorted(failed, key=lambda f: f["slide_number"]),
    )


def speak_accuracy(payload: Mapping[str, Any]) -> float | None:
    value = (payload.get("take_scores") or {}).get("content_coverage")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return math.ceil(value * 100)


def _coverage_open(take: Take | None) -> bool:
    return (
        take is not None
        and take.status == "ANALYZING"
        and take.analysis_stage == "SCORING"
        and take.analysis_hold_reason is None
    )


def _lock_open_take(take_repository: TakeRepository, take_id: uuid.UUID) -> Take | None:
    take = take_repository.lock_take(take_id)
    return take if _coverage_open(take) else None


def _mark_retry_used(db: Session, take_id: uuid.UUID, column: str) -> bool:
    take = _lock_open_take(TakeRepository(db), take_id)
    if take is None or getattr(take, column):
        db.rollback()
        return False
    setattr(take, column, True)
    db.commit()
    return True


def _save_confirmed(db: Session, take_id: uuid.UUID, reply: dict) -> dict | None:
    validate_coverage_reply(reply, take_id)
    take_repository = TakeRepository(db)
    payload = {key: value for key, value in reply.items() if key != "meta"}
    if _lock_open_take(take_repository, take_id) is None or not take_repository.insert_ai_result(
        take_id, COVERAGE_KIND, payload, reply.get("meta")
    ):
        db.rollback()
        return None
    take_repository.upsert_summary_speak_accuracy(take_id, speak_accuracy(payload))
    db.commit()
    return payload


def _save_candidate(db: Session, take_id: uuid.UUID, candidate: dict) -> bool:
    take_repository = TakeRepository(db)
    take = _lock_open_take(take_repository, take_id)
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND) if take else None
    if (
        confirmed is None
        or confirmed.revision != candidate["base_revision"]
        or str(take.script_version_id) != candidate["script_version_id"]
        or not take_repository.insert_ai_result(take_id, COVERAGE_CANDIDATE_KIND, candidate, None)
    ):
        db.rollback()
        return False
    db.commit()
    return True


def _apply_merged(db: Session, take_id: uuid.UUID, base_revision: int, reply: dict) -> bool:
    validate_coverage_reply(reply, take_id)
    take_repository = TakeRepository(db)
    payload = {key: value for key, value in reply.items() if key != "meta"}
    if _lock_open_take(take_repository, take_id) is None or not take_repository.replace_ai_result(
        take_id, COVERAGE_KIND, base_revision, payload, reply.get("meta")
    ):
        db.rollback()
        return False
    take_repository.upsert_summary_speak_accuracy(take_id, speak_accuracy(payload))
    take_repository.delete_ai_result(take_id, COVERAGE_CANDIDATE_KIND)
    db.commit()
    return True


def _hold_coverage(db: Session, take_id: uuid.UUID) -> None:
    take = _lock_open_take(TakeRepository(db), take_id)
    if take is None:
        db.rollback()
        return
    take.analysis_hold_reason = "COVERAGE_RESCORE_FAILED"
    db.commit()


def _conclude_coverage(db: Session, take_id: uuid.UUID) -> str:
    take_repository = TakeRepository(db)
    take = take_repository.lock_take(take_id)
    if take is None or take.status != "ANALYZING" or take.analysis_stage != "SCORING":
        db.rollback()
        return "SKIPPED"
    if take.analysis_hold_reason is not None or take_repository.get_ai_result(
        take_id, COVERAGE_CANDIDATE_KIND
    ):
        db.rollback()
        return "HELD"
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND)
    if confirmed is not None and confirmed.payload.get("confirmations"):
        take_repository.set_analysis_stage(take_id, "AWAITING_CONFIRMATION")
        db.commit()
        return "AWAITING_CONFIRMATION"
    take_repository.set_analysis_stage(take_id, "REVIEWING")
    db.commit()
    return "NO_COVERAGE" if confirmed is None else "READY_FOR_REVIEW"


async def _in_session[T](
    session_factory: Callable[[], Session], fn: Callable[..., T], *args: Any
) -> T:
    def call() -> T:
        db = session_factory()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await run_in_threadpool(call)


async def _merge_candidate(
    session_factory: Callable[[], Session], take_id: uuid.UUID, rubrics: list[dict],
    candidate: dict) -> None:
    body = {
        "take_id": str(take_id),
        "evaluations": candidate["evaluations"],
        "rubrics": rubrics,
        "answers": [],
        "failed": candidate["failed"],
    }
    request_id = uuid.uuid4().hex
    try:
        reply = await request_rescore(body, request_id)
    except CoverageCallError as e:
        logger.warning("take %s: coverage merge rescore failed: %s", take_id, e)
        if not e.retryable or not await _in_session(
            session_factory, _mark_retry_used, take_id, "coverage_rescore_retry_used"
        ):
            await _in_session(session_factory, _hold_coverage, take_id)
            return
        try:
            reply = await request_rescore(body, request_id)
        except CoverageCallError as retry_error:
            logger.warning("take %s: coverage merge rescore retry failed: %s", take_id, retry_error)
            await _in_session(session_factory, _hold_coverage, take_id)
            return
    if not await _in_session(
        session_factory, _apply_merged, take_id, candidate["base_revision"], reply
    ):
        logger.info("take %s: stale coverage merge result discarded", take_id)


async def _retry_failed_slides(
    session_factory: Callable[[], Session], take_id: uuid.UUID, body: dict,
    script_version_id: str, confirmed: dict, revision: int, retry_used: bool) -> None:
    targets = retry_targets(confirmed.get("failed", []))
    if not targets or retry_used or not await _in_session(
        session_factory, _mark_retry_used, take_id, "coverage_retry_used"
    ):
        return
    try:
        reply = await request_analyze({**body, "slide_numbers": targets}, uuid.uuid4().hex)
    except CoverageCallError as e:
        logger.warning("take %s: coverage partial retry failed: %s", take_id, e)
        return
    merged = merge_retry(confirmed, reply, targets)
    if merged is None:
        return
    candidate = {
        "script_version_id": script_version_id,
        "base_revision": revision,
        "evaluations": merged[0],
        "failed": merged[1],
        "retry_meta": reply.get("meta"),
    }
    if await _in_session(session_factory, _save_candidate, take_id, candidate):
        await _merge_candidate(session_factory, take_id, body["rubrics"], candidate)


async def _first_analyze(
    session_factory: Callable[[], Session],
    take_id: uuid.UUID,
    body: dict,
    script_version_id: str,
    retry_used: bool) -> None:
    # 전체 재시도를 보낸 뒤 확정본을 저장하기 전에 재시작했을 수 있다.
    # 이미 쓴 한도를 새 최초 호출로 취급하지 않는다.
    if retry_used:
        return
    request_id = uuid.uuid4().hex
    try:
        reply = await request_analyze(body, request_id)
    except CoverageCallError as e:
        logger.warning("take %s: coverage analyze failed: %s", take_id, e)
        if (
            not e.retryable
            or retry_used
            or not await _in_session(
                session_factory, _mark_retry_used, take_id, "coverage_retry_used"
            )
        ):
            return
        try:
            reply = await request_analyze(body, request_id)
        except CoverageCallError as retry_error:
            logger.warning(
                "take %s: coverage analyze retry failed: %s", take_id, retry_error
            )
            return
        await _in_session(session_factory, _save_confirmed, take_id, reply)
        return
    confirmed = await _in_session(session_factory, _save_confirmed, take_id, reply)
    if confirmed is not None:
        await _retry_failed_slides(
            session_factory, take_id, body, script_version_id, confirmed, 0, retry_used
        )


def _waiting_for_rubric(db: Session, take: Take) -> bool:
    if not script_service.rubric_pending(db, take.script_version_id):
        return False
    started_at = take.analysis_stage_at
    return started_at is not None and datetime.now(UTC) < started_at + RUBRIC_WAIT


def _load_coverage(db: Session, take_id: uuid.UUID) -> dict | None:
    take_repository = TakeRepository(db)
    take = take_repository.get_take(take_id)
    if not _coverage_open(take):
        return None
    confirmed = take_repository.get_ai_result(take_id, COVERAGE_KIND)
    candidate = take_repository.get_ai_result(take_id, COVERAGE_CANDIDATE_KIND)
    if confirmed is None and candidate is None and _waiting_for_rubric(db, take):
        return {"rubric_pending": True}
    slides = PitchRepository(db).get_slides(take.script_version_id)
    return {
        "rubric_pending": False,
        "body": {
            "take_id": str(take_id),
            "slides": slide_stt(
                [slide.slide_number for slide in slides],
                take_repository.get_slide_events(take_id),
                take_repository.get_transcript_words(take_id),
            ),
            "rubrics": [slide.rubric for slide in slides if slide.rubric is not None],
            "slide_numbers": None,
        },
        "script_version_id": str(take.script_version_id),
        "retry_used": take.coverage_retry_used,
        "rescore_retry_used": take.coverage_rescore_retry_used,
        "confirmed": (confirmed.payload, confirmed.revision) if confirmed else None,
        "candidate": candidate.payload if candidate else None,
    }


async def analyze_script_coverage(
    take_id: uuid.UUID, session_factory: Callable[[], Session] = SessionLocal
) -> str:
    job = await _in_session(session_factory, _load_coverage, take_id)
    if job is None:
        return "SKIPPED"
    if job["rubric_pending"]:
        return "RUBRIC_PENDING"
    body = job["body"]
    if job["candidate"] is not None:
        if job["rescore_retry_used"]:
            await _in_session(session_factory, _hold_coverage, take_id)
        else:
            await _merge_candidate(session_factory, take_id, body["rubrics"], job["candidate"])
    elif job["confirmed"] is None:
        await _first_analyze(
            session_factory, take_id, body, job["script_version_id"], job["retry_used"]
        )
    else:
        await _retry_failed_slides(
            session_factory, take_id, body, job["script_version_id"], *job["confirmed"],
            job["retry_used"],
        )
    return await _in_session(session_factory, _conclude_coverage, take_id)
