"""평가 기준 문장을 분석하고 결과를 출력합니다. --all은 results/examples.json에도 저장합니다.

    python run.py --text "추임새는 5번 이하로 해주세요."   # 직접 입력
    python run.py --index 1 3                            # data/examples.json의 1, 3번째
    python run.py --all                                  # data/examples.json 전체
    python run.py --eval --out reports/results/evaluation_criteria_eval.json   # 정답과 비교해 채점
"""

import argparse
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from evaluation_criteria import analyze_criteria
from evaluation_criteria.schemas import (
    Criteria,
    EvaluationCriteriaAnalysis,
    EvaluationCriteriaRequest,
)
from evaluation_criteria.version import FEATURE_VERSION, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent
EXAMPLES_PATH = ROOT / "data" / "examples.json"
RESULTS_DIR = ROOT / "results"
EVAL_OUT = ROOT / "reports" / "results" / "evaluation_criteria_eval.json"
# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = ROOT.parents[1] / ".env"
ELEMENTS = list(Criteria.model_fields)


def build_llm(include_raw: bool = False) -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다.

    자유 키 dict 없이 고정 필드를 사용하여 json_schema 방식으로
    구조화된 출력을 생성합니다.
    (function tools가 아니라 response_format 기반이라 reasoning_effort와 충돌하지 않음)
    include_raw=True면 원래 응답(토큰 수 · 모델 이름)도 함께 돌려줍니다 (MeasuredLLM이 씀).
    """
    load_dotenv(ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(f"OPENAI_API_KEY / OPENAI_MODEL이 비어 있습니다: {ENV_PATH}")

    llm = ChatOpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        model=model,
    )
    return llm.with_structured_output(EvaluationCriteriaAnalysis, include_raw=include_raw)


class MeasuredLLM:
    """analyze_criteria에 넘기는 LLM. 호출마다 걸린 시간 · 출력 토큰 · 응답한 모델을 calls에 남깁니다.

    llm은 build_llm(include_raw=True)로 만든 것입니다. 출력이 스키마와 다르면 예외를 그대로 올립니다.
    """

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.calls: list[dict[str, Any]] = []

    def invoke(self, messages: list[tuple[str, str]]) -> EvaluationCriteriaAnalysis:
        start = time.perf_counter()
        result = self.llm.invoke(messages)
        raw = result["raw"]
        usage = getattr(raw, "usage_metadata", None) or {}
        self.calls.append(
            {
                "latency_s": round(time.perf_counter() - start, 2),
                "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": (usage.get("output_token_details") or {}).get("reasoning"),
                "model": raw.response_metadata.get("model_name"),
            }
        )
        if result["parsing_error"] is not None:
            raise result["parsing_error"]
        return result["parsed"]


# ── 채점 (evaluation-criteria.md 13 · 14절). LLM 없음 ──────────────────────────


def _target_key(target: dict[str, Any]) -> tuple[str, str, float]:
    return target["metric"], target["operator"], round(float(target["value"]), 6)


def score_case(expected: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
    """문장 하나를 정답과 비교합니다. output이 None이면(호출 실패) 모두 틀린 것으로 셉니다."""
    expected_targets = {_target_key(t) for t in expected["targets"]}
    if output is None:
        return {
            "elements_correct": 0,
            "element_errors": [f"{k}: 출력 없음" for k in ELEMENTS],
            "missing_targets": sorted(expected_targets),
            "fabricated_targets": [],
        }

    errors = []
    for k in ELEMENTS:
        want, got = expected["values"][k], output["values"][k]
        # script_used 는 값까지, 나머지는 null 인지 아닌지만 본다
        ok = got == want if k == "script_used" else (got is None) == (want is None)
        if not ok:
            errors.append(f"{k}: 정답={want!r} 출력={got!r}")
    output_targets = {_target_key(t) for t in output["targets"]}
    return {
        "elements_correct": len(ELEMENTS) - len(errors),
        "element_errors": errors,
        "missing_targets": sorted(expected_targets - output_targets),
        "fabricated_targets": sorted(output_targets - expected_targets),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """문장별 채점을 14절 지표로 모읍니다."""
    n = len(cases)
    expected_total = sum(len(c["expected"]["targets"]) for c in cases)
    missing = sum(len(c["score"]["missing_targets"]) for c in cases)
    fabricated = sum(len(c["score"]["fabricated_targets"]) for c in cases)
    hit = expected_total - missing
    return {
        "element_accuracy": _ratio(sum(c["score"]["elements_correct"] for c in cases), n * len(ELEMENTS)),
        "target_recall": _ratio(hit, expected_total),
        "target_precision": _ratio(hit, hit + fabricated),
        "fabricated_targets_per_sentence": _ratio(fabricated, n),
        # 사람이 cases[].display_user_wording(true · false)를 채운 뒤 계산한다
        "display_user_wording_ratio": None,
        "failed": sum(c["error"] is not None for c in cases),
    }


def _spread(values: list[float | int | None]) -> dict[str, float] | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def evaluate(examples: list[dict[str, Any]], llm: MeasuredLLM) -> dict[str, Any]:
    cases = []
    for i, example in enumerate(examples, 1):
        request = EvaluationCriteriaRequest(standard_text=example["text"])
        before = len(llm.calls)
        output, error = None, None
        try:
            output = analyze_criteria(request, llm).model_dump(exclude={"meta"})
        except Exception as e:  # 실패한 문장도 결과에 남기고 다음 문장으로 간다
            error = f"{type(e).__name__}: {e}"
        call = llm.calls[before] if len(llm.calls) > before else {}
        cases.append(
            {
                "case": example["case"],
                "text": example["text"],
                "expected": example["expected"],
                "output": output,
                "error": error,
                "score": score_case(example["expected"], output),
                "latency_s": call.get("latency_s"),
                "output_chars": len(json.dumps(output, ensure_ascii=False)) if output else None,
                "output_tokens": call.get("output_tokens"),
                "reasoning_tokens": call.get("reasoning_tokens"),
                # 사람이 본다: 표시 기준이 사용자 표현을 살렸나 (true · false)
                "display_user_wording": None,
            }
        )
        print(f"[{i}/{len(examples)}] {example['case']} — {call.get('latency_s')}s"
              + (f" 실패: {error}" if error else ""))

    return {
        "meta": {
            "measured_at": datetime.now().isoformat(timespec="seconds"),
            "schema_version": SCHEMA_VERSION,
            "feature_version": FEATURE_VERSION,
            "models": sorted({c["model"] for c in llm.calls if c.get("model")}),
            "data": EXAMPLES_PATH.relative_to(ROOT).as_posix(),
            "sentences": len(examples),
        },
        "metrics": summarize(cases),
        "measurements": {
            "latency_s": _spread([c["latency_s"] for c in cases]),
            "output_chars": _spread([c["output_chars"] for c in cases]),
            "output_tokens": _spread([c["output_tokens"] for c in cases]),
            "reasoning_tokens": _spread([c["reasoning_tokens"] for c in cases]),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", nargs="+", help="분석할 평가 기준 문장")
    group.add_argument("--index", nargs="+", type=int, help="data/examples.json의 번호 (1부터)")
    group.add_argument("--all", action="store_true", help="data/examples.json 전체")
    group.add_argument("--eval", action="store_true", help="data/examples.json 전체를 정답과 비교해 채점")
    parser.add_argument("--out", type=Path, default=EVAL_OUT, help="--eval 결과 JSON 경로")
    args = parser.parse_args()

    if args.eval:
        examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
        report = evaluate(examples, MeasuredLLM(build_llm(include_raw=True)))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("metrics", "measurements")}, ensure_ascii=False, indent=2))
        print(f"-> {args.out}")
        return

    if args.text:
        inputs = args.text
    else:
        examples = [e["text"] for e in json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))]
        if args.all:
            inputs = examples
        else:
            invalid = [i for i in args.index if not 1 <= i <= len(examples)]
            if invalid:
                parser.error(f"examples.json 범위(1~{len(examples)}) 밖의 번호: {invalid}")
            inputs = [examples[i - 1] for i in args.index]

    llm = build_llm()
    results = []
    for i, text in enumerate(inputs, 1):
        data = analyze_criteria(EvaluationCriteriaRequest(standard_text=text), llm).model_dump()
        results.append({"input": text, "output": data})
        print(f"Input {i}: {text}")
        print(f"Output {i}: {json.dumps(data, ensure_ascii=False, indent=2)}\n")

    if args.all:
        RESULTS_DIR.mkdir(exist_ok=True)
        out = RESULTS_DIR / "examples.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
