"""Kiwi 형태소 분석기와 형태소 경계 확인.

Kiwi 는 모델을 읽는 데 시간이 걸려 import 할 때 만들지 않고, 처음 쓸 때 한 번만 만든다.
"""

from __future__ import annotations

from functools import cache

from kiwipiepy import Kiwi


@cache
def get_kiwi() -> Kiwi:
    """프로세스에서 하나만 쓰는 Kiwi. 분석은 호출한 스레드에서 한다."""
    return Kiwi()


class Morphemes:
    """Kiwi 로 한 번 분석한 형태소 목록. 정규식이 찾은 구간이 온전한 형태소인지 확인한다."""

    def __init__(self, text: str):
        self.tokens = [t for t in get_kiwi().tokenize(text) if t.len > 0]

    def inside(self, start: int, end: int) -> list:
        return [t for t in self.tokens if start <= t.start and t.start + t.len <= end]

    def is_whole(self, start: int, end: int, head_tags: set[str], tail_tags: set[str]) -> bool:
        """[start, end) 가 형태소 경계와 정확히 맞고, 품사가 허용 목록에 있는가.

        "3원칙" 의 '원' 은 '원칙' 형태소의 앞부분이라 False, "24억 원" 의 '원' 은 True.
        """
        tokens = self.inside(start, end)
        if not tokens or tokens[0].start != start or tokens[-1].start + tokens[-1].len != end:
            return False
        if any(
            t.start < start < t.start + t.len or t.start < end < t.start + t.len
            for t in self.tokens
        ):
            return False
        return tokens[0].tag in head_tags and all(t.tag in tail_tags for t in tokens[1:])
