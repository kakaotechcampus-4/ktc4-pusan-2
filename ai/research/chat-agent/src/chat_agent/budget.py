"""실행 한도 · 시간 예약 · 출력 정산 (기능 문서 8-1 · 8-2 · 8-3, 7-3 L1-04 · L1-13 · L1-16 · L1-23 · L1-31 · L1-39).

- 누가 쓰나: loop.py(모델 · 도구 · 행동 · 계획 수정 전마다), turn.py(시작 시간 · /agent/turn 호출 수 · 답 몫 확인).
- 하는 일
  1. 시작 때 남은 시간 = max(0, 120 − elapsed_ms/1000)초, 이 호출의 AI 마감 = 지금(monotonic) + min(45, 남은 시간).
  2. 모델 전송 시도마다 한도를 원자적으로 예약한다(`reserve_model`): 모델 호출 수(실행 전체 18번) · 시간 · 입력 토큰 누적(72k) ·
     출력 토큰 누적(8k). 조사(RESEARCH · 형식 고치기) 시도는 "이번 시도를 포함한 누적 시도 수가 16을 넘거나 전송 뒤 24초를
     보장하지 못하면" 보내지 않는다(답 쓰기 1번 · 고치기 1번 몫과 그 24초는 `/agent/turn` 응답마다 남긴다).
     이어 부를 때 남은 호출 수로 두 몫을 남길 수 없으면 모델 없이 검증된 결과로 끝낸다(`answer_slots_short`).
  3. 응답을 받으면 입력 · 출력 예약을 공급자 실측(`usage.input_tokens`(캐시 포함 전체 입력) · `usage.output_tokens`)으로 바꿔
     쓰지 않은 몫을 돌려준다(`settle_input` · `settle_output`). 실측이 없거나 실패한 시도는 예약 그대로 남긴다.
  4. 도구 · 행동 · 저장 행동 · 다시 평가 · 계획 수정 수도 여기서 센다. 넘으면 그 일을 하지 않고 `LIMIT` 진단 단계를 남긴다.
  5. 한도에 닿은 코드를 진단 끝 사유(`TIME` · `MODEL_CALLS` · `TOOL_CALLS` · `TOKENS`)로 옮긴다(`limit_end_reason`, 11-5).
- 숫자: 모든 카운터는 엄격한 비음수 정수이고 내보내는 값은 줄지 않는다(재개 때 요청 값이 서명값보다 작으면 turn.py 가 409).
  입력 · 출력 정산은 이 호출이 방금 예약한 몫만 줄이고 호출을 받을 때의 값 아래로는 내리지 않는다(`input_floor` · `output_floor`).
- 토큰: 전송 전에는 입력 추정량(UTF-8 바이트 ÷ 3 올림 + 메시지당 8, 한글 한 글자 ≈ 한 토큰으로 넉넉히 셈)과 그 호출의 출력 상한을
  예약하고, 응답 뒤에는 둘 다 공급자 실측이다(추정량은 전송 전 검사에만 쓴다). 관측 사용량(`usage`)은 따로 모은다(5-5).
- 고칠 때: 한도 값은 config.py, 이 파일은 순서 · 예약 · 정산 규칙만 둔다. 순서는 8-3 '호출 전 확인 순서'(남은 시간 → 모델 호출 수 →
  도구 호출 수 → 행동 수)다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .config import ChatAgentConfig

ModelKind = Literal["RESEARCH", "ANSWER", "FIX"]

#: 한도 코드(LimitHit.code) → 진단 끝 사유(11-5). 여기에 없는 코드는 이유를 나눌 수 없어 `LIMIT`
_END_REASON_OF_LIMIT = {
    "CALL_TIME": "TIME",
    "TURN_TIME": "TIME",
    "MODEL_CALLS": "MODEL_CALLS",
    "TOOL_CALLS": "TOOL_CALLS",
    "INPUT_TOKENS": "TOKENS",
    "OUTPUT_RESERVE": "TOKENS",
}
#: 한도 때문에 끝났음을 뜻하는 끝 사유
LIMIT_END_REASONS = ("TIME", "MODEL_CALLS", "TOOL_CALLS", "TOKENS", "LIMIT")


def limit_end_reason(code: str) -> str:
    """한도 코드를 진단 끝 사유로 옮긴다(시간 120초 · 호출 45초 = TIME, 입력 · 출력 = TOKENS)."""
    return _END_REASON_OF_LIMIT.get(code, "LIMIT")


@dataclass
class Counters:
    """한 실행의 누적량. 서명 상태로 이어지고 재개해도 처음부터 세지 않는다(8-3)."""

    model_calls: int = 0
    tool_calls: int = 0
    elapsed_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: /agent/turn 호출 수(처음 호출이 1)
    turn_count: int = 0
    actions: int = 0
    saves: int = 0
    reassess: int = 0
    plan_edits: int = 0

    def budget_used(self) -> dict[str, int]:
        """응답 `budget_used`(5-3과 같은 모양)."""
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "elapsed_ms": self.elapsed_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    def dump(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def load(cls, data: dict[str, Any]) -> Counters:
        return cls(**{k: int(v) for k, v in data.items() if k in cls.__dataclass_fields__})


def estimate_tokens(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> int:
    """전송 전 입력 토큰 예약량(보수적). 바이트 ÷ 3 올림 + 메시지당 8."""
    blob = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    if tools:
        blob += json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(blob.encode("utf-8")) / 3) + 8 * len(messages)


@dataclass
class LimitHit:
    """한도에 닿은 기록(진단 기록 LIMIT 단계, 11-5)."""

    code: str
    used: int
    limit: int
    attempted: int | None = None


@dataclass
class Budget:
    """한 /agent/turn 호출 동안의 한도 판단."""

    cfg: ChatAgentConfig
    counters: Counters
    monotonic: Callable[[], float]
    #: 이 호출을 받은 monotonic 시각
    started: float
    #: 이 호출을 받을 때까지 실행이 쓴 시간(ms, BE 행동 실행 시간 포함)
    elapsed_at_start_ms: int
    hits: list[LimitHit] = field(default_factory=list)
    #: 이 호출을 받을 때의 입력 · 출력 누적. 정산이 이 아래로 내리지 않는다(내보낸 값은 줄지 않음)
    input_floor: int = 0
    output_floor: int = 0

    @classmethod
    def start(
        cls, cfg: ChatAgentConfig, counters: Counters, monotonic: Callable[[], float]
    ) -> Budget:
        return cls(
            cfg=cfg,
            counters=counters,
            monotonic=monotonic,
            started=monotonic(),
            elapsed_at_start_ms=counters.elapsed_ms,
            input_floor=counters.input_tokens,
            output_floor=counters.output_tokens,
        )

    # ── 시간 ────────────────────────────────────────────────────────
    def remaining_turn_at_start(self) -> float:
        """시작 때 남은 실행 시간(초) = max(0, 120 − elapsed_ms/1000) (8-3)."""
        return max(0.0, self.cfg.turn_seconds - self.elapsed_at_start_ms / 1000.0)

    @property
    def deadline(self) -> float:
        """이 호출의 AI 마감(monotonic 초) = 시작 + min(45초, 남은 실행 시간)."""
        return self.started + min(self.cfg.call_seconds, self.remaining_turn_at_start())

    def left(self) -> float:
        """마감까지 남은 초(음수면 0)."""
        return max(0.0, self.deadline - self.monotonic())

    def exhausted_at_start(self) -> bool:
        """시작부터 남은 시간이 없으면 모델 · 행동 없이 끝낸다(L1-04)."""
        return self.remaining_turn_at_start() <= 0.0

    def elapsed_ms_now(self) -> int:
        """지금까지의 실행 누적 시간(ms). 응답 budget_used.elapsed_ms 로 나간다."""
        return self.elapsed_at_start_ms + int(round((self.monotonic() - self.started) * 1000))

    def sync_elapsed(self) -> None:
        self.counters.elapsed_ms = max(self.counters.elapsed_ms, self.elapsed_ms_now())

    # ── 모델 ────────────────────────────────────────────────────────
    def reserve_model(self, kind: ModelKind, est_input: int, max_output: int) -> LimitHit | None:
        """모델 전송 시도 하나를 예약한다. 막히면 LimitHit 을 돌려주고 예약하지 않는다.

        - 조사: 이번 시도를 포함한 누적 시도 수 ≤ 18 − 2, 남은 시간 − 답 몫(24초) ≥ 조사 최소 시간(3초)(8-3: 전송 뒤 24초 보장.
          조사 호출의 시간 제한은 `call_timeout`이 min(12, 남은 시간 − 24)로 줄인다),
          입력 · 출력 예약이 이번 것과 답 쓰기 · 고치기 두 번 몫까지 한도 안
        - 답 쓰기 · 고치기: 누적 시도 + 1 ≤ 18, 남은 시간 > 0.5초, 토큰 한도 안
        - 출력은 그 호출의 상한만큼 예약한다. 응답 뒤 `settle_output`이 실측으로 바꾼다
        - 시간으로 막히면 `CALL_TIME`: used = 이 호출에서 지난 초, limit = 그 시도를 시작할 수 있는 마지막 초(정수로 버림)
        """
        c, cfg = self.counters, self.cfg
        self.sync_elapsed()
        left = self.left()
        window = self.deadline - self.started
        spent = int(self.monotonic() - self.started)
        if kind == "RESEARCH":
            if left - cfg.reserved_answer_seconds < cfg.min_research_call_seconds:
                last_start = window - cfg.reserved_answer_seconds - cfg.min_research_call_seconds
                return self._hit("CALL_TIME", spent, max(0, int(last_start)))
            cap = cfg.max_model_calls - cfg.reserved_answer_calls
            if c.model_calls + 1 > cap:
                return self._hit("MODEL_CALLS", c.model_calls, cap)
            need_in = est_input * (1 + cfg.reserved_answer_calls)
            need_out = max_output * (1 + cfg.reserved_answer_calls)
        else:
            if left <= 0.5:
                return self._hit("CALL_TIME", spent, max(0, int(window - 0.5)))
            if c.model_calls + 1 > cfg.max_model_calls:
                return self._hit("MODEL_CALLS", c.model_calls, cfg.max_model_calls)
            need_in, need_out = est_input, max_output
        if c.input_tokens + need_in > cfg.max_input_tokens:
            return self._hit("INPUT_TOKENS", c.input_tokens, cfg.max_input_tokens, need_in)
        if c.output_tokens + need_out > cfg.max_output_tokens:
            return self._hit("OUTPUT_RESERVE", c.output_tokens, cfg.max_output_tokens, need_out)
        c.model_calls += 1
        c.input_tokens += est_input
        c.output_tokens += max_output
        return None

    def settle_input(self, reserved: int, measured: int | None) -> None:
        """응답을 받은 시도의 입력 예약(추정량)을 공급자 실측 입력(캐시 포함 전체)으로 바꾼다(8-3). 규칙은 출력과 같다."""
        if measured is None:
            return
        c = self.counters
        c.input_tokens = max(self.input_floor, c.input_tokens - reserved + max(0, measured))

    def settle_output(self, reserved: int, measured: int | None) -> None:
        """응답을 받은 시도의 출력 예약을 공급자 실측으로 바꾼다(8-3). 실측이 없으면 예약 그대로 둔다.

        이 호출이 방금 예약한 `reserved`만 `measured`로 바꾸고, 호출을 받을 때의 누적(`output_floor`) 아래로는 내리지 않는다.
        """
        if measured is None:
            return
        c = self.counters
        c.output_tokens = max(self.output_floor, c.output_tokens - reserved + max(0, measured))

    def answer_slots_short(self) -> bool:
        """이어 부를 때 남은 호출 수로 답 쓰기 · 고치기 두 몫을 남길 수 없는가(8-3). 그러면 모델 없이 끝낸다."""
        return self.counters.model_calls > self.cfg.max_model_calls - self.cfg.reserved_answer_calls

    def call_timeout(self, kind: ModelKind) -> float:
        """이번 시도의 시간 제한(초). 조사는 답 몫 24초를 빼고, 답 쓰기 · 고치기는 남은 시간 안에서 12초."""
        per_call = self.cfg.reserved_answer_seconds / max(1, self.cfg.reserved_answer_calls)
        left = self.left()
        if kind == "RESEARCH":
            return max(0.0, min(per_call, left - self.cfg.reserved_answer_seconds))
        return max(0.0, min(per_call, left))

    # ── 도구 · 행동 · 계획 ───────────────────────────────────────────
    def reserve_tool(self) -> LimitHit | None:
        """캐시가 아닌 도구 실행 하나를 예약한다(12번)."""
        c = self.counters
        if c.tool_calls + 1 > self.cfg.max_tool_calls:
            return self._hit("TOOL_CALLS", c.tool_calls, self.cfg.max_tool_calls)
        c.tool_calls += 1
        return None

    def reserve_action(self, action_type: str) -> LimitHit | None:
        """행동 하나를 예약한다: 전체 5 · 저장 1 · 다시 평가 1."""
        c, cfg = self.counters, self.cfg
        if c.actions + 1 > cfg.max_actions:
            return self._hit("ACTIONS", c.actions, cfg.max_actions)
        if action_type == "SAVE_GOAL" and c.saves + 1 > cfg.max_save_actions:
            return self._hit("SAVE_ACTIONS", c.saves, cfg.max_save_actions)
        if action_type == "REASSESS" and c.reassess + 1 > cfg.max_reassess_per_turn:
            return self._hit("REASSESS_PER_TURN", c.reassess, cfg.max_reassess_per_turn)
        c.actions += 1
        if action_type == "SAVE_GOAL":
            c.saves += 1
        if action_type == "REASSESS":
            c.reassess += 1
        return None

    def reserve_plan_edit(self) -> LimitHit | None:
        """할 일 목록 고치기(처음 계획 뒤 3번까지)."""
        c = self.counters
        if c.plan_edits + 1 > self.cfg.max_plan_edits:
            return self._hit("PLAN_EDITS", c.plan_edits, self.cfg.max_plan_edits)
        c.plan_edits += 1
        return None

    def turn_limit_hit(self) -> LimitHit | None:
        """한 실행의 /agent/turn 호출이 6번을 넘으면(7번째) 모델 없이 끝낸다(L1-31)."""
        if self.counters.turn_count > self.cfg.max_turn_calls:
            return self._hit("TURN_CALLS", self.counters.turn_count - 1, self.cfg.max_turn_calls)
        return None

    def _hit(self, code: str, used: int, limit: int, attempted: int | None = None) -> LimitHit:
        hit = LimitHit(code=code, used=max(0, used), limit=limit, attempted=attempted)
        self.hits.append(hit)
        return hit
