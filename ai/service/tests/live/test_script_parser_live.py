"""대본 파싱을 실제 LLM 으로 (과금, 호출 2번). `uv run python -m pytest -m live`.

`POST /scripts/parse` 를 서비스의 LLM 객체 · 역할(`script_parser`, 출력 상한 2,000 · 추론 low) ·
마감(25초)으로 부르고, 예시 대본의 장 나누기 · 정제 · 예상 시간이 예시 응답과 같은지 본다.
걸린 시간은 출력만 한다.
키워드 · 용어는 LLM 마다 다를 수 있어 대본에 그대로 있는지만 본다(기능 문서 11절).
`ai/.env` 의 기본 묶음을 직접 읽는다(`test_llm_live.py` 와 같다).
"""

import json
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from pitch_coach_ai.features.script_parser import core
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live

EXAMPLES = Path(core.__file__).resolve().parent / "examples"


def _settings_from_env_file() -> Settings:
    env = {k.lower(): v for k, v in dotenv_values(AI_DIR / ".env").items() if v}
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(_env_file=None, **env)


def _load(case: str, name: str) -> dict:
    return json.loads((EXAMPLES / case / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", ["success", "fail"])
def test_script_parse_with_the_real_llm(case: str) -> None:
    request, expected = _load(case, "request.json"), _load(case, "response.json")
    with TestClient(create_app(_settings_from_env_file())) as client:
        started = time.monotonic()
        res = client.post("/scripts/parse", json=request)
        seconds = time.monotonic() - started
    assert res.status_code == 200, res.text
    body = res.json()
    print(f"model={body['meta']['model']} seconds={seconds:.1f} terms={body['terms']}")
    assert body["meta"]["model"]
    # 걸린 시간은 출력만 한다. 마감(25초)은 서버가 지키고, 게이트웨이 지연이 흔들리면
    # 504 가 된다(기능 문서 8절 '서비스 경로로 잰 시간')
    # 장 나누기 · 판정 · 정제 · 예상 시간은 코드가 하므로 예시와 같아야 한다
    assert body["status"] == expected["status"]
    for key in ("slide_number", "script", "script_chars", "expected_ms"):
        assert [s[key] for s in body["slides"]] == [s[key] for s in expected["slides"]]
    for s in body["slides"]:
        assert [s["script"][h["start"] : h["end"]] for h in s["highlights"]] == s["keywords"]
    assert all(t in request["script_text"] for t in body["terms"])
