"""data/scripts/*.txt 대본을 슬라이드로 분리하고 results/에 JSON으로 저장합니다.

    python run.py --file script3.txt          # 대본 하나
    python run.py --file script1.txt script2.txt
    python run.py --all                       # 전체
    python run.py --all --target-ms 300000    # 발표 제한 시간에 맞춰 예상 시간 나누기
"""

import argparse
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from script_parser import parse_script
from script_parser.schemas import LLMOutput, ParseRequest

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "data" / "scripts"
RESULTS_DIR = ROOT / "results"
# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = ROOT.parents[1] / ".env"


def build_llm() -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다."""
    load_dotenv(ENV_PATH)
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RuntimeError(f"OPENAI_API_KEY / OPENAI_MODEL이 비어 있습니다: {ENV_PATH}")

    llm = ChatOpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=api_key,
        model=model,
    )
    return llm.with_structured_output(LLMOutput)


def natural_key(path: Path) -> list:
    # script2 < script10
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", path.name)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", nargs="+", help="data/scripts/ 안의 파일명")
    group.add_argument("--all", action="store_true", help="data/scripts/*.txt 전체")
    parser.add_argument("--target-ms", type=int, default=None, help="발표 제한 시간(ms). 없으면 기준 말 속도로 계산")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.all:
        paths = sorted(SCRIPTS_DIR.glob("*.txt"), key=natural_key)
    else:
        paths = [SCRIPTS_DIR / name for name in args.file]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            parser.error(f"{SCRIPTS_DIR}에 없는 파일: {', '.join(missing)}")

    RESULTS_DIR.mkdir(exist_ok=True)
    llm = build_llm()

    for path in paths:
        request = ParseRequest(script_text=path.read_text(encoding="utf-8"), target_ms=args.target_ms)
        data = parse_script(request, llm).model_dump()
        out = RESULTS_DIR / f"{path.stem}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: status={data['status']} slides={len(data['slides'])} terms={len(data['terms'])} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
