"""역할별 출력 토큰 수: 서비스의 역할별 출력 토큰 상한(`architecture.md` 9절)을 정하려고 잰다.

- 방법: 가상대본 두 개의 모든 장으로 평가 기준을 만들고(1차 분석 · 최종 결론 · 전달 단위), 그 평가 기준으로
  모든 가상 Take 를 채점한다(의미 평가 · 교차 검증). LLM 클라이언트에 `coverage_lab.usage.UsageLog` 를 붙여
  호출마다 출력 토큰 수(추론 토큰 포함)와 끝난 이유를 역할별로 모은다.
- LLM: 서비스와 같은 조건(스트리밍 · 같은 모델 묶음 · 추론 강도를 보내지 않음). 상한을 재는 실행은 출력 상한을
  주지 않고(자연스러운 길이를 보려고), 호출 시간 제한은 넉넉히(`--call-timeout`, 기본 60초) 둔다. `--cap 역할=N` 을
  주면 그 상한을 걸고 다시 돌려 잘림(`finish_reason=length`)이 없는지 본다. 캐시는 쓰지 않는다.
- 상한: 잰 최대 × 2 를 1,024 단위로 올림, 최소 2,048. 잘린 호출이 있는 역할은 정하지 않는다(`usage.recommended_cap`).
- LLM 호출(과금): 평가 기준 장마다 3번(가상대본 두 개 20장이면 60번) + 채점 장마다 약 1.25번(Take 18개면 약 225번).
  `--takes K` 로 대본마다 앞 K개 Take 만 쓸 수 있다.
- 쓰는 곳: `--name` 을 주면 `reports/results/<name>.json`. DB 는 쓰지 않는다
- 실행: 키가 든 `ai/.env` 를 찾도록 `ai/` 폴더에서 이 폴더의 python 으로
  `research/script-coverage-evaluation/.venv/Scripts/python.exe research/script-coverage-evaluation/experiments/08_output_tokens.py --name output_tokens`
"""

import argparse
import time

import pitch_coach_ai.features.script_coverage.core as core
from coverage_lab.datasets import load_script_json, load_take, script_files, take_files
from coverage_lab.llm import load_settings, structured_llm
from coverage_lab.results import write_result
from coverage_lab.usage import UsageLog
from pitch_coach_ai.features.script_coverage.schemas import (
    AnalyzeRequest,
    RubricRequest,
    ScriptSlide,
    TakeSlide,
)
from pitch_coach_ai.features.script_coverage.script_analysis.schemas import (
    FinalReview,
    SlideSemanticAnalysis,
    SlideUnits,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.schemas import (
    SemanticEvaluation,
    VerifierResult,
)

#: 역할 이름 → 출력 스키마. 이름은 서비스 `settings.LLM_ROLES` 에 더할 역할 이름이다
ROLES = {
    "script_coverage.semantic": SlideSemanticAnalysis,  # 1차 분석
    "script_coverage.final": FinalReview,  # 최종 결론
    "script_coverage.unit": SlideUnits,  # 전달 단위
    "script_coverage.eval": SemanticEvaluation,  # 의미 평가(채점)
    "script_coverage.verifier": VerifierResult,  # 교차 검증
}


def _caps(items: list[str]) -> dict[str, int]:
    caps = {}
    for item in items:
        role, value = item.split("=")
        if role not in ROLES:
            raise SystemExit(f"모르는 역할: {role}")
        caps[role] = int(value)
    return caps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--call-timeout", type=float, default=60.0, help="호출 한 번의 시간 제한(초)"
    )
    parser.add_argument("--takes", type=int, help="대본마다 쓸 Take 수 (없으면 모두)")
    parser.add_argument("--scripts", type=int, help="쓸 대본 수 (없으면 모두)")
    parser.add_argument(
        "--cap", nargs="*", default=[], help="역할=출력 토큰 상한 (예: script_coverage.eval=4096)"
    )
    parser.add_argument("--name", help="결과 파일 이름 (reports/results/<name>.json)")
    args = parser.parse_args()
    caps = _caps(args.cap)
    settings = load_settings()
    logs = {role: UsageLog() for role in ROLES}

    def llm(role: str):
        return structured_llm(
            ROLES[role],
            settings,
            max_retries=1,
            timeout=args.call_timeout,
            streaming=True,
            max_completion_tokens=caps.get(role),
            callbacks=[logs[role]],
        )

    script_llms = core.ScriptLLMs(
        semantic=llm("script_coverage.semantic"),
        final=llm("script_coverage.final"),
        unit=llm("script_coverage.unit"),
    )
    take_llms = core.TakeLLMs(
        eval=llm("script_coverage.eval"), verifier=llm("script_coverage.verifier")
    )
    print(f"MODEL={settings.model} 상한 {caps or '없음'} · 호출 제한 {args.call_timeout:g}초")

    started = time.monotonic()
    failed: dict[str, dict[str, str]] = {}
    scripts = script_files()[: args.scripts]
    for path in scripts:
        slides = [
            ScriptSlide(slide_number=s.slide_number, script=s.script)
            for s in load_script_json(path)
        ]
        try:
            rubric = core.analyze_script(
                RubricRequest(pitch_id="tokens", script_version_id=path.stem, slides=slides),
                llms=script_llms,
                model=settings.model,
                cache=None,
            )
        except core.CoverageError as exc:  # 모든 장이 실패 — 잰 토큰은 남기고 다음 대본으로
            failed[f"rubric {path.stem}"] = {"all": exc.code}
            print(f"평가 기준 {path.stem}: 요청 전체 실패 {exc.code}")
            continue
        failed[f"rubric {path.stem}"] = {str(f.slide_number): f.error.code for f in rubric.failed}
        print(f"평가 기준 {path.stem}: {len(rubric.rubrics)}장, 실패 {len(rubric.failed)}")
        takes = [p for p in take_files() if load_take(p).script_name == path.stem][: args.takes]
        for take_path in takes:
            take = load_take(take_path)
            try:
                response = core.evaluate_take(
                    AnalyzeRequest(
                        take_id=take_path.stem,
                        slides=[
                            TakeSlide(slide_number=s.slide_number, stt=s.stt) for s in take.slides
                        ],
                        rubrics=rubric.rubrics,
                    ),
                    llms=take_llms,
                    model=settings.model,
                    cache=None,
                )
            except core.CoverageError as exc:
                failed[f"take {take_path.stem}"] = {"all": exc.code}
                print(f"  채점 {take_path.stem}: 요청 전체 실패 {exc.code}")
                continue
            failed[f"take {take_path.stem}"] = {
                str(f.slide_number): f.error.code for f in response.failed
            }
            print(f"  채점 {take_path.stem}: 실패 {len(response.failed)}")

    roles = {role: log.summary() for role, log in logs.items()}
    for role, summary in roles.items():
        print(
            f"{role}: {summary['calls']}번 · 최대 {summary['output_tokens']['max']} · 끝난 이유 "
            f"{summary['finish_reason']} → 상한 {summary['recommended_cap']}"
        )
    if args.name:
        path = write_result(
            args.name,
            settings.model,
            {
                "method": "가상대본 · 가상 Take, 서비스와 같은 스트리밍 · 추론 강도 없음, 캐시 없음",
                "call_timeout_seconds": args.call_timeout,
                "caps": caps,
                "scripts": [p.stem for p in scripts],
                "takes_per_script": args.takes,
                "seconds": round(time.monotonic() - started, 1),
                "roles": roles,
                "failed": {k: v for k, v in failed.items() if v},
            },
        )
        print(path)


if __name__ == "__main__":
    main()
