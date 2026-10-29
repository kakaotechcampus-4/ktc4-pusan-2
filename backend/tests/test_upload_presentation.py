import threading
import time
import uuid
from collections.abc import Callable
from datetime import date
from io import BytesIO
from typing import Any
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
    render_first_page,
)
from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion
from pitch_coach_backend.module.pitch.service import pitch as pitch_service
from pitch_coach_backend.module.pitch.service import presentation as presentation_service


@pytest.fixture
def upload_mock(monkeypatch) -> MagicMock:
    mock = MagicMock(side_effect=lambda file, key, content_type=None: key)
    # upload 함수를 mock으로 대체
    monkeypatch.setattr(presentation_service, "upload", mock)
    # generate_presigned_url 함수를 람다함수로 생성하도록 대체
    monkeypatch.setattr(
        presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}"
    )
    return mock


@pytest.fixture
def upload_bytes_mock(monkeypatch) -> MagicMock:
    mock = MagicMock(side_effect=lambda data, key, content_type: key)
    monkeypatch.setattr(presentation_service, "upload_bytes", mock)
    return mock


def test_render_first_page_returns_webp_thumbnail(make_pdf: Callable[[int], bytes]) -> None:
    file = BytesIO(make_pdf(2))

    thumbnail = render_first_page(file)

    assert thumbnail is not None
    image = Image.open(BytesIO(thumbnail))
    assert image.format == "WEBP"
    assert image.mode == "RGB"
    assert image.width <= THUMBNAIL_WIDTH
    assert image.height == THUMBNAIL_HEIGHT
    assert file.tell() == 0


def test_render_first_page_returns_none_for_broken_pdf() -> None:
    file = BytesIO(b"%PDF-1.7\nnot really a pdf")

    assert render_first_page(file) is None
    assert file.tell() == 0


def _make_pitch(db: Session, user_id: uuid.UUID, title: str = "기존 발표") -> uuid.UUID:
    return pitch_service.add_pitch_service(
        db,
        user_id,
        PitchSaveRequestDTO(title=title, time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )


def test_upload_presentation_service(
        db_session: Session,
        user_id: uuid.UUID,
        upload_mock: MagicMock,
        upload_bytes_mock: MagicMock,
        make_pdf: Callable[[int], bytes],
):
    # Given
    pitch_id = _make_pitch(db_session, user_id)
    pdf_real_file = UploadFile(filename="dummy_file.pdf", file=BytesIO(make_pdf(1)))
    dto = UploadPresentationDTO(presentation_file=pdf_real_file)

    result = presentation_service.upload_presentation_service(db_session, pitch_id, dto)

    expected_file_key = f"pitches/{pitch_id}/presentations/1.pdf"
    upload_mock.assert_called_once_with(
        dto.presentation_file, expected_file_key, content_type="application/pdf"
    )
    assert result.pitch_id == pitch_id
    assert result.file_url == f"https://fake/{expected_file_key}"

    expected_thumbnail_key = f"pitches/{pitch_id}/presentations/1.webp"
    upload_bytes_mock.assert_called_once_with(ANY, expected_thumbnail_key, "image/webp")
    presentation = db_session.get(PresentationVersion, result.presentation_version_id)
    assert presentation is not None
    assert presentation.thumbnail_key == expected_thumbnail_key


def test_concurrent_uploads_on_one_pitch_get_distinct_versions_and_keys(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, make_pdf: Callable[[int], bytes]
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

    def slow_upload(file: UploadFile, key: str, content_type: str | None = None) -> str:
        # 첫 요청이 S3 에 올리는 중(잠금을 쥔 채)에 두 번째 요청이 들어오게 한다
        uploaded_keys.append(key)
        if not first_request_uploading.is_set():
            first_request_uploading.set()
            time.sleep(0.3)
        return key

    monkeypatch.setattr(presentation_service, "upload", slow_upload)
    monkeypatch.setattr(
        presentation_service, "upload_bytes", lambda data, key, content_type: key
    )
    monkeypatch.setattr(presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}")

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
            presentation_file=UploadFile(filename=f"{name}.pdf", file=BytesIO(make_pdf(1)))
        )
        with Session(engine, expire_on_commit=False) as db:
            try:
                results[name] = presentation_service.upload_presentation_service(db, target, dto)
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


# ── S3 에 올린 뒤 실패하면 한쪽만 남기지 않는다 ──────────────────────────────


@pytest.fixture
def deleted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    keys: list[str] = []
    monkeypatch.setattr(presentation_service, "delete", keys.append)
    return keys


def _upload_one(db: Session, pitch_id: uuid.UUID, make_pdf: Callable[[int], bytes]):
    file = UploadFile(filename="deck.pdf", file=BytesIO(make_pdf(1)))
    return presentation_service.upload_presentation_service(
        db, pitch_id, UploadPresentationDTO(presentation_file=file)
    )


def _versions(db: Session, pitch_id: uuid.UUID) -> list[int]:
    from pitch_coach_backend.module.pitch.repository import PitchRepository

    return [row.version for row in PitchRepository(db).get_presentations(pitch_id)]


def test_url_failure_leaves_neither_a_version_nor_the_file(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    deleted: list[str],
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
):
    """조회 URL 은 커밋 전에 만든다. 커밋 뒤에 실패하면 오류를 받은 사용자가 다시 올려 버전이
    하나 더 생긴다."""
    pitch_id = _make_pitch(db_session, user_id)

    def broken(key: str) -> str:
        raise RuntimeError("presign failed")

    monkeypatch.setattr(presentation_service, "generate_presigned_url", broken)

    with pytest.raises(RuntimeError):
        _upload_one(db_session, pitch_id, make_pdf)

    assert _versions(db_session, pitch_id) == []
    assert deleted == [f"pitches/{pitch_id}/presentations/1.pdf"]


def test_save_failure_deletes_the_uploaded_file(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    deleted: list[str],
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
):
    from pitch_coach_backend.module.pitch.repository import PitchRepository

    pitch_id = _make_pitch(db_session, user_id)

    def broken(self, presentation):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(PitchRepository, "save_presentation", broken)

    with pytest.raises(RuntimeError):
        _upload_one(db_session, pitch_id, make_pdf)

    assert deleted == [f"pitches/{pitch_id}/presentations/1.pdf"]


def test_failed_commit_deletes_the_file_only_when_the_version_is_missing(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    deleted: list[str],
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
):
    pitch_id = _make_pitch(db_session, user_id)

    def broken() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", broken)

    with pytest.raises(RuntimeError):
        _upload_one(db_session, pitch_id, make_pdf)

    assert _versions(db_session, pitch_id) == []
    assert deleted == [f"pitches/{pitch_id}/presentations/1.pdf"]


def test_commit_whose_reply_was_lost_keeps_the_file(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    deleted: list[str],
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
):
    """커밋은 됐는데 응답 전에 연결이 끊겼다. 버전 행이 가리키는 파일을 지우면 그 버전을 열 수
    없다."""
    pitch_id = _make_pitch(db_session, user_id)
    real_commit = db_session.commit

    def lost_reply() -> None:
        real_commit()
        raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(db_session, "commit", lost_reply)

    with pytest.raises(RuntimeError):
        _upload_one(db_session, pitch_id, make_pdf)

    assert _versions(db_session, pitch_id) == [1]
    assert deleted == []


@pytest.mark.parametrize("next_upload_first", [False, True])
def test_failed_upload_cleanup_does_not_delete_the_next_upload(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
    next_upload_first: bool,
):
    """롤백하면 같은 버전 번호를 다시 쓴다. 다음 업로드가 정리 전이나 정리 중에 와도 그 파일을
    지우지 않아야 한다. 잠금 확인에는 savepoint 대신 별도 트랜잭션 둘을 쓴다."""
    from pitch_coach_backend.module.pitch.entity import Pitch
    from pitch_coach_backend.module.user.entity import User

    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    next_uploaded = threading.Event()
    files: dict[str, str] = {}
    results: dict[str, Any] = {}

    def upload(file, key, content_type=None):
        name = threading.current_thread().name
        files[key] = name
        if name == "next-upload":
            next_uploaded.set()
        return key

    def presign(key):
        if threading.current_thread().name == "failed-upload":
            raise RuntimeError("presign failed")
        return f"https://fake/{key}"

    def delete(key):
        if not next_upload_first:
            cleanup_started.set()
            assert release_cleanup.wait(5)
        files.pop(key, None)

    monkeypatch.setattr(presentation_service, "upload", upload)
    monkeypatch.setattr(presentation_service, "generate_presigned_url", presign)
    monkeypatch.setattr(presentation_service, "delete", delete)

    with Session(engine) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="실패 뒤 동시 업로드")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="동시", time_limit_sec=60)
        setup.add(pitch)
        setup.commit()
        user_id, pitch_id = user.id, pitch.id

    def request() -> None:
        name = threading.current_thread().name
        with Session(engine, expire_on_commit=False) as db:
            if name == "failed-upload" and next_upload_first:
                rollback = db.rollback

                def pause_after_rollback():
                    rollback()
                    cleanup_started.set()
                    assert release_cleanup.wait(5)

                db.rollback = pause_after_rollback
            try:
                results[name] = _upload_one(db, pitch_id, make_pdf)
            except Exception as exc:
                results[name] = exc

    first = threading.Thread(target=request, name="failed-upload")
    second = threading.Thread(target=request, name="next-upload")
    try:
        first.start()
        assert cleanup_started.wait(5)
        second.start()
        if next_upload_first:
            second.join(5)
            assert not second.is_alive()
        else:
            # 파일을 지우는 동안은 새 업로드도 같은 잠금에서 기다려야 한다
            blocked = not next_uploaded.wait(0.2)
        release_cleanup.set()
        first.join(5)
        second.join(5)

        assert not first.is_alive() and not second.is_alive()
        assert isinstance(results["failed-upload"], RuntimeError)
        assert not isinstance(results["next-upload"], Exception), results
        if not next_upload_first:
            assert blocked
        assert files == {f"pitches/{pitch_id}/presentations/1.pdf": "next-upload"}
        with Session(engine) as check:
            assert _versions(check, pitch_id) == [1]
    finally:
        release_cleanup.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user_id))
            cleanup.commit()
