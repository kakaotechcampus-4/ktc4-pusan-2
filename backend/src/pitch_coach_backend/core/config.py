from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 디렉터리 (core/config.py 기준 3단계 위)
BACKEND_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        extra="ignore",
    )

    database_url: str

    jwt_secret_key: str
    access_token_expire_minutes: int = 30


settings = Settings()
