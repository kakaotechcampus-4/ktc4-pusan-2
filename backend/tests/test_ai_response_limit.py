"""AI 응답 본문 크기 상한 (`core/ai_response_limit.py`)."""

import zlib
from collections.abc import AsyncIterator
from functools import partial

import httpx
import pytest

from pitch_coach_backend.core import ai_response_limit
from pitch_coach_backend.core.ai_response_limit import AI_RESPONSE_HOOKS, AiResponseTooLarge
from pitch_coach_backend.module.pitch.adapter.script_parser import (
    HttpScriptParser,
    ScriptParseError,
)
from pitch_coach_backend.module.pitch.dto import ScriptParseErrorCode
from pitch_coach_backend.module.take.service import coverage as coverage_service

LIMIT = 10
PRODUCTION_LIMIT = ai_response_limit.MAX_AI_RESPONSE_BYTES

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_response_limit, "MAX_AI_RESPONSE_BYTES", LIMIT)


class _Chunks(httpx.AsyncByteStream):
    """Content-Length 없이 조각으로 오는 본문."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(
    params=[("gzip", 31), ("deflate", 15), ("deflate", -15)],
    ids=["gzip", "deflate", "raw-deflate"],
)
def compression(request: pytest.FixtureRequest) -> tuple[str, int]:
    return request.param


@pytest.fixture
def compressed_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    # 압축 헤더 자체가 상한을 넘지 않도록 기존 10바이트보다 크게 잡는다.
    limit = 1024
    monkeypatch.setattr(ai_response_limit, "MAX_AI_RESPONSE_BYTES", limit)
    return limit


async def _post(response: httpx.Response) -> httpx.Response:
    transport = httpx.MockTransport(lambda request: response)
    async with httpx.AsyncClient(transport=transport, event_hooks=AI_RESPONSE_HOOKS) as client:
        return await client.post("http://ai/x")


async def test_body_within_the_limit_is_read_as_is() -> None:
    response = await _post(httpx.Response(200, stream=_Chunks(b"12345", b"67890")))

    assert response.content == b"1234567890"


async def test_content_length_over_the_limit_is_cut_before_reading() -> None:
    stream = _Chunks(b"x" * (LIMIT + 1))
    with pytest.raises(AiResponseTooLarge):
        await _post(httpx.Response(200, headers={"Content-Length": str(LIMIT + 1)}, stream=stream))
    assert stream.chunks_read == 0
    assert stream.closed


async def test_body_without_content_length_is_cut_while_reading() -> None:
    """AI 가 조각으로 보내면 길이를 미리 알 수 없다. 읽은 양이 넘는 순간 끊는다."""
    with pytest.raises(AiResponseTooLarge):
        await _post(httpx.Response(200, stream=_Chunks(b"123456", b"789012")))


@pytest.mark.parametrize("split", [False, True], ids=["one-chunk", "byte-chunks"])
@pytest.mark.parametrize("below_limit", [False, True], ids=["at-limit", "below-limit"])
async def test_compressed_body_within_the_limit_is_read_as_is(
    compression: tuple[str, int], compressed_limit: int, split: bool, below_limit: bool
) -> None:
    encoding, wbits = compression
    body = b"x" * (compressed_limit - int(below_limit))
    encoded = zlib.compress(body, wbits=wbits)
    # HTTPX 의 raw deflate 판별은 첫 조각에서 하므로 그 경우 첫 두 바이트를 함께 보낸다.
    first_size = 2 if wbits < 0 else 1
    chunks = (
        [encoded[:first_size], *[encoded[i : i + 1] for i in range(first_size, len(encoded))]]
        if split
        else [encoded]
    )
    stream = _Chunks(*chunks)
    response = await _post(
        httpx.Response(
            200,
            headers={"Content-Encoding": encoding, "Content-Length": str(len(encoded))},
            stream=stream,
        )
    )

    assert response.content == body
    assert response.headers["content-encoding"] == encoding
    assert response.num_bytes_downloaded == len(encoded)
    assert stream.closed


@pytest.mark.parametrize("with_length", [False, True])
async def test_compressed_body_over_the_limit_stops_before_the_next_chunk(
    compression: tuple[str, int], compressed_limit: int, with_length: bool
) -> None:
    encoding, wbits = compression
    encoded = zlib.compress(b"x" * (compressed_limit + 1), wbits=wbits)
    assert len(encoded) < compressed_limit
    headers = {"Content-Encoding": encoding}
    if with_length:
        headers["Content-Length"] = str(len(encoded))
    stream = _Chunks(encoded, b"unread")
    response = httpx.Response(200, headers=headers, stream=stream)

    with pytest.raises(AiResponseTooLarge) as caught:
        await _post(response)

    assert str(caught.value.request.url) == "http://ai/x"
    assert stream.chunks_read == 1
    assert stream.closed
    with pytest.raises(httpx.ResponseNotRead):
        _ = response.content


async def test_compressed_chunks_are_counted_together(
    compression: tuple[str, int], compressed_limit: int
) -> None:
    encoding, wbits = compression
    compressor = zlib.compressobj(wbits=wbits)
    first_size = compressed_limit // 2
    first = compressor.compress(b"x" * first_size) + compressor.flush(zlib.Z_SYNC_FLUSH)
    second = compressor.compress(b"x" * (compressed_limit - first_size + 1)) + compressor.flush()
    stream = _Chunks(first, second, b"unread")

    with pytest.raises(AiResponseTooLarge):
        await _post(httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream))

    assert stream.chunks_read == 2
    assert stream.closed


@pytest.mark.parametrize("over_limit", [False, True], ids=["at-16MiB", "over-16MiB"])
async def test_compressed_body_obeys_the_production_limit(
    compression: tuple[str, int], monkeypatch: pytest.MonkeyPatch, over_limit: bool
) -> None:
    monkeypatch.setattr(ai_response_limit, "MAX_AI_RESPONSE_BYTES", PRODUCTION_LIMIT)
    encoding, wbits = compression
    body = b"x" * (PRODUCTION_LIMIT + int(over_limit))
    encoded = zlib.compress(body, wbits=wbits)
    assert len(encoded) < PRODUCTION_LIMIT
    stream = _Chunks(encoded)
    response = httpx.Response(
        200,
        headers={"Content-Encoding": encoding, "Content-Length": str(len(encoded))},
        stream=stream,
    )

    if over_limit:
        with pytest.raises(AiResponseTooLarge):
            await _post(response)
    else:
        assert (await _post(response)).content == body
    assert stream.closed


@pytest.mark.parametrize("encodings", [("gzip", "deflate"), ("deflate", "gzip")])
@pytest.mark.parametrize("over_limit", [False, True])
async def test_stacked_encodings_obey_the_decoded_limit(
    encodings: tuple[str, str], compressed_limit: int, over_limit: bool
) -> None:
    body = b"x" * (compressed_limit + int(over_limit))
    encoded = body
    for encoding in encodings:
        encoded = zlib.compress(encoded, wbits=31 if encoding == "gzip" else 15)
    response = httpx.Response(
        200, headers={"Content-Encoding": ", ".join(encodings)}, stream=_Chunks(encoded)
    )

    if over_limit:
        with pytest.raises(AiResponseTooLarge):
            await _post(response)
    else:
        assert (await _post(response)).content == body


@pytest.mark.parametrize("encoding", ["br", "zstd", "unknown"])
async def test_unsupported_encoding_is_rejected_before_reading(encoding: str) -> None:
    stream = _Chunks(b"x")
    with pytest.raises(httpx.DecodingError):
        await _post(httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream))
    assert stream.chunks_read == 0
    assert stream.closed


async def test_request_only_advertises_encodings_with_a_size_limit() -> None:
    headers: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers["accept-encoding"])
        return httpx.Response(200, content=b"ok")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        event_hooks=AI_RESPONSE_HOOKS,
        headers={"Accept-Encoding": "gzip, deflate, br, zstd"},
    ) as client:
        await client.post("http://ai/x")

    assert headers == ["gzip, deflate"]


async def test_preloaded_compressed_body_is_checked_after_decoding(
    compression: tuple[str, int], compressed_limit: int
) -> None:
    encoding, wbits = compression
    response = httpx.Response(
        200,
        headers={"Content-Encoding": encoding},
        content=zlib.compress(b"x" * (compressed_limit + 1), wbits=wbits),
    )
    with pytest.raises(AiResponseTooLarge):
        await _post(response)


async def test_script_parser_treats_a_compressed_too_large_body_as_invalid_output(
    compression: tuple[str, int], compressed_limit: int
) -> None:
    encoding, wbits = compression
    response = httpx.Response(
        200,
        headers={"Content-Encoding": encoding},
        stream=_Chunks(zlib.compress(b"x" * (compressed_limit + 1), wbits=wbits)),
    )
    parser = HttpScriptParser(
        "http://ai", attempts=1, transport=httpx.MockTransport(lambda request: response)
    )

    with pytest.raises(ScriptParseError) as caught:
        await parser.parse("x", None)

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br"])
async def test_script_parser_treats_invalid_encoding_as_invalid_output(
    encoding: str, compressed_limit: int
) -> None:
    stream = _Chunks(b"not compressed")
    response = httpx.Response(200, headers={"Content-Encoding": encoding}, stream=stream)
    parser = HttpScriptParser(
        "http://ai", attempts=1, transport=httpx.MockTransport(lambda request: response)
    )

    with pytest.raises(ScriptParseError) as caught:
        await parser.parse("x", None)

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT
    assert stream.closed


async def test_script_parser_treats_a_too_large_body_as_invalid_output() -> None:
    """`httpx.TransportError` 만 잡는 곳이라 따로 받는다."""
    parser = HttpScriptParser(
        "http://ai",
        attempts=1,
        retry_delay_sec=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64)),
    )

    with pytest.raises(ScriptParseError) as caught:
        await parser.parse("x", None)

    assert caught.value.code is ScriptParseErrorCode.AI_INVALID_OUTPUT


async def test_coverage_treats_a_too_large_body_as_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64))
    monkeypatch.setattr(
        coverage_service.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )

    with pytest.raises(coverage_service.CoverageCallError):
        await coverage_service.request_analyze({"take_id": "t"}, "id")
