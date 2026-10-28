"""리뷰 평가 (명세 13 · 14절).

공통 기준 Take 묶음(data/takes)과 정답 리뷰(data/answers.json)로 잰다.

    python -m review_lab.evaluate --out reports/results/review_eval.json   # 실제 LLM (과금)
    python -m review_lab.evaluate --rules-only                             # 규칙 단계만

LLM 응답은 outputs/llm_cache.json 에 캐시해, 같은 프롬프트 · 출력 스키마 · 모델 · 입력이면 다시
부르지 않는다. 결과는 잡음(clean · noisy · harsh)별과 전체로 낸다.

| 지표 | 뜻 |
|---|---|
| 차원 상태 정답 일치 | 정답이 있는 (Take, 차원)마다 차원 상태가 정답과 같은 비율 |
| 1순위 정답 일치 | 부담 1위 PRIORITY 차원(없으면 null)이 정답과 같은 비율 |
| 미션 판정 정답 일치 | 정답이 있는 미션의 상태가 정답과 같은 비율 |
| 거짓 강점 / Take | 정답이 PRIORITY · STABLE 인 차원을 STRENGTH 로 낸 수 ÷ Take 수 |
| 스키마 유효율 | LLM 을 부른 Take 중 기본 결과로 돌아가지 않은(fallback_reason 없음) 비율 |
| 입력에 없는 숫자 | 숫자 검사 전 LLM 문장의 숫자 중 LLM 입력에 없는 숫자 수 |
| 반복 PRIORITY 비율 | 이어진 두 Take 에서 같은 차원이 둘 다 PRIORITY 인 비율 (이 묶음엔 없음) |

실제 LLM 은 ai/.env 의 기본 묶음으로 부른다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean, median
from typing import Any

from pitch_coach_ai.features.review import DEFAULT_CONFIG, apply_rules, review_take
from pitch_coach_ai.features.review.core import _numbers, check_numbers, message_numbers
from pitch_coach_ai.features.review.schemas import ReviewDraft
from pitch_coach_ai.features.review.version import FEATURE_VERSION
from pitch_coach_ai.features.review.vocab import DIMENSION_ORDER

from .llm import CachedLLM, load_settings, review_llm
from .paths import ANSWERS_PATH, OUTPUTS_DIR, PROJECT_ROOT, TAKES_DIR

NOISES = ("clean", "noisy", "harsh")
#: 거짓 강점으로 세는 정답 상태
NOT_STRENGTH = ("PRIORITY", "STABLE")


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(TAKES_DIR.glob("*.json"))]


def top_priority(dimensions: dict[str, str], burden: dict[str, Any]) -> str | None:
    """부담 1위 PRIORITY 차원 (동률이면 차원 표 순서). PRIORITY 가 없으면 None"""
    dims = [d for d, s in dimensions.items() if s == "PRIORITY"]
    return min(dims, key=lambda d: (-burden[d]["total"], DIMENSION_ORDER[d]), default=None)


def draft_sentences(draft: ReviewDraft) -> list[str]:
    """LLM 초안의 문장 (숫자 검사 전, 후보 id 가 맞는지와 상관없이 모두)"""
    return [
        draft.overall_summary,
        *(s.text for s in draft.strengths),
        *(s.text for s in draft.improvements),
        *(t for m in draft.next_missions for t in (m.description, m.reason)),
    ]


def unmatched_numbers(sentence: str, allowed: list[float]) -> list[str]:
    """문장 속 숫자 중 입력에 없는 것. 숫자 하나씩 check_numbers 로 본다 (뽑기는 코어 것을 쓴다)"""
    out = []
    for unit, value, places in _numbers(sentence):
        token = f"{value:.{places}f}{unit}"
        if not check_numbers(token, allowed):
            out.append(token)
    return out


def evaluate_case(
    case: dict[str, Any], answer: dict[str, Any], llm: CachedLLM | None, model: str | None
) -> dict[str, Any]:
    req = case["request"]
    rules = apply_rules(req)
    resp = review_take(req, llm, model=model)
    predicted = dict(resp.dimensions)
    expected = answer.get("dimensions", {})

    record: dict[str, Any] = {
        "case": f"{case['scenario']}__{case['noise']}",
        "scenario": case["scenario"],
        "noise": case["noise"],
        "dimensions": predicted,
        "dimension_checked": len(expected),
        "dimension_errors": {
            d: {"answer": s, "predicted": predicted[d]}
            for d, s in expected.items()
            if predicted[d] != s
        },
        "false_strengths": [
            d for d, s in expected.items() if s in NOT_STRENGTH and predicted[d] == "STRENGTH"
        ],
        "score": resp.score,
    }
    if "top_priority" in answer:
        pred = top_priority(predicted, rules.burden)
        record["top_priority"] = {"answer": answer["top_priority"], "predicted": pred}
    if answer.get("missions"):
        got = {m.mission_id: m.status for m in resp.mission_results}
        record["missions"] = {
            mid: {"answer": s, "predicted": got.get(mid)} for mid, s in answer["missions"].items()
        }

    if llm is not None:
        info: dict[str, Any] = {
            "fallback_reason": resp.fallback_reason,
            "error": llm.last_error,
            "cached": llm.last_cached,
            "latency_ms": None if llm.last_latency_ms is None else round(llm.last_latency_ms),
        }
        if llm.last_draft is not None:
            allowed = message_numbers(llm.last_message)
            sentences = draft_sentences(llm.last_draft)
            missing = [n for s in sentences for n in unmatched_numbers(s, allowed)]
            info.update(
                {
                    "output_chars": len(llm.last_draft.model_dump_json()),
                    "sentences": len(sentences),
                    "sentences_failed": sum(not check_numbers(s, allowed) for s in sentences),
                    "numbers": sum(len(_numbers(s)) for s in sentences),
                    "numbers_not_in_input": missing,
                }
            )
        record["llm"] = info
    return record


def _ratio(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    takes = len(records)
    dim_total = sum(r["dimension_checked"] for r in records)
    dim_errors = sum(len(r["dimension_errors"]) for r in records)
    tops = [r["top_priority"] for r in records if "top_priority" in r]
    missions = [m for r in records for m in r.get("missions", {}).values()]
    out: dict[str, Any] = {
        "takes": takes,
        "dimension_accuracy": _ratio(dim_total - dim_errors, dim_total),
        "dimension_checked": dim_total,
        "top_priority_accuracy": _ratio(
            sum(t["answer"] == t["predicted"] for t in tops), len(tops)
        ),
        "top_priority_checked": len(tops),
        "mission_accuracy": _ratio(
            sum(m["answer"] == m["predicted"] for m in missions), len(missions)
        ),
        "mission_checked": len(missions),
        "false_strengths_per_take": _ratio(sum(len(r["false_strengths"]) for r in records), takes),
        "recurring_priority_rate": None,  # 이어진 Take 가 없는 묶음
    }
    llm = [r["llm"] for r in records if "llm" in r]
    if llm:
        drafted = [x for x in llm if "sentences" in x]
        latencies = [x["latency_ms"] for x in llm if x["latency_ms"] is not None]
        reasons: dict[str, int] = {}
        errors: dict[str, int] = {}
        for x in llm:
            if x["fallback_reason"]:
                reasons[x["fallback_reason"]] = reasons.get(x["fallback_reason"], 0) + 1
            if x["error"]:
                errors[x["error"]] = errors.get(x["error"], 0) + 1
        missing = sum(len(x["numbers_not_in_input"]) for x in drafted)
        out["llm"] = {
            "calls": len(llm),
            "schema_valid_rate": _ratio(sum(x["fallback_reason"] is None for x in llm), len(llm)),
            "fallback_reasons": reasons,
            "errors": errors,
            "numbers": sum(x["numbers"] for x in drafted),
            "numbers_not_in_input": missing,
            "numbers_not_in_input_per_take": _ratio(missing, len(drafted)),
            "sentences": sum(x["sentences"] for x in drafted),
            "sentences_failed_number_check": sum(x["sentences_failed"] for x in drafted),
            "output_chars_mean": round(fmean(x["output_chars"] for x in drafted))
            if drafted
            else None,
            "output_chars_max": max((x["output_chars"] for x in drafted), default=None),
            "latency_ms_median": round(median(latencies)) if latencies else None,
            "latency_ms_max": max(latencies, default=None),
            "uncached_calls": len(latencies),
        }
    return out


def print_summary(results: dict[str, Any]) -> None:
    keys = [
        ("dimension_accuracy", "차원 상태 일치"),
        ("top_priority_accuracy", "1순위 일치"),
        ("mission_accuracy", "미션 판정 일치"),
        ("false_strengths_per_take", "거짓 강점/Take"),
    ]
    print(f"{'':8}" + "".join(f"{label:>16}" for _, label in keys))
    for name, res in results.items():
        print(f"{name:8}" + "".join(f"{str(res[k]):>16}" for k, _ in keys))
    for name, res in results.items():
        if "llm" in res:
            x = res["llm"]
            print(
                f"[{name}] LLM {x['calls']}번, 스키마 유효 {x['schema_valid_rate']}, "
                f"입력에 없는 숫자 {x['numbers_not_in_input']}/{x['numbers']}, "
                f"숫자 검사에 걸린 문장 {x['sentences_failed_number_check']}/{x['sentences']}, "
                f"지연 중앙값 {x['latency_ms_median']} ms"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rules-only", action="store_true", help="LLM 을 부르지 않는다 (과금 없음)"
    )
    parser.add_argument("--out", type=Path, help="결과 JSON 경로 (커밋하는 결과)")
    args = parser.parse_args()

    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))["scenarios"]
    llm, model = None, None
    if not args.rules_only:
        settings = load_settings()
        model = settings.model
        llm = CachedLLM(review_llm(settings), model, OUTPUTS_DIR / "llm_cache.json")

    records = []
    for case in load_cases():
        records.append(evaluate_case(case, answers.get(case["scenario"], {}), llm, model))
        print(f"  {records[-1]['case']}", flush=True)

    results = {"all": summarize(records)}
    for noise in NOISES:
        results[noise] = summarize([r for r in records if r["noise"] == noise])
    print_summary(results)

    if args.out:
        report = {
            "feature_version": FEATURE_VERSION,
            "criteria_version": DEFAULT_CONFIG.criteria_version(),
            "model": model,
            "data": {
                "takes": len(records),
                "scenarios": len({r["scenario"] for r in records}),
                "answers": ANSWERS_PATH.relative_to(PROJECT_ROOT).as_posix(),
            },
            "results": results,
            "cases": records,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()
