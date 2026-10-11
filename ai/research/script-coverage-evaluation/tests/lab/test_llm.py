"""load_settings: .env 를 읽되 이미 있는 환경 변수는 덮어쓰지 않는다 (실행 환경이 정한 키 · 주소가 우선)."""

import pytest

import coverage_lab.llm as lab_llm

ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL")


def _clear_env(monkeypatch):
    """환경 변수를 비우고, 테스트가 끝나면 (load_dotenv 가 채운 값까지) 원래대로 되돌린다."""
    for name in ENV_NAMES:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "OPENAI_API_KEY=key-from-file\nOPENAI_MODEL=model-from-file\nOPENAI_BASE_URL=http://file/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lab_llm, "find_env_file", lambda: str(path))
    _clear_env(monkeypatch)
    return path


def test_values_come_from_env_file_when_environment_is_empty(env_file):
    settings = lab_llm.load_settings()
    assert (settings.api_key, settings.model, settings.base_url) == (
        "key-from-file",
        "model-from-file",
        "http://file/v1",
    )


def test_process_environment_wins_over_env_file(env_file, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key-from-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    settings = lab_llm.load_settings()
    assert settings.api_key == "key-from-env"
    assert settings.base_url == "http://127.0.0.1:9/v1"
    assert settings.model == "model-from-file"  # 환경에 없는 값만 파일에서 채운다


def test_missing_key_or_model_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_llm, "find_env_file", lambda: "")
    _clear_env(monkeypatch)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY / OPENAI_MODEL"):
        lab_llm.load_settings()


def test_llm_builders_return_schema_bound_runnables():
    settings = lab_llm.Settings(base_url="http://127.0.0.1:9/v1", api_key="dummy", model="m")
    assert set(lab_llm.script_llms(settings)) == {"semantic_llm", "final_llm", "unit_llm"}
    assert set(lab_llm.stt_llms(settings)) == {"eval_llm", "verifier_llm"}
