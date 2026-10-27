"""API 계약 `contracts/openapi.json` 을 코드(라우터 · 요청 · 응답 모델)에서 만든다.

- 사용: `ai/service` 에서 `uv run python tools/export_openapi.py`. 손으로 고치지 않는다
- 라우터 · `schemas.py` 를 바꾸면 다시 만들어 같은 PR 에 넣는다. `tests/test_openapi.py` 가 커밋된
  파일과 코드가 같은지 본다 (`docs/ai/ai-service.md` 4-1 · 6-1 5번)
- 이미지에는 들어가지 않는다 (`.dockerignore`)
"""

import json
from pathlib import Path

from pitch_coach_ai.main import create_app

OUT = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"


def render() -> str:
    """앱의 OpenAPI 문서를 키 정렬 JSON 으로 (줄 끝 LF, 마지막 줄바꿈 포함)."""
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(render().encode("utf-8"))
    print(OUT)
