"""공통 fixture.

- 모델 묶음 환경변수를 빈 값으로 덮은 뒤에 앱을 import 한다. 진짜 환경변수가
  `ai/.env` 보다 앞서므로, 단위 테스트는 로컬 `ai/.env` 의 실제 키를 읽지 않는다.
  그래서 내 PC 와 CI(키 없음)의 결과가 같고, 실수로 실제 LLM 을 부를 수 없다.
  실제 호출은 `tests/live/` 가 `ai/.env` 를 직접 읽어 따로 한다
- `client`: 실제 네트워크 없이 앱에 요청을 보내는 TestClient. 테스트마다 앱을
  새로 만들어 한 테스트의 설정이 다른 테스트로 새지 않게 한다
"""

import os

for _name in (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_SOL_BASE_URL",
    "OPENAI_SOL_API_KEY",
    "OPENAI_SOL_MODEL",
):
    os.environ[_name] = ""

from collections.abc import Iterator  # noqa: E402  # 위 환경변수를 먼저 넣어야 한다

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from pitch_coach_ai.main import create_app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c
