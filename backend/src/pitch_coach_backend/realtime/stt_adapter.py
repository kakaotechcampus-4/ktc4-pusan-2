"""Deepgram 스트리밍 STT 어댑터. DB·Redis·Take 를 모른다 (auth/google.py 와 같은 위치).

하는 일은 셋뿐이다.

1. 연결 URL 을 만들고 API 키를 헤더로 붙인다. 브라우저 WebSocket 은 헤더를 못 붙이므로
   FE → BE → Deepgram 구조가 필요한 이유가 이것이다.
2. PCM 을 흘리고 KeepAlive / Finalize / CloseStream 을 보낸다.
3. Deepgram JSON 을 우리 이벤트로 바꾼다. 초(float) 는 ms 정수로. 단 기준은 여전히
   "이 Deepgram 연결에 보낸 첫 바이트 = 0" 이다. Take 기준으로 옮기는 건 service 의 몫.

전사 내용은 사용자 발화 원문이라 로그에 남기지 않는다. request_id 와 길이만 남긴다.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK, WebSocketException

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"

# 환경마다 바뀌지 않는 값은 설정이 아니라 상수 (README 환경변수 절)
MODEL = "nova-3"  # 한국어는 Nova-3 에만 있다. Nova-2 fallback 은 없다
LANGUAGE = "ko"
SAMPLE_RATE = 16_000
CHANNELS = 1
# 16 kHz * 16-bit * mono = 32 bytes/ms. offset 계산과 무음 채우기의 기준 상수
BYTES_PER_MS = SAMPLE_RATE * 2 * CHANNELS // 1000

# Deepgram 은 오디오도 KeepAlive 도 없이 10초가 지나면 NET-0001 로 끊는다. 문서 권장 3~5초
KEEPALIVE_INTERVAL_SEC = 3.0
# keepalive_loop 가 유휴 여부를 확인하는 주기
KEEPALIVE_POLL_SEC = 1.0
CONNECT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class SttConfig:
    """연결마다 달라질 수 있는 값만. 모델·언어·인코딩은 위 상수다."""

    keyterms: tuple[str, ...] = ()
    endpointing_ms: int = 300
    utterance_end_ms: int = 1000
    # Deepgram 콘솔에서 사용량을 Take 별로 볼 수 있게 붙이는 표식
    tag: str | None = None


def build_listen_url(base_url: str, config: SttConfig) -> str:
    params: list[tuple[str, str]] = [
        ("model", MODEL),
        ("language", LANGUAGE),
        # raw PCM 이라 세 값이 전부 필수다. 틀리면 에러 없이 쓰레기 전사가 나온다
        ("encoding", "linear16"),
        ("sample_rate", str(SAMPLE_RATE)),
        ("channels", str(CHANNELS)),
        ("interim_results", "true"),
        ("endpointing", str(config.endpointing_ms)),
        ("utterance_end_ms", str(config.utterance_end_ms)),
        ("vad_events", "true"),
        # word(raw) 와 punctuated_word 를 둘 다 받기 위해. smart_format 은 한국어에서
        # 뭘 바꾸는지 몰라 끈다 — raw 가 필요한 쪽은 우리다
        ("punctuate", "true"),
        ("smart_format", "false"),
    ]
    params.extend(("keyterm", term) for term in config.keyterms)
    if config.tag:
        params.append(("tag", config.tag))
    return f"{base_url}?{urlencode(params)}"


# ── Deepgram → 우리 이벤트 ────────────────────────────────────────────


@dataclass(frozen=True)
class Word:
    word: str
    punctuated_word: str
    start_ms: int
    end_ms: int
    confidence: float


@dataclass(frozen=True)
class Transcript:
    """Results 메시지. interim(is_final=False) 과 final 이 같은 형태로 온다.

    interim 은 같은 구간을 여러 번 보내며 단어·타임스탬프가 매번 바뀔 수 있다.
    저장·분석은 is_final 만 쓴다. speech_final 은 endpointing 이 침묵을 감지한 문장 경계다.
    """

    start_ms: int
    end_ms: int
    is_final: bool
    speech_final: bool
    from_finalize: bool
    transcript: str
    confidence: float
    words: tuple[Word, ...]


@dataclass(frozen=True)
class UtteranceEnd:
    last_word_end_ms: int


@dataclass(frozen=True)
class SpeechStarted:
    timestamp_ms: int


@dataclass(frozen=True)
class Metadata:
    """CloseStream 뒤 마지막으로 온다. duration 은 Deepgram 이 받은 총 오디오 길이."""

    request_id: str
    duration_ms: int


@dataclass(frozen=True)
class SttError:
    code: str
    message: str


SttEvent = Transcript | UtteranceEnd | SpeechStarted | Metadata | SttError


def _ms(seconds: Any) -> int:
    return round(float(seconds or 0) * 1000)


def _parse_results(data: dict[str, Any]) -> Transcript:
    alternatives = data.get("channel", {}).get("alternatives") or [{}]
    best = alternatives[0]
    words = tuple(
        Word(
            word=str(w.get("word", "")),
            punctuated_word=str(w.get("punctuated_word") or w.get("word", "")),
            start_ms=_ms(w.get("start")),
            end_ms=_ms(w.get("end")),
            confidence=float(w.get("confidence") or 0.0),
        )
        for w in best.get("words") or []
    )
    start_ms = _ms(data.get("start"))
    return Transcript(
        start_ms=start_ms,
        end_ms=start_ms + _ms(data.get("duration")),
        is_final=bool(data.get("is_final")),
        speech_final=bool(data.get("speech_final")),
        from_finalize=bool(data.get("from_finalize")),
        transcript=str(best.get("transcript") or ""),
        confidence=float(best.get("confidence") or 0.0),
        words=words,
    )


def parse_message(raw: str) -> SttEvent | None:
    """모르는 타입은 None. Deepgram 이 메시지 종류를 늘려도 여기서 깨지지 않게."""
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Deepgram 메시지가 JSON 이 아닙니다 (%d bytes)", len(raw))
        return None
    if not isinstance(data, dict):
        return None

    match data.get("type"):
        case "Results":
            return _parse_results(data)
        case "UtteranceEnd":
            return UtteranceEnd(last_word_end_ms=_ms(data.get("last_word_end")))
        case "SpeechStarted":
            return SpeechStarted(timestamp_ms=_ms(data.get("timestamp")))
        case "Metadata":
            return Metadata(
                request_id=str(data.get("request_id") or ""),
                duration_ms=_ms(data.get("duration")),
            )
        case "Error":
            return SttError(
                code=str(data.get("code") or data.get("variant") or "DEEPGRAM_ERROR"),
                message=str(data.get("description") or data.get("message") or ""),
            )
        case _:
            return None


# ── 세션 ─────────────────────────────────────────────────────────────


class SttConnectError(Exception):
    """연결 자체가 안 된 경우. 키 오류·네트워크·타임아웃을 구분하지 않는다."""


class SttSession(Protocol):
    """service 가 의존하는 최소 인터페이스. 테스트는 이걸 가짜로 바꿔 끼운다."""

    async def send_audio(self, pcm: bytes) -> None: ...
    async def finalize(self) -> None: ...
    async def close_stream(self) -> None: ...
    async def abort(self) -> None: ...
    async def keepalive_loop(self) -> None: ...
    def __aiter__(self) -> AsyncIterator[SttEvent]: ...


class SttAdapter(Protocol):
    async def connect(self, config: SttConfig) -> SttSession: ...


class DeepgramSession:
    def __init__(self, conn: ClientConnection) -> None:
        self._conn = conn
        self._last_send = time.monotonic()
        # CloseStream 을 보낸 뒤에는 KeepAlive 를 보내지 않는다
        self._closing = False

    async def send_audio(self, pcm: bytes) -> None:
        await self._conn.send(pcm)
        self._last_send = time.monotonic()

    async def _send_control(self, message_type: str) -> None:
        await self._conn.send(json.dumps({"type": message_type}))
        self._last_send = time.monotonic()

    async def finalize(self) -> None:
        """버퍼된 오디오를 지금 final 로 뱉게 한다. 연결은 유지된다."""
        await self._send_control("Finalize")

    async def close_stream(self) -> None:
        """남은 오디오 처리 -> 마지막 Results -> Metadata 순으로 받고 서버가 닫는다.

        그냥 연결을 끊으면 마지막 1~2초 전사가 사라진다.
        """
        self._closing = True
        await self._send_control("CloseStream")

    async def abort(self) -> None:
        self._closing = True
        await self._conn.close()

    async def keepalive_loop(self) -> None:
        """오디오가 멈춘 동안만 KeepAlive 를 보낸다. 취소될 때까지 돈다."""
        while not self._closing:
            await asyncio.sleep(KEEPALIVE_POLL_SEC)
            if self._closing:
                return
            if time.monotonic() - self._last_send < KEEPALIVE_INTERVAL_SEC:
                continue
            try:
                await self._send_control("KeepAlive")
            except ConnectionClosedOK, ConnectionClosedError:
                return

    async def __aiter__(self) -> AsyncIterator[SttEvent]:
        """서버가 닫을 때까지 이벤트를 흘린다. 에러 종료는 SttError 하나로 끝난다."""
        try:
            async for raw in self._conn:
                if isinstance(raw, bytes):
                    continue
                event = parse_message(raw)
                if event is not None:
                    yield event
        except ConnectionClosedOK:
            return
        except ConnectionClosedError as e:
            close = e.rcvd
            yield SttError(
                code="CONNECTION_CLOSED",
                message=f"{close.code} {close.reason}" if close else "connection lost",
            )


class DeepgramSttAdapter:
    def __init__(self, api_key: str, *, base_url: str = DEEPGRAM_LISTEN_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def connect(self, config: SttConfig) -> DeepgramSession:
        url = build_listen_url(self._base_url, config)
        try:
            conn = await connect(
                url,
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=CONNECT_TIMEOUT_SEC,
                # 우리가 보내는 건 100ms 프레임, 받는 건 JSON 이라 기본 1MiB 면 충분하다
            )
        except (OSError, TimeoutError, WebSocketException) as e:
            raise SttConnectError(str(e)) from e
        return DeepgramSession(conn)
