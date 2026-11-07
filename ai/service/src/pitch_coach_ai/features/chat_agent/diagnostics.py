"""진단 기록 — 루프가 진행된 순서를 본문 없이 남긴다 (기능 문서 11-5, 5-2 `Diagnostics` · `DiagStep`).

- 누가 쓰나: loop.py · turn.py 가 단계마다 `step()`을 부르고, 끝에 `finish()`로 `final` · `error` 줄에 실을 기록을 만든다.
- 남기는 것: 코드 enum · ID · 수만. 문장 · 인용 · 검색 질의 · 도구 인자 값 · 기록 값(측정 수치) · 예외 메시지는 넣지 않는다(P8).
  공급자 · 예외에서 온 값은 닫힌 enum 으로 바꾼다(모르는 값은 OTHER).
- 같은 단계를 표준 출력 로그 자리(`log` 콜백)에 `event: "agent_step"` 한 줄로, 끝나면 `event: "agent_end"` 한 줄로 남긴다.
  로그에는 줄이지 않은 전체가 남는다.
- 크기: UTF-8 JSON 65,536바이트를 넘으면 11-5 순서로 줄인다:
  ① MODEL 의 model · success · 토큰 밖 필드와 TOOL 의 detail 을 빼고 ② CHECK 는 SUMMARY 만 남기고 ③ 그래도 넘으면 MODEL 과 end 만.
  ② · ③에서 뺀 단계 수를 `end.omitted_steps` 에, 줄였으면 `truncated: true`.
- 고칠 때: detail 필드는 schemas.py 의 닫힌 모델(ModelDetail 등)이 기준이다. 필드를 더하면 그 모델과 기능 문서 11-5 표를 같이 고친다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .schemas import (
    ActionDetail,
    AssessDetail,
    CheckDetail,
    DiagEnd,
    Diagnostics,
    DiagStep,
    DiagVersions,
    LimitDetail,
    ModelDetail,
    ToolDetail,
)

_DETAIL = {
    "MODEL": ModelDetail,
    "TOOL": ToolDetail,
    "CHECK": CheckDetail,
    "ASSESS": AssessDetail,
    "ACTION": ActionDetail,
    "LIMIT": LimitDetail,
}
_FINISH = {
    "stop": "STOP",
    "length": "LENGTH",
    "tool_calls": "TOOL_CALLS",
    "content_filter": "CONTENT_FILTER",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_CODE = re.compile(r"^[A-Z_]{1,40}$")


def finish_code(raw: str | None) -> str | None:
    """공급자의 finish_reason 을 닫힌 enum 으로(모르는 값은 OTHER)."""
    if raw is None:
        return None
    return _FINISH.get(raw, "OTHER")


def code_enum(raw: str | None) -> str | None:
    """오류 · 이유 코드를 `[A-Z_]` 40자 이하로. 아니면 OTHER."""
    if raw is None:
        return None
    return raw if _CODE.match(raw) else "OTHER"


def safe_request_id(raw: str | None, *, run_id: str, turn_call: int) -> str:
    """X-Request-ID 가 영문 · 숫자 · `-` 64자 이하면 그대로, 아니면 실행 · 호출 번호로 만든 값(11-5)."""
    if raw and _REQUEST_ID.match(raw):
        return raw
    digest = hashlib.sha256(f"{run_id}|{turn_call}".encode()).hexdigest()[:24]
    return f"gen-{digest}"


@dataclass
class DiagRecorder:
    """한 /agent/turn 호출의 진단 기록."""

    request_id: str
    run_id: str
    turn_call: int
    versions: dict[str, str]
    monotonic: Callable[[], float]
    log: Callable[[dict[str, Any]], None] = lambda _line: None
    started: float = 0.0
    steps: list[DiagStep] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started:
            self.started = self.monotonic()

    def now_ms(self) -> int:
        return max(0, int(round((self.monotonic() - self.started) * 1000)))

    def step(
        self,
        kind: str,
        code: str,
        detail: dict[str, Any],
        *,
        start_ms: int | None = None,
        ms: int | None = None,
        parent_seq: int | None = None,
    ) -> int:
        """단계 하나를 남기고 그 seq 를 돌려준다. detail 은 종류별 닫힌 모양이어야 한다."""
        seq = len(self.steps) + 1
        st = DiagStep(
            seq=seq,
            kind=kind,  # type: ignore[arg-type]
            code=code,
            start_ms=self.now_ms() if start_ms is None else start_ms,
            ms=ms,
            parent_seq=parent_seq,
            detail=_DETAIL[kind].model_validate(detail),
        )
        self.steps.append(st)
        self.log(
            {
                "event": "agent_step",
                "request_id": self.request_id,
                "run_id": self.run_id,
                "turn_call": self.turn_call,
                **st.model_dump(),
            }
        )
        return seq

    def finish(
        self,
        status: str,
        reason: str,
        *,
        usage: dict[str, Any] | None = None,
        max_bytes: int = 65_536,
    ) -> Diagnostics:
        """끝 단계를 붙여 크기 한도에 맞춘 기록을 돌려주고 agent_end 로그를 남긴다."""
        full = Diagnostics(
            request_id=self.request_id,
            run_id=self.run_id,
            turn_call=self.turn_call,
            versions=DiagVersions(**self.versions),
            steps=list(self.steps),
            end=DiagEnd(status=status, reason=reason, omitted_steps=0),  # type: ignore[arg-type]
            truncated=False,
        )
        self.log(
            {
                "event": "agent_end",
                "request_id": self.request_id,
                "run_id": self.run_id,
                "turn_call": self.turn_call,
                "end": full.end.model_dump(),
                "usage": usage or {},
            }
        )
        return reduce_to_fit(full, max_bytes)


def _size(d: Diagnostics) -> int:
    return len(d.model_dump_json().encode("utf-8"))


def reduce_to_fit(d: Diagnostics, max_bytes: int) -> Diagnostics:
    """11-5 순서로 줄인다. 남은 단계의 seq 는 바꾸지 않는다(빈 번호 · 사라진 parent 를 가리킬 수 있음)."""
    if _size(d) <= max_bytes:
        return d
    # ① MODEL 의 부가 필드 · TOOL detail 빼기 (뺀 필드는 없어도 되는 필드)
    steps: list[DiagStep] = []
    for st in d.steps:
        if st.kind == "MODEL":
            md = st.detail.model_dump()
            keep = {k: md.get(k) for k in ("input_tokens", "cached_input_tokens", "output_tokens")}
            keep.update({"model": md.get("model"), "success": md.get("success")})
            st = st.model_copy(update={"detail": ModelDetail(**keep)})
        elif st.kind == "TOOL":
            st = st.model_copy(update={"detail": ToolDetail()})
        steps.append(st)
    cur = d.model_copy(update={"steps": steps, "truncated": True})
    if _size(cur) <= max_bytes:
        return cur
    # ② CHECK 는 SUMMARY 만
    kept = [s for s in steps if s.kind != "CHECK" or s.code == "SUMMARY"]
    omitted = len(steps) - len(kept)
    cur = cur.model_copy(
        update={"steps": kept, "end": cur.end.model_copy(update={"omitted_steps": omitted})}
    )
    if _size(cur) <= max_bytes:
        return cur
    # ③ MODEL 과 end 만 (MODEL 은 실행당 8개 이하라 작다)
    models = [s for s in kept if s.kind == "MODEL"]
    omitted = len(steps) - len(models)
    return cur.model_copy(
        update={"steps": models, "end": cur.end.model_copy(update={"omitted_steps": omitted})}
    )


def log_line(line: dict[str, Any]) -> str:
    """로그 줄을 한 줄 JSON 으로(서비스 계층이 표준 출력에 쓴다)."""
    return json.dumps(line, ensure_ascii=False, separators=(",", ":"))
