"""state_store 는 진짜 Redis 를 상대로 테스트한다.

fakeredis 를 쓰면 GETDEL 의 원자성처럼 우리가 의존하는 성질을 확인할 수 없다.
테스트 전용 DB 번호를 쓰고 매 테스트마다 비운다.
"""

from collections.abc import Generator
from urllib.parse import urlparse

import pytest
import redis

from pitch_coach_backend.module.auth import state_store
from pitch_coach_backend.module.auth.state_store import AuthorizationRequest

# 개발용 0번과 겹치지 않게 15번을 쓴다
TEST_REDIS_DB = 15


@pytest.fixture
def redis_client() -> Generator[redis.Redis]:
    from pitch_coach_backend.core.config import settings

    # REDIS_URL 의 DB 번호만 테스트용으로 바꾼다. flushdb 가 개발 데이터를 지우면 안 된다.
    url = urlparse(settings.redis_url)._replace(path=f"/{TEST_REDIS_DB}").geturl()
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def _request(**kw: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        nonce=kw.get("nonce", "nonce-1"),
        code_verifier=kw.get("code_verifier", "verifier-1"),
        browser_token=kw.get("browser_token", "browser-1"),
        return_to=kw.get("return_to", "/"),
    )


def test_generated_values_are_unique() -> None:
    assert len({state_store.new_state() for _ in range(100)}) == 100
    assert len({state_store.new_nonce() for _ in range(100)}) == 100
    assert len({state_store.new_browser_token() for _ in range(100)}) == 100


def test_save_then_consume_roundtrip(redis_client: redis.Redis) -> None:
    state = state_store.new_state()
    original = _request(return_to="/dashboard")
    state_store.save(redis_client, state, original)

    assert state_store.consume(redis_client, state) == original


def test_consume_is_one_shot(redis_client: redis.Redis) -> None:
    """같은 state 로 콜백이 두 번 들어와도 한 번만 통과한다 (재생 공격 차단)."""
    state = state_store.new_state()
    state_store.save(redis_client, state, _request())

    assert state_store.consume(redis_client, state) is not None
    assert state_store.consume(redis_client, state) is None


def test_unknown_state_returns_none(redis_client: redis.Redis) -> None:
    assert state_store.consume(redis_client, "never-issued") is None


def test_saved_request_expires(redis_client: redis.Redis) -> None:
    """TTL 없이 저장하면 오래된 state 가 영원히 유효해진다."""
    state = state_store.new_state()
    state_store.save(redis_client, state, _request())

    ttl = redis_client.ttl(f"oauth:state:{state}")
    assert 0 < ttl <= state_store.STATE_TTL_SECONDS


def test_states_do_not_collide(redis_client: redis.Redis) -> None:
    first, second = state_store.new_state(), state_store.new_state()
    state_store.save(redis_client, first, _request(nonce="first"))
    state_store.save(redis_client, second, _request(nonce="second"))

    assert state_store.consume(redis_client, first).nonce == "first"
    assert state_store.consume(redis_client, second).nonce == "second"
