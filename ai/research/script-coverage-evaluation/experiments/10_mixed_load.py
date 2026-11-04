"""서비스 혼합 부하: 평가 기준 만들기 · 채점이 겹칠 때 실행 스레드가 모자라 다른 요청이 밀리거나 장이 실패하는지 본다.

- 왜: 서비스는 LLM 호출을 실행 스레드 40개(`common/llm.py`)에서, 동기 라우터를 스레드 풀(기본 40)에서 돌린다.
  평가 기준 만들기는 요청 하나가 LLM 호출을 `RUBRIC_MAX_WORKERS`(12)개까지 동시에 하므로, 요청이 겹치면
  실행 스레드가 차서 호출이 줄을 서고, 줄 선 시간도 호출 한 번 시간 제한(20초)에 든다. 실시간 코칭
  (`/coach/evaluate`, 1초마다)은 아직 서비스에 없어, 같은 동기 라우터인 `/takes/rescore`(LLM 없음)의 응답
  시간으로 다른 요청이 밀리는지를 잰다
- 방법: 서비스 앱을 uvicorn 으로 이 프로세스 안에 띄운다(실제 스레드 풀 · 실행 스레드). LLM 은 가짜다:
  서비스의 LLM 객체(`StructuredLlm`, 시간 제한 · 재시도 · 마감 그대로)에 호출마다 `--delay` 초 사이에서 잠깐
  멈췄다 답하는 가짜 Runnable 을 넣는다(과금 없음). 장면마다 평가 기준 · 채점 요청을 동시에 보내면서
  0.25초마다 `/takes/rescore` 를 부른다
  - `be_limit`: BE 가 지키는 동시 개수(백그라운드 합계 4, 그 안에서 평가 기준 2개, `architecture.md` 3절)
    — 평가 기준 2 + 채점 2
  - `over_limit`: 평가 기준 4개가 한꺼번에(BE 제한을 넘긴 경우)
- 한계: 실제 LLM 의 지연 분포 · 게이트웨이 동시 처리 한도는 재지 않는다. 시간 예산(120초) 안에 실제 긴 대본이
  끝나는지는 `07_timing.py`(실제 LLM)가 본다
- 쓰는 곳: `reports/results/mixed_load.json`
- 실행: 프로젝트 폴더에서 `.venv/Scripts/python.exe experiments/10_mixed_load.py` (약 2~3분)
"""

import argparse
import json
import random
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # tests.lab.fakes (가짜 LLM 답)

from coverage_lab.results import write_result  # noqa: E402
from coverage_lab.timing import build_deck, slide_pool  # noqa: E402
from pitch_coach_ai.api import script_coverage as api  # noqa: E402
from pitch_coach_ai.common.llm import StructuredLlm  # noqa: E402
from pitch_coach_ai.features import script_coverage  # noqa: E402
from pitch_coach_ai.main import create_app  # noqa: E402
from pitch_coach_ai.settings import Settings  # noqa: E402
from tests.lab.fakes import DeckAwareLLM, KeepFirstVerifier, SaidAllLLM  # noqa: E402

PORT = 8032
BASE = f"http://127.0.0.1:{PORT}"
RESCORE = json.loads(
    (Path(script_coverage.__file__).parent / "examples" / "rescore" / "request.json").read_text(
        encoding="utf-8"
    )
)


class SlowRunnable:
    """LangChain Runnable 흉내: `.invoke(input, config)` 가 `delay` 초 사이에서 멈췄다가 가짜 답을 낸다."""

    def __init__(self, inner, delay: tuple[float, float], rng: random.Random, lock: threading.Lock):
        self.inner, self.delay, self.rng, self.lock = inner, delay, rng, lock

    def invoke(self, messages, config=None):
        with self.lock:
            wait = self.rng.uniform(*self.delay)
        time.sleep(wait)
        return self.inner.invoke(messages)


def make_factory(delay: tuple[float, float], timeout: float, seed: int):
    rng, lock = random.Random(seed), threading.Lock()

    def inner_for(role: str):
        stage = role.split(".", 1)[1]
        if stage in ("semantic", "final", "unit"):
            return DeckAwareLLM(stage)
        return SaidAllLLM() if stage == "eval" else KeepFirstVerifier()

    def factory(schema, role, deadline, s):
        return StructuredLlm(
            SlowRunnable(inner_for(role), delay, rng, lock),
            role=role,
            timeout_seconds=timeout,
            max_retries=1,
            deadline=deadline,
        )

    return factory


def serve(app) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        try:
            if httpx.get(f"{BASE}/ready", timeout=1).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("서버가 준비되지 않았다")


def probe(stop: threading.Event, out: list[float], errors: list[str]) -> None:
    """0.25초마다 `/takes/rescore`. 실패는 `errors` 에 담아 장면을 측정 실패로 남긴다."""
    with httpx.Client(timeout=30) as c:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                r = c.post(f"{BASE}/takes/rescore", json=RESCORE)
            except httpx.HTTPError as exc:
                errors.append(type(exc).__name__)
            else:
                out.append((time.perf_counter() - t0) * 1000)
                if r.status_code != 200:
                    errors.append(f"status {r.status_code}")
            time.sleep(0.25)


def summary(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0}
    q = statistics.quantiles(ms, n=20) if len(ms) >= 20 else [max(ms)] * 19
    return {
        "n": len(ms),
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(q[18], 1),
        "max_ms": round(max(ms), 1),
    }


def run_scene(name: str, jobs: list[tuple[str, dict]]) -> dict:
    stop, lat, errors = threading.Event(), [], []
    t = threading.Thread(target=probe, args=(stop, lat, errors), daemon=True)
    t.start()
    results: list[dict] = [None] * len(jobs)

    def call(i: int, path: str, body: dict) -> None:
        t0 = time.perf_counter()
        r = httpx.post(f"{BASE}{path}", json=body, timeout=200)
        data = r.json()
        failed = data.get("failed", []) if r.status_code == 200 else []
        results[i] = {
            "path": path,
            "status": r.status_code,
            "seconds": round(time.perf_counter() - t0, 1),
            "failed": len(failed),
            "failed_codes": sorted({f["error"]["code"] for f in failed}),
        }

    threads = [threading.Thread(target=call, args=(i, p, b)) for i, (p, b) in enumerate(jobs)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if not jobs:
        time.sleep(10)  # 부하 없는 기준: 10초 동안 잰다
    stop.set()
    t.join()
    row = {
        "scene": name,
        "requests": results,
        "rescore_while_loaded": summary(lat),
        "probe_errors": errors,
        "ok": not errors and all(r["status"] == 200 for r in results),
    }
    print(json.dumps(row, ensure_ascii=False))
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slides", type=int, default=20)
    ap.add_argument(
        "--delay", type=float, nargs=2, default=(4.0, 9.0), help="가짜 LLM 한 번의 지연(초) 범위"
    )
    ap.add_argument("--call-timeout", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    settings = Settings(
        _env_file=None,
        openai_base_url="http://127.0.0.1:9/v1",
        openai_api_key="fake",
        openai_model="fake-model",
        script_coverage_llm_timeout_seconds=args.call_timeout,
    )
    app = create_app(settings)
    app.dependency_overrides[api.get_llm_factory] = lambda: make_factory(
        tuple(args.delay), args.call_timeout, args.seed
    )
    server = serve(app)

    scripts, stt = build_deck(slide_pool(), args.slides)
    rubric_req = {
        "pitch_id": "p",
        "script_version_id": "sv",
        "slides": [s.model_dump() for s in scripts],
    }
    # 채점에 쓸 평가 기준을 미리 만든다(지연 없이)
    app.dependency_overrides[api.get_llm_factory] = lambda: make_factory((0.0, 0.0), 20.0, 0)
    rubrics = httpx.post(f"{BASE}/scripts/rubric", json=rubric_req, timeout=60).json()["rubrics"]
    app.dependency_overrides[api.get_llm_factory] = lambda: make_factory(
        tuple(args.delay), args.call_timeout, args.seed
    )
    analyze_req = {"take_id": "t", "slides": [s.model_dump() for s in stt], "rubrics": rubrics}

    idle = run_scene("idle", [])
    be = run_scene(
        "be_limit", [("/scripts/rubric", rubric_req)] * 2 + [("/takes/analyze", analyze_req)] * 2
    )
    over = run_scene("over_limit", [("/scripts/rubric", rubric_req)] * 4)
    server.should_exit = True

    write_result(
        "mixed_load",
        "fake (지연만 흉내)",
        {
            "method": "uvicorn in-process, fake LLM via service StructuredLlm",
            "slides": args.slides,
            "fake_llm_delay_s": list(args.delay),
            "call_timeout_s": args.call_timeout,
            "rubric_max_workers": script_coverage.core.RUBRIC_MAX_WORKERS,
            "llm_executor_threads": 40,
            "scenes": [idle, be, over],
        },
    )


if __name__ == "__main__":
    main()
