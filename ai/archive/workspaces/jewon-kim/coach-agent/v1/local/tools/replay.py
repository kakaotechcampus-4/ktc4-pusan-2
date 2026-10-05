"""시나리오 재생 — 가상 발표를 코치에 흘려보내고 결과를 요약한다.

    python -m tools.replay                       # scenarios/*.json 전부
    python -m tools.replay scenarios/05_time_behind.json -v
    python -m tools.replay --config my_override.json --out reports

각 시나리오마다 개입 타임라인, 지표(개입 수 · 효과 · 지연), expect 검사 결과를 출력하고
reports/<시나리오>.json 에 이벤트 전체와 리뷰 근거를 씁니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from coach.config import load_config
from coach.renderer import format_duration

from .simulator import RunResult, Scenario, check_expect, run

ROOT = Path(__file__).resolve().parent.parent


def _print_run(result: RunResult, failures: list[str], verbose: bool) -> None:
    sc = result.scenario
    review = result.review
    assert review is not None
    status = "PASS" if not failures else "FAIL"
    print(f"\n━━ {sc.name}  [{status}]")
    if sc.description:
        print(f"   {sc.description}")
    for row in result.timeline:
        if row["action"] != "INTERVENE" and not verbose:
            continue
        when = format_duration(row["t_ms"])
        why = ", ".join(row["reason_codes"])
        if row["action"] == "INTERVENE":
            print(f"   {when:>7} 장{row['slide']}  {row['instruction']:<15} {row['message']}")
            print(f"   {'':>7}       └ {why}")
        else:
            print(f"   {when:>7} 장{row['slide']}  ({row['action']}: {why})")
    for e in result.events:
        if e["kind"] == "STRATEGY":
            to = f" → {e['to_instruction']}.{e['to_variant']}" if e.get("to_instruction") else ""
            print(
                f"   {format_duration(e['t_ms']):>7} 전략  {e['change']} {e['issue']}"
                f" (장{e['slide_number']}) {e['from_instruction']}.{e['from_variant']}{to}"
            )
    s = review.summary
    stats = result.stats()
    rate = f"{s.effective_rate:.0%}" if s.effective_rate is not None else "-"
    print(
        f"   ── 개입 {s.interventions} (격려 {s.praises})"
        f" · 효과 {s.effective}/{s.effective + s.ineffective}"
        f" ({rate}) · 포기 {s.gave_up} · 문제 구간 {s.episodes} (미대응 {s.episodes_unaddressed})"
    )
    print(
        f"   ── {stats['ticks']}틱 · decide p50 {stats['decide_p50_ms']}ms"
        f" / p95 {stats['decide_p95_ms']}ms · coach_state {stats['coach_state_bytes']}B"
    )
    if s.suppressed_by_reason:
        print(f"   ── 참은 이유 {s.suppressed_by_reason}")
    for f in failures:
        print(f"   ✗ {f}")


def _write_report(result: RunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.scenario.name}.json"
    payload = {
        "scenario": result.scenario.name,
        "stats": result.stats(),
        "timeline": result.timeline,
        "review_evidence": result.review.model_dump(mode="json") if result.review else None,
        "events": result.events,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            encoding="utf-8"
        )  # Windows 콘솔(cp949)에서 한글 · 기호가 깨지지 않게
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenarios", nargs="*", help="시나리오 JSON. 없으면 scenarios/*.json")
    parser.add_argument("--config", help="코치 설정 덮어쓰기 JSON (시나리오의 config 보다 우선)")
    parser.add_argument("--out", default=str(ROOT / "reports"), help="결과 JSON 을 쓸 폴더")
    parser.add_argument("-v", "--verbose", action="store_true", help="WAIT 아닌 판단을 전부 출력")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.scenarios] or sorted((ROOT / "scenarios").glob("*.json"))
    config = load_config(args.config) if args.config else None

    failed = 0
    for path in paths:
        result = run(Scenario.load(path), config)
        failures = check_expect(result)
        failed += bool(failures)
        _print_run(result, failures, args.verbose)
        _write_report(result, Path(args.out))
    print(f"\n{len(paths) - failed}/{len(paths)} 시나리오 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
