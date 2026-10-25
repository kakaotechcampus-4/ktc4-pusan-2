"""LLM 객체는 여기서만 만든다 (`docs/ai/architecture.md` 9절).

- 부르는 곳: 라우터. 만든 객체를 기능 코어에 인자로 넘기고, 코어는 `.invoke` 만 쓴다.

      deadline = deadline_after(25)                       # 요청마다 마감 하나 (AI 예산, 3절)
      llm = structured_llm(ParsedScript, deadline=deadline)
      result = parse_script(req, llm=llm)                 # 코어 안에서 llm.invoke(...)
      meta = build_meta(FEATURE_VERSION, llm=llm)         # 예: "script_parser-1.0"

- 하는 일 (호출 한 번마다)
  1. 마감까지 남은 시간이 호출 한 번의 시간 제한(`LLM_TIMEOUT_SECONDS`, 기본 12초)보다
     짧으면 새 호출을 시작하지 않는다
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
"""

import logging
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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
        return _build(schema, model, role, bundle, deadline, app_settings)
    except Exception as exc:
        logger.error("llm_build_failed role=%s cause=%s", log_safe(model), type(exc).__name__)
        raise LlmUnavailable() from exc


def _build(
    schema: type[BaseModel],
    model: str,
    role: LlmRole,
    bundle: ModelBundle,
    deadline: float | None,
    app_settings: Settings,
) -> StructuredLlm:
    """역할 · 묶음으로 LLM 객체를 만든다. 예외는 부르는 쪽(`structured_llm`)이 바꾼다."""
    client = ChatOpenAI(
        model=bundle.model,
        base_url=bundle.base_url,
        api_key=bundle.api_key,
        max_completion_tokens=role.max_output_tokens,
        reasoning_effort=role.reasoning_effort,
        # 연결 · 읽기 한 번마다의 시간 제한. 호출 전체는 StructuredLlm 이 같은 값으로 끊는다.
        # 버린 호출의 스레드가 오래 남지 않게 같은 값을 준다
        timeout=app_settings.llm_timeout_seconds,
        # 재시도는 StructuredLlm 이 마감을 보며 한다 (SDK 자체 재시도는 끈다)
        max_retries=0,
        # 게이트웨이는 스트리밍이 아닌 요청의 출력 상한을 2,000토큰 이하로만 받고(넘으면 400),
        # 긴 비스트리밍 답은 생성 중에 끊는다. 스트리밍으로 받아 역할 상한을 그대로 쓴다.
        # stream_usage: 스트리밍에서도 토큰 수를 받는다
        streaming=True,
        stream_usage=True,
    )
    return StructuredLlm(
        client.with_structured_output(schema),
        role=model,
        timeout_seconds=app_settings.llm_timeout_seconds,
        max_retries=app_settings.llm_max_retries,
        deadline=deadline,
    )
