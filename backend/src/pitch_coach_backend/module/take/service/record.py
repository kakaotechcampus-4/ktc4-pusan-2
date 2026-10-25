"""실시간 기록 저장 — 전사 커서 · 확정 전사 · 1초 원자료 · 문장 끝 신호."""

import logging
import uuid

from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO, TranscriptSegmentCreateDTO
from pitch_coach_backend.module.take.entity import TakeTranscriptSegment
from pitch_coach_backend.module.take.exception import DuplicateTranscriptSegment, NonExistentTake
from pitch_coach_backend.module.take.repository import TakeRepository

logger = logging.getLogger(__name__)


def transcript_cursor(db: Session, take_id: uuid.UUID) -> tuple[int, int]:
    """저장된 마지막 (seq, stt_session_no). 스트림이 새로 만들어질 때 번호를 이어 받는다."""
    return TakeRepository(db).last_transcript_cursor(take_id)


def append_transcript_segment(
    db: Session, take_id: uuid.UUID, segment_dto: TranscriptSegmentCreateDTO
) -> uuid.UUID:
    """final 구간 하나를 저장하고 커밋한다. 짧은 트랜잭션 하나로 끝난다.

    Take 가 그 사이 지워졌으면 `NonExistentTake`. 스트림은 이걸 보고 더 돌지 않는다.
    같은 번호가 이미 있으면 `DuplicateTranscriptSegment`. 스트림은 저장된 것으로 보고 넘어간다.
    """
    segment = TakeTranscriptSegment(
        take_id=take_id,
        seq=segment_dto.seq,
        stt_session_no=segment_dto.stt_session_no,
        start_ms=segment_dto.start_ms,
        end_ms=segment_dto.end_ms,
        transcript=segment_dto.transcript,
        words=[w.model_dump() for w in segment_dto.words],
        confidence=segment_dto.confidence,
        speech_final=segment_dto.speech_final,
        final_at_ms=segment_dto.final_at_ms,
    )
    try:
        saved = TakeRepository(db).save_transcript_segment(segment)
    except IntegrityError as e:
        # 이 표의 외래 키는 take_id 하나, UNIQUE 는 (take_id, seq) 하나다
        if isinstance(e.orig, ForeignKeyViolation):
            raise NonExistentTake() from e
        if isinstance(e.orig, UniqueViolation):
            raise DuplicateTranscriptSegment() from e
        raise
    db.commit()
    return saved.id


def append_metric_seconds(
    db: Session, take_id: uuid.UUID, rows: list[MetricSecondCreateDTO]
) -> None:
    """1초 원자료 여러 행을 한 번에 저장하고 커밋한다. 이미 있는 1초는 건너뛴다.

    Take 가 그 사이 지워졌으면 `NonExistentTake`. 스트림은 이걸 보고 남은 행을 버린다.
    Take 결과를 이미 만든 Take 면 넣지 않는다 (`TakeRepository.raw_data_open`).
    """
    repository = TakeRepository(db)
    open_ = repository.raw_data_open(take_id)
    if open_ is None:
        raise NonExistentTake()
    if not open_:
        db.rollback()
        logger.warning(
            "Take 결과를 만든 뒤라 1초 원자료를 넣지 않는다 take=%s count=%d", take_id, len(rows)
        )
        return
    try:
        repository.save_metric_seconds(take_id, [row.model_dump() for row in rows])
    except IntegrityError as e:
        # 이 표의 외래 키는 take_id 하나다. 같은 1초는 ON CONFLICT 로 건너뛰어 여기 오지 않는다
        if isinstance(e.orig, ForeignKeyViolation):
            raise NonExistentTake() from e
        raise
    db.commit()


def stored_utterance_ends(db: Session, take_id: uuid.UUID) -> list[int]:
    """Take 에 저장된 문장 끝 신호. 앱이 Take 도중에 다시 떴을 때 이어 받는다."""
    return TakeRepository(db).utterance_ends(take_id)


def save_utterance_ends(db: Session, take_id: uuid.UUID, utterance_ends: list[int]) -> bool:
    """진행 중인 Take 의 문장 끝 신호를 적어 둔다 (앱을 내릴 때). 끝난 Take 는 그대로 둔다."""
    saved = TakeRepository(db).save_utterance_ends(take_id, utterance_ends)
    db.commit()
    return saved
