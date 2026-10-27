"""공통 기준 Take 의 저장 결과 묶음 만들기 — 코치 가상 발표 시나리오를 재생해 Take 결과를 저장한다.

리뷰는 코치를 import 하지 않으므로(architecture.md 8절) 이 파일만 코치 연구 폴더의 .venv 로
스크립트로 실행한다 (review_lab 의 다른 모듈을 import 하지 않는다).

    cd ai/research/review-agent
    ../coach-agent/.venv/Scripts/python.exe src/review_lab/build_data.py   # Windows
    ../coach-agent/.venv/bin/python src/review_lab/build_data.py           # macOS · Linux

시나리오(coach-agent/scenarios/*.json)마다 잡음 clean(seed 0) · noisy · harsh(seed 1)로 한 번씩
재생해 data/takes/<시나리오>__<잡음>.json 에 리뷰 요청 모양으로 저장한다.
- take_result: 코치 finalize 가 낸 Take 결과 그대로
- script_coverage: null. 시나리오에 대본 전달도 채점이 없어 content_delivery 는 판단 불가다
- previous_recurring_issues: 시나리오 memory 의 반복 문제에 차원을 붙인다 (AREA_DIMENSION)
- missions: 시나리오 미션의 계약 필드만
"""

from __future__ import annotations

import json
from pathlib import Path

from coach.version import FEATURE_VERSION as COACH_FEATURE_VERSION
from coach_lab.paths import SCENARIOS_DIR
from coach_lab.simulator import NOISE_PRESETS, Scenario, run, scenario_config

TAKES_DIR = Path(__file__).resolve().parents[2] / "data" / "takes"

#: (잡음, seed). 잡음이 있으면 seed 1 하나 (coach_lab.evaluate 의 첫 seed 와 같다)
RUNS = (("clean", 0), ("noisy", 1), ("harsh", 1))

#: 시나리오 memory 의 반복 문제 영역 → 차원. 시나리오의 GAZE 반복 문제(13 · 14)는 모두 대본 응시다
AREA_DIMENSION = {"GAZE": "script_independence", "TIME": "pacing_rhythm"}

MISSION_FIELDS = ("mission_id", "area", "slide_number", "target")


def review_request(sc: Scenario, take_result: dict) -> dict:
    return {
        "take_result": take_result,
        "script_coverage": None,
        "previous_takes": [],
        "previous_recurring_issues": [
            {**issue, "dimension": AREA_DIMENSION[issue["area"]]}
            for issue in sc.memory.get("recurring_issues", [])
        ],
        "missions": [{k: m.get(k) for k in MISSION_FIELDS} for m in sc.missions],
        "user_criteria": None,
    }


def main() -> None:
    TAKES_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        sc = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        cfg = scenario_config(sc)
        for noise, seed in RUNS:
            result = run(sc, cfg, noise=NOISE_PRESETS[noise], seed=seed)
            if result.take_result is None:
                raise RuntimeError(f"{sc.name} {noise}: Take 결과가 없다")
            case = {
                "scenario": sc.name,
                "noise": noise,
                "seed": seed,
                "description": sc.description,
                "source": {
                    "coach_feature_version": COACH_FEATURE_VERSION,
                    "coach_config_hash": cfg.config_hash(),
                },
                "request": review_request(sc, result.take_result.model_dump(mode="json")),
            }
            out = TAKES_DIR / f"{sc.name}__{noise}.json"
            out.write_text(json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(out.relative_to(TAKES_DIR.parents[1]))


if __name__ == "__main__":
    main()
