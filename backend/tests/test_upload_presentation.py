
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock

from fastapi import UploadFile
from pitch_coach_backend.module.pitch.dto import PitchDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch import service
import pytest
from sqlalchemy.orm import Session
import uuid

@pytest.fixture
def upload_mock(monkeypatch) -> MagicMock:
    mock = MagicMock(side_effect=lambda file, key:key)
    monkeypatch.setattr(service, "upload", mock) # upload 함수를 mock으로 대체
    monkeypatch.setattr(service, "generate_presigned_url", lambda key: f"https://fake/{key}") # generate_presigned_url 함수를 람다함수로 생성하도록 대체
    return mock


def _make_pitch(db: Session, user_id: uuid.UUID, title: str = "기존 발표") -> uuid.UUID:
    return service.add_pitch_service(
        db,
        user_id,
        PitchDTO(title=title, time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )

def test_upload_presentation_service(db_session: Session, user_id: uuid.UUID, upload_mock: MagicMock):
    # Given
    pitch_id = _make_pitch(db_session, user_id)
    pdf_real_file = UploadFile(filename="dummy_file.pdf", file=BytesIO(b"%PDF-1.4\n%Dummy PDF content"))
    dto = UploadPresentationDTO(presentation_file=pdf_real_file)

    result = service.upload_presentation_service(db_session, pitch_id, dto)

    expected_file_key = f"pitches/{pitch_id}/presentations/1.pdf"
    upload_mock.assert_called_once_with(dto.presentation_file, expected_file_key)
    assert result.pitch_id == pitch_id
    assert result.file_url == f"https://fake/{expected_file_key}"

