"""할 일 단계 = 실제 실행 · 상황 신호 · 결정 로그 (기능 문서 5-2 `PlanStep` · 6-1 7번 · 11-5).

- 누가 쓰나: loop.py 가 계획을 정하거나 모델이 고칠 때(`split_steps` → `new_meta`), 도구 하나가 끝날 때(`read_run` → `attach` →
  `render`), 가져오기 행동의 결과를 받을 때(`fetch_run`), 조사가 한도로 멈출 때(`mark_short`) 부르고, turn.py 가 끝 줄을 만들 때
  (`finish_plan`) 부른다. 모델 호출은 없다.
- 단계 메타(`LoopState.plan_meta`, 서명 상태로 이어진다. 옛 상태는 빈 목록): 단계마다 `{purpose, tool, note, done, failed, skipped,
  exec, plain, result, evidence_ids, tool_seqs, ran, take}`. `purpose`는 모델이 쓴 단계 글(목적), `tool`은 계획이 단 도구
  힌트(`PlanStepDraft`, 응답 `PlanStep`에는 없음), 나머지는 실행이 채운다.
- 표시 글(`render`, `PlanStep.step` 60자): 실행 전 = 목적, 실행 뒤 = `목적: 실행 → 결과`(넘으면 목적을 줄여 "…", 그래도 넘으면
  `실행 → 결과`). 실행 · 결과 글은 templates.py `PLAN_*` 표로만 만들고 모델 글은 넣지 않는다. 숫자 · Take · 장 번호는 그 단계가 읽은
  근거로만 — 표시 문자열 검사(6-9, `display_evidence={"plan:0": 그 단계 근거}`)에 걸리면 숫자 없는 실행 이름으로, 그래도 걸리면 목적만.
- 단계 고르기(`attach`, 도구 하나가 끝날 때 · 캐시 적중 포함): 도구 힌트가 같은 첫 미완 단계 → 힌트 없는 지금(DOING) 단계 → 없으면
  실행 뒤에 새 단계(목적 = 도구 종류 이름)를 남은 단계 앞에 끼운다(8단계까지, 차 있으면 trace 에만 남는다). `update_plan`은 고르지 않는다.
- 상태: 끝낸 단계 DONE(도구가 OK · EMPTY · CACHED 이거나 사실 결과 — 비교 불가 · 리뷰 없음 · 기본 자료에 없음 · 기록 없음 — 를 냄),
  첫 미완 단계 DOING, 나머지 TODO, 모델이 건너뛴 단계 SKIPPED. 도구 실패(ERROR)는 미완 + `확인 실패`.
  끝 처리(`finish_plan`): DONE · REFUSED 면 미완 단계 SKIPPED, PARTIAL 이면 TODO(DOING 포함), NEEDS_ACTION 은 그대로.
  답 쓰기 단계(`tool == "answer"`)는 답 문장이 하나 이상이면 DONE.
- 끝 상태(`end_status`, TARGET 6): 조사가 한도 · 장애로 끊기지 않았고 풀리지 않은 단계(`unresolved_steps`: 시간 부족 · 복구되지 않은
  확인 실패)가 없고 질문 대상마다 답 문장(확인한 답 · 확인한 '없음' · 비교할 수 없는 까닭)이 있으면 DONE, 아니면 PARTIAL.
- 상황 신호(`StepRun.signal`): 비교 불가 `NOT_COMPARABLE` · 기본 자료에 없음 `NOT_LOADED`(범위 피치가 있을 때만. 전체 범위에서
  막힌 읽기는 가져올 기록이 아니라 단계에 사실만 보인다) · 리뷰 없음 `REVIEW_MISSING` · 읽은 범위에
  기록 없음 `NO_RECORD`(facts `TAKES` · `GOAL` · `PROBLEM_SEGMENTS`) · 도구 실패 `TOOL_ERROR`. 기록 없음 · 리뷰 없음은 도구가 '없음'
  근거(tools/impl.py)를 낸 결과와 같은 때다(범위 피치의 Take 0개 · 대화 목표 없음 · 물은 영역이 측정됐고 문제 구간 0곳). 결정 로그 줄(`decision_line`,
  `event: agent_decision`)은 서비스 로그에만 남기고 진단 기록(BE 로 감)에는 넣지 않는다. 값은 enum · 정수 · null 뿐이다(글 · 수치 · 인자 값 없음).
- 고칠 때: 화면 낱말은 templates.py `PLAN_*` 표, 스위치는 config.py(`plan_progress` · `decision_log` · `research_signals`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import field_validator

from . import checks as chk
from . import templates as tpl
from .claims import DIM_LABEL, METRIC_LABEL
from .config import ChatAgentConfig
from .diagnostics import code_enum
from .evidence import EvidenceRegistry
from .prompts import PLAN_STEP_TOOLS
from .runctx import RunCtx
from .schemas import PlanStep
from .tools import ToolResult
from .tools.impl import REVIEW_SECTIONS
from .values import METRIC_AREA

#: `PlanStep.step` 글자 수 한도(5-2)
STEP_CHARS = 60
#: 기록을 읽는 도구. 신호 뒤 모델이 이 중 다른 도구 · 다른 대상을 고르면 `ALT_TOOL` 이다
RECORD_TOOLS = frozenset(
    {"list_pitches", "list_takes", "get_take_report", "compare_takes", "get_goals"}
)
#: 조사가 이 까닭으로 멈추면 `BUDGET_LOW` · `STOP_PARTIAL`(한도 · 무효 도구 반복 · 실패한 행동 반복). 앞 다섯은 지금 단계에
#: `시간 부족`을 단다. 같은 요청 되풀이 · 교대 반복(`NO_PROGRESS` · `ALTERNATING`)은 넣지 않는다: 이미 읽은 것을 다시 청한 것이라
#: 끝 상태를 요청 해소로 정한다(turn.py `_SOFT_STOPS`)
LIMIT_STOPS = ("TIME", "MODEL_CALLS", "TOOL_CALLS", "TOKENS", "LIMIT")
BUDGET_STOPS = (
    *LIMIT_STOPS,
    "INVALID_TOOLS",
    "REPEATED_FAILED_ACTION",
)
#: 신호마다 모델이 고를 수 있는 대응(조사 데이터 블록 `signals`, 설정 `research_signals`)
SIGNAL_OPTIONS: dict[str, tuple[str, ...]] = {
    "NOT_COMPARABLE": (
        "같은 대본 · 기준의 다른 Take 와 비교",
        "비교하지 않고 까닭과 각 Take 값만 말하기",
    ),
    "NOT_LOADED": ("fetch_take 로 가져오기",),
    "REVIEW_MISSING": ("코치 결과로만 답하고 리뷰 뒤 확인 안내",),
    "TOOL_ERROR": ("다른 도구로 확인", "확인한 데까지 답"),
    "NO_RECORD": ("읽은 범위의 '없음'을 근거로 답",),
}
#: 결정 로그 줄의 키(고정 순서). 값은 enum · 정수 · null 뿐이다
DECISION_KEYS = (
    "event",
    "request_id",
    "run_id",
    "turn_call",
    "seq",
    "signal",
    "facts",
    "decider",
    "effect",
    "step",
    "tool_seq",
)

#: "Take 3·4" 처럼 Take 를 한 번 쓰고 번호를 나열한 실행 글. 표시 검사에는 "Take 3·Take 4"로 펴서 두 번호를 모두 근거와 맞춘다
_TAKE_PAIR = re.compile(r"(Take\s*)(\d+)·(\d+)")


class PlanStepDraft(PlanStep):
    """계획 출력 · `update_plan` 인자의 단계 초안 = `PlanStep` + 도구 힌트. `PlanStep`으로 바꿀 때 `tool`을 뺀다(응답 모양 그대로)."""

    tool: str | None = None

    @field_validator("tool", mode="before")
    @classmethod
    def _known_tool(cls, v: Any) -> str | None:
        # 모르는 힌트는 없는 것으로 본다(계획 자체를 무효로 만들지 않는다)
        return v if v in PLAN_STEP_TOOLS else None


def split_steps(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str | None]]:
    """단계 초안 목록 → (응답 `PlanStep` 모양 목록, 단계마다 도구 힌트)."""
    drafts = [PlanStepDraft.model_validate(s) for s in raw]
    return [d.model_dump(exclude={"tool"}) for d in drafts], [d.tool for d in drafts]


#: 단계 목적에서 지울 숫자 표기: Take 번호(나열 포함) · 장 번호 · 단위가 붙은 값과 그 바로 뒤 조사(표시 검사 6-9 가 보는 것)
_PURPOSE_NUMBER = re.compile(
    r"(?:(?:Take|테이크)\s*\d+(?:\s*(?:·|,|와|과|랑|하고|및)\s*\d+)*"
    r"|\d+\s*번째?\s*(?:Take|테이크)"
    r"|슬라이드\s*\d+"
    r"|\d+(?:[.,]\d+)*\s*(?:장|퍼센트\s?포인트|퍼센트p|%p|퍼센트|%|ms|dB|초|분|번째|번|회|개|점|자)?)"
    r"(?:의|에서|와|과|랑|보다|부터|까지|을|를|은|는|이|가|도|에)?",
    re.IGNORECASE,
)


def clean_purposes(
    steps: list[dict[str, Any]],
    hints: list[str | None],
    registry: EvidenceRegistry,
    cfg: ChatAgentConfig,
) -> list[dict[str, Any]]:
    """모델이 단계 목적(· 설명)에 쓴 숫자 · Take · 장 번호가 표시 검사(6-9, 근거 없음)에 걸리면 그 표기만 지운다.
    목적은 계획 때 정한 것이라 받칠 근거가 없다 — 숫자는 실행 뒤 그 단계 근거로 결과 쪽에 붙는다. 지운 뒤 빈 목적이면 그 단계
    도구의 종류 이름(없으면 기본 이름). 다른 까닭(개인정보 · 금지 주장 · 지시문)으로 걸린 것은 그대로 두어 호출자가 거른다."""
    out: list[dict[str, Any]] = []
    for s, tool in zip(steps, hints, strict=True):
        if not _blocked(s["step"], [], registry, cfg) and not (
            s.get("note") and _blocked(s["note"], [], registry, cfg)
        ):
            out.append(s)
            continue
        purpose = _strip_numbers(s["step"]) or tpl.PLAN_TOOL_NAME.get(
            tool or "", tpl.PLAN_PURPOSE_FALLBACK.get(tool or "", tpl.PLAN_PURPOSE_DEFAULT)
        )
        note = _strip_numbers(s["note"]) if s.get("note") else s.get("note")
        out.append({**s, "step": purpose, "note": note or None})
    return out


def _strip_numbers(text: str) -> str:
    return " ".join(_PURPOSE_NUMBER.sub(" ", text).split()).strip(" ·,:-")


def _meta(
    purpose: str, tool: str | None, note: str | None = None, *, skipped: bool = False
) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "tool": tool,
        "note": note,
        "done": False,
        "failed": False,
        "skipped": skipped,
        "exec": None,
        "plain": None,
        "result": None,
        "evidence_ids": [],
        "tool_seqs": [],
        "ran": None,
        "take": None,
        #: 이 단계에 붙은 도구 호출 열쇠(`call_key`). 같은 호출의 캐시 적중은 새 단계로 붙이지 않는다
        "keys": [],
    }


def _ran(m: dict[str, Any]) -> bool:
    return bool(m["tool_seqs"]) or m["exec"] is not None


def new_meta(
    steps: list[dict[str, Any]],
    tools: list[str | None],
    *,
    old: list[dict[str, Any]] | None = None,
    shown: list[dict[str, Any]] | None = None,
    max_steps: int = 8,
) -> list[dict[str, Any]]:
    """새 계획의 단계 메타. 모델이 SKIPPED 로 둔 새 단계는 건너뜀으로 둔다.

    모델이 고친 목록(`old`가 있음)이면 합친다: 이미 실행한 단계(결과가 붙은 단계 — 완료 · 사실 결과 · 확인 실패)는 그 메타 ·
    표시 그대로 앞에 남기고(순서 그대로), 그 뒤에 모델의 새 목록 중 아직 실행하지 않은 단계를 잇는다. 남긴 단계와 글이 같은 새
    단계(목적 또는 화면에 보인 글)는 뺀다. 합쳐 `max_steps`를 넘으면 뒤의 새 단계를 자른다(남긴 실행 단계는 자르지 않는다).
    실행하지 않은 옛 단계는 새 목록으로 바뀐다. 끝 처리에서 남긴 단계는 실행 결과 그대로(DONE), 새 미실행 단계만 SKIPPED · TODO 다."""
    old, shown = old or [], shown or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k, m in enumerate(old):
        if _ran(m):
            out.append(dict(m))
            seen.add(m["purpose"])
            if k < len(shown):
                seen.add(shown[k]["step"])
    for s, tool in zip(steps, tools, strict=True):
        if s["step"] in seen:
            continue
        if len(out) >= max_steps:
            break
        out.append(_meta(s["step"], tool, s.get("note"), skipped=s["status"] == "SKIPPED"))
    return out


@dataclass
class StepRun:
    """도구(또는 가져오기 행동) 한 번의 실행을 단계에 붙일 모양: 실행 · 결과 글, 끝났는가, 상황 신호."""

    tool: str
    exec: str
    plain: str
    result: str
    done: bool
    evidence_ids: list[str] = field(default_factory=list)
    take: int | None = None
    signal: str | None = None
    facts: list[str] = field(default_factory=list)


def _take_number(ctx: RunCtx, take_id: str | None) -> int | None:
    if take_id is None:
        return None
    s = ctx.summaries.get(take_id)
    if s is not None:
        return s.take_number
    snap = ctx.snapshots.get(take_id)
    return snap.summary.take_number if snap is not None else None


def asked_areas(drafts: list[dict[str, Any]], cfg: ChatAgentConfig) -> frozenset[str] | None:
    """판단 초안(측정 · 요청)이 집은 지표 · 차원의 코치 결과 영역(GAZE · SPEED …). 집은 것이 없으면 None(물은 영역을 모름)."""
    out: set[str] = set()
    for d in drafts:
        if d.get("type") not in ("MEASURED", "REQUEST"):
            continue
        sub = d.get("subject") or {}
        metric, dim = sub.get("metric"), sub.get("dimension")
        metrics = {metric} if isinstance(metric, str) else set()
        if not metrics and dim in cfg.dimensions:
            metrics = set(cfg.dimensions[dim].metrics)
        out |= {METRIC_AREA[m] for m in metrics if m in METRIC_AREA}
    return frozenset(out) or None


def _take_count(v: Any) -> int | None:
    """`list_pitches` 결과의 Take 수(근거를 단 모양 `{value, evidence_id}` · 옛 결과의 정수)."""
    v = v.get("value") if isinstance(v, dict) else v
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def read_run(
    name: str, args: Any, tr: ToolResult, ctx: RunCtx, *, areas: frozenset[str] | None = None
) -> StepRun:
    """도구 결과 하나를 단계 실행 · 결과 글과 상황 신호로 읽는다(코드 템플릿, 숫자는 인자 · 결과의 번호만).

    '없음' 근거(tools/impl.py)를 낸 결과는 같은 곳에서 `NO_RECORD`(facts `TAKES` · `GOAL` · `PROBLEM_SEGMENTS`) ·
    `REVIEW_MISSING` 신호를 낸다. `areas`: 물은 영역(`asked_areas`). 문제 구간 결과를 그 영역으로 읽는다(모르면 읽은 영역 전체)."""
    r = tpl.PLAN_RESULT
    plain = tpl.PLAN_TOOL_NAME.get(name, name)
    take: int | None = None
    exec_ = tpl.PLAN_EXEC.get(name, plain)
    if name == "compare_takes":
        a = _take_number(ctx, args.previous_take_id)
        b = _take_number(ctx, args.current_take_id)
        exec_ = f"Take {a}·{b} 비교" if a is not None and b is not None else plain
    elif name == "get_take_report":
        take = _take_number(ctx, args.take_id)
        exec_ = tpl.PLAN_REPORT_EXEC.format(n=take) if take is not None else plain
    ev = list(tr.evidence_ids)

    def run(
        result: str, *, done: bool = True, signal: str | None = None, facts: Any = ()
    ) -> StepRun:
        return StepRun(name, exec_, plain, result, done, ev, take, signal, list(facts))

    if tr.read_state == "NOT_LOADED":
        if ctx.scope_pitch_id is None:
            # 전체 범위(범위 피치 없음)에서 Take 를 읽으려다 막힌 것은 가져올 기록이 아니다: 대응을 기다리는 신호(가져오기 대응)를
            # 내지 않는다. 신호를 내면 모델이 fetch_pitch 로 범위를 바꿨다(r9 평가 glob-ambiguous-name · glob-weakest,
            # 조사 지침 '전체 범위 질문은 list_pitches 요약으로 답하고 범위를 바꾸지 않는다'와 어긋남)
            return run(r["NOT_LOADED"])
        return run(r["NOT_LOADED"], signal="NOT_LOADED")
    if not tr.ok:
        code = code_enum((tr.error or {}).get("code"))
        return run(r["ERROR"], done=False, signal="TOOL_ERROR", facts=[code] if code else [])
    data = tr.data or {}
    if name == "compare_takes":
        if data.get("comparable") is False:
            reason = data.get("reason")
            word = tpl.PLAN_NOT_COMPARABLE.get(reason or "", tpl.PLAN_NOT_COMPARABLE_OTHER)
            facts = [code_enum(reason)] if reason else []
            return run(
                r["NOT_COMPARABLE"].format(reason=word), signal="NOT_COMPARABLE", facts=facts
            )
        where = f"{args.slide_number}장 " if args.slide_number else ""
        what = METRIC_LABEL.get(args.metric, "지표")
        outcome = tpl.PLAN_OUTCOME.get(data.get("outcome") or "", tpl.PLAN_OUTCOME_OTHER)
        return run(f"{where}{what} {outcome}")
    if name == "get_take_report":
        return _report_run(args, data, tr, ctx, run, areas)
    if name == "list_takes":
        if not data.get("items"):
            return run(r["NO_TAKES"], signal="NO_RECORD", facts=["TAKES"])
        return run(r["CHECKED"])
    if name == "list_pitches":
        # 범위 피치(범위가 없으면 읽은 피치가 하나일 때 그 피치)의 Take 수가 0 이면 '읽은 범위에 Take 없음'
        rows = [x for x in data.get("items") or [] if isinstance(x, dict)]
        scope = ctx.scope_pitch_id
        target = (
            [x for x in rows if x.get("pitch_id") == scope]
            if scope
            else (rows if len(rows) == 1 else [])
        )
        if target and all(_take_count(x.get("take_count")) == 0 for x in target):
            return run(r["NO_TAKES"], signal="NO_RECORD", facts=["TAKES"])
        return run(r["CHECKED"])
    if name == "get_goals":
        conv = data.get("conversation_goal") or {}
        goal = conv.get("goal") or {}
        if goal.get("metric"):
            return run(r["GOAL"])
        if conv.get("goal") is None:
            # 대화 목표가 없다('없음' 근거 `goal.status` NONE 과 같은 때). 다른 피치의 목표만 있으면 '없음'이 아니다
            return run(r["NO_GOAL"], signal="NO_RECORD", facts=["GOAL"])
        return run(r["CHECKED"])
    if name == "search_guides":
        return run(r["GUIDES"] if data.get("items") else r["NO_GUIDES"])
    return run(r["CHECKED"])


def _report_run(
    args: Any,
    data: dict[str, Any],
    tr: ToolResult,
    ctx: RunCtx,
    run: Any,
    areas: frozenset[str] | None = None,
) -> StepRun:
    """get_take_report 결과: 리뷰 절인데 리뷰가 없으면 `리뷰 분석 전`, 문제 구간은 물은 영역(모르면 읽은 영역 전체)으로 있음 ·
    측정 안 됨(그 영역 하나라도 측정 안 됨) · 없음(모두 측정됐고 0곳, '없음' 근거와 같은 때), 차원 판정은 첫 우선 개선 차원, 그 밖은 `확인`."""
    r = tpl.PLAN_RESULT
    section = args.section
    snap = ctx.snapshots.get(args.take_id)
    if section in REVIEW_SECTIONS and snap is not None and snap.results.review is None:
        return run(r["REVIEW_MISSING"], signal="REVIEW_MISSING")
    items = data.get("items") or []
    if section == "problem_segments":
        if any(areas is None or it.get("area") in areas for it in items):
            return run(r["SEGMENTS"])
        none_areas = {x.get("area") for x in data.get("no_segment_areas") or []}
        unmeasured = {x.get("area") for x in data.get("unmeasured_areas") or []}
        want = set(areas) if areas is not None else none_areas | unmeasured
        if want & unmeasured:
            return run(r["UNMEASURED"])
        if want and want <= none_areas:
            return run(r["NO_SEGMENTS"], signal="NO_RECORD", facts=["PROBLEM_SEGMENTS"])
        return run(r["CHECKED"])
    if section == "dimensions":
        first = next((it["dimension"] for it in items if it.get("state") == "PRIORITY"), None)
        if first is not None:
            return run(r["PRIORITY"].format(dimension=DIM_LABEL.get(first, first)))
    if section == "slides" and (tr.read_state == "UNMEASURED" or _asked_unmeasured(snap, areas)):
        return run(r["UNMEASURED"])
    if section == "slides" and args.slide_number:
        # 무엇을 봤는지 보인다: `3장 확인`(장 번호는 그 단계 근거로 표시 검사를 받는다)
        return run(r["SLIDE_CHECKED"].format(slide=args.slide_number))
    return run(r["CHECKED"])


def _asked_unmeasured(snap: Any, areas: frozenset[str] | None) -> bool:
    """물은 영역(`asked_areas`)이 그 Take 에서 모두 측정 안 됨인가(영역에 미측정 사유가 있거나 Take 값이 null). 장별 값을 읽으면
    다른 지표가 측정됐어도 물은 영역의 측정 안 됨을 단계에 보인다(final60 ana-webinar-voice-unmeasured: 목소리 크기를 물었는데
    `확인`으로만 보이고 답에 측정 안 됨이 빠짐). 물은 영역을 모르거나 Take 결과가 없으면 False."""
    if not areas or snap is None:
        return False
    tr = snap.results.take_result or {}
    got = tr.get("areas") or {}
    for area in areas:
        av = got.get(area)
        if not isinstance(av, dict) or not (
            av.get("unmeasured_reason") is not None or av.get("take") is None
        ):
            return False
    return True


def fetch_run(take_id: str | None, ok: bool, ctx: RunCtx) -> StepRun:
    """가져오기 행동(FETCH_TAKE)의 결과: `Take {n} 가져오기` → `가져옴` · `확인 실패`."""
    plain = tpl.PLAN_TOOL_NAME["fetch_take"]
    n = _take_number(ctx, take_id)
    exec_ = f"Take {n} 가져오기" if n is not None else plain
    r = tpl.PLAN_RESULT
    return StepRun("fetch_take", exec_, plain, r["FETCHED"] if ok else r["ERROR"], ok, take=n)


def _pending(meta: list[dict[str, Any]]) -> list[int]:
    return [i for i, m in enumerate(meta) if not m["done"] and not m["skipped"]]


def pick(meta: list[dict[str, Any]], tool: str) -> int | None:
    """실행한 도구를 붙일 단계: 힌트가 같은 첫 미완 단계 → 힌트 없는 지금 단계 → 없음(None)."""
    pending = _pending(meta)
    for i in pending:
        if meta[i]["tool"] == tool:
            return i
    if pending and meta[pending[0]]["tool"] is None:
        return pending[0]
    return None


def attach(
    meta: list[dict[str, Any]],
    run: StepRun,
    tool_seq: int | None,
    *,
    max_steps: int,
    key: str | None = None,
    cached: bool = False,
) -> int | None:
    """실행을 단계에 붙이고 그 단계 번호(0부터)를 돌려준다. 붙일 단계가 없으면 실행 뒤에 새 단계를 남은 단계 앞에 끼운다
    (목적 = 도구 종류 이름). 이미 `max_steps`단계면 붙이지 않고 None(trace 에만 남는다).
    캐시 적중이라도 힌트가 맞는 미완 단계가 있으면 그 단계를 채운다(계획한 일을 이미 읽은 기록으로 끝냄). 붙일 단계가 없어 새 단계를
    덧붙여야 할 때, 같은 호출(`key`)을 이미 보인 단계가 있는 캐시 적중이면 덧붙이지 않고 그 단계 번호만 돌려준다(되풀이 읽기는 새 일이 아니다)."""
    i = pick(meta, run.tool)
    if i is None and cached and key is not None:
        seen = next((j for j, m in enumerate(meta) if key in m.get("keys", [])), None)
        if seen is not None:
            return seen
    if i is None:
        if len(meta) >= max_steps:
            return None
        pending = _pending(meta)
        i = pending[0] if pending else len(meta)
        meta.insert(i, _meta(tpl.PLAN_TOOL_NAME.get(run.tool, run.tool), run.tool))
    m = meta[i]
    m.update(
        exec=run.exec,
        plain=run.plain,
        result=run.result,
        done=run.done,
        failed=not run.done,
        ran=run.tool,
        take=run.take,
        evidence_ids=list(dict.fromkeys([*m["evidence_ids"], *run.evidence_ids])),
    )
    if tool_seq is not None:
        m["tool_seqs"] = [*m["tool_seqs"], tool_seq]
    if key is not None:
        m["keys"] = [*m.get("keys", []), key]
    return i


def current_step(meta: list[dict[str, Any]]) -> int | None:
    """지금 하던 단계(답 쓰기 단계를 뺀 첫 미완 단계)."""
    return next((i for i in _pending(meta) if meta[i]["tool"] != "answer"), None)


def mark_short(meta: list[dict[str, Any]]) -> int | None:
    """조사가 한도 · 시간으로 멈췄다: 지금 하던 단계에 결과가 없으면 `시간 부족`을 단다. 그 단계 번호(없으면 None)."""
    i = current_step(meta)
    if i is not None and meta[i]["result"] is None:
        meta[i]["result"] = tpl.PLAN_RESULT["SHORT"]
    return i


def compose(purpose: str, exec_: str | None, result: str, limit: int = STEP_CHARS) -> str:
    """`목적: 실행 → 결과`(60자). 넘으면 목적을 줄여 "…", 그래도 넘으면 `실행 → 결과`. 실행이 목적과 같으면(덧붙은 단계) 목적을 되풀이하지 않는다."""
    if exec_ is None:
        tail = result
    elif exec_ == purpose:
        return f"{exec_} → {result}"[:limit]
    else:
        tail = f"{exec_} → {result}"
    full = f"{purpose}: {tail}"
    if len(full) <= limit:
        return full
    room = limit - len(tail) - len(": ") - len("…")
    if room >= 2:
        return f"{purpose[:room].rstrip()}…: {tail}"
    return tail[:limit]


def _blocked(text: str, ids: list[str], registry: EvidenceRegistry, cfg: ChatAgentConfig) -> bool:
    probe = _TAKE_PAIR.sub(r"\1\2·Take \3", text)
    return bool(
        chk.check_display_strings(
            plan=[{"step": probe, "status": "DOING", "note": None}],
            registry=registry,
            display_evidence={"plan:0": list(ids)},
            cfg=cfg,
            assessments={},
        )
    )


def display_text(m: dict[str, Any], registry: EvidenceRegistry, cfg: ChatAgentConfig) -> str:
    """단계 글: 실행 전이면 목적, 실행 뒤면 `목적: 실행 → 결과`. 그 단계 근거로 받칠 수 없으면 숫자 없는 실행 이름, 그래도 안 되면 목적."""
    if m["result"] is None:
        return m["purpose"]
    for exec_ in dict.fromkeys((m["exec"], m["plain"])):
        text = compose(m["purpose"], exec_, m["result"])
        if not _blocked(text, m["evidence_ids"], registry, cfg):
            return text
    return m["purpose"]


def render(
    meta: list[dict[str, Any]], registry: EvidenceRegistry, cfg: ChatAgentConfig
) -> list[dict[str, Any]]:
    """단계 메타 → 응답 `PlanStep` 목록(상태: 끝낸 단계 DONE · 건너뛴 단계 SKIPPED · 첫 미완 단계 DOING · 나머지 TODO)."""
    pending = _pending(meta)
    first = pending[0] if pending else None
    out: list[dict[str, Any]] = []
    for i, m in enumerate(meta):
        if m["done"]:
            status = "DONE"
        elif m["skipped"]:
            status = "SKIPPED"
        else:
            status = "DOING" if i == first else "TODO"
        out.append(
            PlanStep(
                step=display_text(m, registry, cfg), status=status, note=m["note"]
            ).model_dump()  # type: ignore[arg-type]
        )
    return out


def unresolved_steps(meta: list[dict[str, Any]]) -> list[int]:
    """끝까지 풀리지 않은 단계(0부터): 시간 · 한도로 멈춘 단계(`시간 부족`)와, 도구가 실패했고(`확인 실패`) 그 뒤 다른 실행이
    성공하지 않은 단계. 실패 뒤 다른 도구 · 대상으로 확인했으면(복구된 실패) 풀린 것으로 본다. 실행하지 않은 단계는 넣지 않는다
    (답이 DONE 이면 건너뜀)."""
    done_seqs = [max(m["tool_seqs"]) for m in meta if m["done"] and m["tool_seqs"]]
    out: list[int] = []
    for i, m in enumerate(meta):
        if m["done"] or m["skipped"]:
            continue
        if m["result"] == tpl.PLAN_RESULT["SHORT"]:
            out.append(i)
        elif m["failed"]:
            last = max(m["tool_seqs"], default=None)
            if last is None or not any(s > last for s in done_seqs):
                out.append(i)
    return out


def end_status(meta: list[dict[str, Any]], *, cut: bool, answered: bool) -> str:
    """끝 상태(TARGET 6 · 5-5 status): 요청한 부분이 모두 해소됐으면 `DONE`, 아니면 `PARTIAL`.

    해소 = 조사가 한도 · 장애로 끊기지 않았고(`cut` 아님), 풀리지 않은 단계(`unresolved_steps`)가 없고, 질문 대상마다 확인한
    답 · 확인한 '없음' · 비교할 수 없는 까닭 문장이 있다(`answered`, turn.py 가 답 문장으로 본다). 복구된 도구 실패 이력만으로는
    PARTIAL 이 되지 않는다. 끝 처리(`finish_plan`)도 이 상태를 쓴다(DONE 이면 실행하지 않은 단계 SKIPPED, PARTIAL 이면 TODO)."""
    return "DONE" if answered and not cut and not unresolved_steps(meta) else "PARTIAL"


def finish_plan(
    meta: list[dict[str, Any]],
    registry: EvidenceRegistry,
    cfg: ChatAgentConfig,
    status: str,
    *,
    answered: bool,
) -> list[dict[str, Any]]:
    """끝 처리: DONE · REFUSED 면 실행하지 않은 단계 SKIPPED, PARTIAL 이면 TODO(DOING 포함), NEEDS_ACTION 은 그대로.
    답 쓰기 단계(`tool == "answer"`)는 `answered`(답 문장이 하나 이상)면 DONE. 실행했지만 실패한 단계(`확인 실패`)는 DONE · REFUSED
    답에서 DONE 이다 — 실행한 단계이고 글이 실패를 보이므로 '의도적으로 건너뜀'(SKIPPED)이 아니다. PARTIAL 이면 남은 일(TODO)."""
    plan = render(meta, registry, cfg)
    if status not in ("DONE", "REFUSED", "PARTIAL"):
        return plan
    left = "TODO" if status == "PARTIAL" else "SKIPPED"
    out = []
    for m, p in zip(meta, plan, strict=True):
        if p["status"] in ("DOING", "TODO"):
            ran_failed = bool(m.get("ran")) and bool(m.get("failed")) and status != "PARTIAL"
            done = (m["tool"] == "answer" and answered) or ran_failed
            p = {**p, "status": "DONE" if done else left}
        out.append(p)
    return out


# ── 상황 신호 · 결정 로그 ──────────────────────────────────────────────────────


def call_key(name: str, args: Any) -> str:
    """같은 도구 · 같은 대상인지 가리는 열쇠(대응 판정에만, 로그에는 쓰지 않는다)."""
    body = args.model_dump(mode="json") if hasattr(args, "model_dump") else args
    return json.dumps([name, body], ensure_ascii=False, sort_keys=True, default=str)


def open_signal(run: StepRun, key: str, step: int | None, tool_seq: int | None) -> dict[str, Any]:
    """대응을 기다리는 신호(`LoopState.signals_open`). `seen`은 모델이 이 신호를 본 조사 호출이 있었는가."""
    return {
        "signal": run.signal,
        "facts": list(run.facts),
        "key": key,
        "step": step,
        "tool_seq": tool_seq,
        "seen": False,
    }


def signals_view(open_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """조사 데이터 블록의 `signals`: 신호 · 사실 enum · 고를 수 있는 대응."""
    return [
        {
            "signal": s["signal"],
            "facts": list(s["facts"]),
            "options": list(SIGNAL_OPTIONS.get(s["signal"], ())),
        }
        for s in open_
    ]


def decision_line(
    *,
    request_id: str,
    run_id: str,
    turn_call: int,
    seq: int,
    signal: str,
    facts: list[str],
    decider: str,
    effect: str,
    step: int | None,
    tool_seq: int | None,
) -> dict[str, Any]:
    """결정 로그 한 줄(`event: agent_decision`, 키 순서 `DECISION_KEYS`)."""
    return {
        "event": "agent_decision",
        "request_id": request_id,
        "run_id": run_id,
        "turn_call": turn_call,
        "seq": seq,
        "signal": signal,
        "facts": [f for f in facts if f],
        "decider": decider,
        "effect": effect,
        "step": step,
        "tool_seq": tool_seq,
    }


__all__ = [
    "BUDGET_STOPS",
    "clean_purposes",
    "DECISION_KEYS",
    "LIMIT_STOPS",
    "PlanStepDraft",
    "RECORD_TOOLS",
    "StepRun",
    "asked_areas",
    "attach",
    "call_key",
    "compose",
    "current_step",
    "decision_line",
    "display_text",
    "end_status",
    "fetch_run",
    "finish_plan",
    "mark_short",
    "new_meta",
    "open_signal",
    "pick",
    "read_run",
    "render",
    "signals_view",
    "split_steps",
    "unresolved_steps",
]
