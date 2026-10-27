"""엔드포인트: 회원 탈퇴."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.auth.controller import REFRESH_COOKIE_NAME
from pitch_coach_backend.module.auth.dependencies import CSRF_COOKIE_NAME
from pitch_coach_backend.module.auth.entity import OAuthAccount, RefreshToken
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.user.entity import User
from tests.auth.conftest import ME, REFRESH, csrf_headers, do_login

# --- 회원 탈퇴 -----------------------------------------------------------


@pytest.fixture
def deleted_prefixes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """S3 에서 지우라고 한 prefix. conftest 의 막아 둔 함수를 기록하는 것으로 바꾼다."""
    from pitch_coach_backend.module.pitch.service import pitch as pitch_service

    prefixes: list[str] = []
    monkeypatch.setattr(pitch_service, "delete_prefix", prefixes.append)
    return prefixes


def login_and_get_access(client: TestClient) -> dict[str, str]:
    do_login(client)
    access = client.post(REFRESH, headers=csrf_headers(client)).json()["access_token"]
    return {"Authorization": f"Bearer {access}"}


def add_pitch(client: TestClient, auth: dict[str, str]) -> uuid.UUID:
    res = client.post(
        "/api/pitches/add", json={"title": "탈퇴 전 발표", "time_limit_sec": 300}, headers=auth
    )
    assert res.status_code == 200, res.text
    return uuid.UUID(res.json()["pitch_id"])


def test_withdraw_deletes_the_user_and_everything_under_them(
    client: TestClient,
    db_session: Session,
    fake_google: dict[str, Any],
    deleted_prefixes: list[str],
) -> None:
    auth = login_and_get_access(client)
    user_id = uuid.UUID(client.get(ME, headers=auth).json()["id"])
    pitch_id = add_pitch(client, auth)

    res = client.delete(ME, headers=auth)

    assert res.status_code == 204
    assert db_session.scalar(select(User).where(User.id == user_id)) is None
    assert db_session.scalars(select(OAuthAccount)).all() == []
    assert db_session.scalars(select(RefreshToken)).all() == []
    assert db_session.scalars(select(Pitch)).all() == []
    assert deleted_prefixes == [f"pitches/{pitch_id}/"]


def test_withdraw_clears_the_session_cookies(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    auth = login_and_get_access(client)

    client.delete(ME, headers=auth)

    assert client.cookies.get(REFRESH_COOKIE_NAME) is None
    assert client.cookies.get(CSRF_COOKIE_NAME) is None


def test_access_token_stops_working_after_withdrawal(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """Access 는 폐기할 수 없지만 사용자를 찾지 못해 401 이 난다."""
    auth = login_and_get_access(client)
    client.delete(ME, headers=auth)

    assert client.get(ME, headers=auth).status_code == 401
    assert client.delete(ME, headers=auth).status_code == 401


def test_withdraw_does_not_touch_other_users(
    client: TestClient, db_session: Session, fake_google: dict[str, Any], user_id: uuid.UUID
) -> None:
    from pitch_coach_backend.module.pitch.dto import PitchSaveRequestDTO
    from pitch_coach_backend.module.pitch.service import pitch as pitch_service

    other_pitch_id = pitch_service.add_pitch_service(
        db_session, user_id, PitchSaveRequestDTO(title="남의 발표", time_limit_sec=300)
    )
    auth = login_and_get_access(client)

    client.delete(ME, headers=auth)

    assert db_session.scalar(select(User).where(User.id == user_id)) is not None
    assert db_session.scalar(select(Pitch).where(Pitch.id == other_pitch_id)) is not None


def test_signing_in_again_after_withdrawal_starts_a_new_account(
    client: TestClient, fake_google: dict[str, Any]
) -> None:
    """같은 구글 계정이어도 이전 기록이 붙지 않는 새 회원이 된다."""
    auth = login_and_get_access(client)
    old_id = client.get(ME, headers=auth).json()["id"]
    add_pitch(client, auth)
    client.delete(ME, headers=auth)

    auth = login_and_get_access(client)

    assert client.get(ME, headers=auth).json()["id"] != old_id
    assert client.get("/api/pitches/", headers=auth).json()["pitches"] == []
