"""STT 평가: 같은 STT 를 여러 번 채점해 LLM 판정이 채점할 때마다 얼마나 달라지는지 본다.

STT_CONSISTENCY_SAMPLES 번(기본 3) 채점한다. 0번은 04_evaluate_takes.py 의 채점이고, 1번부터는 LLM 의미 평가와
교차 검증을 다시 부른다. 1 이면 측정하지 않는다.

- **LLM 비용 (과금)**: 기본 3벌이라, 캐시가 비어 있으면 연습 18번의 슬라이드 약 180장마다 의미 평가를 2회(= 약 360회) 부르고,
  충돌한 문장이 있는 슬라이드는 교차 검증도 부른다. 캐시에 있는 응답은 다시 부르지 않으므로 다시 실행하면 0회다.
  호출 없이 건너뛰려면 `STT_CONSISTENCY_SAMPLES=1` (건너뛰면 reports/results/stt_stability.json 을 덮어쓰지 않는다)
- 시작할 때 처음 채점(0번)이 온전한지(모든 슬라이드에 결과가 있고 평가 기준이 같은지) 먼저 확인하고, 아니면 LLM 을 부르기 전에 멈춘다
- 쓰는 곳: outputs/rubrics.sqlite 의 stt_llm_cache (kind = semantic#k / verifier#k. 채점 결과는 저장하지 않는다),
  reports/results/stt_stability.json
- 먼저 04_evaluate_takes.py 를 실행해 채점해 둔다
"""

# %% 준비
import os

import pandas as pd
from IPython.display import display

from coverage_lab.datasets import load_take, take_files
from coverage_lab.llm import load_settings, stt_llms
from coverage_lab.results import write_result
from coverage_lab.store import connect, load_take_evaluations
from coverage_lab.stt_checks import confidence_check, conflict_check, first_status_check
from coverage_lab.stt_stability import (
    accuracy_by_run,
    repeat_runs,
    stability_summary,
    unstable_table,
)

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
conn = connect()
llms = stt_llms(settings)
takes = [load_take(p) for p in take_files()]
all_evaluations = {take.take_id: load_take_evaluations(conn, take) for take in takes}

# 같은 STT 를 몇 번 채점해 비교할지. 0번은 처음 채점이고, 1번부터는 LLM 을 다시 부른다 (응답은 캐시해 다시 실행하면 호출하지 않음)
N_EVAL_SAMPLES = int(os.getenv("STT_CONSISTENCY_SAMPLES", "3"))

# %% 반복 채점
if N_EVAL_SAMPLES < 2:
    print(
        "N_EVAL_SAMPLES 가 1 이하라 반복 채점을 건너뜁니다. reports/results/stt_stability.json 은 그대로 둡니다."
    )
else:
    runs, extra_calls = repeat_runs(
        conn, takes, settings, all_evaluations, N_EVAL_SAMPLES, llms=llms
    )
    print(f"반복 채점 {N_EVAL_SAMPLES - 1}번 추가 — 이번 실행의 API 호출: {dict(extra_calls)}")

    # 채점 사이 일관성
    stability, unstable = stability_summary(runs, N_EVAL_SAMPLES)
    print(f"같은 STT 를 {N_EVAL_SAMPLES}번 채점했을 때")
    display(stability)

    # 채점마다 정답 라벨과 비교
    accuracy, all_sentences = accuracy_by_run(conn, takes, runs)
    print("채점별 정확도 (정답 라벨 대비)")
    display(accuracy)

    # 충돌 조건 점검 — 채점 합산
    print(f"충돌 조건 점검 — 채점 {N_EVAL_SAMPLES}번 합산 (대본 문장 판정 {len(all_sentences)}개)")
    conflicts = conflict_check(all_sentences)
    display(conflicts)
    display(first_status_check(all_sentences))
    print("LLM 확신도별 1차 오답 (채점 합산)")
    confidence = confidence_check(all_sentences)
    display(confidence)

    # 채점마다 판정이 달라진 Key Point
    print(f"채점마다 최종 판정이 달라진 Key Point {len(unstable)}개")
    display(unstable_table(unstable))

    metrics = {
        "samples": N_EVAL_SAMPLES,
        "api_calls": dict(extra_calls),
        "stability": stability["값"].to_dict(),
        "accuracy_by_run": accuracy.to_dict(orient="index"),
        "conflict_check_all_runs": conflicts.to_dict(orient="index"),
        "confidence_check_all_runs": confidence.to_dict(orient="index"),
        "unstable_key_points": unstable_table(unstable).to_dict(orient="records"),
    }

    # 핵심 지표를 보고서용 JSON 으로 저장
    print("저장:", write_result("stt_stability", settings.model, metrics))
