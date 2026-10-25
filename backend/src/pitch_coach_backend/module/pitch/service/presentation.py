"""발표자료(PDF) 업로드 · 조회."""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    MAX_PRESENTATION_BYTES,
    count_pdf_pages,
    file_size,
)
from pitch_coach_backend.module.pitch.adapter.storage import generate_presigned_url, upload
from pitch_coach_backend.module.pitch.dto import PresentationDetailDTO, UploadPresentationResultDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion
from pitch_coach_backend.module.pitch.exception import (
    InvalidPresentation,
    NonExistentPresentationVersion,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.service.pitch import KST


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

    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다.
    # 번호가 S3 키에도 들어가서, 잠그지 않으면 500 에 더해 앞서 올린 파일이 덮어쓰인다
    pitch_repository.lock_for_new_version(pitch_id)

    version = pitch_repository.next_presentation_version(pitch_id)
    presentation_key = upload(
        presentation_file,
        f"pitches/{pitch_id}/presentations/{version}.pdf",
        content_type="application/pdf",
    )

    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=presentation_key,
        page_count=page_count,
    )

    pitch_repository.save_presentation(presentation)
    db.commit()

    return UploadPresentationResultDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation.id,
        file_url = generate_presigned_url(presentation_key),
        page_count=page_count,
    )


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
        created_at=presentation_version.created_at.astimezone(KST).date()
    )
