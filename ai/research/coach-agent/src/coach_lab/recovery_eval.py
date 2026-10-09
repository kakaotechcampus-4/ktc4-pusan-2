"""복구 실험 — 같은 Take 를 정상 · 응답 누락 · replay 로 돌려 Take 결과를 비교한다.

    python -m coach_lab.recovery_eval                 # clean 1회 + noisy 2 seed
    python -m coach_lab.recovery_eval --seeds 5

시나리오를 한 번 재생한 뒤 그 원자료(발표자는 그대로)로 코치를 다시 돌린다.

| 경우 | 뜻 |
|---|---|
| replay | coach_state 없이 Take 전체 원자료로 finalize (처음부터 다시 판정) |
| short_drop | 60~80초 요청을 보내지 않음. 창(30초)보다 짧아 다음 요청 창이 메운다 |
| long_drop | 60~100초 요청을 보내지 않음. 창보다 길어 409 → replay 로 다시 부른다 |

각 경우의 Take 결과를 정상 재생의 Take 결과와 비교한다: 영역별 Take 지표의 절대 차이, 문제 구간
일치율(같은 문제 · 장의 구간이 겹치는 비율, 정상 → 경우 / 경우 → 정상), replay 가 필요했던 비율.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean
from typing import Any

from coach import ReplayRequired, decide, finalize, recovery
from coach.schemas import FinalizeRequest, ProblemSegment, TakeResult
from coach.version import FEATURE_VERSION

from .judges import lab_judges
from .paths import RESULTS_DIR, SCENARIOS_DIR
from .simulator import NOISE_PRESETS, RunResult, Scenario, run, scenario_config

DROPS: dict[str, tuple[int, int]] = {"short_drop": (60_000, 80_000), "long_drop": (60_000, 100_000)}


def offline(
    result: RunResult, *, drop: tuple[int, int] | None = None, judges: Any = None
) -> tuple[TakeResult, bool]:
    """같은 원자료로 코치를 다시 돌린다. drop 구간의 요청은 보내지 않는다.

    합계가 Take 를 덮지 못하면(409) BE 처럼 replay 를 실어 다시 부른다. (Take 결과, replay 를 썼나)
    """
    judges = judges or lab_judges()
    cfg = result.config
    payload = result.presenter.replay_payload()
    assert result.final_request is not None
    fr = FinalizeRequest.model_validate({**result.final_request, "replay": payload})
    raw = fr.replay
    assert raw is not None
    state: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    for t in recovery.ticks(fr.t_ms):
        if drop is not None and drop[0] <= t < drop[1]:
            continue
        resp = decide(recovery.request_at(fr, raw, t, state, mode=fr.mode), judges, cfg)
        state = resp.coach_state
        events.extend(e.model_dump(mode="json") for e in resp.events)
    base = {
        **result.final_request,
        "events": events,
        "coach_state": state,
        "inputs": recovery.inputs_at(raw, fr.t_ms).model_dump(mode="json"),
    }
    try:
        return finalize(base, judges, cfg).take_result, False
    except ReplayRequired:
        return finalize({**base, "replay": payload}, judges, cfg).take_result, True


def full_replay(result: RunResult, judges: Any = None) -> TakeResult:
    """coach_state 없이 Take 전체 원자료로 finalize 한다."""
    assert result.final_request is not None
    req = {
        **result.final_request,
        "coach_state": None,
        "replay": result.presenter.replay_payload(),
    }
    return finalize(req, judges or lab_judges(), result.config).take_result


def _match(a: list[ProblemSegment], b: list[ProblemSegment]) -> float | None:
    """a 의 구간 중 b 에 같은 문제 · 장의 겹치는 구간이 있는 비율."""
    if not a:
        return None
    hit = sum(
        any(
            x.issue_type == y.issue_type
            and x.slide_number == y.slide_number
            and min(x.end_ms, y.end_ms) > max(x.start_ms, y.start_ms)
            for y in b
        )
        for x in a
    )
    return hit / len(a)


def compare(normal: TakeResult, other: TakeResult) -> dict[str, Any]:
    """영역별 Take 지표의 절대 차이와 문제 구간 일치율."""
    diffs: dict[str, dict[str, float]] = {}
    for area, a in normal.areas.items():
        b = other.areas.get(area)
        if a.take is None or b is None or b.take is None:
            continue
        for name, value in a.take.items():
            other_value = b.take.get(name)
            if isinstance(value, int | float) and isinstance(other_value, int | float):
                diffs.setdefault(area, {})[name] = abs(value - other_value)
    return {
        "area_diff": diffs,
        "segments_found": _match(normal.problem_segments, other.problem_segments),
        "segments_extra": _match(other.problem_segments, normal.problem_segments),
    }


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(fmean(vals), 4) if vals else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    area_mae: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        for area, metrics in r["area_diff"].items():
            for name, d in metrics.items():
                area_mae.setdefault(area, {}).setdefault(name, []).append(d)
    return {
        "runs": len(rows),
        "needed_replay_rate": _mean([float(r["needed_replay"]) for r in rows]),
        "segments_found": _mean([r["segments_found"] for r in rows]),
        "segments_extra": _mean([r["segments_extra"] for r in rows]),
        "area_mae": {
            area: {name: _mean(list(ds)) for name, ds in sorted(m.items())}
            for area, m in sorted(area_mae.items())
        },
    }


def evaluate(scenarios: list[Path], seeds: int) -> dict[str, Any]:
    judges = lab_judges()
    results: dict[str, Any] = {}
    for noise_name in ("clean", "noisy"):
        seed_list = [None] if noise_name == "clean" else list(range(1, seeds + 1))
        cases: dict[str, list[dict[str, Any]]] = {"replay": [], **{k: [] for k in DROPS}}
        for path in scenarios:
            sc = Scenario.load(path)
            for seed in seed_list:
                result = run(sc, noise=NOISE_PRESETS[noise_name], seed=seed, judges=judges)
                normal = result.take_result
                assert normal is not None
                cases["replay"].append(
                    {**compare(normal, full_replay(result, judges)), "needed_replay": True}
                )
                for name, window in DROPS.items():
                    if result.end_ms <= window[1]:
                        continue  # 누락 구간이 끝나기 전에 Take 가 끝났다
                    take, used = offline(result, drop=window, judges=judges)
                    cases[name].append({**compare(normal, take), "needed_replay": used})
        results[noise_name] = {name: summarize(rows) for name, rows in cases.items()}
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=2, help="noisy 의 seed 수")
    parser.add_argument("--out", default=str(RESULTS_DIR / "recovery.json"))
    args = parser.parse_args(argv)
    scenarios = sorted(SCENARIOS_DIR.glob("*.json"))
    first = Scenario.load(scenarios[0])
    payload = {
        "scenarios": [p.stem for p in scenarios],
        "seeds": args.seeds,
        "drops": {k: list(v) for k, v in DROPS.items()},
        "config_hash": scenario_config(first).config_hash(),
        "feature_version": FEATURE_VERSION,
        "results": evaluate(scenarios, args.seeds),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    out.write_bytes(text.encode("utf-8"))
    print(json.dumps(payload["results"], ensure_ascii=False, indent=1))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
