"""발표자료(PDF) 업로드 · 조회."""

import logging
import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    MAX_PRESENTATION_BYTES,
    count_pdf_pages,
    file_size,
    render_first_page,
)
from pitch_coach_backend.module.pitch.adapter.storage import (
    delete,
    generate_presigned_url,
    upload,
    upload_bytes,
)
from pitch_coach_backend.module.pitch.dto import PresentationDetailDTO, UploadPresentationResultDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion
from pitch_coach_backend.module.pitch.exception import (
    InvalidPresentation,
    NonExistentPitch,
    NonExistentPresentationVersion,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service.pitch import KST

logger = logging.getLogger(__name__)


def upload_presentation_service(db: Session, pitch_id: uuid.UUID, upload_dto):
    pitch_repository = PitchRepository(db)
    presentation_file = upload_dto.presentation_file

    # 확장자 · MIME 은 클라이언트가 정하는 값이라 보지 않고 직접 연다. 잠그기 전에 해서 쪽수를
    # 세는 동안 같은 pitch 의 다른 요청을 막지 않는다
    if file_size(presentation_file.file) > MAX_PRESENTATION_BYTES:
        raise InvalidPresentation()
    page_count = count_pdf_pages(presentation_file.file)
    if page_count is None:
        raise InvalidPresentation()
    thumbnail = render_first_page(presentation_file.file)
    presentation_id = uuid.uuid7()

    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다.
    # 번호가 S3 키에도 들어가서, 잠그지 않으면 500 에 더해 앞서 올린 파일이 덮어쓰인다.
    # 소유권을 확인한 뒤 쪽수를 세는 사이에 발표가 지워졌으면 S3 에 올리기 전에 404 로 끝낸다
    presentation_id = uuid.uuid7()
    presentation_key = upload(
        presentation_file,
        f"pitches/{pitch_id}/presentations/{presentation_id}.pdf",
        content_type="application/pdf",
    )
    thumbnail_key = None
    if thumbnail is not None:
        try:
            thumbnail_key = upload_bytes(
                thumbnail, f"pitches/{pitch_id}/presentations/{presentation_id}.webp", "image/webp"
            )
        except Exception as e:
            logger.warning("발표자료 썸네일을 올리지 못했다 pitch=%s: %s", pitch_id, e)

    # 여기부터 실패하면 S3 에만 파일이 남는다. 버전을 남기지 못했으면 올린 파일을 지운다
    try:
        # 조회 URL 은 커밋 전에 만든다. 커밋한 뒤에 실패하면 사용자는 오류를 받는데 버전은 생겨서
        # 다시 올릴 때 버전이 하나 더 생긴다
        file_url = generate_presigned_url(presentation_key)
        presentation = PresentationVersion(
            id=presentation_id,
            pitch_id=pitch_id,
            version=version,
            file_key=presentation_key,
            page_count=page_count,
            thumbnail_key=thumbnail_key,
        )
        pitch_repository.save_presentation(presentation)
    except Exception:
        db.rollback()
        _discard_upload(db, presentation_id, presentation_key, thumbnail_key)
        raise
    try:
        db.commit()
    except Exception:
        db.rollback()
        # 커밋 응답만 잃었을 수 있다 (커밋 뒤 연결이 끊김). 버전 행이 없을 때만 지운다
        _discard_upload(db, presentation_id, presentation_key, thumbnail_key)
        raise

    return UploadPresentationResultDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation.id,
        file_url=file_url,
        page_count=page_count,
    )


def _discard_upload(
    db: Session, presentation_id: uuid.UUID, key: str, thumbnail_key: str | None
) -> None:
    """
    presentation_id 로 버전 행이 없을 때만 이 요청이 올린 PDF · 썸네일을 지운다. 
    커밋 응답만 잃었으면 행이 이미 있는 상태라 남긴다
    """
    try:
        exists = PitchRepository(db).has_presentation(presentation_id)
    except Exception:
        logger.warning(
            "발표자료 업로드 실패 후 버전 행 확인 중 오류 발생 presentation_id=%s, keys=%s",
            presentation_id,
            [k for k in (key, thumbnail_key) if k is not None],
            exc_info=True,
        )
        return
    finally:
        db.rollback() # 성공 시 트랜잭션 정리용, 실패 시 잠금 해제용

    if not exists:
        for k in (key, thumbnail_key):
            if k is None:
                continue
            try:
                delete(k)
            except Exception:
                logger.warning("발표자료 업로드 실패 후 삭제 중 오류 발생 key=%s", k, exc_info=True)


# 각 발표자료 버전의 상세 정보.
def get_presentation_detail(db: Session, pitch_id: uuid.UUID, presentation_version_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    presentation_version = pitch_repository.get_presentation_detail(
        pitch_id, presentation_version_id
    )

    if not presentation_version:
        raise NonExistentPresentationVersion()

    return PresentationDetailDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation_version.id,
        version=presentation_version.version,
        file_url=generate_presigned_url(presentation_version.file_key),
        page_count=presentation_version.page_count,
        description=presentation_version.description,
        created_at=presentation_version.created_at.astimezone(KST).date(),
    )
