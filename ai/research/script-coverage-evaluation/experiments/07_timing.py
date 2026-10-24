"""장 수 · 동시 개수별 소요 시간: 요청 하나(대본 분석 → 평가)가 120초 안에 끝나는 조합을 찾는다.

- 방법: 가상대본 두 개의 장을 이어 붙여 N장(10 · 20 · 30) 덱을 만든다 (20장이 넘으면 앞에서부터 다시 쓰고
  장 번호는 1..N 으로 새로 매긴다). 동시 개수 W(4 · 8)마다 최상위 `analyze_script`(모든 장) → 그 평가 기준으로
  `evaluate_take`(모든 장)를 한 번씩 부른다. 캐시는 쓰지 않아 매번 실제로 부르고, 동시 개수는 실험 안에서
  `script_coverage.core.MAX_WORKERS` 만 바꿔 준다 (코어 코드는 그대로).
- LLM 은 서비스와 같은 규칙(요청 마감 120초 · 호출 12초 · 재시도 1번)으로 감싼다 (`coverage_lab.deadline_llm`).
  요청마다 마감과 기록이 새로 시작한다.
- LLM 호출: 전체 약 510회 (평가 기준 3 × (10+20+30) × 2 = 360, 채점 약 1.25 × 60 × 2 = 150). 과금되므로 먼저
  `--grid 10:4` 로 한 조합만 돌려 호출 수 · 시간을 보고 나머지를 돌린다. 한 조합만 돌릴 때는 파일을 쓰지 않는다
- 쓰는 곳: reports/results/timing.json (전체 조합을 돌렸을 때만). DB 는 쓰지 않는다
- 실행: 프로젝트 폴더에서 `.venv/Scripts/python.exe experiments/07_timing.py [--grid 10:4 [20:8 ...]]`
"""

# %% 준비
import argparse
import time

import script_coverage.core as core
from coverage_lab.datasets import load_script_json, load_take, script_files, take_files
from coverage_lab.deadline_llm import CallLog, DeadlineLlm
from coverage_lab.llm import load_settings, structured_llm
from coverage_lab.results import write_result
from script_coverage.schemas import AnalyzeRequest, RubricRequest, ScriptSlide, TakeSlide
from script_coverage.script_analysis.schemas import FinalReview, SlideSemanticAnalysis, SlideUnits
from script_coverage.stt_evaluation.schemas import SemanticEvaluation, VerifierResult

REQUEST_DEADLINE_SECONDS = 120.0  # 요청 하나의 마감 (architecture.md 의 AI 예산)
CALL_TIMEOUT_SECONDS = 12.0
GRID_SLIDES = (10, 20, 30)
GRID_WORKERS = (4, 8)

parser = argparse.ArgumentParser()
parser.add_argument("--grid", nargs="*", help="N:W 조합만 돌린다 (예: 10:4). 없으면 전체")
args = parser.parse_args()
grid = (
    [tuple(int(x) for x in item.split(":")) for item in args.grid]
    if args.grid
    else [(n, w) for n in GRID_SLIDES for w in GRID_WORKERS]
)

settings = load_settings()
print(f"MODEL={settings.model}")

# %% 덱 만들기: 장은 가상대본 1 · 2 를 이어 붙이고, 각 장에 그 장의 STT (take1)를 붙인다
pool: list[tuple[str, int]] = []  # (대본 이름, 원래 장 번호)
scripts: dict[tuple[str, int], str] = {}
for path in script_files():
    for slide in load_script_json(path):
        pool.append((path.stem, slide.slide_number))
        scripts[(path.stem, slide.slide_number)] = slide.script
stt: dict[tuple[str, int], str] = {}
for path in take_files():
    take = load_take(path)
    if path.stem == f"{take.script_name}_take1":
        for slide in take.slides:
            stt[(take.script_name, slide.slide_number)] = slide.stt


def build_deck(n: int) -> tuple[list[ScriptSlide], list[TakeSlide]]:
    """N장 덱: 풀을 앞에서부터 다시 쓰며 장 번호는 1..N 으로 새로 매긴다."""
    keys = [pool[i % len(pool)] for i in range(n)]
    return (
        [ScriptSlide(slide_number=i + 1, script=scripts[k]) for i, k in enumerate(keys)],
        [TakeSlide(slide_number=i + 1, stt=stt[k]) for i, k in enumerate(keys)],
    )


def deadline_llm(schema, deadline: float, log: CallLog) -> DeadlineLlm:
    inner = structured_llm(schema, settings, max_retries=0)  # 재시도는 감싸개가 세며 한다
    return DeadlineLlm(
        inner, deadline=deadline, log=log, timeout=CALL_TIMEOUT_SECONDS, max_retries=1
    )


def measure(name: str, call) -> dict:
    """요청 하나를 새 마감 · 새 기록으로 부르고 걸린 시간 · 실패 장 · 호출 기록을 모은다."""
    log = CallLog()
    deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
    started = time.monotonic()
    try:
        response = call(deadline, log)
        failed = {f.slide_number: f.error.code for f in response.failed}
        error = None
    except core.CoverageError as exc:  # 모든 장이 실패해 요청 전체가 실패한 경우
        response, failed, error = None, {}, exc.code
    seconds = round(time.monotonic() - started, 1)
    row = {
        "seconds": seconds,
        "within_deadline": seconds <= REQUEST_DEADLINE_SECONDS and not failed and error is None,
        "failed_slides": len(failed),
        "failed": {str(n): code for n, code in sorted(failed.items())},
        "request_error": error,
        **log.summary(),
    }
    print(
        f"  {name}: {seconds}초, 실패 장 {len(failed)}{' ' + error if error else ''}, "
        f"호출 {row['calls']} (재시도 {row['retries']}, 시간 초과 {row['timeouts']}), "
        f"호출당 중앙값 {row['call_median_seconds']}초 · 최대 {row['call_max_seconds']}초"
    )
    return {"row": row, "response": response}


# %% 측정
results: dict[str, dict] = {}
total_calls = 0
for n, workers in grid:
    core.MAX_WORKERS = workers  # 코어가 모듈 전역 이름으로 읽으므로 실험 안에서만 바꾼다
    script_slides, take_slides = build_deck(n)
    print(f"N={n} W={workers}")

    def rubric_call(deadline, log, slides=script_slides):
        llms = core.ScriptLLMs(
            semantic=deadline_llm(SlideSemanticAnalysis, deadline, log),
            final=deadline_llm(FinalReview, deadline, log),
            unit=deadline_llm(SlideUnits, deadline, log),
        )
        request = RubricRequest(pitch_id="timing", script_version_id="timing", slides=slides)
        return core.analyze_script(request, llms=llms, model=settings.model, cache=None)

    rubric = measure("평가 기준 만들기", rubric_call)

    def analyze_call(deadline, log, slides=take_slides, rubrics=rubric["response"]):
        llms = core.TakeLLMs(
            eval=deadline_llm(SemanticEvaluation, deadline, log),
            verifier=deadline_llm(VerifierResult, deadline, log),
        )
        request = AnalyzeRequest(take_id="timing", slides=slides, rubrics=rubrics.rubrics)
        return core.evaluate_take(request, llms=llms, model=settings.model, cache=None)

    if rubric["response"] is None:
        analyze = {"row": None}
        print("  채점: 평가 기준이 없어 건너뜀")
    else:
        analyze = measure("채점", analyze_call)
    results[f"N{n}_W{workers}"] = {
        "slides": n,
        "workers": workers,
        "rubric": rubric["row"],
        "analyze": analyze["row"],
    }
    total_calls += rubric["row"]["calls"] + (analyze["row"]["calls"] if analyze["row"] else 0)
    print(f"  누적 LLM 호출 {total_calls}")

# %% 저장 (전체 조합을 돌렸을 때만)
if args.grid:
    print("일부 조합만 돌렸으므로 timing.json 을 쓰지 않는다")
else:
    metrics = {
        "request_deadline_seconds": REQUEST_DEADLINE_SECONDS,
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
        "total_calls": total_calls,
        "combinations": results,
    }
    print(write_result("timing", settings.model, metrics))
