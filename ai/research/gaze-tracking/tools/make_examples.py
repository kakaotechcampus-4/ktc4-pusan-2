"""서버 판정 모듈의 계약 예시 출력 파일을 입력에서 다시 만드는 도구.

하는 일: `src/gaze/examples/` 의 `<이름>_input.json` 을 읽어 지금 코드(`gaze.core`)로 판정하고 짝이 되는
`<이름>_output.json` 을 다시 쓴다. `summarize_output.json` · `criteria.json` 도 지금 기준값으로 다시 쓴다.
누가 부르나: 사람이 기준값 · 판정 규칙을 바꾼 뒤 한 번 돌린다. 코어가 아니라 도구라 파일을 읽고 쓴다.
입력과 출력: 입력 JSON 은 손으로 쓰고 그대로 둔다. 출력 JSON 은 줄 끝 LF 로 쓴다.
고칠 때 주의: 다시 만든 뒤 `tests/unit/test_examples.py` 의 상황 대조 테스트가 깨지면 예시가 보여 주려던
상황이 달라진 것이므로 입력을 고친다. 출력 JSON 은 손으로 고치지 않는다.

실행: `ai/research/gaze-tracking` 에서 `.venv\\Scripts\\python.exe tools/make_examples.py`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gaze.core import criteria, judge, summarize

EXAMPLES = Path(__file__).resolve().parents[1] / "src" / "gaze" / "examples"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: Any) -> None:
    # 줄 끝 자동 변환을 피하려고 바이트로 쓴다
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main() -> None:
    for src in sorted(EXAMPLES.glob("judge_input*.json")):
        inputs = _load(src)
        out = src.with_name(src.name.replace("_input", "_output"))
        _save(out, [r.model_dump(mode="json") for r in judge(inputs, inputs["t_ms"])])
        print(f"{src.name} -> {out.name}")
    _save(EXAMPLES / "summarize_output.json", summarize(_load(EXAMPLES / "summarize_input.json")))
    _save(
        EXAMPLES / "criteria.json",
        {k: v.model_dump(mode="json") for k, v in criteria().items()},
    )
    print("summarize_output.json, criteria.json")


if __name__ == "__main__":
    main()
