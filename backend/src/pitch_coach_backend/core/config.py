from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 디렉터리 (core/config.py 기준 3단계 위)
BACKEND_DIR = Path(__file__).resolve().parents[3]

# HS256 키는 해시 출력 길이(32바이트) 이상이어야 추측으로 서명을 깨기 어렵다
JWT_SECRET_KEY_MIN_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        extra="ignore",
    )

    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # 앱 로거(pitch_coach_backend.*)의 레벨. DEBUG · INFO · WARNING · ERROR (core/logging.py)
    log_level: str = "INFO"

    jwt_secret_key: str
    # Access 토큰은 짧게. 폐기 수단이 없으므로 만료가 유일한 방어선이다.
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14
    # 회전할 때마다 만료가 다시 잡혀도 로그인한 지 이만큼 지나면 다시 로그인해야 한다.
    # 없으면 14일 안에 한 번씩만 갱신해도 세션이 끝없이 이어진다
    refresh_session_max_days: int = 30
    # 방금 회전된 Refresh 가 이 시간 안에 다시 오면 재사용으로 보지 않는다.
    # 회전 응답을 받기 전에 새로고침한 브라우저는 새 쿠키를 못 받아 옛 토큰을 다시 보낸다.
    refresh_token_reuse_grace_seconds: int = 10

    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    # Deepgram 스트리밍 STT. 서버만 알고 브라우저에는 절대 내려보내지 않는다.
    # 키가 여럿이면 DEEPGRAM_API_KEYS 에 쉼표로 잇는다. 앞에서부터 쓰고, 크레딧이 떨어지거나
    # 키가 틀려 거절되면 다음 키로 넘어간다 (realtime/stt_adapter.py). 둘 다 있으면
    # DEEPGRAM_API_KEY 가 맨 앞이다. 실제 목록은 deepgram_keys 로 읽는다
    deepgram_api_key: str = ""
    deepgram_api_keys: str = ""

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

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_secret_key_is_long_enough(cls, value: str) -> str:
        # .env.example 의 change-me 그대로 운영에 올라가는 것을 시작할 때 막는다
        if len(value.encode()) < JWT_SECRET_KEY_MIN_BYTES:
            raise ValueError(
                f"JWT_SECRET_KEY 는 {JWT_SECRET_KEY_MIN_BYTES}바이트 이상이어야 합니다. "
                "openssl rand -hex 32 로 만드세요."
            )
        return value

    @model_validator(mode="after")
    def _has_deepgram_key(self) -> Settings:
        # 키가 없으면 STT 가 하나도 안 된다. 예전처럼 시작할 때 막는다
        if not self.deepgram_keys:
            raise ValueError("DEEPGRAM_API_KEY 나 DEEPGRAM_API_KEYS 에 키를 하나 이상 넣으세요.")
        return self

    @property
    def deepgram_keys(self) -> tuple[str, ...]:
        keys = [self.deepgram_api_key, *self.deepgram_api_keys.split(",")]
        # 같은 키를 두 번 넣어도 한 번만 시도한다
        return tuple(dict.fromkeys(key.strip() for key in keys if key.strip()))


settings = Settings()
