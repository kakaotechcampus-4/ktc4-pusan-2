"""대화 에이전트 루프 — 계획 → 조사(도구 · 행동) → 답 쓰기 → 답 검사 · 한 번 고치기 (기능 문서 6-1 · 6-2 · 6-3 · 6-9 · 8-3).

- 누가 쓰나: turn.py 가 요청 검사 · 서명 검사를 마친 뒤 `run()`을 부르고, 진행 이벤트(`emit`)를 스트림 줄로 내보낸다.
- 처리 순서
  1. 처음(USER_MESSAGE)이면 계획 호출(구조화)로 할 일 목록 · 판단 초안을 받는다. 무효면 형식 고치기 1회(조사 몫), 그래도 무효면 기본 계획.
     판단은 근거를 읽을 때까지 내보내지 않는다(실행 상태 안에만 둔다).
  2. 조사: 모델이 도구를 고르면 실행해 결과를 돌려주고, 행동을 고르면 그 지점에서 멈추고 NEEDS_ACTION 을 낸다(한 응답에서 첫 행동까지만).
     한도(8-2: 모델 호출 18번 중 조사는 16번째 시도까지 · 도구 12번 · 시간 · 토큰)에 닿거나, 같은 (도구, 인자, 범위, 자료 판)을 새 근거 없이
     연속 2회, 최근 네 요청이 A-B-A-B로 번갈아 되풀이(교대 반복, 캐시 적중 포함), 무효 도구 응답이 연속 2회면 조사를 끝낸다.
     교대 반복은 요청마다 판정해 그 응답의 남은 요청은 실행하지 않고, 새 근거가 생긴 요청에서 기록을 비운다. 이미 한도로 정해진 끝난 이유는 덮지 않는다.
     끝난 까닭은 `stop_reason`(TIME · MODEL_CALLS · TOOL_CALLS · TOKENS · 반복 계열)에 남기고 turn.py 가 진단 `end.reason`으로 옮긴다.
  2-1. 모델 한 번(`_call`)마다 입력 · 출력 예약을 응답 뒤 공급자 실측으로 정산한다(budget.py `settle_input` · `settle_output`).
  3. 답 쓰기(구조화) → 판단 확정(코드 판정이 모델 판정을 덮음) → 판단 첫 문장 · 결론 · 성공 문장은 코드 템플릿 → 답 검사(6-9).
     걸리면 걸린 문장과 까닭을 알려 한 번 고치게 하고 다시 검사한다. 그래도 걸린 문장은 지운다.
  4. 최종 출력(`final.data`)을 조립한다. `message.text` 는 검사를 통과한 문장을 LF 로 이은 값이다.
- 이어 부르기(ACTION_RESULT): 서명 상태의 루프 데이터를 되살리고, 지워진 원천(`removed_sources`)에 기댄 근거 · 캐시 · 도구 결과를 모델 호출 전에 버린 뒤,
  행동 결과를 검사해 반영하고 2부터 잇는다.
- 자동 목표 확인(TAKE_REVIEWED): 모델 없이 코드가 목표를 판정하고 결과 문장 · 카드를 만든다(meta.model null).
- 고칠 때: 한도 숫자는 config.py, 순서 규칙은 budget.py. 사용자에게 보이는 문구는 templates.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from . import actions as act
from . import checks as chk
from . import claims as clm
from . import prompts, templates
from .budget import Budget, Counters, LimitHit, estimate_tokens, limit_end_reason
from .config import ChatAgentConfig
from .diagnostics import DiagRecorder, code_enum, finish_code
from .evidence import EvidenceRegistry
from .goals import judge_goal
from .llm_types import LlmBudgetStop, LlmError, LlmRefused, LlmRequest, LlmResult, ToolLlm, Usage
from .runctx import RunCtx
from .schemas import (
    Assessment,
    Claim,
    GuideCard,
    PlanStep,
    TraceItem,
    TurnRequest,
)
from .tools import (
    P0_TOOLS,
    ToolResult,
    invalid_result,
    model_tool_definitions,
    result_bytes,
    run_tool,
    validate_args,
)
from .version import FEATURE_VERSION, SCHEMA_VERSION, TOOL_SCHEMA_VERSION

DEFAULT_PLAN = [
    {"step": "기록 확인", "status": "DOING", "note": None},
    {"step": "답 쓰기", "status": "TODO", "note": None},
]


@dataclass
class LoopState:
    """서명 상태(`payload.loop`)로 이어지는 루프 데이터."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    processed_calls: list[str] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    plan_set: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    #: 같은 실행에서 이미 내보낸 판단(그 근거 사본과 함께 최종 답에 남김, 5-5)
    exported: list[dict[str, Any]] = field(default_factory=list)
    exported_sentences: dict[str, str] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None
    #: 이 실행에서 처리한 행동(id · type · 결과 · 성공 문장)
    executed: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    action_seq: int = 0
    repeat_key: str | None = None
    repeat_count: int = 0
    #: 최근 요청 열쇠 네 개(같은 열쇠와 같은 열쇠 규칙, 캐시 적중 포함). 교대 반복(A-B-A-B)을 가린다(5-7)
    recent_keys: list[str] = field(default_factory=list)
    invalid_streak: int = 0
    failed_action_key: str | None = None
    failed_action_count: int = 0
    #: 이 실행이 읽은 원천(피치 · Take) — BE가 재개 때 다시 확인한다(5-6)
    read_sources: list[str] = field(default_factory=list)
    #: 도구 결과 메시지가 기댄 원천(tool_call_id → 원천 id 목록). 원천이 지워지면 그 메시지 내용을 버린다
    message_deps: dict[str, list[str]] = field(default_factory=dict)
    tool_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    usage_in: int = 0
    usage_out: int = 0
    usage_missing: bool = False
    #: 공급자가 준 입력 · 출력 토큰 실측값을 하나라도 받았는가(하나도 없으면 그쪽 usage 토큰은 null, 5-5)
    usage_seen_in: bool = False
    usage_seen_out: bool = False
    #: 다시 평가 결과에서 받은 리뷰 규칙 판(답 meta.criteria_versions 에 싣는다)
    review_versions: dict[str, str] = field(default_factory=dict)
    research_done: bool = False
    #: 크기 · 시간 등으로 조사를 끝낸 까닭(진단 · PARTIAL)
    stop_reason: str | None = None
    corpus_version: str | None = None

    def dump(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def load(cls, d: dict[str, Any]) -> LoopState:
        s = cls()
        for k in cls.__dataclass_fields__:
            if k in d:
                setattr(s, k, d[k])
        return s


@dataclass
class TurnDeps:
    """서비스 계층(연구 모듈은 chat_agent_lab/app.py)이 넘기는 것. 코어는 받은 것만 쓴다."""

    llm: ToolLlm | None
    corpus: Any
    corpus_version: str
    clock_ms: Callable[[], int]
    monotonic: Callable[[], float]
    cfg: ChatAgentConfig
    log: Callable[[dict[str, Any]], None] = lambda _l: None


@dataclass
class LoopOutcome:
    """루프의 끝: final 데이터 또는 error 줄."""

    final: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    #: NEEDS_ACTION 이면 서명할 루프 데이터 · 범위 · 판 번호
    loop_state: LoopState | None = None
    scope_pitch_id: str | None = None
    scope_changed: bool = False
    diag_end: tuple[str, str] = ("DONE", "ANSWERED")


class Loop:
    """한 /agent/turn 호출의 루프."""

    def __init__(
        self,
        req: TurnRequest,
        deps: TurnDeps,
        *,
        state: LoopState,
        original_message: str | None,
        scope_pitch_id: str | None,
        counters: Counters,
        diag: DiagRecorder,
        emit: Callable[[str, dict[str, Any]], None],
        corpus_ok: bool,
    ) -> None:
        self.req = req
        self.deps = deps
        self.cfg = deps.cfg
        self.st = state
        self.original = original_message
        #: 계획 단계에서 모델이 고른 범위 밖 이유(route 가 OUT_OF_SCOPE 일 때만, 1-3). 한 호출 안에서만 쓰고 저장하지 않는다
        self.scope_reason: str | None = None
        self.counters = counters
        self.budget = Budget.start(self.cfg, counters, deps.monotonic)
        self.diag = diag
        self.emit = emit
        registry = (
            EvidenceRegistry.load(req.run.run_id, state.registry)
            if state.registry
            else EvidenceRegistry(req.run.run_id)
        )
        self.ctx = RunCtx.build(
            req,
            cfg=self.cfg,
            registry=registry,
            scope_pitch_id=scope_pitch_id,
            corpus=deps.corpus,
            corpus_version=deps.corpus_version,
            trusted_takes=set(state.read_sources),
        )
        self.ctx.tool_cache = dict(state.tool_cache)
        if not corpus_ok:
            # 실행 중 자료 판이 바뀌면 검색 캐시만 버리고 지금 판으로 다시 찾는다(5-6 · 6-10)
            self.ctx.tool_cache = {
                k: v
                for k, v in self.ctx.tool_cache.items()
                if json.loads(k).get("tool") != "search_guides"
            }
        self.ctx.read_sources = set(state.read_sources)
        #: 이어 부르는 사이 결과 판이 바뀐 Take 가 있었는가(옛 근거는 사본으로 낮췄다, 5-5 · L1-32)
        self.record_changed = self._demote_changed_records() if state.registry else False
        self.scope_changed = False
        self.model_name: str | None = None
        self.model_used = False
        #: 이 호출에서 모델 전송이 한도로 막힌 까닭(진단 끝 사유 TIME · MODEL_CALLS · TOKENS …). 답 단계 실패를 나눌 때 쓴다
        self.limit_end: str | None = None
        #: 연결이 끊겼는가(turn.py 가 넣는다). 끊기면 새 모델 · 도구 호출을 하지 않는다(L1-08)
        self.cancelled: Callable[[], bool] = lambda: False
        self._last_model_seq: int | None = None

    # ── 모델 호출 공통 ───────────────────────────────────────────────
    def _call(
        self,
        purpose: str,
        messages: list[dict[str, Any]],
        *,
        tools=None,
        schema=None,
        max_attempts: int = 2,
        fix_for: list[int] | None = None,
    ) -> LlmResult | LlmError | LimitHit:
        """모델 한 번(내부 재시도 포함). 시도마다 한도를 예약한다. 실패 · 한도는 결과로 돌려준다(예외 없음).

        진단 기록에는 전송 시도마다 MODEL 단계를 남긴다(실패한 앞 시도 포함, 11-5). `fix_for`는 고치기 호출이 고칠
        CHECK 단계의 seq 다.
        """
        kind = "RESEARCH" if purpose == "RESEARCH" else purpose
        est = estimate_tokens(messages, tools)
        max_out = self.cfg.output_tokens_per_call
        hit_holder: list[LimitHit] = []
        reserved = [0]  # 이 호출에서 예약한 전송 시도 수

        def before(_n: int) -> bool:
            if self.cancelled():
                return False  # 연결이 끊기면 재시도도 보내지 않는다(L1-08)
            hit = self.budget.reserve_model(kind, est, max_out)  # type: ignore[arg-type]
            if hit is not None:
                hit_holder.append(hit)
                return False
            reserved[0] += 1
            return True

        llm = self.deps.llm
        if llm is None:
            return LlmError(cause="no_llm")
        if self.cancelled():
            return LlmError(cause="client_disconnected")
        timeout_left = self.budget.call_timeout(kind)  # type: ignore[arg-type]
        deadline = (
            self.deps.monotonic() + timeout_left if purpose == "RESEARCH" else self.budget.deadline
        )
        req = LlmRequest(
            purpose=purpose,  # type: ignore[arg-type]
            messages=messages,
            tools=tools,
            response_schema=schema,
            max_output_tokens=max_out,
            deadline=deadline,
            max_attempts=max_attempts,
        )
        start = self.diag.now_ms()
        try:
            res = llm.invoke(req, before)
        except LlmBudgetStop as e:
            self._count_usage(e.usage)
            self._failed_steps(
                purpose, llm.model_name, e.usage, [e.code] * len(e.usage), start, fix_for
            )
            if hit_holder:
                self._limit(hit_holder[-1])
                self.limit_end = limit_end_reason(hit_holder[-1].code)
                return hit_holder[-1]
            return e
        except LlmError as e:
            usages = e.usage or [Usage()]  # 보냈지만 사용량을 모르는 시도도 미측정으로 센다(L1-39)
            self._count_usage(usages)
            self._failed_steps(
                purpose, llm.model_name, usages, [e.code] * len(usages), start, fix_for
            )
            return e
        self.model_used = True
        self.model_name = res.model or llm.model_name or self.model_name
        usages = list(res.attempt_usages) or [res.usage]
        self._count_usage(usages)
        # 응답을 받은 시도(마지막)의 입력 · 출력 예약을 실측으로 바꾼다. 실패한 앞 시도 · 실측이 없는 시도는 예약 그대로(8-3)
        if reserved[0]:
            self.budget.settle_input(est, res.usage.input_tokens)
            self.budget.settle_output(max_out, res.usage.output_tokens)
        # 실패한 앞 시도(재시도 전)도 진단 단계로 남긴다
        self._failed_steps(
            purpose, llm.model_name, usages[:-1], list(res.attempt_errors), start, fix_for
        )
        u = res.usage
        detail: dict[str, Any] = {
            "model": res.model,
            "success": True,
            "input_tokens": u.input_tokens,
            "cached_input_tokens": u.cached_input_tokens,
            "output_tokens": u.output_tokens,
            "finish_reason": finish_code(res.finish_reason),
            "tool_call_count": len(res.tool_calls),
        }
        if fix_for:
            detail["fix_for"] = list(fix_for)
        self._last_model_seq = self.diag.step(
            "MODEL", purpose, detail, start_ms=start, ms=max(0, self.diag.now_ms() - start)
        )
        return res

    def _failed_steps(
        self,
        purpose: str,
        model: str | None,
        usages: list[Usage],
        codes: list[str],
        start: int,
        fix_for: list[int] | None,
    ) -> None:
        for i, u in enumerate(usages):
            detail: dict[str, Any] = {
                "model": model,
                "success": False,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "finish_reason": None,
                "error_code": code_enum(codes[i] if i < len(codes) else "LLM_ERROR"),
                "tool_call_count": 0,
            }
            if fix_for:
                detail["fix_for"] = list(fix_for)
            self.diag.step(
                "MODEL", purpose, detail, start_ms=start, ms=max(0, self.diag.now_ms() - start)
            )

    def _count_usage(self, usages: list[Usage]) -> None:
        """관측 사용량(5-5): 실측값만 더하고, 입력 · 출력 각각 실측이 하나라도 있었는지와 빠진 시도가 있었는지를 따로 센다."""
        for u in usages:
            if u.input_tokens is None or u.output_tokens is None:
                self.st.usage_missing = True
            if u.input_tokens is not None:
                self.st.usage_seen_in = True
                self.st.usage_in += u.input_tokens
            if u.output_tokens is not None:
                self.st.usage_seen_out = True
                self.st.usage_out += u.output_tokens

    def _limit(self, hit: LimitHit) -> None:
        self.diag.step(
            "LIMIT", hit.code, {"used": hit.used, "limit": hit.limit, "attempted": hit.attempted}
        )

    # ── 모델이 보는 데이터 ───────────────────────────────────────────
    def data_block(self) -> dict[str, Any]:
        """외부 문자열을 모은 데이터 블록(6-1 · 6-10). 키 · 서명 상태 · 내부 판단 키는 넣지 않는다(L1-22)."""
        ctx, req = self.ctx, self.req
        pitch = ctx.pitch
        takes = []
        for t in sorted(ctx.summaries.values(), key=lambda x: x.take_number, reverse=True):
            takes.append(
                {
                    "take_id": t.take_id,
                    "take_number": t.take_number,
                    "status": t.status,
                    "completed_fully": t.completed_fully,
                    "mode": t.mode,
                    "script_mode": t.script_mode,
                    "script_version_id": t.script_version_id,
                    "review_score": t.review.score if t.review else None,
                    "priority_dimensions": [
                        d
                        for d, s in (t.review.dimensions.items() if t.review else [])
                        if s == "PRIORITY"
                    ],
                    "loaded": t.take_id in ctx.snapshots,
                }
            )
        focus = (
            pitch.focus_take.summary.take_id
            if pitch is not None and pitch.focus_take is not None
            else None
        )
        hist = req.history
        messages = [
            {"role": m.role, "text": m.text[:600], "stale": m.stale_reasons or None}
            for m in hist.messages[-6:]
        ]
        prior = [
            {"quote": a.quote, "type": a.type, "verdict": a.verdict}
            for a in hist.prior_assessments[-10:]
        ]
        screen = req.page.screen
        goal = ctx.goal
        return {
            "user_message": self.original,
            "trigger": req.run.trigger,
            "page": {
                "route": req.page.route,
                "take_id": req.page.take_id,
                "screen": None
                if screen is None
                else {"name": screen.name, "step": getattr(screen, "step", None)},
            },
            "scope": (
                {
                    "pitch_id": pitch.info.pitch_id,
                    "title": pitch.info.title,
                    "script_version": pitch.versions.script_version_label,
                }
                if pitch is not None
                else "ALL"
            ),
            "pitches": [
                {
                    "pitch_id": p.pitch_id,
                    "title": p.title,
                    "take_count": p.take_count,
                    "last_score": p.last_review.score if p.last_review else None,
                    "priority_dimensions": p.last_review.priority_dimensions
                    if p.last_review
                    else [],
                }
                for p in ctx.pitches
            ],
            "pitches_total": ctx.pitches_total,
            "takes": takes,
            "focus_take_id": focus,
            "conversation_goal": (
                {
                    "metric": goal.metric,
                    "slide_number": goal.slide_number,
                    "operator": goal.operator,
                    "base_take_number": goal.base.take_number,
                    "status": goal.status,
                }
                if goal is not None
                else None
            ),
            "history": {"summary": (hist.summary or "")[:600] or None, "messages": messages},
            "prior_assessments": prior,
            "actions_done": [{"type": e["type"], "status": e["status"]} for e in self.st.executed],
        }

    def _system(self, phase: str) -> dict[str, str]:
        return {"role": "system", "content": prompts.SYSTEM + "\n\n" + phase}

    # ── 1. 계획 ─────────────────────────────────────────────────────
    def plan_phase(self) -> str:
        """계획 · 판단 초안. 돌려주는 값: route(RESEARCH · ANSWER_DIRECT · OUT_OF_SCOPE). OUT_OF_SCOPE 면 `scope_reason`도 남는다."""
        msgs = [self._system(prompts.PLAN), prompts.data_message(self.data_block())]
        res = self._call("RESEARCH", msgs, schema=prompts.PLAN_SCHEMA)
        if not isinstance(res, LlmResult) or res.parsed is None:
            self._set_plan(DEFAULT_PLAN, edit=False)
            return "RESEARCH"
        parsed = res.parsed
        route = (
            parsed.get("route")
            if parsed.get("route") in ("RESEARCH", "ANSWER_DIRECT", "OUT_OF_SCOPE")
            else "RESEARCH"
        )
        if route == "OUT_OF_SCOPE":
            self.scope_reason = parsed.get("scope_reason")
        drafts = self._parse_drafts(parsed.get("assessments") or [])
        valid, problems = clm.split_check(self.original or "", drafts, self.cfg)
        plan_ok = self._plan_valid(parsed.get("plan") or [], allow_empty=route != "RESEARCH")
        if (problems or not plan_ok) and route != "OUT_OF_SCOPE":
            # 형식 고치기 1회(조사 몫, 6-1). 여러 형식 오류는 한 번에 묶는다
            note = {
                "problems": [p.code if hasattr(p, "code") else str(p) for p in problems][:10],
                "plan_invalid": not plan_ok,
            }
            fix_msgs = msgs + [
                {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": "<format_problems>"
                    + json.dumps(note, ensure_ascii=False)
                    + "</format_problems>\n판단마다 quote 는 사용자 말 원문 그대로 한 절만, 측정 항목을 말한 절마다 판단 하나, 할 일 목록은 1~8단계로 다시 내라.",
                },
            ]
            res2 = self._call("RESEARCH", fix_msgs, schema=prompts.PLAN_SCHEMA)
            if isinstance(res2, LlmResult) and res2.parsed is not None:
                parsed = res2.parsed
                drafts = self._parse_drafts(parsed.get("assessments") or [])
                valid, problems = clm.split_check(self.original or "", drafts, self.cfg)
                plan_ok = self._plan_valid(
                    parsed.get("plan") or [], allow_empty=route != "RESEARCH"
                )
        self.st.drafts = [d.model_dump(mode="json") for d in drafts]
        if route == "RESEARCH":
            self._set_plan(
                parsed.get("plan") if plan_ok and parsed.get("plan") else DEFAULT_PLAN, edit=False
            )
        elif plan_ok and parsed.get("plan"):
            self._set_plan(parsed["plan"], edit=False)
        return route

    def _parse_drafts(self, raw: list[Any]) -> list[clm.AssessmentDraft]:
        out = []
        for r in raw:
            try:
                out.append(clm.AssessmentDraft.model_validate(r))
            except ValidationError:
                continue
        return out

    def _plan_valid(self, plan: list[Any], *, allow_empty: bool) -> bool:
        if not plan:
            return allow_empty
        if not (self.cfg.plan_steps_min <= len(plan) <= self.cfg.plan_steps_max):
            return False
        try:
            for s in plan:
                PlanStep.model_validate(s)
        except ValidationError:
            return False
        return True

    def _set_plan(self, plan: list[dict[str, Any]], *, edit: bool) -> bool:
        if edit and self.st.plan_set:
            hit = self.budget.reserve_plan_edit()
            if hit is not None:
                self._limit(hit)
                return False
        steps = [PlanStep.model_validate(s).model_dump() for s in plan]
        # 할 일 목록도 화면에 보이는 문자열이다: 근거 없는 숫자 · 금지 주장 · 개인정보가 있으면 쓰지 않는다(6-9 · L1-37)
        if chk.check_display_strings(
            plan=steps,
            registry=self.ctx.registry,
            display_evidence=None,
            cfg=self.cfg,
            assessments={},
        ):
            if self.st.plan_set:
                return False
            steps = [PlanStep.model_validate(s).model_dump() for s in DEFAULT_PLAN]
        self.st.plan = steps
        self.st.plan_set = True
        self.emit("plan", {"plan": self.st.plan})
        return True

    # ── 2. 조사 ─────────────────────────────────────────────────────
    def research_phase(self) -> dict[str, Any] | None:
        """도구 · 행동 루프. 행동을 내면 {'action': Action, 'card': …}, 조사가 끝나면 None."""
        tools = model_tool_definitions() + act.action_tool_definitions()
        while not self.st.research_done:
            if self.cancelled():
                self.st.research_done, self.st.stop_reason = True, "CLIENT_DISCONNECTED"
                break
            msgs = [
                self._system(prompts.RESEARCH),
                prompts.data_message(self.data_block()),
                *self.st.messages,
            ]
            res = self._call("RESEARCH", msgs, tools=tools)
            if isinstance(res, LimitHit):
                self.st.research_done, self.st.stop_reason = True, limit_end_reason(res.code)
                break
            if isinstance(res, LlmError):
                self.st.research_done = True
                self.st.stop_reason = "LLM_REFUSED" if isinstance(res, LlmRefused) else "LLM_ERROR"
                break
            calls = list(res.tool_calls)
            if not calls:
                self.st.research_done = True
                break
            model_seq = self._last_model_seq
            if len(calls) > self.cfg.max_tools_per_response:
                self._limit(
                    LimitHit("TOOLS_PER_RESPONSE", len(calls), self.cfg.max_tools_per_response)
                )
            assistant = {"role": "assistant", "content": res.message or "", "tool_calls": []}
            results: list[dict[str, Any]] = []
            stop_action: dict[str, Any] | None = None
            any_valid = False
            progressed = False
            #: 교대 반복(A-B-A-B)이 이 응답의 어느 요청에서 확인되면 남은 요청은 실행하지 않는다(5-7)
            alt_stop = False
            for i, call in enumerate(calls):
                if self.cancelled():
                    break  # 연결이 끊기면 같은 응답의 남은 도구 · 행동도 실행하지 않는다(L1-08)
                if call.id in self.st.processed_calls:
                    continue  # 이미 처리한 호출은 다시 실행하지 않는다(6-1 4)
                assistant["tool_calls"].append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.raw_args
                            or json.dumps(call.args or {}, ensure_ascii=False),
                        },
                    }
                )
                self.st.processed_calls.append(call.id)
                #: 같은 응답 안에서 한도로 조사가 끝났으면 남은 요청도 실행하지 않는다(6-1 · 8-2)
                if (
                    stop_action is not None
                    or alt_stop
                    or self.st.research_done
                    or i >= self.cfg.max_tools_per_response
                ):
                    results.append(
                        self._tool_msg(
                            call.id,
                            {
                                "ok": False,
                                "error": {
                                    "code": "TOOL_SKIPPED",
                                    "message": "앞 요청에서 멈춰 실행하지 않았다",
                                },
                            },
                        )
                    )
                    continue
                if call.name in act.ACTION_TOOL_ARGS:
                    out = self._handle_action(call, model_seq)
                    any_valid = any_valid or out.get("valid", False)
                    results.append(self._tool_msg(call.id, out["message"]))
                    if out.get("action") is not None:
                        stop_action = {
                            "action": out["action"],
                            "call_id": call.id,
                            "tool_name": call.name,
                            "tool_args": call.args,
                        }
                    if out.get("progressed", False):
                        progressed = True
                        self.st.recent_keys = []  # 새 근거가 생긴 요청에서 반복 기록을 비운다(5-7)
                    continue
                args = validate_args(call.name, call.args)
                if isinstance(args, str):
                    tr = invalid_result(call.name, args)
                    results.append(self._tool_msg(call.id, tr.model_view()))
                    self._trace_and_diag(tr, model_seq, call_ok=False)
                    continue
                any_valid = True
                key = json.dumps(
                    [call.name, args.model_dump(mode="json"), self.ctx.scope_pitch_id],
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if key == self.st.repeat_key:
                    self.st.repeat_count += 1
                else:
                    self.st.repeat_key, self.st.repeat_count = key, 1
                self.st.recent_keys = [*self.st.recent_keys, key][-4:]
                request_progressed = False
                cached = call.name != "update_plan" and self._is_cached(call.name, args)
                if not cached:
                    hit = self.budget.reserve_tool()
                    if hit is not None:
                        self._limit(hit)
                        results.append(
                            self._tool_msg(
                                call.id,
                                {
                                    "ok": False,
                                    "error": {"code": "LIMIT", "message": "도구 호출 한도"},
                                },
                            )
                        )
                        self.st.research_done, self.st.stop_reason = True, "TOOL_CALLS"
                        continue
                if call.name == "update_plan":
                    plan = [s.model_dump() for s in args.steps]  # type: ignore[attr-defined]
                    ok = self._set_plan(plan, edit=True)
                    tr, trace = run_tool(
                        self.ctx,
                        call.name,
                        args,
                        seq=len(self.st.trace) + 1,
                        clock=self.deps.monotonic,
                    )
                    if not ok:
                        tr.ok, tr.error = (
                            False,
                            {"code": "LIMIT", "message": "할 일 목록 고치기 한도"},
                        )
                else:
                    tr, trace = run_tool(
                        self.ctx,
                        call.name,
                        args,
                        seq=len(self.st.trace) + 1,
                        clock=self.deps.monotonic,
                    )
                    if tr.outcome != "CACHED" and tr.ok:
                        progressed = True
                        request_progressed = True
                self.st.trace.append(trace.model_dump())
                self.emit("tool_done", {"trace": trace.model_dump()})
                self._tool_diag(tr, trace, model_seq)
                self.st.message_deps[call.id] = list(tr.target_ids) + (
                    [self.ctx.scope_pitch_id] if self.ctx.scope_pitch_id else []
                )
                results.append(self._tool_msg(call.id, tr.model_view()))
                # 교대 반복은 요청마다 본다: 새 근거가 생기면 기록을 비우고 다음 요청부터 다시 센다
                if request_progressed:
                    self.st.recent_keys = []
                elif _alternating(self.st.recent_keys):
                    alt_stop = True
                    if not self.st.research_done:  # 이미 한도로 끝난 이유는 덮지 않는다
                        self.st.research_done, self.st.stop_reason = True, "ALTERNATING"
            self.st.messages.append(assistant)
            self.st.messages.extend(results)
            if stop_action is not None:
                return stop_action
            if not any_valid:
                self.st.invalid_streak += 1
                if self.st.invalid_streak >= self.cfg.invalid_stop and not self.st.research_done:
                    self.st.research_done, self.st.stop_reason = True, "INVALID_TOOLS"
            else:
                self.st.invalid_streak = 0
            if (
                self.st.repeat_count >= self.cfg.repeat_stop
                and not progressed
                and not self.st.research_done
            ):
                self.st.research_done, self.st.stop_reason = True, "NO_PROGRESS"
        return None

    def _is_cached(self, name: str, args: Any) -> bool:
        from .tools import cache_key

        return cache_key(self.ctx, name, args) in self.ctx.tool_cache

    def _tool_msg(self, call_id: str, content: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
        }

    def _trace_and_diag(self, tr: ToolResult, model_seq: int | None, *, call_ok: bool) -> None:
        trace = TraceItem(
            seq=len(self.st.trace) + 1,
            tool=tr.name if tr.name in P0_TOOLS else "invalid",
            args_summary=tr.args_summary or "무효 호출",
            result_summary=tr.result_summary or "실행하지 않음",
            ms=0,
            outcome="ERROR",
        )
        self.st.trace.append(trace.model_dump())
        self.emit("tool_done", {"trace": trace.model_dump()})

    def _tool_diag(self, tr: ToolResult, trace: TraceItem, model_seq: int | None) -> None:
        if tr.name not in P0_TOOLS:
            return
        self.diag.step(
            "TOOL",
            tr.name,
            {
                "target_ids": tr.target_ids or None,
                "revisions": tr.revisions or None,
                "outcome": trace.outcome,
                "result_bytes": result_bytes(tr),
                "query_chars": None,
                "error_code": code_enum(tr.error["code"]) if tr.error else None,
            },
            ms=trace.ms,
            parent_seq=model_seq,
        )

    def _handle_action(self, call: Any, model_seq: int | None) -> dict[str, Any]:
        """행동 도구 하나를 처리한다. 같은 실패 행동 · 인자가 연속 2회면 더 실행하지 않는다(5-8 · L2-31)."""
        key = _action_key(call.name, call.args)
        if key == self.st.failed_action_key and self.st.failed_action_count >= 1:
            self.st.failed_action_count += 1
            self.st.research_done, self.st.stop_reason = True, "REPEATED_FAILED_ACTION"
            return {
                "message": {
                    "ok": False,
                    "error": {
                        "code": "REPEATED",
                        "message": "같은 실패 행동은 다시 실행하지 않는다",
                    },
                }
            }
        self.st.action_seq += 1
        p = act.propose(
            call.name,
            call.args,
            ctx=self.ctx,
            budget=self.budget,
            seq=self.st.action_seq,
            original_message=self.original,
            trigger=self.req.run.trigger,
        )
        atype = act.ACTION_TOOL_TYPE.get(call.name, "FETCH_PITCH")
        if p.result == "TOOL_ERROR":
            return {"message": p.tool_message, "valid": False}
        self.diag.step(
            "ACTION",
            atype,
            {
                "action_id": p.action.action_id if p.action else None,
                "autonomy": act.AUTONOMY.get(atype),
                "authorization": p.authorization,
                "result": p.result,
                "reason_code": code_enum(p.reason_code),
            },
            parent_seq=model_seq,
        )
        if p.result == "PROPOSED" and p.action is not None:
            return {
                "message": {"ok": True, "data": {"requested": atype}},
                "action": p.action,
                "valid": True,
            }
        if p.card is not None:
            self.st.cards.append(p.card)
        return {"message": p.tool_message, "valid": True, "progressed": p.card is not None}

    def _demote_changed_records(self) -> bool:
        """이어 부를 때 Take 결과 판이 서명 때와 다르면 그 Take 의 옛 근거를 사본으로 낮춘다(새 문장의 근거로 못 씀)."""
        reg = self.ctx.registry
        changed = False
        for eid, ver in list(reg.versions.items()):
            snap = self.ctx.snapshots.get(ver.take_id)
            if snap is None:
                continue
            now = {k: v for k, v in snap.revisions.model_dump().items() if v is not None}
            if now != dict(ver.results):
                stale = {eid}
                grew = True
                while grew:  # 그 근거를 입력으로 쓴 계산 근거(그 계산을 입력으로 쓴 것 포함)까지
                    more = {i for i, e in reg.items.items() if set(e.inputs or []) & stale} - stale
                    grew = bool(more)
                    stale |= more
                reg.keep_as_copy(sorted(stale))
                changed = True
        return changed

    # ── 이어 부르기 · 지워진 원천 ─────────────────────────────────────
    def drop_removed(self, removed: list[str]) -> bool:
        """지워진 원천에 기댄 근거 · 캐시 · 도구 결과 메시지를 버린다(5-6 · L1-32 · L4-38). 떼어 낼 수 없으면 False."""
        if not removed:
            return True
        rs = set(removed)
        self.ctx.registry.drop_sources(rs)
        self.ctx.tool_cache = {
            k: v for k, v in self.ctx.tool_cache.items() if not (set(v.get("target_ids", [])) & rs)
        }
        for m in self.st.messages:
            if (
                m.get("role") == "tool"
                and set(self.st.message_deps.get(m.get("tool_call_id", ""), [])) & rs
            ):
                # 구조화된 근거만 버리면 이전 도구 메시지에 지운 수치가 남는다 → 내용 자체를 버린다
                m["content"] = json.dumps(
                    {
                        "ok": False,
                        "read_state": "NOT_FOUND",
                        "error": {
                            "code": "SOURCE_DELETED",
                            "message": "근거 기록이 지워져 결과를 버렸다",
                        },
                    },
                    ensure_ascii=False,
                )
            if m.get("role") == "assistant":
                m["content"] = ""  # 모델의 중간 글에도 지운 수치가 있을 수 있다
        for idx, m in enumerate(self.st.messages):
            # 행동 결과 메시지(가져온 Take · 다시 평가 결과)도 그 행동의 대상에 기대어 있다
            if m.get("role") == "user" and set(self.st.message_deps.get(f"msg:{idx}", [])) & rs:
                m["content"] = (
                    "<action_result>"
                    + json.dumps(
                        {
                            "ok": False,
                            "error": {
                                "code": "SOURCE_DELETED",
                                "message": "근거 기록이 지워져 결과를 버렸다",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "</action_result>"
                )
        self.st.cards = [
            c
            for c in self.st.cards
            if c.get("take_id") not in rs
            and (c.get("goal") or {}).get("base", {}).get("take_id") not in rs
        ]
        self.st.exported = [a for a in self.st.exported if not set(_assessment_sources(a)) & rs]
        pending = self.st.pending_action
        if pending is not None and set(_action_targets(pending)) & rs:
            return False
        self.ctx.read_sources -= rs
        return True

    def apply_action_result(self, result: Any) -> act.Applied:
        pending = self.st.pending_action
        assert pending is not None
        applied = act.apply_result(pending, result, ctx=self.ctx)
        call_id = pending.get("_call_id")
        content = applied.tool_message
        self.st.messages.append(self._tool_msg_replace(call_id, content))
        self.st.message_deps[f"msg:{len(self.st.messages) - 1}"] = _action_targets(pending)
        rec = {
            "action_id": pending["action_id"],
            "type": pending["type"],
            "status": "SUCCEEDED" if applied.ok else ("MISMATCH" if applied.mismatch else "FAILED"),
            "success_text": applied.success_text,
            "reason_code": applied.reason_code,
            "failed_code": applied.failed_code,
            "official_differs": applied.official_differs,
            # 대본 허용으로 다시 평가해 점수가 올랐으면 "평가 항목이 빠져 오른 점수예요"를 붙인다(7-10)
            "script_points_removed": _script_points_removed(applied.card),
        }
        if applied.review_versions:
            self.st.review_versions.update(applied.review_versions)
        self.st.executed.append(rec)
        if applied.card is not None:
            self.st.cards.append(applied.card)
        if applied.scope_changed:
            self.scope_changed = True
        if applied.ok:
            # 다른 행동이 성공하면 '연속' 실패가 끊긴다(5-8: 같은 실패 행동 · 인자가 연속 2회면 멈춤)
            self.st.failed_action_key, self.st.failed_action_count = None, 0
        if not applied.ok and not applied.mismatch:
            key = _action_key(pending.get("_tool_name"), pending.get("_tool_args"))
            if key == self.st.failed_action_key:
                self.st.failed_action_count += 1
            else:
                self.st.failed_action_key, self.st.failed_action_count = key, 1
        self.st.pending_action = None
        return applied

    def _tool_msg_replace(self, call_id: str | None, content: dict[str, Any]) -> dict[str, Any]:
        """행동 도구 호출에 대한 결과 메시지. 멈출 때 넣어 둔 '요청함' 메시지 뒤에 실제 결과를 덧붙인다."""
        return {
            "role": "user",
            "content": "<action_result>"
            + json.dumps({"tool_call_id": call_id, **content}, ensure_ascii=False)
            + "</action_result>",
        }

    # ── 3. 답 쓰기 · 검사 ────────────────────────────────────────────
    def answer_phase(self, *, route: str) -> dict[str, Any]:
        """답 초안 → 판단 확정 → 템플릿 문장 → 답 검사 → 한 번 고치기. 돌려주는 값: 메시지 · 판단 · 카드 · 상태."""
        research = [
            {"tool": t["tool"], "args": t["args_summary"], "result": t["result_summary"]}
            for t in self.st.trace
        ]
        tool_views = [json.loads(m["content"]) for m in self.st.messages if m.get("role") == "tool"]
        action_views = [m["content"] for m in self.st.messages if m.get("role") == "user"]
        research_block = {
            "trace": research,
            "results": tool_views[-20:],
            "actions": action_views[-5:],
            "drafts": self.st.drafts,
        }
        msgs = [
            self._system(prompts.ANSWER),
            prompts.data_message(self.data_block()),
            {
                "role": "user",
                "content": "<research>\n"
                + json.dumps(research_block, ensure_ascii=False, separators=(",", ":"))
                + "\n</research>",
            },
        ]
        res = self._call("ANSWER", msgs, schema=prompts.ANSWER_SCHEMA)
        if not isinstance(res, LlmResult) or res.parsed is None:
            refused = isinstance(res, LlmRefused)
            return {
                "failed": True,
                "refused": refused,
                "llm_error": isinstance(res, LlmError) and not refused,
            }
        return {"failed": False, "draft": res.parsed, "messages": msgs}

    def fix_call(
        self,
        msgs: list[dict[str, Any]],
        draft_claims: list[dict[str, Any]],
        failures: list[Any],
        fix_for: list[int] | None = None,
    ) -> dict[int, str | None] | None:
        """걸린 문장만 한 번 고치게 한다(6-9). 실패하면 None(원본에서 통과한 문장만 남긴다)."""
        items = [
            {
                "index": f.claim_index,
                "text": draft_claims[f.claim_index]["text"],
                "check": f.check_id,
                "reason": f.reason,
            }
            for f in failures
            if f.claim_index is not None and f.claim_index < len(draft_claims)
        ]
        if not items:
            return None
        fix_msgs = [
            self._system(prompts.FIX),
            *msgs[1:],
            {
                "role": "user",
                "content": "<fix>\n" + json.dumps(items, ensure_ascii=False) + "\n</fix>",
            },
        ]
        res = self._call(
            "FIX", fix_msgs, schema=prompts.FIX_SCHEMA, max_attempts=1, fix_for=fix_for
        )
        if not isinstance(res, LlmResult) or res.parsed is None:
            return None
        out: dict[int, str | None] = {}
        for c in res.parsed.get("claims") or []:
            if isinstance(c, dict) and isinstance(c.get("index"), int):
                t = c.get("text")
                out[c["index"]] = t if isinstance(t, str) and t.strip() else None
        return out


def _script_points_removed(card: dict[str, Any] | None) -> bool:
    if not card or card.get("type") != "REASSESS_RESULT":
        return False
    b, a = card.get("before") or {}, card.get("after") or {}
    before, after = b.get("score"), a.get("score")
    # 원인을 확인한다: 대본 허용으로 B 에서 대본 차원이 '평가하지 않음'으로 빠지고 점수가 올랐을 때만
    dim_b = (b.get("dimensions") or {}).get("script_independence")
    dim_a = (a.get("dimensions") or {}).get("script_independence")
    return (
        (card.get("change") or {}).get("script_used") is True
        and dim_a == "NOT_EVALUABLE"
        and dim_b != "NOT_EVALUABLE"
        and isinstance(before, int)
        and isinstance(after, int)
        and after > before
    )


def _alternating(keys: list[str]) -> bool:
    """최근 네 요청이 A-B-A-B(첫째 = 셋째, 둘째 = 넷째, 두 요청은 서로 다름)인가(5-7 '호출 제한')."""
    if len(keys) < 4:
        return False
    a, b, c, d = keys[-4:]
    return a == c and b == d and a != b


def _action_key(name: Any, args: Any) -> str:
    """같은 실패 행동인지 가리는 열쇠: 행동 이름과 인자(모델이 쓰는 설명 reason 은 뺀다, 5-8 · L2-31)."""
    a = {k: v for k, v in args.items() if k != "reason"} if isinstance(args, dict) else args
    return json.dumps([name, a], ensure_ascii=False, sort_keys=True, default=str)


def _assessment_sources(a: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rec in (a.get("evidence_basis") or {}).get("records", []):
        out.append(rec.get("pitch_id"))
        out.extend(t.get("take_id") for t in rec.get("takes", []))
    return [x for x in out if x]


def _action_targets(action: dict[str, Any]) -> list[str]:
    args = action.get("args") or {}
    out = [args.get("pitch_id"), args.get("take_id")]
    goal = args.get("goal") or {}
    if isinstance(goal, dict):
        out.append((goal.get("base") or {}).get("take_id"))
    return [x for x in out if x]


def meta(
    *,
    model: str | None,
    prompt_version: str,
    corpus_version: str,
    cfg: ChatAgentConfig,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    cv = {"chat_agent": cfg.criteria_version()}
    cv.update(extra or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model": model,
        "prompt_version": prompt_version,
        "corpus_version": corpus_version,
        "criteria_versions": cv,
    }


__all__ = [
    "Loop",
    "LoopState",
    "LoopOutcome",
    "TurnDeps",
    "meta",
    "DEFAULT_PLAN",
    "Assessment",
    "Claim",
    "GuideCard",
    "judge_goal",
    "chk",
    "templates",
    "TOOL_SCHEMA_VERSION",
]
