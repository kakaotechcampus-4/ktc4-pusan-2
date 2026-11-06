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
- 고칠 때: 도구를 더하거나 인자 · 결과 모양을 바꾸면 `version.TOOL_SCHEMA_VERSION`을 올린다(진행 중 실행은 판을 고정, 5-6).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..evidence import canonical_json
from ..runctx import RunCtx
from ..schemas import TraceItem
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
    "update_plan": "할 일 목록(1~8단계)을 만들거나 고친다. 단계 상태는 TODO · DOING · DONE · SKIPPED.",
    "get_screen": "대화창을 연 화면의 상태(입력 중인 값과 저장된 값)를 읽는다. 피치 만들기 · 수정 · 장치 점검 화면에서만 값이 있다.",
    "list_pitches": "내 피치 목록에서 이름(query)으로 찾는다. query 가 null 이면 전체 목록 요약.",
    "list_takes": "지금 범위 피치의 최근 Take 요약을 조건으로 거른다(대본 버전 · 끝까지 한 발표 · 모드).",
    "get_take_report": "Take 하나의 결과에서 필요한 부분만 읽는다. section: dimensions(차원 상태 · 점수) · improvements(강점 · 개선점) · problem_segments(문제 구간) · slides(장별 지표, slide_number 로 한 장만) · mission_results(미션 결과). 기본 자료에 없으면 NOT_LOADED → fetch_take 행동을 쓴다.",
    "compare_takes": "같은 피치의 두 Take 를 같은 지표로 비교한다. 비교할 수 없으면 이유만 돌려준다. 결론(좋아짐 · 나빠짐)은 결과의 outcome 을 쓴다.",
    "get_goals": "공식 미션 · 평가 기준 목표 · 대화 목표를 출처와 함께 읽는다.",
    "search_guides": "발표법 · 화면별 사용법 자료에서 찾는다(최대 5개). 자료에 없는 발표법은 답하지 않는다.",
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


def validate_args(name: str, args: dict[str, Any] | None) -> BaseModel | str:
    """인자 검사. 통과하면 모델 객체, 아니면 실패 사유 코드(실행하지 않음, L1-15)."""
    if name not in P0_TOOLS:
        return "UNKNOWN_TOOL"
    if args is None:
        return "BAD_ARGS"
    try:
        return impl.ARGS[name].model_validate(args)
    except ValidationError:
        return "BAD_ARGS"


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
        res = impl.RUNNERS[name](ctx, args)
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


def invalid_result(name: str, code: str) -> ToolResult:
    """실행하지 않은 무효 호출의 결과(TOOL_INVALID). 모델에 사유만 돌려준다."""
    return ToolResult(
        name=name if name in P0_TOOLS else "invalid",
        ok=False,
        data=None,
        error={"code": "TOOL_INVALID", "message": code},
        outcome="ERROR",
        args_summary="무효 호출",
        result_summary=f"실행하지 않음({code})",
        cacheable=False,
    )


def result_bytes(res: ToolResult) -> int:
    return len(json.dumps(res.model_view(), ensure_ascii=False).encode("utf-8"))
