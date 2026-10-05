"""STT 정규화: 대본과 같은 정규화에 간투사 · 말 반복 제거를 더한다."""

import re
import unicodedata

from ..shared.text import QUOTE_MAP, NormalizedScript, normalize_script
from .fillers import remove_fillers


def normalize_stt(text: str) -> NormalizedScript:
    """STT 정규화: 대본과 같은 정규화에 간투사·말 반복 제거를 더한다.

    숫자를 한글로 받아 적은 표기("사십이 퍼센트")는 바꾸지 않는다. `facts.py` 의 사실 검증에서 대본 쪽과 같은 수 파서로 읽는다.
    """
    text = unicodedata.normalize("NFKC", text).translate(QUOTE_MAP)
    text = remove_fillers(text)
    return normalize_script(text)


def locate_raw(raw: str, surface: str, norm_start: int, norm_length: int) -> tuple[int, int] | None:
    """정규화 STT 의 표현을 원본 STT 에서 찾는다 (간투사를 지워 위치가 달라지므로). 여러 번 나오면 상대 위치가 가장 가까운 것."""
    pattern = r"\s*".join(map(re.escape, surface.replace(" ", "")))
    found = [(m.start(), m.end()) for m in re.finditer(pattern, raw)] if pattern else []
    if not found:
        return None
    target = norm_start / max(norm_length, 1)
    return min(found, key=lambda span: abs(span[0] / max(len(raw), 1) - target))
