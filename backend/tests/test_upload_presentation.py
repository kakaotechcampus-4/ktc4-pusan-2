
import threading
import time
from datetime import date
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

from fastapi import UploadFile
from pitch_coach_backend.module.pitch.dto import PitchDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch import service
import pytest
from sqlalchemy import Engine
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



def test_concurrent_uploads_on_one_pitch_get_distinct_versions_and_keys(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 pitch 에 발표자료 둘이 동시에 와도 둘 다 성공하고 버전·S3 키가 갈린다.

    잠금이 없으면 둘 다 max(version)+1 = 1 을 받아 같은 S3 키(.../1.pdf)에 올리고(앞 파일이
    덮어쓰임), 두 번째 INSERT 가 unique 위반(500)이 된다. 트랜잭션 두 개가 실제로 겹쳐야 해서
    테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고, 만든 데이터는 끝에 지운다.
    """
    from pitch_coach_backend.module.pitch.entity import Pitch
    from pitch_coach_backend.module.user.entity import User

    first_request_uploading = threading.Event()
    uploaded_keys: list[str] = []

    def slow_upload(file: UploadFile, key: str) -> str:
        # 첫 요청이 S3 에 올리는 중(잠금을 쥔 채)에 두 번째 요청이 들어오게 한다
        uploaded_keys.append(key)
        if not first_request_uploading.is_set():
            first_request_uploading.set()
            time.sleep(0.3)
        return key

    monkeypatch.setattr(service, "upload", slow_upload)
    monkeypatch.setattr(service, "generate_presigned_url", lambda key: f"https://fake/{key}")

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="동시 업로드")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="동시", time_limit_sec=60)
        setup.add(pitch)
        setup.commit()
        user_id, target = user.id, pitch.id

    results: dict[str, Any] = {}

    def upload(name: str) -> None:
        dto = UploadPresentationDTO(
            presentation_file=UploadFile(filename=f"{name}.pdf", file=BytesIO(b"%PDF-1.4"))
        )
        with Session(engine, expire_on_commit=False) as db:
            try:
                results[name] = service.upload_presentation_service(db, target, dto)
            except Exception as exc:  # 잠금이 없으면 여기로 온다
                results[name] = exc

    try:
        first = threading.Thread(target=upload, args=("a",))
        first.start()
        assert first_request_uploading.wait(5)
        second = threading.Thread(target=upload, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        # 잠금이 없으면 한쪽이 IntegrityError(unique 위반) 다
        assert not any(isinstance(v, Exception) for v in results.values()), results
        assert sorted(uploaded_keys) == [
            f"pitches/{target}/presentations/1.pdf",
            f"pitches/{target}/presentations/2.pdf",
        ]
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))  # pitch·버전은 CASCADE
            cleanup.commit()
