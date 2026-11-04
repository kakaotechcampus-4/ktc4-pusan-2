"""코칭 계획을 실제 LLM 으로 한 번 (과금, 호출 1번). `uv run python -m pytest -m live`.

`POST /coach/plan` 을 서비스의 LLM 객체 · 역할(`coach.plan`) · 마감(25초)으로 부르고,
기본 계획이 아니라 LLM 계획이 검증을 통과해 돌아오는지 본다
(코치 기능 문서 11절 '옮긴 뒤 같은 결과인지 확인' 4).
`ai/.env` 의 기본 묶음을 직접 읽는다(`test_llm_live.py` 와 같다).
"""

import time

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import AI_DIR, Settings

pytestmark = pytest.mark.live

REQUEST = {
    "pitch_id": "p-live",
    "plan": {
        "target_ms": 300_000,
        "min_ms": 270_000,
        "max_ms": 330_000,
        "slides": [
            {"slide_number": 1, "target_ms": 60_000, "script_chars": 300},
            {"slide_number": 2, "target_ms": 120_000, "script_chars": 600},
            {"slide_number": 3, "target_ms": 120_000, "script_chars": 600},
        ],
    },
    "scripts": [
        {"slide_number": 1, "script": "안녕하세요, 도서관 좌석 예약 앱을 소개합니다."},
        {"slide_number": 2, "script": "도입 전 학생들은 빈자리를 찾는 데 평균 23분이 걸렸습니다."},
        {"slide_number": 3, "script": "매출은 3분기 12억 원으로 전년 대비 18.2% 늘었습니다."},
    ],
}


def _settings_from_env_file() -> Settings:
    env = {k.lower(): v for k, v in dotenv_values(AI_DIR / ".env").items() if v}
    if not env.get("openai_api_key"):
        pytest.skip("ai/.env 에 기본 묶음(OPENAI_*)이 없다")
    return Settings(_env_file=None, **env)


def test_coach_plan_with_the_real_llm() -> None:
    with TestClient(create_app(_settings_from_env_file())) as client:
        started = time.monotonic()
        res = client.post("/coach/plan", json=REQUEST)
        seconds = time.monotonic() - started
    assert res.status_code == 200, res.text
    body = res.json()
    print(f"model={body['meta']['model']} seconds={seconds:.1f} plan={body['coaching_plan']}")
    assert body["fallback_reason"] is None
    assert body["meta"]["model"]
    assert seconds < 25
