"""STT 평가: 정답 라벨과 비교해 판정 정확도를 재고, 충돌 조건을 점검하고, 사용자 확인 뒤 정확도를 본다.

- LLM 호출: 없음. 채점 결과는 04_evaluate_takes.py 가 DB 에 남긴 것을 읽는다
- 쓰는 곳: outputs/rubrics.sqlite 의 similar_confirmations · confirmed_evaluations (정답 라벨로 사용자 답을 대신한 확인),
  reports/results/stt_accuracy.json (핵심 지표)
- 먼저 04_evaluate_takes.py 를 실행해 채점해 둔다
"""

# %% 준비

import pandas as pd
from IPython.display import display

from coverage_lab.datasets import load_take, take_files
from coverage_lab.llm import load_settings
from coverage_lab.results import write_result
from coverage_lab.store import connect, load_take_evaluations
from coverage_lab.stt_checks import (
    accuracy_row,
    confirm_with_labels,
    conflict_check,
    conflict_pairs,
    first_status_check,
    unchecked_errors,
)
from coverage_lab.stt_labels import (
    FACT_PRED_ORDER,
    FACT_TRUTH_ORDER,
    ORDER,
    SENTENCE_ORDER,
    build_tables,
    fact_accuracy,
    kp_accuracy,
    similar_accuracy,
    similar_tables,
)
from script_coverage.stt_evaluation.confirm import CONFIRM_TEXT
from script_coverage.stt_evaluation.scoring import take_scores

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
conn = connect()
takes = [load_take(p) for p in take_files()]
all_evaluations = {take.take_id: load_take_evaluations(conn, take) for take in takes}

# %% 정답 라벨과 비교 (성능 측정)
kp_table, fact_table, slide_table, sentence_table = build_tables(conn, takes, all_evaluations)
METHODS = ("규칙만", "LLM 1차", "최종")

sentence_acc = pd.DataFrame({col: kp_accuracy(sentence_table, col) for col in METHODS}).T
print("대본 문장 판정 (전체", len(sentence_table), "개) — 라벨과 같은 단위라 가장 직접적인 비교")
display(sentence_acc)
sentence_by_scenario = (
    sentence_table.groupby("scenario")
    .apply(lambda g: round(float((g["최종"] == g["정답"]).mean()), 3))
    .to_dict()
)
print("시나리오별 최종 정확도:", sentence_by_scenario)
display(
    pd.crosstab(sentence_table["정답"], sentence_table["최종"]).reindex(
        index=SENTENCE_ORDER, columns=SENTENCE_ORDER, fill_value=0
    )
)

kp_acc = pd.DataFrame({col: kp_accuracy(kp_table, col) for col in METHODS}).T
print("Key Point 판정 (전체", len(kp_table), "개)")
display(kp_acc)
kp_by_scenario = (
    kp_table.groupby("scenario")
    .apply(lambda g: round(float((g["최종"] == g["정답"]).mean()), 3))
    .to_dict()
)
print("시나리오별 최종 정확도:", kp_by_scenario)
print("최종 판정 혼동 행렬 (행 = 정답, 열 = 예측)")
display(
    pd.crosstab(kp_table["정답"], kp_table["최종"]).reindex(
        index=ORDER, columns=ORDER, fill_value=0
    )
)

held = fact_table[fact_table["최종"] == "held"]
fact_acc = fact_accuracy(fact_table)
print(f"핵심 사실 검증 (전체 {len(fact_table)}개, 판단 보류 {len(held)}개 제외) 정확도:", fact_acc)
display(
    pd.crosstab(fact_table["정답"], fact_table["최종"]).reindex(
        index=FACT_TRUTH_ORDER, columns=FACT_PRED_ORDER, fill_value=0
    )
)

sim_items, sim_labels = similar_tables(takes, all_evaluations)
sim_acc = similar_accuracy(takes, all_evaluations)
print(
    f"비슷한 말 — 라벨의 인식 오류 {len(sim_labels)}개 (영문 이름 제외), 보류한 비슷한 말 {len(sim_items)}개"
)
similar_row = {
    "인식 오류를 보류한 비율": sim_acc["인식 오류 보류율"],
    "보류한 것 중 실제 인식 오류": sim_acc["보류 중 실제 인식 오류"],
    "보류한 것 중 실제 발표자 실수 (사용자 확인으로 가릴 것)": round(
        float((sim_items["실제"] == "speaker_error").mean()), 3
    ),
    "규칙 추정 정확도": round(float((sim_items["실제"] == sim_items["규칙 추정"]).mean()), 3),
}
display(pd.DataFrame([similar_row]).T.rename(columns={0: "값"}))
print("보류한 비슷한 말의 실제 원인 (행) × 규칙 추정 (열)")
display(pd.crosstab(sim_items["실제"], sim_items["규칙 추정"]))
print("보류하지 못한 인식 오류")
display(sim_labels[~sim_labels["보류함"]])

slide_table["LLM 1차 오차"] = (slide_table["LLM 1차 coverage"] - slide_table["정답 coverage"]).abs()
slide_table["최종 오차"] = (slide_table["최종 coverage"] - slide_table["정답 coverage"]).abs()
slide_error = {
    "LLM 1차": round(float(slide_table["LLM 1차 오차"].mean()), 3),
    "최종": round(float(slide_table["최종 오차"].mean()), 3),
}
print("슬라이드 Content Coverage 평균 절대 오차 (0~1):", slide_error)
display(
    slide_table.groupby("scenario")[["정답 coverage", "최종 coverage", "최종 오차"]].mean().round(3)
)

print("틀린 판정 목록 (최종 기준)")
display(
    kp_table[kp_table["최종"] != kp_table["정답"]][
        ["take_id", "slide", "kp", "정답", "LLM 1차", "최종", "검증"]
    ]
)
display(
    fact_table[
        (fact_table["최종"] != fact_table["정답"])
        & (fact_table["최종"] != "held")
        & ~((fact_table["정답"] == "asr") & (fact_table["최종"] == "matched"))
    ][["take_id", "slide", "fact", "STT", "정답", "최종"]]
)

# %% 충돌 조건 점검 (정답 라벨 기준)
print("충돌 조건별 점검 (정답 라벨 기준)")
conflicts = conflict_check(sentence_table)
display(conflicts)
print("1차 판정별 — 오답이 있는데 다시 확인하지 않는 판정이 검사의 빈틈")
display(first_status_check(sentence_table))
print("함께 뜬 조건 쌍")
display(conflict_pairs(sentence_table))
print("다시 확인하지 않은 1차 오답")
display(unchecked_errors(sentence_table))

# %% 사용자 확인 뒤 정확도: 정답 라벨로 사용자 답을 대신한다 (LLM 호출 없음)
answered, confirmed_by_take = confirm_with_labels(conn, takes, all_evaluations)
print(
    "사용자 답 (정답 라벨로 대신):",
    {CONFIRM_TEXT[k]: n for k, n in answered.items()},
    "/ 남은 보류",
    sum(len(e.similar_items) - len(e.confirmations) for v in confirmed_by_take.values() for e in v),
)

kp_c, fact_c, slide_c, sent_c = build_tables(conn, takes, confirmed_by_take)
before_after = {
    "처음 채점": accuracy_row(sentence_table, kp_table, fact_table, slide_table),
    "사용자 확인 뒤": accuracy_row(sent_c, kp_c, fact_c, slide_c),
}
display(pd.DataFrame(before_after))
changed = sentence_table.merge(
    sent_c, on=["take_id", "slide", "sentence"], suffixes=("", " (확인 뒤)")
)
changed = changed[changed["최종"] != changed["최종 (확인 뒤)"]]
print(f"확인 때문에 판정이 바뀐 문장 {len(changed)}개")
display(changed[["take_id", "slide", "sentence", "정답", "최종", "최종 (확인 뒤)"]])
print("확인으로 점수가 바뀐 연습 (발표 전체)")
SCORE_KEYS = ("content_coverage", "critical_fact_accuracy", "script_fidelity")
changed_scores = pd.DataFrame(
    [
        {
            "take_id": t,
            **{f"{k} (처음)": take_scores(all_evaluations[t])[k] for k in SCORE_KEYS},
            **{f"{k} (확인 뒤)": take_scores(v)[k] for k in SCORE_KEYS},
        }
        for t, v in confirmed_by_take.items()
        if any(e.confirmations for e in v)
    ]
).set_index("take_id")
display(changed_scores)

# %% 핵심 지표를 보고서용 JSON 으로 저장
path = write_result(
    "stt_accuracy",
    settings.model,
    {
        "counts": {
            "sentences": len(sentence_table),
            "key_points": len(kp_table),
            "facts": len(fact_table),
            "held_facts": len(held),
        },
        "sentence_accuracy": sentence_acc.to_dict(orient="index"),
        "sentence_accuracy_by_scenario": sentence_by_scenario,
        "key_point_accuracy": kp_acc.to_dict(orient="index"),
        "key_point_accuracy_by_scenario": kp_by_scenario,
        "fact_accuracy": fact_acc,
        "similar": similar_row,
        "slide_coverage_error": slide_error,
        "conflict_check": conflicts.to_dict(orient="index"),
        "confirmation": {
            "answers": {CONFIRM_TEXT[k]: n for k, n in answered.items()},
            "accuracy": before_after,
            "changed_sentences": len(changed),
            "changed_take_scores": changed_scores.to_dict(orient="index"),
        },
    },
)
print("저장:", path)
