"""data/scripts/*.txt 대본을 장으로 나누고 results/에 JSON으로 저장하거나, 라벨과 비교해 평가합니다.

    python run.py --file script3.txt          # 대본 하나
    python run.py --file script1.txt script2.txt
    python run.py --all                       # 전체
    python run.py --all --target-ms 300000    # 발표 제한 시간에 맞춰 예상 시간 나누기
    python run.py --eval --out reports/results/script_parser_eval.json   # data/labels/와 비교
"""

import argparse
import json
import logging
import os
import re
import statistics
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from script_parser import parse_script
from script_parser.schemas import LLMOutput, ParseRequest, ParseResponse

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "data" / "scripts"
LABELS_DIR = ROOT / "data" / "labels"
RESULTS_DIR = ROOT / "results"
# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = ROOT.parents[1] / ".env"
# 서비스의 LLM 호출 한 번 시간 제한 기본값(docs/ai/architecture.md 10절). 넘었는지만 기록한다
SERVICE_TIMEOUT_MS = 12_000


def build_llm(include_raw: bool = False, reasoning_effort: str | None = None) -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다.

    include_raw=True면 {"raw", "parsed", "parsing_error"}를 돌려줍니다(평가에서 토큰 수를 잴 때).
    reasoning_effort가 None이면 보내지 않습니다(모델 · 게이트웨이 기본값).
    """
    load_dotenv(ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(f"OPENAI_API_KEY / OPENAI_MODEL이 비어 있습니다: {ENV_PATH}")

    llm = ChatOpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return llm.with_structured_output(LLMOutput, include_raw=include_raw)


class RecordingLLM:
    """parse_script에 넘기는 LLM. 호출 시간 · 토큰 수 · 거르기 전 LLM 출력을 남깁니다."""

    def __init__(self, llm: Runnable):
        self.llm = llm
        self.last: dict = {}

    def invoke(self, messages):
        start = time.perf_counter()
        out = self.llm.invoke(messages)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        if out.get("parsing_error") is not None:
            raise out["parsing_error"]
        usage = getattr(out["raw"], "usage_metadata", None) or {}
        self.last = {
            "elapsed_ms": elapsed_ms,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": (usage.get("output_token_details") or {}).get("reasoning"),
            "parsed": out["parsed"],
        }
        return out["parsed"]


def natural_key(path: Path) -> list:
    # script2 < script10
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", path.name)]


def score(text: str, label: dict, response: ParseResponse, picked: LLMOutput) -> dict:
    """대본 하나를 라벨과 비교합니다 (docs/ai/features/script-parser.md 14절)."""
    slides = response.slides
    split_ok = (
        response.status == label["status"]
        and [s.slide_number for s in slides] == [s["slide_number"] for s in label["slides"]]
        and all(
            s.script.startswith(want["first"]) and s.script.endswith(want["last"])
            for s, want in zip(slides, label["slides"])
        )
    )
    scripts = {s.slide_number: s.script for s in slides}
    joined = "\n".join(scripts.values())
    keywords = [(s.slide_number, k) for s in picked.slides for k in s.keywords]
    keywords_in = sum(1 for n, k in keywords if k in scripts.get(n, joined))
    terms_in = sum(1 for t in picked.terms if t in text)
    # 제목 줄이 정제 뒤에도 장 대본의 한 줄로 남았는지 (앞의 # 표시는 정제가 걷어 내므로 빼고 본다)
    lines = {line.strip() for s in slides for line in s.script.splitlines()}
    titles_left = [t for t in label["title_lines"] if re.sub(r"^#{1,6}\s+", "", t).strip() in lines]
    return {
        "split_ok": split_ok,
        "status": response.status,
        "slide_numbers": [s.slide_number for s in slides],
        "keywords": len(keywords),
        "keywords_in_script": keywords_in,
        "terms": len(picked.terms),
        "terms_in_script": terms_in,
        "titles_left": titles_left,
        # 사람이 품질을 볼 수 있게 거른 뒤의 결과도 남긴다
        "result_keywords": {s.slide_number: s.keywords for s in slides},
        "result_terms": response.terms,
    }


def evaluate(llm: RecordingLLM, out: Path, reasoning_effort: str | None) -> None:
    rows = []
    for label_path in sorted(LABELS_DIR.glob("*.json"), key=natural_key):
        text = (SCRIPTS_DIR / f"{label_path.stem}.txt").read_text(encoding="utf-8")
        label = json.loads(label_path.read_text(encoding="utf-8"))
        row = {"script": label_path.stem, "chars": len(text)}
        try:
            response = parse_script(ParseRequest(script_text=text), llm)
        except Exception as e:  # 평가는 한 대본이 실패해도 계속한다
            row["error"] = f"{type(e).__name__}: {e}"[:300]
        else:
            row.update(score(text, label, response, llm.last["parsed"]))
        row.update({k: v for k, v in llm.last.items() if k != "parsed"})
        llm.last = {}
        rows.append(row)
        print(f"{row['script']}: {'ERROR ' + row['error'] if 'error' in row else 'split_ok=' + str(row['split_ok'])}"
              f" {row.get('elapsed_ms')}ms out={row.get('output_tokens')}")

    ok = [r for r in rows if "error" not in r]
    times = {}
    for name, lo, hi in [("~999자", 0, 1000), ("1,000~2,999자", 1000, 3000), ("3,000자~", 3000, 10**9)]:
        ms = [r["elapsed_ms"] for r in rows if lo <= r["chars"] < hi and r.get("elapsed_ms") is not None]
        if ms:
            times[name] = {"count": len(ms), "median_ms": statistics.median(ms), "max_ms": max(ms)}
    summary = {
        "date": date.today().isoformat(),
        "model": os.getenv("OPENAI_MODEL"),
        "reasoning_effort": reasoning_effort,
        "scripts": len(rows),
        "errors": len(rows) - len(ok),
        "split_accuracy": round(sum(r["split_ok"] for r in ok) / len(rows), 3) if rows else None,
        "keywords_in_script_ratio": _ratio(ok, "keywords_in_script", "keywords"),
        "terms_in_script_ratio": _ratio(ok, "terms_in_script", "terms"),
        "titles_left": sum(len(r["titles_left"]) for r in ok),
        "call_time": times,
        "over_service_timeout": [r["script"] for r in rows if (r.get("elapsed_ms") or 0) > SERVICE_TIMEOUT_MS],
        "max_output_tokens": max((r["output_tokens"] or 0 for r in rows if r.get("output_tokens")), default=None),
        # 장별 실제 발표 시간 라벨(actual_slide_ms)이 아직 없다
        "expected_time_error": None,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "scripts": rows}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _ratio(rows: list[dict], part: str, whole: str) -> float | None:
    total = sum(r[whole] for r in rows)
    return round(sum(r[part] for r in rows) / total, 3) if total else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", nargs="+", help="data/scripts/ 안의 파일명")
    group.add_argument("--all", action="store_true", help="data/scripts/*.txt 전체")
    group.add_argument("--eval", action="store_true", help="data/labels/의 라벨이 있는 대본을 평가")
    parser.add_argument("--target-ms", type=int, default=None, help="발표 제한 시간(ms). 없으면 기준 말 속도로 계산")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/results/script_parser_eval.json",
                        help="--eval 결과 JSON 경로")
    parser.add_argument("--reasoning-effort", default=None, help="추론 강도(예: low · minimal). 없으면 보내지 않음")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.eval:
        evaluate(RecordingLLM(build_llm(include_raw=True, reasoning_effort=args.reasoning_effort)), args.out,
                 args.reasoning_effort)
        return

    if args.all:
        paths = sorted(SCRIPTS_DIR.glob("*.txt"), key=natural_key)
    else:
        paths = [SCRIPTS_DIR / name for name in args.file]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            parser.error(f"{SCRIPTS_DIR}에 없는 파일: {', '.join(missing)}")

    RESULTS_DIR.mkdir(exist_ok=True)
    llm = build_llm(reasoning_effort=args.reasoning_effort)

    for path in paths:
        request = ParseRequest(script_text=path.read_text(encoding="utf-8"), target_ms=args.target_ms)
        data = parse_script(request, llm).model_dump()
        out = RESULTS_DIR / f"{path.stem}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: status={data['status']} slides={len(data['slides'])} terms={len(data['terms'])} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
