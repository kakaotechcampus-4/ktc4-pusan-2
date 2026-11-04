import uuid
from collections.abc import Callable, Generator
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.dependencies import get_owned_pitch
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.pitch.service import presentation as presentation_service
from pitch_coach_backend.module.user.entity import User


def test_upload_holds_no_transaction_while_rendering_and_uploading(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    make_pdf: Callable[[int], bytes],
) -> None:
    pitch = Pitch(user_id=user_id, title="썸네일", time_limit_sec=60)
    db_session.add(pitch)
    db_session.commit()
    in_transaction: dict[str, bool] = {}

    def render(file: BinaryIO) -> bytes | None:
        in_transaction["render"] = db_session.in_transaction()
        return None

    def upload(file: object, key: str, content_type: str | None = None) -> str:
        in_transaction["upload"] = db_session.in_transaction()
        return key

    monkeypatch.setattr(presentation_service, "render_first_page", render)
    monkeypatch.setattr(presentation_service, "upload", upload)
    monkeypatch.setattr(
        presentation_service, "generate_presigned_url", lambda key: f"https://fake/{key}"
    )

    response = client.post(
        f"/api/pitches/add/{pitch.id}/presentation",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        files={"presentation_file": ("deck.pdf", make_pdf(1), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    assert in_transaction == {"render": False, "upload": False}
    assert (
        db_session.scalar(
            select(PresentationVersion.id).where(PresentationVersion.pitch_id == pitch.id)
        )
        is not None
    )


@pytest.fixture
def committed_pitch(engine: Engine) -> Generator[tuple[User, uuid.UUID]]:
    with Session(engine, expire_on_commit=False) as setup:
        user = User(email=f"{uuid.uuid4()}@example.com", name="커넥션 반환")
        setup.add(user)
        setup.flush()
        pitch = Pitch(user_id=user.id, title="커넥션", time_limit_sec=60)
        setup.add(pitch)
        setup.commit()
        pitch_id = pitch.id
    try:
        yield user, pitch_id
    finally:
        with Session(engine) as cleanup:
            cleanup.delete(cleanup.get(User, user.id))
            cleanup.commit()


def test_owned_pitch_returns_connection_and_session_keeps_working(
    engine: Engine, committed_pitch: tuple[User, uuid.UUID]
) -> None:
    user, pitch_id = committed_pitch
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        before = engine.pool.checkedout()

        assert get_owned_pitch(user, db, pitch_id) == pitch_id

        assert engine.pool.checkedout() == before
        assert not db.in_transaction()
        assert user.id is not None
        assert db.scalar(select(Pitch.title).where(Pitch.id == pitch_id)) == "커넥션"
        assert db.in_transaction()
        assert engine.pool.checkedout() == before + 1


def test_owned_pitch_returns_connection_when_not_owned(
    engine: Engine, committed_pitch: tuple[User, uuid.UUID]
) -> None:
    user, _ = committed_pitch
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        before = engine.pool.checkedout()

        with pytest.raises(NonExistentPitch):
            get_owned_pitch(user, db, uuid.uuid4())

        assert engine.pool.checkedout() == before
        assert not db.in_transaction()
