"""First Take 튜토리얼 샘플 발표.

샘플 발표는 사용자마다 하나다. 준비된 PDF 와 미리 만든 AI 결과(`adapter/sample.py`)를 복사해
만들어서 AI 를 부르지 않는다. 대본 파싱 · 장별 평가 기준은 끝난 상태로 넣고, 첫 Take 코칭 계획도
한 행으로 넣는다. 그 뒤 장치 점검 · Take · 리포트는 일반 발표와 같다. 튜토리얼 리포트는 계정당
한 번이다 (`take/service/take.py` 의 Take 생성 · `take/service/review.py` 의 리뷰 저장).
"""

import copy
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter import sample as sample_files
from pitch_coach_backend.module.pitch.adapter.sample import FirstTakeSample
from pitch_coach_backend.module.pitch.adapter.storage import presentation_key, upload_bytes
from pitch_coach_backend.module.pitch.dto import FirstTakeDTO
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptRubricStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.exception import StorageError, TutorialPitchBusy
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service.presentation import discard_upload
from pitch_coach_backend.module.user import service as user_service

logger = logging.getLogger(__name__)


def get_or_create_first_take(db: Session, user_id: uuid.UUID) -> FirstTakeDTO:
    """사용자의 샘플 발표를 준다. 없으면 만든다. 여러 탭에서 동시에 불러도 하나만 생긴다.

    튜토리얼 리포트를 이미 받았으면 새로 만들지 않는다 — 만들어도 Take 를 시작할 수 없고(409
    `TUTORIAL_REPORT_USED`) 파일만 또 올라간다. 그대로 있는 샘플 발표는 기록을 보라고 준다.
    """
    pitch_repository = PitchRepository(db)
    report_received = user_service.has_tutorial_report(db, user_id)
    pitch_id = pitch_repository.get_demo_pitch_id(user_id)
    if pitch_id is None and not report_received:
        pitch_id = _create(db, pitch_repository, user_id, sample_files.first_take())
    return FirstTakeDTO(pitch_id=pitch_id, report_received=report_received)


def _create(
    db: Session, pitch_repository: PitchRepository, user_id: uuid.UUID, sample: FirstTakeSample
) -> uuid.UUID:
    pitch_id = pitch_repository.insert_demo_pitch(
        Pitch(
            user_id=user_id,
            title=sample.title,
            time_limit_sec=sample.time_limit_sec,
            presentation_date=None,
            upper_deviation=sample.upper_deviation,
            lower_deviation=sample.lower_deviation,
        )
    )
    if pitch_id is None:
        # 다른 요청이 먼저 만들어 커밋했다. 그 요청이 커밋할 때까지 기다린 뒤라 바로 읽힌다
        db.rollback()
        existing = pitch_repository.get_demo_pitch_id(user_id)
        if existing is None:
            raise TutorialPitchBusy()
        return existing

    # 샘플 발표 행을 넣은 요청만 올린다. 진 요청이 파일을 올렸다가 지우는 일이 없다
    presentation_id = uuid.uuid7()
    pdf_key = thumbnail_key = None
    try:
        pdf_key = upload_bytes(
            sample.pdf, presentation_key(pitch_id, presentation_id, "pdf"), "application/pdf"
        )
        try:
            thumbnail_key = upload_bytes(
                sample.thumbnail,
                presentation_key(pitch_id, presentation_id, "webp"),
                "image/webp",
            )
        except StorageError:
            # 원인은 storage 가 남겼다. 썸네일이 없어도 발표자료는 쓸 수 있다 (presentation.py)
            logger.warning("샘플 발표자료 썸네일을 올리지 못했다 pitch=%s", pitch_id)
        _add_materials(pitch_repository, pitch_id, presentation_id, pdf_key, thumbnail_key, sample)
        db.commit()
    except Exception:
        db.rollback()
        # 커밋 응답만 잃었으면 발표자료 행이 있어 남긴다. 행이 없으면 올린 파일을 지운다
        if pdf_key is not None:
            discard_upload(db, presentation_id, pdf_key, thumbnail_key)
        raise
    return pitch_id


def _add_materials(
    pitch_repository: PitchRepository,
    pitch_id: uuid.UUID,
    presentation_id: uuid.UUID,
    pdf_key: str,
    thumbnail_key: str | None,
    sample: FirstTakeSample,
) -> None:
    """발표자료 · 대본 · 장별 결과 · 첫 Take 코칭 계획을 넣는다. 버전은 모두 1이다."""
    now = datetime.now(UTC)
    pitch_repository.save_presentation(
        PresentationVersion(
            id=presentation_id,
            pitch_id=pitch_id,
            version=1,
            file_key=pdf_key,
            page_count=sample.page_count,
            thumbnail_key=thumbnail_key,
        )
    )
    script = pitch_repository.save_script(
        ScriptVersion(
            pitch_id=pitch_id,
            version=1,
            content=sample.content,
            parse_status=ScriptParseStatus.DONE,
            parse_requested_at=now,
            segmented=sample.segmented,
            terms=list(sample.terms),
            parse_meta=copy.deepcopy(sample.parse_meta),
            # 모든 장의 평가 기준이 있는 샘플만 쓴다 (dev/build-first-take-sample.py 가 확인한다)
            rubric_status=ScriptRubricStatus.DONE,
            rubric_requested_at=now,
        )
    )
    pitch_repository.add_slides(
        [
            ScriptSlide(
                script_version_id=script.id,
                slide_number=slide.slide_number,
                full_content=slide.full_content,
                highlights=copy.deepcopy(slide.highlights),
                keywords=list(slide.keywords),
                script_chars=slide.script_chars,
                expected_ms=slide.expected_ms,
                rubric=_rubric_for(slide.rubric, script.id),
                rubric_meta=copy.deepcopy(slide.rubric_meta),
            )
            for slide in sample.slides
        ]
    )
    pitch_repository.add_coaching_plan(
        CoachingPlan(
            pitch_id=pitch_id,
            source_take_id=None,
            script_version_id=script.id,
            target_ms=sample.plan.target_ms,
            min_ms=sample.plan.min_ms,
            max_ms=sample.plan.max_ms,
            requested_at=now,
            payload=copy.deepcopy(sample.plan.payload),
            meta=copy.deepcopy(sample.plan.meta),
        )
    )


def _rubric_for(rubric: dict[str, Any], script_version_id: uuid.UUID) -> dict[str, Any]:
    """평가 기준 meta 의 대본 식별자를 새 대본 버전으로 바꾼다. AI 가 요청의 script_version_id 를
    그대로 넣는 칸이고, 채점은 장 대본으로 만든 rubric_id 로 기준을 가리켜 이 값과 상관없다."""
    copied = copy.deepcopy(rubric)
    copied["meta"]["script_name"] = str(script_version_id)
    return copied
