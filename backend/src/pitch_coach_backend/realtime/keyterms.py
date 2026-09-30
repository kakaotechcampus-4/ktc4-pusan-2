"""Deepgram `keyterm` 목록을 한도 안에서 조립한다.

순서 = 우선순위: filler(고정) → 대본 용어(가변). 한도를 넘는 용어는 건너뛰고 다음 것을 본다.

한도는 둘이다.
- 개수 100개 (실측, docs/stt-streaming-plan.md §5-1)
- 전체 500 토큰. **넘으면 Deepgram 이 연결 자체를 거절한다** ("Keyterm limit exceeded").
  토큰을 어떻게 세는지는 문서에 없어서 UTF-8 바이트 수로 센다. 어떤 토크나이저든 토큰 하나는
  1바이트 이상이라 바이트 수를 넘을 수 없다 — 과하게 보수적이지만 STT 가 통째로 막히는 것보다 낫다.
  용어마다 구분 토큰이 붙을 수 있어 1씩 더 센다.
"""

from collections.abc import Iterable

MAX_KEYTERMS = 100
MAX_KEYTERM_TOKENS = 500


def normalize_term(raw: str) -> str:
    # 여러 칸 공백·개행은 한 칸으로. Deepgram 은 개행을 용어 구분으로 보지 않는다
    return " ".join(raw.split())


def estimate_tokens(term: str) -> int:
    """토큰 수의 상한. 실제 값은 이보다 작거나 같다."""
    return len(term.encode()) + 1


def build_keyterms(*groups: Iterable[str]) -> tuple[str, ...]:
    """앞 그룹부터 한도 안에 들어가는 것만 담는다. 공백을 정리하고 겹치는 용어는 뺀다."""
    picked: dict[str, None] = {}
    budget = MAX_KEYTERM_TOKENS
    for group in groups:
        for raw in group:
            if len(picked) == MAX_KEYTERMS:
                return tuple(picked)
            term = normalize_term(raw)
            if not term or term in picked:
                continue
            cost = estimate_tokens(term)
            if cost > budget:
                continue
            picked[term] = None
            budget -= cost
    return tuple(picked)
