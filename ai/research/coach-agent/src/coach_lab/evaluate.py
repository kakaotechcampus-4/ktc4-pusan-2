"""리뷰 근거 실험 — 코치가 만든 리뷰 근거를 정답과 비교해 채점하고, 설정 변형을 비교한다.

    python -m coach_lab.evaluate                      # clean 1회 + noisy · harsh 각 5 seed
    python -m coach_lab.evaluate --seeds 10 --sweep   # seed 를 늘리고 병합 간격 × 지연 보정 격자도
    python -m coach_lab.evaluate --noise noisy        # 잡음 수준 하나만

리뷰 설정(config.review)은 실시간 판단에 쓰이지 않습니다. 그래서 발표는 (시나리오 × 잡음 × seed)마다
한 번만 재생하고, 변형마다 리뷰 근거만 다시 만들어 채점합니다.
변형 사이의 차이는 리뷰 근거 규칙에서만 옵니다.

채점 항목 (리뷰 에이전트가 쓰는 것 기준)

| 지표 | 뜻 |
|---|---|
| 구간 재현율 | 센서가 볼 수 있던 실제 문제 구간 중, 리뷰 근거가 문제로 말한 비율 |
| 구간 정밀도 | 리뷰 근거가 문제로 말한 구간 중, 실제로 (볼 수 있던) 문제였던 비율 |
| 근거 없는 지적 | 실제 문제가 아니었거나 센서를 믿을 수 없던 곳을 문제로 말한 수 (Take 당) |
| 구간 IoU · 시작 오차 | 맞춘 구간의 시간 겹침, "몇 분 몇 초부터"의 오차 |
| 조각남 | 실제 문제 하나를 몇 조각으로 나눠 말했나 (1 이 이상) |
| 장별 오차 | 장별 대본 응시 · CPM · 시간의 평균 절대 오차 |
| 미션 · 영역 상태 · 기억 비교 정확도 | 정답 판정과 같은 비율 |
| 1순위 문제 일치 | 다음에 먼저 고칠 것(영역)이 정답과 같은 비율 |
| 거짓 강점 | 실제로 문제가 있던 영역을 강점(문제 없음)이라고 한 수 |
| 효과 판정 정확도 | 개입 효과(EFFECTIVE / INEFFECTIVE)가 발표자의 실제 반응과 같은 비율 |
| 불필요한 개입률 | 최근 20초 안에 실제 문제가 없던 개입 비율 (실시간) |
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any

from coach import build_review_evidence
from coach.config import load_config
from coach.schemas import CoachReviewEvidence
from coach.version import POLICY_VERSION
from coach.vocab import FeedbackType, Issue, SegmentHint, TypeStatus

from .paths import OUTPUTS_DIR, SCENARIOS_DIR
from .simulator import NOISE_PRESETS, RunResult, Scenario, run, scenario_config
from .truth import (
    PROBLEM_TYPES,
    TruthInterval,
    truth_aggs,
    truth_assessment,
    truth_outcome,
)

#: 비교할 리뷰 근거 변형. A 가 첫 구현(v1.0)과 같은 리뷰 근거 규칙이다
VARIANTS: dict[str, dict[str, Any]] = {
    "A 기준선(v1.0)": {
        "review": {"exclude_unreliable": False, "lag_compensation": False, "merge_gap_ms": 0}
    },
    "B +신뢰도 필터": {
        "review": {"exclude_unreliable": True, "lag_compensation": False, "merge_gap_ms": 0}
    },
    "C +구간 병합": {
        "review": {"exclude_unreliable": True, "lag_compensation": False, "merge_gap_ms": 10_000}
    },
    "D +지연 보정": {
        "review": {"exclude_unreliable": True, "lag_compensation": True, "merge_gap_ms": 10_000}
    },
}


@dataclass
class Score:
    """한 변형의 채점 누적. 비율은 전체 개수로 모아서(micro) 낸다."""

    truth_intervals: int = 0
    truth_hit: int = 0
    claims: int = 0
    claims_supported: int = 0
    unsupported: int = 0
    takes: int = 0
    iou: list[float] = field(default_factory=list)
    onset_err_ms: list[float] = field(default_factory=list)
    fragments: list[int] = field(default_factory=list)
    slide_err: dict[str, list[float]] = field(default_factory=dict)
    missions: list[bool] = field(default_factory=list)
    type_status: list[bool] = field(default_factory=list)
    memory: list[bool] = field(default_factory=list)
    top_type: list[bool] = field(default_factory=list)
    next_mission: list[bool] = field(default_factory=list)
    false_strengths: int = 0
    outcomes: list[bool] = field(default_factory=list)
    rt_interventions: int = 0
    rt_false: int = 0
    #: --explain 로 볼 틀린 사례
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def rate(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        def mean(xs: list[Any]) -> float | None:
            return round(fmean(xs), 4) if xs else None

        recall = rate(self.truth_hit, self.truth_intervals)
        precision = rate(self.claims_supported, self.claims)
        f1 = (
            round(2 * recall * precision / (recall + precision), 4)
            if recall and precision
            else None
        )
        return {
            "segment_recall": recall,
            "segment_precision": precision,
            "segment_f1": f1,
            "unsupported_claims_per_take": rate(self.unsupported, self.takes),
            "segment_iou": mean(self.iou),
            "onset_error_s": round(fmean(self.onset_err_ms) / 1000, 2)
            if self.onset_err_ms
            else None,
            "fragments_per_problem": mean(self.fragments),
            "slide_mae": {k: mean(v) for k, v in sorted(self.slide_err.items())},
            "mission_accuracy": mean(self.missions),
            "type_status_accuracy": mean(self.type_status),
            "memory_accuracy": mean(self.memory),
            "top_issue_type_agreement": mean(self.top_type),
            "next_mission_agreement": mean(self.next_mission),
            "false_strengths_per_take": rate(self.false_strengths, self.takes),
            "outcome_accuracy": mean(self.outcomes),
            "false_intervention_rate": rate(self.rt_false, self.rt_interventions),
        }


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def score_segments(score: Score, review: CoachReviewEvidence, truth: list[TruthInterval]) -> None:
    claims = [
        s for s in review.segments if s.type in PROBLEM_TYPES and s.hint != SegmentHint.UNRELIABLE
    ]
    seen = [t for t in truth if t.observable]
    score.truth_intervals += len(seen)
    score.claims += len(claims)
    for c in claims:
        if any(
            t.type == c.type and _overlap(c.onset_ms, c.offset_ms, t.start_ms, t.end_ms) > 0
            for t in seen
        ):
            score.claims_supported += 1
        else:
            score.unsupported += 1
            score.notes.append(
                f"근거 없는 지적 {review.take_id}: {c.type.value} 장{c.slide_number}"
                f" {c.onset_ms // 1000}~{c.offset_ms // 1000}s (신뢰도 {c.reliability})"
            )
    for t in seen:
        hits = [
            c
            for c in claims
            if c.type == t.type and _overlap(c.onset_ms, c.offset_ms, t.start_ms, t.end_ms) > 0
        ]
        if not hits:
            score.notes.append(
                f"놓친 구간 {review.take_id}: {t.type.value} 장{t.slide_number}"
                f" {t.start_ms // 1000}~{t.end_ms // 1000}s"
            )
            continue
        score.truth_hit += 1
        inter = sum(_overlap(c.onset_ms, c.offset_ms, t.start_ms, t.end_ms) for c in hits)
        union = t.span_ms + sum(c.offset_ms - c.onset_ms for c in hits) - inter
        score.iou.append(inter / union if union else 0.0)
        score.onset_err_ms.append(abs(min(c.onset_ms for c in hits) - t.start_ms))
        score.fragments.append(len(hits))


@dataclass
class Truth:
    """한 재생의 정답. 리뷰 근거 변형과 무관해 재생마다 한 번만 만든다."""

    assessment: Any
    intervals: list[TruthInterval]
    slides: dict[int, Any]

    @classmethod
    def of(cls, result: RunResult) -> Truth:
        assessment, intervals = truth_assessment(result)
        slides, _ = truth_aggs(result)
        return cls(assessment, intervals, slides)


def score_review(
    score: Score, result: RunResult, review: CoachReviewEvidence, truth: Truth | None = None
) -> None:
    truth = truth or Truth.of(result)
    intervals = truth.intervals
    score.takes += 1
    score_segments(score, review, intervals)

    for s in review.slides:
        t = truth.slides.get(s.slide_number)
        if t is None:
            continue
        for name in ("script_ratio", "cpm", "duration_ms"):
            a, b = getattr(s, name), getattr(t, name)
            if a is not None and b is not None:
                scale = 1000 if name == "duration_ms" else 1
                score.slide_err.setdefault(name, []).append(abs(a - b) / scale)

    want_m = {m.mission_id: m.status for m in truth.assessment.missions}
    for m in review.mission_results:
        score.missions.append(want_m.get(m.mission_id) == m.status)

    want_t = {t.type: t.status for t in truth.assessment.type_status}
    for t in review.type_status:
        score.type_status.append(want_t.get(t.type) == t.status)
        if t.status == TypeStatus.STRENGTH and want_t.get(t.type) in (
            TypeStatus.PRIORITY,
            TypeStatus.STABLE,
        ):
            score.false_strengths += 1

    want_mem = {(m.type, m.slide_number): m.label for m in truth.assessment.memory}
    for m in review.memory_check:
        score.memory.append(want_mem.get((m.type, m.slide_number)) == m.label)

    got_top = review.issues[0].type if review.issues else None
    want_top = truth.assessment.issues[0].type if truth.assessment.issues else None
    score.top_type.append(got_top == want_top)
    got_nm = review.next_missions[0] if review.next_missions else None
    want_nm = truth.assessment.next_missions[0] if truth.assessment.next_missions else None
    score.next_mission.append(
        (got_nm is None and want_nm is None)
        or (
            got_nm is not None
            and want_nm is not None
            and got_nm.type == want_nm.type
            and got_nm.target.metric == want_nm.target.metric
        )
    )

    tag = f"{result.scenario.name}#{result.noise.name}/{result.seed}"
    if got_top != want_top:
        score.notes.append(f"1순위 {tag}: 코치 {got_top} · 정답 {want_top}")
    iv_events = {e["event_id"]: e for e in result.events if e["kind"] == "INTERVENTION"}
    for oc in (e for e in result.events if e["kind"] == "OUTCOME"):
        if oc["outcome"] not in ("EFFECTIVE", "INEFFECTIVE"):
            continue
        iv = iv_events.get(oc["intervention_id"])
        if iv is None:
            continue
        want = truth_outcome(
            result,
            Issue(iv["issue"]),
            iv["t_ms"],
            oc["t_ms"],
            iv["slide_number"],
            iv["evidence"].get("keyword"),
        )
        if want is None:
            continue
        ok = (oc["outcome"] == "EFFECTIVE") == want
        score.outcomes.append(ok)
        if not ok:
            score.notes.append(
                f"효과 {tag}: {iv['issue']}@{iv['t_ms'] // 1000}s"
                f" 코치 {oc['outcome']} · 정답 {want}"
                f" (before {oc.get('before')} → after {oc.get('after')})"
            )

    for iv in result.interventions:
        ftype = FeedbackType(iv["type"])
        if ftype not in PROBLEM_TYPES or iv["instruction"] == "CONTINUE":
            continue
        score.rt_interventions += 1
        t = iv["t_ms"]
        recent = [
            i
            for i in intervals
            if i.type == ftype and i.observable and i.start_ms <= t and i.end_ms >= t - 20_000
        ]
        if not recent:
            score.rt_false += 1
            score.notes.append(f"불필요한 개입 {tag}: {iv['issue']}@{t // 1000}s")


def evaluate(
    scenarios: list[Scenario],
    noises: list[str],
    seeds: int,
    variants: dict[str, dict[str, Any]],
    raw: bool = False,
) -> dict[str, dict[str, Score]]:
    """{noise: {variant: Score}}. raw 면 FE · BE 요약 대신 원자료 입력으로 재생한다"""
    out: dict[str, dict[str, Score]] = {n: {v: Score() for v in variants} for n in noises}
    for noise_name in noises:
        noise = NOISE_PRESETS[noise_name]
        seed_list = [0] if noise_name == "clean" else list(range(1, seeds + 1))
        for sc in scenarios:
            for seed in seed_list:
                result = run(sc, noise=noise, seed=seed, raw=raw)
                truth = Truth.of(result)
                for vname, override in variants.items():
                    cfg = scenario_config(sc, override)
                    review = build_review_evidence(
                        sc.take_id,
                        result.events,
                        plan=sc.plan,
                        missions=sc.missions,
                        memory=sc.memory,
                        config=cfg,
                    )
                    score_review(out[noise_name][vname], result, review, truth)
    return out


# ── 출력 ─────────────────────────────────────────────────────────────────

_ROWS = [
    ("segment_recall", "구간 재현율", "↑"),
    ("segment_precision", "구간 정밀도", "↑"),
    ("segment_f1", "구간 F1", "↑"),
    ("unsupported_claims_per_take", "근거 없는 지적/Take", "↓"),
    ("segment_iou", "구간 IoU", "↑"),
    ("onset_error_s", "시작 오차(초)", "↓"),
    ("fragments_per_problem", "조각남", "↓"),
    ("mission_accuracy", "미션 판정 정확도", "↑"),
    ("type_status_accuracy", "영역 상태 정확도", "↑"),
    ("memory_accuracy", "기억 비교 정확도", "↑"),
    ("top_issue_type_agreement", "1순위 영역 일치", "↑"),
    ("next_mission_agreement", "1순위 미션 일치", "↑"),
    ("false_strengths_per_take", "거짓 강점/Take", "↓"),
    ("outcome_accuracy", "효과 판정 정확도", "↑"),
    ("false_intervention_rate", "불필요한 개입률", "↓"),
]


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def print_table(results: dict[str, dict[str, Score]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for noise, by_variant in results.items():
        summaries = {v: s.summary() for v, s in by_variant.items()}
        report[noise] = summaries
        names = list(summaries)
        print(f"\n━━ 잡음: {noise}  ({next(iter(by_variant.values())).takes} Take)")
        print("   " + f"{'지표':<22}" + "".join(f"{n:>16}" for n in names))
        for key, label, arrow in _ROWS:
            vals = [summaries[n][key] for n in names]
            print("   " + f"{label + ' ' + arrow:<22}" + "".join(f"{_fmt(v):>16}" for v in vals))
        mae = summaries[names[-1]]["slide_mae"]
        print(f"   장별 오차 (마지막 변형) {', '.join(f'{k} {_fmt(v)}' for k, v in mae.items())}")
    return report


def _print_grid(title: str, res: dict[str, Score]) -> dict[str, Any]:
    print(f"\n━━ {title}")
    print(
        f"   {'변형':<24}{'재현율':>8}{'정밀도':>8}{'F1':>8}{'IoU':>8}"
        f"{'시작오차':>10}{'조각남':>8}{'근거없음':>10}"
    )
    out = {}
    for name, s in res.items():
        m = s.summary()
        out[name] = m
        print(
            f"   {name:<24}{_fmt(m['segment_recall']):>8}{_fmt(m['segment_precision']):>8}"
            f"{_fmt(m['segment_f1']):>8}{_fmt(m['segment_iou']):>8}"
            f"{_fmt(m['onset_error_s']):>10}{_fmt(m['fragments_per_problem']):>8}"
            f"{_fmt(m['unsupported_claims_per_take']):>10}"
        )
    return out


def sweep(scenarios: list[Scenario], seeds: int, raw: bool = False) -> dict[str, Any]:
    """리뷰 근거 규칙 격자. 실시간 판단은 그대로라 발표는 한 번만 재생한다."""
    base = {"exclude_unreliable": True, "lag_compensation": True}
    lag_grid = {
        f"gap {gap // 1000:>2}s · lag ×{scale}": {
            "review": {
                **base,
                "lag_compensation": scale > 0,
                "lag_scale": scale or 1.0,
                "merge_gap_ms": gap,
            }
        }
        for gap in (0, 5_000, 10_000, 20_000)
        for scale in (0.0, 0.5, 1.0, 1.5)
    }
    min_grid = {
        f"최소 구간 {ms / 1000:.0f}s": {"review": {**base, "min_segment_ms": ms}}
        for ms in (0, 2_000, 3_000, 4_000, 6_000)
    }
    out: dict[str, Any] = {}
    for noise in ("noisy", "harsh"):
        res = evaluate(scenarios, [noise], seeds, {**lag_grid, **min_grid}, raw)[noise]
        out[f"{noise}/lag"] = _print_grid(
            f"격자 — 병합 간격 × 지연 보정 ({noise}, seed {seeds})",
            {k: v for k, v in res.items() if k in lag_grid},
        )
        out[f"{noise}/min_segment"] = _print_grid(
            f"격자 — 리뷰에 넘길 최소 구간 길이 ({noise}, seed {seeds})",
            {k: v for k, v in res.items() if k in min_grid},
        )
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenarios", nargs="*", help="시나리오 JSON. 없으면 scenarios/*.json")
    parser.add_argument(
        "--noise", default="clean,noisy,harsh", help="쉼표로 구분 (clean, noisy, harsh)"
    )
    parser.add_argument("--seeds", type=int, default=5, help="잡음이 있을 때 seed 수")
    parser.add_argument("--sweep", action="store_true", help="리뷰 근거 규칙 격자 (noisy · harsh)")
    parser.add_argument("--out", default=str(OUTPUTS_DIR / "evaluation.json"))
    parser.add_argument("--explain", default=None, help="이 변형의 틀린 사례를 출력 (예: D)")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="FE · BE 요약 대신 원자료(시선 1초 기록 · 음량 dBFS · 표시 없는 단어)로 재생",
    )
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.scenarios] or sorted(SCENARIOS_DIR.glob("*.json"))
    scenarios = [Scenario.load(p) for p in paths]
    noises = [n.strip() for n in args.noise.split(",") if n.strip()]
    report: dict[str, Any] = {
        "scenarios": [s.name for s in scenarios],
        "seeds": args.seeds,
        "variants": VARIANTS,
        "config_hash": load_config().config_hash(),
        "policy_version": POLICY_VERSION,
        "inputs": "raw" if args.raw else "summary",
    }
    results = evaluate(scenarios, noises, args.seeds, VARIANTS, args.raw)
    report["results"] = print_table(results)
    if args.explain:
        for noise, by_variant in results.items():
            for vname, score in by_variant.items():
                if vname.startswith(args.explain):
                    print(f"\n━━ 틀린 사례 — {vname} · {noise}")
                    for note in score.notes:
                        print(f"   {note}")
    if args.sweep:
        report["sweep"] = sweep(scenarios, args.seeds, args.raw)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    return 0


__all__ = ["VARIANTS", "Score", "Truth", "evaluate", "main", "score_review"]

if __name__ == "__main__":
    raise SystemExit(main())
