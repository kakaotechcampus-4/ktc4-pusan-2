"""대본과 STT 의 내용 형태소를 위치와 함께 뽑고, 대본 문장마다 가장 비슷한 STT 구간을 찾는다."""

from collections import Counter
from difflib import SequenceMatcher
from typing import NamedTuple

from ..shared.facts import extract_critical_facts
from ..shared.kiwi import Morphemes
from ..shared.rubric import EvaluationRubric
from ..shared.text import NormalizedScript
from .schemas import Alignment, SimilarItem

# 내용 형태소: 조사·어미·간투사를 빼고 뜻을 가진 말만 비교한다 (동사·형용사는 어간이라 '했습니다' / '했어요' 차이가 사라진다)
CONTENT_TAGS = {"NNG", "NNP", "NR", "SN", "SL", "SH", "VV", "VA", "XR", "MAG"}
NUMERIC_TYPES = ("percentage", "money", "date", "time", "duration", "quantity", "ratio", "number")


class Token(NamedTuple):
    key: str  # 비교에 쓰는 값: 형태소 원형(소문자) 또는 수치 자리표시 '<percentage:42%>'
    sentence: int
    start: int  # 정규화 텍스트 기준 위치
    end: int
    tag: str


def positioned_tokens(norm: NormalizedScript) -> list[Token]:
    """내용 형태소를 위치·품사와 함께. 수치는 표기와 상관없이 같은 토큰이 되도록 정규형으로 바꾼다 ('42%' = '사십이 퍼센트')."""
    numbers = {}
    for fact in extract_critical_facts(norm):
        if fact.type in NUMERIC_TYPES:
            for start, end in fact.spans:
                numbers[start] = (end, f"<{fact.type}:{fact.normalized}>")
    tokens, skip_until = [], -1
    for tok in Morphemes(norm.text).tokens:
        if tok.start < skip_until:
            continue
        sentence = next((s.index for s in norm.sentences if s.start <= tok.start < s.end), 0)
        if tok.start in numbers:
            skip_until, placeholder = numbers[tok.start]
            tokens.append(Token(placeholder, sentence, tok.start, skip_until, "NUM"))
        elif (
            tok.tag == "XSN"
            and tok.form != "들"
            and tokens
            and tokens[-1].tag in ("NNG", "NNP")
            and tokens[-1].end == tok.start
            and tokens[-1].sentence == sentence
        ):
            # 명사에 붙은 접미사는 한 단어로: '이용'+'률' → '이용률' ('이용료' 와 구별된다). 복수 '들' 은 뗀다
            prev = tokens.pop()
            tokens.append(
                Token(
                    prev.key + tok.form.lower(), sentence, prev.start, tok.start + tok.len, prev.tag
                )
            )
        elif tok.tag in CONTENT_TAGS:
            tokens.append(
                Token(tok.form.lower(), sentence, tok.start, tok.start + tok.len, tok.tag)
            )
    return tokens


def content_tokens(norm: NormalizedScript) -> list[tuple[str, int]]:
    """(내용 형태소, 문장 번호) 목록. 조사·어미·간투사를 빼고 뜻을 가진 말만."""
    return [(t.key, t.sentence) for t in positioned_tokens(norm)]


def align_sentences(
    script_norm: NormalizedScript,
    script_tokens,
    stt_norm: NormalizedScript,
    stt_tokens,
    roles: list[str],
) -> list[Alignment]:
    """대본 문장마다 가장 비슷한 STT 구간(연속 1~3문장)을 찾는다. skip 문장은 뺀다.

    발표자는 문장을 합치거나 쪼개 말하므로 1:1 이 아니라 구간으로 맞춘다. 이 정렬이 LLM 판정과 비교할 규칙 쪽 근거가 된다.
    """
    by_script = {
        i: Counter(t for t, s in script_tokens if s == i) for i in range(len(script_norm.sentences))
    }
    by_stt = [Counter(t for t, s in stt_tokens if s == j) for j in range(len(stt_norm.sentences))]
    alignments = []
    for i, role in enumerate(roles):
        target = by_script[i]
        if role == "skip" or not target:
            continue
        candidates = []
        for start in range(len(by_stt)):
            window = Counter()
            for end in range(start, min(start + 3, len(by_stt))):
                window += by_stt[end]
                coverage = sum((target & window).values()) / sum(target.values())
                candidates.append((round(coverage, 6), -(end - start), list(range(start, end + 1))))
        # 비율이 같으면 짧은 구간: 뜻 없는 앞뒤 문장까지 끌어오지 않는다
        coverage, _, ids = max(candidates, key=lambda c: (c[0], c[1]), default=(0.0, 0, []))
        alignments.append(
            Alignment(
                sentence_index=i, stt_ids=ids if coverage > 0 else [], coverage=round(coverage, 3)
            )
        )
    return alignments


def script_fidelity(script_tokens, stt_tokens, roles: list[str]) -> dict:
    """대본 충실도: 대본(skip 제외)의 내용 형태소가 같은 순서로 STT 에 나온 정도.

    recall = 대본 쪽이 얼마나 그대로 나왔나, precision = STT 중 대본에 있던 말의 비율, fidelity = 둘의 조화평균.
    """
    a = [t for t, s in script_tokens if roles[s] != "skip"]
    b = [t for t, _ in stt_tokens]
    if not a or not b:
        return {"recall": 0.0, "precision": 0.0, "fidelity": 0.0}
    matched = sum(
        block.size for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    )
    recall, precision = matched / len(a), matched / len(b)
    fidelity = 2 * recall * precision / (recall + precision) if matched else 0.0
    return {
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "fidelity": round(fidelity, 3),
    }


def without_spans(tokens: list[Token], spans: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """주어진 위치와 겹치는 형태소를 뺀 (형태소, 문장 번호) 목록 — 대본 충실도에서 비슷한 말을 빼는 데 쓴다."""
    return [
        (t.key, t.sentence)
        for t in tokens
        if not any(t.start < end and start < t.end for start, end in spans)
    ]


def fidelity_excluding(
    rubric: EvaluationRubric,
    script_ptokens: list[Token],
    stt_ptokens: list[Token],
    items: list[SimilarItem],
) -> dict:
    """대본 충실도 (`script_fidelity`). 주어진 비슷한 말 자리의 형태소는 양쪽에서 뺀다 — 판단을 보류한 자리는 맞음·틀림 어느 쪽으로도 세지 않는다."""
    script_tokens = without_spans(script_ptokens, [it.script_span for it in items])
    fidelity = script_fidelity(
        script_tokens,
        without_spans(stt_ptokens, [it.stt_span for it in items]),
        rubric.sentence_roles,
    )
    fidelity["script_tokens"] = sum(
        1 for _, i in script_tokens if rubric.sentence_roles[i] != "skip"
    )
    return fidelity
