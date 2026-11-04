"""대본 전달도를 실제 LLM 으로 장 하나 끝까지 (과금, 약 4~5번 호출).

`uv run python -m pytest -m live`. `/scripts/rubric` 으로 평가 기준을 만들고, 대본을 그대로 읽은
STT 로 `/takes/analyze` 를 부른다(기능 문서 11절 '옮긴 뒤 같은 결과인지 확인' 3).
서비스의 LLM 객체 · 역할 · 시간 제한 · 마감을 그대로 쓴다.
`ai/.env` 의 기본 묶음을 직접 읽는다
(conftest 가 묶음 환경변수를 비워 둔다, `test_llm_live.py` 와 같다).
"""

import time

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live

# 숫자 · 단위가 든 짧은 가상 대본 한 장
SCRIPT = (
    "학생들이 가장 불편해한 점은 열람실 빈자리를 찾는 데 시간이 걸린다는 것이었습니다. "
    "도입 전 설문에서 이용자 1,860명은 자리를 찾는 데 평균 23분이 걸린다고 답했습니다. "
    "앱 도입 뒤에는 이 시간이 13분으로 줄어 42퍼센트가 짧아졌습니다."
)


def _settings_from_env_file() -> Settings:
    env = {k.lower(): v for k, v in dotenv_values(AI_DIR / ".env").items() if v}
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(_env_file=None, **env)


def test_one_slide_rubric_then_analyze_with_the_real_llm() -> None:
    s = _settings_from_env_file()
    with TestClient(create_app(s)) as client:
        assert client.app.state.readiness.wait(60)
        started = time.monotonic()
        rubric = client.post(
            "/scripts/rubric",
            json={
                "pitch_id": "p-live",
                "script_version_id": "sv-live",
                "slides": [{"slide_number": 6, "script": SCRIPT}],
            },
        )
        rubric_seconds = time.monotonic() - started
        assert rubric.status_code == 200, rubric.text
        body = rubric.json()
        assert body["failed"] == []
        (r,) = body["rubrics"]
        assert r["key_points"]
        assert body["meta"]["model"]

        started = time.monotonic()
        analyze = client.post(
            "/takes/analyze",
            json={
                "take_id": "take-live",
                "slides": [{"slide_number": 6, "stt": SCRIPT}],
                "rubrics": [r],
            },
        )
        analyze_seconds = time.monotonic() - started
        assert analyze.status_code == 200, analyze.text
        result = analyze.json()
        print(
            f"model={body['meta']['model']} rubric={rubric_seconds:.1f}s "
            f"analyze={analyze_seconds:.1f}s scores={result['take_scores']}"
        )
        assert result["failed"] == []
        assert result["take_scores"]["scored_ratio"] == 1.0
        # 대본을 그대로 읽었으므로 핵심 내용 전달률이 높아야 한다
        assert result["take_scores"]["content_coverage"] >= 0.8
