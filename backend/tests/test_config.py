import pytest
from pydantic import ValidationError

from pitch_coach_backend.core.config import Settings


@pytest.mark.parametrize("key", ["change-me", "x" * 31])
def test_short_jwt_secret_key_is_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(jwt_secret_key=key)


def test_jwt_secret_key_of_32_bytes_is_accepted() -> None:
    assert Settings(jwt_secret_key="x" * 32).jwt_secret_key == "x" * 32


JWT = "x" * 32


def test_single_deepgram_key_still_works(monkeypatch) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEYS", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "only")
    assert Settings(jwt_secret_key=JWT, _env_file=None).deepgram_keys == ("only",)


def test_deepgram_keys_are_comma_separated_after_the_single_key(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "a")
    monkeypatch.setenv("DEEPGRAM_API_KEYS", " b , a,, c ")
    assert Settings(jwt_secret_key=JWT, _env_file=None).deepgram_keys == ("a", "b", "c")


def test_missing_deepgram_key_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    monkeypatch.setenv("DEEPGRAM_API_KEYS", " , ")
    with pytest.raises(ValidationError, match="DEEPGRAM_API_KEY"):
        Settings(jwt_secret_key=JWT, _env_file=None)
