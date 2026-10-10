"""Take 결과 실험 — 코치가 만든 Take 결과의 사실을 정답과 비교해 채점하고, 설정 변형을 비교한다.

    python -m coach_lab.evaluate                      # clean 1회 + noisy · harsh 각 5 seed
    python -m coach_lab.evaluate --seeds 10 --sweep   # seed 를 늘리고 병합 간격 × 최소 구간 격자도
    python -m coach_lab.evaluate --noise noisy        # 잡음 수준 하나만

Take 결과 설정(config.take_result)은 실시간 판단에 쓰이지 않습니다. 그래서 발표는
(시나리오 × 잡음 × seed)마다 한 번만 재생하고, 변형마다 마지막 요청으로 finalize 만 다시 불러
Take 결과를 만들어 채점합니다. 변형 사이의 차이는 구간을 합치고 거르는 규칙에서만 옵니다.

채점 항목 (Take 결과의 사실만 본다. 결론 판정은 채점하지 않는다)

| 지표 | 뜻 |
|---|---|
| 구간 재현율 | 센서가 볼 수 있던 실제 문제 구간 중, 믿을 수 있는 문제 구간으로 잡힌 비율 |
| 구간 정밀도 | 믿을 수 있는 문제 구간 중, 실제로 (볼 수 있던) 문제였던 비율 |
| 근거 없는 지적 | 실제 문제가 아니었거나 센서를 믿을 수 없던 곳을 문제로 말한 수 (Take 당) |
| 구간 IoU · 시작 오차 | 맞춘 구간의 시간 겹침, "몇 분 몇 초부터"의 오차 |
| 조각남 | 실제 문제 하나를 몇 조각으로 나눠 말했나 (1 이 이상) |
| 장별 오차 | 장별 대본 응시(script_ratio) · CPM · 시간(초)의 평균 절대 오차 |
| 효과 판정 정확도 | 개입 효과(EFFECTIVE / INEFFECTIVE)가 발표자의 실제 반응과 같은 비율 |
| 불필요한 개입률 | 최근 20초 안에 실제 문제가 없던 개입 비율 (실시간) |

기록만 하는 문제(GAZE_ON_SCREEN · GAZE_UNMEASURABLE · PACE_SLOW)와 시간 영역 구간은
지적으로 세지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any

from coach import finalize
from coach.config import CoachConfig, load_config
from coach.schemas import ProblemSegment, TakeResult
from coach.version import FEATURE_VERSION
from coach.vocab import FeedbackType, Issue

from .judges import lab_judges
from .paths import OUTPUTS_DIR, SCENARIOS_DIR
from .simulator import NOISE_PRESETS, RunResult, Scenario, run, scenario_config, slide_metric
from .truth import PROBLEM_TYPES, TruthInterval, truth_aggs, truth_intervals, truth_outcome

#: 기본 설정 그대로 채점하는 변형 하나
DEFAULT_VARIANT = "기본"

#: 장별 오차를 재는 지표: (정답 이름, Take 결과의 영역, 지표 이름)
_SLIDE_METRICS = (
    ("script_ratio", "GAZE", "script_ratio"),
    ("cpm", "SPEED", "cpm"),
    ("duration_ms", "TIME", "slide_duration_ms"),
)

#: 병합 간격 × 최소 구간 격자 (ms)
SWEEP_MERGE_GAPS = (0, 5_000, 10_000, 20_000)
SWEEP_MIN_SEGMENTS = (0, 3_000, 6_000)


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
            "takes": self.takes,
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
            "outcome_accuracy": mean(self.outcomes),
            "false_intervention_rate": rate(self.rt_false, self.rt_interventions),
        }


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def claimed_segments(take: TakeResult, config: CoachConfig) -> list[ProblemSegment]:
    """코치가 '문제였다'고 말한 구간 — 믿을 수 있고, 기록만 하는 문제가 아니고, 비교하는 영역."""
    return [
        s
        for s in take.problem_segments
        if s.reliable and not config.issues[s.issue_type].record_only and s.area in PROBLEM_TYPES
    ]


def score_segments(
    score: Score, take_id: str, claims: list[ProblemSegment], truth: list[TruthInterval]
) -> None:
    seen = [t for t in truth if t.observable]
    score.truth_intervals += len(seen)
    score.claims += len(claims)
    for c in claims:
        if any(
            t.area == c.area and _overlap(c.start_ms, c.end_ms, t.start_ms, t.end_ms) > 0
            for t in seen
        ):
            score.claims_supported += 1
        else:
            score.unsupported += 1
            score.notes.append(
                f"근거 없는 지적 {take_id}: {c.issue_type.value} 장{c.slide_number}"
                f" {c.start_ms // 1000}~{c.end_ms // 1000}s (심각도 {c.mean_severity})"
            )
    for t in seen:
        hits = [
            c
            for c in claims
            if c.area == t.area and _overlap(c.start_ms, c.end_ms, t.start_ms, t.end_ms) > 0
        ]
        if not hits:
            score.notes.append(
                f"놓친 구간 {take_id}: {t.area.value} 장{t.slide_number}"
                f" {t.start_ms // 1000}~{t.end_ms // 1000}s"
            )
            continue
        score.truth_hit += 1
        inter = sum(_overlap(c.start_ms, c.end_ms, t.start_ms, t.end_ms) for c in hits)
        union = t.span_ms + sum(c.end_ms - c.start_ms for c in hits) - inter
        score.iou.append(inter / union if union else 0.0)
        score.onset_err_ms.append(abs(min(c.start_ms for c in hits) - t.start_ms))
        score.fragments.append(len(hits))


@dataclass
class Truth:
    """한 재생의 정답. Take 결과 변형과 무관해 재생마다 한 번만 만든다."""

    intervals: list[TruthInterval]
    slides: dict[int, Any]

    @classmethod
    def of(cls, result: RunResult) -> Truth:
        slides, _ = truth_aggs(result)
        return cls(truth_intervals(result), slides)


def score_take(
    score: Score,
    result: RunResult,
    take: TakeResult,
    config: CoachConfig,
    truth: Truth | None = None,
) -> None:
    """Take 결과 하나를 채점해 누적한다. 개입 · 효과는 재생의 이벤트에서 본다."""
    truth = truth or Truth.of(result)
    intervals = truth.intervals
    score.takes += 1
    score_segments(score, take.take_id, claimed_segments(take, config), intervals)

    for slide_number, t in truth.slides.items():
        for truth_name, area, metric in _SLIDE_METRICS:
            a = slide_metric(take, area, slide_number, metric)
            b = getattr(t, truth_name)
            if a is not None and b is not None:
                scale = 1000 if truth_name == "duration_ms" else 1
                score.slide_err.setdefault(truth_name, []).append(abs(a - b) / scale)

    tag = f"{result.scenario.name}#{result.noise.name}/{result.seed}"
    iv_events = {e["event_id"]: e for e in result.events if e["kind"] == "INTERVENTION"}
    for oc in (e for e in result.events if e["kind"] == "OUTCOME"):
        if oc["outcome"] not in ("EFFECTIVE", "INEFFECTIVE"):
            continue
        iv = iv_events.get(oc["intervention_id"])
        if iv is None:
            continue
        want = truth_outcome(
            result,
            Issue(iv["issue_type"]),
            iv["t_ms"],
            oc["t_ms"],
            iv["slide_number"],
        )
        if want is None:
            continue
        ok = (oc["outcome"] == "EFFECTIVE") == want
        score.outcomes.append(ok)
        if not ok:
            score.notes.append(
                f"효과 {tag}: {iv['issue_type']}@{iv['t_ms'] // 1000}s"
                f" 코치 {oc['outcome']} · 정답 {want}"
                f" (before {oc.get('before')} → after {oc.get('after')})"
            )

    for iv in result.interventions:
        ftype = FeedbackType(iv["area"])
        if ftype not in PROBLEM_TYPES or iv["instruction"] == "CONTINUE":
            continue
        score.rt_interventions += 1
        t = iv["t_ms"]
        recent = [
            i
            for i in intervals
            if i.area == ftype and i.observable and i.start_ms <= t and i.end_ms >= t - 20_000
        ]
        if not recent:
            score.rt_false += 1
            score.notes.append(f"불필요한 개입 {tag}: {iv['issue_type']}@{t // 1000}s")


def rebuild_take(result: RunResult, config: CoachConfig) -> TakeResult:
    """같은 재생의 마지막 요청으로 finalize 만 다시 불러, 다른 설정의 Take 결과를 만든다."""
    assert result.final_request is not None
    return finalize(result.final_request, lab_judges(), config).take_result


def evaluate(
    scenarios: list[Scenario],
    noises: list[str],
    seeds: int,
    variants: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Score]]:
    """{noise: {variant: Score}}. 변형은 설정 덮어쓰기({} 면 기본 설정 그대로)."""
    out: dict[str, dict[str, Score]] = {n: {v: Score() for v in variants} for n in noises}
    for noise_name in noises:
        noise = NOISE_PRESETS[noise_name]
        seed_list = [0] if noise_name == "clean" else list(range(1, seeds + 1))
        for sc in scenarios:
            for seed in seed_list:
                result = run(sc, noise=noise, seed=seed)
                truth = Truth.of(result)
                for vname, override in variants.items():
                    if override:
                        cfg = scenario_config(sc, override)
                        take = rebuild_take(result, cfg)
                    else:
                        cfg, take = result.config, result.take_result
                    assert take is not None
                    score_take(out[noise_name][vname], result, take, cfg, truth)
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
    """잡음 수준마다 한 줄(요약)씩 담아 돌려주고, 지표 × 잡음 표를 출력한다."""
    report = {noise: by_variant[DEFAULT_VARIANT].summary() for noise, by_variant in results.items()}
    names = list(report)
    takes = ", ".join(f"{n} {report[n]['takes']}" for n in names)
    print(f"\n━━ Take 결과 채점  ({takes} Take)")
    print("   " + f"{'지표':<22}" + "".join(f"{n:>12}" for n in names))
    for key, label, arrow in _ROWS:
        vals = [report[n][key] for n in names]
        print("   " + f"{label + ' ' + arrow:<22}" + "".join(f"{_fmt(v):>12}" for v in vals))
    for n in names:
        mae = report[n]["slide_mae"]
        print(f"   장별 오차 {n}: {', '.join(f'{k} {_fmt(v)}' for k, v in mae.items())}")
    return report


def _variant_name(gap: int, min_ms: int, default: bool) -> str:
    mark = " (기본)" if default else ""
    return f"병합 {gap / 1000:>2.0f}s · 최소 {min_ms / 1000:.0f}s{mark}"


def sweep(scenarios: list[Scenario], seeds: int) -> dict[str, Any]:
    """Take 결과 규칙 격자. 실시간 판단은 그대로라 발표는 한 번만 재생한다."""
    base = load_config().take_result
    grid = {
        _variant_name(gap, min_ms, gap == base.merge_gap_ms and min_ms == base.min_segment_ms): {
            "take_result": {"merge_gap_ms": gap, "min_segment_ms": min_ms}
        }
        for gap in SWEEP_MERGE_GAPS
        for min_ms in SWEEP_MIN_SEGMENTS
    }
    out: dict[str, Any] = {}
    for noise in ("noisy", "harsh"):
        res = evaluate(scenarios, [noise], seeds, grid)[noise]
        print(f"\n━━ 격자 — 병합 간격 × 최소 구간 ({noise}, seed {seeds})")
        print(
            f"   {'변형':<26}{'재현율':>8}{'정밀도':>8}{'F1':>8}{'IoU':>8}"
            f"{'시작오차':>10}{'조각남':>8}{'근거없음':>10}"
        )
        rows = []
        for name, s in res.items():
            m = s.summary()
            rows.append({"variant": name, **grid[name]["take_result"], **m})
            print(
                f"   {name:<26}{_fmt(m['segment_recall']):>8}{_fmt(m['segment_precision']):>8}"
                f"{_fmt(m['segment_f1']):>8}{_fmt(m['segment_iou']):>8}"
                f"{_fmt(m['onset_error_s']):>10}{_fmt(m['fragments_per_problem']):>8}"
                f"{_fmt(m['unsupported_claims_per_take']):>10}"
            )
        out[noise] = rows
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
    parser.add_argument(
        "--sweep", action="store_true", help="병합 간격 × 최소 구간 격자 (noisy · harsh)"
    )
    parser.add_argument("--out", default=str(OUTPUTS_DIR / "evaluation.json"))
    parser.add_argument("--explain", action="store_true", help="틀린 사례를 출력")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.scenarios] or sorted(SCENARIOS_DIR.glob("*.json"))
    scenarios = [Scenario.load(p) for p in paths]
    noises = [n.strip() for n in args.noise.split(",") if n.strip()]
    report: dict[str, Any] = {
        "scenarios": [s.name for s in scenarios],
        "seeds": args.seeds,
        "config_hash": load_config().config_hash(),
        "feature_version": FEATURE_VERSION,
    }
    results = evaluate(scenarios, noises, args.seeds, {DEFAULT_VARIANT: {}})
    report["results"] = print_table(results)
    if args.explain:
        for noise, by_variant in results.items():
            print(f"\n━━ 틀린 사례 — {noise}")
            for note in by_variant[DEFAULT_VARIANT].notes:
                print(f"   {note}")
    if args.sweep:
        report["sweep"] = sweep(scenarios, args.seeds)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    return 0


__all__ = ["Score", "Truth", "evaluate", "main", "rebuild_take", "score_take"]

if __name__ == "__main__":
    raise SystemExit(main())
