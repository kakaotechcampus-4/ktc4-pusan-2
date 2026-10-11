"""Deepgram `keyterm` 목록을 한도 안에서 조립한다.

순서 = 우선순위: filler(고정) → 대본 용어(가변). 한도를 넘는 용어는 건너뛰고 다음 것을 본다.

한도는 둘이다.
- 개수 100개 (실측. 기록은 docs/stt-streaming-plan.md §5-1, 로컬 문서)
- 전체 500 토큰. **넘으면 Deepgram 이 연결 자체를 거절한다** — HTTP 400
  "Keyterm limit exceeded. The maximum number of tokens across all keyterms is 500."

토큰을 어떻게 세는지는 Deepgram 문서에 없어서 실측했다 (2026-10-06, nova-3 · ko).
가장 비싸게 나온 모양이 "한글·ASCII 글자 수 + 용어당 2" 였다 (흔한 음절을 이어 붙인
"가나다라" 꼴). 실제 단어는 이보다 훨씬 싸다 — 20개 90자가 약 68 토큰.
측정 못 한 문자(한자·이모지 등)는 바이트 단위로 쪼개진다고 보고 UTF-8 바이트 수로 센다.
그래도 공식이 딱 맞는 경계라 TOKEN_HEADROOM 만큼 비워 둔다.
"""

from collections.abc import Iterable

MAX_KEYTERMS = 100
MAX_KEYTERM_TOKENS = 500
# 실측 공식이 한도와 딱 맞는 경계라, 측정하지 못한 문자열 모양에서 넘지 않게 남기는 여유
TOKEN_HEADROOM = 50
# 실측에서 용어 하나마다 글자 수 외에 붙은 토큰
TOKENS_PER_TERM = 2


def normalize_term(raw: str) -> str:
    # 여러 칸 공백·개행은 한 칸으로. Deepgram 은 개행을 용어 구분으로 보지 않는다
    return " ".join(raw.split())


def _char_tokens(char: str) -> int:
    if char.isascii() or "가" <= char <= "힣":
        return 1
    return len(char.encode())


def estimate_tokens(term: str) -> int:
    """토큰 수의 상한 추정. 실측한 가장 비싼 모양과 같거나 크다."""
    return sum(_char_tokens(c) for c in term) + TOKENS_PER_TERM


def build_keyterms(*groups: Iterable[str]) -> tuple[str, ...]:
    """앞 그룹부터 한도 안에 들어가는 것만 담는다. 공백을 정리하고 겹치는 용어는 뺀다."""
    picked: dict[str, None] = {}
    budget = MAX_KEYTERM_TOKENS - TOKEN_HEADROOM
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
