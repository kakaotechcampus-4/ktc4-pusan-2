"""시험용 가짜 모델과 요청 만들기 — 루프 · 턴 시험이 실제 API 없이 정해 둔 응답을 차례로 받는다.

- `FakeLlm(script)`: `script`의 항목을 호출마다 하나씩 쓴다. 항목은 `LlmResult` · 예외(`LlmError`) · 함수(`LlmRequest → 둘 중 하나`).
  함수 항목은 앞 도구 결과(요청 메시지)를 읽어 근거 id 를 고르는 데 쓴다. 호출마다 `before_attempt`로 한도를 예약한다(실제와 같음).
- `plan()` · `tools()` · `answer()` · `fix()`: 구조화 · 도구 응답 만들기. `tool_results(req)`: 요청 속 도구 결과 JSON 목록.
- `request(**바꿀 것)`: 예시 `request_user_message.json`에서 시작한 요청 dict.
- `load_corpus()`: 코어 패키지의 자료 묶음(검수 전 카드 포함, 연구 단계와 같음).
- `run_turn(req, llm, ...)`: `handle_turn`을 불러 (상태 코드 · 줄 목록 · final 데이터)를 돌려준다. 받은 줄은 `check_contract`로
  계약(줄 종류 5종 · 행동 종류 5종, 새 종류 없음)을 본다 — 턴 시험이 모두 이 함수를 거치므로 모든 턴 시험의 스트림이 이 검사를 받는다
  (묶음 시험: test_turn_contract.py).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pitch_coach_ai.features import chat_agent as _core
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, ChatAgentConfig
from pitch_coach_ai.features.chat_agent.corpus import Corpus, build_corpus
from pitch_coach_ai.features.chat_agent.llm_types import (
    LlmBudgetStop,
    LlmError,
    LlmRequest,
    LlmResult,
    ToolCall,
    Usage,
)
from pitch_coach_ai.features.chat_agent.loop import TurnDeps
from pitch_coach_ai.features.chat_agent.turn import handle_turn

EXAMPLES = Path(_core.__file__).parent / "examples"
SECRET = b"k" * 32
NOW_MS = 1_793_000_000_000

Item = LlmResult | LlmError | Callable[[LlmRequest], "LlmResult | LlmError"]
#: 가짜 응답의 기본 사용량(공급자 실측값 흉내)
DEFAULT_USAGE = Usage(100, 20)
#: 스트림 줄 종류(5-4)와 행동 종류(5-8)의 닫힌 집합. 여기에 없는 종류가 나오면 계약 변경이다(목표 동작: 새 줄 · 행동 종류 없음)
STREAM_EVENTS = frozenset({"plan", "tool_done", "claim", "final", "error"})
ACTION_TYPES = frozenset({"FETCH_PITCH", "FETCH_TAKE", "REASSESS", "SAVE_GOAL", "START_PRACTICE"})


def check_contract(lines: list[dict[str, Any]]) -> None:
    """한 응답의 줄 종류 ⊆ `STREAM_EVENTS`, 끝 줄(final)의 행동 종류 ⊆ `ACTION_TYPES`. 어기면 AssertionError."""
    bad = [ln.get("event") for ln in lines if ln.get("event") not in STREAM_EVENTS]
    if bad:
        raise AssertionError(f"계약에 없는 줄 종류: {bad}")
    last = lines[-1] if lines else {}
    if last.get("event") == "final":
        types = [a.get("type") for a in (last.get("data") or {}).get("actions") or []]
        wrong = [t for t in types if t not in ACTION_TYPES]
        if wrong:
            raise AssertionError(f"계약에 없는 행동 종류: {wrong}")


class FakeLlm:
    model_name: str | None = "fake-model"

    def __init__(
        self,
        script: list[Item],
        *,
        answers: list[Item] | None = None,
        fixes: list[Item] | None = None,
        usage: Usage | None = DEFAULT_USAGE,
        stream_chunk: int | None = 7,
    ) -> None:
        self.script = list(script)
        #: 구조화 응답을 받는 쪽(`text_sink`)에 이 글자 수씩 흘린다(None 이면 흘리지 않음 — 시험이 직접 흘릴 때)
        self.stream_chunk = stream_chunk
        #: 단계별 줄: 주면 답 쓰기(ANSWER) · 고치기(FIX) 호출은 여기서 꺼낸다(조사가 몇 번 돌지 모를 때)
        self.queues: dict[str, list[Item] | None] = {
            "ANSWER": None if answers is None else list(answers),
            "FIX": None if fixes is None else list(fixes),
        }
        self.calls: list[LlmRequest] = []
        self.usage = usage

    def invoke(self, req: LlmRequest, before_attempt: Callable[[int], bool]) -> LlmResult:
        if not before_attempt(1):
            raise LlmBudgetStop(attempts=0)
        self.calls.append(req)
        queue = self.queues.get(req.purpose)
        source = queue if queue is not None else self.script
        if not source:
            raise AssertionError(
                f"가짜 모델 응답이 모자란다(purpose={req.purpose}, 호출 {len(self.calls)})"
            )
        item = source.pop(0)
        out = item(req) if callable(item) and not isinstance(item, (LlmResult, LlmError)) else item
        if isinstance(out, LlmError):
            raise out
        sink = req.text_sink
        if sink is not None and self.stream_chunk and out.message and req.response_schema:
            sink.start(1)
            for i in range(0, len(out.message), self.stream_chunk):
                sink.feed(1, out.message[i : i + self.stream_chunk])
        if self.usage is not None and out.usage == Usage():
            out = LlmResult(
                out.message,
                out.tool_calls,
                out.finish_reason,
                out.model or "fake-model",
                self.usage,
                out.attempts,
                out.parsed,
            )
        return out


def structured(parsed: dict[str, Any]) -> LlmResult:
    return LlmResult(
        message=json.dumps(parsed, ensure_ascii=False), parsed=parsed, model="fake-model"
    )


def plan(
    route: str = "RESEARCH",
    steps: list[str] | None = None,
    assessments: list[dict[str, Any]] | None = None,
    scope_reason: str | None = None,
    talk_kind: str | None = None,
    guide_queries: list[str] | None = None,
    step_tools: list[str | None] | None = None,
) -> LlmResult:
    """계획 호출의 응답. `step_tools`는 단계마다 도구 힌트(`tool`)다. 기본 단계(기록 확인 · 답 쓰기)면 기본 계획과 같은
    힌트(None · answer), 단계를 준 시험은 주지 않으면 모두 None 이다."""
    if step_tools is None:
        step_tools = [None, "answer"] if steps is None else [None] * len(steps)
    steps = steps if steps is not None else ["기록 확인", "답 쓰기"]
    return structured(
        {
            "route": route,
            "talk_kind": talk_kind,
            "scope_reason": scope_reason,
            "plan": [
                {
                    "step": s,
                    "status": "DOING" if i == 0 else "TODO",
                    "note": None,
                    "tool": step_tools[i] if i < len(step_tools) else None,
                }
                for i, s in enumerate(steps)
            ],
            "guide_queries": guide_queries or [],
            "assessments": assessments or [],
        }
    )


def tools(*calls: tuple[str, dict[str, Any]], prefix: str = "call") -> LlmResult:
    return LlmResult(
        message=None,
        tool_calls=[
            ToolCall(id=f"{prefix}-{i}", name=n, args=a, raw_args=json.dumps(a))
            for i, (n, a) in enumerate(calls)
        ],
        finish_reason="tool_calls",
        model="fake-model",
    )


def done() -> LlmResult:
    """조사를 끝낸다(도구 호출 없음)."""
    return LlmResult(message="", tool_calls=[], finish_reason="stop", model="fake-model")


def claim(
    kind: str,
    text: str,
    *,
    evidence_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
    quote: str | None = None,
    about: str | None = None,
    assessment_index: int | None = None,
    comparison_evidence_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "evidence_ids": evidence_ids or [],
        "source_ids": source_ids or [],
        "quote": quote,
        "about": about,
        "assessment_index": assessment_index,
        "comparison_evidence_id": comparison_evidence_id,
    }


def answer(
    claims: list[dict[str, Any]],
    *,
    route: str = "ANSWER",
    assessments: list[dict[str, Any]] | None = None,
    start_practice: dict[str, Any] | None = None,
    scope_reason: str | None = None,
    scope_notes: list[str] | None = None,
    profile_updates: list[dict[str, Any]] | None = None,
    usage_unanswered: bool = False,
) -> LlmResult:
    return structured(
        {
            "route": route,
            "scope_reason": scope_reason,
            "scope_notes": scope_notes or [],
            "assessments": assessments or [],
            "claims": claims,
            "usage_unanswered": usage_unanswered,
            "start_practice": start_practice,
            "profile_updates": profile_updates or [],
        }
    )


def summary(*items: tuple[str, str]) -> LlmResult:
    """대화 요약 호출(HISTORY_SUMMARY)의 응답: (kind, text) 항목들."""
    return structured({"items": [{"kind": k, "text": t} for k, t in items]})


def fix(items: dict[int, str | None]) -> LlmResult:
    return structured({"claims": [{"index": i, "text": t} for i, t in items.items()]})


def draft(
    quote: str,
    *,
    type_: str = "MEASURED",
    claim_op: str | None = "ISSUE",
    dimension: str | None = None,
    metric: str | None = None,
    slide_number: int | None = None,
    take_numbers: list[int] | None = None,
    polarity: str = "NEGATIVE",
    verdict: str = "SUPPORTED",
    value: float | None = None,
    value_unit: str | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "quote": quote,
        "type": type_,
        "speaker": "USER",
        "speaker_label": None,
        "claim_op": claim_op,
        "subject": {
            "dimension": dimension,
            "metric": metric,
            "slide_number": slide_number,
            "take_numbers": take_numbers or [],
            "relation": None,
        },
        "value": value,
        "value_unit": value_unit,
        "polarity": polarity,
        "verdict": verdict,
        "reason": "",
        "source_ids": source_ids or [],
    }


def tool_results(req: LlmRequest) -> list[dict[str, Any]]:
    """요청 속 도구 결과. 조사 단계는 tool 메시지, 답 단계는 `<research>` 블록의 results 다."""
    out = [json.loads(m["content"]) for m in req.messages if m.get("role") == "tool"]
    for m in req.messages:
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, str) and c.startswith("<research>"):
            block = json.loads(c[len("<research>") : c.rindex("</research>")])
            out.extend(block.get("results") or [])
    return out


def find_evidence(req: LlmRequest, pred: Callable[[dict[str, Any]], bool]) -> str:
    """도구 결과 안에서 조건에 맞는 항목의 evidence_id(첫 것)."""

    def walk(o: Any) -> str | None:
        if isinstance(o, dict):
            if "evidence_id" in o and pred(o):
                return o["evidence_id"]
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        return None

    for res in reversed(tool_results(req)):
        r = walk(res)
        if r:
            return r
    raise AssertionError("도구 결과에서 근거를 찾지 못함")


def request(**changes: Any) -> dict[str, Any]:
    """예시 요청에서 시작한다. `user_message="..."`처럼 최상위 키를, `run__trigger=...`처럼 한 단계 아래 키를 바꾼다."""
    req = copy.deepcopy(
        json.loads((EXAMPLES / "request_user_message.json").read_text(encoding="utf-8"))
    )
    for k, v in changes.items():
        if "__" in k:
            a, b = k.split("__", 1)
            req[a][b] = v
        else:
            req[k] = v
    return req


def load_corpus(cfg: ChatAgentConfig = DEFAULT_CONFIG) -> Corpus:
    """코어 패키지의 자료 묶음(`guides/`)을 검수 전 카드까지 읽는다(시험용, 연구 단계와 같은 `allow_unreviewed=True`)."""
    guides = Path(_core.__file__).parent / "guides"
    manifest = json.loads((guides / "manifest.json").read_text(encoding="utf-8"))
    files = {
        name: json.loads((guides / name).read_text(encoding="utf-8"))
        for name in manifest.get("files", [])
    }
    return build_corpus(manifest, files, cfg=cfg, allow_unreviewed=True)


def with_loaded(req: dict[str, Any], *take_ids: str) -> dict[str, Any]:
    """예시 피치(가상 기록)의 Take 결과를 `context.loaded_takes`에 싣는다(BE가 이미 읽어 둔 Take)."""
    from .fixtures import sample_pitch, take_snapshot

    p = sample_pitch(req["run"]["user_id"])
    req["context"]["loaded_takes"] = [take_snapshot(p.take(t)) for t in take_ids]  # type: ignore[arg-type]
    return req


class Clock:
    """단조 시계 · 벽시계. `advance(초)`로 시간을 흘린다."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.ms = NOW_MS

    def monotonic(self) -> float:
        return self.t

    def clock_ms(self) -> int:
        return self.ms

    def advance(self, s: float) -> None:
        self.t += s
        self.ms += int(s * 1000)


def deps(
    llm: Any,
    *,
    corpus: Any = None,
    corpus_version: str = "guides-1",
    cfg: ChatAgentConfig = DEFAULT_CONFIG,
    clock: Clock | None = None,
    log: list[dict[str, Any]] | None = None,
) -> TurnDeps:
    clock = clock or Clock()
    sink = log if log is not None else []
    return TurnDeps(
        llm=llm,
        corpus=corpus,
        corpus_version=corpus_version,
        clock_ms=clock.clock_ms,
        monotonic=clock.monotonic,
        cfg=cfg,
        log=sink.append,
    )


def run_turn(
    req: dict[str, Any], llm: Any, *, secret: bytes | None = SECRET, **dep_kw: Any
) -> tuple[int, list[dict[str, Any]], dict[str, Any] | None]:
    """(HTTP 상태, 줄 목록, final 또는 error 데이터). 스트림 전 오류면 줄 목록은 비고 상태는 오류 코드."""
    # 빈 목록도 그대로 쓴다(`or []` 로 바꾸면 시험이 넘긴 기록 목록이 버려진다)
    sink: list[dict[str, Any]] | None = dep_kw.pop("log", None)
    if sink is None:
        sink = []
    resp = handle_turn(
        json.dumps(req, ensure_ascii=False).encode("utf-8"),
        deps(llm, log=sink, **dep_kw),
        secret=secret,
    )
    if resp.error is not None:
        return resp.error.status, [], resp.error.body()
    assert resp.lines is not None
    lines = [json.loads(x) for x in resp.lines]
    check_contract(lines)
    last = lines[-1]
    for entry in sink:
        if entry.get("event") in ("agent_crash", "agent_final_invalid"):
            print("턴 기록:", entry)  # 시험이 실패하면 pytest 가 보여 준다
    return 200, lines, last["data"]
