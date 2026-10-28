"""요청 DTO 가 같이 쓰는 저장 가능 검사.

PostgreSQL 은 TEXT 에 NUL(\\x00) 을, JSONB 에 \\u0000 · NaN · Infinity 를 넣지 못한다.
JSON 파서와 pydantic 은 이 값을 그대로 받아 주므로 막지 않으면 저장할 때 DataError 로 500 이 난다.
받는 단계에서 ValueError 로 거절해 422 가 되게 한다.
"""

import math
from typing import Annotated, Any

from pydantic import AfterValidator


def reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL 문자는 쓸 수 없습니다.")
    return value


# 객체 · 배열을 겹칠 수 있는 깊이. AI 계약 예시 중 가장 깊은 것이 10단계다.
# 상한이 없으면 아래 검사가 재귀 한도를 넘어 RecursionError(500) 가 난다
MAX_JSON_DEPTH = 32


def reject_unstorable_json(value: Any, *, _depth: int = 0) -> Any:
    """JSONB 에 넣을 값을 안쪽까지 보고 NUL 문자열 · NaN · Infinity 를 거절한다. 키도 본다.

    `MAX_JSON_DEPTH` 보다 깊게 겹친 값도 거절한다.
    """
    if isinstance(value, str):
        reject_nul(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN · Infinity 는 쓸 수 없습니다.")
    elif isinstance(value, dict | list):
        if _depth >= MAX_JSON_DEPTH:
            raise ValueError(f"{MAX_JSON_DEPTH}단계보다 깊게 겹칠 수 없습니다.")
        if isinstance(value, dict):
            for key, item in value.items():
                reject_nul(key)
                reject_unstorable_json(item, _depth=_depth + 1)
        else:
            for item in value:
                reject_unstorable_json(item, _depth=_depth + 1)
    return value


NulFreeStr = Annotated[str, AfterValidator(reject_nul)]
