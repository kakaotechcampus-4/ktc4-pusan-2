"""대화 에이전트를 서비스 경로로 실제 LLM 한 턴 (과금, 호출 약 4~6번).

- 실행: `uv run python -m pytest -m live`
- `POST /agent/turn` 을 서비스의 도구 호출 LLM(`tool_llm`, 역할 `chat_agent`) · 실행 자리 ·
  NDJSON 스트림으로 부르고, 코어 예시 요청(`examples/request_user_message.json`)이 끝 줄 하나
  (`final`)로 끝나는지, 진단 기록에 요청 ID 가 이어지는지 본다(대화 기능 문서 5-4 · 6-11 · 11-1).
- 답의 질은 연구 폴더 평가셋이 본다.
- `ai/.env` 의 기본 묶음을 직접 읽고, 서명 키는 시험마다 새로 만든다.
"""

import json
import secrets
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pydantic import SecretStr

from pitch_coach_ai.features import chat_agent as core
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live

REQUEST = (Path(core.__file__).parent / "examples" / "request_user_message.json").read_text(
    encoding="utf-8"
)


def _settings_from_env_file() -> Settings:
    env = {
        k.lower(): v
        for k, v in dotenv_values(AI_DIR / ".env").items()
        if v and k.startswith("OPENAI_") and not k.startswith("OPENAI_SOL_")
    }
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(
        _env_file=None,
        agent_enabled=True,
        agent_state_secret=SecretStr(secrets.token_urlsafe(32)),
        **env,
    )


def test_one_turn_through_the_service_path() -> None:
    req = json.loads(REQUEST)
    with (
        TestClient(create_app(_settings_from_env_file())) as client,
        client.stream(
            "POST", "/agent/turn", json=req, headers={"X-Request-ID": "live-chat-1"}
        ) as r,
    ):
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(x) for x in r.iter_lines() if x]
    events = [x["event"] for x in lines]
    assert events[-1] == "final" and events.count("final") == 1 and "error" not in events
    data = lines[-1]["data"]
    # 행동 요청(보던 Take 밖 기록 가져오기 등)으로 멈추는 것도 정상 끝이다(5-5)
    assert data["status"] in ("DONE", "PARTIAL", "NEEDS_ACTION")
    assert data["diagnostics"]["request_id"] == "live-chat-1"
    assert data["meta"]["model"]
    print(data["status"], [c["text"] for c in data["message"]["claims"]])
