"""auth service: Refresh 회전 · 재사용 탐지 · 잘못된 토큰."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from pitch_coach_backend.core import security
from pitch_coach_backend.core.exceptions import UnauthorizedException
from pitch_coach_backend.module.auth import repository, service
from pitch_coach_backend.module.auth.exception import InvalidRefreshToken, RefreshTokenReused
from tests.auth.conftest import _past_grace, login

# --- 회전 ---------------------------------------------------------------


def test_rotate_returns_new_tokens(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session)

    rotated = service.rotate(db_session, issued.refresh_token)

    assert rotated.user_id == issued.user_id
    assert rotated.refresh_token != issued.refresh_token
    # 같은 브라우저 세션이 이어진다
    assert rotated.device_id == issued.device_id


def test_rotate_revokes_the_used_token(db_session: Session, fake_google: dict[str, Any]) -> None:
    issued = login(db_session)
    service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token_for_update(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    assert old is not None
    assert old.revoked_at is not None


def test_rotate_links_the_used_token_to_the_new_one(
    db_session: Session, fake_google: dict[str, Any]
) -> None:
    issued = login(db_session)
    rotated = service.rotate(db_session, issued.refresh_token)

    old = repository.get_refresh_token_for_update(
        db_session, security.hash_refresh_token(issued.refresh_token)
    )
    new = repository.get_refresh_token_for_update(
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

    assert service.rotate(db_session, phone.refresh_token).user_id == phone.user_id


# --- 잘못된 토큰 -------------------------------------------------------


def test_unknown_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, security.create_refresh_token())


def test_missing_refresh_token_is_rejected(db_session: Session) -> None:
    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, None)

    with pytest.raises(InvalidRefreshToken):
        service.rotate(db_session, "")
