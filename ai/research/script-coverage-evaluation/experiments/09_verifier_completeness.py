"""교차 검증 출력 검사의 영향: 실제 검증 출력 중 '대상 문장 · 전달 단위를 다 덮지 못해' 장 실패가 되는 비율.

- 방법: 연구 캐시(평가 기준 · LLM 응답)를 복사본으로 읽어 가상 Take 18개를 다시 채점한다. LLM 은 부르면 바로 오류를
  내는 대역이라 과금이 없다. 같은 채점을 두 번 한다 — 교차 검증 출력 검사(`verify.check_verification`)를 켠 채로
  한 번, 끈 채로 한 번. 검사를 끄면 캐시에 있던 검증 출력이 그대로 쓰이고, 켜면 대상을 다 덮지 못한 출력은 캐시에
  없는 것으로 보고 LLM 을 다시 부르다가(대역이라 실패) 그 장이 실패한다. 두 실행의 '교차 검증' 실패 장 수의 차이가
  검사 때문에 새로 실패하는 장이다. 의미 평가 캐시가 없는 장은 두 실행에서 똑같이 실패하므로 차이에 들지 않는다.
- 캐시의 설정 해시에 모델 이름이 들어가므로 캐시를 만든 모델 이름(`--model`, 없으면 `ai/.env` 의 `OPENAI_MODEL`)으로 찾는다(키는 쓰지 않는다).
- 쓰는 곳: `--name` 을 주면 `reports/results/<name>.json`. 원본 DB 는 바꾸지 않는다(복사본만 읽음)
- 실행: `ai/` 폴더에서 `research/script-coverage-evaluation/.venv/Scripts/python.exe
  research/script-coverage-evaluation/experiments/09_verifier_completeness.py --db <rubrics.sqlite> --name verifier_completeness`
"""

import argparse
import shutil
import sqlite3
import tempfile
from pathlib import Path

import script_coverage.stt_evaluation.core as stt_core
from coverage_lab.cache import SqliteLLMCache
from coverage_lab.datasets import load_take, take_files
from coverage_lab.llm import load_settings
from coverage_lab.paths import DB_PATH
from coverage_lab.results import write_result
from coverage_lab.store import load_rubrics


class _NoCall:
    """부르면 실패하는 LLM 대역 (캐시에 없는 호출을 막는다)."""

    def invoke(self, messages):
        raise RuntimeError("캐시에 없는 LLM 호출")


class _ReadOnly:
    """캐시를 읽기만 한다 (실패한 재호출 결과를 남기지 않게)."""

    def __init__(self, inner):
        self.inner = inner

    def get(self, kind, key, config_hash):
        return self.inner.get(kind, key, config_hash)

    def put(self, kind, key, config_hash, output_json):
        return None


def _score_all(conn: sqlite3.Connection, model: str) -> dict:
    cache = _ReadOnly(SqliteLLMCache(conn))
    rows, verified, ver_failed, sem_failed = [], 0, 0, 0
    for path in take_files():
        take = load_take(path)
        _, stats = stt_core.evaluate_take(
            take,
            load_rubrics(conn, take.script_name),
            eval_llm=_NoCall(),
            verifier_llm=_NoCall(),
            model=model,
            cache=cache,
        )
        causes = stats["causes"]
        v = sum(1 for c in causes.values() if c.startswith("교차 검증"))
        s = sum(1 for c in causes.values() if c.startswith("의미 평가"))
        verified += stats["verified_slides"]
        ver_failed += v
        sem_failed += s
        rows.append(
            {"take": path.stem, "slides": stats["slides"], "verified_slides": stats["verified_slides"],
             "verifier_failed": v, "semantic_failed": s}
        )  # fmt: skip
    return {"verified_slides": verified, "verifier_failed": ver_failed,
            "semantic_failed": sem_failed, "takes": rows}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path, default=DB_PATH, help="연구 캐시 SQLite (복사본을 읽는다)"
    )
    parser.add_argument("--name", help="결과 파일 이름 (reports/results/<name>.json)")
    parser.add_argument("--model", help="캐시를 만든 모델 이름 (없으면 ai/.env 의 OPENAI_MODEL)")
    args = parser.parse_args()
    model = args.model or load_settings().model
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "rubrics.sqlite"
        shutil.copy(args.db, copy)
        conn = sqlite3.connect(copy)
        with_check = _score_all(conn, model)
        original = stt_core.check_verification
        stt_core.check_verification = lambda sentences, result: None  # 검사를 끈다
        try:
            without_check = _score_all(conn, model)
        finally:
            stt_core.check_verification = original
        conn.close()
    newly_failed = with_check["verifier_failed"] - without_check["verifier_failed"]
    verified = without_check["verified_slides"]
    summary = {
        "verified_slides": verified,
        "newly_failed_by_check": newly_failed,
        "newly_failed_ratio": round(newly_failed / verified, 4) if verified else None,
        "semantic_cache_misses": without_check["semantic_failed"],
    }
    print(f"MODEL={model} {summary}")
    if args.name:
        print(write_result(args.name, model, {"summary": summary, "with_check": with_check,
                                              "without_check": without_check}))  # fmt: skip


if __name__ == "__main__":
    main()
