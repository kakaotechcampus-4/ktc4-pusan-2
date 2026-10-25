"""LLM 호출마다 출력 토큰 수와 끝난 이유를 모은다 — 역할별 출력 토큰 상한을 정하려고 잰다.

서비스 `common/llm.py` 의 `_UsageRecorder` 와 같은 곳에서 값을 읽는다. 스트리밍이면 `llm_output` 이
비고, 토큰 수는 메시지의 `usage_metadata`, 끝난 이유는 `generation_info` 에 실린다.
출력 토큰 수(`output_tokens` · `completion_tokens`)는 추론 토큰을 포함한 값이다(OpenAI 호환 API).

상한 = 잰 최대 × 2 를 1,024 단위로 올림, 최소 2,048 (#503). 잘린 호출(`finish_reason ==
"length"`)이 있으면 그 역할은 상한을 정하지 않는다 — 잰 최대가 진짜 최대가 아니다.
"""

import math
import threading
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

CAP_FACTOR = 2
CAP_STEP = 1024
CAP_MIN = 2048


def output_of(response: LLMResult) -> tuple[int | None, str | None]:
    """응답 하나의 (출력 토큰 수, 끝난 이유). 모르면 None."""
    output = response.llm_output or {}
    usage = output.get("token_usage") or {}
    info: dict[str, Any] = {}
    message_usage: dict[str, Any] = {}
    if response.generations and response.generations[0]:
        generation = response.generations[0][0]
        info = generation.generation_info or {}
        message = getattr(generation, "message", None)
        message_usage = dict(getattr(message, "usage_metadata", None) or {})
    tokens = usage.get("completion_tokens", message_usage.get("output_tokens"))
    return tokens, info.get("finish_reason")


@dataclass
class UsageLog(BaseCallbackHandler):
    """한 역할의 호출 기록. 여러 스레드가 함께 쓴다."""

    tokens: list[int] = field(default_factory=list)
    finish: dict[str, int] = field(default_factory=dict)
    unknown: int = 0
    errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tokens, reason = output_of(response)
        with self._lock:
            if tokens is None:
                self.unknown += 1
            else:
                self.tokens.append(int(tokens))
            key = reason or "unknown"
            self.finish[key] = self.finish.get(key, 0) + 1

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        with self._lock:
            self.errors += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            values = sorted(self.tokens)
            finish = dict(sorted(self.finish.items()))
            unknown, errors = self.unknown, self.errors
        # 잘림은 finish_reason length 로, 또는 구조화 출력이 예외로 올리면 오류로 보인다.
        # 둘 다 없을 때만 잰 최대를 믿는다. 시간 초과로 버린 호출은 끝까지 받지 못해 세지 않는다
        truncated = finish.get("length", 0) + errors
        return {
            "calls": len(values) + unknown,
            "errors": errors,
            "unknown_usage": unknown,
            "finish_reason": finish,
            "output_tokens": _stats(values),
            "recommended_cap": None if truncated or not values else recommended_cap(values[-1]),
        }


def _stats(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None}
    p95 = values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)]
    return {
        "min": values[0],
        "median": values[len(values) // 2],
        "p95": p95,
        "max": values[-1],
    }


def recommended_cap(max_tokens: int) -> int:
    """잰 최대 × 2 를 1,024 단위로 올린 값, 최소 2,048."""
    return max(CAP_MIN, math.ceil(max_tokens * CAP_FACTOR / CAP_STEP) * CAP_STEP)
