"""data/scripts/*.txt 대본을 슬라이드로 분리하고 results/에 JSON으로 저장합니다.

    python run.py --file script3.txt          # 대본 하나
    python run.py --file script1.txt script2.txt
    python run.py --all                       # 전체
"""

import argparse
import json
import logging
import re
from pathlib import Path

from script_parser import build_llm, parse_script

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "data" / "scripts"
RESULTS_DIR = ROOT / "results"


def natural_key(path: Path) -> list:
    # script2 < script10
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", path.name)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", nargs="+", help="data/scripts/ 안의 파일명")
    group.add_argument("--all", action="store_true", help="data/scripts/*.txt 전체")
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
        data = parse_script(path.read_text(encoding="utf-8"), llm=llm)
        out = RESULTS_DIR / f"{path.stem}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{path.name}: status={data['status']} slides={len(data['slides'])} terms={len(data['terms'])} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
