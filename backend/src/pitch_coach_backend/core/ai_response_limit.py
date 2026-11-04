"""AI 응답 본문 크기 상한. AI 를 부르는 `httpx.AsyncClient` 마다 `event_hooks=AI_RESPONSE_HOOKS`
로 건다.

`client.post` 는 돌아오기 전에 본문을 다 읽는다. 받은 뒤 길이를 재면 이미 메모리에 올라온 뒤라
전송 크기와 gzip·deflate 를 푼 크기를 읽는 도중에 센다. 넘으면 `AiResponseTooLarge` 다.
`httpx.HTTPError` 라 부르는 곳의 AI 호출 실패 처리를 탄다.

16 MiB: 가장 큰 정상 응답은 상한(50,000자)까지 쓴 대본의 평가 기준 · 채점 응답으로 2~3MB 로 추정한다
(AI 예시 응답 크기와 대본 대비 비율). 그 약 5배다. AI 버그로 오는 수백 MB 응답을 막는 것이 목적이다.
"""

import zlib
from collections.abc import AsyncIterator

import httpx

MAX_AI_RESPONSE_BYTES = 16 * 1024 * 1024


class AiResponseTooLarge(httpx.DecodingError):
    """AI 응답 본문이 `MAX_AI_RESPONSE_BYTES` 를 넘었다."""


class _LimitedDecoder:
    """HTTPX 가 압축을 풀기 전에 각 단계의 출력 크기를 제한한다."""

    def __init__(self, encoding: str, request: httpx.Request) -> None:
        self._request = request
        self._received = 0
        self._try_raw = encoding == "deflate"
        self._decoder = zlib.decompressobj(
            16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        )

    def decode(self, data: bytes) -> bytes:
        try_raw = self._try_raw
        self._try_raw = False
        try:
            # 전체를 푼 뒤 길이를 재면 늦다. 남은 허용량보다 한 바이트까지만 만든다.
            decoded = self._decoder.decompress(data, MAX_AI_RESPONSE_BYTES - self._received + 1)
        except zlib.error as exc:
            if try_raw:
                # HTTPX 와 같이 zlib 헤더 없는 deflate 도 받는다.
                self._decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                return self.decode(data)
            raise httpx.DecodingError(
                "AI 응답의 압축을 풀지 못했다", request=self._request
            ) from exc
        self._received += len(decoded)
        if self._received > MAX_AI_RESPONSE_BYTES:
            raise _too_large(self._request)
        return decoded


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(
        self, stream: httpx.AsyncByteStream, request: httpx.Request, encodings: list[str]
    ) -> None:
        self._stream = stream
        self._request = request
        self._decoders = [
            _LimitedDecoder(encoding, request)
            for encoding in reversed(encodings)
            if encoding != "identity"
        ]

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        async for chunk in self._stream:
            received += len(chunk)
            if received > MAX_AI_RESPONSE_BYTES:
                raise _too_large(self._request)
            decoded = chunk
            for decoder in self._decoders:
                decoded = decoder.decode(decoded)
            # 검사 결과는 버리고 원본을 넘겨 HTTPX 의 응답 헤더·디코딩 동작을 유지한다.
            del decoded
            yield chunk
        # flush 는 출력 상한이 없으므로, 내부에 남은 출력도 제한된 decompress 로 확인한다.
        decoded = b""
        for decoder in self._decoders:
            decoded = decoder.decode(decoded)

    async def aclose(self) -> None:
        await self._stream.aclose()


def _too_large(request: httpx.Request) -> AiResponseTooLarge:
    return AiResponseTooLarge(f"AI 응답이 {MAX_AI_RESPONSE_BYTES} 바이트를 넘었다", request=request)


async def _limit_body(response: httpx.Response) -> None:
    # 응답 훅은 본문을 읽기 전에 불린다. Content-Length 가 이미 넘으면 읽지 않고 끊는다
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_AI_RESPONSE_BYTES:
        raise _too_large(response.request)
    encodings = [
        value.strip().lower()
        for value in response.headers.get_list("content-encoding", split_commas=True)
    ]
    if any(encoding not in {"identity", "gzip", "deflate"} for encoding in encodings):
        raise httpx.DecodingError("AI 응답의 압축 형식을 지원하지 않는다", request=response.request)
    if response.is_stream_consumed:
        # MockTransport 등에서 이미 읽어 둔 본문은 HTTPX 가 압축도 푼 상태다.
        if len(response.content) > MAX_AI_RESPONSE_BYTES:
            raise _too_large(response.request)
        return
    stream = response.stream
    assert isinstance(stream, httpx.AsyncByteStream)
    response.stream = _LimitedStream(stream, response.request, encodings)


async def _accept_supported_encodings(request: httpx.Request) -> None:
    # 선택 의존성이 추가돼도 크기를 검사할 수 있는 압축만 요청한다.
    request.headers["accept-encoding"] = "gzip, deflate"


AI_RESPONSE_HOOKS = {"request": [_accept_supported_encodings], "response": [_limit_body]}
