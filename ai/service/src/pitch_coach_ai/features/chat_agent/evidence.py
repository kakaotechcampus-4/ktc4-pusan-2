"""근거 값 발급 · 모음과 근거 지문 (기능 문서 5-2 `Evidence` · `EvidenceBasis`, 6-8, 6-9).

- 누가 쓰나: tools/(도구 결과에서 근거 발급) · goals.py(계산 근거) · claims.py(판단의 근거 지문) ·
  checks.py(문장에 이은 근거 대조) · loop.py(최종 출력의 `evidence` · `basis`).
- 규칙
  1. 근거는 코드가 허용된 경로에서만 발급한다. 모델이 낸 근거 id · 값은 받지 않는다(L4-23).
  2. 근거 id 는 실행 · 대상 · 판에 묶는다: 같은 실행에서 같은 (원천, 피치, Take, 결과 종류, 판, 경로, 화면 스냅샷)이면
     같은 id 다. 그래서 캐시 결과를 다시 써도 id 가 같고, 범위를 바꿔 다른 피치의 같은 Take 번호를 읽어도 섞이지 않는다(L1-18).
  3. `COMPUTED` 근거(차이 · 거리 · 목표 판정)는 계산 종류(`calc`)와 입력 근거 id(`inputs`)를 함께 남기고,
     입력 근거도 같은 등록부에 있어야 한다. 검사기가 입력으로 다시 계산한다(L4-25).
  4. 이번 턴에 만든 근거만 지금 판단 · 문장 · 행동의 근거다. 앞 판단을 보이려고 남긴 사본(`kept`)은
     `is_current()`가 False 다(6-9 STALE_EVIDENCE).
- 지문(`fingerprint`)은 원천별 버전 묶음을 정렬한 JSON 의 SHA-256 앞 16자리다. 근거가 같은지는 이것으로 가린다(6-3 압력).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .schemas import BasisRecord, BasisScreen, BasisSource, BasisTake, Evidence, EvidenceBasis


def _h(*parts: Any, n: int = 10) -> str:
    blob = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:n]


def canonical_json(value: Any) -> str:
    """정렬 · 공백 없는 JSON. 해시 · 캐시 열쇠에 쓴다."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class RecordVersion:
    """근거 지문에 넣을 Take 하나의 버전 정보(5-2 EvidenceBasis.records[].takes[]).

    - `script_version_id` · `presentation_version_id` · `standard_version`은 **피치의 판단 시점 버전**이다
      (5-2 "기록은 피치마다 판단할 때의 대본 · 발표자료 · 평가 기준 버전"). Take가 만들어질 때의 값이 아니다.
      그래서 같은 피치의 `RecordVersion`은 이 세 값이 같아야 하고, `basis_for`는 피치마다 첫 값 하나를 쓴다.
    - Take마다 다른 것은 `results`(결과 판 번호)와 `rule_versions`(영역별 판정 기준 판)로 드러난다.
    """

    pitch_id: str
    script_version_id: str | None
    presentation_version_id: str | None
    standard_version: int | None
    take_id: str
    results: dict[str, int | None] = field(default_factory=dict)
    rule_versions: dict[str, str | None] = field(default_factory=dict)


@dataclass
class EvidenceRegistry:
    """한 실행의 근거 등록부. 상태(state)에 직렬화돼 이어 부르기에도 유지된다.

    - `items`: id → Evidence (이번 실행에서 발급)
    - `kept`: 앞서 내보낸 판단을 보이려고 남긴 사본 id(같은 실행이지만 근거가 바뀐 판단의 것). 지금 문장의 근거로 인정하지 않는다
    - `versions`: 근거 id → 그 근거의 원천 버전(지문 계산용)
    - `sources`: 자료 근거 id(`source_id`) → corpus_version
    - `depends`: 근거 id → 의존한 원천 id(피치 · Take). 원천이 지워지면 함께 버린다(5-6)
    """

    run_id: str
    items: dict[str, Evidence] = field(default_factory=dict)
    kept: set[str] = field(default_factory=set)
    versions: dict[str, RecordVersion] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    depends: dict[str, set[str]] = field(default_factory=dict)

    # ── 발급 ──────────────────────────────────────────────────────────
    def record(
        self,
        *,
        pitch_id: str,
        take_id: str | None,
        take_number: int | None,
        kind: str | None,
        revision: int | None,
        path: str,
        value: float | int | str,
        metric: str | None = None,
        slide_number: int | None = None,
        unit: str | None = None,
        rule_version: str | None = None,
        version: RecordVersion | None = None,
    ) -> Evidence:
        """저장된 기록 값의 근거(`origin: RECORD`)."""
        eid = "e-" + _h(self.run_id, "RECORD", pitch_id, take_id, kind, revision, path)
        ev = Evidence(
            evidence_id=eid,
            origin="RECORD",
            pitch_id=pitch_id,
            take_id=take_id,
            take_number=take_number,
            kind=kind,
            revision=revision,
            path=path,
            metric=metric,
            slide_number=slide_number,
            value=value,
            unit=unit,
            rule_version=rule_version,
        )
        self._put(ev, deps={pitch_id, take_id or ""})
        if version is not None:
            self.versions[eid] = version
        return ev

    def screen(
        self,
        *,
        pitch_id: str | None,
        snapshot_id: str,
        path: str,
        value: float | int | str,
        saved: bool,
        unit: str | None = None,
    ) -> Evidence:
        """화면 상태 값의 근거(`origin: SCREEN`). 공식 점수 · 완료 행동 · 목표 기준으로 쓰지 않는다."""
        eid = "e-" + _h(self.run_id, "SCREEN", pitch_id, snapshot_id, path)
        ev = Evidence(
            evidence_id=eid,
            origin="SCREEN",
            pitch_id=pitch_id,
            path=path,
            value=value,
            unit=unit,
            saved=saved,
            screen_snapshot_id=snapshot_id,
        )
        self._put(ev, deps={pitch_id} if pitch_id else set())
        return ev

    def computed(
        self,
        *,
        calc: str,
        inputs: list[str],
        value: float | int | str,
        pitch_id: str | None,
        path: str,
        metric: str | None = None,
        slide_number: int | None = None,
        unit: str | None = None,
        rule_version: str | None = None,
    ) -> Evidence:
        """코드가 계산한 값(`origin: COMPUTED`). 입력 근거가 이 등록부에 있어야 한다."""
        missing = [i for i in inputs if i not in self.items]
        if missing:
            raise KeyError(f"계산 근거의 입력이 등록부에 없다: {missing}")
        eid = "c-" + _h(self.run_id, calc, *inputs, path)
        ev = Evidence(
            evidence_id=eid,
            origin="COMPUTED",
            pitch_id=pitch_id,
            path=path,
            metric=metric,
            slide_number=slide_number,
            value=value,
            unit=unit,
            rule_version=rule_version,
            inputs=list(inputs),
            calc=calc,
        )
        deps: set[str] = set()
        for i in inputs:
            deps |= self.depends.get(i, set())
        self._put(ev, deps=deps)
        return ev

    def source(self, source_id: str, corpus_version: str) -> None:
        """자료 근거(GUIDE 문장의 `source_ids`)를 이번 실행에 읽은 것으로 남긴다."""
        self.sources[source_id] = corpus_version

    def _put(self, ev: Evidence, *, deps: set[str]) -> None:
        self.items[ev.evidence_id] = ev
        self.kept.discard(ev.evidence_id)
        self.depends[ev.evidence_id] = {d for d in deps if d}

    # ── 조회 ──────────────────────────────────────────────────────────
    def get(self, evidence_id: str) -> Evidence | None:
        return self.items.get(evidence_id)

    def is_current(self, evidence_id: str) -> bool:
        """이번 실행에서 발급했고 사본으로 낮추지 않은 근거인가(6-9 RECORD 검사)."""
        return evidence_id in self.items and evidence_id not in self.kept

    def keep_as_copy(self, ids: Iterable[str]) -> None:
        """근거가 바뀐 앞 판단의 근거를 '보이기용 사본'으로 낮춘다."""
        self.kept |= {i for i in ids if i in self.items}

    def drop_sources(self, removed: Iterable[str]) -> set[str]:
        """지워진 원천(피치 · Take id)에 기댄 근거를 모두 버리고 버린 근거 id 를 돌려준다(5-6)."""
        removed_set = set(removed)
        gone = {eid for eid, deps in self.depends.items() if deps & removed_set}
        # 계산 근거는 입력이 하나라도 버려지면 함께 버린다
        changed = True
        while changed:
            changed = False
            for eid, ev in self.items.items():
                if eid in gone or ev.inputs is None:
                    continue
                if any(i in gone for i in ev.inputs):
                    gone.add(eid)
                    changed = True
        for eid in gone:
            self.items.pop(eid, None)
            self.versions.pop(eid, None)
            self.depends.pop(eid, None)
            self.kept.discard(eid)
        return gone

    # ── 지문 ──────────────────────────────────────────────────────────
    def basis_for(
        self,
        evidence_ids: Iterable[str],
        source_ids: Iterable[str] = (),
        corpus_version: str | None = None,
    ) -> EvidenceBasis:
        """판단 · 제안이 기댄 원천별 버전 지문(5-2). COMPUTED 는 입력을 따라 원천까지 넣는다."""
        seen: set[str] = set()
        stack = list(evidence_ids)
        record_versions: list[RecordVersion] = []
        screens: set[str] = set()
        while stack:
            eid = stack.pop()
            if eid in seen:
                continue
            seen.add(eid)
            ev = self.items.get(eid)
            if ev is None:
                continue
            if ev.origin == "COMPUTED" and ev.inputs:
                stack.extend(ev.inputs)
            elif ev.origin == "SCREEN" and ev.screen_snapshot_id:
                screens.add(ev.screen_snapshot_id)
            elif eid in self.versions:
                record_versions.append(self.versions[eid])
        by_pitch: dict[str, BasisRecord] = {}
        takes_seen: dict[tuple[str, str], BasisTake] = {}
        for rv in sorted(record_versions, key=lambda r: (r.pitch_id, r.take_id)):
            rec = by_pitch.get(rv.pitch_id)
            if rec is None:
                rec = BasisRecord(
                    pitch_id=rv.pitch_id,
                    script_version_id=rv.script_version_id,
                    presentation_version_id=rv.presentation_version_id,
                    standard_version=rv.standard_version,
                    takes=[],
                )
                by_pitch[rv.pitch_id] = rec
            key = (rv.pitch_id, rv.take_id)
            bt = takes_seen.get(key)
            if bt is None:
                bt = BasisTake(take_id=rv.take_id, results={}, rule_versions={})
                takes_seen[key] = bt
                rec.takes.append(bt)
            bt.results.update({k: v for k, v in rv.results.items()})
            bt.rule_versions.update({k: v for k, v in rv.rule_versions.items()})
        sources = sorted(
            {(sid, self.sources.get(sid) or corpus_version or "") for sid in source_ids}
        )
        records = [by_pitch[k] for k in sorted(by_pitch)]
        basis_sources = [BasisSource(source_id=s, corpus_version=c) for s, c in sources]
        basis_screens = [BasisScreen(snapshot_id=s) for s in sorted(screens)]
        payload = {
            "records": [r.model_dump() for r in records],
            "sources": [s.model_dump() for s in basis_sources],
            "screens": [s.model_dump() for s in basis_screens],
        }
        fp = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]
        return EvidenceBasis(
            records=records, sources=basis_sources, screens=basis_screens, fingerprint=fp
        )

    # ── 직렬화 (state payload) ─────────────────────────────────────────
    def dump(self) -> dict[str, Any]:
        return {
            "items": [e.model_dump() for e in self.items.values()],
            "kept": sorted(self.kept),
            "versions": {k: v.__dict__ for k, v in self.versions.items()},
            "sources": dict(self.sources),
            "depends": {k: sorted(v) for k, v in self.depends.items()},
        }

    @classmethod
    def load(cls, run_id: str, data: dict[str, Any]) -> EvidenceRegistry:
        reg = cls(run_id=run_id)
        for raw in data.get("items", []):
            ev = Evidence.model_validate(raw)
            reg.items[ev.evidence_id] = ev
        reg.kept = set(data.get("kept", []))
        reg.versions = {k: RecordVersion(**v) for k, v in data.get("versions", {}).items()}
        reg.sources = dict(data.get("sources", {}))
        reg.depends = {k: set(v) for k, v in data.get("depends", {}).items()}
        return reg
