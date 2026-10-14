"""settings.py: 기본값, 빈 값, 모델 묶음 검사, 키 숨김, 묶음이 일부만 찼을 때 시작 오류."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pitch_coach_ai.settings import LLM_ROLES, Settings

_FULL_DEFAULT = {
    "openai_base_url": "https://llm.example/v1",
    "openai_api_key": "sk-test-secret",
    "openai_model": "openai/gpt-6-luna",
}


def _settings(**values: object) -> Settings:
    # .env 파일은 읽지 않는다 (conftest 가 묶음 환경변수를 비워 둔 상태에서 넘긴 값만 본다)
    return Settings(_env_file=None, **values)


def test_defaults_without_any_value() -> None:
    s = _settings()
    assert s.llm_timeout_seconds == 12
    assert s.llm_max_retries == 1
    assert s.web_concurrency == 1
    assert s.bundle("default") is None
    assert s.bundle("sol") is None


def test_full_bundle_is_returned_with_hidden_key() -> None:
    bundle = _settings(**_FULL_DEFAULT).bundle("default")
    assert bundle is not None
    assert (bundle.name, bundle.model, bundle.base_url) == (
        "default",
        "openai/gpt-6-luna",
        "https://llm.example/v1",
    )
    assert bundle.api_key.get_secret_value() == "sk-test-secret"
    assert "sk-test-secret" not in repr(bundle)


def test_key_is_not_shown_in_settings_repr() -> None:
    assert "sk-test-secret" not in repr(_settings(**_FULL_DEFAULT))


def test_blank_values_count_as_missing() -> None:
    s = _settings(openai_base_url="", openai_api_key=" ", openai_model="")
    assert s.bundle("default") is None


def test_partly_filled_bundle_is_rejected_with_missing_names() -> None:
    with pytest.raises(ValidationError) as err:
        _settings(
            openai_sol_base_url="https://llm.example/sol/v1",
            openai_sol_model="openai/gpt-6.1-sol",
        )
    assert "OPENAI_SOL_API_KEY" in str(err.value)


def test_startup_error_does_not_show_other_keys() -> None:
    """시작 오류는 컨테이너 로그로 나간다. 다른 묶음의 키를 포함한 입력값을 싣지 않는다."""
    with pytest.raises(ValidationError) as err:
        _settings(**_FULL_DEFAULT, openai_sol_model="openai/gpt-6.1-sol")
    # pydantic 은 입력값을 잘라서 싣기 때문에 키 문자열만 찾으면 우연히 통과할 수 있다.
    # 입력값 칸(input_value) 자체가 없는지 본다
    assert "input_value" not in str(err.value)
    assert "sk-test-secret" not in str(err.value)


@pytest.mark.parametrize(
    "values",
    [
        {"llm_timeout_seconds": 0},
        {"llm_timeout_seconds": "nan"},
        {"llm_max_retries": -1},
        {"web_concurrency": 0},
    ],
)
def test_out_of_range_numbers_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _settings(**values)


def test_every_role_points_to_a_known_bundle() -> None:
    s = _settings()
    for role in LLM_ROLES.values():
        s.bundle(role.bundle)  # 모르는 묶음 이름이면 KeyError
        assert role.max_output_tokens > 0


def test_server_starts_and_health_is_ok_without_any_model(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200


def test_app_import_fails_when_a_bundle_is_partly_filled() -> None:
    """반만 찬 묶음이면 앱을 import 하는 순간(= uvicorn 시작) 실패한다. 새 프로세스로 확인한다."""
    env = {
        **os.environ,
        "OPENAI_SOL_BASE_URL": "https://llm.example/sol/v1",
        "OPENAI_SOL_API_KEY": "",
        "OPENAI_SOL_MODEL": "openai/gpt-6.1-sol",
        # Windows 는 파이프로 받는 stderr 를 cp949 로 내보낸다. 한국어 오류 문장을 읽으려고 맞춘다
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import pitch_coach_ai.main"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode != 0
    assert "OPENAI_SOL_API_KEY" in result.stderr
    assert "https://llm.example/sol/v1" not in result.stderr
