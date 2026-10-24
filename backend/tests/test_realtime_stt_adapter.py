"""Deepgram 어댑터. 외부 통신은 가짜 서버로, 파싱은 실제 코드로.

가짜 서버는 websockets 로 띄운다 — Deepgram 프로토콜이 JSON 4종뿐이라 그대로 흉내 낼 수 있다.
"""

import asyncio
import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.asyncio.server import serve

from pitch_coach_backend.realtime import stt_adapter
from pitch_coach_backend.realtime.stt_adapter import (
    DeepgramSttAdapter,
    Metadata,
    SpeechStarted,
    SttConfig,
    SttConfigRejected,
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
        adapter = DeepgramSttAdapter(["test-key"], base_url=f"ws://127.0.0.1:{port}/v1/listen")

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
        adapter = DeepgramSttAdapter(["test-key"], base_url=f"ws://127.0.0.1:{port}/v1/listen")

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
        adapter = DeepgramSttAdapter(["test-key"], base_url=f"ws://127.0.0.1:{port}/v1/listen")
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
    adapter = DeepgramSttAdapter(["test-key"], base_url="ws://127.0.0.1:1/v1/listen")
    with pytest.raises(SttConnectError):
        await adapter.connect(SttConfig())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "rejected"),
    [(HTTPStatus.BAD_REQUEST, True), (HTTPStatus.UNAUTHORIZED, False)],
)
async def test_http_400_on_handshake_is_config_rejected(status: HTTPStatus, rejected: bool):
    # 400 만 "설정 문제" 다. 401(키 오류) 같은 건 그대로 SttConnectError
    def refuse(conn, request):
        return conn.respond(status, "Keyterm limit exceeded.")

    async with serve(lambda conn: None, "127.0.0.1", 0, process_request=refuse) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter(["test-key"], base_url=f"ws://127.0.0.1:{port}/v1/listen")
        with pytest.raises(SttConnectError) as info:
            await adapter.connect(SttConfig(keyterms=("음",)))

    assert isinstance(info.value, SttConfigRejected) is rejected
    if rejected:
        assert "Keyterm limit exceeded." in str(info.value)


# ── 키 여러 개 ────────────────────────────────────────────────────────


class KeyedDeepgram(FakeDeepgram):
    """refuse 에 든 키는 연결 요청에서 그 코드로 거절한다. 테스트 도중 바꿀 수 있다."""

    def __init__(self, refuse: dict[str, HTTPStatus] | None = None) -> None:
        super().__init__()
        self.refuse = dict(refuse or {})
        self.tried: list[str] = []

    def process_request(self, conn, request):
        key = request.headers.get("Authorization", "").removeprefix("Token ")
        self.tried.append(key)
        if key in self.refuse:
            return conn.respond(self.refuse[key], "Project does not have enough credits.")
        return None


async def _close(session) -> None:
    await session.close_stream()
    [event async for event in session]


@pytest.mark.anyio
async def test_rejected_key_moves_to_next_key_and_is_skipped_afterwards():
    fake = KeyedDeepgram({"key-1": HTTPStatus.PAYMENT_REQUIRED})
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter(
            ["key-1", "key-2"], base_url=f"ws://127.0.0.1:{port}/v1/listen"
        )
        await _close(await adapter.connect(SttConfig()))
        await _close(await adapter.connect(SttConfig()))

    # 한 번의 connect 안에서 바로 다음 키로 붙고, 두 번째 연결은 소진된 키를 건너뛴다
    assert fake.tried == ["key-1", "key-2", "key-2"]


@pytest.mark.anyio
async def test_key_exhausted_after_session_loss_moves_to_next_key():
    # 발표 도중 크레딧이 떨어져 끊긴 뒤 다시 붙는 경우. 재접속이 402 로 거절되면 다음 키다
    fake = KeyedDeepgram()
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter(
            ["key-1", "key-2"], base_url=f"ws://127.0.0.1:{port}/v1/listen"
        )
        await _close(await adapter.connect(SttConfig()))
        fake.refuse["key-1"] = HTTPStatus.PAYMENT_REQUIRED
        await _close(await adapter.connect(SttConfig()))

    assert fake.tried == ["key-1", "key-1", "key-2"]


@pytest.mark.anyio
async def test_all_keys_rejected_logs_error_and_retries_from_first_key(caplog):
    fake = KeyedDeepgram({"key-1": HTTPStatus.PAYMENT_REQUIRED, "key-2": HTTPStatus.UNAUTHORIZED})
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter(
            ["key-1", "key-2"], base_url=f"ws://127.0.0.1:{port}/v1/listen"
        )
        with (
            caplog.at_level("WARNING", logger=stt_adapter.logger.name),
            pytest.raises(SttConnectError) as info,
        ):
            await adapter.connect(SttConfig())
        assert not isinstance(info.value, SttConfigRejected)

        # 그사이 첫 키에 크레딧을 채웠다. 재시작 없이 다음 재시도에서 다시 붙는다
        del fake.refuse["key-1"]
        await _close(await adapter.connect(SttConfig()))

    assert fake.tried == ["key-1", "key-2", "key-1"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert [r.getMessage() for r in errors] == ["Deepgram 키 2개가 모두 거절됐다"]
    # 로그에는 몇 번째 키인지만 남는다
    assert "1번째" in caplog.text and "2번째" in caplog.text
    assert "key-1" not in caplog.text and "key-2" not in caplog.text


@pytest.mark.anyio
async def test_other_handshake_failures_do_not_exhaust_the_key():
    # 일시 오류(5xx)는 키 탓이 아니다. 다음 키로 넘기지 않고 지금처럼 backoff 재시도에 맡긴다
    fake = KeyedDeepgram({"key-1": HTTPStatus.SERVICE_UNAVAILABLE})
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = DeepgramSttAdapter(
            ["key-1", "key-2"], base_url=f"ws://127.0.0.1:{port}/v1/listen"
        )
        with pytest.raises(SttConnectError):
            await adapter.connect(SttConfig())
        del fake.refuse["key-1"]
        await _close(await adapter.connect(SttConfig()))

    assert fake.tried == ["key-1", "key-1"]


@pytest.mark.parametrize("keys", ["", "test-key", []])
def test_adapter_needs_a_list_of_keys(keys):
    with pytest.raises(ValueError):
        DeepgramSttAdapter(keys)
