"""코치 retention 실행부의 실패·복구 회귀 테스트."""


import pytest

from pitch_coach_backend.coach import retention
from tests.coach_fakes import FakeStore


@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_retention_purges_and_survives_errors(caplog: pytest.LogCaptureFixture):
    store = FakeStore()
    assert await retention.purge_once(store) == 3

    class Broken(FakeStore):
        async def purge_expired_raw(self, now):
            raise RuntimeError("db down")

    with caplog.at_level("ERROR"):
        assert await retention.purge_once(Broken()) == 0
    assert "지우지 못했다" in caplog.text
