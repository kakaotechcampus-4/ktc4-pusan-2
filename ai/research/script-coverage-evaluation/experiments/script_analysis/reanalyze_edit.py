"""대본 분석: 대본을 한 문장 고쳐 다시 넣으면 바뀐 슬라이드만 다시 분석하는지 본다.

가상 시나리오: 발표자가 가상대본2 슬라이드 5 의 결과 문장 하나만 고친 JSON 을 다시 넣었다.
바뀐 슬라이드 하나만 1차 분석 · 최종 결론 · 전달 단위를 다시 부르고, 나머지는 캐시를 쓴다.
원본과 수정본 평가 기준은 파일 이름별로 따로 저장되어 서로 비교할 수 있다.

- LLM 호출: 처음 실행하면 바뀐 슬라이드 하나만 최대 3회 (1차 분석 · 최종 결론 · 전달 단위). 다시 실행하면 0회
- 쓰는 곳: outputs/가상대본2_수정.json (고친 대본), outputs/rubrics.sqlite
- 먼저 build_rubrics.py 를 실행해 원본 평가 기준을 만들어 둔다
"""

# %% 준비
import json

import pandas as pd
from IPython.display import display

from coverage_lab.llm import load_settings, script_llms
from coverage_lab.paths import OUTPUTS_DIR, SCRIPT_DIR
from coverage_lab.runs import run_script_analysis
from coverage_lab.script_analysis.quality import fact_values, facts_of
from coverage_lab.script_analysis.report import key_points_table
from coverage_lab.store import connect, load_rubric

# 스크립트로 실행할 때도 표가 잘리지 않게 한다
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 80)

settings = load_settings()
conn = connect()
llms = script_llms(settings)

# %% 가상 시나리오: 발표자가 가상대본2 슬라이드 5 의 결과 문장 하나만 고쳤다
V2_OLD = "사장님이 감으로 정하던 기존 방식과 비교하면 폐기율이 약 18퍼센트 줄었습니다."
V2_NEW = "사장님이 감으로 정하던 기존 방식과 비교하면 폐기율이 약 23퍼센트 줄었고, 오후 품절 알림의 정확도는 91퍼센트였습니다."

edited = json.loads((SCRIPT_DIR / "가상대본2.json").read_text(encoding="utf-8"))
slide5 = next(item for item in edited if item["slide_number"] == 5)
assert V2_OLD in slide5["script"]
slide5["script"] = slide5["script"].replace(V2_OLD, V2_NEW)

EDITED_PATH = OUTPUTS_DIR / "가상대본2_수정.json"
EDITED_PATH.parent.mkdir(parents=True, exist_ok=True)
EDITED_PATH.write_text(json.dumps(edited, ensure_ascii=False, indent=2), encoding="utf-8")

rubrics_v2, stats_v2 = run_script_analysis(EDITED_PATH, conn, settings, llms=llms)
print(f"{EDITED_PATH.name}:", stats_v2)  # 바뀐 슬라이드 하나만 1차 분석·최종 결론을 다시 부른다

# %% 고치기 전과 후: 사실과 Key Point 의 차이
before = load_rubric(conn, "가상대본2", 5)
after = load_rubric(conn, EDITED_PATH.stem, 5)
print("사라진 사실:", fact_values(before) - fact_values(after))
print("새로 생긴 사실:", fact_values(after) - fact_values(before))
display(key_points_table(after))

# %% 규칙 점검 — 규칙 분기는 결정적이므로 assert (파서 · 정규화 단위 점검은 tests/unit 에 있다)
# 1) Critical Fact Parser: 가상 데이터
f4 = facts_of(conn, "가상대본1", 4)
assert f4["12,400,000건"].qualifier == "약" and "3:1" in f4 and "xgboost" in f4
f6 = facts_of(conn, "가상대본1", 6)
assert f6["2025-03"].type == "date" and "8주" in f6
assert f6["42%"].qualifier == "약" and f6["85%"].qualifier == "이상"
f7 = facts_of(conn, "가상대본1", 7)
assert {"2,400,000,000원", "3,600,000,000원", "1,200곳", "430곳", "900곳"} <= f7.keys()
f9 = facts_of(conn, "가상대본1", 9)
assert "2026-H1" in f9 and not any(f.value.endswith("단계") for f in f9.values())  # 서수 제외
assert {"15%", "20%"} <= facts_of(conn, "가상대본2", 1).keys()  # 15~20% 범위
assert {"18:00", "09:00"} <= facts_of(conn, "가상대본2", 8).keys()  # 오후 6시, 오전 9시
g9 = facts_of(conn, "가상대본2", 9)
assert "19,000원" in g9 and "6배" in g9  # 1만 9천 원, 여섯 배

# 2) 대본 수정 시: 바뀐 슬라이드만 다시 분석하고, 바뀐 수치가 사실에 반영된다 (이 셀만 다시 돌리면 호출 0)
assert (
    stats_v2["slides"] == 11
    and stats_v2["analysis_calls"] <= 1
    and stats_v2["final_calls"] <= 1
    and not stats_v2["failed"]
)
assert stats_v2["unit_calls"] <= 1
assert "23%" in facts_of(conn, EDITED_PATH.stem, 5)
print("규칙 점검 통과")
