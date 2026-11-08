"""발표법 · 사용법 자료 묶음 검사와 키워드 검색 (기능 문서 6-10, 5-7 `search_guides`, L1-21 · L4-28 ~ L4-30).

- 누가 쓰나: tools/ 의 `search_guides` 도구(검색), 답 검사 checks.py(인용 원문 대조: `get` · `text_of`),
  원인 설명 판정(`is_approved_cause_source`), 서비스 계층(묶음 만들기).
- 입력 · 출력
  - 입력: 서비스 계층이 파일을 읽어 만든 `manifest`(dict)와 `files`(파일 이름 → 카드 dict 목록).
    **이 모듈은 파일을 읽지 않는다**(코어 규칙). 연구 모듈은 `chat_agent_lab`, 서비스는 `features/chat_agent` 쪽이 읽는다.
  - 출력: `Corpus`(검사를 통과한 카드 묶음). `search(...)`는 `GuideHit` 목록(`GuideCard` + 점수)을 돌려준다.
  - 실패: `CorpusInvalid(reason_code)`. 호출자는 **검색 기능만** 준비 실패로 두고 자료 근거 답을 막는다.
    기록 답은 계속 한다(6-10, L1-21).
- 처리 순서(`build_corpus`)
  1. manifest 모양 · 목록에 적힌 파일이 모두 있는지(`FILE_MISSING`) · 목록에 없는 파일이 끼지 않았는지.
  2. 카드 스키마(`SCHEMA`), 유일한 `source_id`(`DUPLICATE_ID`), 허용 라이선스(`LICENSE_FORBIDDEN`, L4-29).
  3. 묶음 해시(`manifest_hash`)가 manifest 와 같은지(`HASH_MISMATCH`, L4-30: 본문이 바뀌었는데 판이 그대로인 경우를 막는다).
     판 고정표(`version.CORPUS_VERSION_HASHES`): 서비스(`allow_unreviewed=False`)는 등록한 판만(`UNKNOWN_VERSION`), 등록한 판에
     다른 본문이면 `VERSION_REUSED`.
  4. 검수 상태(카드 단위, P-0008 5-2): `allow_unreviewed=False` 면 1~3을 **모든 카드**에 한 뒤 `review == "APPROVED"` 인 카드만
     묶음에 싣는다(검수 전 카드는 검색 · 인용 대상이 아님). 실을 카드가 0장이면 `UNREVIEWED`(자료 묶음 없음). 실을 발표법 카드는
     참고 문헌(`source`의 '참고:')이 있어야 하고, 하나라도 없으면 묶음 전체가 준비 실패다(`NO_REFERENCE`, 6-10 '카드마다 참고 문헌',
     #885 리뷰 — 검수 표시만 바꾸고 참고 문헌을 빠뜨린 카드가 섞이지 않게).
- 검수 상태(`review`)가 있는 까닭: 기능 문서 L4-29 는 P0 에서 '팀 작성 · 검수 완료 카드만' 빌드에 허용한다고 정했다.
  발표법 카드는 다른 AI 담당(PR 리뷰), 화면 사용법 카드는 FE 의 검수를 받는다. 검수를 마치지 않은 카드(`PENDING`)는
  묶음에 남아 해시 · 형식 검사는 받지만 서비스 검색에는 나오지 않는다. g-19(원인 설명)는 CC BY 참고 문헌을 찾지 못해 `PENDING`
  이라, 원인 단정 문장은 지금처럼 승인된 근거가 없어 지워진다(6-9).
  연구 모듈의 평가 · 시험은 `allow_unreviewed=True` 로 초안까지 시험하고, **서비스는 `False` 로 부른다**(`api/chat_agent.py`).
  실을 카드가 하나도 없으면 서비스는 자료 묶음 없이 시작한다(검색은 준비 실패 → 일반 조언 규칙, 6-10 · L1-21). 카드의 검수 표시를
  바꾸면 본문이 같아도 해시가 바뀌므로 판을 올려 `version.CORPUS_VERSION_HASHES` 에 등록한다.
- 검색(P0 키워드, 자료가 작아 충분. 의미 검색은 P1)
  - 한글 정규화: 소문자, 글자 · 숫자 아닌 것은 공백. 낱말과 그 낱말의 2글자 조각(shingle)을 같이 쓴다. 조사 · 어미가 달라도 걸린다("빨라져" ↔ "빨라").
  - 점수 = 질문 조각마다 (희귀도 가중) × (제목 2 + 주제어 2 + 본문 1), 주제어 구절이 질문에 통째로 들어 있으면 가산, 화면이 맞으면 ×1.5.
  - 임계값(`MIN_SCORE`) 이하는 버린다. 정렬은 점수 내림차순, 같으면 `source_id` 오름차순이라 결정적이다.
  - 인용(`quote`)은 질문과 가장 맞는 문장(이어진 다음 문장이 맞고 300자 안이면 함께)이며 항상 카드 본문의 **그대로 잘라 낸 부분 문자열**이다.
    답 검사가 인용이 원문에 있는지 대조하므로 문장을 고쳐 쓰지 않는다.
- 고칠 때
  - 카드 본문 · 제목 · 주제어를 고치면 `manifest.json` 의 `sha256` 을 `manifest_hash` 로 다시 만들고(`corpus_version` 도 올린다) 시험을 돌린다.
    같은 `source_id` 의 본문을 바꾸면 그 판을 인용한 옛 답과 달라지므로 `corpus_version` 을 반드시 올린다(L4-30).
  - 임계값 · 가중치를 바꾸면 tests/unit/test_corpus.py 의 질문 예시가 그대로 맞는지 본다.
  - 카드 문장은 한 문장씩 따로 인용할 수 있게 300자 안으로 쓴다(`GuideCard.quote` 한도).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import DEFAULT_CONFIG, ChatAgentConfig
from .schemas import GuideCard
from .version import CORPUS_VERSION_HASHES

#: 질문과 카드가 이 점수를 넘어야 결과에 든다(자료가 작아 낮은 점수는 우연한 겹침이다)
MIN_SCORE = 4.0
#: 필드 가중치: 제목 · 주제어 · 본문
_W_TITLE, _W_TOPIC, _W_TEXT = 2.0, 2.0, 1.0
#: 주제어 구절이 질문에 통째로 들어 있을 때 더하는 점수
_PHRASE_BONUS = 4.0
#: 화면이 맞는 카드에 곱하는 값
_SCREEN_BOOST = 1.5
#: 원인 설명(승인된 자료)으로 지정한 카드 — 6-9 인과 단정은 이 카드를 근거로 한 때만 허용한다
CAUSE_SOURCE_ID = "g-19"

_SENTENCE_RE = re.compile(r"\S[^.!?]*(?:[.!?]+|$)")
_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣]+")

Reason = Literal[
    "MANIFEST_INVALID",
    "FILE_MISSING",
    "FILE_UNLISTED",
    "SCHEMA",
    "DUPLICATE_ID",
    "LICENSE_FORBIDDEN",
    "HASH_MISMATCH",
    "UNKNOWN_VERSION",
    "VERSION_REUSED",
    "UNREVIEWED",
    "NO_REFERENCE",
    "EMPTY",
]


class CorpusInvalid(Exception):
    """자료 묶음 검사 실패. `reason_code` 로 까닭을 가른다(검색만 준비 실패, 기록 답은 계속)."""

    def __init__(self, reason_code: Reason, detail: str = "") -> None:
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code: Reason = reason_code
        self.detail = detail


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """본문을 문장으로 나눈 (시작, 끝) 위치. 끝 부호(. ! ?)까지 포함한다."""
    return [(m.start(), m.end()) for m in _SENTENCE_RE.finditer(text)]


class GuideCardData(BaseModel):
    """자료 카드 한 장(파일의 한 항목). 모르는 필드는 거절한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[gu]-\d{2,3}$")
    kind: Literal["method", "usage"]
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)
    source: str = Field(min_length=1, pattern=r"\S")  # 공백뿐인 출처는 출처가 아니다(L4-29)
    license: str = Field(min_length=1)
    review: Literal["PENDING", "APPROVED"]
    topics: tuple[str, ...] = Field(min_length=1)
    #: 사용법 카드가 설명하는 화면(예: "pitch_create:slides"). 발표법 카드는 null
    screen: str | None = None

    @field_validator("text")
    @classmethod
    def _sentences_quotable(cls, v: str) -> str:
        if v != v.strip():
            raise ValueError("text has leading/trailing whitespace")
        for start, end in sentence_spans(v):
            if end - start > 300:
                raise ValueError("a sentence is longer than 300 chars (not quotable)")
        return v

    @model_validator(mode="after")
    def _kind_screen(self) -> GuideCardData:
        if (self.kind == "usage") != (self.screen is not None):
            raise ValueError("screen is required for usage cards and forbidden for method cards")
        if not self.source_id.startswith("g-" if self.kind == "method" else "u-"):
            raise ValueError("source_id prefix does not match kind")
        return self


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str = Field(min_length=1)
    files: list[str] = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: 검수한 동의어 사전(6-10): 같은 뜻의 말 묶음. 첫 말은 카드가 쓰는 대표어이고, 질문에 나머지 말이 있으면 대표어로도
    #: 찾는다. 묶음 해시에 든다
    synonyms: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _synonym_groups(self) -> _Manifest:
        for g in self.synonyms:
            if len(g) < 2 or any(not isinstance(t, str) or not t.strip() for t in g):
                raise ValueError("동의어 묶음은 빈 말 없이 2개 이상이다")
            if not _grams(g[0]):
                raise ValueError(
                    "동의어 묶음의 대표어(첫 말)는 두 글자 이상 낱말을 가져야 찾을 수 있다"
                )
        return self


class GuideHit(BaseModel):
    """검색 결과 한 건: 도구가 모델에 돌려줄 카드(`GuideCard`)와 점수."""

    model_config = ConfigDict(frozen=True)

    card: GuideCard
    score: float


def manifest_hash(
    cards: Iterable[Mapping[str, Any]], synonyms: list[list[str]] | None = None
) -> str:
    """모든 카드를 `source_id` 순으로 놓은 정규 JSON(키 정렬 · 공백 없음 · UTF-8)의 SHA-256 16진수.

    동의어 사전이 있으면 `{"cards": …, "synonyms": …}`를 해시한다(사전도 자료 묶음 판에 든다, 6-10). 없으면 카드 목록만
    해시하므로 사전이 없던 판(guides-1)의 해시는 그대로다.
    파일에서 읽은 dict 그대로 계산한다(모델을 거치지 않아 기본값이 해시에 끼지 않는다).
    카드 · 사전을 만들거나 고친 뒤 이 값으로 `manifest.json` 의 `sha256` 을 다시 쓴다.
    """
    ordered = sorted(cards, key=lambda c: str(c.get("source_id", "")))
    body: Any = {"cards": ordered, "synonyms": synonyms} if synonyms else ordered
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _words(s: str) -> list[str]:
    return _NON_WORD_RE.sub(" ", s.lower()).split()


def _grams(s: str) -> set[str]:
    """낱말(2글자 이상)과 그 낱말의 2글자 조각 모음."""
    out: set[str] = set()
    for w in _words(s):
        if len(w) < 2:
            continue
        out.add(w)
        out.update(w[i : i + 2] for i in range(len(w) - 1))
    return out


class _Indexed:
    """카드 한 장의 검색용 조각 모음(만들 때 한 번 계산)."""

    __slots__ = ("card", "title", "topics", "text", "phrases", "sentences", "all")

    def __init__(self, card: GuideCardData) -> None:
        self.card = card
        self.title = _grams(card.title)
        self.topics = set().union(*(_grams(t) for t in card.topics))
        self.text = _grams(card.text)
        self.phrases = [p for p in (" ".join(_words(t)) for t in card.topics) if len(p) >= 2]
        self.sentences = [(s, e, _grams(card.text[s:e])) for s, e in sentence_spans(card.text)]
        self.all = self.title | self.topics | self.text


class Corpus:
    """검사를 통과한 자료 묶음. 만들면 바뀌지 않는다."""

    def __init__(
        self,
        *,
        version: str,
        hash_: str,
        cards: list[GuideCardData],
        cfg: ChatAgentConfig,
        synonyms: list[list[str]] | None = None,
    ) -> None:
        self.version = version
        self.hash = hash_
        self._cfg = cfg
        #: 동의어 묶음: (질문에서 찾을 다른 말들(정규화), 대표어 조각 모음)
        self._synonyms = [
            (tuple(n for n in (" ".join(_words(t)) for t in g[1:]) if n), _grams(g[0]))
            for g in synonyms or []
        ]
        self._cards = tuple(sorted(cards, key=lambda c: c.source_id))
        self._by_id = {c.source_id: c for c in self._cards}
        self._indexed = [_Indexed(c) for c in self._cards]
        n = len(self._indexed)
        df: dict[str, int] = {}
        for ix in self._indexed:
            for g in ix.all:
                df[g] = df.get(g, 0) + 1
        self._idf = {g: math.log(1.0 + n / d) for g, d in df.items()}

    @property
    def cards(self) -> tuple[GuideCardData, ...]:
        return self._cards

    def __len__(self) -> int:
        return len(self._cards)

    def get(self, source_id: str) -> GuideCardData | None:
        return self._by_id.get(source_id)

    def text_of(self, source_id: str) -> str | None:
        """인용 대조용 원문. 없는 `source_id`(가짜 · 철회)는 None."""
        card = self._by_id.get(source_id)
        return card.text if card else None

    def is_approved_cause_source(self, source_id: str) -> bool:
        """원인 설명(6-9 인과 단정)을 받쳐 주는 승인된 자료인가. 검수 완료(APPROVED) 카드만이다.

        연구 단계에서 검수 전 카드를 열어 두어도(`allow_unreviewed=True`) 원인 근거로는 인정하지 않는다.
        """
        card = self._by_id.get(source_id)
        return source_id == CAUSE_SOURCE_ID and card is not None and card.review == "APPROVED"

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        screen: str | None = None,
    ) -> list[GuideHit]:
        """키워드 검색. 빈 질문 · 한도(`max_search_query_chars`) 초과는 ValueError."""
        cfg = self._cfg
        if not query or not query.strip():
            raise ValueError("query is empty")
        if len(query) > cfg.max_search_query_chars:
            raise ValueError(f"query is longer than {cfg.max_search_query_chars} chars")
        cap = cfg.max_search_results
        limit = cap if limit is None else max(0, min(limit, cap))
        q_grams = _grams(query)
        q_norm = " ".join(_words(query))
        # 검수한 동의어 사전으로 질의를 넓힌다(6-10): 질문에 묶음의 다른 말(카드가 쓰지 않는 말)이 있으면 대표어로도
        # 찾는다("원고만 봐요" → 대본). 한 방향으로만 넓힌다 — 대표어를 쓴 질문까지 다른 말로 넓히면 그 말을 주제어로 가진
        # 카드가 질문의 나머지 말(예: "시선 기준")이 맞는 카드를 앞지른다. 대표어는 사용자가 쓴 말을 대신하므로 질문에
        # 직접 쓴 말과 같은 무게로 센다(가볍게 세면 대표어로만 맞는 카드가 MIN_SCORE 를 못 넘는다)
        for variants, rep in self._synonyms:
            if any(v in q_norm for v in variants):
                q_grams |= rep
        hits: list[tuple[float, _Indexed]] = []
        for ix in self._indexed:
            score = 0.0
            for g in q_grams:
                idf = self._idf.get(g)
                if idf is None:
                    continue
                w = (
                    (_W_TITLE if g in ix.title else 0.0)
                    + (_W_TOPIC if g in ix.topics else 0.0)
                    + (_W_TEXT if g in ix.text else 0.0)
                )
                score += idf * w
            score += _PHRASE_BONUS * sum(1 for p in ix.phrases if p in q_norm)
            if score <= 0:
                continue
            if screen and ix.card.screen and _screen_match(ix.card.screen, screen):
                score *= _SCREEN_BOOST
            if score > MIN_SCORE:
                hits.append((score, ix))
        hits.sort(key=lambda h: (-h[0], h[1].card.source_id))
        return [self._hit(ix, score, q_grams) for score, ix in hits[:limit]]

    def _hit(self, ix: _Indexed, score: float, q_grams: set[str]) -> GuideHit:
        text = ix.card.text
        scores = [
            sum(self._idf.get(g, 0.0) for g in q_grams & grams) for _, _, grams in ix.sentences
        ]
        best = max(range(len(scores)), key=lambda i: (scores[i], -i))
        start, end = ix.sentences[best][0], ix.sentences[best][1]
        nxt = best + 1
        if nxt < len(scores) and scores[nxt] > 0 and ix.sentences[nxt][1] - start <= 300:
            end = ix.sentences[nxt][1]
        card = GuideCard(
            source_id=ix.card.source_id,
            title=ix.card.title,
            quote=text[start:end],
            source=ix.card.source,
            license=ix.card.license,
        )
        return GuideHit(card=card, score=round(score, 4))


def _screen_match(card_screen: str, screen: str) -> bool:
    """카드 화면이 요청 화면과 같거나 `:` 경계의 부모 · 자식(`pitch_create` ↔ `pitch_create:slides`)인가."""
    return (
        card_screen == screen
        or card_screen.startswith(screen + ":")
        or screen.startswith(card_screen + ":")
    )


def build_corpus(
    manifest: Mapping[str, Any],
    files: Mapping[str, list[dict[str, Any]]],
    *,
    cfg: ChatAgentConfig = DEFAULT_CONFIG,
    allow_unreviewed: bool,
) -> Corpus:
    """파일에서 읽은 manifest · 카드로 `Corpus` 를 만든다. 하나라도 어긋나면 `CorpusInvalid`.

    `allow_unreviewed` 는 일부러 기본값이 없다: 연구 단계는 True(초안 시험), 서비스 탑재는 False(L4-29).
    """
    try:
        man = _Manifest.model_validate(dict(manifest))
    except ValidationError as e:
        raise CorpusInvalid("MANIFEST_INVALID", str(e)) from e
    for name in man.files:
        if name not in files:
            raise CorpusInvalid("FILE_MISSING", name)
    extra = sorted(set(files) - set(man.files))
    if extra:
        raise CorpusInvalid("FILE_UNLISTED", ", ".join(extra))

    raw: list[dict[str, Any]] = []
    for name in man.files:
        raw.extend(files[name])
    if not raw:
        raise CorpusInvalid("EMPTY")

    cards: list[GuideCardData] = []
    for item in raw:
        if not isinstance(item, dict):
            raise CorpusInvalid("SCHEMA", f"카드가 객체가 아니다: {type(item).__name__}")
        try:
            cards.append(GuideCardData.model_validate(item))
        except ValidationError as e:
            raise CorpusInvalid(
                "SCHEMA", f"{item.get('source_id')}: {e.error_count()} errors"
            ) from e
    ids = [c.source_id for c in cards]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise CorpusInvalid("DUPLICATE_ID", ", ".join(dup))
    for c in cards:
        if c.license not in cfg.allowed_licenses:
            raise CorpusInvalid("LICENSE_FORBIDDEN", f"{c.source_id}: {c.license}")
    if manifest_hash(raw, man.synonyms or None) != man.sha256:
        raise CorpusInvalid("HASH_MISMATCH", man.corpus_version)
    fixed = CORPUS_VERSION_HASHES.get(man.corpus_version)
    if fixed is None and not allow_unreviewed:
        # 서비스는 등록한 판만 쓴다(판을 올리고 고정표에 적는 일을 빠뜨리지 않게, L4-30). 연구는 시험용 판을 허용
        raise CorpusInvalid("UNKNOWN_VERSION", man.corpus_version)
    if fixed is not None and fixed != man.sha256:
        # 같은 판에 다른 본문: 판을 올려야 한다(L4-30)
        raise CorpusInvalid("VERSION_REUSED", man.corpus_version)
    if not allow_unreviewed:
        # 카드 단위 검수(P-0008 5-2): 위 검사는 모든 카드에 했고, 검색 대상은 검수를 마친 카드만이다
        cards = [c for c in cards if c.review == "APPROVED"]
        if not cards:
            raise CorpusInvalid("UNREVIEWED", "no approved card")
        # 6-10: 발표법 카드는 카드마다 참고 문헌이 있어야 한다(팀 작성 원칙만으로는 검수 완료로 쓰지 않는다)
        no_ref = [c.source_id for c in cards if c.kind == "method" and "참고:" not in c.source]
        if no_ref:
            raise CorpusInvalid("NO_REFERENCE", ", ".join(no_ref))
    return Corpus(
        version=man.corpus_version,
        hash_=man.sha256,
        cards=cards,
        cfg=cfg,
        synonyms=man.synonyms,
    )
