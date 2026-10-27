"""환경변수를 읽는 설정. 서비스에서 환경변수는 이 파일만 읽는다.

- 부르는 곳: `main.py` · `common/` · 라우터
  (`from pitch_coach_ai.settings import settings`)
- 읽는 순서(앞이 이김): 직접 넘긴 값 > 진짜 환경변수 > `ai/.env` > 기본값.
  `ai/.env` 는 로컬 개발용이다. 이미지 안에는 그 경로가 없어 무시되고,
  서버는 compose 가 넣는 환경변수만 쓴다
- 모든 칸에 기본값이 있다. 키가 없어도 서버가 뜨고 `/health` 가 응답해야
  하기 때문이다(CI · 로컬 확인). 대신 모델 묶음(이름 · 주소 · 키)이 일부만
  차 있으면 시작할 때 오류를 낸다. 키를 빠뜨린 실수를 첫 요청이 아니라
  배포 직후에 보이게 하려는 것이다 (`docs/ai/architecture.md` 10절)
- 고칠 때: 새 환경변수는 `Settings` 에 기본값과 함께 더하고,
  `ai/.env.example` 과 `ai/service/README.md` 환경변수 표에도 더한다.
  역할 → 묶음 연결과 역할별 출력 토큰 상한은 환경변수가 아니라
  아래 `LLM_ROLES` 상수다
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai/ 폴더 (이 파일에서 3단계 위). 로컬은 ai/.env 하나를 research 와 함께 쓴다
AI_DIR = Path(__file__).resolve().parents[3]

# 묶음 이름 → 환경변수 접두어.
# 묶음을 더하면 Settings 에 세 칸을 더하고 여기에 한 줄을 더한다
_BUNDLE_PREFIXES = {"default": "openai", "sol": "openai_sol"}
_PARTS = ("base_url", "api_key", "model")


@dataclass(frozen=True)
class ModelBundle:
    """모델 하나 = 이름 · 주소 · 키. 다른 묶음의 주소 · 키를 빌려 쓰지 않는다."""

    name: str
    model: str
    base_url: str
    api_key: SecretStr


@dataclass(frozen=True)
class LlmRole:
    """기능이 LLM 을 고르는 단위. 기능 코드는 묶음 이름을 모르고 역할만 쓴다.

    - bundle: 쓸 모델 묶음 이름 (`_BUNDLE_PREFIXES` 의 키)
    - max_output_tokens: 출력 토큰 상한. 게이트웨이 기본값에 기대지 않는다.
      추론 모델은 추론에 한도를 써서, 상한이 없거나 작으면 답이 잘린다
      (잘리면 `LLM_INVALID_OUTPUT`, `docs/ai/architecture.md` 9절)
    - reasoning_effort: 추론 강도. None 이면 보내지 않는다
    - timeout_setting: 이 역할의 호출 한 번 시간 제한을 담은 `Settings` 칸 이름. 그 칸이 비어 있거나
      None 이면 `LLM_TIMEOUT_SECONDS` 를 쓴다 (기능별 시간 제한, `docs/ai/architecture.md` 10절)
    """

    bundle: str
    max_output_tokens: int
    reasoning_effort: str | None = None
    timeout_setting: str | None = None


_SCRIPT_COVERAGE_TIMEOUT = "script_coverage_llm_timeout_seconds"

# 역할 → 묶음 연결과 출력 상한. 환경변수로 두지 않는다.
# 서버 시크릿만 바꿔 잰 적 없는 모델로 바뀌지 않게, 바꾸는 것은 측정을 실은
# PR 로 한다. 지금은 모든 역할이 기본 묶음이다.
LLM_ROLES: dict[str, LlmRole] = {
    # 뼈대용 기본 역할. 4096 은 잰 값이 아니라 임시값이다.
    # 각 기능 PR 이 잰 출력 길이로 역할을 더한다
    "default": LlmRole(bundle="default", max_output_tokens=4096),
    # 대본 전달도: 단계(출력 스키마)마다 하나.
    # 상한 = 잰 최대 출력 × 2 를 1,024 단위로 올림(최소 2,048),
    # 호출 한 번 시간 제한은 SCRIPT_COVERAGE_LLM_TIMEOUT_SECONDS (기능 문서 8절)
    "script_coverage.semantic": LlmRole(
        bundle="default", max_output_tokens=4096, timeout_setting=_SCRIPT_COVERAGE_TIMEOUT
    ),
    "script_coverage.final": LlmRole(
        bundle="default", max_output_tokens=4096, timeout_setting=_SCRIPT_COVERAGE_TIMEOUT
    ),
    "script_coverage.unit": LlmRole(
        bundle="default", max_output_tokens=5120, timeout_setting=_SCRIPT_COVERAGE_TIMEOUT
    ),
    "script_coverage.eval": LlmRole(
        bundle="default", max_output_tokens=3072, timeout_setting=_SCRIPT_COVERAGE_TIMEOUT
    ),
    "script_coverage.verifier": LlmRole(
        bundle="default", max_output_tokens=2048, timeout_setting=_SCRIPT_COVERAGE_TIMEOUT
    ),
    # 코치 코칭 계획: 잰 최대 출력 × 2 를 1,024 단위로 올림(최소 2,048).
    # 호출 한 번은 LLM_TIMEOUT_SECONDS (코치 기능 문서 8절)
    "coach.plan": LlmRole(bundle="default", max_output_tokens=2048),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=AI_DIR / ".env",
        extra="ignore",
        # 검사 오류에 입력값(키 포함)을 싣지 않는다. 시작 오류는 컨테이너 로그로 나간다
        hide_input_in_errors=True,
    )

    # 기본 묶음 (6 luna). 셋이 다 있거나 다 없어야 한다
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    # sol 묶음 (6.1 sol). 선택
    openai_sol_base_url: str | None = None
    openai_sol_api_key: SecretStr | None = None
    openai_sol_model: str | None = None

    # LLM 호출 한 번의 시간 제한(초)과 재시도 횟수 (architecture.md 9 · 10절)
    # 범위 밖(0 이하 · nan · 음수)이면 시작 오류
    llm_timeout_seconds: float = Field(12, gt=0, allow_inf_nan=False)
    llm_max_retries: int = Field(1, ge=0)
    # 기능별 호출 한 번 시간 제한(초). 없으면 LLM_TIMEOUT_SECONDS. 어느 역할이 쓰는지는 LLM_ROLES
    script_coverage_llm_timeout_seconds: float | None = Field(None, gt=0, allow_inf_nan=False)
    # uvicorn 워커 수. uvicorn 이 시작할 때 프로세스 환경변수에서 직접 읽고, 여기서는 형식만
    # 검사한다. 그래서 ai/.env 에만 적으면 로컬 `uv run uvicorn` 에는 먹지 않는다
    # (compose env_file · docker run --env-file 은 진짜 환경변수로 넣어 먹는다)
    web_concurrency: int = Field(1, ge=1)

    @field_validator(
        "openai_base_url",
        "openai_api_key",
        "openai_model",
        "openai_sol_base_url",
        "openai_sol_api_key",
        "openai_sol_model",
        "script_coverage_llm_timeout_seconds",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """`OPENAI_API_KEY=` 처럼 빈 값은 '없음'으로 본다 (예시 파일을 복사한 칸).

        기능별 시간 제한도 빈 값이면 없음(→ `LLM_TIMEOUT_SECONDS`)이다.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def _bundles_are_complete(self) -> "Settings":
        for prefix in _BUNDLE_PREFIXES.values():
            names = [f"{prefix}_{part}" for part in _PARTS]
            missing = [n.upper() for n in names if getattr(self, n) is None]
            if 0 < len(missing) < len(_PARTS):
                raise ValueError(
                    f"{prefix.upper()}_* 묶음이 일부만 채워졌습니다. "
                    f"빈 칸: {', '.join(missing)} "
                    "(셋 다 채우거나, 쓰지 않으면 세 줄 모두 주석으로 둡니다)"
                )
        return self

    def timeout_for(self, role: LlmRole) -> float:
        """역할의 호출 한 번 시간 제한(초). 기능별 값이 없으면 `llm_timeout_seconds`.

        예: SCRIPT_COVERAGE_LLM_TIMEOUT_SECONDS=20 이면
        `settings.timeout_for(LLM_ROLES["script_coverage.eval"])` → 20.0
        """
        if role.timeout_setting is not None:
            value = getattr(self, role.timeout_setting)
            if value is not None:
                return value
        return self.llm_timeout_seconds

    def bundle(self, name: str) -> ModelBundle | None:
        """묶음 이름으로 모델 묶음을 돌려준다. 설정되지 않았으면 None.

        예: `settings.bundle("default")`
        → `ModelBundle(name="default", model="openai/gpt-6-luna", ...)`
        """
        prefix = _BUNDLE_PREFIXES[name]
        model = getattr(self, f"{prefix}_model")
        if model is None:
            return None
        return ModelBundle(
            name=name,
            model=model,
            base_url=getattr(self, f"{prefix}_base_url"),
            api_key=getattr(self, f"{prefix}_api_key"),
        )


# 앱이 import 될 때 한 번 읽는다. 묶음이 일부만 차 있으면 여기서 ValidationError
# 가 나서 uvicorn 이 시작하지 못한다
settings = Settings()
