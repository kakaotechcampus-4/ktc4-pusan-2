import pytest
from pydantic import ValidationError

from pitch_coach_backend.core.config import Settings


@pytest.mark.parametrize("key", ["change-me", "x" * 31])
def test_short_jwt_secret_key_is_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(jwt_secret_key=key)


def test_jwt_secret_key_of_32_bytes_is_accepted() -> None:
    assert Settings(jwt_secret_key="x" * 32).jwt_secret_key == "x" * 32


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_username", "관리자"),
        ("admin_password", "관리자" * 5),
        ("admin_username", "team:admin"),
        ("admin_password", "x" * 11),
    ],
)
def test_admin_account_the_login_window_cannot_send_is_refused(field: str, value: str) -> None:
    """브라우저 로그인 창(HTTP Basic)으로 보낼 수 없거나 짧은 계정은 시작할 때 막는다."""
    with pytest.raises(ValidationError, match="ADMIN_"):
        Settings(**{field: value})


def test_empty_admin_account_keeps_console_off() -> None:
    settings = Settings(admin_username="", admin_password="")

    assert (settings.admin_username, settings.admin_password) == ("", "")
