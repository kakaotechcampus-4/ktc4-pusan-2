"""⑦ 문구 렌더링 — instruction + variant 를 템플릿으로 문장으로 만든다. LLM 은 쓰지 않는다.

params 의 "*_time_ms" 값은 "1분 5초" 같은 문자열로 바꿔 "{*_time}" 자리에 넣습니다.
"""

from __future__ import annotations

from typing import Any

from .config import CoachConfig
from .vocab import Instruction


class _Strict(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise KeyError(f"문구 템플릿에 필요한 값 '{key}' 가 없습니다")


def format_duration(ms: int | float) -> str:
    seconds = max(0, round(ms / 1000))
    if seconds < 60:
        return f"{seconds}초"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}분" if rest == 0 else f"{minutes}분 {rest}초"


def template_key(instruction: Instruction, variant: str) -> str:
    return f"{instruction.value}.{variant}"


def render(cfg: CoachConfig, instruction: Instruction, variant: str, params: dict[str, Any]) -> str:
    template = cfg.templates.get(template_key(instruction, variant)) or cfg.templates.get(
        template_key(instruction, "default")
    )
    if template is None:
        raise KeyError(f"{template_key(instruction, variant)} 문구 템플릿이 없습니다")
    values = _Strict()
    for key, value in params.items():
        if key.endswith("_time_ms") and value is not None:
            values[key.removesuffix("_ms")] = format_duration(value)
        else:
            values[key] = value
    return template.format_map(values)
