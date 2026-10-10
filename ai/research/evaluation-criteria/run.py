"""평가 기준 문장을 분석하고 결과를 출력합니다. --all은 results/examples.json에도 저장합니다.

    python run.py --text "추임새는 5번 이하로 해주세요."   # 직접 입력
    python run.py --index 1 3                            # data/examples.json의 1, 3번째
    python run.py --all                                  # data/examples.json 전체
"""

import argparse
import json
from pathlib import Path

from evaluation_criteria import analyze_criteria, build_llm

ROOT = Path(__file__).resolve().parent
EXAMPLES_PATH = ROOT / "data" / "examples.json"
RESULTS_DIR = ROOT / "results"


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
