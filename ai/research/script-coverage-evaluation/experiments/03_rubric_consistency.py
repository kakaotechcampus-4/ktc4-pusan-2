"""대본 분석: LLM 결과 품질 지표와, 같은 대본을 여러 번 분석했을 때의 일관성.

- 품질 지표: 저장된 평가 기준(최종)을 모아 LLM 분기 출력이 채점 기준으로 쓸 만한지 본다 (LLM 응답에 따라 값이 달라진다)
- 일관성: 같은 대본으로 평가 기준을 RUBRIC_CONSISTENCY_SAMPLES 벌(기본 3) 만들어 1차 결과와 최종 결과가 얼마나 흔들리는지 비교한다.
  1 이면 측정하지 않는다
- **LLM 비용 (과금)**: 기본 3벌이라, 캐시가 비어 있으면 슬라이드 20장마다 1차 분석 2회(= 40회)와, 그 결과로 달라진 입력의
  최종 결론 호출(최대 40회)을 부른다. 캐시에 있는 응답은 다시 부르지 않으므로 같은 설정으로 다시 실행하면 0회다.
  호출 없이 건너뛰려면 `RUBRIC_CONSISTENCY_SAMPLES=1` (건너뛰면 reports/results/rubric_consistency.json 을 덮어쓰지 않는다)
- 쓰는 곳: outputs/rubrics.sqlite (semantic_samples 등 캐시), reports/results/rubric_consistency.json
- 먼저 01_build_rubrics.py 를 실행해 평가 기준을 만들어 둔다
"""

# %% 준비
import os

import pandas as pd
from IPython.display import display

from coverage_lab.cache import SqliteLLMCache
from coverage_lab.datasets import script_files
from coverage_lab.llm import load_settings, script_llms
from coverage_lab.results import write_result
from coverage_lab.rubric_consistency import measure_consistency
from coverage_lab.rubric_quality import facts_of, llm_quality_report
from coverage_lab.store import connect, load_rubrics

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
conn = connect()
llms = script_llms(settings)
cache = SqliteLLMCache(conn)

# 슬라이드마다 채점 기준을 몇 벌 만들어 비교할지. 1 이면 측정하지 않는다
N_SAMPLES = int(os.getenv("RUBRIC_CONSISTENCY_SAMPLES", "3"))

# %% LLM 결과 품질 (최종 기준) — LLM 응답에 따라 달라지므로 지표로만 본다
all_rubrics = {path.stem: list(load_rubrics(conn, path.stem).values()) for path in script_files()}
quality = llm_quality_report([r for rs in all_rubrics.values() for r in rs])
display(quality)

# 사람 이름처럼 Kiwi 가 놓친 고유명사를 LLM key_terms 가 채웠는지
team_names = [
    f"{f.value}({f.source})"
    for f in facts_of(conn, "가상대본2", 2).values()
    if f.value in ("한도윤", "서민재", "오예린")
]
print("팀 소개 슬라이드의 이름 사실:", team_names)

# %% LLM 일관성 측정
metrics = {"quality": dict(zip(quality["지표"], quality["값"], strict=True))}
if N_SAMPLES < 2:
    print(
        "N_SAMPLES 가 1 이하라 일관성 측정을 건너뜁니다. reports/results/rubric_consistency.json 은 그대로 둡니다."
    )
else:
    consistency_table, summary, calls = measure_consistency(
        script_files(), cache, llms["semantic_llm"], llms["final_llm"], settings.model, N_SAMPLES
    )
    for name, n_calls in calls.items():
        print(f"{name}: 추가 호출 {n_calls}회")
    display(consistency_table[consistency_table["단계"] == "최종"].drop(columns="단계").round(2))
    display(summary.round(2))
    metrics |= {
        "samples": N_SAMPLES,
        "api_calls": calls,
        "summary": summary.round(4).to_dict(orient="index"),
        "per_slide_final": consistency_table[consistency_table["단계"] == "최종"]
        .drop(columns="단계")
        .round(4)
        .to_dict(orient="records"),
    }

    # 핵심 지표를 보고서용 JSON 으로 저장
    print("저장:", write_result("rubric_consistency", settings.model, metrics))
