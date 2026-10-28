"""AI 응답 본문 크기 상한. AI 를 부르는 `httpx.AsyncClient` 마다 `event_hooks=AI_RESPONSE_HOOKS`
로 건다.

`client.post` 는 돌아오기 전에 본문을 다 읽는다. 받은 뒤 길이를 재면 이미 메모리에 올라온 뒤라
읽는 도중에 센다. 넘으면 `AiResponseTooLarge` 다. `httpx.HTTPError` 라 부르는 곳의 AI 호출 실패
처리를 탄다.

16 MiB: 가장 큰 정상 응답은 상한(50,000자)까지 쓴 대본의 평가 기준 · 채점 응답으로 2~3MB 로 추정한다
(AI 예시 응답 크기와 대본 대비 비율). 그 약 5배다. AI 버그로 오는 수백 MB 응답을 막는 것이 목적이다.
"""

from collections.abc import AsyncIterator

import httpx

MAX_AI_RESPONSE_BYTES = 16 * 1024 * 1024


class AiResponseTooLarge(httpx.DecodingError):
    """AI 응답 본문이 `MAX_AI_RESPONSE_BYTES` 를 넘었다."""


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, request: httpx.Request) -> None:
        self._stream = stream
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        async for chunk in self._stream:
            received += len(chunk)
            if received > MAX_AI_RESPONSE_BYTES:
                raise _too_large(self._request)
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


def _too_large(request: httpx.Request) -> AiResponseTooLarge:
    return AiResponseTooLarge(f"AI 응답이 {MAX_AI_RESPONSE_BYTES} 바이트를 넘었다", request=request)


async def _limit_body(response: httpx.Response) -> None:
    # 응답 훅은 본문을 읽기 전에 불린다. Content-Length 가 이미 넘으면 읽지 않고 끊는다
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_AI_RESPONSE_BYTES:
        raise _too_large(response.request)
    stream = response.stream
    assert isinstance(stream, httpx.AsyncByteStream)
    response.stream = _LimitedStream(stream, response.request)


AI_RESPONSE_HOOKS = {"response": [_limit_body]}
