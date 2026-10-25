"""Deepgram 스트리밍 STT 어댑터. DB·Redis·Take 를 모른다 (auth/google.py 와 같은 위치).

하는 일은 셋뿐이다.

1. 연결 URL 을 만들고 API 키를 헤더로 붙인다. 브라우저 WebSocket 은 헤더를 못 붙이므로
   FE → BE → Deepgram 구조가 필요한 이유가 이것이다. 키가 여럿이면 거절된 키를 건너뛴다.
2. PCM 을 흘리고 KeepAlive / Finalize / CloseStream 을 보낸다.
3. Deepgram JSON 을 우리 이벤트로 바꾼다. 초(float) 는 ms 정수로. 단 기준은 여전히
   "이 Deepgram 연결에 보낸 첫 바이트 = 0" 이다. Take 기준으로 옮기는 건 service 의 몫.

전사 내용은 사용자 발화 원문이라 로그에 남기지 않는다. request_id 와 길이만 남긴다.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import (
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidStatus,
    WebSocketException,
)

from pitch_coach_backend.realtime.audio import CHANNELS, ENCODING, SAMPLE_RATE

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"

# 환경마다 바뀌지 않는 값은 설정이 아니라 상수 (README 환경변수 절)
MODEL = "nova-3"  # 한국어는 Nova-3 에만 있다. Nova-2 fallback 은 없다
LANGUAGE = "ko"

# Deepgram 은 오디오도 KeepAlive 도 없이 10초가 지나면 NET-0001 로 끊는다. 문서 권장 3~5초
KEEPALIVE_INTERVAL_SEC = 3.0
# keepalive_loop 가 유휴 여부를 확인하는 주기
KEEPALIVE_POLL_SEC = 1.0
CONNECT_TIMEOUT_SEC = 10.0
# 연결 요청에 이 코드로 답하면 그 키는 다시 붙어도 안 된다. 401 은 틀린 키, 402 는 크레딧 부족
# (ASR_PAYMENT_REQUIRED). 발표 도중 크레딧이 떨어져 끊기는 경우는 닫힘 코드가 문서에 없다.
# 끊기면 어차피 다시 붙으므로 그 재접속이 402 로 거절될 때 다음 키로 넘어간다
KEY_REJECTED_STATUSES = frozenset({401, 402})


@dataclass(frozen=True)
class SttConfig:
    """연결마다 달라질 수 있는 값만. 모델·언어·인코딩은 위 상수다."""

    keyterms: tuple[str, ...] = ()
    # Deepgram 이 이 설정을 거절(SttConfigRejected)하면 keyterms 를 뒤에서부터 줄여 다시 붙는다.
    # 앞에서 이만큼(filler)은 끝까지 남긴다. keyterms 개수와 같으면 줄일 것이 없다
    min_keyterms: int = 0
    endpointing_ms: int = 300
    utterance_end_ms: int = 1000
    # Deepgram 콘솔에서 사용량을 Take 별로 볼 수 있게 붙이는 표식
    tag: str | None = None


def build_listen_url(base_url: str, config: SttConfig) -> str:
    params: list[tuple[str, str]] = [
        ("model", MODEL),
        ("language", LANGUAGE),
        # raw PCM 이라 세 값이 전부 필수다. 틀리면 에러 없이 쓰레기 전사가 나온다
        ("encoding", ENCODING),
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


class SttConfigRejected(SttConnectError):
    """Deepgram 이 요청 값을 HTTP 400 으로 거절했다 (예: keyterm 토큰 한도 초과).

    같은 설정으로는 몇 번을 다시 붙어도 또 거절된다.
    """


class _SttKeyRejected(SttConnectError):
    """이 키를 Deepgram 이 받아 주지 않는다 (KEY_REJECTED_STATUSES). 다른 키로는 붙을 수 있다."""


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
    """키를 앞에서부터 쓰고, 거절된 키는 소진으로 표시해 다음 연결부터 건너뛴다.

    소진 표시는 프로세스 메모리에만 둔다. 재시작하면 첫 키부터 다시 확인한다.
    로그에는 키 값 대신 몇 번째 키인지만 남긴다.
    """

    def __init__(self, api_keys: Sequence[str], *, base_url: str = DEEPGRAM_LISTEN_URL) -> None:
        # 문자열도 Sequence 라 그대로 받으면 글자 하나하나가 키가 된다
        if isinstance(api_keys, str) or not api_keys:
            raise ValueError("Deepgram 키 목록이 필요합니다")
        self._api_keys = tuple(api_keys)
        self._base_url = base_url
        self._exhausted: set[int] = set()

    async def connect(self, config: SttConfig) -> DeepgramSession:
        url = build_listen_url(self._base_url, config)
        if len(self._exhausted) == len(self._api_keys):
            # 전부 소진이면 첫 키부터 다시 확인한다. 그사이 누가 크레딧을 채웠을 수 있다.
            # 그대로 두면 재시작할 때까지 재시도가 Deepgram 에 닿지도 않는다
            self._exhausted.clear()
        for index, api_key in enumerate(self._api_keys):
            if index in self._exhausted:
                continue
            try:
                conn = await self._open(url, api_key)
            except _SttKeyRejected as e:
                # 기다리지 않고 바로 다음 키로 붙는다. 다른 Take 도 이 키를 건너뛴다
                self._exhausted.add(index)
                logger.warning("Deepgram %d번째 키가 거절됐다 %s", index + 1, e)
                continue
            return DeepgramSession(conn)
        logger.error("Deepgram 키 %d개가 모두 거절됐다", len(self._api_keys))
        raise SttConnectError("모든 Deepgram 키가 거절됐다")

    async def _open(self, url: str, api_key: str) -> ClientConnection:
        try:
            return await connect(
                url,
                additional_headers={"Authorization": f"Token {api_key}"},
                open_timeout=CONNECT_TIMEOUT_SEC,
                # 우리가 보내는 건 100ms 프레임, 받는 건 JSON 이라 기본 1MiB 면 충분하다
            )
        except InvalidStatus as e:
            status = e.response.status_code
            if status == 400:
                # 거절 이유는 본문에 있다. 사용자 발화가 아니라 우리가 보낸 설정에 대한 설명이다
                body = bytes(e.response.body[:200]).decode(errors="replace")
                raise SttConfigRejected(f"{e} {body}") from e
            if status in KEY_REJECTED_STATUSES:
                raise _SttKeyRejected(str(e)) from e
            raise SttConnectError(str(e)) from e
        except (OSError, TimeoutError, WebSocketException) as e:
            raise SttConnectError(str(e)) from e
