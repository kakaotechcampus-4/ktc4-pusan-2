"""발표를 지운 뒤 S3 에 남은 파일 정리. 지울 경로를 DB 에 남겨 성공할 때까지 다시 한다.

    발표 삭제 트랜잭션 ──▶ pending_file_deletions 에 경로 기록 (같은 커밋)
    응답 뒤 백그라운드 ──▶ S3 삭제 ──▶ 성공하면 기록을 지운다 / 실패하면 남긴다
    주기 작업          ──▶ 남은 기록을 다시 지운다 (`module/pitch/file_cleanup_sweep.py`)

S3 삭제는 없는 파일이어도 성공이라 몇 번을 해도 같다. 실패는 밖으로 던지지 않는다 — 삭제 요청은
이미 성공했고 받을 사람이 없다. 남은 기록은 `attempts` 와 로그의 pitch 경로로 찾는다.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch.adapter.storage import delete_prefix
from pitch_coach_backend.module.pitch.repository import PitchRepository

logger = logging.getLogger(__name__)

# 방금 남긴 기록은 응답 뒤 백그라운드 삭제가 하고 있다. 겹쳐 지우지 않게 이만큼 지난 것만 다시 한다
RETRY_AFTER = timedelta(minutes=1)
# 한 번에 다시 하는 경로 수. 남은 것은 다음 주기에 한다
RETRY_BATCH = 100


def pitch_prefix(pitch_id: uuid.UUID) -> str:
    return f"pitches/{pitch_id}/"


def reserve(db: Session, pitch_ids: list[uuid.UUID]) -> None:
    """발표 파일을 지울 일을 남긴다. commit 은 호출자가 한다."""
    PitchRepository(db).add_file_deletions([pitch_prefix(pitch_id) for pitch_id in pitch_ids])


def delete_files(prefix: str, session_factory: Callable[[], Session] = SessionLocal) -> bool:
    """경로 아래 파일을 지우고 기록을 뺀다. 실패하면 기록을 남기고 False 를 돌려준다."""
    try:
        delete_prefix(prefix)
    except Exception:
        logger.exception("pitch 파일 삭제 실패 prefix=%s", prefix)
        _record(session_factory, prefix, failed=True)
        return False
    _record(session_factory, prefix, failed=False)
    return True


def retry_pending(session_factory: Callable[[], Session] = SessionLocal) -> int:
    """남은 기록을 다시 지운다. 지운 경로 수를 돌려준다."""
    db = session_factory()
    try:
        prefixes = PitchRepository(db).list_file_deletions(
            datetime.now(UTC) - RETRY_AFTER, RETRY_BATCH
        )
    finally:
        db.close()
    return sum(delete_files(prefix, session_factory) for prefix in prefixes)


def _record(session_factory: Callable[[], Session], prefix: str, *, failed: bool) -> None:
    """결과를 기록에 반영한다. 못 바꿔도 던지지 않는다 — 기록이 남으면 다음 주기에 다시 한다."""
    db = session_factory()
    try:
        repository = PitchRepository(db)
        if failed:
            repository.count_file_deletion_failure(prefix)
        else:
            repository.remove_file_deletion(prefix)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("pitch 파일 삭제 기록을 바꾸지 못함 prefix=%s", prefix)
    finally:
        db.close()
