"""도구 8개 — 정의 · 인자 검사 · 캐시 · 도구 기록 (기능 문서 5-7, 7-3 L1-15 · L1-16 · L1-17 · L1-18 · L1-20).

- 누가 쓰나: loop.py 가 모델에 도구 정의(`model_tool_definitions`)를 주고, 모델이 고른 호출을 `run_tool()`로 실행한다.
- 도구는 요청에 실린 값과 자료 묶음만 읽는다. DB · BE 를 부르지 않는다. 기본 자료에 없으면 `NOT_LOADED`로 돌려주고
  모델이 행동(`fetch_pitch` · `fetch_take`)을 고르게 한다.
- 공통 반환(5-7): `{ok, data, error, evidence, basis}`. 모델에는 `ToolResult.model_view()`(값 옆에 근거 id 를 단 짧은 JSON)만 간다.
- 처리 순서 (`run_tool`)
  1. 이름이 P0 허용 목록에 있는지, 인자가 그 도구의 엄격한 스키마에 맞는지 본다. 아니면 실행하지 않고 `TOOL_INVALID`.
  2. 캐시 열쇠 = (도구, 검증한 인자의 정렬 JSON, 지금 범위, 읽은 결과 판 집합 · 화면 스냅샷). 있으면 `CACHED`로 돌려주고
     도구 호출 수에 세지 않는다. `NOT_LOADED` · 오류 결과는 캐시하지 않는다.
  3. 실행해 근거를 발급하고, 도구 기록(`TraceItem`)을 코드 템플릿으로 만든다(대본 · 사용자 원문 · 검색 질의 원문은 넣지 않는다).
     도구 구현 안에서 예상하지 못한 예외가 나면 실행 전체를 끝내지 않고 그 호출 하나의 오류 결과(`TOOL_FAILED`, `failed_result`)로
     돌려준다(L1-19). 예외 글은 결과 · 기록에 싣지 않고, 예외 종류 · 위치만 `crash`에 남겨 loop.py 가 서비스 로그에 쓴다.
- 고칠 때: 도구를 더하거나 인자 · 결과 모양을 바꾸면 `version.TOOL_SCHEMA_VERSION`을 올린다(진행 중 실행은 판을 고정, 5-6).
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

from pydantic import BaseModel, ValidationError

from ..evidence import canonical_json
from ..runctx import RunCtx
from ..schemas import TraceItem
from ..templates import PLAN_RESULT, PLAN_TOOL_NAME
from . import impl

P0_TOOLS = (
    "update_plan",
    "get_screen",
    "list_pitches",
    "list_takes",
    "get_take_report",
    "compare_takes",
    "get_goals",
    "search_guides",
)

#: 모델에 주는 도구 설명. 짧게 둔다(도구가 많고 설명이 길면 모델이 잘못 고르기 쉽다, 4-3)
DESCRIPTIONS = {
    # '단계마다 tool 에 적는다'처럼 힌트를 채우라는 지시는 넣지 않고(계획 지침 prompts.PLAN 에만), 고칠 때만 · 상태는 코드가 단다고
    # 적는다: 인자 스키마에 `tool`(도구 이름 목록)이 생긴 뒤로 모델이 조사 첫머리에 계획을 만들고 끝에 상태만 바꾸려고 update_plan 을
    # 거듭 불러 반복 멈춤(PARTIAL) · 한도에 걸렸다(r9 평가). 이 설명 · 조사 지침의 같은 말로 같은 문항 33번 실행의 호출이 48 → 13번
    "update_plan": "할 일 자체가 바뀔 때 할 일 목록(1~8단계)을 고친다. 단계 상태 · 결과는 코드가 실행에 맞춰 단다. tool 은 그 단계에서 쓸 도구(모르면 null), step 은 목적만.",
    "get_screen": "대화창을 연 화면의 상태(입력 중인 값과 저장된 값)를 읽는다. 피치 만들기 · 수정 · 장치 점검 화면에서만 값이 있다.",
    "list_pitches": "내 피치 목록에서 이름(query)으로 찾는다. query 가 null 이면 전체 목록 요약. 목록은 마지막 연습 시작이 늦은 순이고(분석 중 · 실패한 Take 포함), 항목마다 마지막 연습 시작 날짜(last_practiced)와 분석이 끝난 Take 수(take_count)를 근거와 함께 준다.",
    "list_takes": "지금 범위 피치의 최근 Take 요약을 조건으로 거른다(대본 버전 · 끝까지 한 발표 · 모드).",
    "get_take_report": "Take 하나의 결과에서 필요한 부분만 읽는다. section: slides(지표값: slide_number 가 null 이면 Take 전체 값과 장별 요약, 있으면 그 장의 값) · dimensions(차원 상태 · 점수) · improvements(강점 · 개선점) · problem_segments(문제 구간: 어디서 · 무슨 문제) · mission_results(미션 결과). 질문에 답할 section 만 읽는다. 기본 자료에 없으면 NOT_LOADED → fetch_take 행동을 쓴다.",
    "compare_takes": "같은 피치의 두 Take 를 같은 지표로 비교한다. metric 은 지표 키(audience_ratio · script_ratio · cpm · filler_per_min · duration_ms 등). 비교할 수 없으면 이유(reason)만 돌려준다. 결론(좋아짐 · 나빠짐)은 결과의 outcome 을 쓴다.",
    "get_goals": "공식 미션 · 평가 기준 목표 · 대화 목표를 출처와 함께 읽는다.",
    "search_guides": "발표법 · 화면별 사용법 자료에서 찾는다(최대 5개). 자료에 없는 발표법은 일반 조언(GENERAL)으로만 짧게 답한다.",
}


@dataclass
class ToolResult:
    """도구 한 번의 결과."""

    name: str
    ok: bool
    data: dict[str, Any] | None
    error: dict[str, str] | None
    evidence_ids: list[str] = field(default_factory=list)
    #: 읽기 상태(NOT_LOADED · OMITTED · UNMEASURED · NOT_FOUND · EMPTY), 정상이면 None
    read_state: str | None = None
    target_ids: list[str] = field(default_factory=list)
    revisions: dict[str, int | None] = field(default_factory=dict)
    outcome: str = "OK"
    args_summary: str = ""
    result_summary: str = ""
    cacheable: bool = True
    source_ids: list[str] = field(default_factory=list)
    #: 서비스 로그에 더할 관측값(`search_guides`: embed · embed_ms · search_mode, `agent_embed` 로그로만 나가고 진단 기록 모양은 안 바꾼다). 캐시에 담지 않는다
    diag: dict[str, Any] = field(default_factory=dict)
    #: 도구 구현의 예외(`failed_result`): 예외 종류 · 마지막 위치만(예외 글 없음). loop.py 가 `agent_tool_crash` 로그로 남긴다. 캐시에 담지 않는다
    crash: dict[str, Any] | None = None

    def model_view(self) -> dict[str, Any]:
        """모델에 돌려줄 짧은 JSON."""
        view: dict[str, Any] = {"ok": self.ok}
        if self.read_state:
            view["read_state"] = self.read_state
        if self.data is not None:
            view["data"] = self.data
        if self.error is not None:
            view["error"] = self.error
        return view

    def to_cache(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "evidence_ids": self.evidence_ids,
            "read_state": self.read_state,
            "target_ids": self.target_ids,
            "revisions": self.revisions,
            "args_summary": self.args_summary,
            "result_summary": self.result_summary,
            "source_ids": self.source_ids,
        }

    @classmethod
    def from_cache(cls, d: dict[str, Any]) -> ToolResult:
        return cls(
            name=d["name"],
            ok=d["ok"],
            data=d["data"],
            error=d["error"],
            evidence_ids=list(d.get("evidence_ids", [])),
            read_state=d.get("read_state"),
            target_ids=list(d.get("target_ids", [])),
            revisions=dict(d.get("revisions", {})),
            outcome="CACHED",
            args_summary=d.get("args_summary", ""),
            result_summary=d.get("result_summary", ""),
            source_ids=list(d.get("source_ids", [])),
        )


def model_tool_definitions() -> list[dict[str, Any]]:
    """OpenAI function 모양의 도구 정의 8개(인자 JSON Schema 는 impl 의 엄격한 모델에서 만든다)."""
    out = []
    for name in P0_TOOLS:
        schema = impl.ARGS[name].model_json_schema()
        schema.pop("title", None)
        out.append(
            {
                "type": "function",
                "function": {"name": name, "description": DESCRIPTIONS[name], "parameters": schema},
            }
        )
    return out


class InvalidArgs(str):
    """틀린 인자의 사유 코드(`BAD_ARGS` · `UNKNOWN_TOOL`)와 칸별 안내(`fields`, Issue #1194 4-D). 문자열이라 사유 코드로도 쓴다."""

    fields: list[dict[str, Any]]

    def __new__(cls, code: str, fields: list[dict[str, Any]] | None = None) -> InvalidArgs:
        obj = super().__new__(cls, code)
        obj.fields = list(fields or [])
        return obj


#: pydantic 오류 종류 → 모델에게 줄 문제 낱말(받은 값은 되돌리지 않는다)
_PROBLEM = {
    "missing": "MISSING",
    "extra_forbidden": "UNKNOWN_FIELD",
    "literal_error": "NOT_ALLOWED_VALUE",
    "string_type": "WRONG_TYPE",
    "int_type": "WRONG_TYPE",
    "bool_type": "WRONG_TYPE",
    "list_type": "WRONG_TYPE",
    "dict_type": "WRONG_TYPE",
    "model_type": "WRONG_TYPE",
    "greater_than_equal": "OUT_OF_RANGE",
    "string_too_long": "TOO_LONG",
    "string_too_short": "TOO_SHORT",
    "too_long": "TOO_LONG",
    "too_short": "TOO_SHORT",
    "value_error": "BAD_VALUE",
}


def _sub_model(annotation: Any) -> type[BaseModel] | None:
    """칸 타입 안의 하위 모델(`X | None` · `list[X]` · `Annotated[X, …]`를 벗긴다). 없으면 None."""
    if get_origin(annotation) is Annotated:
        return _sub_model(get_args(annotation)[0])
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for a in get_args(annotation):
        m = _sub_model(a)
        if m is not None:
            return m
    return None


@dataclass
class _Located:
    """오류 위치(`loc`)를 스키마 칸으로 따라간 결과. `path`는 스키마에 있는 앞부분 경로(지어낸 칸 이름은 담지 않는다)."""

    owner: type[BaseModel]  # 마지막 이름 칸을 가진 모델
    inner: type[BaseModel] | None  # 그 칸의 하위 모델(지어낸 칸이면 그 칸이 들어간 모델)
    path: list[Any]
    known: bool


def _locate(model: type[BaseModel], loc: tuple[Any, ...]) -> _Located:
    """중첩 인자(`update_plan.steps[]` · `reassess.change.targets[]`)에서도 칸마다 그 칸을 가진 모델을 찾는다(4-D)."""
    owner: type[BaseModel] = model
    cur: type[BaseModel] | None = model
    path: list[Any] = []
    for x in loc:
        if isinstance(x, int):
            path.append(x)
            continue
        if cur is None or str(x) not in cur.model_fields:
            return _Located(owner, cur, path, False)
        owner = cur
        cur = _sub_model(cur.model_fields[str(x)].annotation)
        path.append(x)
    return _Located(owner, cur, path, True)


def _expected(model: type[BaseModel], loc: tuple[Any, ...], err: dict[str, Any]) -> Any:
    """그 칸의 올바른 꼴: 허용 값 목록(리터럴)이나 타입 · 범위를 그 칸을 가진 모델의 스키마에서 읽는다(받은 값은 쓰지 않는다)."""
    ctx = err.get("ctx") or {}
    if err.get("type") == "literal_error" and "expected" in ctx:
        return str(ctx["expected"])
    at = _locate(model, loc)
    if err.get("type") == "extra_forbidden":
        holder = at.inner or at.owner
        return "이 칸을 빼고 다시 부른다. 쓸 수 있는 칸: " + ", ".join(holder.model_fields)
    names = [x for x in at.path if not isinstance(x, int)]
    if not at.known or not names:
        return None
    props = at.owner.model_json_schema().get("properties", {})
    prop = props.get(str(names[-1]))
    if not isinstance(prop, dict):
        return None
    keep = ("type", "enum", "minimum", "maxLength", "minLength", "anyOf")
    return {k: prop[k] for k in keep if k in prop} or None


def arg_problems(
    name: str, args: dict[str, Any] | None, model: type[BaseModel] | None = None
) -> list[dict[str, Any]]:
    """틀린 인자의 칸별 안내(4-D): `{field, problem, expected}`. 받은 값(`input`)은 담지 않고, 모델이 지어낸 없는 칸은
    이름도 되돌리지 않는다(쓸 수 있는 칸 목록만, L1-09 · 5-7). `model`을 주면 행동 도구 인자에도 쓴다."""
    model = model or impl.ARGS.get(name)
    if model is None:
        return []
    if not isinstance(args, dict):
        return [{"field": None, "problem": "WRONG_TYPE", "expected": "인자는 JSON 객체"}]
    try:
        model.model_validate(args)
    except ValidationError as e:
        out = []
        for err in e.errors()[:5]:
            loc = tuple(err.get("loc") or ())
            # 칸 이름은 스키마에 있는 것만 되돌린다: 지어낸 칸(중첩 포함)이면 그 칸이 들어간 곳까지만, 맨 위면 null
            at = _locate(model, loc)
            out.append(
                {
                    "field": ".".join(str(x) for x in at.path) or None,
                    "problem": _PROBLEM.get(str(err.get("type")), "BAD_VALUE"),
                    "expected": _expected(model, loc, err),
                }
            )
        return out
    return []


def validate_args(name: str, args: dict[str, Any] | None) -> BaseModel | InvalidArgs:
    """인자 검사. 통과하면 모델 객체, 아니면 실패 사유 코드(실행하지 않음, L1-15) — 틀린 인자면 칸별 안내(`fields`)를 함께."""
    if name not in P0_TOOLS:
        return InvalidArgs("UNKNOWN_TOOL", [{"field": None, "problem": "UNKNOWN_TOOL", "expected": list(P0_TOOLS)}])
    if args is None:
        return InvalidArgs("BAD_ARGS", arg_problems(name, args))
    try:
        return impl.ARGS[name].model_validate(args)
    except ValidationError:
        return InvalidArgs("BAD_ARGS", arg_problems(name, args))


def cache_key(ctx: RunCtx, name: str, args: BaseModel) -> str:
    """캐시 열쇠(5-7): 도구 · 정렬 인자 · 지금 범위 · 읽을 결과의 판 집합 · 화면 스냅샷 · 자료 판."""
    revs = sorted(
        # 같은 판이어도 크기 제한으로 생략된 경로가 다르면 다른 결과다(L1-17)
        (tid, canonical_json([s.revisions.model_dump(), sorted(s.omitted_paths or [])]))
        for tid, s in ctx.snapshots.items()
    )
    snap = ctx.req.page.screen.snapshot_id if ctx.req.page.screen is not None else None
    return canonical_json(
        {
            "tool": name,
            "args": args.model_dump(mode="json"),
            "scope": ctx.scope_pitch_id,
            "revs": revs if name not in ("update_plan", "search_guides", "list_pitches") else [],
            # 목록은 요약이 바뀌면(제목 · 최근 리뷰 · Take 추가 · 삭제) 다시 읽는다
            "list": _list_digest(ctx, name),
            "screen": snap if name == "get_screen" else None,
            "corpus": ctx.corpus_version if name == "search_guides" else None,
            "goal": ctx.goal_version if name == "get_goals" else None,
        }
    )


def _list_digest(ctx: RunCtx, name: str) -> str | None:
    if name == "list_pitches":
        return canonical_json([ctx.pitches_total, [p.model_dump(mode="json") for p in ctx.pitches]])
    if name == "list_takes":
        return canonical_json(
            sorted((k, v.model_dump(mode="json")) for k, v in ctx.summaries.items())
        )
    return None


def _has_omitted(data: Any) -> bool:
    """결과 안에 생략(OMITTED) 값이 하나라도 있는가. 있으면 캐시하지 않는다(L1-17 · L4-05)."""
    if isinstance(data, dict):
        if data.get("omitted_paths") or data.get("read_state") == "OMITTED":
            return True
        return any(_has_omitted(v) for v in data.values())
    if isinstance(data, list):
        return any(_has_omitted(v) for v in data)
    return data == "OMITTED"


def run_tool(
    ctx: RunCtx, name: str, args: BaseModel, *, seq: int, clock=time.monotonic
) -> tuple[ToolResult, TraceItem]:
    """검증한 인자로 도구를 실행하고(캐시 포함) 결과와 도구 기록을 돌려준다. 호출 수 예약은 loop.py 가 한다."""
    started = clock()
    key = cache_key(ctx, name, args)
    if key in ctx.tool_cache:
        res = ToolResult.from_cache(ctx.tool_cache[key])
    else:
        try:
            res = impl.RUNNERS[name](ctx, args)
        except Exception as exc:  # noqa: BLE001  # 도구 하나의 예외로 실행 전체를 끝내지 않는다(L1-19)
            res = failed_result(name, exc)
        if (
            res.ok
            and res.read_state not in ("NOT_LOADED", "OMITTED")
            and res.cacheable
            and not _has_omitted(res.data)
        ):
            ctx.tool_cache[key] = res.to_cache()
    ms = int(round((clock() - started) * 1000))
    outcome = (
        res.outcome
        if res.outcome == "CACHED"
        else ("ERROR" if not res.ok else ("EMPTY" if res.read_state == "EMPTY" else "OK"))
    )
    trace = TraceItem(
        seq=seq,
        tool=name,
        args_summary=res.args_summary[:200] or name,
        result_summary=res.result_summary[:200] or outcome,
        ms=max(0, ms),
        outcome=outcome,  # type: ignore[arg-type]
    )
    return res, trace


#: 도구 구현 안의 예상하지 못한 예외(L1-19). 모델은 다른 도구 · 대상으로 확인하거나 확인한 데까지 답한다(상황 신호 `TOOL_ERROR`)
TOOL_FAILED = "TOOL_FAILED"


def failed_result(name: str, exc: BaseException) -> ToolResult:
    """도구 구현이 예외로 끝난 호출의 결과: 그 도구의 오류(`outcome:"ERROR"`, 캐시하지 않음). 예외 글은 모델 · 도구 기록에
    싣지 않는다(사용자 글 · 기록 값을 담을 수 있다). 로그에는 예외 종류와 마지막 위치(파일:줄 · 함수)만 남긴다(turn.py `_crash_info`와 같은 모양)."""
    tb = traceback.extract_tb(exc.__traceback__)
    return ToolResult(
        name=name,
        ok=False,
        data=None,
        error={
            "code": TOOL_FAILED,
            "message": "도구를 실행하지 못했다. 다른 도구 · 대상으로 확인하거나 확인한 데까지 답한다",
        },
        outcome="ERROR",
        args_summary=PLAN_TOOL_NAME.get(name, name),
        result_summary=PLAN_RESULT["ERROR"],
        cacheable=False,
        crash={
            "cause": type(exc).__name__,
            "where": [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb[-3:]],
        },
    )


def invalid_result(name: str, code: str) -> ToolResult:
    """실행하지 않은 무효 호출의 결과(TOOL_INVALID). 모델에 사유와, 틀린 인자면 칸별 안내(틀린 칸 · 문제 · 올바른 꼴)를 돌려준다
    (4-D, Anthropic 'writing tools' — 오류에 다음 행동). 받은 값은 되돌리지 않는다."""
    error: dict[str, Any] = {"code": "TOOL_INVALID", "message": str(code)}
    fields = getattr(code, "fields", None)
    if fields:
        error["fields"] = fields
    return ToolResult(
        name=name if name in P0_TOOLS else "invalid",
        ok=False,
        data=None,
        error=error,
        outcome="ERROR",
        args_summary="무효 호출",
        result_summary=f"실행하지 않음({code})",
        cacheable=False,
    )


def result_bytes(res: ToolResult) -> int:
    return len(json.dumps(res.model_view(), ensure_ascii=False).encode("utf-8"))
