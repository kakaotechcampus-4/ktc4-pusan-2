"""대본 분석: data/virtual/scripts 의 대본을 모두 분석해 슬라이드별 평가 기준을 만들고 요약 표를 본다.

- LLM 호출: 대본 슬라이드마다 1차 분석 · 최종 결론 · 전달 단위 (캐시가 비어 있으면 20장 × 3회). 같은 대본 ·
  같은 LLM 설정이면 outputs/rubrics.sqlite 의 캐시를 써서 다시 호출하지 않는다 (과금은 처음 한 번)
- 쓰는 곳: outputs/rubrics.sqlite 의 evaluation_rubrics (STT 평가가 읽어 가는 평가 기준) 와 캐시 표
- 실행: VS Code Interactive Window 에서 셀 단위로, 또는 프로젝트 폴더에서 `uv run python experiments/01_build_rubrics.py`
"""

# %% 준비: .env 의 키와 모델, 로컬 DB, LLM
import json

import pandas as pd
from IPython.display import display

from coverage_lab.datasets import script_files
from coverage_lab.llm import load_settings, script_llms
from coverage_lab.rubric_report import rubric_summary, show_rubric
from coverage_lab.runs import run_script_analysis
from coverage_lab.store import connect, load_rubric
from script_coverage.shared.rubric import EvaluationRubric

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
print(f"MODEL={settings.model}")
conn = connect()  # DB 는 지우지 않는다. 처음부터 다시 분석하려면 outputs/rubrics.sqlite 를 지운다
llms = script_llms(settings)

# %% 모든 대본 분석 (캐시에 있으면 호출 0)
all_rubrics: dict[str, list[EvaluationRubric]] = {}
failures: dict[str, dict[int, str]] = {}
for path in script_files():
    rubrics, stats = run_script_analysis(path, conn, settings, llms=llms)
    all_rubrics[path.stem] = rubrics
    failures[path.stem] = stats.pop("failed")
    print(path.name, stats, "실패한 슬라이드:", sorted(failures[path.stem]) or "-")

# 실패한 슬라이드가 있으면 여기서 멈춘다. 성공한 슬라이드는 캐시에 남으므로 원인을 고치고 다시 실행하면 실패한 것만 호출한다.
first_error = next((msg for failed in failures.values() for msg in failed.values()), None)
if first_error:
    raise RuntimeError(f"LLM 호출 실패 — 첫 오류: {first_error}")

# %% 슬라이드별 요약
for script_name, rubrics in all_rubrics.items():
    print(f"■ {script_name}")
    display(rubric_summary(rubrics))

# %% 슬라이드 상세 — Key Point 와 연결된 Critical Fact
show_rubric(all_rubrics["가상대본1"][5])  # 수치가 많은 슬라이드
show_rubric(all_rubrics["가상대본2"][1])  # 사람 이름(LLM key_terms 보완)이 필요한 슬라이드

# %% 저장된 Evaluation Rubric (JSON) — STT 와 비교할 때 읽어 갈 모양
stored = load_rubric(conn, "가상대본1", 7)
print(
    json.dumps(
        stored.model_dump(exclude={"sentences", "normalized_script", "keywords"}),
        ensure_ascii=False,
        indent=2,
    )
)
