"""장 수 · 동시 개수별 소요 시간: 요청 하나(대본 분석 → 평가)가 120초 안에 끝나는 조합을 찾는다.

- 방법: 가상대본 두 개의 장을 이어 붙여 N장(10 · 20 · 30) 덱을 만든다 (`coverage_lab.timing.build_deck`).
  동시 개수 W(4 · 8)마다 최상위 `analyze_script`(모든 장) → 그 평가 기준으로 `evaluate_take`(모든 장)를 한 번씩 부른다.
  캐시는 쓰지 않아 매번 실제로 부르고, 동시 개수는 실험 안에서 `script_coverage.core.MAX_WORKERS` 만 바꿔 준다
  (코어 코드는 그대로). 채점은 평가 기준이 만들어진 장만 하므로, 채점 기록의 `produced_slides` 가 실제로 채점한 장 수다
- LLM 은 서비스와 같은 규칙(요청 마감 120초 · 호출 12초 · 재시도 1번 · 서비스와 같은 재시도 분류)으로 감싸고
  (`coverage_lab.deadline_llm`), 서비스처럼 스트리밍 · 전송 시간 제한 12초로 받는다. 요청마다 마감과 기록이 새로
  시작하고, 측정 사이에는 앞 측정에서 버린 호출이 끝나기를 기다린다 (남은 수는 `leftover_calls` 로 남긴다).
  서비스의 역할별 출력 토큰 상한 · 추론 강도와 출력 잘림(`finish_reason=length`) 검사는 없다 (이 기능의 서비스 설정이 아직 없음)
- LLM 호출: 전체 약 330 ~ 510회 (평가 기준 최대 3 × (10+20+30) × 2 = 360, 채점 약 1.25 × 60 × 2 = 150, 실패한 장은 일찍
  멈춘다). 과금되므로 먼저 `--grid 10:4` 로 한 조합만 돌려 호출 수 · 시간을 보고 나머지를 돌린다. 한 조합만 돌릴 때는 파일을 쓰지 않는다
- 쓰는 곳: reports/results/timing.json (전체 조합을 돌렸을 때만). DB 는 쓰지 않는다
- 실행: 프로젝트 폴더에서 `.venv/Scripts/python.exe experiments/07_timing.py [--grid 10:4 [20:8 ...]]`
"""

import argparse
import time

import script_coverage.core as core
from coverage_lab.deadline_llm import CallLog, DeadlineLlm, wait_idle
from coverage_lab.llm import load_settings, structured_llm
from coverage_lab.results import write_result
from coverage_lab.timing import build_deck, request_row, slide_pool
from script_coverage.schemas import AnalyzeRequest, RubricRequest, RubricResponse
from script_coverage.script_analysis.schemas import FinalReview, SlideSemanticAnalysis, SlideUnits
from script_coverage.stt_evaluation.schemas import SemanticEvaluation, VerifierResult

REQUEST_DEADLINE_SECONDS = 120.0  # 요청 하나의 마감 (architecture.md 의 AI 예산)
CALL_TIMEOUT_SECONDS = 12.0
GRID_SLIDES = (10, 20, 30)
GRID_WORKERS = (4, 8)


def main() -> None:
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
    pool = slide_pool()

    def deadline_llm(schema, deadline: float, log: CallLog) -> DeadlineLlm:
        inner = structured_llm(  # 재시도는 감싸개가 세며 한다 (SDK 재시도는 끈다)
            schema, settings, max_retries=0, timeout=CALL_TIMEOUT_SECONDS, streaming=True
        )
        return DeadlineLlm(
            inner, deadline=deadline, log=log, timeout=CALL_TIMEOUT_SECONDS, max_retries=1
        )

    def measure(name: str, call, targets: int) -> tuple[dict, object]:
        """요청 하나를 새 마감 · 새 기록으로 부르고 기록을 모은다. 시작 전에 앞 측정의 버린 호출이 끝나기를 기다린다."""
        leftover = wait_idle(timeout=2 * CALL_TIMEOUT_SECONDS)
        log = CallLog()
        started = time.monotonic()
        deadline = started + REQUEST_DEADLINE_SECONDS
        try:
            response = call(deadline, log)
            failed = {f.slide_number: f.error.code for f in response.failed}
            produced = len(
                response.rubrics if isinstance(response, RubricResponse) else response.evaluations
            )
            error = None
        except core.CoverageError as exc:  # 대상 장이 모두 실패해 요청 전체가 실패한 경우
            response, failed, produced, error = None, {}, 0, exc.code
        row = request_row(
            seconds=round(time.monotonic() - started, 1),
            deadline_seconds=REQUEST_DEADLINE_SECONDS,
            targets=targets,
            failed=failed,
            produced=produced,
            request_error=error,
            log_summary=log.summary(),
        )
        row["leftover_calls"] = leftover
        print(
            f"  {name}: {row['seconds']}초, 결과 {produced} / {targets}장, 실패 장 {row['failed_slides']}"
            f"{' ' + error if error else ''}, 호출 {row['calls']} (재시도 {row['retries']}, "
            f"시간 초과 {row['timeouts']}), 호출당 중앙값 {row['call_median_seconds']}초 · "
            f"최대 {row['call_max_seconds']}초, 앞 측정에서 남은 호출 {leftover}"
        )
        return row, response

    results: dict[str, dict] = {}
    total_calls = 0
    for n, workers in grid:
        core.MAX_WORKERS = workers  # 코어가 모듈 전역 이름으로 읽으므로 실험 안에서만 바꾼다
        script_slides, take_slides = build_deck(pool, n)
        print(f"N={n} W={workers}")

        def rubric_call(deadline, log, slides=script_slides):
            llms = core.ScriptLLMs(
                semantic=deadline_llm(SlideSemanticAnalysis, deadline, log),
                final=deadline_llm(FinalReview, deadline, log),
                unit=deadline_llm(SlideUnits, deadline, log),
            )
            request = RubricRequest(pitch_id="timing", script_version_id="timing", slides=slides)
            return core.analyze_script(request, llms=llms, model=settings.model, cache=None)

        rubric_row, rubric = measure("평가 기준 만들기", rubric_call, n)
        if rubric is None:
            analyze_row = None
            print("  채점: 평가 기준이 없어 건너뜀")
        else:

            def analyze_call(deadline, log, slides=take_slides, rubrics=rubric.rubrics):
                llms = core.TakeLLMs(
                    eval=deadline_llm(SemanticEvaluation, deadline, log),
                    verifier=deadline_llm(VerifierResult, deadline, log),
                )
                request = AnalyzeRequest(take_id="timing", slides=slides, rubrics=rubrics)
                return core.evaluate_take(request, llms=llms, model=settings.model, cache=None)

            analyze_row, _ = measure("채점", analyze_call, n)
        results[f"N{n}_W{workers}"] = {
            "slides": n,
            "workers": workers,
            "rubric": rubric_row,
            "analyze": analyze_row,
        }
        total_calls += rubric_row["calls"] + (analyze_row["calls"] if analyze_row else 0)
        print(f"  누적 LLM 호출 {total_calls}")

    if args.grid:
        print("일부 조합만 돌렸으므로 timing.json 을 쓰지 않는다")
        return
    metrics = {
        "request_deadline_seconds": REQUEST_DEADLINE_SECONDS,
        "call_timeout_seconds": CALL_TIMEOUT_SECONDS,
        "client": "streaming, transport timeout = call timeout, no output token cap",
        "total_calls": total_calls,
        "combinations": results,
    }
    print(write_result("timing", settings.model, metrics))


if __name__ == "__main__":
    main()
