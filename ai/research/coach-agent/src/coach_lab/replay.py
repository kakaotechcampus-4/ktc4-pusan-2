"""시나리오 재생 — 가상 발표를 코치에 흘려보내고 결과를 요약한다.

    python -m coach_lab.replay                       # scenarios/*.json 전부
    python -m coach_lab.replay scenarios/05_time_behind.json -v
    python -m coach_lab.replay --config my_override.json --out outputs/tuned

각 시나리오마다 개입 타임라인, 지표(개입 수 · 효과 · 지연), expect 검사 결과를 출력하고
outputs/replay/<시나리오>.json 에 이벤트 전체와 Take 결과를 씁니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from coach.config import CoachConfig, load_config
from coach.renderer import format_duration
from coach.schemas import TakeResult
from coach.vocab import Instruction, Outcome

from .paths import REPLAY_DIR, SCENARIOS_DIR
from .simulator import RunResult, Scenario, check_expect, run


def _print_run(result: RunResult, failures: list[str], verbose: bool) -> None:
    sc = result.scenario
    take = result.take_result
    assert take is not None
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
                f"   {format_duration(e['t_ms']):>7} 전략  {e['change']} {e['issue_type']}"
                f" (장{e['slide_number']}) {e['from_instruction']}.{e['from_variant']}{to}"
            )
    _print_take_result(take)
    stats = result.stats()
    print(
        f"   ── {stats['ticks']}틱 · decide p50 {stats['decide_p50_ms']}ms"
        f" / p95 {stats['decide_p95_ms']}ms · coach_state {stats['coach_state_bytes']}B"
    )
    suppressed = Counter(
        r for e in result.events if e["kind"] == "SUPPRESSED" for r in e["reasons"]
    )
    if suppressed:
        print(f"   ── 참은 이유 {dict(sorted(suppressed.items()))}")
    for f in failures:
        print(f"   ✗ {f}")


def _print_take_result(take: TakeResult) -> None:
    """Take 결과의 사실을 줄여서 보여 준다: 문제 구간, 개입과 효과, 포기."""
    corrections = [i for i in take.interventions if i.instruction != Instruction.CONTINUE]
    effective = sum(i.outcome == Outcome.EFFECTIVE for i in take.interventions)
    measured = sum(
        i.outcome in (Outcome.EFFECTIVE, Outcome.INEFFECTIVE) for i in take.interventions
    )
    print(
        f"   ── 개입 {len(take.interventions)} (격려 {len(take.interventions) - len(corrections)})"
        f" · 효과 {effective}/{measured} · 포기 {len(take.gave_up)}"
        f" · 문제 구간 {len(take.problem_segments)}"
    )
    for s in take.problem_segments:
        tags = [
            "믿을 수 있음" if s.reliable else "믿을 수 없음",
            "개입함" if s.coached else "개입 없음",
        ]
        slide = f"장{s.slide_number}" if s.slide_number is not None else "전체"
        print(
            f"   {'':>7} 구간  {s.issue_type.value:<18}{slide:<5}"
            f" {format_duration(s.start_ms)}~{format_duration(s.end_ms)}  {' · '.join(tags)}"
        )
    for i in take.interventions:
        outcome = i.outcome.value if i.outcome else "-"
        print(
            f"   {format_duration(i.t_ms):>7} 개입  {i.issue_type.value:<18}"
            f"{i.instruction.value:<15} 효과 {outcome}"
        )
    for g in take.gave_up:
        slide = f"장{g.slide_number}" if g.slide_number is not None else "전체"
        print(f"   {'':>7} 포기  {g.issue_type.value} ({slide})")


def _write_report(result: RunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.scenario.name}.json"
    payload = {
        "scenario": result.scenario.name,
        "stats": result.stats(),
        "timeline": result.timeline,
        "take_result": result.take_result.model_dump(mode="json") if result.take_result else None,
        "events": result.events,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_config_file(path: str | Path) -> CoachConfig:
    """설정 덮어쓰기 JSON 을 읽어 코치 설정을 만든다. 코어는 파일을 읽지 않으므로 여기서 읽는다."""
    return load_config(**json.loads(Path(path).read_text(encoding="utf-8")))


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
    parser.add_argument("--out", default=str(REPLAY_DIR), help="결과 JSON 을 쓸 폴더")
    parser.add_argument("-v", "--verbose", action="store_true", help="WAIT 아닌 판단을 전부 출력")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.scenarios] or sorted(SCENARIOS_DIR.glob("*.json"))
    config = load_config_file(args.config) if args.config else None

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
