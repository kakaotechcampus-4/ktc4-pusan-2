import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import ANY, MagicMock

import pytest
from fastapi import UploadFile
from PIL import Image
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter import presentation_file
from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
    render_first_page,
)
from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO, UploadPresentationDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion
from pitch_coach_backend.module.pitch.exception import StorageError
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


@pytest.fixture(autouse=True)
def upload_bytes_mock(monkeypatch) -> MagicMock:
    """진짜 PDF 를 쓰면 썸네일도 그려 올리므로 모든 시험에서 막는다. AWS 세션이 살아 있는
    PC 에서 실제 버킷에 파일이 남고, 세션이 없는 CI 와 결과가 달라지는 것을 막는다."""
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


# 그리는 프로세스 대신 띄운다. 끝에 "%slow" 를 붙인 PDF 는 pid 를 남기고 오래 멈추고, 나머지는
# 진짜로 그린다. 부모에서 함수를 바꿔치면 자식 프로세스에는 닿지 않아서 명령째 바꾼다
_SLOW_RENDER = """
import os, sys, time
from pitch_coach_backend.module.pitch.adapter.pdf_thumbnail import render_webp
data = sys.stdin.buffer.read()
if data.rstrip().endswith(b"%slow"):
    with open(os.environ["SLOW_RENDER_PID_FILE"], "w") as f:
        f.write(str(os.getpid()))
    time.sleep(60)
sys.stdout.buffer.write(render_webp(data, int(sys.argv[1]), int(sys.argv[2])))
"""

RENDER_TIMEOUT = 2.0


@pytest.fixture
def slow_render(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """오래 걸리는 그리기를 흉내 낸다. 멈춘 자식이 pid 를 적는 파일을 돌려준다."""
    pid_file = tmp_path / "render.pid"
    monkeypatch.setenv("SLOW_RENDER_PID_FILE", str(pid_file))
    monkeypatch.setattr(
        presentation_file, "_THUMBNAIL_COMMAND", [sys.executable, "-c", _SLOW_RENDER]
    )
    monkeypatch.setattr(presentation_file, "THUMBNAIL_TIMEOUT_SECONDS", RENDER_TIMEOUT)
    return pid_file


def _slow_pdf(make_pdf: Callable[[int], bytes]) -> bytes:
    return make_pdf(1) + b"%slow\n"


def _assert_killed(pid_file: Path) -> None:
    # 자식이 바꾼 명령을 실제로 실행했고, 상한을 넘긴 뒤에는 끝났는지 본다
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_render_first_page_kills_render_over_time_limit(
    slow_render: Path, make_pdf: Callable[[int], bytes]
) -> None:
    file = BytesIO(_slow_pdf(make_pdf))

    started = time.monotonic()
    thumbnail = render_first_page(file)
    elapsed = time.monotonic() - started

    assert thumbnail is None
    assert RENDER_TIMEOUT <= elapsed < RENDER_TIMEOUT + 2
    _assert_killed(slow_render)
    assert file.tell() == 0


def test_slow_render_does_not_take_thumbnail_from_other_upload(
    slow_render: Path, make_pdf: Callable[[int], bytes]
) -> None:
    """그리기 하나가 오래 걸려도 그동안 들어온 다른 업로드는 썸네일을 받는다."""
    slow_result: list[bytes | None] = []
    slow = threading.Thread(
        target=lambda: slow_result.append(render_first_page(BytesIO(_slow_pdf(make_pdf))))
    )
    slow.start()
    deadline = time.monotonic() + RENDER_TIMEOUT
    while not slow_render.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert slow_render.exists()

    thumbnail = render_first_page(BytesIO(make_pdf(1)))
    still_rendering = slow.is_alive()
    slow.join(RENDER_TIMEOUT + 2)

    assert thumbnail is not None
    assert still_rendering
    assert slow_result == [None]
    _assert_killed(slow_render)


def test_render_first_page_skips_when_render_slots_are_full(
    monkeypatch: pytest.MonkeyPatch, make_pdf: Callable[[int], bytes]
) -> None:
    """그리는 프로세스가 이미 꽉 찼으면 기다리지 않고 썸네일만 건너뛴다."""
    slots = threading.BoundedSemaphore(presentation_file.THUMBNAIL_SLOTS)
    for _ in range(presentation_file.THUMBNAIL_SLOTS):
        slots.acquire()
    monkeypatch.setattr(presentation_file, "_thumbnail_slots", slots)
    run = MagicMock()
    monkeypatch.setattr(presentation_file.subprocess, "run", run)

    assert render_first_page(BytesIO(make_pdf(1))) is None
    run.assert_not_called()


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

    expected_file_key = f"pitches/{pitch_id}/presentations/{result.presentation_version_id}.pdf"
    upload_mock.assert_called_once_with(
        dto.presentation_file, expected_file_key, content_type="application/pdf"
    )
    assert result.pitch_id == pitch_id
    assert result.file_url == f"https://fake/{expected_file_key}"

    expected_thumbnail_key = (
        f"pitches/{pitch_id}/presentations/{result.presentation_version_id}.webp"
    )
    upload_bytes_mock.assert_called_once_with(ANY, expected_thumbnail_key, "image/webp")
    presentation = db_session.get(PresentationVersion, result.presentation_version_id)
    assert presentation is not None
    assert presentation.thumbnail_key == expected_thumbnail_key


def test_upload_finishes_without_thumbnail_when_render_is_too_slow(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
    slow_render: Path,
    make_pdf: Callable[[int], bytes],
) -> None:
    pitch_id = _make_pitch(db_session, user_id)
    file = UploadFile(filename="deck.pdf", file=BytesIO(_slow_pdf(make_pdf)))

    started = time.monotonic()
    result = presentation_service.upload_presentation_service(
        db_session, pitch_id, UploadPresentationDTO(presentation_file=file)
    )

    assert time.monotonic() - started < RENDER_TIMEOUT + 2
    upload_mock.assert_called_once()
    upload_bytes_mock.assert_not_called()
    presentation = db_session.get(PresentationVersion, result.presentation_version_id)
    assert presentation is not None
    assert presentation.thumbnail_key is None
    _assert_killed(slow_render)


def test_concurrent_uploads_on_one_pitch_get_distinct_versions_and_keys(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, make_pdf: Callable[[int], bytes]
) -> None:
    """같은 pitch 에 발표자료 둘이 동시에 와도 둘 다 성공하고 버전·S3 키가 갈린다.

    S3 업로드는 잠그기 전에 끝나므로 첫 요청을 잠금 안(조회 URL 만들기)에서 멈춘다. 잠금이 없으면
    둘 다 max(version)+1 = 1 을 받아 두 번째 INSERT 가 unique 위반(500)이 된다. 트랜잭션 두 개가
    실제로 겹쳐야 해서 테스트 세션(savepoint) 대신 진짜 세션 둘을 쓰고, 만든 데이터는 끝에 지운다.
    """
    from pitch_coach_backend.module.pitch.entity import Pitch
    from pitch_coach_backend.module.user.entity import User

    first_request_locked = threading.Event()
    second_request_uploaded = threading.Event()
    second_request_locked = threading.Event()
    uploaded_keys: list[str] = []
    entered_together: list[bool] = []

    def record_upload(file: UploadFile, key: str, content_type: str | None = None) -> str:
        uploaded_keys.append(key)
        if first_request_locked.is_set():
            second_request_uploaded.set()
        return key

    def presign_in_lock(key: str) -> str:
        if not first_request_locked.is_set():
            # 첫 요청이 잠금을 쥔 채로 두 번째 요청이 잠금 앞까지 오기를 기다린다. 잠금이 있으면
            # 두 번째 요청은 여기에 오지 못하고 대기 시간이 끝난다
            first_request_locked.set()
            assert second_request_uploaded.wait(5)
            entered_together.append(second_request_locked.wait(0.3))
        else:
            second_request_locked.set()
        return f"https://fake/{key}"

    monkeypatch.setattr(presentation_service, "upload", record_upload)
    monkeypatch.setattr(presentation_service, "generate_presigned_url", presign_in_lock)

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
        assert first_request_locked.wait(5)
        second = threading.Thread(target=upload, args=("b",))
        second.start()
        first.join(10)
        second.join(10)

        # 잠금이 없으면 한쪽이 IntegrityError(unique 위반) 다
        assert not any(isinstance(v, Exception) for v in results.values()), results
        assert entered_together == [False]
        assert sorted(uploaded_keys) == sorted(
            f"pitches/{target}/presentations/{r.presentation_version_id}.pdf"
            for r in results.values()
        )
        with Session(engine) as check:
            assert sorted(_versions(check, target)) == [1, 2]
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


def _uploaded_keys(upload_mock: MagicMock, upload_bytes_mock: MagicMock) -> list[str]:
    """이 요청이 올린 PDF · 썸네일 키. 버전을 남기지 못하면 둘 다 지워야 한다."""
    return [upload_mock.call_args.args[1], upload_bytes_mock.call_args.args[1]]


def _versions(db: Session, pitch_id: uuid.UUID) -> list[int]:
    from pitch_coach_backend.module.pitch.repository import PitchRepository

    return [row.version for row in PitchRepository(db).get_presentations(pitch_id)]


def test_url_failure_leaves_neither_a_version_nor_the_file(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
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
    assert deleted == _uploaded_keys(upload_mock, upload_bytes_mock)


def test_cleanup_failure_does_not_replace_the_storage_error(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
):
    """올린 파일을 지우다 또 실패해도 사용자는 처음 실패를 받는다."""
    pitch_id = _make_pitch(db_session, user_id)
    url_failure = StorageError()

    def broken_url(key: str) -> str:
        raise url_failure

    def broken_delete(key: str) -> None:
        raise StorageError()

    monkeypatch.setattr(presentation_service, "generate_presigned_url", broken_url)
    monkeypatch.setattr(presentation_service, "delete", broken_delete)

    with pytest.raises(StorageError) as raised:
        _upload_one(db_session, pitch_id, make_pdf)

    assert raised.value is url_failure
    assert _versions(db_session, pitch_id) == []


def test_thumbnail_failure_keeps_the_upload(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
    make_pdf: Callable[[int], bytes],
):
    pitch_id = _make_pitch(db_session, user_id)
    upload_bytes_mock.side_effect = StorageError()

    result = _upload_one(db_session, pitch_id, make_pdf)

    presentation = db_session.get(PresentationVersion, result.presentation_version_id)
    assert presentation is not None
    assert presentation.thumbnail_key is None


def test_save_failure_deletes_the_uploaded_file(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
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

    assert deleted == _uploaded_keys(upload_mock, upload_bytes_mock)


def test_failed_commit_deletes_the_file_only_when_the_version_is_missing(
    db_session: Session,
    user_id: uuid.UUID,
    upload_mock: MagicMock,
    upload_bytes_mock: MagicMock,
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
    assert deleted == _uploaded_keys(upload_mock, upload_bytes_mock)


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
    files: dict[str, str] = {}
    results: dict[str, Any] = {}

    def upload(file, key, content_type=None):
        name = threading.current_thread().name
        files[key] = name
        return key

    def upload_thumbnail(data, key, content_type):
        files[key] = threading.current_thread().name
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
    monkeypatch.setattr(presentation_service, "upload_bytes", upload_thumbnail)
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
        second.join(5)
        assert not second.is_alive()
        release_cleanup.set()
        first.join(5)
        second.join(5)

        assert not first.is_alive() and not second.is_alive()
        assert isinstance(results["failed-upload"], RuntimeError)
        assert not isinstance(results["next-upload"], Exception), results
        next_id = results["next-upload"].presentation_version_id
        assert files == {
            f"pitches/{pitch_id}/presentations/{next_id}.pdf": "next-upload",
            f"pitches/{pitch_id}/presentations/{next_id}.webp": "next-upload",
        }
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
