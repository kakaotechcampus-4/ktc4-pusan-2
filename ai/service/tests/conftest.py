"""공통 fixture.

- `client`: 실제 네트워크 없이 앱에 요청을 보내는 TestClient. 테스트마다 앱을 새로 만들어
  한 테스트의 설정이 다른 테스트로 새지 않게 한다
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c
