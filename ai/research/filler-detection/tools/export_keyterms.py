"""STT keyterm 단어 목록 파일을 사전에서 다시 만드는 도구.

하는 일: 기본 사전(`filler.config.DEFAULT`)의 keyterm 목록과 그 목록을 만든 `criteria_version` 을
서비스 `features/filler/keyterms.json` 에 다시 쓴다.
누가 부르나: 사람이 사전의 keyterm 이나 기준값을 바꾼 뒤 한 번 돌린다. BE 는 이 파일의 복사본을
쓰고, `tests/test_keyterms.py` 가 파일이 지금 사전과 같은지 본다.
입력과 출력: 입력 없음. 출력은 `{"criteria_version": ..., "keyterms": [...]}`
(UTF-8 · 줄 끝 LF · 들여쓰기 2칸 · 끝에 빈 줄 하나).
처리 순서: `criteria_version(DEFAULT)` 와 `keyterms(DEFAULT)` 를 읽어 dict 를 만들고 바이트로 쓴다.
고칠 때 주의: 파일을 손으로 고치지 않는다. 쓰는 식(dumps 인자)을 바꾸면
`tests/test_keyterms.py` 의 같은 식도 함께 바꾼다. 커밋된 파일과 같으면 `git diff` 가 비어야 한다.

실행: `ai/research/filler-detection` 에서 `uv run python tools/export_keyterms.py`
"""

from __future__ import annotations

import json
from pathlib import Path

from pitch_coach_ai.features import filler as filler_pkg
from pitch_coach_ai.features.filler.config import DEFAULT
from pitch_coach_ai.features.filler.core import criteria_version, keyterms

# 원본은 서비스의 판정 모듈 안에 있다 (연구 폴더는 서비스를 editable 로 설치해 그 모듈을 쓴다)
TARGET = Path(filler_pkg.__file__).resolve().parent / "keyterms.json"


def build_text() -> str:
    """파일에 쓸 텍스트."""
    data = {"criteria_version": criteria_version(DEFAULT), "keyterms": keyterms(DEFAULT)}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    # 줄 끝 자동 변환을 피하려고 바이트로 쓴다
    TARGET.write_bytes(build_text().encode("utf-8"))
    print(f"{TARGET.name}: {len(keyterms(DEFAULT))}개, {criteria_version(DEFAULT)}")


if __name__ == "__main__":
    main()
