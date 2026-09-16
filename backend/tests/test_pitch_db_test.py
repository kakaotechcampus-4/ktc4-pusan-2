import uuid
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch import service
from pitch_coach_backend.module.pitch.dto import PitchDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion

# user-id는 conftest.py에서 fixture를 이용, 공통으로 받아옴

# 테스트할 pitch_id 생성
@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return service.add_pitch_service(
        db_session,
        user_id,
        PitchDTO(title="기존 발표", time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )


# S3 upload Mocking
@pytest.fixture
def upload_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(side_effect=lambda file, key: key)
    monkeypatch.setattr(service, "upload", mock)
    return mock


# pitch 생성 테스트
def test_add_pitch_persists_the_row_and_returns_its_id(
    db_session: Session, user_id: uuid.UUID
) -> None:
    dto = PitchDTO(title="중간 발표", time_limit_sec=420, presentation_date=date(2026, 5, 20))

    new_id = service.add_pitch_service(db_session, user_id, dto)

    saved = db_session.get(Pitch, new_id)
    assert saved is not None
    assert saved.user_id == user_id
    assert saved.title == "중간 발표"
    assert saved.time_limit_sec == 420
    assert saved.presentation_date == date(2026, 5, 20)

# pitch 수정 테스트
def test_update_pitch_overwrites_every_editable_field(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    dto = PitchDTO(title="수정된 발표", time_limit_sec=600, presentation_date=date(2026, 7, 7))

    returned = service.update_pitch_service(db_session, pitch_id, dto)

    assert returned == pitch_id
    saved = db_session.get(Pitch, pitch_id)
    assert saved is not None
    assert saved.title == "수정된 발표"
    assert saved.time_limit_sec == 600
    assert saved.presentation_date == date(2026, 7, 7)

# pitch 삭제 테스트
def test_delete_pitch_removes_the_row_and_returns_its_id(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    returned = service.delete_pitch_service(db_session, pitch_id)

    assert returned == pitch_id
    assert db_session.get(Pitch, pitch_id) is None

# pitch presentation 업로드 테스트
def test_upload_presentation_persists_version_one(
    db_session: Session, pitch_id: uuid.UUID, upload_mock: MagicMock
) -> None:
    dto = UploadPresentationDTO(
        presentation_file=UploadFile(file=BytesIO(b"fake-bytes"), filename="deck.pdf"),
        description="초안",
    )

    presentation_id = service.upload_presentation_service(db_session, pitch_id, dto)

    expected_key = f"pitches/{pitch_id}/presentations/1.pdf"
    upload_mock.assert_called_once_with(dto.presentation_file, expected_key)
    saved = db_session.get(PresentationVersion, presentation_id)
    assert saved is not None
    assert saved.pitch_id == pitch_id
    assert saved.version == 1
    assert saved.description == "초안"
    assert saved.file_url == expected_key
