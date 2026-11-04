"""요청을 보낸 쪽의 IP. HTTP 요청과 WebSocket 연결이 같이 쓴다."""

from starlette.requests import HTTPConnection


def client_ip(conn: HTTPConnection) -> str:
    """연결한 쪽의 IP. Caddy 뒤라 `X-Forwarded-For` 의 첫 값이다.

    바깥에서 붙는 첫 Caddy 는 믿지 않는 상대(`infra/Caddyfile` 의 `trusted_proxies` 밖)가 보낸
    `X-Forwarded-For` 를 버리고 상대 주소로 채운다. release 는 그 Caddy 를 한 번 더 거쳐 뒤에 그
    Caddy 주소가 붙으므로 마지막 값이 아니라 첫 값을 쓴다. 헤더가 없으면(Caddy 없이 띄운 로컬) 소켓
    상대 주소다.
    """
    forwarded = conn.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return conn.client.host if conn.client is not None else "unknown"
