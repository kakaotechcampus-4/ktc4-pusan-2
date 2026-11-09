"""전달 단위 실패 복구(R1) 검증: 문장 하나 = 단위 하나인 복구 기준으로 채점하면 정확도가 얼마나 떨어지는가.

서비스 코어는 전달 단위 단계(LLM)만 실패한 장을 `attach_units(rubric, None, model)` 로 문장 단위 평가 기준
(`meta.rubric_quality = "sentence_fallback"`)으로 복구한다. 이 실험은 정상 평가 기준에서 같은 방법으로 복구 기준을 만들어
가상 연습 18번을 다시 채점하고, 정상 기준(DB 에 저장된 채점)과 정답 라벨 대비 정확도를 비교한다.
복구한 장의 상세를 `ok` 로 둘지 `provisional` 로 둘지(`SENTENCE_FALLBACK_PROVISIONAL`) 정하는 근거다.

판정 기준(미리 정함): 문장 판정 정확도 하락 <= 1%p 이고 심각한 오판 비율 증가 <= 0.5%p 면 통과.
통과하면 `SENTENCE_FALLBACK_PROVISIONAL = False` 를 추천하고, 아니면 `True` 를 유지한다.
한 문장에 내용이 여럿인(정상 기준의 전달 단위가 2개 이상인) 문장만 따로 본 값, 시나리오별 값도 함께 낸다.

- LLM 호출: 복구 기준으로는 메시지가 달라 캐시에 없으면 장마다 의미 평가 1회 + 충돌한 문장이 있는 장만 교차 검증 1회
  (6 luna 기본 묶음 `OPENAI_*`). 시작 전에 상한을 출력한다. 캐시 표에는 새 답이 쌓이지만 채점 표는 건드리지 않는다
- 쓰는 곳: outputs/rubrics.sqlite 의 evaluation_rubrics · slide_evaluations (읽기만), 캐시 표 (읽기 + 새 답 쓰기),
  reports/results/sentence_fallback.json (결과)
- 먼저 01_build_rubrics.py 로 평가 기준을, 04_evaluate_takes.py 로 정상 기준 채점을 DB 에 만들어 둔다
"""

# %% 준비
import copy
from pathlib import Path

import pandas as pd

from coverage_lab.cache import SqliteLLMCache
from coverage_lab.datasets import load_take, take_files
from coverage_lab.llm import load_settings, stt_llms
from coverage_lab.results import write_result
from coverage_lab.runs import _CountingLLM
from coverage_lab.store import connect, load_rubrics, load_take_evaluations, save_rubric
from coverage_lab.stt_labels import build_tables, kp_accuracy
from pitch_coach_ai.features.script_coverage.config import MAX_WORKERS
from pitch_coach_ai.features.script_coverage.script_analysis.units import attach_units
from pitch_coach_ai.features.script_coverage.stt_evaluation.core import evaluate_take
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import SlideEvaluation

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)

# 판정 기준 (결과를 보기 전에 정함)
MAX_ACCURACY_DROP = 0.01  # 문장 판정 정확도 하락 상한 (1%p)
MAX_SEVERE_RISE = 0.005  # 심각한 오판 비율 증가 상한 (0.5%p)

settings = load_settings()
print(f"MODEL={settings.model}")  # 키 · 주소는 출력하지 않는다
conn = connect()
llms = stt_llms(settings)
takes = [load_take(p) for p in take_files()]

# %% 정상 평가 기준 → 문장 단위 복구 기준
normal_rubrics = {name: load_rubrics(conn, name) for name in sorted({t.script_name for t in takes})}
all_slides = {
    (t.script_name, s.slide_number) for t in takes for s in t.slides
}  # 채점 대상 (연습, 장)
excluded_slides = sorted(
    {(name, n) for name, n in all_slides if n not in normal_rubrics[name]}
)  # 로컬 DB 에 평가 기준이 없는 장 (전달 단위 실패 장)
print("평가 기준이 없어 비교에서 뺀 장:", excluded_slides)

fallback_rubrics = {}
units_per_sentence = {}  # {(대본, 장, 문장): 정상 기준의 전달 단위 수}
for name, by_slide in normal_rubrics.items():
    fallback_rubrics[name] = {}
    for n, rubric in by_slide.items():
        for u in rubric.content_units:
            key = (name, n, u.sentence_index)
            units_per_sentence[key] = units_per_sentence.get(key, 0) + 1
        fb = attach_units(copy.deepcopy(rubric), None, settings.model)
        fb.meta.rubric_quality = "sentence_fallback"
        fallback_rubrics[name][n] = fb

# 정답 라벨 비교(build_tables)는 DB 의 평가 기준을 읽으므로, 복구 기준은 메모리 DB 에 따로 둔다 (실제 DB 는 건드리지 않는다)
memory = connect(Path(":memory:"))
for by_slide in fallback_rubrics.values():
    for rubric in by_slide.values():
        save_rubric(memory, rubric)

# %% 채점 (캐시에 있으면 호출 0). 연습마다 그 대본의 복구 기준 전체를 넘긴다
n_target = sum(
    1 for t in takes for s in t.slides if s.slide_number in normal_rubrics[t.script_name]
)
print(
    f"채점할 장 {n_target}개 -> 의미 평가 호출 최대 {n_target}회 + 교차 검증 최대 {n_target}회 (캐시에 있으면 0)"
)


class _HitCountingCache(SqliteLLMCache):
    """캐시 적중 수를 센다."""

    hits = 0

    def get(self, kind, key, config_hash):
        value = super().get(kind, key, config_hash)
        if value is not None:
            type(self).hits += 1
        return value


counts = {"semantic_calls": 0, "verifier_calls": 0}
counted_eval = _CountingLLM(llms.eval, counts, "semantic_calls")
counted_verifier = _CountingLLM(llms.verifier, counts, "verifier_calls")
cache = _HitCountingCache(conn)

fallback_evaluations: dict[str, list[SlideEvaluation]] = {}
failed_slides = []  # [{take_id, slide, code, cause}]
for take in takes:
    targets = [s for s in take.slides if s.slide_number in fallback_rubrics[take.script_name]]
    sub = take.model_copy(update={"slides": targets})
    evaluations, stats = evaluate_take(
        sub,
        fallback_rubrics[take.script_name],
        eval_llm=counted_eval,
        verifier_llm=counted_verifier,
        model=settings.model,
        cache=cache,
        max_workers=MAX_WORKERS,
    )
    fallback_evaluations[take.take_id] = evaluations
    for n, code in sorted(stats["failed"].items()):
        failed_slides.append(
            {
                "take_id": take.take_id,
                "slide": n,
                "code": code,
                "cause": stats["causes"].get(n, ""),
            }
        )
    print(take.take_id, "채점", len(evaluations), "장 / 실패", sorted(stats["failed"]) or "-")
print(
    "LLM 호출:", counts, "/ 캐시 적중:", _HitCountingCache.hits, "/ 실패한 장:", len(failed_slides)
)

# %% 같은 장 집합으로 두 기준의 표를 만든다 (기준선 = DB 에 저장된 정상 기준 채점)
failed_keys = {(f["take_id"], f["slide"]) for f in failed_slides}
baseline_evaluations = {
    t.take_id: [
        e
        for e in load_take_evaluations(conn, t)
        if (t.take_id, e.slide_number) not in failed_keys
        and e.slide_number in fallback_rubrics[t.script_name]
    ]
    for t in takes
}
fallback_evaluations = {
    take_id: [e for e in evals if (take_id, e.slide_number) not in failed_keys]
    for take_id, evals in fallback_evaluations.items()
}
slide_sets = {t: {e.slide_number for e in evals} for t, evals in baseline_evaluations.items()}
assert slide_sets == {
    t: {e.slide_number for e in evals} for t, evals in fallback_evaluations.items()
}, "두 기준의 장 집합이 다르다 — 04_evaluate_takes.py 를 먼저 실행한다"

tables = {
    "정상": build_tables(conn, takes, baseline_evaluations),
    "복구": build_tables(memory, takes, fallback_evaluations),
}
sentence = {k: v[3] for k, v in tables.items()}
key_point = {k: v[0] for k, v in tables.items()}

# 한 문장에 내용이 여럿인 문장 (정상 기준의 전달 단위가 2개 이상)
script_of = {t.take_id: t.script_name for t in takes}
for table in sentence.values():
    table["복합"] = [
        units_per_sentence.get((script_of[r.take_id], r.slide, r.sentence), 1) >= 2
        for r in table.itertuples()
    ]


def metrics(table: pd.DataFrame) -> dict:
    acc = kp_accuracy(table, "최종")
    return {
        "n": len(table),
        "accuracy": float((table["최종"] == table["정답"]).mean()) if len(table) else None,
        "severe": acc["심각한 오판 비율"] if len(table) else None,
    }


def severe_rate(table: pd.DataFrame) -> float:
    """심각한 오판 비율 (반올림하지 않은 값)."""
    from coverage_lab.stt_labels import SEVERE

    if table.empty:
        return float("nan")
    return float(
        sum((p, t) in SEVERE for p, t in zip(table["최종"], table["정답"], strict=True))
        / len(table)
    )


def block(table: pd.DataFrame) -> dict:
    return {
        "n": int(len(table)),
        "accuracy": round(float((table["최종"] == table["정답"]).mean()), 4),
        "severe": round(severe_rate(table), 4),
    }


def compare(selector) -> dict:
    a, b = block(selector(sentence["정상"])), block(selector(sentence["복구"]))
    return {
        "normal": a,
        "fallback": b,
        "accuracy_diff": round(b["accuracy"] - a["accuracy"], 4),
        "severe_diff": round(b["severe"] - a["severe"], 4),
    }


overall = compare(lambda t: t)
compound = compare(lambda t: t[t["복합"]])
by_scenario = {
    sc: compare(lambda t, sc=sc: t[t["scenario"] == sc])
    for sc in sorted(sentence["정상"]["scenario"].unique())
}
kp_overall = {
    "normal": {
        "n": int(len(key_point["정상"])),
        "accuracy": round(
            float((key_point["정상"]["최종"] == key_point["정상"]["정답"]).mean()), 4
        ),
    },
    "fallback": {
        "n": int(len(key_point["복구"])),
        "accuracy": round(
            float((key_point["복구"]["최종"] == key_point["복구"]["정답"]).mean()), 4
        ),
    },
}
kp_overall["normal"]["severe"] = kp_accuracy(key_point["정상"], "최종")["심각한 오판 비율"]
kp_overall["fallback"]["severe"] = kp_accuracy(key_point["복구"], "최종")["심각한 오판 비율"]
kp_overall["accuracy_diff"] = round(
    kp_overall["fallback"]["accuracy"] - kp_overall["normal"]["accuracy"], 4
)
kp_overall["severe_diff"] = round(
    kp_overall["fallback"]["severe"] - kp_overall["normal"]["severe"], 4
)

# 판정 (반올림하지 않은 차이로)
raw_acc_drop = float(
    (sentence["정상"]["최종"] == sentence["정상"]["정답"]).mean()
    - (sentence["복구"]["최종"] == sentence["복구"]["정답"]).mean()
)
raw_severe_rise = severe_rate(sentence["복구"]) - severe_rate(sentence["정상"])
passed = raw_acc_drop <= MAX_ACCURACY_DROP + 1e-9 and raw_severe_rise <= MAX_SEVERE_RISE + 1e-9
verdict = {
    "result": "pass" if passed else "fail",
    "accuracy_drop": round(raw_acc_drop, 4),
    "severe_rise": round(raw_severe_rise, 4),
    "criteria": {
        "max_accuracy_drop": MAX_ACCURACY_DROP,
        "max_severe_rise": MAX_SEVERE_RISE,
    },
    "recommended_SENTENCE_FALLBACK_PROVISIONAL": not passed,
}

display_rows = {"전체": overall, "복합 문장": compound, **by_scenario}
print(
    pd.DataFrame(
        {
            k: {
                "n": v["normal"]["n"],
                "정확도(정상)": v["normal"]["accuracy"],
                "정확도(복구)": v["fallback"]["accuracy"],
                "차이": v["accuracy_diff"],
                "심각(정상)": v["normal"]["severe"],
                "심각(복구)": v["fallback"]["severe"],
                "심각 차이": v["severe_diff"],
            }
            for k, v in display_rows.items()
        }
    ).T
)
print("핵심 내용:", kp_overall)
print("판정:", verdict)

# %% 결과 저장 (기존 채점 표는 덮어쓰지 않는다)
n_slides = sum(len(v) for v in baseline_evaluations.values())
path = write_result(
    "sentence_fallback",
    settings.model,
    {
        "counts": {
            "slides": n_slides,
            "sentences": overall["normal"]["n"],
            "compound_sentences": compound["normal"]["n"],
            "key_points": kp_overall["normal"]["n"],
        },
        "excluded_slides": [
            {
                "script": name,
                "slide": n,
                "reason": "로컬 DB 에 평가 기준이 없음 (전달 단위 실패 장)",
            }
            for name, n in excluded_slides
        ],
        "sentence_overall": overall,
        "sentence_compound": compound,
        "sentence_by_scenario": by_scenario,
        "key_point": kp_overall,
        "verdict": verdict,
        "llm": {
            "semantic_calls": counts["semantic_calls"],
            "verifier_calls": counts["verifier_calls"],
            "cache_hits": _HitCountingCache.hits,
            "failed_slides": failed_slides,
        },
    },
)
print("저장:", path)
