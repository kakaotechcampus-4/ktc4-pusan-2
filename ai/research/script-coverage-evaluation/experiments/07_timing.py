"""장 수 · 동시 개수별 소요 시간: 평가 기준 만들기가 시간 예산(120초) 안에, BE 재시도 1번까지 해서 모든 장을 만드는지 잰다.

- 방법: 가상대본 두 개의 장을 이어 붙여 N장 덱을 만든다 (`coverage_lab.timing.build_deck`). 조합(N장 · 동시 개수 W)마다
  최상위 `analyze_script`(모든 장)를 부르고, `--retry` 면 BE 처럼 1번 다시 부른다(최초 200 이면 LLM 오류 장만
  `slide_numbers` 로, 요청 전체가 실패했으면 전체를). `--concurrent K` 면 같은 요청 K개를 동시에 보내 서비스에 백그라운드
  요청이 겹친 경우를 잰다(BE 백그라운드 동시 개수 권장 4, `architecture.md` 3절). `--analyze` 면 동시 요청이 1개일 때
  그 평가 기준으로 `evaluate_take`(모든 장)도 한 번 부른다.
- 동시 개수: 평가 기준 만들기는 `script_coverage.core.RUBRIC_MAX_WORKERS`, 채점은 `MAX_WORKERS`(그대로 4)를 실험 안에서만 바꾼다.
- LLM: 서비스와 같은 규칙(요청 마감 120초 · 호출 한 번 `--call-timeout` 초 · 재시도 1번 · 서비스와 같은 재시도 분류)으로 감싸고
  (`coverage_lab.deadline_llm`, 실행 풀은 서비스와 같은 40개), 서비스처럼 스트리밍 · 전송 시간 제한을 호출 제한과 같게 받는다.
  캐시는 쓰지 않는다. 조합 사이에는 앞 조합에서 버린 호출이 끝나기를 기다리고, 그래도 남으면 멈춘다.
  `--cap 역할=N` 으로 서비스와 같은 역할별 출력 토큰 상한을 건다(역할 이름 · 값은 `08_output_tokens.py`).
  역할마다 끝난 이유(`finish_reason`, 잘림은 `length`)와 출력 토큰 수를 결과에 남긴다(모든 조합 · 재시도를 합친 값,
  시간 초과로 버린 호출은 끝까지 받지 못해 세지 않는다). 추론 강도는 보내지 않는다(서비스와 같음)
- LLM 호출(과금): 평가 기준 장마다 최대 3번 × 동시 요청 수 (+ 재시도 장), 채점 장마다 약 1.25번. 먼저 작은 조합 하나로
  호출 수 · 시간을 보고 나머지를 돌린다.
- 쓰는 곳: `--name` 을 주면 `reports/results/<name>.json`. DB 는 쓰지 않는다
- 실행: 프로젝트 폴더에서 `.venv/Scripts/python.exe experiments/07_timing.py --grid 30:8 30:12 --call-timeout 20 --retry --analyze --name timing_20s`
  (상한을 걸면 `--cap script_coverage.semantic=4096 ...` 을 더한다)
  (예전 측정 `timing.json` 은 `--grid` 없이 · 호출 12초 · 재시도 없이 · 도구를 고치기 전에 잰 것)
"""

import argparse
import threading
import time

import script_coverage.core as core
from coverage_lab.deadline_llm import CallLog, DeadlineLlm, wait_idle
from coverage_lab.llm import load_settings, structured_llm
from coverage_lab.results import write_result
from coverage_lab.timing import build_deck, request_row, rubric_with_retry, slide_pool
from coverage_lab.usage import UsageLog
from script_coverage.schemas import AnalyzeRequest, RubricRequest, RubricResponse
from script_coverage.script_analysis.schemas import FinalReview, SlideSemanticAnalysis, SlideUnits
from script_coverage.stt_evaluation.schemas import SemanticEvaluation, VerifierResult

REQUEST_DEADLINE_SECONDS = 120.0  # 요청 하나의 마감 (architecture.md 의 AI 예산)
ANALYZE_WORKERS = 4  # 채점 동시 개수 (config.MAX_WORKERS 와 같게 둔다)
LEFTOVER_WAIT_SECONDS = 60.0  # 조합 사이에 버린 호출이 끝나기를 기다리는 한도
DEFAULT_GRID = [(n, w) for n in (10, 20, 30) for w in (4, 8)]
#: 출력 스키마 → 역할 이름 (`08_output_tokens.py` 의 ROLES 와 같다)
ROLE_OF = {
    SlideSemanticAnalysis: "script_coverage.semantic",
    FinalReview: "script_coverage.final",
    SlideUnits: "script_coverage.unit",
    SemanticEvaluation: "script_coverage.eval",
    VerifierResult: "script_coverage.verifier",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--grid", nargs="*", help="N:W 조합 (예: 30:8). 없으면 10 · 20 · 30장 × 4 · 8"
    )
    parser.add_argument(
        "--call-timeout", type=float, default=12.0, help="호출 한 번의 시간 제한(초)"
    )
    parser.add_argument("--concurrent", type=int, default=1, help="같은 요청을 동시에 몇 개 보낼지")
    parser.add_argument("--retry", action="store_true", help="BE 처럼 1번 다시 부른다")
    parser.add_argument("--analyze", action="store_true", help="동시 요청이 1개일 때 채점도 잰다")
    parser.add_argument(
        "--name", help="결과 파일 이름 (reports/results/<name>.json). 없으면 쓰지 않는다"
    )
    parser.add_argument("--cap", nargs="*", default=[], help="역할=출력 토큰 상한")
    args = parser.parse_args()
    caps: dict[str, int] = {}
    for item in args.cap:
        role, sep, value = item.partition("=")
        if not sep or not value.isdigit():
            raise SystemExit(f"--cap 은 역할=정수 꼴이다: {item}")
        caps[role] = int(value)
    unknown = set(caps) - set(ROLE_OF.values())
    if unknown:
        raise SystemExit(f"모르는 역할: {sorted(unknown)}")
    usage = {role: UsageLog() for role in ROLE_OF.values()}
    grid = (
        [tuple(int(x) for x in item.split(":")) for item in args.grid]
        if args.grid
        else DEFAULT_GRID
    )
    call_timeout = args.call_timeout
    settings = load_settings()
    print(
        f"MODEL={settings.model} 호출 제한 {call_timeout:g}초 · 동시 요청 {args.concurrent} · 재시도 {args.retry}"
    )
    pool = slide_pool()

    def deadline_llm(schema, deadline: float, log: CallLog) -> DeadlineLlm:
        role = ROLE_OF[schema]
        inner = structured_llm(  # 재시도는 감싸개가 세며 한다 (SDK 재시도는 끈다)
            schema,
            settings,
            max_retries=0,
            timeout=call_timeout,
            streaming=True,
            max_completion_tokens=caps.get(role),
            callbacks=[usage[role]],
        )
        return DeadlineLlm(inner, deadline=deadline, log=log, timeout=call_timeout, max_retries=1)

    def measure(call, targets: int) -> tuple[dict, object]:
        """요청 하나를 새 마감 · 새 기록으로 부르고 기록을 모은다."""
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
        return row, response

    def rubric_call(slides, slide_numbers=None):
        def call(deadline, log):
            llms = core.ScriptLLMs(
                semantic=deadline_llm(SlideSemanticAnalysis, deadline, log),
                final=deadline_llm(FinalReview, deadline, log),
                unit=deadline_llm(SlideUnits, deadline, log),
            )
            request = RubricRequest(
                pitch_id="timing",
                script_version_id="timing",
                slides=slides,
                slide_numbers=slide_numbers,
            )
            return core.analyze_script(request, llms=llms, model=settings.model, cache=None)

        return call

    def rubric_job(n: int, slides) -> dict:
        """평가 기준 만들기 한 작업: 최초 요청 (+ BE 재시도 1번, `coverage_lab.timing.rubric_with_retry`)."""

        def request(slide_numbers):
            targets = len(slide_numbers) if slide_numbers else n
            return measure(rubric_call(slides, slide_numbers), targets)

        return rubric_with_retry(request, [s.slide_number for s in slides], retry=args.retry)

    results: dict[str, dict] = {}
    total_calls = 0
    for n, workers in grid:
        leftover = wait_idle(timeout=LEFTOVER_WAIT_SECONDS)
        if leftover:
            raise SystemExit(f"앞 조합에서 버린 호출 {leftover}개가 아직 돈다 — 측정을 멈춘다")
        core.RUBRIC_MAX_WORKERS, core.MAX_WORKERS = workers, ANALYZE_WORKERS
        script_slides, take_slides = build_deck(pool, n)
        print(f"N={n} W={workers}")
        jobs: list[dict] = [{} for _ in range(args.concurrent)]

        def run(k: int, slides=script_slides, n=n, jobs=jobs) -> None:
            jobs[k] = rubric_job(n, slides)

        threads = [threading.Thread(target=run, args=(k,)) for k in range(args.concurrent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for k, job in enumerate(jobs):
            first, retry = job["first"], job["retry"]
            print(
                f"  요청 {k + 1}: 최초 {first['seconds']}초 · {first['produced_slides']}/{n}장 "
                f"(호출 {first['calls']}, 시간 초과 {first['timeouts']}, 중앙값 {first['call_median_seconds']}초)"
                + (
                    f" → 재시도 {retry['seconds']}초 · {retry['produced_slides']}/{retry['target_slides']}장"
                    if retry
                    else ""
                )
                + f" → 최종 {job['final_produced']}/{n}장"
            )
        calls = sum(j["first"]["calls"] + (j["retry"]["calls"] if j["retry"] else 0) for j in jobs)
        analyze_row = None
        if args.analyze and args.concurrent == 1 and jobs[0]["rubrics"]:

            def analyze_call(deadline, log, slides=take_slides, rubrics=jobs[0]["rubrics"]):
                llms = core.TakeLLMs(
                    eval=deadline_llm(SemanticEvaluation, deadline, log),
                    verifier=deadline_llm(VerifierResult, deadline, log),
                )
                request = AnalyzeRequest(take_id="timing", slides=slides, rubrics=rubrics)
                return core.evaluate_take(request, llms=llms, model=settings.model, cache=None)

            leftover = wait_idle(
                timeout=LEFTOVER_WAIT_SECONDS
            )  # 평가 기준 만들기에서 버린 호출이 섞이지 않게
            if leftover:
                raise SystemExit(
                    f"평가 기준 만들기에서 버린 호출 {leftover}개가 아직 돈다 — 측정을 멈춘다"
                )
            analyze_row, _ = measure(analyze_call, n)
            calls += analyze_row["calls"]
            print(f"  채점: {analyze_row['seconds']}초 · {analyze_row['produced_slides']}/{n}장")
        results[f"N{n}_W{workers}_K{args.concurrent}"] = {
            "slides": n,
            "rubric_workers": workers,
            "analyze_workers": ANALYZE_WORKERS,
            "concurrent_requests": args.concurrent,
            "complete_requests": sum(j["complete"] for j in jobs),
            "jobs": [{k: v for k, v in j.items() if k != "rubrics"} for j in jobs],
            "analyze": analyze_row,
        }
        total_calls += calls
        print(f"  이 조합 LLM 호출 {calls} · 누적 {total_calls}")

    if not args.name:
        print("--name 이 없어 결과 파일을 쓰지 않는다")
        return
    metrics = {
        "request_deadline_seconds": REQUEST_DEADLINE_SECONDS,
        "call_timeout_seconds": call_timeout,
        "be_retry": args.retry,
        "client": "streaming, transport timeout = call timeout, executor 40, "
        + (f"output token caps {caps}" if caps else "no output token cap"),
        "output_token_caps": caps,
        "usage": {role: log.summary() for role, log in usage.items()},
        "total_calls": total_calls,
        "combinations": results,
    }
    print(write_result(args.name, settings.model, metrics))


if __name__ == "__main__":
    main()
