"""LLM 객체는 여기서만 만든다 (`docs/ai/architecture.md` 9절).

- 부르는 곳: 라우터. 만든 객체를 기능 코어에 인자로 넘기고, 코어는 `.invoke` 만 쓴다.

      deadline = deadline_after(25)                       # 요청마다 마감 하나 (AI 예산, 3절)
      llm = structured_llm(ParsedScript, deadline=deadline)
      result = parse_script(req, llm=llm)                 # 코어 안에서 llm.invoke(...)
      meta = build_meta(FEATURE_VERSION, llm=llm)         # 예: "script_parser-1.0"

- 하는 일 (호출 한 번마다)
  1. 마감까지 남은 시간이 호출 한 번의 시간 제한(`LLM_TIMEOUT_SECONDS`, 기본 12초. 역할에
     기능별 값이 있으면 그 값, 예: `SCRIPT_COVERAGE_LLM_TIMEOUT_SECONDS`)보다 짧으면 새 호출을
     시작하지 않는다
  2. 호출을 별도 스레드에서 돌리고 시간 제한 전체만 기다린다. `ChatOpenAI` 의 timeout 은
     연결 · 읽기 한 번마다라서, 조금씩 오는 응답은 그것만으로 끊기지 않는다
  3. 실패를 공통 예외(`common/errors.py`)로 바꾼다. 시간 초과 · 연결 실패 · 429 · 5xx 만
     마감 안에서 `LLM_MAX_RETRIES`(기본 1)번 다시 부른다. 다시 불러도 같은 실패(설정 없음 ·
     400 · 401 · 출력 잘림 · 형식 오류)는 다시 부르지 않는다
  4. 콜백으로 실제 응답한 모델과 토큰 수를 받아 로그에 남기고 `responded_model` 에 둔다
     (`common/meta.py` 가 `meta.model` 로 싣는다)
- 로그: `llm_call request_id=… role=… model=… attempt=… outcome=… cause=… tokens_in=…
  tokens_out=… duration_ms=…`. `cause` 는 실패한 예외 종류와 상태 코드(예: `BadRequestError:400`).
  프롬프트 · 답 · 키 · 예외 메시지(입력이 섞일 수 있음)는 남기지 않는다
- 시간 초과로 버린 호출도 LLM 쪽에서는 끝까지 돌 수 있다(비용은 그대로 든다). 그래서 재시도는
  마감 안에서 1번뿐이다
- 스트리밍으로 부른다: 게이트웨이가 비스트리밍 요청의 출력 상한을 2,000토큰으로 막기 때문이다.
  결과는 다 모은 뒤 한 번에 돌려주므로 코어가 보기에는 같다
- 고칠 때: 역할 → 묶음 · 출력 상한은 `settings.py` 의 `LLM_ROLES`. 예비 모델(`with_fallbacks`)은
  아직 없다
- 도구 호출 LLM(`tool_llm` · `ToolLlmClient`, 대화 에이전트만): 파일 아래쪽 '도구 호출 LLM' 절.
  `structured_llm` 과 따로 두고 서로의 스레드 · 예외를 쓰지 않는다
  (`docs/ai/features/chat-agent.md` 6-11)
"""

import contextlib
import json
import logging
import threading
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

import httpx2
import openai
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import OpenAIRefusalError
from pydantic import BaseModel, ValidationError

from pitch_coach_ai.common.errors import (
    LlmCallFailed,
    LlmError,
    LlmInvalidOutput,
    LlmTimeout,
    LlmUnavailable,
    log_safe,
)
from pitch_coach_ai.common.request_log import current_request_id
from pitch_coach_ai.features.chat_agent import llm_types as chat_llm
from pitch_coach_ai.settings import LLM_ROLES, LlmRole, ModelBundle, Settings, settings

logger = logging.getLogger("pitch_coach_ai.llm")

# 스트리밍 + 구조화 출력에서 LangChain 이 결과 객체를 메시지에 담으며 pydantic 직렬화 경고를
# 매 호출 낸다(결과는 정상). 로그를 덮지 않게 그 경고만 끈다
warnings.filterwarnings(
    "ignore", message="Pydantic serializer warnings", category=UserWarning, module="pydantic"
)

# LLM 호출을 돌리는 스레드. def 라우터의 스레드 풀(최대 40)과 같은 크기로 둔다.
# 시간 초과로 버린 호출이 끝날 때까지 스레드 하나를 쓰므로, 모자라면 대기 시간도 시간 제한에 든다
_executor = ThreadPoolExecutor(max_workers=40, thread_name_prefix="llm")


def _or_dash(value: object) -> object:
    return "-" if value is None else value


def deadline_after(seconds: float) -> float:
    """지금부터 `seconds` 초 뒤의 마감 시각(`time.monotonic()` 기준).

    라우터가 요청을 받자마자 한 번 만들고, 그 요청의 모든 LLM 호출이 같이 쓴다.
    """
    return time.monotonic() + seconds


class _UsageRecorder(BaseCallbackHandler):
    """호출이 끝나면 응답한 모델 · 토큰 수 · 끝난 이유를 받아 둔다."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        self.finish_reason: str | None = None

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        # 스트리밍이면 llm_output 이 비고, 모델 · 끝난 이유는 generation_info 에,
        # 토큰 수는 메시지의 usage_metadata 에 실린다. 두 모양을 다 읽는다
        output = response.llm_output or {}
        usage = output.get("token_usage") or {}
        info: dict[str, Any] = {}
        message_usage: dict[str, Any] = {}
        if response.generations and response.generations[0]:
            generation = response.generations[0][0]
            info = generation.generation_info or {}
            message = getattr(generation, "message", None)
            message_usage = dict(getattr(message, "usage_metadata", None) or {})
        self.model = output.get("model_name") or info.get("model_name")
        self.tokens_in = usage.get("prompt_tokens", message_usage.get("input_tokens"))
        self.tokens_out = usage.get("completion_tokens", message_usage.get("output_tokens"))
        self.finish_reason = info.get("finish_reason")


# (예외, 다시 부를 수 있나). 위에서부터 맞는 첫 줄을 쓴다
def _classify(exc: BaseException) -> tuple[LlmError, bool]:
    if isinstance(exc, openai.APITimeoutError):
        return LlmTimeout(), True
    if isinstance(exc, openai.APIConnectionError):
        return LlmCallFailed(), True
    # 스트리밍으로 받는 도중의 전송 오류는 openai 예외로 감싸지지 않고 그대로 올라온다
    if isinstance(exc, httpx2.TimeoutException):
        return LlmTimeout(), True
    if isinstance(exc, httpx2.TransportError):
        return LlmCallFailed(), True
    if isinstance(exc, openai.RateLimitError):
        return LlmUnavailable(), True
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code == 503:
            return LlmUnavailable(), True
        if exc.status_code >= 500:
            return LlmCallFailed(), True
        # 400 · 401 · 403 · 404 등: 다시 불러도 같다
        return LlmCallFailed(), False
    if isinstance(
        exc,
        openai.LengthFinishReasonError
        | openai.ContentFilterFinishReasonError
        | OpenAIRefusalError
        | OutputParserException
        | ValidationError,
    ):
        return LlmInvalidOutput(), False
    return LlmCallFailed(), False


def _cause(exc: BaseException) -> str:
    """실패 원인을 로그용 한 단어로. 예외 메시지는 입력이 섞일 수 있어 남기지 않는다.

    예: `BadRequestError:400`, `APITimeoutError`, `OutputParserException`
    """
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return f"{name}:{status}" if status is not None else name


class StructuredLlm:
    """출력 스키마를 고정한 LLM. 코어는 `.invoke(입력)` → 스키마 객체만 쓴다.

    - `invoke` 실패: `LlmTimeout` · `LlmCallFailed` · `LlmInvalidOutput` · `LlmUnavailable`
      (모두 `LlmError`, `code` 를 가짐)
    - `responded_model`: 마지막으로 성공한 응답의 모델 이름. 아직 성공한 호출이 없으면 None
    """

    def __init__(
        self,
        runnable: Runnable,
        *,
        role: str,
        timeout_seconds: float,
        max_retries: int,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runnable = runnable
        self._role = role
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._deadline = deadline
        self._clock = clock
        self.responded_model: str | None = None

    def invoke(self, input: Any) -> Any:  # noqa: A002  # LangChain 과 같은 이름
        last_error: LlmError | None = None
        for attempt in range(1, self._max_retries + 2):
            if self._deadline is not None:
                remaining = self._deadline - self._clock()
                if remaining < self._timeout:
                    # 마감 안에 끝낼 수 없는 호출은 시작하지 않는다 (architecture.md 9절)
                    self._log(attempt, "skipped_deadline", None, 0.0, "remaining<timeout")
                    raise last_error or LlmTimeout()
            recorder = _UsageRecorder()
            started = self._clock()
            future = _executor.submit(
                self._runnable.invoke, input, RunnableConfig(callbacks=[recorder])
            )
            # 제출에 걸린 시간까지 마감에서 뺀다 (마감을 넘겨 기다리지 않게)
            wait = self._timeout
            if self._deadline is not None:
                wait = min(wait, max(0.0, self._deadline - self._clock()))
            try:
                result = future.result(timeout=wait)
            except FutureTimeout:
                future.cancel()
                error, retryable = LlmTimeout(), True
                cause = f"no_reply_in_{wait:g}s"
            except Exception as exc:  # noqa: BLE001  # 아래에서 공통 예외로 바꾼다
                error, retryable = _classify(exc)
                cause = _cause(exc)
            else:
                if recorder.finish_reason == "length":
                    # 출력 상한에서 잘렸다. 형식이 맞아 보여도 받지 않는다
                    error, retryable = LlmInvalidOutput(), False
                    cause = "finish_reason=length"
                else:
                    self.responded_model = recorder.model
                    self._log(attempt, "ok", recorder, self._clock() - started, "-")
                    return result
            self._log(attempt, error.code, recorder, self._clock() - started, cause)
            last_error = error
            if not retryable:
                raise error
        assert last_error is not None
        raise last_error

    def _log(
        self,
        attempt: int,
        outcome: str,
        rec: _UsageRecorder | None,
        seconds: float,
        cause: str,
    ) -> None:
        logger.info(
            "llm_call request_id=%s role=%s model=%s attempt=%d outcome=%s cause=%s "
            "tokens_in=%s tokens_out=%s duration_ms=%.1f",
            log_safe(current_request_id() or "-"),
            self._role,
            _or_dash(rec.model if rec else None),
            attempt,
            outcome,
            cause,
            _or_dash(rec.tokens_in if rec else None),
            _or_dash(rec.tokens_out if rec else None),
            seconds * 1000,
        )


def structured_llm(
    schema: type[BaseModel],
    model: str = "default",
    deadline: float | None = None,
    *,
    app_settings: Settings = settings,
) -> StructuredLlm:
    """출력 스키마를 고정한 LLM 객체를 만든다.

    - schema: 출력 pydantic 모델. LLM 이 이 모양(JSON 스키마)으로만 답하게 한다
    - model: 역할 이름 (`settings.LLM_ROLES` 의 키). 기능 코드는 묶음 이름을 모른다
    - deadline: `deadline_after(...)` 로 만든 요청 마감. None 이면 호출 시간 제한만 건다
    - 실패: 역할의 묶음이 설정되지 않았거나 LLM 객체를 만들다 예외가 나면 `LlmUnavailable`(503,
      `ai-service.md` 6-2). 기본 결과를 내는 기능(코칭 계획 · 리뷰)은 이것을 잡아 `llm=None` 으로
      코어를 부른다. 만들다 난 예외는 클래스 이름만 로그에 남긴다(메시지에 설정값이 섞일 수 있다)
    """
    role = LLM_ROLES[model]
    bundle = app_settings.bundle(role.bundle)
    if bundle is None:
        raise LlmUnavailable()
    try:
        runnable = _client(schema, role, bundle, app_settings)
    except Exception as exc:
        logger.error("llm_build_failed role=%s cause=%s", log_safe(model), type(exc).__name__)
        raise LlmUnavailable() from exc
    return StructuredLlm(
        runnable,
        role=model,
        timeout_seconds=app_settings.timeout_for(role),
        max_retries=app_settings.llm_max_retries,
        deadline=deadline,
    )


def _client(
    schema: type[BaseModel], role: LlmRole, bundle: ModelBundle, app_settings: Settings
) -> Runnable:
    """역할 · 묶음으로 출력 스키마를 고정한 LLM 클라이언트를 만든다.

    여기서 난 예외(설정 값 · 라이브러리)만 `structured_llm` 이 `LlmUnavailable` 로 바꾼다.
    """
    client = ChatOpenAI(
        model=bundle.model,
        base_url=bundle.base_url,
        api_key=bundle.api_key,
        max_completion_tokens=role.max_output_tokens,
        reasoning_effort=role.reasoning_effort,
        # 연결 · 읽기 한 번마다의 시간 제한. 호출 전체는 StructuredLlm 이 같은 값으로 끊는다.
        # 버린 호출의 스레드가 오래 남지 않게 같은 값을 준다
        timeout=app_settings.timeout_for(role),
        # 재시도는 StructuredLlm 이 마감을 보며 한다 (SDK 자체 재시도는 끈다)
        max_retries=0,
        # 게이트웨이는 스트리밍이 아닌 요청의 출력 상한을 2,000토큰 이하로만 받고(넘으면 400),
        # 긴 비스트리밍 답은 생성 중에 끊는다. 스트리밍으로 받아 역할 상한을 그대로 쓴다.
        # stream_usage: 스트리밍에서도 토큰 수를 받는다
        streaming=True,
        stream_usage=True,
    )
    return client.with_structured_output(schema)


# ── 도구 호출 LLM (대화 에이전트) ──────────────────────────────────────────────
# 대화 에이전트(`features/chat_agent`)만 쓴다. 기존 `structured_llm` 과 따로 둔다(기능 문서 6-11):
# - 코어가 시도마다 한도를 원자적으로 예약하고(`before_attempt`), 시도 수 · 사용량을 한도에 센다
# - 도구 호출을 주고받고, 구조화 출력(`response_schema`)도 같은 함수로 받는다
# - 대화 전용 실행 풀(크기 = 동시 실행 상한 × 2)에서 돌아 코치 · 리뷰의 스레드(`_executor`)를
#   쓰지 않는다
# - 공급자 SDK(openai)를 직접 부른다(LangChain 을 거치지 않음): 도구 호출 조각 · 끝난 이유 ·
#   사용량을 그대로 본다
# 코어의 LLM 모양 · 예외는 이름이 공통 예외(`common/errors.py`)와 겹치므로
# 모듈 별칭(`chat_llm`)으로만 쓴다. 이 절의 예외는 코어의 것이고, `structured_llm` 의 공통 예외와
# 섞이지 않는다.

#: 시간 초과 뒤 앞 시도가 끝났는지 기다려 보는 시간(초). 이 안에 끝나야 다시 보낸다
_CANCEL_GRACE_SECONDS = 0.5
#: Gemini 의 공식 대체 서명: 모델이 만들지 않은 함수 호출에 쓴다
#: (코드가 넣은 호출 · 저장한 기록에서 되살린 호출)
#: (Google AI 문서 'Thought signatures' FAQ, 2026-11-11 확인)
GEMINI_DUMMY_SIGNATURE = "skip_thought_signature_validator"


def _is_gemini(model: str | None) -> bool:
    return "gemini" in (model or "").lower()


def _with_gemini_signatures(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini 호환 API 는 이번 차례의 함수 호출마다 thought_signature 를 요구한다(없으면 400).
    모델이 붙여 보낸 칸(`extra_content`)은 루프가 그대로 돌려주고, 칸이 없는 호출(코드가 먼저
    읽은 호출 · 서명 상태에서 되살린 호출)에는 공식 대체 서명을 채운다. 다른 공급자에는
    쓰지 않는다(OpenAI 계열 요청 모양은 그대로)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if calls and not all(_has_gemini_signature(c) for c in calls):
            m = {
                **m,
                "tool_calls": [
                    c if _has_gemini_signature(c) else _with_dummy_signature(c) for c in calls
                ],
            }
        out.append(m)
    return out


def _has_gemini_signature(call: dict[str, Any]) -> bool:
    """칸이 있어도 서명 값이 비었으면(`{"google": {}}` · null) 없는 것으로 본다."""
    extra = call.get("extra_content")
    google = extra.get("google") if isinstance(extra, dict) else None
    return isinstance(google, dict) and bool(google.get("thought_signature"))


def _with_dummy_signature(call: dict[str, Any]) -> dict[str, Any]:
    """서명만 대체 서명으로 채우고, 공급자가 붙인 다른 칸은 그대로 둔다(입력은 바꾸지 않는다)."""
    extra = dict(call["extra_content"]) if isinstance(call.get("extra_content"), dict) else {}
    google = dict(extra["google"]) if isinstance(extra.get("google"), dict) else {}
    google["thought_signature"] = GEMINI_DUMMY_SIGNATURE
    extra["google"] = google
    return {**call, "extra_content": extra}


_ALLOWED_FINISH = {"tools": {"stop", "tool_calls"}, "schema": {"stop"}}


@dataclass(frozen=True)
class ToolRole:
    """도구 호출 역할 설정: 출력 상한 · 추론 강도 · 호출 한 번 시간 제한.

    `LLM_ROLES` 와 `Settings` 로 만든다(`tool_role`).

    - purpose_efforts: 호출 목적별 추론 강도((목적, 강도) 묶음, 예: (("ANSWER", "medium"),)). 없으면
      `reasoning_effort`. 연구 모듈 평가의 비교 실험만 쓴다. 도구 모드(조사의 도구 고르기)는
      게이트웨이 제약으로 언제나 none 이라 이 값과 관계없다
    """

    max_output_tokens: int
    reasoning_effort: str | None
    timeout_seconds: float
    purpose_efforts: tuple[tuple[str, str], ...] = ()
    #: 역할 이름(호출 로그의 `role=`)
    name: str = "chat_agent"

    def effort(self, purpose: str) -> str | None:
        return dict(self.purpose_efforts).get(purpose, self.reasoning_effort)


def tool_role(
    name: str = "chat_agent",
    *,
    app_settings: Settings = settings,
    max_call_seconds: float | None = None,
) -> ToolRole:
    """역할 이름으로 `ToolRole` 을 만든다. 예: `tool_role()` → 출력 2,000 · 추론 low · 12초.

    - max_call_seconds: 호출 한 번의 상한. 대화 코어는 답 쓰기 · 고치기 몫을 호출 한 번
      12초로 잡아 두므로(기능 문서 8-3) 부르는 쪽이 그 값을 넘긴다. 설정
      (`LLM_TIMEOUT_SECONDS`)이 더 작으면 설정을 따르고, 더 커도 이 상한을 넘지 않는다
    """
    role = LLM_ROLES[name]
    timeout = app_settings.timeout_for(role)
    if max_call_seconds is not None:
        timeout = min(timeout, max_call_seconds)
    return ToolRole(
        max_output_tokens=role.max_output_tokens,
        reasoning_effort=role.reasoning_effort,
        timeout_seconds=timeout,
        name=name,
    )


class ToolLlmClient:
    """OpenAI 호환 엔드포인트로 도구 호출 · 구조화 호출을 한다.

    - 코어의 `ToolLlm` 모양(`invoke`)을 따른다.
    - 결과: 도구 · 구조화 모드 모두 `LlmResult`
      `{message, tool_calls, finish_reason, model, usage, attempts, parsed}`.
    - 처리 (시도마다)
      1. 코어의 `before_attempt(n)` 이 False 면 보내지 않는다(실행 한도 원자 예약, 기능 문서 8-3).
      2. 대화 전용 실행 풀의 스레드에서 스트리밍으로 부르고 시간 제한 전체만 기다린다
         (게이트웨이는 비스트리밍 출력 상한이 2,000토큰이라 `structured_llm` 과 같이 스트리밍).
         시간 초과면 스트림을 닫고, 그 자리는 끝날 때까지 남는다.
      3. 실패를 코어의 예외로 바꾼다. 시간 초과 · 연결 실패 · 429 · 5xx 만 남은 시간 안에서
         1번 다시 부른다. 429 · 503 은 `Retry-After` 가 유효하면 따르고(대기 + 호출 한 번이
         남지 않으면 다시 부르지 않음), 헤더가 없으면 0.5초 기다린다. 400 · 401 · 403 · 404 ·
         잘린 출력 · 형식 오류 · 빈 답 · 모르는 끝난 이유는 다시 부르지 않는다
         (기능 문서 6-11 · L1-11 · L1-12). 스트림 도중의 전송 오류는 openai 예외로 감싸지지 않고
         httpx2 예외로 오므로 그것도 잡는다(`structured_llm` 과 같음).
      4. 끝난 이유를 단계별 허용 목록으로 본다: 도구 모드 stop · tool_calls, 구조화 모드 stop.
         length 는 잘림(무효 출력), content_filter · refusal 은 거절, 그 밖은 무효 출력.
    - `saturated()`: 실행 풀이 다 찼는가(시간 초과로 버렸지만 끝나지 않은 호출 포함).
      라우터는 이때 새 턴을 429 `AGENT_BUSY` 로 거절한다(L1-14).
    - 로그 · 예외에는 프롬프트 · 답 · 키 · 예외 메시지를 넣지 않는다(예외 종류와 상태 코드만).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        role: ToolRole,
        pool_size: int = 8,
        executor: ThreadPoolExecutor | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = openai.OpenAI(
            base_url=base_url, api_key=api_key, max_retries=0, timeout=role.timeout_seconds
        )
        self._model = model
        self._role = role
        self._pool_size = pool_size
        self._executor = executor or ThreadPoolExecutor(
            max_workers=pool_size, thread_name_prefix="chat-llm"
        )
        #: 실행 중인 호출 수(시간 초과로 버렸지만 아직 안 끝난 것 포함)
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._monotonic = monotonic
        self._sleep = sleep
        self._log = log or _log_tool_call
        self.model_name: str | None = None

    # ── 공개 ──────────────────────────────────────────────────────────
    def saturated(self) -> bool:
        with self._inflight_lock:
            return self._inflight >= self._pool_size

    def _done(self, _f: Any) -> None:
        with self._inflight_lock:
            self._inflight -= 1

    def invoke(
        self, req: chat_llm.LlmRequest, before_attempt: Callable[[int], bool]
    ) -> chat_llm.LlmResult:
        mode = "schema" if req.response_schema is not None else "tools"
        usages: list[chat_llm.Usage] = []
        errors: list[str] = []
        last: chat_llm.LlmError | None = None
        sink = req.text_sink
        for attempt in range(1, req.max_attempts + 1):
            if not before_attempt(attempt):
                raise chat_llm.LlmBudgetStop(
                    attempts=attempt - 1, usage=usages, cause="budget", attempt_errors=errors
                )
            left = req.deadline - self._monotonic()
            timeout = min(self._role.timeout_seconds, left)
            if timeout <= 0:
                raise last or chat_llm.LlmTimeout(
                    attempts=attempt - 1, usage=usages, cause="deadline"
                )
            started = self._monotonic()
            if sink is not None:
                sink.start(attempt)
            try:
                result = self._once(req, mode, timeout, attempt)
            except _Retryable as r:
                usages.append(r.usage if r.usage is not None else chat_llm.Usage())
                errors.append(r.error.code)
                last = r.error
                last.attempts, last.usage = attempt, list(usages)
                self._emit(req, attempt, r.error.code, None, started, r.cause)
                wait = r.retry_after if r.retry_after is not None else 0.5
                # 다시 부를 시간: 대기 + 호출 한 번이 마감 안에 들어와야 한다(기능 문서 8-3).
                # 앞 시도의 문장을 이미 화면에 냈으면 다시 보내지 않는다
                # (낸 문장이 다른 초안과 섞이지 않게, 5-4 · 6-11)
                shown = sink is not None and sink.committed()
                if (
                    attempt < req.max_attempts
                    and not shown
                    and req.deadline - self._monotonic() >= wait + self._role.timeout_seconds
                ):
                    self._sleep(wait)
                    continue
                raise last from None
            except chat_llm.LlmError as e:
                usages.append(getattr(e, "_usage_one", chat_llm.Usage()))
                e.attempts, e.usage = attempt, list(usages)
                self._emit(req, attempt, e.code, None, started, e.cause)
                raise
            usages.append(result.usage)
            self.model_name = result.model or self.model_name
            self._emit(req, attempt, "ok", result, started, "-")
            return chat_llm.LlmResult(
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
        raise last or chat_llm.LlmCallFailed(attempts=req.max_attempts, usage=usages)

    # ── 한 번 보내기 ──────────────────────────────────────────────────
    def _once(
        self, req: chat_llm.LlmRequest, mode: str, timeout: float, attempt: int = 1
    ) -> chat_llm.LlmResult:
        holder: dict[str, Any] = {"attempt": attempt}
        with self._inflight_lock:
            self._inflight += 1
        try:
            future = self._executor.submit(self._stream, req, holder)
        except RuntimeError as exc:
            if "shutdown" not in str(exc):
                # 스레드 시작 실패 등: 작업이 이미 대기열에 들어갔을 수 있어 자리를 그대로 둔다
                raise
            # 실행 풀이 닫힘(프로세스 종료 중): 대기열에 넣기 전 거절이라 보내지 않음이 확실하다.
            # 센 자리를 돌려주고 보내지 않은 실패로 끝낸다
            self._done(None)
            raise chat_llm.LlmCallFailed(cause="executor_closed") from None
        future.add_done_callback(self._done)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            if future.cancel():
                # 풀이 차서 아직 시작하지 않았다(보내지 않음이 확실): 다시 보낼 수 있다
                raise _Retryable(
                    chat_llm.LlmTimeout(cause="queued_timeout"), "queued_timeout", None
                ) from None
            stream = holder.get("stream")
            if stream is not None:
                # 전송 취소 · 응답 close 시도(6-11).
                # close 가 멈출 수 있어 따로 돌리고 기다리지 않는다
                threading.Thread(target=_quiet_close, args=(stream,), daemon=True).start()
            try:
                late = future.result(timeout=_CANCEL_GRACE_SECONDS)
            except FutureTimeout:
                # 앞 시도가 끝났는지 확인하지 못하면 같은 요청을 다시 보내지 않는다
                # (6-11 · L1-14: 중복 호출 · 과금)
                raise chat_llm.LlmTimeout(cause="timeout_unconfirmed") from None
            except chat_llm.LlmError:
                raise  # 끝났고 무효 출력 · 거절이면 다시 보내지 않는다
            except (
                _Retryable
            ) as r:  # 닫혀서 끊긴 것 등: 끝났으니 다시 보낼 수 있다(받은 사용량은 둔다)
                raise _Retryable(
                    chat_llm.LlmTimeout(cause="timeout"), "timeout", None, r.usage
                ) from None
            except Exception:  # noqa: BLE001  # 닫혀서 끊긴 것 등: 끝났으니 다시 보낼 수 있다
                raise _Retryable(chat_llm.LlmTimeout(cause="timeout"), "timeout", None) from None
            return late  # 마감 직후 끝난 결과는 그대로 쓴다

    def _stream(self, req: chat_llm.LlmRequest, holder: dict[str, Any]) -> chat_llm.LlmResult:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _with_gemini_signatures(req.messages)
            if _is_gemini(self._model)
            else req.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": min(req.max_output_tokens, self._role.max_output_tokens),
        }
        if req.tools:
            # 게이트웨이의 gpt-6-luna 는 chat completions 에서 도구와 추론을 함께 받지 않는다
            # (400 "Function tools with reasoning_effort are not supported ... set reasoning_effort
            # to 'none'", 2026-11-04 확인). 도구 모드는 none
            kwargs["reasoning_effort"] = "none"
        elif self._role.effort(req.purpose):
            kwargs["reasoning_effort"] = self._role.effort(req.purpose)
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
            raise _classify_tool_status(e) from None
        except (openai.APITimeoutError, httpx2.TimeoutException):
            raise _Retryable(chat_llm.LlmTimeout(cause="timeout"), "timeout", None) from None
        except (openai.APIConnectionError, httpx2.TransportError):
            raise _Retryable(
                chat_llm.LlmCallFailed(cause="connection"), "connection", None
            ) from None
        holder["stream"] = stream
        text: list[str] = []
        #: 도구 호출 조각을 모으는 칸. 열쇠는 조각의 `index`이고,
        #: index 없이 오는 공급자(Gemini 호환 API)는 호출 id 로 나눈다
        calls: dict[Any, dict[str, Any]] = {}
        #: 호출이 여럿인데 index · id 가 없는 조각이 왔다(어느 호출의 것인지 모름)
        ambiguous = False
        finish: str | None = None
        model: str | None = None
        usage = chat_llm.Usage()
        refusal = False
        try:
            for chunk in stream:
                model = getattr(chunk, "model", None) or model
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    cached = getattr(
                        getattr(u, "prompt_tokens_details", None), "cached_tokens", None
                    )
                    usage = chat_llm.Usage(u.prompt_tokens, u.completion_tokens, cached)
                for ch in chunk.choices or []:
                    d = ch.delta
                    if d is not None:
                        if getattr(d, "content", None):
                            text.append(d.content)
                            if req.text_sink is not None:
                                # 받는 쪽이 지난 시도의 조각을 가려 버린다
                                # (시간 초과 뒤 늦게 끝난 전송)
                                req.text_sink.feed(holder["attempt"], d.content)
                        if getattr(d, "refusal", None):
                            refusal = True
                        for tc in getattr(d, "tool_calls", None) or []:
                            idx = getattr(tc, "index", None)
                            if idx is not None:
                                key: Any = idx
                            elif tc.id:
                                key = f"id:{tc.id}"
                            elif len(calls) <= 1:
                                # index · id 가 없는 조각: 모은 호출이 하나뿐이면
                                # 그 호출의 이어지는 조각이다
                                key = next(iter(calls), None)
                            else:
                                # 호출이 여럿인데 어느 호출의 조각인지 알 수 없다:
                                # 잘못 붙여 실행하지 않고 끝에서 무효 출력으로 거절
                                ambiguous = True
                                continue
                            slot = calls.setdefault(key, {"id": None, "name": "", "args": ""})
                            if tc.id:
                                slot["id"] = tc.id
                            extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
                            if extra is not None:
                                # 다음 차례에 그대로 돌려줄 공급자 칸(Gemini thought_signature)
                                slot["extra"] = extra
                            fn = tc.function
                            if fn is not None:
                                if fn.name:
                                    slot["name"] += fn.name
                                if fn.arguments:
                                    slot["args"] += fn.arguments
                    if ch.finish_reason:
                        finish = ch.finish_reason
        except (httpx2.TimeoutException, openai.APITimeoutError):
            raise _Retryable(
                chat_llm.LlmTimeout(cause="stream_timeout"), "stream_timeout", None, _seen(usage)
            ) from None
        except (httpx2.TransportError, openai.APIConnectionError):
            raise _Retryable(
                chat_llm.LlmCallFailed(cause="stream_cut"), "stream_cut", None, _seen(usage)
            ) from None
        except openai.APIError:
            # 스트림 도중 공급자가 SSE `error` 이벤트를 보내면 openai 는 기본 APIError 를
            # 던진다(위 둘의 상위 클래스라 위에서 걸리지 않음). 연결 실패처럼 다룬다: 화면에
            # 낸 문장이 없으면 한 번 다시 보내고, 있으면 실패로 끝내 코어가 받은 문장으로
            # PARTIAL 을 만든다(7 · L1-11)
            raise _Retryable(
                chat_llm.LlmCallFailed(cause="stream_error"), "stream_error", None, _seen(usage)
            ) from None
        mode = "schema" if req.response_schema is not None else "tools"
        if refusal or finish == "content_filter":
            raise _with_usage(chat_llm.LlmRefused(cause="refused"), usage)
        if finish == "length":
            raise _with_usage(chat_llm.LlmInvalidOutput(cause="finish_reason=length"), usage)
        if finish not in _ALLOWED_FINISH[mode]:
            raise _with_usage(chat_llm.LlmInvalidOutput(cause=f"finish_reason={finish}"), usage)
        if ambiguous:
            raise _with_usage(chat_llm.LlmInvalidOutput(cause="ambiguous_tool_call"), usage)
        tool_calls: list[chat_llm.ToolCall] = []
        # 실행 순서: index 가 있는 호출은 index 순서(전과 같음),
        # index 없이 id 로 모은 호출은 받은 순서로 그 뒤
        arrival = {k: i for i, k in enumerate(calls)}
        order = sorted(calls, key=lambda k: (0, k, 0) if isinstance(k, int) else (1, 0, arrival[k]))
        for c in (calls[k] for k in order):
            if not c["id"] or not c["name"]:
                # 호출 id · 이름이 없는 불완전한 도구 호출은 실행하지 않는다(6-11 · L1-12 · L1-15)
                raise _with_usage(chat_llm.LlmInvalidOutput(cause="incomplete_tool_call"), usage)
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            tool_calls.append(
                chat_llm.ToolCall(
                    id=c["id"], name=c["name"], args=args, raw_args=c["args"], extra=c.get("extra")
                )
            )
        message = "".join(text) or None
        parsed = None
        if mode == "schema":
            if not message:
                raise _with_usage(chat_llm.LlmInvalidOutput(cause="empty"), usage)
            try:
                # 같은 키가 두 번이면 무효 출력: 흘려 낸 문장(앞 값)과 다 받은 파싱(뒤 값)이
                # 갈라지지 않게(5-4, L1-46. 대화 경로에만 건다)
                parsed = json.loads(message, object_pairs_hook=_no_duplicate_keys)
            except ValueError:
                raise _with_usage(chat_llm.LlmInvalidOutput(cause="json"), usage) from None
            if not isinstance(parsed, dict):
                raise _with_usage(chat_llm.LlmInvalidOutput(cause="json"), usage)
        elif not message and not tool_calls:
            raise _with_usage(chat_llm.LlmInvalidOutput(cause="empty"), usage)
        return chat_llm.LlmResult(
            message=message,
            tool_calls=tool_calls,
            finish_reason=finish or "stop",
            model=model,
            usage=usage,
            parsed=parsed,
        )

    def _emit(
        self,
        req: chat_llm.LlmRequest,
        attempt: int,
        outcome: str,
        r: chat_llm.LlmResult | None,
        started: float,
        cause: str,
    ) -> None:
        self._log(
            {
                "event": "llm_call",
                "role": self._role.name,
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
    def __init__(
        self,
        error: chat_llm.LlmError,
        cause: str,
        retry_after: float | None,
        usage: chat_llm.Usage | None = None,
    ) -> None:
        super().__init__(cause)
        self.error = error
        self.cause = cause
        self.retry_after = retry_after
        #: 스트림 도중 끊겼을 때 그 전에 받은 사용량(없으면 모름)
        self.usage = usage


def _seen(usage: chat_llm.Usage) -> chat_llm.Usage | None:
    """스트림 도중 받은 사용량. 사용량 조각을 아직 받지 못했으면 None(모름)."""
    return usage if usage != chat_llm.Usage() else None


def _with_usage(err: chat_llm.LlmError, usage: chat_llm.Usage) -> chat_llm.LlmError:
    err._usage_one = usage  # type: ignore[attr-defined]
    return err


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k}")
        out[k] = v
    return out


def _tool_retry_after(e: openai.APIStatusError) -> float | None:
    try:
        raw = e.response.headers.get("retry-after")
        return max(0.0, float(raw)) if raw is not None else None
    except (ValueError, AttributeError):
        return None


def _classify_tool_status(e: openai.APIStatusError) -> Exception:
    code = e.status_code
    if code == 429 or code == 503:
        err: chat_llm.LlmError = chat_llm.LlmUnavailable(cause=f"{type(e).__name__}:{code}")
        return _Retryable(err, f"{type(e).__name__}:{code}", _tool_retry_after(e))
    if code >= 500:
        return _Retryable(
            chat_llm.LlmCallFailed(cause=f"{type(e).__name__}:{code}"), str(code), None
        )
    # 400 · 401 · 403 · 404: 다시 불러도 같다
    return chat_llm.LlmCallFailed(cause=f"{type(e).__name__}:{code}")


def _log_tool_call(event: dict[str, Any]) -> None:
    """도구 호출 LLM 로그 한 줄(`structured_llm` 과 같은 모양).

    프롬프트 · 답 · 키 · 예외 메시지는 남기지 않는다.
    """
    logger.info(
        "llm_call request_id=%s role=%s purpose=%s model=%s attempt=%d outcome=%s cause=%s "
        "tokens_in=%s tokens_out=%s duration_ms=%.1f",
        log_safe(current_request_id() or "-"),
        event["role"],
        event["purpose"],
        _or_dash(event["model"]),
        event["attempt"],
        event["outcome"],
        log_safe(event["cause"]),
        _or_dash(event["tokens_in"]),
        _or_dash(event["tokens_out"]),
        event["duration_ms"],
    )


def tool_llm(
    role: str = "chat_agent",
    *,
    pool_size: int,
    app_settings: Settings = settings,
    max_call_seconds: float | None = None,
) -> ToolLlmClient | None:
    """도구 호출 LLM 을 만든다(대화 에이전트, 기능 문서 6-11). 프로세스에서 한 번 만들어 같이 쓴다.

    - role: 역할 이름(`settings.LLM_ROLES` 의 키)
    - pool_size: 대화 전용 실행 풀 크기(동시 실행 상한 × 2). 끝나지 않은 호출도 자리를 차지한다
    - 역할의 묶음이 설정되지 않았거나 클라이언트를 만들다 예외가 나면 None 이다. 코어는 LLM 이
      None 이면 스트림 전 503 `LLM_UNAVAILABLE` 로 답한다. 만들다 난 예외는 클래스 이름만 남긴다
    """
    llm_role = LLM_ROLES.get(role)
    if llm_role is None:
        # 없는 역할도 '모델 없음'으로 통일한다(기능 문서 L1-05)
        logger.error("llm_build_failed role=%s cause=UnknownRole", log_safe(role))
        return None
    bundle = app_settings.bundle(llm_role.bundle)
    if bundle is None:
        return None
    try:
        return ToolLlmClient(
            base_url=bundle.base_url,
            api_key=bundle.api_key.get_secret_value(),
            model=bundle.model,
            role=tool_role(role, app_settings=app_settings, max_call_seconds=max_call_seconds),
            pool_size=pool_size,
        )
    except Exception as exc:  # noqa: BLE001  # 설정 값 · 라이브러리 오류는 '모델 없음'으로 본다
        logger.error("llm_build_failed role=%s cause=%s", log_safe(role), type(exc).__name__)
        return None
