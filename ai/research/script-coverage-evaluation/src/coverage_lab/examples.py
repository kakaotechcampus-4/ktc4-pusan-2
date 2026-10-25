"""API 예시 파일 만들기: 연구 캐시의 실제 LLM 출력으로 요청 · 응답 · LLM 캐시 JSON 을 쓴다.

`python -m coverage_lab.examples` 로 `src/script_coverage/examples/<api>/` 에 `request.json` ·
`response.json` · `llm_cache.json` 을 만든다. 과금이 없다: LLM 은 부르면 `TimeoutError` 를 내는
객체이고, 응답은 연구 캐시(`outputs/rubrics.sqlite`)에 있는 실제 LLM 출력으로만 만든다.
캐시에 없는 호출은 실패한 장이 된다 (예시가 `failed` 를 보이는 데 일부러 쓴다).

- `request.json` / `response.json`: 최상위 진입 함수(`script_coverage.core`)의 입력 · 출력
- `llm_cache.json`: 이 요청이 읽은 캐시 항목 `[{kind, key, config_hash, output}]` (정렬). 서비스로 옮긴 계약
  테스트(`tests/unit/test_examples.py`)가 이것으로 캐시를 만들어 같은 응답이 나오는지 본다.

만드는 예시: `rubric` (`/scripts/rubric`), `analyze` (`/takes/analyze`), `rescore` (`/takes/rescore`).
`analyze` 의 평가 기준은 `rubric` 과 같은 방식으로(연구 캐시 재생) 코어에서 새로 만든다 — 평가 기준 스키마
버전이 올라 `rubric_id` 가 바뀌었으므로 연구 DB 에 저장된 예전 채점 행은 쓰지 않는다 (어느 장에 비슷한 말이
있었는지 찾는 데만 썼다). `rescore` 는 LLM 이 없어 `llm_cache.json` 이 없다.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from script_coverage.core import (
    ScriptLLMs,
    TakeLLMs,
    analyze_script,
    evaluate_take,
    rescore_evaluation,
)
from script_coverage.schemas import AnalyzeRequest, RescoreRequest, RubricRequest

from .cache import SqliteLLMCache
from .datasets import load_script_json, load_take
from .paths import DB_PATH, PROJECT_ROOT, SCRIPT_DIR, STT_DIR

EXAMPLES_DIR = PROJECT_ROOT / "src" / "script_coverage" / "examples"

# ── rubric 예시: 가상대본1 의 모든 장을 보내고 6 · 7번 장만 만든다 ──
RUBRIC_SCRIPT = "가상대본1"
RUBRIC_SLIDE_NUMBERS = [6, 7]  # 6: 수치가 많은 장 (성공), 7: 전달 단위 호출이 시간 초과 (실패)
RUBRIC_FAILING_SLIDE = 7
PITCH_ID = "p-1"
SCRIPT_VERSION_ID = "sv-3"

# ── analyze · rescore 예시: 가상대본1_take9 의 모든 장 STT 를 보내고 2 · 4 · 6번 장만 채점한다 ──
# 연구 DB 의 저장된 채점에서 사람이 고른 장이다 (take9 는 2 · 3 · 5 · 6 · 8 · 9번 장에 비슷한 말이 있다)
TAKE_FILE = "가상대본1_take9"
TAKE_ID = "take-123"
ANALYZE_SLIDE_NUMBERS = [2, 4, 6]
ANALYZE_RUBRIC_SLIDES = [2, 4]  # 6번 장은 평가 기준을 싣지 않는다 → RUBRIC_MISSING
SIMILAR_SLIDE = 2  # 비슷한 말(R1 · R2)이 있는 장 → 성공
TIMEOUT_SLIDE = 4  # 의미 평가 캐시 항목을 빼서 LLM_TIMEOUT
RESCORE_ANSWER = {"slide_number": SIMILAR_SLIDE, "item_id": "R1", "answer": "as_script"}


class TimeoutLLM:
    """부르면 시간 초과. 캐시에 없는 호출이 실패한 장이 되게 한다 (실제 호출은 하지 않는다)."""

    def invoke(self, messages):
        raise TimeoutError("예시를 만들 때는 LLM 을 부르지 않는다")


class RecordingCache:
    """읽기 전용 캐시 한 겹: `get` 이 돌려준 항목을 기록하고, `put` 은 아무것도 하지 않는다.

    `drop` 에 든 (kind, key) 는 없는 것처럼 None 을 돌려준다 (그 호출을 일부러 실패시킨다).
    """

    def __init__(self, inner, drop: frozenset[tuple[str, str]] = frozenset()):
        self.inner = inner
        self.drop = drop
        self.records: dict[tuple[str, str, str], str] = {}

    def get(self, kind: str, key: str, config_hash: str) -> str | None:
        if (kind, key) in self.drop:
            return None
        output = self.inner.get(kind, key, config_hash)
        if output is not None:
            self.records[kind, key, config_hash] = output
        return output

    def put(self, kind: str, key: str, config_hash: str, output_json: str) -> None:
        pass  # 예시를 만드는 일이 연구 캐시를 바꾸지 않는다

    def entries(self) -> list[dict]:
        """기록한 항목. (kind, key, config_hash) 순으로 정렬하고 출력은 JSON 값으로 푼다."""
        return [
            {"kind": kind, "key": key, "config_hash": config_hash, "output": json.loads(output)}
            for (kind, key, config_hash), output in sorted(self.records.items())
        ]


def open_cache_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """연구 캐시를 읽기 전용으로 연다. 없으면 분명한 오류로 멈춘다."""
    if not path.is_file():
        raise FileNotFoundError(
            f"연구 캐시가 없다: {path} — experiments/01_build_rubrics.py 로 만든다"
        )
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def research_model(conn: sqlite3.Connection, script_name: str) -> str:
    """연구 캐시에 저장된 평가 기준이 쓴 모델 이름. 캐시 항목의 설정 해시가 이 이름에 의존한다."""
    models = {
        json.loads(rubric_json)["meta"]["llm_model"]
        for (rubric_json,) in conn.execute(
            "SELECT rubric_json FROM evaluation_rubrics WHERE script_name = ?", (script_name,)
        )
    }
    if len(models) != 1:
        raise RuntimeError(f"연구 캐시에서 {script_name} 의 모델 이름을 하나로 못 찾았다: {models}")
    return models.pop()


def _rubric_request() -> dict:
    slides = load_script_json(SCRIPT_DIR / f"{RUBRIC_SCRIPT}.json")
    return RubricRequest(
        pitch_id=PITCH_ID,
        script_version_id=SCRIPT_VERSION_ID,
        slides=[{"slide_number": s.slide_number, "script": s.script} for s in slides],
        slide_numbers=RUBRIC_SLIDE_NUMBERS,
    ).model_dump(mode="json")


def _run_rubric(request: dict, model: str, cache: RecordingCache):
    llm = TimeoutLLM()
    return analyze_script(
        request, llms=ScriptLLMs(semantic=llm, final=llm, unit=llm), model=model, cache=cache
    )


def build_rubric_example(conn: sqlite3.Connection) -> dict[str, object]:
    """`rubric` 예시의 파일 내용 {파일 이름: JSON 값}."""
    model = research_model(conn, RUBRIC_SCRIPT)
    request = _rubric_request()
    # 실패시킬 장의 전달 단위 캐시 키: 그 장만 만드는 요청을 한 번 돌려 읽은 항목에서 찾는다
    probe = RecordingCache(SqliteLLMCache(conn))
    _run_rubric({**request, "slide_numbers": [RUBRIC_FAILING_SLIDE]}, model, probe)
    unit_keys = {key for kind, key, _ in probe.records if kind == "script.units"}
    if len(unit_keys) != 1:
        raise RuntimeError(f"{RUBRIC_FAILING_SLIDE}번 장의 전달 단위 캐시를 못 찾았다: {unit_keys}")
    cache = RecordingCache(
        SqliteLLMCache(conn), drop=frozenset(("script.units", key) for key in unit_keys)
    )
    response = _run_rubric(request, model, cache)
    if len(response.rubrics) != 1 or len(response.failed) != 1:
        raise RuntimeError("예시가 성공 1 · 실패 1 이 아니다: 연구 캐시가 이 요청과 맞지 않는다")
    return {
        "request.json": request,
        "response.json": response.model_dump(mode="json"),
        "llm_cache.json": cache.entries(),
    }


def _timeout_take_llms() -> TakeLLMs:
    llm = TimeoutLLM()
    return TakeLLMs(eval=llm, verifier=llm)


def _analyze_request(conn: sqlite3.Connection, model: str) -> dict:
    """STT 는 가상 Take 파일에서, 평가 기준은 연구 캐시를 재생해 코어에서 새로 만든다."""
    script = _run_rubric(
        {**_rubric_request(), "slide_numbers": ANALYZE_RUBRIC_SLIDES},
        model,
        RecordingCache(SqliteLLMCache(conn)),
    )
    if len(script.rubrics) != len(ANALYZE_RUBRIC_SLIDES) or script.failed:
        raise RuntimeError("평가 기준을 연구 캐시로 만들지 못했다: 연구 캐시가 맞지 않는다")
    take = load_take(STT_DIR / f"{TAKE_FILE}.json")
    return AnalyzeRequest(
        take_id=TAKE_ID,
        slides=[{"slide_number": s.slide_number, "stt": s.stt} for s in take.slides],
        rubrics=script.rubrics,
        slide_numbers=ANALYZE_SLIDE_NUMBERS,
    ).model_dump(mode="json")


def build_take_examples(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    """`analyze` · `rescore` 예시의 파일 내용 {예시 이름: {파일 이름: JSON 값}}."""
    model = research_model(conn, RUBRIC_SCRIPT)
    request = _analyze_request(conn, model)
    # 실패시킬 장의 의미 평가 캐시 키: 그 장만 채점하는 요청을 한 번 돌려 읽은 항목에서 찾는다
    probe = RecordingCache(SqliteLLMCache(conn))
    evaluate_take(
        {**request, "slide_numbers": [TIMEOUT_SLIDE]},
        llms=_timeout_take_llms(),
        model=model,
        cache=probe,
    )
    keys = {key for kind, key, _ in probe.records if kind == "stt.semantic"}
    if len(keys) != 1:
        raise RuntimeError(f"{TIMEOUT_SLIDE}번 장의 의미 평가 캐시를 못 찾았다: {keys}")
    cache = RecordingCache(
        SqliteLLMCache(conn), drop=frozenset(("stt.semantic", key) for key in keys)
    )
    response = evaluate_take(request, llms=_timeout_take_llms(), model=model, cache=cache)
    if (
        [e.slide_number for e in response.evaluations] != [SIMILAR_SLIDE]
        or [(f.slide_number, f.error.code) for f in response.failed]
        != [(TIMEOUT_SLIDE, "LLM_TIMEOUT"), (6, "RUBRIC_MISSING")]
        or not response.confirmations
    ):
        raise RuntimeError("analyze 예시가 성공 1 · 실패 2 · 확인할 항목 있음이 아니다")
    rescore_request = RescoreRequest(
        take_id=TAKE_ID,
        evaluations=response.evaluations,
        rubrics=[r for r in request["rubrics"] if r["meta"]["slide_number"] == SIMILAR_SLIDE],
        answers=[RESCORE_ANSWER],
        failed=response.failed,
    ).model_dump(mode="json")
    rescored = rescore_evaluation(rescore_request)
    if not rescored.evaluations[0].applied_answers:
        raise RuntimeError("rescore 예시에 반영된 답이 없다")
    return {
        "analyze": {
            "request.json": request,
            "response.json": response.model_dump(mode="json"),
            "llm_cache.json": cache.entries(),
        },
        "rescore": {
            "request.json": rescore_request,
            "response.json": rescored.model_dump(mode="json"),
        },
    }


def dump_json(value) -> str:
    """예시 파일의 글: 한글 그대로, 들여쓰기 2, 실수는 반올림하지 않는다, 끝에 개행."""
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build_examples(out_dir: Path = EXAMPLES_DIR, db_path: Path = DB_PATH) -> list[Path]:
    """모든 예시를 `out_dir/<api>/` 에 쓴다 (줄 끝은 LF). 쓴 파일 목록을 돌려준다."""
    conn = open_cache_db(db_path)
    try:
        examples = {"rubric": build_rubric_example(conn), **build_take_examples(conn)}
    finally:
        conn.close()
    written = []
    for api, files in examples.items():
        folder = out_dir / api
        folder.mkdir(parents=True, exist_ok=True)
        for name, value in files.items():
            path = folder / name
            path.write_bytes(dump_json(value).encode("utf-8"))
            written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="API 예시 파일을 연구 캐시에서 다시 만든다")
    parser.add_argument(
        "--out", type=Path, default=EXAMPLES_DIR, help="쓸 폴더 (기본: 커밋하는 예시 폴더)"
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="연구 캐시 SQLite")
    args = parser.parse_args()
    for path in build_examples(args.out, args.db):
        print(path)


if __name__ == "__main__":
    main()
