"""대화 에이전트 엔드포인트 `POST /agent/turn`.

기준: `docs/ai/features/chat-agent.md` 5-4 · 6-11 · 11절.

- 부르는 곳: BE 턴 실행기(`backend/.../module/chat/ai_client.py`). 응답은 `application/x-ndjson`
  스트림이다(`plan` · `tool_done` · `claim` 줄, 끝 줄 `final` 또는 `error` 하나). 스트림을 열기 전
  오류는 공통 오류 본문 `{"error": {"code", "message"}}` 와 상태 코드다
  (503 · 429 · 413 · 422 · 409).
- 하는 일 (서비스 계층 몫, 코어는 `features/chat_agent/turn.py` 의 `handle_turn`)
  1. 꺼져 있거나(`AGENT_ENABLED=false`) 켜졌는데 서명 키(`AGENT_STATE_SECRET`)가 없거나 32바이트보다
     짧으면 503 `AGENT_DISABLED`(11-1). 이때는 모델 · 자료 묶음을 만들지 않고 본문도 읽지 않는다.
     코어도 같은 검사를 한 번 더 한다.
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
     보내기 막힘은 계약이 예상한 상황(느린 소비자, L1-09)이라 예외로 올리지 않고 응답을 끝낸다
     (`agent_stream_write_timeout` 한 줄). 받는 쪽은 끝 줄 없는 EOF 로 본다(5-4).
     2초는 서버로 실제로 보내는 곳에서 잰다: 요청 로그 미들웨어(`BaseHTTPMiddleware`)는 응답을
     내부 큐로 한 번 더 넘기므로, 가장 바깥의 순수 ASGI 미들웨어 `StreamSendGuard`(main.py 에
     등록)가 이 경로의 보내기에 상한을 걸고, 막히면 남은 메시지를 버린다. 그러면 앱이 돌아오고
     uvicorn 이 'ASGI callable returned without completing response.' 한 줄을 ERROR 수준으로
     남기며 연결을 닫는다(의도한 동작, 바로 앞에 같은 요청 ID 의 `agent_stream_write_timeout`
     WARNING 이 있다. `ai-service.md` 11-6). 미들웨어 없이 이 응답을 부르면 응답이 직접 잰다.
  6. 끝 줄 방어: 코어가 끝 줄 없이 예외로 멈추면(코어가 잡지 못한 곳) 읽기 스레드가 `error`
     `INTERNAL_ERROR` 끝 줄 하나를 대신 낸다(연결이 살아 있을 때, 5-4 '끝 줄은 정확히 하나').
- 자료 묶음: 서비스는 검수를 마친 카드만 읽는다(`allow_unreviewed=False`). 검수 전 카드 ·
  판 미등록 · 파일 오류면 자료 묶음 없이 시작하고 이유 코드만 로그에 남긴다. 그때 발표법 질문은
  일반 조언으로 답한다(기능 문서 6-10 · L1-21).
- 로그: 코어의 진단 기록 · 실행 끝(`agent_step` · `agent_end` 등)은 JSON 한 줄로 남긴다(11-5,
  본문 없음). 응답이 끝나면 `agent_stream` 한 줄(요청 ID · 끝난 까닭 · 서버로 보낸 줄 수 · 걸린
  시간)을 남긴다. 끝 줄을 보내는 중에 끊기거나 막혀도 `end=` 에 드러나게 보내는 쪽에서 남긴다. 요청
  로그(`common/request_log.py`)는 스트림을 열 때 200 을 남기므로 스트림 결과는 이 줄로 본다.
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

from pitch_coach_ai.common.errors import ERROR_RESPONSES, ErrorResponse, error_response, log_safe
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
    def from_settings(cls, s: Settings, cfg: ChatAgentConfig = DEFAULT_CONFIG) -> AgentRuntime:
        pool = s.agent_max_concurrency * 2
        # 모델 호출 한 번은 코어가 답 쓰기 · 고치기 몫으로 잡은 호출 한 번(24초 / 2 = 12초)을 넘지
        # 않는다(8-3). `LLM_TIMEOUT_SECONDS` 가 더 작으면 그것을 따른다
        per_call = cfg.reserved_answer_seconds / cfg.reserved_answer_calls
        return cls(
            llm=tool_llm("chat_agent", pool_size=pool, app_settings=s, max_call_seconds=per_call),
            corpus=load_corpus(),
            secret=_secret(s.agent_state_secret),
            previous_secret=_secret(s.agent_state_secret_previous),
            max_concurrency=s.agent_max_concurrency,
            cfg=cfg,
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
    """앱의 대화 자원. 프로세스(앱)에서 처음 부를 때 한 번만 만들고, 그 뒤에는 같은 것을 돌려준다.

    `tool_llm` 은 부를 때마다 새 실행 풀을 만들므로 이 함수 밖에서 부르지 않는다.
    시험은 `app.state.chat_agent_runtime` 에 미리 넣을 수 있다.
    """
    with _runtime_lock:
        rt = getattr(app.state, "chat_agent_runtime", None)
        if rt is None:
            rt = AgentRuntime.from_settings(app.state.settings)
            app.state.chat_agent_runtime = rt
        return rt


# ── 스트림 ────────────────────────────────────────────────────────────────────


#: 끝 줄(`final` · `error`)의 앞부분(코어 줄 쓰기의 키 순서)
_TERMINAL_PREFIXES = ('{"event":"final"', '{"event":"error"')


def _fallback_error_line(run_id: str, seq: int) -> str:
    """코어가 끝 줄 없이 멈췄을 때 읽기 스레드가 대신 내는 `error` 끝 줄.

    5-4 모양이고 진단 기록은 싣지 않는다.
    """
    body = {
        "event": "error",
        "run_id": run_id,
        "seq": seq,
        "data": {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": templates.ERROR_MESSAGES.get("INTERNAL_ERROR", "INTERNAL_ERROR"),
            }
        },
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n"


class TurnStream:
    """턴 하나의 스트림.

    읽기 스레드가 코어 줄을 이벤트 루프 큐로 넘기고, 끝나면 자리를 한 번 돌려준다.
    응답이 끝나면 `finish_response` 가 요약 줄(`agent_stream`)을 한 번 남긴다.
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
        self._run_id = resp.run_id or "-"
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stopped = threading.Event()
        self._started = time.monotonic()
        self._logged = False
        #: 큐에서 끝 표시(_END)를 받았다(코어 줄을 모두 넘겼다)
        self._ended = False
        self.reason = "done"
        #: 코어가 낸 줄 수(= 마지막 줄의 seq, 코어 줄은 1부터 빈틈없이 잇는다) · 끝 줄을 냈는지
        self.count = 0
        self._terminal = False
        #: 서버로 보낸 줄 수(보내는 쪽 `_SendMeter` 가 센다)
        self.sent = 0
        self._thread = threading.Thread(target=self._pump, name="chat-agent-stream", daemon=True)
        self._thread.start()

    def _put(self, item: Any) -> None:
        with contextlib.suppress(RuntimeError):  # 이벤트 루프가 이미 닫혔다(서버 종료)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _note(self, line: str) -> None:
        """끝 줄 방어에 쓸 끝 줄 여부를 기록한다. 줄을 다시 해석하지 않고 앞부분만 본다.

        코어 줄 쓰기(`turn._Writer`)는 늘 `{"event":"<종류>",…` 로 시작하는 줄을 낸다(시험이 고정).
        seq 는 줄 수로, run_id 는 `TurnResponse.run_id` 로 안다.
        """
        if line.startswith(_TERMINAL_PREFIXES):
            self._terminal = True

    def _pump(self) -> None:
        try:
            for line in self._lines:
                if self._stopped.is_set():
                    break
                self.count += 1
                self._note(line)
                self._put(line)
        except Exception as exc:  # noqa: BLE001  # 코어는 예외를 error 줄로 바꾸므로 여기까지 오지 않아야 한다
            self.reason = "pump_error"
            logger.error(
                "agent_stream_error request_id=%s cause=%s",
                log_safe(self._request_id),
                type(exc).__name__,
            )
            if not self._terminal and not self._stopped.is_set():
                # 두 번째 방어: 끝 줄 없이 200 으로 닫지 않는다(5-4 · L1-08)
                self._terminal = True
                self._put(_fallback_error_line(self._run_id, self.count + 1))
        finally:
            close = getattr(self._lines, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
            self._release()
            self._put(_END)

    def finish_response(self, *, completed: bool, sent: int) -> None:
        """응답이 끝났을 때 한 번: 끝까지 보내지 못했으면 새 호출을 멈추고 요약 줄을 남긴다.

        끝 줄을 보내다 끊기거나 막힌 것도 `end=` 에 보인다(`completed` 는 서버로 마지막
        메시지까지 보냈는가).
        """
        if self._logged:
            return
        self._logged = True
        if not completed and self.reason == "done":
            self.stop("client_gone")
        self.sent = sent
        logger.info(
            "agent_stream request_id=%s end=%s lines=%d duration_ms=%.1f",
            log_safe(self._request_id),
            self.reason,
            self.sent,
            (time.monotonic() - self._started) * 1000,
        )

    def stop(self, reason: str) -> None:
        """연결이 끊기거나 보내기가 막혔다: 새 모델 · 도구 호출을 먼저 멈추고 남은 줄은 버린다.

        코어가 이미 끝났어도(끝 줄을 보내는 중) 끝난 까닭은 남긴다. 응답이 다음 줄을 기다리는
        중이면 곧바로 깨워 끝낸다: 모델 호출이 끝나기를 기다리지 않고 연결을 정리한다(자리는
        코어 반복자가 끝난 뒤 읽기 스레드가 돌려준다).
        """
        if not self._stopped.is_set():
            if self.reason == "done":
                self.reason = reason
            self._stopped.set()
            self._cancel()
            self._put(_END)

    async def body(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    self._ended = True
                    return
                yield item.encode("utf-8")
        finally:
            # 끝 표시를 받기 전에 닫혔다(끊김 · 취소). 스레드가 살아 있는지로 보지 않는다:
            # 끝 표시를 넣은 직후 스레드가 아직 돌아오지 않았을 수 있다
            if not self._ended:
                self.stop("client_gone")


class _WriteBlocked(Exception):
    """보내기가 2초 넘게 막혔다(이 모듈 안에서만 쓰고 밖으로 내보내지 않는다)."""


#: 바깥 미들웨어가 요청 scope 에 넣는 보내기 재는 것(`_SendMeter`)의 키
SEND_METER = "pitch_coach_ai.chat_agent.send_meter"


class _SendMeter:
    """보내기 하나를 감싼다: 메시지마다 2초 상한, 보낸 줄 수, 마지막 메시지까지 보냈는지.

    처음 막히면 턴을 멈추고 한 줄을 남긴 뒤 `_WriteBlocked` 를 낸다. 그 뒤의 메시지는 보내지
    않고 버린다(막힌 연결에 더 쓰지 않는다).
    """

    def __init__(self, send: Send) -> None:
        self._send = send
        self.turn: TurnStream | None = None
        self.sent = 0
        self.completed = False
        self.blocked = False

    async def __call__(self, message: Any) -> None:
        if self.blocked:
            return
        try:
            with anyio.fail_after(WRITE_TIMEOUT_SECONDS):
                await self._send(message)
        except TimeoutError:
            self.blocked = True
            if self.turn is not None:
                # 느린 소비자는 계약이 예상한 상황이라 예외로 올리지 않는다(올리면 처리되지 않은
                # 예외 · traceback 이 남는다). 끝 줄 없이 끝나므로 받는 쪽은 전송 실패로 본다(5-4)
                self.turn.stop("write_timeout")
                logger.warning(
                    "agent_stream_write_timeout request_id=%s lines=%d",
                    log_safe(self.turn._request_id),
                    self.sent,
                )
            raise _WriteBlocked from None
        if message.get("type") == "http.response.body":
            if message.get("body"):
                self.sent += 1
            if not message.get("more_body", False):
                self.completed = True


def _route_path(scope: Scope) -> str:
    """라우터가 맞추는 경로: `root_path` 를 뗀 `path`.

    Starlette 라우터의 `get_route_path` 와 같은 규칙이다.
    """
    path: str = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root) and (path == root or path[len(root)] == "/"):
        return path[len(root) :]
    return path


class StreamSendGuard:
    """`/agent/turn` 의 실제 보내기(서버 쪽)에 줄마다 2초 상한을 건다(L1-08 · L1-09). 순수 ASGI.

    main.py 에서 가장 바깥에 둔다(시험 `test_send_guard_is_the_outermost_user_middleware` 가 고정).
    요청 로그 미들웨어(`BaseHTTPMiddleware`)보다 안쪽이면 응답을 내부 큐로 넘긴 뒤를 재므로
    조용히 큐까지만 재게 된다. 막히면 턴을 멈추고 남은 메시지는 버린다. 그러면 앱이 돌아오고
    서버(uvicorn)가 연결을 닫는다. 다른 경로는 그대로 지나간다.

    경로는 라우터와 같은 기준으로 맞춘다: `root_path`(앞에 붙은 경로 접두어)를 뗀 경로가
    `/agent/turn` 이면 건다. 그래서 접두어를 붙여 배포해도 보호가 빠지지 않는다.
    """

    def __init__(self, app: Any, path: str = "/agent/turn") -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or _route_path(scope) != self.path:
            await self.app(scope, receive, send)
            return
        meter = _SendMeter(send)
        scope[SEND_METER] = meter  # 같은 scope 가 미들웨어 · 라우터 · 응답까지 이어진다

        async def guarded(message: Any) -> None:
            with contextlib.suppress(_WriteBlocked):
                await meter(message)

        try:
            await self.app(scope, receive, guarded)
        finally:
            if meter.turn is not None:
                meter.turn.finish_response(completed=meter.completed, sent=meter.sent)


class TurnStreamResponse(StreamingResponse):
    """NDJSON 스트림 응답. 줄마다 보내기 대기를 2초로 끊고, 응답이 어떻게 끝나든 턴을 정리한다.

    바깥 `StreamSendGuard` 가 있으면 보내기는 그쪽이 재고, 없으면(미들웨어 없이 부름) 직접 잰다.
    """

    def __init__(self, stream: TurnStream) -> None:
        super().__init__(stream.body(), media_type=NDJSON, headers=STREAM_HEADERS)
        self._turn = stream
        self._outer: _SendMeter | None = None
        self._own: _SendMeter | None = None
        #: 이 응답이 마지막 메시지까지 넘겼다(바깥이 서버로 보냈는지는 바깥이 안다)
        self._handed_all = False

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        except _WriteBlocked:
            with contextlib.suppress(Exception):
                await self.body_iterator.aclose()  # type: ignore[attr-defined]
            return
        self._handed_all = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        outer = scope.get(SEND_METER)
        if isinstance(outer, _SendMeter):
            outer.turn = self._turn
            self._outer = outer
        else:
            self._own = _SendMeter(send)
            self._own.turn = self._turn
            send = self._own
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
        # 헤더를 보내기 전 끊김 · 보내기 실패 · 취소 등 어떤 끝이든 새 호출을 곧바로 멈춘다.
        # 요약 줄은 서버로 보낸 결과를 아는 쪽이 남긴다(바깥이 있으면 바깥이 앱이 돌아온 뒤에)
        if self._own is not None:
            self._turn.finish_response(completed=self._own.completed, sent=self._own.sent)
        elif not self._handed_all and self._turn.reason == "done":
            self._turn.stop("client_gone")


# ── 라우터 ────────────────────────────────────────────────────────────────────


def _error(request: Request, status: int, code: str, message: str | None = None) -> JSONResponse:
    request.state.error_code = code  # 요청 로그의 code=
    return error_response(status, code, message or templates.ERROR_MESSAGES.get(code, code))


def _secret_ready(s: Settings, cfg: ChatAgentConfig = DEFAULT_CONFIG) -> bool:
    """서명 키가 있고 코어 최소 길이(32바이트) 이상인가(11-1). 코어 `handle_turn` 과 같은 기준."""
    secret = _secret(s.agent_state_secret)
    return secret is not None and len(secret) >= cfg.min_secret_bytes


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
    s = request.app.state.settings
    # 꺼짐 · 서명 키 없음은 자원을 만들거나 본문을 읽기 전에 거른다(11-1). 코어 검사는 두 번째 방어
    if not s.agent_enabled or not _secret_ready(s):
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


__all__ = ["AgentRuntime", "StreamSendGuard", "load_corpus", "router", "runtime_for"]
