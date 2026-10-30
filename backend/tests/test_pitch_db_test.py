import uuid
from collections.abc import Callable
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
from pitch_coach_backend.module.pitch.service import pitch as pitch_service
from pitch_coach_backend.module.pitch.service import presentation as presentation_service
from pitch_coach_backend.module.take.entity import Take

# user-id는 conftest.py에서 fixture를 이용, 공통으로 받아옴

# 테스트할 pitch_id 생성
@pytest.fixture
def pitch_id(db_session: Session, user_id: uuid.UUID) -> uuid.UUID:
    return pitch_service.add_pitch_service(
        db_session,
        user_id,
        PitchSaveRequestDTO(
            title="기존 발표", time_limit_sec=300, presentation_date=date(2026, 3, 1)
            ),
    )


# S3 upload Mocking
@pytest.fixture
def upload_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(side_effect=lambda file, key, content_type=None: key)
    monkeypatch.setattr(presentation_service, "upload", mock)
    monkeypatch.setattr(presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}")
    return mock


# pitch 생성 테스트
def test_add_pitch_persists_the_row_and_returns_its_id(
    db_session: Session, user_id: uuid.UUID
) -> None:
    dto = PitchSaveRequestDTO(
        title="중간 발표", time_limit_sec=420, presentation_date=date(2026, 5, 20)
        )

    new_id = pitch_service.add_pitch_service(db_session, user_id, dto)

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
    dto = PitchSaveRequestDTO(
        title="수정된 발표", time_limit_sec=600, presentation_date=date(2026, 7, 7))

    returned = pitch_service.update_pitch_service(db_session, pitch_id, dto)

    assert returned == pitch_id
    saved = db_session.get(Pitch, pitch_id)
    assert saved is not None
    assert saved.title == "수정된 발표"
    assert saved.time_limit_sec == 600
    assert saved.presentation_date == date(2026, 7, 7)

# pitch 삭제 테스트
def test_delete_pitch_removes_the_row_and_returns_its_take_ids(
    db_session: Session, pitch_id: uuid.UUID
) -> None:
    # 지운 Take 의 실시간 STT 를 호출자가 끊으려면 CASCADE 로 사라진 Take 의 id 가 필요하다
    presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key="deck.pdf")
    script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본")
    db_session.add_all([presentation, script])
    db_session.flush()
    take = Take(
        pitch_id=pitch_id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db_session.add(take)
    db_session.flush()
    take_id = take.id

    returned = pitch_service.delete_pitch_service(db_session, pitch_id)

    assert returned == [take_id]
    assert db_session.get(Pitch, pitch_id) is None
    # get() 은 세션에 남은 객체를 돌려준다. CASCADE 는 DB 가 지워서 세션은 모른다
    assert db_session.scalar(select(Take).where(Take.id == take_id)) is None

# pitch presentation 업로드 테스트
def test_upload_presentation_persists_version_one(
    db_session: Session,
    pitch_id: uuid.UUID,
    upload_mock: MagicMock,
    make_pdf: Callable[[int], bytes],
) -> None:
    dto = UploadPresentationDTO(
        presentation_file=UploadFile(file=BytesIO(make_pdf(3)), filename="deck.pdf"),
    )

    result = presentation_service.upload_presentation_service(db_session, pitch_id, dto)

    expected_key = f"pitches/{pitch_id}/presentations/{result.presentation_version_id}.pdf"
    upload_mock.assert_called_once_with(
        dto.presentation_file, expected_key, content_type="application/pdf"
    )
    saved = db_session.get(PresentationVersion, result.presentation_version_id)
    assert saved is not None
    assert saved.pitch_id == pitch_id
    assert saved.version == 1
    assert saved.file_key == expected_key
    assert saved.page_count == 3
    assert result.page_count == 3
