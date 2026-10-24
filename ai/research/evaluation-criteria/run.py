"""평가 기준 문장을 분석하고 결과를 출력합니다. --all은 results/examples.json에도 저장합니다.

    python run.py --text "추임새는 5번 이하로 해주세요."   # 직접 입력
    python run.py --index 1 3                            # data/examples.json의 1, 3번째
    python run.py --all                                  # data/examples.json 전체
"""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from evaluation_criteria import analyze_criteria
from evaluation_criteria.schemas import EvaluationCriteriaAnalysis

ROOT = Path(__file__).resolve().parent
EXAMPLES_PATH = ROOT / "data" / "examples.json"
RESULTS_DIR = ROOT / "results"
# ai/.env (작업 디렉터리와 상관없이 고정)
ENV_PATH = ROOT.parents[1] / ".env"


def build_llm() -> Runnable:
    """ai/.env의 OPENAI_* 값으로 Structured Output LLM을 만듭니다.

    자유 키 dict 없이 고정 필드를 사용하여 json_schema 방식으로
    구조화된 출력을 생성합니다.
    (function tools가 아니라 response_format 기반이라 reasoning_effort와 충돌하지 않음)
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
    )
    return llm.with_structured_output(EvaluationCriteriaAnalysis)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", nargs="+", help="분석할 평가 기준 문장")
    group.add_argument("--index", nargs="+", type=int, help="data/examples.json의 번호 (1부터)")
    group.add_argument("--all", action="store_true", help="data/examples.json 전체")
    args = parser.parse_args()

    if args.text:
        inputs = args.text
    else:
        examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
        if args.all:
            inputs = examples
        else:
            invalid = [i for i in args.index if not 1 <= i <= len(examples)]
            if invalid:
                parser.error(f"examples.json 범위(1~{len(examples)}) 밖의 번호: {invalid}")
            inputs = [examples[i - 1] for i in args.index]

    llm = build_llm()
    results = []
    for i, text in enumerate(inputs, 1):
        data = analyze_criteria(text, llm=llm)
        results.append({"input": text, "output": data})
        print(f"Input {i}: {text}")
        print(f"Output {i}: {json.dumps(data, ensure_ascii=False, indent=2)}\n")

    if args.all:
        RESULTS_DIR.mkdir(exist_ok=True)
        out = RESULTS_DIR / "examples.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
