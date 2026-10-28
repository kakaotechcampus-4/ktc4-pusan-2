"""요청 DTO 가 같이 쓰는 저장 가능 검사.

PostgreSQL 은 TEXT 에 NUL(\\x00) 을, JSONB 에 \\u0000 · NaN · Infinity 를 넣지 못한다. 짝 없는
서로게이트(\\ud800)는 UTF-8 로 바꿀 수 없어 어디에도 넣지 못한다.
JSON 파서와 pydantic 은 이 값을 그대로 받아 주므로 막지 않으면 저장할 때 DataError 로 500 이 난다.
받는 단계에서 ValueError 로 거절해 422 가 되게 한다.
"""

import math
from typing import Annotated, Any

from pydantic import AfterValidator


def reject_nul(value: str) -> str:
    """NUL 과 UTF-8 로 바꿀 수 없는 문자(짝 없는 서로게이트)를 거절한다."""
    if "\x00" in value:
        raise ValueError("NUL 문자는 쓸 수 없습니다.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("저장할 수 없는 문자가 있습니다.") from exc
    return value


def reject_unstorable_json(value: Any) -> Any:
    """JSONB 에 넣을 값을 안쪽까지 보고 NUL 문자열 · NaN · Infinity 를 거절한다. 키도 본다."""
    if isinstance(value, str):
        reject_nul(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN · Infinity 는 쓸 수 없습니다.")
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_nul(key)
            reject_unstorable_json(item)
    elif isinstance(value, list):
        for item in value:
            reject_unstorable_json(item)
    return value


NulFreeStr = Annotated[str, AfterValidator(reject_nul)]
