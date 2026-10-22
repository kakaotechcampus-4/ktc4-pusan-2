import pytest
from pydantic import ValidationError

from pitch_coach_backend.core.config import Settings


@pytest.mark.parametrize("key", ["change-me", "x" * 31])
def test_short_jwt_secret_key_is_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(jwt_secret_key=key)


def test_jwt_secret_key_of_32_bytes_is_accepted() -> None:
    assert Settings(jwt_secret_key="x" * 32).jwt_secret_key == "x" * 32
