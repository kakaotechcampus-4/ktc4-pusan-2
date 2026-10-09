"""evaluate · finalize 요청 · 응답 예시 만들기 — BE 개발자가 읽는 `src/coach/examples/` 파일.

    python -m coach_lab.examples [--out 폴더]

가상 발표자(simulator)가 1초마다 보내는 요청을 코치에 흘려보내 92초 시점의 요청을 만든다.
  request.json        92000 시점 evaluate 요청 (coach_state 는 91000 응답의 것)
  judge_results.json  그 요청에서 판정 모듈이 낸 결과 · 기준 · 지표 (대역이 낸 계약 모양 값)
  response.json       그 요청에 코치가 준 응답

같은 가상 발표를 Take 끝(92초)까지 재생해 finalize 예시도 만든다 (`finalize/`).
  request.json · response.json                Take 끝 요청(마지막 창 · 이벤트 · coach_state)과 응답
  request_replay.json · response_replay.json  coach_state 없이 Take 전체 원자료(replay)로 부른
                                              요청과 응답 (409 를 받은 BE 가 다시 부르는 경우)

세 파일은 시계 · 난수 없이 만들어서 다시 돌려도 바이트까지 같다. 계약 테스트
(tests/unit/test_examples.py)가 request + judge_results 를 코치에 넣어 response 와 맞는지 본다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from coach import decide, finalize
from coach.judges import Judges

from .judges import filler, gaze, lab_judges, pace, volume
from .simulator import Presenter, Scenario, run

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "coach" / "examples"
OUT_DIR = EXAMPLES_DIR / "evaluate"
FINAL_MS = 92_000
BASE_LEVEL_DB = -29.5
#: 발표자의 평소 목소리 레벨이 BASE_LEVEL_DB 가 되도록 하는 상대 음량 (시뮬레이터 기준 -24.0)
_RELATIVE_DB = BASE_LEVEL_DB + 24.0

SCENARIO = {
    "name": "evaluate-example",
    "take_id": "take-123",
    "duration_ms": FINAL_MS,
    "plan": {
        "target_ms": 300_000,
        "min_ms": 270_000,
        "max_ms": 330_000,
        "slides": [
            {"slide_number": 1, "target_ms": 60_000, "script_chars": 300},
            {"slide_number": 2, "target_ms": 60_000, "script_chars": 300},
            {"slide_number": 3, "target_ms": 90_000, "script_chars": 450},
        ],
    },
    "missions": [
        {
            "mission_id": "m-2",
            "area": "GAZE",
            "slide_number": 3,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.4},
        }
    ],
    "memory": {"recurring_issues": [{"area": "GAZE", "slide_number": 3}]},
    # 평소에는 청중을 보고 말하다 84초부터 대본을 읽는다. completion 은 앞 두 장을 대본의 일부만
    # 말하고 넘기게 해서 3장이 60초 근처에 시작하게 맞춘 값이다
    "baseline": {"script_ratio": 0.15, "relative_db": _RELATIVE_DB, "completion": 0.37},
    "segments": [{"from_ms": 84_000, "to_ms": 200_000, "set": {"script_ratio": 0.85}}],
}
COACHING_PLAN = {
    "focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5}],
    "relax": [],
    "max_interventions": None,
}


class _Recorder:
    """판정 모듈을 감싸 부른 결과를 적어 둔다. 요청마다 `reset()` 으로 비운다."""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.reset()

    def reset(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.summaries: list[dict[str, Any]] = []

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[Any]:
        out = self.module.judge(inputs, t_ms)
        self.results = [r if isinstance(r, dict) else r.model_dump(mode="json") for r in out]
        return out

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        out = self.module.summarize(tally)
        self.summaries.append({"tally": tally, "metrics": out})
        return out

    def criteria(self) -> dict[str, Any]:
        return self.module.criteria()


def _full_records(request: dict[str, Any]) -> dict[str, Any]:
    """시뮬레이터가 안 채우는 1초 기록 필드(FE 가 보내는 모양)를 채운다."""
    inputs = request["inputs"]
    gaze_records = [
        {
            "t_ms": r["t_ms"],
            "duration_ms": r["duration_ms"],
            "state": r["state"],
            "direction": None,
            "confidence": 0.88,
            "reliability": 0.95,
            "issues": [],
            "frames": 12,
        }
        for r in inputs["gaze_records"]
    ]
    return {**request, "inputs": {**inputs, "gaze_records": gaze_records}}


def build() -> dict[str, Any]:
    """92000 시점 요청 · 판정 결과 · 응답을 만든다."""
    presenter = Presenter(Scenario.model_validate(SCENARIO), coaching_plan=COACHING_PLAN)
    modules = {
        "gaze": _Recorder(gaze),
        "pace": _Recorder(pace),
        "volume": _Recorder(volume),
        "filler": _Recorder(filler),
    }
    judges = Judges(**modules, baseline=lab_judges().baseline)
    state: dict[str, Any] | None = None
    for t in range(0, FINAL_MS + 1, 1_000):
        for module in modules.values():
            module.reset()
        request = _full_records(presenter.request(t, state))
        request["calibration"] = {"base_level_db": BASE_LEVEL_DB}
        # 사용자 평가 기준에 대본 사용 설정이 없다 (Take 동안 바뀌지 않는 값은 매 요청에 싣는다)
        request["script_used"] = None
        response = decide(request, judges)
        if t < FINAL_MS:
            state = response.coach_state
    return {
        "request": request,
        "judge_results": {
            name: {
                "judge": m.results,
                "criteria": {k: v.model_dump(mode="json") for k, v in m.criteria().items()},
                "summarize": m.summaries,
            }
            for name, m in modules.items()
        },
        "response": response.model_dump(mode="json"),
    }


def build_finalize() -> dict[str, Any]:
    """같은 가상 발표를 Take 끝까지 재생한 finalize 요청 · 응답 (정상 · replay)."""
    result = run(Scenario.model_validate(SCENARIO), coaching_plan=COACHING_PLAN)
    assert result.final_request is not None
    judges = lab_judges()
    request = {**result.final_request, "script_mode": "HIGHLIGHT"}
    replay = {**request, "coach_state": None, "replay": result.presenter.replay_payload()}
    return {
        "request": request,
        "response": finalize(request, judges, result.config).model_dump(mode="json"),
        "request_replay": replay,
        "response_replay": finalize(replay, judges, result.config).model_dump(mode="json"),
    }


def _write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))  # 줄바꿈을 OS 에 맡기지 않는다
    return path


def write(root: Path = EXAMPLES_DIR) -> list[Path]:
    """root/evaluate/ 와 root/finalize/ 에 예시를 쓴다."""
    built = build()
    paths = [_write(root / "evaluate" / f"{n}.json", built[n]) for n in built]
    fin = build_finalize()
    paths += [_write(root / "finalize" / f"{n}.json", fin[n]) for n in fin]
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", default=str(EXAMPLES_DIR), help="예시 폴더(evaluate · finalize 위)"
    )
    for path in write(Path(parser.parse_args(argv).out)):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
