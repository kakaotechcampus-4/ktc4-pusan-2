"""계약 예시의 출력 파일을 입력에서 다시 만드는 도구.

하는 일: 서비스 `features/filler/examples/` 의
`<이름>_input.json` 을 읽어 지금 코드(서비스 `pitch_coach_ai.features.filler.core`)로 판정하고
짝이 되는 `<이름>_output.json` 을 다시 쓴다. `criteria.json` 도 지금 기준값으로 다시 쓴다.
누가 부르나: 사람이 기준값 · 규칙을 바꾼 뒤 한 번 돌린다. 코어가 아니라 도구라 파일을 읽고 쓴다.
입력과 출력: 입력 JSON 은 손으로 쓰고 그대로 둔다. 출력 JSON 은 줄 끝 LF 로 쓴다.
처리 순서: judge 입력(`judge_input*.json`)마다 judge → summarize 입력 → criteria.
설정: 이름에 `default` 가 들어간 예시만 배포 기본값(`DEFAULT`, T1만 셈)으로, 나머지는 보류 ·
문장 끝 신호 · T2 · T3 규칙을 보여 주려고 세 층을 모두 켠 설정(`ALL_TIERS`)으로 판정한다.
고칠 때 주의: 출력을 다시 만든 뒤에도 서비스 `tests/unit/filler/test_examples.py` 의
문서 대조 테스트가 깨지면 예시가 보여 주려던 상황이 달라진 것이므로 입력을 고친다.
출력 JSON 은 손으로 고치지 않는다.

실행: `ai/research/filler-detection` 에서 `uv run python tools/make_examples.py`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pitch_coach_ai.features import filler as filler_pkg
from pitch_coach_ai.features.filler.config import ALL_TIERS, DEFAULT, FillerConfig
from pitch_coach_ai.features.filler.core import criteria, judge, summarize

# 원본은 서비스의 판정 모듈 안에 있다 (연구 폴더는 서비스를 editable 로 설치해 그 모듈을 쓴다)
EXAMPLES = Path(filler_pkg.__file__).resolve().parent / "examples"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: Any) -> None:
    # 줄 끝 자동 변환을 피하려고 바이트로 쓴다
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def config_for(name: str) -> FillerConfig:
    """예시 파일 이름 → 판정에 쓸 설정. `default` 가 들어가면 배포 기본값, 아니면 세 층 모두."""
    return DEFAULT if "default" in name else ALL_TIERS


def main() -> None:
    for src in sorted(EXAMPLES.glob("judge_input*.json")):
        inputs = _load(src)
        results = judge(inputs, inputs["t_ms"], config_for(src.name))
        out = src.with_name(src.name.replace("_input", "_output"))
        _save(out, [r.model_dump(mode="json") for r in results])
        print(f"{src.name} -> {out.name}")
    _save(EXAMPLES / "summarize_output.json", summarize(_load(EXAMPLES / "summarize_input.json")))
    _save(
        EXAMPLES / "criteria.json",
        {k: v.model_dump(mode="json") for k, v in criteria().items()},
    )
    print("summarize_output.json, criteria.json")


if __name__ == "__main__":
    main()
