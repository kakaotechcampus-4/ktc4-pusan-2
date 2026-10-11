"""대본·STT 공통 텍스트 정규화와 구간(span) 도우미."""

import hashlib
import re
import unicodedata

from pydantic import BaseModel, Field

from .kiwi import get_kiwi


class Sentence(BaseModel):
    index: int
    text: str
    start: int = Field(description="정규화 텍스트 기준 시작 오프셋")
    end: int = Field(description="정규화 텍스트 기준 끝 오프셋 (exclusive)")


class NormalizedScript(BaseModel):
    text: str = Field(description="정규화된 대본. 이후 모든 span 은 이 텍스트 기준")
    sentences: list[Sentence]
    content_hash: str = Field(description="정규화 텍스트의 sha256. 대본 변경 감지용")


QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "「": '"', "」": '"'})


def normalize_script(text: str) -> NormalizedScript:
    """대본 정규화: 유니코드·따옴표 통일, 공백 정리, 문장 분리.

    발화 표현 자체(어미, 숫자 표기, 오탈자)는 바꾸지 않는다. 나중에 STT 와 대본 표현이 얼마나 같은지
    비교할 때 원문 표현이 필요하기 때문이다.
    """
    text = unicodedata.normalize("NFKC", text).translate(QUOTE_MAP)
    text = re.sub(r"\s+", " ", text).strip()

    sentences = [
        Sentence(index=i, text=s.text, start=s.start, end=s.end)
        for i, s in enumerate(get_kiwi().split_into_sents(text))
    ]
    return NormalizedScript(
        text=text,
        sentences=sentences,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def compact(text: str) -> tuple[str, list[int]]:
    """공백·문장부호를 뺀 문자열과, 각 글자의 원래 위치."""
    chars, index = [], []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.lower())
            index.append(i)
    return "".join(chars), index


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]
