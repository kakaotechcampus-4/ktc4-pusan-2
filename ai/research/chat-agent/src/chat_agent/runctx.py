"""한 실행(run)의 작업 문맥 — 지금 범위 · 읽은 Take · 근거 등록부 · 도구 캐시 (기능 문서 5-3 · 5-6 · 5-7 · 6-5 · 6-8).

- 누가 쓰나: loop.py 가 요청 · 서명 상태로 만들고, tools/ · actions.py 가 읽고 고친다.
- 하는 일
  1. 기본 자료(`context`)에서 범위 피치 · 최근 Take 요약 · 보던 Take · 이 실행이 가져온 Take(`loaded_takes`)를 모은다.
  2. 식별자 관계를 코드로 대조한다(5-3 '식별자 대조', L1-03): 범위 피치가 아닌 Take · 다른 피치의 목표는 모델에 넘기지 않는다.
  3. 근거 지문 · 근거 표시(`basis`)에 쓸 Take 버전(`version_of`)과 이번 턴에 읽은 원천을 남긴다.
- 고칠 때: 범위는 `FETCH_PITCH` 성공으로만 바뀐다(6-5). 화면 이동 · 화면 상태로는 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import ChatAgentConfig
from .evidence import EvidenceRegistry, RecordVersion
from .schemas import Goal, PitchContext, PitchSummary, TakeSnapshot, TakeSummary, TurnRequest


@dataclass
class RunCtx:
    """한 실행의 작업 문맥."""

    cfg: ChatAgentConfig
    req: TurnRequest
    registry: EvidenceRegistry
    scope_pitch_id: str | None
    pitch: PitchContext | None
    pitches: list[PitchSummary]
    pitches_total: int
    goal: Goal | None
    goal_version: int
    #: 이 실행에서 읽을 수 있는 Take 스냅샷(보던 Take · 가져온 Take). take_id → 스냅샷
    snapshots: dict[str, TakeSnapshot] = field(default_factory=dict)
    #: 범위 피치의 최근 Take 요약(take_id → 요약)
    summaries: dict[str, TakeSummary] = field(default_factory=dict)
    corpus: Any = None
    corpus_version: str = "none"
    #: 도구 결과 캐시: 열쇠 → 결과(모델에 준 모양). state 로 이어진다
    tool_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 이번 실행에 읽은 원천(피치 · Take id). 재개 때 BE가 다시 확인할 대상과 삭제 의존(5-6)
    read_sources: set[str] = field(default_factory=set)
    #: 같은 대상을 읽다 NOT_FOUND 면 이 실행 안에서 다시 요청하지 않는다(5-7 '읽기 상태')
    not_found: set[str] = field(default_factory=set)
    #: 크기 때문에 다시 읽은 (Take, 경로)는 턴당 한 번만(6-12)
    omitted_retries: set[str] = field(default_factory=set)
    #: 화면 상태의 피치가 범위와 다른가(SCREEN 근거만 따로 표시, 5-7 '범위 대조')
    screen_other_pitch: bool = False

    @classmethod
    def build(
        cls,
        req: TurnRequest,
        *,
        cfg: ChatAgentConfig,
        registry: EvidenceRegistry,
        scope_pitch_id: str | None,
        corpus: Any,
        corpus_version: str,
        trusted_takes: set[str] | None = None,
    ) -> RunCtx:
        """`trusted_takes`: 이어 부를 때 서명 상태에 남은 '이 실행이 읽은 Take'(가져오기 행동으로 받은 오래된 Take 포함)."""
        ctx = req.context
        pitch = ctx.pitch
        # 범위와 다른 피치의 자세한 자료는 쓰지 않는다(식별자 대조)
        if pitch is not None and pitch.info.pitch_id != scope_pitch_id:
            pitch = None
        goal = ctx.conversation_goal
        rc = cls(
            cfg=cfg,
            req=req,
            registry=registry,
            scope_pitch_id=scope_pitch_id,
            pitch=pitch,
            pitches=list(ctx.pitches),
            pitches_total=ctx.pitches_total,
            goal=goal,
            goal_version=ctx.goal_version,
            corpus=corpus,
            corpus_version=corpus_version,
        )
        rc._index_pitch()
        for snap in ctx.loaded_takes:
            rc.add_snapshot(snap, trusted=snap.summary.take_id in (trusted_takes or set()))
        rc._check_screen()
        return rc

    def _check_screen(self) -> None:
        """화면 상태의 피치가 지금 범위와 다른가. 범위가 바뀌면(FETCH_PITCH) 다시 본다(L1-18)."""
        page = self.req.page
        self.screen_other_pitch = (
            page.screen is not None
            and page.pitch_id is not None
            and page.pitch_id != self.scope_pitch_id
        )

    def _index_pitch(self) -> None:
        self.summaries = {}
        if self.pitch is None:
            return
        for t in self.pitch.takes:
            self.summaries[t.take_id] = t
        if self.pitch.focus_take is not None:
            # 보던 Take 는 최근 목록과 따로 BE 가 범위 피치의 것으로 싣는다(5-3) — 오래된 Take 여도 받는다
            self.add_snapshot(self.pitch.focus_take, trusted=True)

    def add_snapshot(self, snap: TakeSnapshot, *, trusted: bool = False) -> bool:
        """스냅샷을 더한다. 범위 피치가 없거나, 그 피치의 Take 목록에 없고 이 실행이 가져온 것도 아니면 더하지 않는다.

        `trusted`: 대기 중인 가져오기 행동의 결과(대상 id 를 대조함)이거나 이 실행이 이미 읽은 Take.
        """
        if self.pitch is None:
            return False
        tid = snap.summary.take_id
        if not trusted and tid not in self.summaries:
            return False
        self.snapshots[tid] = snap
        self.summaries.setdefault(tid, snap.summary)
        return True

    def set_pitch(self, pitch: PitchContext) -> None:
        """FETCH_PITCH 성공: 범위를 바꾸고 자료를 새로 묶는다(6-5). 이전 범위의 Take 스냅샷은 버린다."""
        self.scope_pitch_id = pitch.info.pitch_id
        self.pitch = pitch
        self.snapshots = {}
        self._index_pitch()
        self._check_screen()

    # ── 버전 · 근거 표시 ─────────────────────────────────────────────
    def version_of(self, snap: TakeSnapshot) -> RecordVersion:
        """근거 지문용 Take 버전(5-2): 피치의 판단 시점 대본 · 발표자료 · 평가 기준 버전 + 그 Take 의 결과 판 · 영역별 판정 기준 판.

        피치 단위 세 값은 Take 가 만들어질 때가 아니라 지금(판단할 때) 피치의 버전이다(evidence.RecordVersion).
        피치 기본 자료가 없으면(전체 범위) Take 요약의 값을 쓴다.
        """
        s = snap.summary
        tr = snap.results.take_result or {}
        rules = {
            area: (data or {}).get("criteria_version")
            for area, data in (tr.get("criteria") or {}).items()
            if isinstance(data, dict)
        }
        revs = snap.revisions.model_dump()
        pv = self.pitch.versions if self.pitch is not None else None
        return RecordVersion(
            pitch_id=self.scope_pitch_id or "",
            script_version_id=pv.script_version_id if pv is not None else s.script_version_id,
            presentation_version_id=pv.presentation_version_id
            if pv is not None
            else s.presentation_version_id,
            standard_version=pv.standard_version if pv is not None else s.standard_version,
            take_id=s.take_id,
            results={k: v for k, v in revs.items() if v is not None},
            rule_versions=rules,
        )

    def touch(self, *ids: str | None) -> None:
        for i in ids:
            if i:
                self.read_sources.add(i)

    def basis(self, sources: list[str]) -> dict[str, Any]:
        """근거 표시(6-8): 이번 턴이 읽은 피치 · Take 마다 버전 · 결과 판 · 규칙 판, 목표 판, 자료 판."""
        pitches: list[dict[str, Any]] = []
        if self.pitch is not None:
            v = self.pitch.versions
            takes = []
            for tid in sorted(self.read_sources):
                snap = self.snapshots.get(tid)
                summ = self.summaries.get(tid)
                if summ is None:
                    continue
                rv = self.version_of(snap) if snap is not None else None
                takes.append(
                    {
                        "take_id": tid,
                        "take_number": summ.take_number,
                        "script_version_id": summ.script_version_id,
                        "presentation_version_id": summ.presentation_version_id,
                        "standard_version": summ.standard_version,
                        "results": dict(rv.results) if rv else {},
                        "rule_versions": dict(rv.rule_versions) if rv else {},
                    }
                )
            if takes or self.pitch.info.pitch_id in self.read_sources:
                pitches.append(
                    {
                        "pitch_id": self.pitch.info.pitch_id,
                        "script_version_id": v.script_version_id,
                        "presentation_version_id": v.presentation_version_id,
                        "standard_version": v.standard_version,
                        "takes": takes,
                    }
                )
        return {
            "pitches": pitches,
            "conversation_goal_version": self.goal_version,
            "corpus_version": self.corpus_version,
            "sources": sorted(set(sources)),
        }

    # ── 조회 ─────────────────────────────────────────────────────────
    def latest_completed(self) -> TakeSnapshot | None:
        """보던 Take(focus_take), 없으면 스냅샷 중 가장 최근 완료 Take(6-3 '판정 대상 Take')."""
        if self.pitch is not None and self.pitch.focus_take is not None:
            return self.pitch.focus_take
        done = [s for s in self.snapshots.values() if s.summary.status == "COMPLETED"]
        return max(done, key=lambda s: s.summary.take_number) if done else None

    def take_by_number(self, n: int) -> TakeSummary | None:
        return next((t for t in self.summaries.values() if t.take_number == n), None)
