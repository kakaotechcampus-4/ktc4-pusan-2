"""auth service: Refresh 회전 · 재사용 탐지 · 잘못된 토큰."""

from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import repository, service
from pitch_coach_backend.module.auth.entity import RefreshToken
from pitch_coach_backend.module.auth.exception import InvalidRefreshToken, RefreshTokenReused
from tests.auth.conftest import _past_grace, login

# --- 회전 ---------------------------------------------------------------


def test_rotate_returns_new_tokens(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session)

    rotated = service.rotate(db_session, issued.refresh_token)

    assert rotated.user.id == issued.user.id
    assert rotated.refresh_token != issued.refresh_token
    # 같은 브라우저 세션이 이어진다
    assert rotated.device_id == issued.device_id


def test_rotate_revokes_the_used_token(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session)
    service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    assert old is not None
    assert old.revoked_at is not None


def test_rotate_links_the_used_token_to_the_new_one(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)
    rotated = service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    new = repository.get_refresh_token(
        db_session, security.hash_refresh_token(rotated.refresh_token)
    )
    assert old is not None and new is not None
    assert old.replaced_by_id == new.id
    assert new.replaced_by_id is None


def test_rotated_token_cannot_be_used_again(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)
    service.rotate(db_session, issued.refresh_token)
    _past_grace(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)


def test_reuse_kills_the_whole_device_session(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """유출로 보고 공격자와 정상 사용자 양쪽을 모두 로그아웃시킨다."""
    issued = login(db_session)
    current = service.rotate(db_session, issued.refresh_token)
    _past_grace(db_session, issued.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    # 방금까지 멀쩡하던 최신 토큰도 함께 끊긴다
    with pytest.raises(UnauthorizedException):
        service.rotate(db_session, current.refresh_token)


def test_reuse_does_not_touch_other_devices(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    """한 브라우저가 털려도 다른 기기의 세션까지 끊지는 않는다."""
    laptop = login(db_session)
    phone = login(db_session)
    service.rotate(db_session, laptop.refresh_token)
    _past_grace(db_session, laptop.refresh_token)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, laptop.refresh_token)

    assert service.rotate(db_session, phone.refresh_token).user.id == phone.user.id


def test_token_revoked_after_it_was_read_is_rejected(
    db_session: Session,
    fake_google: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """읽은 뒤 쓰기 전에 로그아웃이 먼저 폐기했다. 새 토큰을 내주면 끊은 세션이 되살아난다.

    행을 잠그므로 실제로는 일어나지 않아야 하지만, 조건부 폐기가 마지막 방어선으로 남아 있다.
    """
    issued = login(db_session)
    original_get = repository.get_refresh_token_for_update

    def get_then_lose_the_race(db: Session, token_hash: str) -> RefreshToken | None:
        token = original_get(db, token_hash)
        assert token is not None
        # 로그아웃이 이 사이에 커밋됐다. ORM 객체는 모른 채 revoked_at=None 으로 남는다
        db.execute(
            text("UPDATE refresh_tokens SET revoked_at = now() WHERE id = :id"),
            {"id": token.id},
        )
        return token

    monkeypatch.setattr(repository, "get_refresh_token_for_update", get_then_lose_the_race)

    with pytest.raises(RefreshTokenReused):
        service.rotate(db_session, issued.refresh_token)

    monkeypatch.undo()
    device_tokens = db_session.scalars(
        select(RefreshToken).where(RefreshToken.device_id == issued.device_id)
    ).all()
    # 진 쪽은 새 토큰을 받지 않고, 그 세션은 통째로 끊긴다
    assert len(device_tokens) == 1
    assert all(t.revoked_at is not None for t in device_tokens)


# --- 잘못된 토큰 -------------------------------------------------------


def test_unknown_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, security.create_refresh_token())


def test_missing_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, None)

    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, "")
