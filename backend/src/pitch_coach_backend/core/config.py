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
    # 방금 회전된 Refresh 가 이 시간 안에 다시 오면 재사용으로 보지 않는다.
    # 회전 응답을 받기 전에 새로고침한 브라우저는 새 쿠키를 못 받아 옛 토큰을 다시 보낸다.
    refresh_token_reuse_grace_seconds: int = 10

    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # Deepgram 스트리밍 STT. 서버만 알고 브라우저에는 절대 내려보내지 않는다.
    deepgram_api_key: str

    # Take 자동 종료. 말 없이 켜 둔 Take 가 STT 비용을 계속 쓰지 않게 한다.
    # 말 없음 — 최근 take_silence_end_sec 동안 말이 없고, 직전 take_silence_window_sec 중
    # take_silence_ratio 이상이 말 없음이면 끝낸다. 시간은 전사가 확인된 오디오 기준이다.
    # 값 검사는 realtime/auto_end.py 의 AutoEndPolicy 한 곳에서 한다 (앱 시작 때)
    take_silence_end_sec: int = 120
    take_silence_window_sec: int = 300
    take_silence_ratio: float = 0.8
    # 강의 녹음처럼 원래 용도와 다르게 오래 쓰는 것을 막는 상한.
    # AI 요청 시각의 상한(1시간 + 60초, docs/ai/architecture.md 1-3)이
    # 이 값에 맞춰져 있다. 늘리려면 AI 와 먼저 맞춘다
    take_max_duration_sec: int = 3600

    # 콜백이 끝난 뒤 브라우저를 돌려보낼 프론트 주소. CORS 허용 origin 으로도 쓴다.
    frontend_base_url: str = "http://localhost:3000"

    # 운영에서 설정을 빠뜨려도 Secure 가 붙는 쪽이 안전하다.
    # Chrome·Firefox 는 http://localhost 를 신뢰 출처로 보므로 로컬에서도 동작한다.
    cookie_secure: bool = True

    # AI 서버 (대본 파싱 등). 운영은 compose 내부 주소 http://ai:8000.
    # 필수로 두지 않는다 — 빠져도 BE 는 뜨고, 파싱만 AI_UNAVAILABLE 로 실패한다
    ai_base_url: str = "http://localhost:8001"

    # AWS S3 Configuration
    # SSO로 로그인, 그래서 따로 액세스&시크릿 키를 발급받지 않아도 됨.
    s3_bucket_name: str
    s3_region: str = "ap-northeast-2"
    # AWS_PROFILE 환경변수로 SSO 프로필 지정
    aws_profile: str | None = None


settings = Settings()
