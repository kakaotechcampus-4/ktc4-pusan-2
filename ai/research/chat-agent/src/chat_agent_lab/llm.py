"""도구 호출 LLM — OpenAI 호환 스트리밍 호출 (기능 문서 6-11, 연구 전용. 서비스 탑재 때 `common/llm.py`의 `tool_llm`).

- 누가 쓰나: 서비스 계층 대역(app.py)이 만들어 코어 루프(`chat_agent.loop`)에 넘긴다. 코어는 `invoke()`만 쓴다.
- 결과 모양: 도구 호출 · 구조화(`response_schema`) 모드 모두 `chat_agent.llm_types.LlmResult`
  `{message, tool_calls, finish_reason, model, usage, attempts, parsed}`.
- 처리 (시도마다)
  1. 코어의 `before_attempt(n)`이 False 면 보내지 않는다(실행 한도 원자 예약, 8-3).
  2. 대화 전용 실행 풀의 스레드에서 스트리밍으로 부르고 시간 제한 전체만 기다린다(게이트웨이는 비스트리밍 출력 상한이
     2,000토큰이라 서비스 `structured_llm`과 같이 스트리밍으로 받는다). 시간 초과면 스트림을 닫고 그 자리는 끝날 때까지 남는다.
  3. 실패를 공통 예외로 바꾼다. 시간 초과 · 연결 실패 · 429 · 5xx 만 남은 시간 안에서 1번 다시 부른다. 429 · 503 은
     `Retry-After`가 유효하면 따르고(대기 + 12초가 남지 않으면 다시 부르지 않음), 헤더가 없으면 0.5초 기다린다.
     400 · 401 · 403 · 404 · 잘린 출력 · 형식 오류 · 빈 답 · 모르는 끝난 이유는 다시 부르지 않는다(6-11 · L1-11 · L1-12).
  4. 끝난 이유를 단계별 허용 목록으로 본다: 도구 모드 stop · tool_calls, 구조화 모드 stop. length 는 잘림(무효 출력),
     content_filter · refusal 은 거절(`LlmRefused`), 그 밖은 무효 출력.
- 로그 · 예외에는 프롬프트 · 답 · 키 · 예외 메시지를 넣지 않는다(예외 종류와 상태 코드만, P8).
- 고칠 때: 역할 설정(모델 묶음 · 출력 상한 · 추론 강도)은 `ROLE_CHAT_AGENT`. 서비스로 옮기면 `settings.LLM_ROLES["chat_agent"]`가 된다.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

import httpx
import openai

from chat_agent.llm_types import (
    LlmBudgetStop,
    LlmCallFailed,
    LlmError,
    LlmInvalidOutput,
    LlmRefused,
    LlmRequest,
    LlmResult,
    LlmTimeout,
    LlmUnavailable,
    ToolCall,
    Usage,
)


@dataclass(frozen=True)
class Role:
    """역할 → 모델 설정(서비스 settings.LLM_ROLES 와 같은 뜻)."""

    max_output_tokens: int
    reasoning_effort: str | None
    timeout_seconds: float


#: 대화 에이전트 역할. 출력 상한은 실행 출력 예약 16k 를 8번에 나눈 2,000 이하, 추론 강도는 평가에서 잰다(15-3)
#: 시간 초과 뒤 앞 시도가 끝났는지 기다려 보는 시간(초). 이 안에 끝나야 다시 보낸다
_CANCEL_GRACE_SECONDS = 0.5
ROLE_CHAT_AGENT = Role(max_output_tokens=2_000, reasoning_effort="low", timeout_seconds=12.0)

_ALLOWED_FINISH = {"tools": {"stop", "tool_calls"}, "schema": {"stop"}}


class OpenAiToolLlm:
    """OpenAI 호환 엔드포인트로 도구 호출 · 구조화 호출을 한다. `ToolLlm` 프로토콜을 따른다."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        role: Role = ROLE_CHAT_AGENT,
        executor: ThreadPoolExecutor | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[dict[str, Any]], None] = lambda _l: None,
    ) -> None:
        self._client = openai.OpenAI(
            base_url=base_url, api_key=api_key, max_retries=0, timeout=role.timeout_seconds
        )
        self._model = model
        self._role = role
        # 대화 전용 실행 풀(6-11). 서비스에서는 AGENT_MAX_CONCURRENCY × 2. 시간 초과로 버린 호출도 끝날 때까지 자리를 쓴다
        self._pool_size = 8
        self._executor = executor or ThreadPoolExecutor(
            max_workers=self._pool_size, thread_name_prefix="chat-llm"
        )
        #: 실행 중인 호출 수(시간 초과로 버렸지만 아직 안 끝난 것 포함). 풀이 차면 새 턴을 받지 않는다(L1-14)
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._monotonic = monotonic
        self._sleep = sleep
        self._log = log
        self.model_name: str | None = None

    # ── 공개 ──────────────────────────────────────────────────────────
    def saturated(self) -> bool:
        """대화 전용 실행 풀이 다 찼는가(끝나지 않은 호출 포함). 서비스 계층은 이때 AGENT_BUSY 로 거절한다."""
        with self._inflight_lock:
            return self._inflight >= self._pool_size

    def _done(self, _f: Any) -> None:
        with self._inflight_lock:
            self._inflight -= 1

    def invoke(self, req: LlmRequest, before_attempt: Callable[[int], bool]) -> LlmResult:
        mode = "schema" if req.response_schema is not None else "tools"
        usages: list[Usage] = []
        errors: list[str] = []
        last: LlmError | None = None
        sink = req.text_sink
        for attempt in range(1, req.max_attempts + 1):
            if not before_attempt(attempt):
                raise LlmBudgetStop(attempts=attempt - 1, usage=usages, cause="budget")
            left = req.deadline - self._monotonic()
            timeout = min(self._role.timeout_seconds, left)
            if timeout <= 0:
                raise last or LlmTimeout(attempts=attempt - 1, usage=usages, cause="deadline")
            started = self._monotonic()
            if sink is not None:
                sink.start(attempt)
            try:
                result = self._once(req, mode, timeout, attempt)
            except _Retryable as r:
                usages.append(Usage())
                errors.append(r.error.code)
                last = r.error
                last.attempts, last.usage = attempt, list(usages)
                self._emit(req, attempt, r.error.code, None, started, r.cause)
                wait = r.retry_after if r.retry_after is not None else 0.5
                # 다시 부를 시간: 대기 + 호출 한 번(12초)이 마감 안에 들어와야 한다(8-3).
                # 앞 시도의 문장을 이미 화면에 냈으면 다시 보내지 않는다(낸 문장이 다른 초안과 섞이지 않게, 5-4 · 6-11)
                shown = sink is not None and sink.committed()
                if (
                    attempt < req.max_attempts
                    and not shown
                    and req.deadline - self._monotonic() >= wait + self._role.timeout_seconds
                ):
                    self._sleep(wait)
                    continue
                raise last from None
            except LlmError as e:
                usages.append(getattr(e, "_usage_one", Usage()))
                e.attempts, e.usage = attempt, list(usages)
                self._emit(req, attempt, e.code, None, started, e.cause)
                raise
            usages.append(result.usage)
            self.model_name = result.model or self.model_name
            self._emit(req, attempt, "ok", result, started, "-")
            return LlmResult(
                message=result.message,
                tool_calls=result.tool_calls,
                finish_reason=result.finish_reason,
                model=result.model,
                usage=result.usage,
                attempts=attempt,
                parsed=result.parsed,
                attempt_usages=list(usages),
                attempt_errors=list(errors),
            )
        raise last or LlmCallFailed(attempts=req.max_attempts, usage=usages)

    # ── 한 번 보내기 ──────────────────────────────────────────────────
    def _once(self, req: LlmRequest, mode: str, timeout: float, attempt: int = 1) -> LlmResult:
        holder: dict[str, Any] = {"attempt": attempt}
        with self._inflight_lock:
            self._inflight += 1
        future = self._executor.submit(self._stream, req, holder)
        future.add_done_callback(self._done)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            stream = holder.get("stream")
            if stream is not None:
                # 전송 취소 · 응답 close 시도(6-11). close 가 멈출 수 있어 따로 돌리고 기다리지 않는다
                threading.Thread(target=_quiet_close, args=(stream,), daemon=True).start()
            try:
                late = future.result(timeout=_CANCEL_GRACE_SECONDS)
            except FutureTimeout:
                # 앞 시도가 끝났는지 확인하지 못하면 같은 요청을 다시 보내지 않는다(6-11 · L1-14: 중복 호출 · 과금)
                raise LlmTimeout(cause="timeout_unconfirmed") from None
            except LlmError:
                raise  # 끝났고 무효 출력 · 거절이면 다시 보내지 않는다
            except Exception:  # noqa: BLE001  # 닫혀서 끊긴 것 등: 끝났으니 다시 보낼 수 있다
                raise _Retryable(LlmTimeout(cause="timeout"), "timeout", None) from None
            return late  # 마감 직후 끝난 결과는 그대로 쓴다

    def _stream(self, req: LlmRequest, holder: dict[str, Any]) -> LlmResult:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": req.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": min(req.max_output_tokens, self._role.max_output_tokens),
        }
        if req.tools:
            # 게이트웨이의 gpt-6-luna 는 chat completions 에서 도구와 추론을 함께 받지 않는다(400 "Function tools with
            # reasoning_effort are not supported ... set reasoning_effort to 'none'", 2026-11-04 확인). 도구 모드는 none
            kwargs["reasoning_effort"] = "none"
        elif self._role.reasoning_effort:
            kwargs["reasoning_effort"] = self._role.reasoning_effort
        if req.tools:
            kwargs["tools"] = req.tools
            kwargs["tool_choice"] = "auto"
            # 한 응답에서는 첫 행동까지만 처리한다. 가능하면 한 번에 하나만 받는다(6-1)
            kwargs["parallel_tool_calls"] = False
        if req.response_schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": req.response_schema}
        try:
            stream = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as e:
            raise _classify_status(e) from None
        except (openai.APITimeoutError, httpx.TimeoutException):
            raise _Retryable(LlmTimeout(cause="timeout"), "timeout", None) from None
        except (openai.APIConnectionError, httpx.TransportError):
            raise _Retryable(LlmCallFailed(cause="connection"), "connection", None) from None
        holder["stream"] = stream
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        model: str | None = None
        usage = Usage()
        refusal = False
        try:
            for chunk in stream:
                model = getattr(chunk, "model", None) or model
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    cached = getattr(
                        getattr(u, "prompt_tokens_details", None), "cached_tokens", None
                    )
                    usage = Usage(u.prompt_tokens, u.completion_tokens, cached)
                for ch in chunk.choices or []:
                    d = ch.delta
                    if d is not None:
                        if getattr(d, "content", None):
                            text.append(d.content)
                            if req.text_sink is not None:
                                # 받는 쪽이 지난 시도의 조각을 가려 버린다(시간 초과 뒤 늦게 끝난 전송)
                                req.text_sink.feed(holder["attempt"], d.content)
                        if getattr(d, "refusal", None):
                            refusal = True
                        for tc in getattr(d, "tool_calls", None) or []:
                            slot = calls.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                            if tc.id:
                                slot["id"] = tc.id
                            fn = tc.function
                            if fn is not None:
                                if fn.name:
                                    slot["name"] += fn.name
                                if fn.arguments:
                                    slot["args"] += fn.arguments
                    if ch.finish_reason:
                        finish = ch.finish_reason
        except (httpx.TimeoutException, openai.APITimeoutError):
            raise _Retryable(LlmTimeout(cause="stream_timeout"), "stream_timeout", None) from None
        except (httpx.TransportError, openai.APIConnectionError):
            raise _Retryable(LlmCallFailed(cause="stream_cut"), "stream_cut", None) from None
        mode = "schema" if req.response_schema is not None else "tools"
        if refusal or finish == "content_filter":
            raise _with_usage(LlmRefused(cause="refused"), usage)
        if finish == "length":
            raise _with_usage(LlmInvalidOutput(cause="finish_reason=length"), usage)
        if finish not in _ALLOWED_FINISH[mode]:
            raise _with_usage(LlmInvalidOutput(cause=f"finish_reason={finish}"), usage)
        tool_calls: list[ToolCall] = []
        for idx in sorted(calls):
            c = calls[idx]
            if not c["id"] or not c["name"]:
                # 호출 id · 이름이 없는 불완전한 도구 호출은 실행하지 않는다(6-11 · L1-12 · L1-15)
                raise _with_usage(LlmInvalidOutput(cause="incomplete_tool_call"), usage)
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            tool_calls.append(ToolCall(id=c["id"], name=c["name"], args=args, raw_args=c["args"]))
        message = "".join(text) or None
        parsed = None
        if mode == "schema":
            if not message:
                raise _with_usage(LlmInvalidOutput(cause="empty"), usage)
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                raise _with_usage(LlmInvalidOutput(cause="json"), usage) from None
            if not isinstance(parsed, dict):
                raise _with_usage(LlmInvalidOutput(cause="json"), usage)
        elif not message and not tool_calls:
            raise _with_usage(LlmInvalidOutput(cause="empty"), usage)
        return LlmResult(
            message=message,
            tool_calls=tool_calls,
            finish_reason=finish or "stop",
            model=model,
            usage=usage,
            parsed=parsed,
        )

    def _emit(
        self,
        req: LlmRequest,
        attempt: int,
        outcome: str,
        r: LlmResult | None,
        started: float,
        cause: str,
    ) -> None:
        self._log(
            {
                "event": "llm_call",
                "role": "chat_agent",
                "purpose": req.purpose,
                "attempt": attempt,
                "outcome": outcome,
                "cause": cause,
                "model": r.model if r else None,
                "tokens_in": r.usage.input_tokens if r else None,
                "tokens_out": r.usage.output_tokens if r else None,
                "duration_ms": round((self._monotonic() - started) * 1000, 1),
            }
        )


def _quiet_close(stream: Any) -> None:
    with contextlib.suppress(Exception):
        stream.close()


class _Retryable(Exception):
    def __init__(self, error: LlmError, cause: str, retry_after: float | None) -> None:
        super().__init__(cause)
        self.error = error
        self.cause = cause
        self.retry_after = retry_after


def _with_usage(err: LlmError, usage: Usage) -> LlmError:
    err._usage_one = usage  # type: ignore[attr-defined]
    return err


def _retry_after(e: openai.APIStatusError) -> float | None:
    try:
        raw = e.response.headers.get("retry-after")
        return max(0.0, float(raw)) if raw is not None else None
    except (ValueError, AttributeError):
        return None


def _classify_status(e: openai.APIStatusError) -> Exception:
    code = e.status_code
    if code == 429 or code == 503:
        err: LlmError = LlmUnavailable(cause=f"{type(e).__name__}:{code}")
        return _Retryable(err, f"{type(e).__name__}:{code}", _retry_after(e))
    if code >= 500:
        return _Retryable(LlmCallFailed(cause=f"{type(e).__name__}:{code}"), str(code), None)
    # 400 · 401 · 403 · 404: 다시 불러도 같다
    return LlmCallFailed(cause=f"{type(e).__name__}:{code}")
