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
    generate_presigned_url,
    upload,
    upload_bytes,
)
from pitch_coach_backend.module.pitch.adapter.storage import delete
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

    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다.
    # 번호가 S3 키에도 들어가서, 잠그지 않으면 500 에 더해 앞서 올린 파일이 덮어쓰인다.
    # 소유권을 확인한 뒤 쪽수를 세는 사이에 발표가 지워졌으면 S3 에 올리기 전에 404 로 끝낸다
    if not pitch_repository.lock_for_new_version(pitch_id):
        raise NonExistentPitch()

    version = pitch_repository.next_presentation_version(pitch_id)
    presentation_key = upload(
        presentation_file,
        f"pitches/{pitch_id}/presentations/{version}.pdf",
        content_type="application/pdf",
    )
    thumbnail_key = None
    if thumbnail is not None:
        try:
            thumbnail_key = upload_bytes(
                thumbnail, f"pitches/{pitch_id}/presentations/{version}.webp", "image/webp"
            )
        except Exception as e:
            logger.warning("발표자료 썸네일을 올리지 못했다 pitch=%s: %s", pitch_id, e)

    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=presentation_key,
        page_count=page_count,
        thumbnail_key=thumbnail_key,
    )

    pitch_repository.save_presentation(presentation)
    db.commit()

    return UploadPresentationResultDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation.id,
        file_url=file_url,
        page_count=page_count,
    )


def _discard_upload(db: Session, pitch_id: uuid.UUID, version: int, key: str) -> None:
    """롤백 뒤 같은 발표를 다시 잠그고, 버전 행이 없을 때만 올린 파일을 지운다.

    롤백하면 다음 업로드가 같은 버전 번호 · S3 키를 쓸 수 있다. 확인부터 삭제까지 잠가 그 요청의
    파일을 지우지 않게 한다. 커밋 응답만 잃었거나 다음 요청이 이미 같은 번호를 저장했으면 남긴다.
    확인도 못 하면 남기고 원래 실패를 가리지 않게 로그만 남긴다.
    """
    try:
        repository = PitchRepository(db)
        repository.lock_for_new_version(pitch_id)
        if not repository.has_presentation_version(pitch_id, version):
            delete(key)
    except Exception:
        logger.warning(
            "발표자료 업로드 실패 뒤 파일을 정리하지 못해 남긴다 pitch_id=%s version=%d",
            pitch_id,
            version,
        )
    finally:
        db.rollback()


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
