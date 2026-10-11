"""STT 평가: 연습 18번(대본 2개 x 9번)을 대본의 평가 기준으로 채점하고 결과 표를 본다.

- LLM 호출: 연습의 슬라이드마다 의미 평가 1회 + 충돌한 문장이 있는 슬라이드만 교차 검증 1회 (캐시에 있으면 호출 0)
- 쓰는 곳: outputs/rubrics.sqlite 의 slide_evaluations · similar_items (처음 채점 결과)
- 먼저 experiments/01_build_rubrics.py 로 평가 기준을 만들어 둔다
"""

# %% 준비
import pandas as pd
from IPython.display import display

from coverage_lab.datasets import load_take, take_files
from coverage_lab.llm import load_settings, stt_llms
from coverage_lab.runs import run_take_evaluation
from coverage_lab.store import connect
from coverage_lab.stt_report import (
    conflict_log,
    segmentation_report,
    show_evaluation,
    similar_counts,
    similar_report,
    take_summary,
)
from script_coverage.stt_evaluation.schemas import SlideEvaluation

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
print(f"MODEL={settings.model}")
conn = connect()
llms = stt_llms(settings)

takes = [load_take(p) for p in take_files()]
display(
    pd.DataFrame(
        [
            {
                "take_id": t.take_id,
                "script": t.script_name,
                "scenario": t.scenario,
                "slides": len(t.slides),
                "chars": sum(len(s.stt) for s in t.slides),
                "slide 1 STT": t.slides[0].stt[:50] + "…",
            }
            for t in takes
        ]
    )
)

# %% STT 문장 분리 점검 (규칙, API 호출 없음)
display(segmentation_report(conn, takes))

# %% 채점 (캐시에 있으면 호출 0). semantic_calls / verifier_calls 는 이번 실행에서 실제로 API 를 부른 횟수다
all_evaluations: dict[str, list[SlideEvaluation]] = {}
failures = {}
for take in takes:
    evaluations, stats = run_take_evaluation(take, conn, settings, llms=llms)
    all_evaluations[take.take_id] = evaluations
    failures[take.take_id] = stats.pop("failed")
    print(take.take_id, stats, "실패한 슬라이드:", sorted(failures[take.take_id]) or "-")

first_error = next((msg for failed in failures.values() for msg in failed.values()), None)
if first_error:
    raise RuntimeError(f"LLM 호출 실패 — 첫 오류: {first_error}")

# %% 연습별 점수 요약
display(take_summary(takes, all_evaluations))

# %% 충돌 조건별 발동 기록 (정답 라벨 없이 셀 수 있는 기록)
_all_sentences = [
    s for evaluations in all_evaluations.values() for e in evaluations for s in e.sentences
]
print(
    f"대본 문장 {len(_all_sentences)}개 중 충돌한 문장 {sum(bool(s.conflicts) for s in _all_sentences)}개"
)
display(conflict_log(all_evaluations))

# %% 비슷한 말 목록 (리뷰 agent 용): 연습마다 개수, 그리고 음성 인식이 발음이 비슷한 단어를 잘못 적은 연습
display(similar_counts(conn))
display(similar_report(conn, takes, "가상대본1_take9"))

# %% 슬라이드 상세
# 잘못 말한 수치와 말을 고쳐 말한 경우가 섞인 슬라이드
show_evaluation(all_evaluations, "가상대본1_take4", 6)
# 음성 인식이 수치 두 개를 발음이 비슷한 다른 값으로 적은 슬라이드 (천이백 → 천백, 이십사억 → 십사억) — 비율에서 빠진다
show_evaluation(all_evaluations, "가상대본1_take6", 7)
# 핵심 주장 문장을 빠뜨린 슬라이드
show_evaluation(all_evaluations, "가상대본1_take3", 7)
