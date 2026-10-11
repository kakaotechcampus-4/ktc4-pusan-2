"""코칭 계획 실험 — 실제 LLM 으로 계획을 세우고, 계획이 쓸 만한지 · 흔들리는지 ·
재생에서 무엇을 바꾸는지 잰다.

    python -m coach_lab.plan_eval                 # scenarios/plan/*.json, 시나리오마다 5번 묻는다
    python -m coach_lab.plan_eval --samples 1     # 한 번만 (비용 줄이기)
    python -m coach_lab.plan_eval --out reports/results/coaching_plan.json   # 커밋하는 결과

실제 LLM API 를 부른다 (ai/.env, 과금). 응답은 outputs/llm_cache.sqlite 에 캐시해, 같은
프롬프트 · 모델 · 입력이면 다시 부르지 않는다. 반복(sample)마다 따로 캐시하므로 --samples 를
늘리면 늘린 만큼만 새로 부른다.

| 지표 | 뜻 |
|---|---|
| 유효 | LLM 이 답했고 기본 계획으로 돌아가지 않았다 (fallback_reason 없음) |
| 기대 통과 | 검증을 거친 계획이 시나리오의 plan_expect 를 만족한다 (예: 수치 장의 시선을 봐줌) |
| 검증에서 뺌 | LLM 초안에 범위 밖 · 근거 없는 항목이 있어 코치가 뺀 계획의 비율 (dropped) |
| 일관성 | 같은 입력을 여러 번 물었을 때 집중 · 봐주기 (영역, 장) 묶음이 첫 답과 같은 비율 |
| 재생 효과 | 첫 답의 계획으로 재생한 영역 · 장별 개입 수를 계획 없이 재생한 것과 비교 |
| 지연 | 캐시에 없어 실제로 부른 호출의 시간 |
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from statistics import fmean, median
from typing import Any

from coach import plan_coaching
from coach.planner import planner_hash
from coach.schemas import CoachingPlan, PlanRequest, PlanResponse
from coach.version import FEATURE_VERSION

from .cache import SqlitePlanCache
from .paths import OUTPUTS_DIR, SCENARIOS_DIR
from .simulator import RunResult, Scenario, run, scenario_config

PLAN_SCENARIOS_DIR = SCENARIOS_DIR / "plan"


class TimedLLM:
    """실제로 부른 호출의 시간을 잰다 (캐시에 있던 답은 여기를 지나지 않는다)."""

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.latencies_ms: list[float] = []

    def invoke(self, messages: Any) -> Any:
        started = time.perf_counter()
        try:
            return self.llm.invoke(messages)
        finally:
            self.latencies_ms.append((time.perf_counter() - started) * 1000)


def plan_request(sc: Scenario) -> PlanRequest:
    return PlanRequest.model_validate(
        {
            "take_id": sc.take_id,
            "mode": sc.mode,
            "plan": sc.plan,
            "scripts": sc.scripts,
            "missions": sc.missions,
            "memory": sc.memory,
            "previous_review": sc.previous_review,
        }
    )


def _scopes(items: list[Any]) -> set[tuple[str, int | None]]:
    return {(i.area.value, i.slide_number) for i in items}


def check_plan(plan: CoachingPlan, exp: dict[str, Any]) -> list[str]:
    """plan_expect 와 다른 점을 문장으로. 비어 있으면 통과."""
    out: list[str] = []
    relax, focus = _scopes(plan.relax), _scopes(plan.focus)
    for item in exp.get("relax_includes", []):
        if (item["area"], item.get("slide_number")) not in relax:
            out.append(f"봐주기에 {item['area']} {item.get('slide_number')} 가 없다")
    for t in exp.get("focus_includes_areas", []):
        if t not in {f[0] for f in focus}:
            out.append(f"집중에 {t} 가 없다")
    for t in exp.get("relax_excludes_areas", []):
        if t in {r[0] for r in relax}:
            out.append(f"봐주기에 {t} 가 있다")
    if exp.get("relax_empty") and relax:
        out.append(f"근거 없는 봐주기 {sorted(relax)}")
    if exp.get("no_budget") and plan.max_interventions is not None:
        out.append(f"근거 없는 개입 상한 {plan.max_interventions}")
    if exp.get("budget_set") and plan.max_interventions is None:
        out.append("개입 상한이 없다")
    return out


def interventions_by_scope(result: RunResult) -> dict[str, int]:
    """'영역 장' → 개입 수 (격려 CONTINUE 제외)."""
    out: dict[str, int] = {}
    for row in result.timeline:
        if row["action"] == "INTERVENE" and row["instruction"] != "CONTINUE":
            key = f"{row['area']} {row['slide']}"
            out[key] = out.get(key, 0) + 1
    return out


def plan_reasons(result: RunResult) -> dict[str, int]:
    """계획 때문에 생긴 이유 코드 수.

    개입에 붙은 PLAN_FOCUS, 참은 기록의 PLAN_RELAXED · BUDGET_EXHAUSTED.
    """
    counts = {"PLAN_FOCUS": 0, "PLAN_RELAXED": 0, "BUDGET_EXHAUSTED": 0}
    for e in result.events:
        codes = e.get("reason_codes", []) + e.get("reasons", [])
        for code in counts:
            counts[code] += codes.count(code)
    return counts


def evaluate_plans(
    scenarios: list[Scenario], llm: Any, model: str, cache: SqlitePlanCache | None, samples: int
) -> dict[str, Any]:
    timed = TimedLLM(llm)
    rows: list[dict[str, Any]] = []
    for sc in scenarios:
        req = plan_request(sc)
        cfg = scenario_config(sc)  # 시나리오가 바꾼 설정을 계획 검증과 재생에 똑같이 쓴다
        responses: list[PlanResponse] = [
            plan_coaching(
                req,
                llm=timed,
                model=model,
                cache=cache.with_sample(k) if cache else None,
                config=cfg,
            )
            for k in range(samples)
        ]
        first = responses[0]
        base = run(sc)
        with_plan = run(sc, coaching_plan=first.plan.model_dump(mode="json"))
        before, after = interventions_by_scope(base), interventions_by_scope(with_plan)
        failures = [check_plan(r.plan, sc.plan_expect) for r in responses]
        for item in sc.plan_expect.get("with_plan_no_interventions", []):
            key = f"{item['area']} {item['slide_number']}"
            if after.get(key):
                failures[0].append(f"계획으로 재생해도 {key} 개입 {after[key]}번")
        scopes = [(_scopes(r.plan.focus), _scopes(r.plan.relax)) for r in responses]
        rows.append(
            {
                "scenario": sc.name,
                "valid": [r.fallback_reason is None for r in responses],
                "fallback_reasons": [r.fallback_reason for r in responses],
                "expect_failures": failures,
                "consistent": [s == scopes[0] for s in scopes],
                "plans": [r.plan.model_dump(mode="json") for r in responses],
                "dropped": [r.dropped for r in responses],
                "interventions_without_plan": before,
                "interventions_with_plan": after,
                "plan_reasons_with_plan": plan_reasons(with_plan),
            }
        )
    flat = [x for row in rows for x in row["valid"]]
    checks = [not f for row in rows for f in row["expect_failures"]]
    consistent = [x for row in rows for x in row["consistent"][1:]]
    dropped = [bool(d) for row in rows for d in row["dropped"]]
    lat = timed.latencies_ms
    return {
        "summary": {
            "plans": len(flat),
            "valid_rate": round(sum(flat) / len(flat), 3) if flat else None,
            "expect_pass_rate": round(sum(checks) / len(checks), 3) if checks else None,
            "dropped_rate": round(sum(dropped) / len(dropped), 3) if dropped else None,
            "consistency": round(sum(consistent) / len(consistent), 3) if consistent else None,
            "llm_calls": len(lat),
            "latency_ms_median": round(median(lat)) if lat else None,
            "latency_ms_mean": round(fmean(lat)) if lat else None,
            "latency_ms_max": round(max(lat)) if lat else None,
        },
        "scenarios": rows,
    }


def _print(report: dict[str, Any]) -> None:
    for row in report["scenarios"]:
        ok = all(row["valid"]) and not any(row["expect_failures"])
        print(f"\n━━ {row['scenario']}  [{'PASS' if ok else 'FAIL'}]")
        plan = row["plans"][0]
        for f in plan["focus"]:
            print(f"   집중  {f['area']} 장{f['slide_number']} ×{f['weight']}  {f['why']}")
        for r in plan["relax"]:
            print(f"   봐줌  {r['area']} 장{r['slide_number']}  {r['why']}")
        if plan.get("max_interventions") is not None:
            print(f"   개입 상한 {plan['max_interventions']}")
        print(f"   ── 일관성 {row['consistent']} · 대체 {row['fallback_reasons']}")
        for i, items in enumerate(row["dropped"]):
            for msg in items:
                print(f"   - [{i}] 뺌: {msg}")
        print(
            f"   ── 개입 (계획 없음 → 계획) {row['interventions_without_plan']}"
            f" → {row['interventions_with_plan']} · {row['plan_reasons_with_plan']}"
        )
        for i, fails in enumerate(row["expect_failures"]):
            for msg in fails:
                print(f"   ✗ [{i}] {msg}")
    print(f"\n{json.dumps(report['summary'], ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenarios", nargs="*", help="시나리오 JSON. 없으면 scenarios/plan/*.json")
    parser.add_argument("--samples", type=int, default=5, help="같은 입력을 몇 번 물을지")
    parser.add_argument("--out", default=str(OUTPUTS_DIR / "coaching_plan.json"))
    parser.add_argument("--no-cache", action="store_true", help="캐시를 쓰지 않고 매번 부른다")
    args = parser.parse_args(argv)

    from .llm import load_settings, plan_llm  # LLM 라이브러리는 실제로 부를 때만 불러온다

    settings = load_settings()
    paths = [Path(p) for p in args.scenarios] or sorted(PLAN_SCENARIOS_DIR.glob("*.json"))
    scenarios = [Scenario.load(p) for p in paths]
    cache = None if args.no_cache else SqlitePlanCache()
    report = {
        "model": settings.model,
        "planner_hash": planner_hash(settings.model),
        "policy_version": FEATURE_VERSION,
        "samples": args.samples,
        **evaluate_plans(scenarios, plan_llm(settings), settings.model, cache, args.samples),
    }
    _print(report)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
