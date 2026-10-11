"""간투사(음 · 어 …)와 바로 반복된 말을 찾고 지운다.

이 모듈은 일부러 다른 모듈과 떼어 두었다. `re` 만 쓰고 STT 평가의 나머지 코드를 가져오지 않으므로,
나중에 간투사 횟수를 세는 기능(필러 카운트)을 만들 때 이 파일을 그대로 가져가 쓸 수 있다.

`normalize_stt` 는 단독으로 쓰인 소리뿐인 간투사(음 · 어 · 으 · 엄 · 흠 · 아 · 에)와 바로 반복된 말만 지운다.
`그러니까`, `이제` 처럼 뜻이 있을 수 있는 말은 간투사로 쓰였더라도 일부러 남긴다.
"""

import re

# 뜻 없는 간투사와 바로 반복된 말은 지운다. "그러니까", "이제" 처럼 뜻이 있을 수 있는 말은 남긴다
FILLER = re.compile(r"(?<![가-힣])(?:음+|어+|으+|엄+|흠+|아+|에+)(?![가-힣])")
REPEATED_WORD = re.compile(r"(?<![가-힣])([가-힣]{1,4})(?:\s+\1)+(?![가-힣])")


def remove_fillers(text: str) -> str:
    """간투사를 공백으로 바꾸고, 바로 반복된 말은 하나로 줄인다 ('그 그 결과' → '그 결과')."""
    text = FILLER.sub(" ", text)
    text = REPEATED_WORD.sub(r"\1", text)
    return text


def find_fillers(text: str) -> list[tuple[int, int]]:
    """간투사(FILLER)가 나온 위치 (시작, 끝) 목록. 파이프라인은 쓰지 않는다 — 필러 카운트용."""
    return [m.span() for m in FILLER.finditer(text)]
