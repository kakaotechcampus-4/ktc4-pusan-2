"""발표를 지울 때 S3 파일 정리와, 회원 탈퇴가 지울 대상을 모으는 조회."""

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch import s3_service, service
from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO
from pitch_coach_backend.module.pitch.entity import PresentationVersion, ScriptVersion
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.user.entity import User


def _add_pitch(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    return service.add_pitch_service(
        db,
        user_id,
        PitchSaveRequestDTO(title="발표", time_limit_sec=300, presentation_date=date(2026, 3, 1)),
    )


def _add_take(db: Session, pitch_id: uuid.UUID) -> uuid.UUID:
    presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key="deck.pdf")
    script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본")
    db.add_all([presentation, script])
    db.flush()
    take = Take(
        pitch_id=pitch_id,
        take_number=1,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="HIGHLIGHT",
        goal_time_sec=300,
    )
    db.add(take)
    db.flush()
    return take.id


def _fake_s3(monkeypatch: pytest.MonkeyPatch, pages: list[dict]) -> MagicMock:
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = pages
    s3.delete_objects.return_value = {}
    monkeypatch.setattr(s3_service, "get_s3", lambda: s3)
    return s3


def test_ids_of_user_cover_every_pitch_and_only_theirs(
    db_session: Session, user_id: uuid.UUID
) -> None:
    other = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(other)
    db_session.flush()
    first, second = _add_pitch(db_session, user_id), _add_pitch(db_session, user_id)
    take_ids = {_add_take(db_session, first), _add_take(db_session, second)}
    _add_take(db_session, _add_pitch(db_session, other.id))

    assert set(service.get_pitch_ids_of_user(db_session, user_id)) == {first, second}
    assert set(service.get_take_ids_of_user(db_session, user_id)) == take_ids


def test_delete_prefix_deletes_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """목록은 1000개씩 끊겨 온다. 첫 페이지만 지우면 나머지가 남는다."""
    s3 = _fake_s3(
        monkeypatch,
        [
            {"Contents": [{"Key": "pitches/p/presentations/1.pdf"}]},
            {"Contents": [{"Key": "pitches/p/presentations/2.pdf"}]},
        ],
    )

    s3_service.delete_prefix("pitches/p/")

    deleted = [
        obj["Key"]
        for call in s3.delete_objects.call_args_list
        for obj in call.kwargs["Delete"]["Objects"]
    ]
    assert deleted == ["pitches/p/presentations/1.pdf", "pitches/p/presentations/2.pdf"]


def test_delete_prefix_skips_an_empty_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """파일이 없으면 Contents 가 아예 없다. 빈 목록으로 delete_objects 를 부르면 S3 가 거절한다."""
    s3 = _fake_s3(monkeypatch, [{"KeyCount": 0}])

    s3_service.delete_prefix("pitches/p/")

    s3.delete_objects.assert_not_called()


def test_delete_prefix_raises_when_some_files_were_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s3 = _fake_s3(monkeypatch, [{"Contents": [{"Key": "pitches/p/a.pdf"}]}])
    s3.delete_objects.return_value = {
        "Errors": [{"Key": "pitches/p/a.pdf", "Code": "AccessDenied"}]
    }

    with pytest.raises(Exception, match="S3 파일 삭제 실패"):
        s3_service.delete_prefix("pitches/p/")


def test_delete_pitch_files_logs_a_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """행은 이미 지워졌다. 파일 정리가 실패해도 요청을 실패로 만들지 않고 찾을 단서만 남긴다."""
    pitch_id = uuid.uuid4()

    def fail(prefix: str) -> None:
        raise Exception("S3 파일 삭제 실패")

    monkeypatch.setattr(service, "delete_prefix", fail)

    service.delete_pitch_files(pitch_id)

    assert str(pitch_id) in caplog.text


def test_deleting_a_pitch_deletes_its_files(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefixes: list[str] = []
    monkeypatch.setattr(service, "delete_prefix", prefixes.append)
    pitch_id = _add_pitch(db_session, user_id)

    res = client.delete(
        f"/api/pitches/delete/{pitch_id}",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert res.status_code == 200
    assert prefixes == [f"pitches/{pitch_id}/"]
