"""oauth_accounts·refresh_tokens 쿼리. 커밋은 service 가 한다."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.auth.entity import OAuthAccount, RefreshToken

GOOGLE = "google"


# --- OAuthAccount -------------------------------------------------------


def get_account(db: Session, *, provider: str, subject: str) -> OAuthAccount | None:
    return db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_subject == subject,
        )
    )


def create_account(
    db: Session, *, user_id: uuid.UUID, provider: str, subject: str, email: str | None
) -> OAuthAccount:
    account = OAuthAccount(
        user_id=user_id,
        provider=provider,
        provider_subject=subject,
        provider_email=email,
    )
    db.add(account)
    db.flush()
    return account


# --- RefreshToken -------------------------------------------------------


def create_refresh_token(
    db: Session,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    device_id: uuid.UUID,
    expires_at: datetime,
) -> RefreshToken:
    token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        device_id=device_id,
        expires_at=expires_at,
    )
    db.add(token)
    db.flush()
    return token


def get_refresh_token(db: Session, token_hash: str) -> RefreshToken | None:
    """폐기·만료 여부와 무관하게 가져온다. 판단은 service 가 한다.

    이미 폐기된 토큰이 들어온 것 자체가 유출 신호라서, 여기서 걸러내면
    재사용 탐지를 할 수 없다.
    """
    return db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))


def revoke(db: Session, token: RefreshToken) -> None:
    """이미 폐기된 토큰의 시각은 덮어쓰지 않는다. 최초 폐기 시점이 유출 조사에 쓰인다."""
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        db.flush()


def revoke_if_active(db: Session, token_id: uuid.UUID) -> bool:
    """아직 살아 있을 때만 폐기한다. 이 요청이 폐기했으면 True.

    읽고 나서 쓰기 전 사이에 다른 요청이 같은 토큰을 먼저 폐기했을 수 있다. 조건을 WHERE 에
    넣으면 DB 가 행 잠금으로 줄을 세우고, 늦은 쪽은 0행을 바꿔 진 것을 안다.
    """
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
    return result.rowcount == 1


def revoke_device(db: Session, *, user_id: uuid.UUID, device_id: uuid.UUID) -> int:
    """세션(브라우저 1개) 의 살아 있는 토큰을 전부 폐기한다.

    회전된 토큰이 다시 들어오면 유출로 보고 이 함수로 계보 전체를 끊는다.
    """
    result = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.device_id == device_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
    return result.rowcount
