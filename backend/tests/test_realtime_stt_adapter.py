"""Deepgram 어댑터. 외부 통신은 가짜 서버로, 파싱은 실제 코드로.

가짜 서버는 websockets 로 띄운다 — Deepgram 프로토콜이 JSON 4종뿐이라 그대로 흉내 낼 수 있다.
"""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.asyncio.server import serve

from pitch_coach_backend.realtime import stt_adapter
from pitch_coach_backend.realtime.stt_adapter import (
    DeepgramSttAdapter,
    Metadata,
    SpeechStarted,
    SttConfig,
    SttConnectError,
    SttError,
    Transcript,
    UtteranceEnd,
    build_listen_url,
    parse_message,
)

RESULTS = {
    "type": "Results",
    "channel_index": [0, 1],
    "duration": 1.02,
    "start": 10.24,
    "is_final": True,
    "speech_final": True,
    "channel": {
        "alternatives": [
            {
                "transcript": "안녕하세요 피치코치입니다",
                "confidence": 0.93,
                "words": [
                    {
                        "word": "안녕하세요",
                        "start": 10.42,
                        "end": 10.98,
                        "confidence": 0.94,
                        "punctuated_word": "안녕하세요",
                    },
                    {
                        "word": "피치코치입니다",
                        "start": 11.01,
                        "end": 11.26,
                        "confidence": 0.88,
                        "punctuated_word": "피치코치입니다.",
                    },
                ],
            }
        ]
    },
    "metadata": {"request_id": "req-1"},
}


# ── URL ───────────────────────────────────────────────────────────────


def test_listen_url_has_raw_pcm_params_and_repeated_keyterms():
    url = build_listen_url("wss://example/v1/listen", SttConfig(keyterms=("음", "피치코치")))
    query = parse_qs(urlsplit(url).query)

    assert query["model"] == ["nova-3"]
    assert query["language"] == ["ko"]
    assert query["encoding"] == ["linear16"]
    assert query["sample_rate"] == ["16000"]
    assert query["channels"] == ["1"]
    assert query["interim_results"] == ["true"]
    assert query["punctuate"] == ["true"]
    assert query["smart_format"] == ["false"]
    assert query["keyterm"] == ["음", "피치코치"]
    assert "tag" not in query


def test_listen_url_tag_is_optional():
    url = build_listen_url("wss://example/v1/listen", SttConfig(tag="take:abc"))
    assert parse_qs(urlsplit(url).query)["tag"] == ["take:abc"]


# ── 파싱 ──────────────────────────────────────────────────────────────


def test_parse_results_converts_seconds_to_ms_int():
    event = parse_message(json.dumps(RESULTS))

    assert isinstance(event, Transcript)
    assert (event.start_ms, event.end_ms) == (10240, 11260)
    assert event.is_final and event.speech_final and not event.from_finalize
    assert event.transcript == "안녕하세요 피치코치입니다"
    assert event.confidence == pytest.approx(0.93)
    first, second = event.words
    assert (first.word, first.start_ms, first.end_ms) == ("안녕하세요", 10420, 10980)
    assert second.punctuated_word == "피치코치입니다."
    assert all(isinstance(w.start_ms, int) for w in event.words)


def test_parse_empty_interim():
    raw = {
        "type": "Results",
        "start": 0,
        "duration": 0.5,
        "is_final": False,
        "speech_final": False,
        "channel": {"alternatives": [{"transcript": "", "confidence": 0, "words": []}]},
    }
    event = parse_message(json.dumps(raw))
    assert isinstance(event, Transcript)
    assert event.transcript == "" and event.words == ()


def test_parse_other_message_types():
    assert parse_message('{"type":"UtteranceEnd","channel":[0,1],"last_word_end":2.5}') == (
        UtteranceEnd(last_word_end_ms=2500)
    )
    assert parse_message('{"type":"SpeechStarted","channel":[0,1],"timestamp":0.15}') == (
        SpeechStarted(timestamp_ms=150)
    )
    assert parse_message('{"type":"Metadata","request_id":"r","duration":612.4}') == (
        Metadata(request_id="r", duration_ms=612400)
    )
    assert parse_message('{"type":"Error","description":"bad","variant":"x"}') == (
        SttError(code="x", message="bad")
    )


def test_parse_ignores_unknown_and_malformed():
    assert parse_message('{"type":"SomethingNew"}') is None
    assert parse_message("not json") is None
    assert parse_message("[1,2]") is None


# ── 세션 (가짜 Deepgram 서버) ──────────────────────────────────────────


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeDeepgram:
    """받은 것을 기록하고, 오디오마다 RESULTS 를, CloseStream 에 Metadata 를 돌려준다."""

    def __init__(self, *, close_code: int | None = None) -> None:
        self.audio: list[bytes] = []
        self.controls: list[dict] = []
        self.request_path = ""
        self.auth_header = ""
        # None 이면 CloseStream 에 정상(1000) 종료, 아니면 그 코드로 에러 종료
        self.close_code = close_code

    async def handler(self, conn) -> None:
        self.request_path = conn.request.path
        self.auth_header = conn.request.headers.get("Authorization", "")
        async for message in conn:
            if isinstance(message, bytes):
                self.audio.append(message)
                await conn.send(json.dumps(RESULTS))
                continue
            data = json.loads(message)
            self.controls.append(data)
            if data["type"] == "CloseStream":
                if self.close_code is None:
                    metadata = {"type": "Metadata", "request_id": "r", "duration": 0.1}
                    await conn.send(json.dumps(metadata))
                    await conn.close(1000)
                else:
                    await conn.close(self.close_code, "NET-0000")
                return


@pytest.mark.anyio
async def test_session_roundtrip():
    fake = FakeDeepgram()
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter("test-key", base_url=f"ws://127.0.0.1:{port}/v1/listen")

        session = await adapter.connect(SttConfig(keyterms=("음",)))
        await session.send_audio(b"\x00" * 3200)
        await session.finalize()
        await session.close_stream()
        events = [event async for event in session]

    assert fake.auth_header == "Token test-key"
    assert "keyterm=%EC%9D%8C" in fake.request_path
    assert fake.audio == [b"\x00" * 3200]
    assert fake.controls == [{"type": "Finalize"}, {"type": "CloseStream"}]
    assert isinstance(events[0], Transcript)
    assert events[-1] == Metadata(request_id="r", duration_ms=100)


@pytest.mark.anyio
async def test_error_close_becomes_single_stt_error():
    fake = FakeDeepgram(close_code=1011)
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter("test-key", base_url=f"ws://127.0.0.1:{port}/v1/listen")

        session = await adapter.connect(SttConfig())
        await session.close_stream()
        events = [event async for event in session]

    assert events == [SttError(code="CONNECTION_CLOSED", message="1011 NET-0000")]


@pytest.mark.anyio
async def test_keepalive_is_sent_only_while_idle(monkeypatch):
    monkeypatch.setattr(stt_adapter, "KEEPALIVE_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(stt_adapter, "KEEPALIVE_POLL_SEC", 0.01)

    fake = FakeDeepgram()
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter("test-key", base_url=f"ws://127.0.0.1:{port}/v1/listen")
        session = await adapter.connect(SttConfig())

        task = asyncio.create_task(session.keepalive_loop())
        await asyncio.sleep(0.2)
        await session.close_stream()
        await asyncio.wait_for(task, 1.0)
        [event async for event in session]

    keepalives = [c for c in fake.controls if c["type"] == "KeepAlive"]
    assert keepalives, "유휴 상태에서 KeepAlive 가 가야 한다"
    # CloseStream 뒤에는 KeepAlive 가 없다
    assert fake.controls[-1] == {"type": "CloseStream"}


@pytest.mark.anyio
async def test_connect_failure_is_one_exception_type():
    adapter = DeepgramSttAdapter("test-key", base_url="ws://127.0.0.1:1/v1/listen")
    with pytest.raises(SttConnectError):
        await adapter.connect(SttConfig())
