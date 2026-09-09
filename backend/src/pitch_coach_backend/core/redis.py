"""Redis 클라이언트 하나와 주입용 의존성.

지금은 OAuth 의 state/nonce/PKCE 임시 저장에만 쓴다. 나중에 realtime 세션과
rate limit 이 같은 클라이언트를 공유한다.

decode_responses=True: 저장하는 값이 전부 문자열(JSON)이라 매번 decode 하지 않는다.
"""

from collections.abc import Generator

import redis

from pitch_coach_backend.core.config import settings

# 커넥션 풀은 클라이언트가 내부적으로 관리한다. 프로세스당 하나면 충분하다.
redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Generator[redis.Redis]:
    yield redis_client
