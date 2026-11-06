"""도구 8개의 인자 모양과 실행 (기능 문서 5-7). 정의 · 캐시 · 도구 기록은 `tools/__init__.py`.

- 모든 도구는 `RunCtx`(요청의 기본 자료 · 이 실행이 가져온 Take)만 읽고, 값마다 근거(`Evidence`)를 발급해
  결과 JSON 의 값 옆에 `evidence_id`를 단다. 모델은 답 문장에 그 id 를 잇는다(6-9).
- 읽기 상태: 값이 없으면 까닭을 `NOT_LOADED`(기본 자료에 없음) · `OMITTED`(크기 때문에 뺌) · `UNMEASURED`(잴 수 없음) ·
  `NOT_FOUND`(지워짐 · 없음) · `EMPTY`(실제로 빈 결과)로 나눈다. null 을 0 으로 바꾸지 않는다(L1-17).
- 범위 대조: 모든 도구가 대상 피치를 지금 범위와 대조한다. 범위 밖 Take 는 `NOT_FOUND` 로 돌려준다(L1-18).
- 도구 기록 문구(`args_summary` · `result_summary`)는 도구명 · 대상 번호 · 상태로 만든다. 사용자 원문 · 대본 · 검색 질의는 넣지 않는다.
- 고칠 때: 인자 모델은 `extra="forbid"`(모르는 인자 거부, L1-15). 결과 모양을 바꾸면 TOOL_SCHEMA_VERSION 을 올린다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..claims import ISSUE_LABEL, METRIC_LABEL, format_metric_value
from ..goals import compare_takes, record_read_value
from ..schemas import PlanStep, TakeSnapshot
from ..values import read_dimension_state, read_metric

if TYPE_CHECKING:
    from ..runctx import RunCtx
    from . import ToolResult


class _Args(BaseModel):
    # strict: "3" → 3 · "true" → True 같은 형 변환도 틀린 인자로 본다(L1-15 '형 변환 유도')
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanStepArg(_Args):
    step: str = Field(min_length=1, max_length=60)
    status: Literal["TODO", "DOING", "DONE", "SKIPPED"]
    note: str | None = Field(default=None, max_length=200)


class UpdatePlanArgs(_Args):
    steps: list[PlanStepArg] = Field(min_length=1, max_length=8)


class NoArgs(_Args):
    pass


class ListPitchesArgs(_Args):
    query: str | None = Field(default=None, max_length=100)


class ListTakesArgs(_Args):
    script_version_id: str | None = None
    completed_fully: bool | None = None
    mode: str | None = None


class TakeReportArgs(_Args):
    take_id: str
    section: Literal["dimensions", "improvements", "problem_segments", "slides", "mission_results"]
    slide_number: int | None = Field(default=None, ge=1)


class CompareArgs(_Args):
    previous_take_id: str
    current_take_id: str
    metric: str
    slide_number: int | None = Field(default=None, ge=1)


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=200)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # 공백뿐인 질의는 자료 묶음 검색이 ValueError 를 던진다 → 실행 전에 틀린 인자로 막는다(L1-15)
        if not v.strip():
            raise ValueError("빈 질의")
        return v


ARGS: dict[str, type[BaseModel]] = {
    "update_plan": UpdatePlanArgs,
    "get_screen": NoArgs,
    "list_pitches": ListPitchesArgs,
    "list_takes": ListTakesArgs,
    "get_take_report": TakeReportArgs,
    "compare_takes": CompareArgs,
    "get_goals": NoArgs,
    "search_guides": SearchArgs,
}


def _res(name: str, **kw: Any) -> ToolResult:
    from . import ToolResult

    return ToolResult(name=name, **kw)


def _not_loaded(name: str, what: str, args_summary: str) -> ToolResult:
    return _res(
        name,
        ok=False,
        data=None,
        error={
            "code": "NOT_LOADED",
            "message": f"{what} 기본 자료에 없다. fetch_take · fetch_pitch 행동으로 가져온다",
        },
        read_state="NOT_LOADED",
        args_summary=args_summary,
        result_summary="기본 자료에 없음",
        cacheable=False,
    )


def _not_found(name: str, args_summary: str) -> ToolResult:
    return _res(
        name,
        ok=False,
        data=None,
        error={"code": "NOT_FOUND", "message": "범위 피치에 없는 대상이다"},
        read_state="NOT_FOUND",
        args_summary=args_summary,
        result_summary="찾을 수 없음",
        cacheable=False,
    )


# ── update_plan ───────────────────────────────────────────────────────────


def _update_plan(ctx: RunCtx, a: UpdatePlanArgs) -> ToolResult:
    # 계획 수정 횟수(3번)는 loop.py 가 예약한다. 여기서는 모양만 확정한다
    plan = [PlanStep(step=s.step, status=s.status, note=s.note).model_dump() for s in a.steps]
    return _res(
        "update_plan",
        ok=True,
        data={"plan": plan},
        error=None,
        args_summary=f"할 일 {len(plan)}단계",
        result_summary="할 일 목록 갱신",
        cacheable=False,
    )


# ── get_screen ────────────────────────────────────────────────────────────


def _get_screen(ctx: RunCtx, _a: NoArgs) -> ToolResult:
    sc = ctx.req.page.screen
    if sc is None:
        return _res(
            "get_screen",
            ok=True,
            data={"screen": None, "saved": None},
            error=None,
            read_state="EMPTY",
            args_summary="화면 상태",
            result_summary="화면 상태 없음",
        )
    pid = ctx.req.page.pitch_id
    data: dict[str, Any] = {"name": sc.name, "snapshot_id": sc.snapshot_id}
    eids: list[str] = []
    if sc.name in ("pitch_create", "pitch_edit"):
        data["step"] = sc.step
        data["version_label"] = sc.version_label
        draft: dict[str, Any] = {}
        for k, v in sc.draft.model_dump().items():
            if v is None:
                draft[k] = None
                continue
            saved_val = getattr(sc.saved, k, None) if sc.saved is not None else None
            ev = ctx.registry.screen(
                pitch_id=pid,
                snapshot_id=sc.snapshot_id,
                path=f"screen.draft.{k}",
                value=v,
                saved=saved_val == v and sc.saved is not None,
                unit="s" if k == "time_limit_sec" else None,
            )
            eids.append(ev.evidence_id)
            draft[k] = {"value": v, "evidence_id": ev.evidence_id, "saved": ev.saved}
        data["draft"] = draft
        data["changed"] = list(sc.changed)
        saved = None
        if sc.saved is not None:
            # 저장된 값도 근거 id 를 붙여 돌려준다("저장된 값은 4분"을 근거와 이을 수 있게, 6-8 입력 중 값 · 저장 값 구분)
            saved = {}
            for k, v in sc.saved.model_dump().items():
                if v is None:
                    saved[k] = None
                    continue
                ev = ctx.registry.screen(
                    pitch_id=pid,
                    snapshot_id=sc.snapshot_id,
                    path=f"screen.saved.{k}",
                    value=v,
                    saved=True,
                    unit="s" if k == "time_limit_sec" else None,
                )
                eids.append(ev.evidence_id)
                saved[k] = {"value": v, "evidence_id": ev.evidence_id}
    else:
        for k in ("camera", "mic", "gaze_setup", "gaze_reason"):
            v = getattr(sc, k)
            if v is None:
                data[k] = None
                continue
            ev = ctx.registry.screen(
                pitch_id=pid, snapshot_id=sc.snapshot_id, path=f"screen.{k}", value=v, saved=False
            )
            eids.append(ev.evidence_id)
            data[k] = {"value": v, "evidence_id": ev.evidence_id}
        saved = None
    if ctx.screen_other_pitch:
        # 화면의 피치가 범위와 다르면 SCREEN 근거로만 따로 표시하고 기록과 합치지 않는다(L1-18)
        data["note"] = "현재 선택한 발표와 다른 화면의 정보예요."
    return _res(
        "get_screen",
        ok=True,
        data={"screen": data, "saved": saved},
        error=None,
        evidence_ids=eids,
        args_summary="화면 상태",
        result_summary=f"{sc.name} 화면",
    )


# ── list_pitches · list_takes ─────────────────────────────────────────────


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def _list_pitches(ctx: RunCtx, a: ListPitchesArgs) -> ToolResult:
    items = ctx.pitches
    if a.query:
        q = _norm(a.query)
        items = [p for p in items if q in _norm(p.title)]
    out = []
    eids: list[str] = []
    for p in items:
        row: dict[str, Any] = {"pitch_id": p.pitch_id, "title": p.title, "take_count": p.take_count}
        lr = p.last_review
        if lr is not None:
            # 우선 개선 차원도 근거로 낸다(피치마다 따로 말하는 전체 범위 답이 근거 없는 문장으로 빠지지 않게, 6-5)
            dims = []
            for d in lr.priority_dimensions:
                dev = ctx.registry.record(
                    pitch_id=p.pitch_id,
                    take_id=lr.take_id,
                    take_number=lr.take_number,
                    kind="REVIEW",
                    revision=None,
                    path=f"pitches[pitch_id={p.pitch_id}].last_review.dimensions.{d}",
                    value="PRIORITY",
                )
                eids.append(dev.evidence_id)
                dims.append({"dimension": d, "evidence_id": dev.evidence_id})
            row["last_review"] = {
                "take_number": lr.take_number,
                "priority_dimensions": dims,
            }
            if lr.score is not None:
                ev = ctx.registry.record(
                    pitch_id=p.pitch_id,
                    take_id=lr.take_id,
                    take_number=lr.take_number,
                    kind="REVIEW",
                    revision=None,
                    path=f"pitches[pitch_id={p.pitch_id}].last_review.score",
                    value=lr.score,
                    unit="score",
                )
                eids.append(ev.evidence_id)
                row["last_review"]["score"] = {"value": lr.score, "evidence_id": ev.evidence_id}
        out.append(row)
    data = {"items": out, "total": ctx.pitches_total, "shown": len(ctx.pitches)}
    return _res(
        "list_pitches",
        ok=True,
        data=data,
        error=None,
        evidence_ids=eids,
        # 지워진 피치가 이어 부르기에서 removed_sources 로 오면 이 캐시도 버린다
        target_ids=[r["pitch_id"] for r in out if isinstance(r.get("pitch_id"), str)]
        + [
            p.last_review.take_id
            for p in items
            if p.last_review is not None and p.last_review.take_id
        ],
        read_state=None if out else "EMPTY",
        args_summary="피치 목록" + (" 이름 검색" if a.query else ""),
        result_summary=f"피치 {len(out)}개(전체 {ctx.pitches_total}개)",
    )


def _list_takes(ctx: RunCtx, a: ListTakesArgs) -> ToolResult:
    if ctx.pitch is None:
        return _not_loaded("list_takes", "범위 피치가", "Take 목록")
    rows = []
    eids: list[str] = []
    for t in sorted(ctx.summaries.values(), key=lambda x: x.take_number, reverse=True):
        if a.script_version_id is not None and t.script_version_id != a.script_version_id:
            continue
        if a.completed_fully is not None and t.completed_fully is not a.completed_fully:
            continue
        if a.mode is not None and t.mode != a.mode:
            continue
        row: dict[str, Any] = {
            "take_id": t.take_id,
            "take_number": t.take_number,
            "status": t.status,
            "mode": t.mode,
            "script_mode": t.script_mode,
            "completed_fully": t.completed_fully,
            "script_version_id": t.script_version_id,
            "standard_version": t.standard_version,
            "completed_at": t.completed_at,
            "loaded": t.take_id in ctx.snapshots,
        }
        if t.review is not None and t.review.score is not None:
            ev = ctx.registry.record(
                pitch_id=ctx.scope_pitch_id or "",
                take_id=t.take_id,
                take_number=t.take_number,
                kind="REVIEW",
                revision=t.review.revision,
                path="review.score",
                value=t.review.score,
                unit="score",
            )
            eids.append(ev.evidence_id)
            row["review"] = {
                "score": {"value": t.review.score, "evidence_id": ev.evidence_id},
                "priority_dimensions": [
                    d for d, s in t.review.dimensions.items() if s == "PRIORITY"
                ],
            }
        rows.append(row)
        ctx.touch(t.take_id)
    ctx.touch(ctx.scope_pitch_id)
    return _res(
        "list_takes",
        ok=True,
        data={
            "items": rows,
            "pitch_title": ctx.pitch.info.title,
            "script_version_id": ctx.pitch.versions.script_version_id,
        },
        error=None,
        evidence_ids=eids,
        read_state=None if rows else "EMPTY",
        target_ids=[r["take_id"] for r in rows],
        args_summary="Take 목록",
        result_summary=f"Take {len(rows)}개",
    )


# ── get_take_report ───────────────────────────────────────────────────────


def _snapshot_for(ctx: RunCtx, take_id: str) -> TakeSnapshot | str:
    if take_id in ctx.not_found:
        return "NOT_FOUND"
    snap = ctx.snapshots.get(take_id)
    if snap is not None:
        return snap
    if take_id in ctx.summaries:
        return "NOT_LOADED"
    return "NOT_FOUND"


def _num(ctx: RunCtx, snap: TakeSnapshot, rv: Any) -> dict[str, Any]:
    """읽은 값 하나를 결과 항목으로: 값 · 화면 표시 · 근거 id, 또는 읽기 상태."""
    if not rv.ok:
        return {"metric": rv.metric, "read_state": rv.state}
    eid = record_read_value(
        ctx.registry, ctx.scope_pitch_id or "", snap, rv, ctx.cfg, ctx.version_of
    )
    item: dict[str, Any] = {"metric": rv.metric, "value": rv.value, "evidence_id": eid}
    if rv.metric in METRIC_LABEL:
        # 답 문장에는 지표 키(audience_ratio) 대신 이 이름을 쓴다(답 검사의 지표 이름 사전에 있는 이름)
        item["label"] = METRIC_LABEL[rv.metric]
    if isinstance(rv.value, (int, float)) and rv.metric in ctx.cfg.metric_units:
        item["display"] = format_metric_value(rv.metric, rv.value, ctx.cfg)
    return item


_SLIDE_METRICS = (
    "audience_ratio",
    "script_ratio",
    "cpm",
    "filler_per_min",
    "voice_diff_db",
    "slide_duration_ms",
)


def _take_report(ctx: RunCtx, a: TakeReportArgs) -> ToolResult:
    summ = f"Take 리포트 · {a.section}"
    snap = _snapshot_for(ctx, a.take_id)
    if snap == "NOT_LOADED":
        return _not_loaded("get_take_report", "이 Take 결과가", summ)
    if isinstance(snap, str):
        ctx.not_found.add(a.take_id)
        return _not_found("get_take_report", summ)
    tn = snap.summary.take_number
    summ = f"Take {tn} · {a.section}" + (f" · {a.slide_number}장" if a.slide_number else "")
    ctx.touch(snap.summary.take_id, ctx.scope_pitch_id)
    review = snap.results.review
    tr = snap.results.take_result
    items: list[dict[str, Any]] = []
    eids: list[str] = []
    state: str | None = None
    note: str | None = None
    if a.section == "dimensions":
        if review is None:
            state, note = (
                "EMPTY",
                "리뷰가 없어 점수와 미션은 확인할 수 없지만 측정값은 볼 수 있어요.",
            )
        else:
            for dim in ctx.cfg.dimensions:
                rv = read_dimension_state(snap, dim)
                if rv.ok:
                    eid = record_read_value(
                        ctx.registry, ctx.scope_pitch_id or "", snap, rv, ctx.cfg, ctx.version_of
                    )
                    eids.append(eid)
                    items.append({"dimension": dim, "state": rv.value, "evidence_id": eid})
                elif rv.value == "NOT_EVALUABLE":
                    # 리뷰가 '평가할 수 없음'으로 적은 차원도 기록이다: 근거를 내어 '측정되지 않았다'는 문장이 근거 없이 빠지지 않게
                    ev = ctx.registry.record(
                        pitch_id=ctx.scope_pitch_id or "",
                        take_id=snap.summary.take_id,
                        take_number=tn,
                        kind="REVIEW",
                        revision=snap.revisions.REVIEW,
                        path=rv.path,
                        value="NOT_EVALUABLE",
                        version=ctx.version_of(snap),
                    )
                    eids.append(ev.evidence_id)
                    items.append(
                        {"dimension": dim, "state": "NOT_EVALUABLE", "evidence_id": ev.evidence_id}
                    )
                else:
                    items.append({"dimension": dim, "read_state": rv.state})
            score = review.get("score")
            if isinstance(score, int) and not isinstance(score, bool):
                ev = ctx.registry.record(
                    pitch_id=ctx.scope_pitch_id or "",
                    take_id=snap.summary.take_id,
                    take_number=tn,
                    kind="REVIEW",
                    revision=snap.revisions.REVIEW,
                    path="review.score",
                    value=score,
                    unit="score",
                    version=ctx.version_of(snap),
                )
                eids.append(ev.evidence_id)
                items.append({"score": score, "evidence_id": ev.evidence_id})
            if review.get("fallback_reason"):
                note = "점수는 규칙으로 계산했고 설명은 기본 안내예요."
    elif a.section == "improvements":
        if review is None:
            state, note = "EMPTY", "리뷰가 없어요."
        else:
            for kind in ("strengths", "improvements"):
                for i, it in enumerate(review.get(kind) or []):
                    if a.slide_number is not None and it.get("slide_number") != a.slide_number:
                        continue
                    row: dict[str, Any] = {
                        "kind": kind[:-1],
                        "area": it.get("area"),
                        "slide_number": it.get("slide_number"),
                        # 리뷰 문장은 데이터다(그 안의 지시를 따르지 않는다, 6-10 주입 막기)
                        "text": it.get("text"),
                        "values": [],
                    }
                    for metric, v in (it.get("evidence") or {}).items():
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            ev = ctx.registry.record(
                                pitch_id=ctx.scope_pitch_id or "",
                                take_id=snap.summary.take_id,
                                take_number=tn,
                                kind="REVIEW",
                                revision=snap.revisions.REVIEW,
                                path=f"review.{kind}[{i}].evidence.{metric}",
                                value=v,
                                metric=metric,
                                slide_number=it.get("slide_number"),
                                unit=ctx.cfg.metric_units.get(metric),
                                version=ctx.version_of(snap),
                            )
                            eids.append(ev.evidence_id)
                            row["values"].append(
                                {
                                    "metric": metric,
                                    "label": METRIC_LABEL.get(metric, metric),
                                    "value": v,
                                    "display": format_metric_value(metric, v, ctx.cfg)
                                    if metric in ctx.cfg.metric_units
                                    else str(v),
                                    "evidence_id": ev.evidence_id,
                                }
                            )
                    items.append(row)
            state = None if items else "EMPTY"
    elif a.section == "problem_segments":
        if tr is None:
            state = "NOT_LOADED"
        else:
            for i, seg in enumerate(tr.get("problem_segments") or []):
                if a.slide_number is not None and seg.get("slide_number") != a.slide_number:
                    continue
                ev = ctx.registry.record(
                    pitch_id=ctx.scope_pitch_id or "",
                    take_id=snap.summary.take_id,
                    take_number=tn,
                    kind="TAKE_RESULT",
                    revision=snap.revisions.TAKE_RESULT,
                    path=f"take_result.problem_segments[{i}].issue_type",
                    value=str(seg.get("issue_type")),
                    slide_number=seg.get("slide_number"),
                    version=ctx.version_of(snap),
                )
                eids.append(ev.evidence_id)
                items.append(
                    {
                        "issue_type": seg.get("issue_type"),
                        "label": ISSUE_LABEL.get(str(seg.get("issue_type")), seg.get("issue_type")),
                        "area": seg.get("area"),
                        "slide_number": seg.get("slide_number"),
                        "start_ms": seg.get("start_ms"),
                        "end_ms": seg.get("end_ms"),
                        # reliable:false 구간은 문제로 세지 않는다(6-3 판정 표 머리)
                        "reliable": seg.get("reliable"),
                        "evidence_id": ev.evidence_id,
                    }
                )
            state = None if items else "EMPTY"
    elif a.section == "slides":
        if tr is None:
            state = "NOT_LOADED"
        else:
            metrics = _SLIDE_METRICS
            if a.slide_number is None:
                take_vals = [
                    _num(ctx, snap, read_metric(snap, m, None, ctx.cfg))
                    for m in (*metrics[:-1], "duration_ms")
                ]
                items.append({"scope": "take", "values": take_vals})
                n_slides = len(((tr.get("areas") or {}).get("TIME") or {}).get("slides") or [])
                for sn in range(1, n_slides + 1):
                    vals = [
                        _num(ctx, snap, read_metric(snap, m, sn, ctx.cfg))
                        for m in ("audience_ratio", "cpm")
                    ]
                    items.append({"slide_number": sn, "values": vals})
            else:
                vals = [
                    _num(ctx, snap, read_metric(snap, m, a.slide_number, ctx.cfg)) for m in metrics
                ]
                items.append({"slide_number": a.slide_number, "values": vals})
            for it in items:
                for v in it.get("values", []):
                    if "evidence_id" in v:
                        eids.append(v["evidence_id"])
            vals = [v for it in items for v in it.get("values", [])]
            if all("read_state" in v for v in vals):
                # 크기 때문에 뺀 값(OMITTED)을 '측정 안 됨'으로 합치지 않는다(L1-17). OMITTED 는 캐시하지 않는다
                omitted_vals = any(v["read_state"] == "OMITTED" for v in vals)
                state = "OMITTED" if omitted_vals else "UNMEASURED"
    elif a.section == "mission_results":
        missions = ctx.pitch.missions if ctx.pitch is not None else []
        results = (review or {}).get("mission_results") or []
        for i, m in enumerate(results):
            row = {
                k: m.get(k)
                for k in ("mission_id", "status", "metric", "operator", "slide_number", "target")
            }
            obs = m.get("observed")
            if isinstance(obs, (int, float)) and not isinstance(obs, bool):
                ev = ctx.registry.record(
                    pitch_id=ctx.scope_pitch_id or "",
                    take_id=snap.summary.take_id,
                    take_number=tn,
                    kind="REVIEW",
                    revision=snap.revisions.REVIEW,
                    path=f"review.mission_results[{i}].observed",
                    value=obs,
                    metric=m.get("metric"),
                    slide_number=m.get("slide_number"),
                    unit=ctx.cfg.metric_units.get(m.get("metric") or ""),
                    version=ctx.version_of(snap),
                )
                eids.append(ev.evidence_id)
                row["observed"] = {"value": obs, "evidence_id": ev.evidence_id}
            items.append(row)
        state = None if results else "EMPTY"
    data: dict[str, Any] = {
        "take_id": snap.summary.take_id,
        "take_number": tn,
        "section": a.section,
        "items": items,
    }
    if a.section == "mission_results":
        # 지금 진행 중인 미션은 결과 행(items)과 모양이 달라 따로 둔다(items 는 한 모양, #883 리뷰)
        data["active_missions"] = missions
    if note:
        data["note"] = note
    omitted = [
        p
        for p in snap.omitted_paths
        if a.section in ("slides", "improvements") or "script_coverage" in p
    ]
    if omitted:
        data["omitted_paths"] = omitted
    # 읽은 원천의 판을 남긴다: 리뷰에서 읽은 절은 REVIEW, Take 결과에서 읽은 절은 TAKE_RESULT(#883 리뷰)
    source_revision = (
        snap.revisions.TAKE_RESULT
        if a.section in ("slides", "problem_segments")
        else snap.revisions.REVIEW
    )
    return _res(
        "get_take_report",
        ok=state != "NOT_LOADED",
        data=data,
        error=None if state != "NOT_LOADED" else {"code": "NOT_LOADED", "message": "결과가 없다"},
        evidence_ids=eids,
        read_state=state,
        target_ids=[snap.summary.take_id],
        revisions={snap.summary.take_id: source_revision},
        args_summary=summ,
        result_summary=f"{len(items)}개 항목" if items else (state or "OK"),
    )


# ── compare_takes ─────────────────────────────────────────────────────────


def _compare(ctx: RunCtx, a: CompareArgs) -> ToolResult:
    # 도구 기록에는 아는 지표 이름만 쓴다(모델이 metric 에 사용자 원문을 넣어도 진행 줄에 나오지 않게, L1-09)
    metric_label = a.metric if a.metric in ctx.cfg.metric_units else "모르는 지표"
    summ = "Take 비교 · " + metric_label + (f" · {a.slide_number}장" if a.slide_number else "")
    if a.metric not in ctx.cfg.metric_units:
        # 모르는 지표는 실행하지 않고, 받은 글을 결과에 되돌려 주지 않는다(5-7 엄격한 인자 · L1-09)
        return _res(
            "compare_takes",
            ok=False,
            data=None,
            error={
                "code": "BAD_ARGS",
                "message": "모르는 지표다. 지표 이름은 도구 설명의 목록에서 고른다",
            },
            args_summary=summ,
            result_summary="모르는 지표",
        )
    snaps = []
    for tid in (a.previous_take_id, a.current_take_id):
        s = _snapshot_for(ctx, tid)
        if s == "NOT_LOADED":
            return _not_loaded("compare_takes", "비교할 Take 결과가", summ)
        if isinstance(s, str):
            ctx.not_found.add(tid)
            return _not_found("compare_takes", summ)
        snaps.append(s)
    prev, cur = snaps
    summ = f"Take {prev.summary.take_number} · {cur.summary.take_number} · {metric_label}" + (
        f" · {a.slide_number}장" if a.slide_number else ""
    )
    ctx.touch(prev.summary.take_id, cur.summary.take_id, ctx.scope_pitch_id)
    cmp = compare_takes(
        prev,
        cur,
        a.metric,
        a.slide_number,
        pitch_id=ctx.scope_pitch_id or "",
        registry=ctx.registry,
        version_of=ctx.version_of,
        cfg=ctx.cfg,
    )
    data = cmp.model_dump()
    # 근거 id 마다 역할을 붙인다: 모델이 결론(CONCLUSION)에는 outcome, 값 문장에는 previous · current 를 고르게(6-9)
    roles: dict[str, str] = {}
    for eid in cmp.evidence_ids:
        ev = ctx.registry.get(eid)
        if ev is None:
            continue
        if ev.origin == "COMPUTED":
            role = (ev.calc or "computed").lower()
            if role == "distance":
                role = (
                    "previous_distance"
                    if prev.summary.take_id in _input_takes(ctx, ev)
                    and cur.summary.take_id not in _input_takes(ctx, ev)
                    else "current_distance"
                )
        elif ev.take_id == prev.summary.take_id:
            role = (
                "previous" if ev.metric == a.metric else "previous_" + (ev.kind or "input").lower()
            )
        elif ev.take_id == cur.summary.take_id:
            role = "current" if ev.metric == a.metric else "current_" + (ev.kind or "input").lower()
        else:
            role = (ev.kind or "input").lower()
        roles.setdefault(role, eid)
    data["evidence_by_role"] = roles
    if cmp.comparable and a.metric in ctx.cfg.metric_units:
        for k in ("previous", "current"):
            v = data.get(k)
            if isinstance(v, (int, float)):
                data[f"{k}_display"] = format_metric_value(a.metric, v, ctx.cfg)
    result = f"{a.metric} {cmp.outcome}" if cmp.comparable else f"비교할 수 없음({cmp.reason})"
    return _res(
        "compare_takes",
        ok=True,
        data=data,
        error=None,
        evidence_ids=list(cmp.evidence_ids),
        target_ids=[prev.summary.take_id, cur.summary.take_id],
        revisions={
            prev.summary.take_id: prev.revisions.TAKE_RESULT,
            cur.summary.take_id: cur.revisions.TAKE_RESULT,
        },
        args_summary=summ,
        result_summary=result,
    )


def _input_takes(ctx: RunCtx, ev: Any) -> set[str]:
    out: set[str] = set()
    for i in ev.inputs or []:
        e = ctx.registry.get(i)
        if e is not None and e.take_id:
            out.add(e.take_id)
    return out


# ── get_goals ─────────────────────────────────────────────────────────────


def _get_goals(ctx: RunCtx, _a: NoArgs) -> ToolResult:
    missions = list(ctx.pitch.missions) if ctx.pitch is not None else []
    crit = ctx.pitch.criteria if ctx.pitch is not None else None
    targets = [t.model_dump() for t in crit.targets] if crit is not None else []
    goal = ctx.goal
    eids: list[str] = []
    goal_view = None
    if goal is not None and goal.pitch_id == ctx.scope_pitch_id:
        ev = ctx.registry.record(
            pitch_id=goal.pitch_id,
            take_id=goal.base.take_id,
            take_number=goal.base.take_number,
            kind="GOAL",
            revision=goal.version,
            path="conversation_goal.base.value",
            value=goal.base.value,
            metric=goal.metric,
            slide_number=goal.slide_number,
            unit=ctx.cfg.metric_units.get(goal.metric),
            rule_version=goal.goal_rule_version,
        )
        eids.append(ev.evidence_id)
        goal_view = {
            "metric": goal.metric,
            "slide_number": goal.slide_number,
            "operator": goal.operator,
            "base_take_number": goal.base.take_number,
            "base_value": {"value": goal.base.value, "evidence_id": ev.evidence_id},
            "status": goal.status,
            "version": goal.version,
        }
    elif goal is not None:
        goal_view = {
            "note": "다른 피치의 대화 목표다",
            "pitch_id": goal.pitch_id,
            "status": goal.status,
        }
    return _res(
        "get_goals",
        ok=True,
        data={
            "missions": {"source": "리뷰가 정한 미션", "items": missions},
            "criteria_targets": {"source": "평가 기준", "items": targets},
            "conversation_goal": {"source": "대화에서 정한 목표", "goal": goal_view},
        },
        error=None,
        evidence_ids=eids,
        read_state=None if (missions or targets or goal_view) else "EMPTY",
        args_summary="목표 · 미션",
        result_summary=f"미션 {len(missions)} · 기준 {len(targets)} · 대화 목표 {'있음' if goal_view else '없음'}",
    )


# ── search_guides ─────────────────────────────────────────────────────────


def _search(ctx: RunCtx, a: SearchArgs) -> ToolResult:
    summ = f"자료 검색(질의 {len(a.query)}자)"
    corpus = ctx.corpus
    if corpus is None:
        # 자료 묶음 검사에 실패하면 검색만 준비 실패다(기록 답은 계속, 6-10 · L1-21).
        # 자료 근거(GUIDE) 답은 막고, 결과 0건과 같이 일반 조언 규칙을 따른다(루프가 찾은 것으로 센다)
        return _res(
            "search_guides",
            ok=False,
            data=None,
            error={
                "code": "CORPUS_UNAVAILABLE",
                "message": "자료 묶음을 쓸 수 없다. 자료 근거 답은 하지 않고, 자료가 없을 때처럼 일반 조언 규칙을 따른다",
            },
            args_summary=summ,
            result_summary="자료 준비 실패",
            cacheable=False,
        )
    screen = ctx.req.page.screen
    screen_key = None
    if screen is not None:
        screen_key = screen.name + (f":{screen.step}" if getattr(screen, "step", None) else "")
    hits = corpus.search(a.query, screen=screen_key)
    items = []
    sources = []
    for h in hits:
        card = h.card
        ctx.registry.source(card.source_id, ctx.corpus_version)
        sources.append(card.source_id)
        items.append(card.model_dump())
    return _res(
        "search_guides",
        ok=True,
        data={"items": items, "corpus_version": ctx.corpus_version},
        error=None,
        read_state=None if items else "EMPTY",
        args_summary=summ,
        result_summary=f"자료 {len(items)}개",
        source_ids=sources,
    )


RUNNERS: dict[str, Callable[..., ToolResult]] = {
    "update_plan": _update_plan,
    "get_screen": _get_screen,
    "list_pitches": _list_pitches,
    "list_takes": _list_takes,
    "get_take_report": _take_report,
    "compare_takes": _compare,
    "get_goals": _get_goals,
    "search_guides": _search,
}
