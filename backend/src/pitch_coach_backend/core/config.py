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
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str
    # Access 토큰은 짧게. 폐기 수단이 없으므로 만료가 유일한 방어선이다.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # 콜백이 끝난 뒤 브라우저를 돌려보낼 프론트 주소. CORS 허용 origin 으로도 쓴다.
    frontend_base_url: str = "http://localhost:3000"

    # 운영에서 설정을 빠뜨려도 Secure 가 붙는 쪽이 안전하다.
    # Chrome·Firefox 는 http://localhost 를 신뢰 출처로 보므로 로컬에서도 동작한다.
    cookie_secure: bool = True


settings = Settings()
