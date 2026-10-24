"""군더더기 판정의 사전과 기준값.

하는 일: 판정 사전(T1 · T2 · T3 단어 목록)과 간격 · 잦음 기준값을 `FillerConfig` 하나에 모은다.
누가 부르나: `core.py` 의 `judge` · `summarize` · `criteria` · `keyterms`, 테스트, 예시 도구.
입력과 출력: 값 없이 `FillerConfig()` 로 기본값을 만들거나 일부만 바꿔 만든다.
만든 객체는 바꿀 수 없다(frozen). 값이 서로 어긋나면 만들 때 pydantic
`ValidationError`(`ValueError` 의 하위 클래스)가 난다.
처리 순서: 필드 기본값 → `model_validator` 가 사전의 일관성을 검사한다.
고칠 때 주의: 사전도 판정 기준이라 단어를 더하거나 빼도 `criteria_version` 해시가 바뀐다
(filler.md 4-3). 사전의 단어와 keyterm 순서는 BE · STT 와 맞춰야 하는 값이므로(filler.md 5-2)
바꾸면 filler.md 5-2 의 표도 같이 고친다. 모두 잠정값이다.
"""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, model_validator


def normalize(word: str) -> str:
    """사전과 맞추기 전에 단어의 앞뒤 공백과 유니코드 문장부호를 뗀다 (filler.md 5-2).

    문장부호는 `unicodedata.category` 가 'P' 로 시작하는 글자다. 가운데 글자는 건드리지 않는다
    ("어-음" 은 그대로). 사전 검증(여기)과 판정(`core.py`)이 같은 정의를 쓰도록 이 파일에 둔다.

    인자: word - STT 확정 단어 원문. 반환: 앞뒤를 뗀 단어(전부 떼어지면 빈 문자열).
    """
    start, end = 0, len(word)
    while start < end and _is_edge(word[start]):
        start += 1
    while end > start and _is_edge(word[end - 1]):
        end -= 1
    return word[start:end]


def _is_edge(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


class FillerConfig(BaseModel):
    """judge · summarize · criteria 의 사전과 기준값. 해시가 criteria_version 에 들어간다.

    모두 잠정값이다. 잦음 기준 네 값(window_ms · frequent_count · frequent_bad · lag)은 코치 연구용
    대역(`ai/research/coach-agent` 의 군더더기 대역)과 같은 값이라,
    코치 재생 결과를 그대로 비교할 수 있다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: T1 채움말. 나오면 늘 군더더기 (filler.md 5-2)
    t1_words: tuple[str, ...] = ("음", "어", "아", "에", "으", "음음", "어어", "으음")
    #: T2 지시 · 대용. 간격 · 반복 · 문장 첫 단어 규칙으로 가른다
    t2_words: tuple[str, ...] = (
        "그", "저", "이제", "인제", "뭐", "자", "저기", "이", "인자", "저기요", "아아",
    )  # fmt: skip
    #: T3 어휘적. 앞뒤 간격이 모두 기준 이상이거나 바로 반복일 때만 군더더기
    t3_words: tuple[str, ...] = (
        "그니까", "그러니까", "그니깐", "근데", "그런데", "그래서", "그래가지고", "그래갖고",
        "뭐지", "뭐냐", "뭐더라", "뭔가", "뭐랄까", "그게", "막", "좀", "약간", "그냥", "되게",
        "진짜", "완전", "솔직히", "사실", "이렇게", "저렇게", "그렇게", "이런", "그런", "있잖아",
        "있잖아요", "아무튼", "어쨌든", "하여튼", "아니", "글쎄", "글쎄요",
    )  # fmt: skip
    #: STT keyterm 으로 넘기는 단어. 순서는 BE 목록과 같다: T1 짧은 5 → T2 7 → T1 늘인 3
    keyterms: tuple[str, ...] = (
        "음", "어", "아", "에", "으", "그", "저", "이제", "인제", "뭐", "자", "저기",
        "음음", "어어", "으음",
    )  # fmt: skip

    #: 앞 · 뒤 간격 기준(ms). 이 값 이상이면 단독 단어.
    #: filler.md 5-5 예시("그니까" 뒤 간격 400 ms 가 단독)와 맞춘 첫 값
    gap_ms: int = 400
    #: 문장 첫 단어로 보는 앞 간격 기준(ms). Deepgram utterance_end_ms(1초)와 같은 값
    sentence_gap_ms: int = 1_000
    #: 최근 창(잦음 판정, ms). BE 가 보내는 창 60초 안
    window_ms: int = 60_000
    #: FILLER_FREQUENT 기준값: 최근 창 군더더기 수가 이 값 이상이면 HIGH
    frequent_count: int = 6
    #: 아주 나쁨 값. 이때 severity 가 1.0
    frequent_bad: int = 15
    #: filler_per_min 을 내는 최소 관측 시간(ms)
    min_rate_ms: int = 10_000
    #: 최근 창 때문에 문제의 시작 · 끝이 늦게 잡히는 폭(ms)
    onset_lag_ms: int = 30_000
    offset_lag_ms: int = 30_000

    @model_validator(mode="after")
    def _check_dictionary(self) -> FillerConfig:
        layers = {"T1": self.t1_words, "T2": self.t2_words, "T3": self.t3_words}
        seen: dict[str, str] = {}
        for name, words in layers.items():
            if len(set(words)) != len(words):
                raise ValueError(f"{name} 안에 같은 단어가 두 번 있다")
            for w in words:
                if not w or normalize(w) != w:
                    raise ValueError(f"{name} 단어 {w!r} 는 비어 있거나 정규화돼 있지 않다")
                if w in seen:
                    raise ValueError(f"단어 {w!r} 가 {seen[w]} 와 {name} 에 모두 있다")
                seen[w] = name
        if len(set(self.keyterms)) != len(self.keyterms):
            raise ValueError("keyterms 안에 같은 단어가 두 번 있다")
        allowed = set(self.t1_words) | set(self.t2_words)
        outside = [w for w in self.keyterms if w not in allowed]
        if outside:
            raise ValueError(f"keyterms 는 T1 · T2 단어만 쓸 수 있다: {outside}")
        missing = [w for w in self.t1_words if w not in self.keyterms]
        if missing:
            raise ValueError(f"T1 단어는 모두 keyterms 에 있어야 한다: {missing}")
        return self


DEFAULT = FillerConfig()
