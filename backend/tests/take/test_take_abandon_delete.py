"""Take API — 창을 다시 연 FE 의 abandon · 삭제."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.take.entity import Take
from tests.take.conftest import Versions, _make_take

# ── POST /takes/{take_id}/abandon ───────────────────────────────────


def _abandon(client: TestClient, take: Take, headers: dict[str, str]):
    return client.post(f"/api/pitches/{take.pitch_id}/takes/{take.id}/abandon", headers=headers)


@pytest.mark.parametrize("status", ["READY", "RUNNING"])
def test_abandon_fails_a_take_whose_tab_was_closed(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    status: str,
) -> None:
    """창을 닫았다 다시 연 FE 가 부른다. 이어 가지 않고 바로 실패(CLOSED)로 끝낸다. 끝난 시각은
    마지막 활동 시각이다 (여기서는 만든 시각). 다시 불러도 같다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = status
    db_session.flush()

    first = _abandon(client, take, auth_headers)
    again = _abandon(client, take, auth_headers)

    assert first.status_code == 200
    assert first.json()["status"] == again.json()["status"] == "FAILED"
    db_session.refresh(take)
    assert (take.status, take.end_reason, take.ended_at) == ("FAILED", "CLOSED", take.created_at)


def test_abandon_leaves_a_take_that_already_ended(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """끝내기가 이미 닿았으면 그대로 두고 지금 상태를 돌려준다. FE 는 처리 중 화면으로 간다."""
    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "ANALYZING"
    db_session.flush()

    response = _abandon(client, take, auth_headers)

    assert response.json()["status"] == "ANALYZING"
    db_session.refresh(take)
    assert (take.status, take.end_reason) == ("ANALYZING", None)


def test_abandon_is_refused_while_another_window_is_presenting(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FE 는 하트비트로 다른 창이 살아 있는지 보지만 늦을 수 있다. 연결이 붙어 있고 오디오가
    들어오면 BE 가 거절한다 — 발표 중인 Take 를 끝내지 않는다."""
    from pitch_coach_backend.realtime import take_stream

    take = _make_take(db_session, pitch_id, versions, 1)
    take.status = "RUNNING"
    db_session.flush()
    monkeypatch.setattr(take_stream, "is_live", lambda take_id: take_id == take.id)

    response = _abandon(client, take, auth_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "TAKE_IN_PROGRESS"
    db_session.refresh(take)
    assert (take.status, take.end_reason) == ("RUNNING", None)


def test_abandon_on_someone_elses_take_is_404(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    from pitch_coach_backend.module.user.entity import User

    take = _make_take(db_session, pitch_id, versions, 1)
    stranger = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db_session.add(stranger)
    db_session.flush()

    response = _abandon(
        client, take, {"Authorization": f"Bearer {create_access_token(stranger.id)}"}
    )

    assert response.status_code == 404
    db_session.refresh(take)
    assert take.status == "READY"


# ── DELETE /takes/{take_id} ─────────────────────────────────────────


def test_delete_take_without_a_live_stream(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    pitch_id: uuid.UUID,
    versions: Versions,
) -> None:
    """실시간 STT 가 붙어 있지 않은 Take 도 그대로 지워진다 (스트림 끊기는 건너뛴다)."""
    take_id = _make_take(db_session, pitch_id, versions, 1).id

    response = client.delete(f"/api/pitches/{pitch_id}/takes/{take_id}", headers=auth_headers)

    assert response.status_code == 200
    assert db_session.get(Take, take_id) is None
