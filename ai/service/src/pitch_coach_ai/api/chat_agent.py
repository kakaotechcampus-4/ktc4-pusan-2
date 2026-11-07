"""대화 에이전트 엔드포인트 `POST /agent/turn`.

기준: `docs/ai/features/chat-agent.md` 5-4 · 6-11 · 11절.

- 부르는 곳: BE 턴 실행기(`backend/.../module/chat/ai_client.py`). 응답은 `application/x-ndjson`
  스트림이다(`plan` · `tool_done` · `claim` 줄, 끝 줄 `final` 또는 `error` 하나). 스트림을 열기 전
  오류는 공통 오류 본문 `{"error": {"code", "message"}}` 와 상태 코드다
  (503 · 429 · 413 · 422 · 409).
- 하는 일 (서비스 계층 몫, 코어는 `features/chat_agent/turn.py` 의 `handle_turn`)
  1. 꺼져 있으면(`AGENT_ENABLED=false`) 503 `AGENT_DISABLED`. 이때는 모델 · 자료 묶음을 만들지
     않는다.
  2. 본문은 2 MiB 까지만 읽는다(Content-Length 를 믿지 않고 읽으며 센다, 넘으면 413, L1-02).
  3. 실행 자리: 본문을 다 읽은 뒤 대화 동시 상한(`AGENT_MAX_CONCURRENCY`)과 대화 전용 실행 풀
     (그 × 2)을 한 잠금 안에서 함께 보고, 대기열 없이 자리를 얻는다. 없으면 429 `AGENT_BUSY`
     (모델 호출 0회, L1-06 · L1-14). 자리는 그 턴의 코어 반복자가 끝나 닫힌 뒤 한 번 돌려준다
     (스트림 끝 · 예외 · 연결 끊김 모두).
  4. `handle_turn` 을 대화 전용 스레드 자리에서 부른다(공용 스레드 풀 40 을 쓰지 않는다,
     6-11 · 8-4).
  5. 스트림: 턴마다 읽기 스레드 하나가 코어 줄을 받아 이벤트 루프의 큐로 넘긴다. 연결이 끊기거나
     보내기가 2초 넘게 막히면(L1-08) 코어에 취소 신호(`TurnResponse.cancel`)를 먼저 보내 새
     모델 · 도구 호출을 멈추고, 반복자는 읽기 스레드가 닫는다(읽는 중인 생성기를 다른 스레드에서
     닫지 않는다). 끊김은 ASGI 2.3(uvicorn)이면 Starlette 가, 2.4 면 이 응답이 직접 듣는다.
- 자료 묶음: 서비스는 검수를 마친 카드만 읽는다(`allow_unreviewed=False`). 검수 전 카드 ·
  판 미등록 · 파일 오류면 자료 묶음 없이 시작하고 이유 코드만 로그에 남긴다. 그때 발표법 질문은
  일반 조언으로 답한다(기능 문서 6-10 · L1-21).
- 로그: 코어의 진단 기록 · 실행 끝(`agent_step` · `agent_end` 등)은 JSON 한 줄로 남긴다(11-5,
  본문 없음). 스트림이 끝나면 `agent_stream` 한 줄(요청 ID · 끝난 까닭 · 줄 수 · 걸린 시간)을
  남긴다. 요청 로그(`common/request_log.py`)는 스트림을 열 때 200 을 남기므로 스트림 결과는 이
  줄로 본다.
- 고칠 때: 환경변수는 `settings.py`, 모델 역할은 `LLM_ROLES["chat_agent"]`, 코어 규칙 · 한도는
  `features/chat_agent/config.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from pitch_coach_ai.common.errors import ERROR_RESPONSES, ErrorResponse, log_safe
from pitch_coach_ai.common.llm import tool_llm
from pitch_coach_ai.common.request_log import current_request_id
from pitch_coach_ai.features import chat_agent as core_pkg
from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, ChatAgentConfig
from pitch_coach_ai.features.chat_agent.corpus import Corpus, CorpusInvalid, build_corpus
from pitch_coach_ai.features.chat_agent.loop import TurnDeps
from pitch_coach_ai.features.chat_agent.turn import TurnResponse, handle_turn
from pitch_coach_ai.settings import Settings

logger = logging.getLogger("pitch_coach_ai.chat_agent")

router = APIRouter(prefix="/agent", tags=["chat_agent"])

NDJSON = "application/x-ndjson"
#: 한 줄을 보내는 데 기다리는 상한(초). 넘으면 연결을 정리한다(L1-08)
WRITE_TIMEOUT_SECONDS = 2.0
#: 스트림 응답 헤더: 프록시 버퍼링 · 캐시를 끄고 줄마다 바로 보낸다(11-4)
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
GUIDES = Path(core_pkg.__file__).parent / "guides"
_END = object()


# ── 자료 묶음 ─────────────────────────────────────────────────────────────────


def load_corpus(
    cfg: ChatAgentConfig = DEFAULT_CONFIG, *, allow_unreviewed: bool = False
) -> Corpus | None:
    """코어 패키지의 자료 묶음을 읽는다. 서비스는 검수를 마친 카드만 쓴다(`allow_unreviewed=False`).

    어긋나면(검수 전 · 판 미등록 · 파일 · 해시 오류) None 과 이유 코드 로그. 앱 시작을 막지 않는다.
    """
    try:
        manifest = json.loads((GUIDES / "manifest.json").read_text(encoding="utf-8"))
        files = {
            name: json.loads((GUIDES / name).read_text(encoding="utf-8"))
            for name in manifest.get("files", [])
        }
        return build_corpus(manifest, files, cfg=cfg, allow_unreviewed=allow_unreviewed)
    except CorpusInvalid as exc:
        reason = exc.reason_code  # 이유 코드만(카드 id · 본문 같은 상세는 남기지 않는다)
    except Exception as exc:  # noqa: BLE001  # 파일 · JSON 오류도 '자료 없음'으로 본다
        reason = type(exc).__name__
    logger.warning("chat_agent_corpus_unavailable reason=%s", log_safe(reason))
    return None


# ── 실행 자원 (프로세스마다 하나) ─────────────────────────────────────────────


def _secret(value: Any) -> bytes | None:
    return value.get_secret_value().encode("utf-8") if value is not None else None


def _log_event(event: dict[str, Any]) -> None:
    """코어의 진단 기록 한 줄(본문 없음, 11-5). JSON 으로 남긴다."""
    logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str))


class AgentRuntime:
    """대화 에이전트의 프로세스 자원: 모델 · 자료 묶음 · 서명 키 · 실행 자리.

    처음 켜진 요청이 들어올 때 한 번 만든다(`runtime_for`). 꺼져 있으면 만들지 않는다.
    """

    def __init__(
        self,
        *,
        llm: Any,
        corpus: Corpus | None,
        secret: bytes | None,
        previous_secret: bytes | None,
        max_concurrency: int,
        cfg: ChatAgentConfig = DEFAULT_CONFIG,
        log: Callable[[dict[str, Any]], None] = _log_event,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.llm = llm
        self.corpus = corpus
        self.secret = secret
        self.previous_secret = previous_secret
        self.max_concurrency = max_concurrency
        self.cfg = cfg
        self.log = log
        self.clock_ms = clock_ms
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._active = 0
        #: `handle_turn`(스트림 전 검사)을 돌리는 스레드 자리.
        #: 실행 자리를 얻은 요청만 쓰므로 기다리지 않는다
        self.limiter = anyio.CapacityLimiter(max_concurrency)

    @classmethod
    def from_settings(cls, s: Settings) -> AgentRuntime:
        pool = s.agent_max_concurrency * 2
        return cls(
            llm=tool_llm("chat_agent", pool_size=pool, app_settings=s),
            corpus=load_corpus(),
            secret=_secret(s.agent_state_secret),
            previous_secret=_secret(s.agent_state_secret_previous),
            max_concurrency=s.agent_max_concurrency,
        )

    @property
    def corpus_version(self) -> str:
        return self.corpus.version if self.corpus is not None else "none"

    def deps(self) -> TurnDeps:
        return TurnDeps(
            llm=self.llm,
            corpus=self.corpus,
            corpus_version=self.corpus_version,
            clock_ms=self.clock_ms,
            monotonic=self.monotonic,
            cfg=self.cfg,
            log=self.log,
        )

    def try_acquire(self) -> bool:
        """실행 자리를 원자적으로 얻는다. 동시 상한이 찼거나 대화 전용 실행 풀이 찼으면 False."""
        with self._lock:
            if self._active >= self.max_concurrency:
                return False
            saturated = getattr(self.llm, "saturated", None)
            if callable(saturated) and saturated():
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        with self._lock:
            return self._active


_runtime_lock = threading.Lock()


def runtime_for(app: FastAPI) -> AgentRuntime:
    """앱의 대화 자원(처음 부를 때 만든다).

    시험은 `app.state.chat_agent_runtime` 에 미리 넣을 수 있다.
    """
    with _runtime_lock:
        rt = getattr(app.state, "chat_agent_runtime", None)
        if rt is None:
            rt = AgentRuntime.from_settings(app.state.settings)
            app.state.chat_agent_runtime = rt
        return rt


# ── 스트림 ────────────────────────────────────────────────────────────────────


class TurnStream:
    """턴 하나의 스트림.

    읽기 스레드가 코어 줄을 이벤트 루프 큐로 넘기고, 끝나면 자리를 한 번 돌려준다.
    """

    def __init__(
        self,
        resp: TurnResponse,
        release: Callable[[], None],
        *,
        request_id: str,
    ) -> None:
        assert resp.lines is not None
        self._lines: Iterator[str] = resp.lines
        self._cancel = resp.cancel or (lambda: None)
        self._release = release
        self._request_id = request_id
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stopped = threading.Event()
        self._started = time.monotonic()
        self.reason = "done"
        self.count = 0
        self._thread = threading.Thread(target=self._pump, name="chat-agent-stream", daemon=True)
        self._thread.start()

    def _put(self, item: Any) -> None:
        with contextlib.suppress(RuntimeError):  # 이벤트 루프가 이미 닫혔다(서버 종료)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _pump(self) -> None:
        try:
            for line in self._lines:
                if self._stopped.is_set():
                    break
                self.count += 1
                self._put(line)
        except Exception as exc:  # noqa: BLE001  # 코어는 예외를 error 줄로 바꾸므로 여기까지 오지 않아야 한다
            self.reason = "pump_error"
            logger.error(
                "agent_stream_error request_id=%s cause=%s",
                log_safe(self._request_id),
                type(exc).__name__,
            )
        finally:
            close = getattr(self._lines, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
            self._release()
            logger.info(
                "agent_stream request_id=%s end=%s lines=%d duration_ms=%.1f",
                log_safe(self._request_id),
                self.reason,
                self.count,
                (time.monotonic() - self._started) * 1000,
            )
            self._put(_END)

    def stop(self, reason: str) -> None:
        """연결이 끊기거나 보내기가 막혔다: 새 모델 · 도구 호출을 먼저 멈추고 남은 줄은 버린다."""
        if not self._stopped.is_set():
            if self.reason == "done":
                self.reason = reason
            self._stopped.set()
            self._cancel()

    async def body(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    return
                yield item.encode("utf-8")
        finally:
            if self._thread.is_alive():
                self.stop("client_gone")


class TurnStreamResponse(StreamingResponse):
    """NDJSON 스트림 응답. 줄마다 보내기 대기를 2초로 끊고, 응답이 어떻게 끝나든 턴을 정리한다."""

    def __init__(self, stream: TurnStream) -> None:
        super().__init__(stream.body(), media_type=NDJSON, headers=STREAM_HEADERS)
        self._turn = stream

    async def stream_response(self, send: Send) -> None:
        async def timed_send(message: Any) -> None:
            try:
                with anyio.fail_after(WRITE_TIMEOUT_SECONDS):
                    await send(message)
            except TimeoutError:
                self._turn.stop("write_timeout")
                raise

        await super().stream_response(timed_send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        spec = tuple(map(int, scope.get("asgi", {}).get("spec_version", "2.0").split(".")))
        if spec < (2, 4):
            # ASGI 2.4 미만(uvicorn 은 2.3)은 Starlette 가 끊김을 듣고 응답을 멈춘다. 그러면 아래
            # finally 가 곧바로 새 호출을 멈춘다. 같은 receive 를 두 곳에서 기다리지 않게
            # 감시자를 두지 않는다
            try:
                await super().__call__(scope, receive, send)
            finally:
                self._finish()
            return

        async def watch_disconnect() -> None:
            # 본문은 이미 다 읽었으므로 다음 메시지는 연결 끊김뿐이다. 다음 줄을 보낼 때까지
            # 기다리지 않고 끊긴 즉시 새 모델 · 도구 호출을 멈춘다(L1-08).
            # ASGI 2.4 에서는 Starlette 가 끊김을 듣지 않는다
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    self._turn.stop("client_gone")
                    return

        error: Exception | None = None

        async def respond() -> None:
            nonlocal error
            try:
                await super(TurnStreamResponse, self).__call__(scope, receive, send)
            except Exception as exc:  # noqa: BLE001  # 묶음 예외로 감싸지 않고 아래에서 그대로 다시 올린다
                error = exc
            finally:
                tg.cancel_scope.cancel()

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(watch_disconnect)
                tg.start_soon(respond)
            if error is not None:
                raise error
        finally:
            self._finish()

    def _finish(self) -> None:
        # 헤더를 보내기 전 끊김 · 보내기 실패 · 취소 등 어떤 끝이든 새 호출을 멈춘다
        # (끝난 턴이면 아무 일 없음)
        if self._turn.reason == "done" and self._turn._thread.is_alive():
            self._turn.stop("client_gone")


# ── 라우터 ────────────────────────────────────────────────────────────────────


def _error(request: Request, status: int, code: str, message: str | None = None) -> JSONResponse:
    request.state.error_code = code
    body = {"error": {"code": code, "message": message or templates.ERROR_MESSAGES.get(code, code)}}
    return JSONResponse(body, status_code=status)


async def _read_body(request: Request, limit: int) -> bytes | None:
    """본문을 읽으며 센다. `limit` 를 넘으면 더 읽지 않고 None.

    Content-Length 를 믿지 않는다(없음 · 거짓값 · 청크 전송, L1-02).
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/turn",
    summary="대화 에이전트 한 턴 (NDJSON 스트림)",
    description=(
        "요청 · 스트림 줄 · 끝 줄의 모양은 `docs/ai/features/chat-agent.md` 5절과 "
        "코어 예시(`features/chat_agent/examples/`)가 기준이다. 정상 응답은 `application/x-ndjson` "
        "스트림(줄마다 JSON 하나, 끝 줄 `final` 또는 `error` 하나)이고, 스트림을 열기 전 오류는 "
        '`{"error": {"code", "message"}}` 다.'
    ),
    response_class=Response,
    # 본문은 코어 `schemas.TurnRequest`(trigger 별 판별 유니온)이다. 코어가 직접 검사하므로 여기서는
    # JSON 이라는 것만 적는다(예시: `features/chat_agent/examples/request_*.json`)
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "TurnRequest — docs/ai/features/chat-agent.md 5-3, 예시 request_*.json",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
    responses={
        200: {"content": {NDJSON: {}}, "description": "진행 · 문장 줄과 끝 줄(NDJSON)"},
        409: {"model": ErrorResponse, "description": "실행 상태가 맞지 않음(AGENT_STATE_INVALID)"},
        413: {
            "model": ErrorResponse,
            "description": "본문 · 기본 자료가 큼(AGENT_CONTEXT_TOO_LARGE)",
        },
        422: ERROR_RESPONSES[422],
        429: {"model": ErrorResponse, "description": "대화 실행 자리 없음(AGENT_BUSY)"},
        500: ERROR_RESPONSES[500],
        503: {
            "model": ErrorResponse,
            "description": "꺼짐 · 서명 키 없음(AGENT_DISABLED) · 모델 없음(LLM_UNAVAILABLE)",
        },
    },
)
async def turn(request: Request) -> Response:
    if not request.app.state.settings.agent_enabled:
        return _error(request, 503, "AGENT_DISABLED")
    # 첫 요청에 자원(자료 파일 · 모델 클라이언트)을 만든다. 이벤트 루프를 막지 않게 스레드에서.
    # 이미 만들었으면 스레드를 쓰지 않는다
    rt = getattr(request.app.state, "chat_agent_runtime", None)
    if rt is None:
        rt = await anyio.to_thread.run_sync(runtime_for, request.app)
    # 본문을 다 읽은 뒤에 자리를 얻는다(본문을 천천히 보내는 연결이 자리를 잡고 있지 않게)
    body = await _read_body(request, rt.cfg.max_request_bytes)
    if body is None:
        return _error(request, 413, "AGENT_CONTEXT_TOO_LARGE")
    if not rt.try_acquire():
        return _error(request, 429, "AGENT_BUSY")
    handed_over = False
    try:
        request_id = current_request_id() or "-"
        resp = await anyio.to_thread.run_sync(
            partial(
                handle_turn,
                body,
                rt.deps(),
                secret=rt.secret,
                previous_secret=rt.previous_secret,
                request_id=request_id,
            ),
            limiter=rt.limiter,
        )
        if resp.error is not None:
            return _error(request, resp.error.status, resp.error.code, resp.error.message)
        stream = TurnStream(resp, rt.release, request_id=request_id)
        handed_over = True  # 이제 자리는 스트림이 돌려준다
        return TurnStreamResponse(stream)
    finally:
        if not handed_over:
            rt.release()


__all__ = ["AgentRuntime", "load_corpus", "router", "runtime_for"]
